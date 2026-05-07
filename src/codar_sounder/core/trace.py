"""Trace extraction — find ionospheric peaks in a range profile.

A dechirped CPI yields a power-vs-range profile.  At the lowest
ranges (group range ≈ ground distance) we see strong direct ground-wave
energy; at longer ranges we see sky-wave reflections (E-region,
F-region, sporadic-E).  We want every plausible peak in the search
window so a downstream consumer can see all open propagation modes —
high-ray vs low-ray F2 returns, E vs F-layer reflections, and so on.

Strategy:

1.  Maintain a slowly-varying ground-clutter mask by median-filtering
    the last N profiles.  Subtracting that out removes time-stable
    structure (direct path, persistent backscatter) and emphasises
    transient sky-wave returns.

2.  Within an operator-configured ``[range_min_km, range_max_km]``
    window, find every local maximum that exceeds the SNR threshold,
    enforcing a minimum-separation rule so a single broad peak doesn't
    split into duplicate detections.

3.  Compute per-peak SNR as the peak-to-median ratio (in dB) within
    the search window.  Sort by SNR descending; return up to
    ``max_peaks`` detections.

The convenience wrapper :func:`find_f_region_peak` (singular) returns
just the strongest detection — preserved for backwards compatibility
with v0.3 callers.

Per-peak layer classification (E / F1 / F2 / sporadic-E) needs the
*virtual* height — which only emerges after the inversion (see
:mod:`codar_sounder.core.invert`).  Keep that physics out of this
module so layer-classification policy lives in one place.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import List, Optional

import numpy as np


# Default minimum group-range separation between peaks.  Set roughly to
# one FMCW range bin (Δp ≈ c / BW ≈ 11.7 km at 4.5 MHz CODAR's 25.7 kHz
# BW — see invert.group_range_resolution_km).  Two peaks closer than
# this are taken to be one broad return; we keep the stronger.
DEFAULT_MIN_PEAK_SEPARATION_KM = 12.0

# Default cap on peaks returned per CPI.  Real propagation rarely shows
# more than ~3 distinct ionospheric returns on a single oblique path
# (1F2 high-ray, 1F2 low-ray, occasionally 2F2 or an E-layer return),
# and the SNR threshold filters out the rest.  4 leaves headroom.
DEFAULT_MAX_PEAKS = 4


@dataclass(frozen=True)
class TraceDetection:
    """One peak detection from one CPI's range profile."""
    group_range_km: float
    snr_db: float
    power: float                 # raw power at the peak (units arbitrary)
    bin_index: int               # index into the (positive-range) profile


class GroundClutterMask:
    """Slowly-adapting median mask of previous range profiles.

    Maintains the last ``window`` profiles in a deque and serves the
    pointwise median as a clutter estimate.  The median is robust to
    transient sky-wave peaks — the F-region returns we *want* to keep
    move enough between CPIs that they don't dominate the median, while
    direct-path / ground-clutter energy is stationary and gets
    subtracted out cleanly.

    Subtraction is clamped to zero so a profile with sub-clutter
    excursions doesn't produce negative power values.
    """

    def __init__(self, window: int = 20):
        if window < 1:
            raise ValueError(f"window must be >= 1; got {window}")
        self.window = window
        self._profiles: deque[np.ndarray] = deque(maxlen=window)

    def update(self, profile: np.ndarray) -> None:
        """Add a profile to the rolling window."""
        self._profiles.append(np.asarray(profile, dtype=np.float32))

    @property
    def n_observations(self) -> int:
        return len(self._profiles)

    def estimate(self, length: int) -> np.ndarray:
        """Return the current clutter estimate (median of buffered profiles).

        If no profiles have been observed yet, returns a zero vector of
        the requested length.
        """
        if not self._profiles:
            return np.zeros(length, dtype=np.float32)
        stack = np.stack(self._profiles, axis=0)
        return np.median(stack, axis=0).astype(np.float32)

    def subtract(self, profile: np.ndarray) -> np.ndarray:
        """Return ``max(profile - clutter, 0)``."""
        clutter = self.estimate(len(profile))
        return np.clip(profile - clutter, 0.0, None)


def _power_to_db(x: np.ndarray, eps: float = 1e-30) -> np.ndarray:
    return 10.0 * np.log10(np.maximum(x, eps))


def find_f_region_peaks(
    profile: np.ndarray,
    range_axis_km: np.ndarray,
    *,
    range_min_km: float,
    range_max_km: float,
    snr_threshold_db: float,
    clutter_mask: Optional[GroundClutterMask] = None,
    max_peaks: int = DEFAULT_MAX_PEAKS,
    min_separation_km: float = DEFAULT_MIN_PEAK_SEPARATION_KM,
) -> List[TraceDetection]:
    """Find all qualifying ionospheric peaks in a range profile.

    Returns up to ``max_peaks`` detections, sorted by SNR descending.
    Two peaks closer than ``min_separation_km`` are collapsed (the
    weaker is dropped) so a single broad return doesn't appear as
    duplicate detections.

    Args:
        profile: 1-D power-vs-range vector (sorted by ascending range,
            positive-range half only — see ``positive_range_window``).
        range_axis_km: companion 1-D array of group-range values (km).
        range_min_km / range_max_km: search-window bounds.
        snr_threshold_db: minimum (peak / median-in-window) ratio in dB.
        clutter_mask: optional rolling-median ground-clutter estimator.
            If provided, the mask is updated with the current profile
            and the residual is searched.
        max_peaks: cap on returned detections.
        min_separation_km: peaks closer than this collapse to the
            stronger of the pair.

    Returns the empty list if the search window is empty, the window
    has fewer than three samples, or no peak meets the SNR threshold.
    """
    if profile.shape != range_axis_km.shape:
        raise ValueError(
            f"profile shape {profile.shape} != range_axis shape "
            f"{range_axis_km.shape}"
        )
    if range_min_km >= range_max_km:
        raise ValueError(
            f"range_min_km ({range_min_km}) must be < range_max_km "
            f"({range_max_km})"
        )

    if clutter_mask is not None:
        residual = clutter_mask.subtract(profile)
        clutter_mask.update(profile)
    else:
        residual = profile

    window_mask = (range_axis_km >= range_min_km) & (range_axis_km <= range_max_km)
    if not np.any(window_mask):
        return []

    window_indices = np.where(window_mask)[0]
    if window_indices.size < 3:
        return []

    window_powers = residual[window_indices]
    median_power = float(np.median(window_powers))

    # Local-maxima detection: a sample is a local max if it's strictly
    # greater than both neighbours.  Endpoints can be local maxima
    # against their single neighbour.  This is plenty for FMCW range
    # profiles, which have one-bin-wide peaks; broader peaks get
    # collapsed by the min_separation filter below.
    candidates: list[tuple[float, int]] = []
    n = window_powers.size
    for i in range(n):
        left_ok = i == 0 or window_powers[i] > window_powers[i - 1]
        right_ok = i == n - 1 or window_powers[i] > window_powers[i + 1]
        if not (left_ok and right_ok):
            continue
        peak_power = float(window_powers[i])
        # SNR cap of 60 dB: when the clutter mask zeroes the median,
        # log10(eps) → ~ -300 → bogus 300+ dB SNRs.  60 dB is well
        # above any plausible real SNR.
        snr_db = min(60.0, float(
            _power_to_db(np.array([peak_power]))[0]
            - _power_to_db(np.array([median_power]))[0]
        ))
        if snr_db < snr_threshold_db:
            continue
        candidates.append((snr_db, int(window_indices[i])))

    if not candidates:
        return []

    # Sort by SNR descending; greedily keep peaks separated by at least
    # min_separation_km.  This is O(N²) in the candidate count but N
    # rarely exceeds a few dozen so it doesn't matter.
    candidates.sort(key=lambda t: -t[0])
    kept: list[TraceDetection] = []
    for snr_db, peak_idx in candidates:
        if len(kept) >= max_peaks:
            break
        peak_range = float(range_axis_km[peak_idx])
        if any(
            abs(peak_range - existing.group_range_km) < min_separation_km
            for existing in kept
        ):
            continue
        kept.append(TraceDetection(
            group_range_km=peak_range,
            snr_db=snr_db,
            power=float(residual[peak_idx]),
            bin_index=peak_idx,
        ))
    return kept


def find_f_region_peak(
    profile: np.ndarray,
    range_axis_km: np.ndarray,
    *,
    range_min_km: float,
    range_max_km: float,
    snr_threshold_db: float,
    clutter_mask: Optional[GroundClutterMask] = None,
) -> Optional[TraceDetection]:
    """Find the strongest F-region peak in a range profile.

    Backwards-compat wrapper around :func:`find_f_region_peaks` that
    returns just the strongest detection (or ``None`` if no peak meets
    the SNR threshold).  New code should prefer the plural form so all
    open propagation modes are surfaced.
    """
    peaks = find_f_region_peaks(
        profile, range_axis_km,
        range_min_km=range_min_km,
        range_max_km=range_max_km,
        snr_threshold_db=snr_threshold_db,
        clutter_mask=clutter_mask,
        max_peaks=1,
    )
    return peaks[0] if peaks else None
