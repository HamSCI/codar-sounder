"""codar-sounder's rows for sigmondb, in the shared chirp-row form.

Design: tasks/chirp-registry/2026-11-12-codar-sigmondb-design.md.  The row
definition lives in ``hamsci_dsp.sigmondb_rows`` so chirp-recorder writes the
same rows later; this module only turns one dechirped CPI into them.

Two choices differ from codar-sounder's physics path on purpose:

- The search covers the whole range profile, both halves of the FFT.  The
  physics looks only beyond the ground distance for F-region peaks, so it
  never reports the ground wave, and the ground wave pins a radar's offset.
  While that offset stays unknown, the ground wave can land anywhere in the
  sweep period; the central analysis says which peak came by ground.
- Each arrival keeps absolute time.  A CPI starts wherever its anchor fell
  (stream.py anchors the first sample, not a UTC second), and the replica
  starts at CPI start minus the TDMA offset.  So the arrival falls at
  CPI start - TDMA offset + tau, with tau = -f_beat / kappa for the mixing
  rx * conj(replica).  The row folds that instant into the sweep period.
"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime
from typing import Iterable, Optional

import numpy as np

from hamsci_dsp import sigmondb_rows as sr

log = logging.getLogger(__name__)

C_KM_PER_S = 299_792.458
MAX_ARRIVALS = 8
THRESHOLD_DB = 10.0       # peak over the profile's median


def _epoch_ns(t: datetime) -> int:
    """A tz-aware datetime as integer ns, exactly (datetime holds µs)."""
    return calendar.timegm(t.utctimetuple()) * 1_000_000_000 + t.microsecond * 1000


def _peaks(profile: np.ndarray, max_n: int, threshold_db: float) -> list:
    """Indices of local maxima (circular) at least threshold_db over the
    median, strongest first, at least 3 bins apart."""
    floor = float(np.median(profile))
    if not floor > 0:
        return []
    left, right = np.roll(profile, 1), np.roll(profile, -1)
    cand = np.nonzero((profile > left) & (profile >= right)
                      & (profile >= floor * 10 ** (threshold_db / 10)))[0]
    cand = cand[np.argsort(profile[cand])[::-1]]
    n_bins = profile.size
    out: list = []
    for i in cand:
        if all(min(abs(int(i) - j), n_bins - abs(int(i) - j)) >= 3 for j in out):
            out.append(int(i))
            if len(out) >= max_n:
                break
    return out


def rows_for_cpi(
    *, result, cpi_start_utc: datetime, tdma_offset_samples: int, centre_hz: float,
    period_s: float, reporter_id: str, t_level: Optional[str], expected: Iterable[str],
    detector_version: str, duration_s: float, ring_ref: str = "", radiod: str = "",
    max_arrivals: int = MAX_ARRIVALS, threshold_db: float = THRESHOLD_DB,
) -> tuple:
    """(capture_row, [arrival_rows]) for one dechirped CPI of one sweep group."""
    kappa = float(result.sweep_rate_hz_per_s)
    profile = np.abs(result.range_doppler).sum(axis=0).astype(np.float64)
    floor = float(np.median(profile)) if profile.size else 0.0
    prov = sr.provenance(t_level)
    gkey = sr.group_key(centre_hz, kappa)
    start_ns = _epoch_ns(cpi_start_utc)
    sweep_start_ns = start_ns - round(tdma_offset_samples / result.sample_rate_hz * 1e9)

    arrivals = []
    for i in _peaks(profile, max_arrivals, threshold_db):
        # range_axis_km = c * f / |kappa|; recover f, then tau = -f / kappa.
        f_beat = result.range_axis_km[i] / C_KM_PER_S * abs(kappa)
        tau_ns = round(-f_beat / kappa * 1e9)
        second, delay_ns = sr.split_arrival(sweep_start_ns + tau_ns, period_s)
        col = np.abs(result.range_doppler[:, i])
        doppler = float(result.doppler_axis_hz[int(np.argmax(col))]) if col.size else None
        level = 10 * np.log10(profile[i] / floor)
        arrivals.append(sr.arrival_row(
            time=second, reporter_id=reporter_id, group_key=gkey, centre_hz=float(centre_hz),
            rate_hz_per_s=kappa, delay_ns=delay_ns, level_db=float(level),
            margin_db=float(level), doppler_hz=doppler, clean_tag="A",
            detector_version=detector_version,
            engineering={"radiod": radiod,
                         "tdma_offset_samples": str(tdma_offset_samples),
                         "t_level": t_level or "", "bin": str(i),
                         "sweeps_rejected": str(getattr(result, "n_sweeps_rejected", 0))},
            **prov))

    expected = list(expected)
    capture = sr.capture_row(
        time=sr.utc_iso_ms(start_ns / 1e9), reporter_id=reporter_id, centre_hz=float(centre_hz),
        rate_hz_per_s=kappa, duration_s=float(duration_s),
        n_sweeps=int(result.range_doppler.shape[0]),
        expected={tx: 1.0 for tx in expected},
        null_median_db=float(10 * np.log10(floor)) if floor > 0 else None,
        n_expected=len(expected), n_found=len(arrivals),
        detector_version=detector_version, ring_ref=ring_ref,
        engineering={"radiod": radiod, "t_level": t_level or ""}, **prov)
    return capture, arrivals


def make_writers():
    """(arrival_writer, capture_writer) on the local sink, or (None, None).

    The sink's ``sigmondb`` database, producer ``codar-sounder``; hs-uploader's
    codar-sigmondb pipelines carry the rows to wd30.  A missing hs-uploader
    leaves codar-sounder recording JSONL alone, as before."""
    try:
        try:
            from hs_uploader.sink import Writer  # type: ignore[import-not-found]
        except ImportError:
            from sigmond.hamsci_sink import Writer  # type: ignore[import-not-found]
        return tuple(Writer.from_env(table=t, mode="sigmondb",
                                     schema_version=sr.SCHEMA_VERSION,
                                     producer="codar-sounder", batch_rows=200)
                     for t in ("arrival", "capture"))
    except ImportError:
        log.debug("no sink writer importable; sigmondb rows off")
    except Exception as exc:  # noqa: BLE001
        log.warning("sigmondb writer init failed (%s); JSONL unaffected", exc)
    return None, None


def anchor_t_level(anchor) -> Optional[str]:
    """The timing tier an AnchorUTC rode: the authority's active T-level
    when its offset applied to an RTP-referenced anchor, else None."""
    snap = getattr(anchor, "snapshot", None)
    if anchor is None or not getattr(anchor, "rtp_referenced", False) or snap is None:
        return None
    if not getattr(snap, "offset_usable", False):
        return None
    return getattr(snap, "t_level_active", None)


class Emitter:
    """Writes one sweep group's rows per CPI.  One emitter serves every
    block of a radiod (the writers hold their own locks)."""

    def __init__(self, writers, *, reporter_id: str, detector_version: str):
        self.arrival_w, self.capture_w = writers
        self.reporter_id = reporter_id or ""
        self.detector_version = detector_version

    @property
    def active(self) -> bool:
        return self.arrival_w is not None and self.capture_w is not None

    def emit(self, *, result, cpi_start_utc, tdma_offset_samples, centre_hz, period_s,
             expected, duration_s, anchor, radiod: str = "") -> int:
        """Store one CPI's rows; the number of arrivals, or -1 on failure.
        Never raises: the sink must not take the daemon down."""
        if not self.active:
            return 0
        try:
            cap, arrs = rows_for_cpi(
                result=result, cpi_start_utc=cpi_start_utc,
                tdma_offset_samples=tdma_offset_samples, centre_hz=centre_hz,
                period_s=period_s, reporter_id=self.reporter_id,
                t_level=anchor_t_level(anchor), expected=expected,
                detector_version=self.detector_version, duration_s=duration_s,
                radiod=radiod)
            if arrs:
                self.arrival_w.insert(arrs)
            self.capture_w.insert([cap])
            return len(arrs)
        except Exception as exc:  # noqa: BLE001
            log.warning("sigmondb rows for %.4f MHz failed: %s", centre_hz / 1e6, exc)
            return -1

    def close(self) -> None:
        for w in (self.arrival_w, self.capture_w):
            if w is not None:
                try:
                    w.close()
                except Exception as exc:  # noqa: BLE001
                    log.warning("sigmondb writer close failed: %s", exc)
