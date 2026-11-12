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
import json
import logging
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from hamsci_dsp import sigmondb_rows as sr

log = logging.getLogger(__name__)

C_KM_PER_S = 299_792.458
MAX_ARRIVALS = 8
THRESHOLD_DB = 10.0       # peak over the profile's median
# sigma_phi below this keeps sweep-to-sweep phase unwrappable (my call, 2026-11-12):
# the windows sigmondb marks coherent are the ones worth fetching from the station.
COHERENT_SIGMA_PHI_RAD = np.pi / 4
PHASE_KEEP_DAYS = 7


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
    phase_store=None, max_arrivals: int = MAX_ARRIVALS, threshold_db: float = THRESHOLD_DB,
) -> tuple:
    """(capture_row, [arrival_rows]) for one dechirped CPI of one sweep group."""
    kappa = float(result.sweep_rate_hz_per_s)
    profile = np.abs(result.range_doppler).sum(axis=0).astype(np.float64)
    floor = float(np.median(profile)) if profile.size else 0.0
    prov = sr.provenance(t_level)
    gkey = sr.group_key(centre_hz, kappa, period_s)
    srf = 1.0 / period_s
    srf_text = f"{srf:g}"
    from codar_sounder.core.scintillation import compute_scintillation
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
        # Kaeppler et al. 2022: S4 and sigma_phi from the per-sweep series at
        # this bin; Troyer et al. 2025: its carrier phase (at mid-CPI here;
        # the whole series stays on the station, PhaseStore).
        slow = result.range_spectrum[:, i]
        scint = compute_scintillation(slow, sample_rate_hz=srf,
                                      pre_rejected_mask=getattr(result, "bad_sweep_mask", None))
        known = scint.s4_severity != "unknown"
        phase_mid = float(np.angle(slow[slow.size // 2])) if slow.size else None
        coherent = known and scint.sigma_phi_rad < COHERENT_SIGMA_PHI_RAD
        if phase_store is not None:
            ring_ref = phase_store.write(group=gkey, cpi_start_utc=cpi_start_utc, radiod=radiod,
                                         sweep_start_ns=sweep_start_ns, bin=i,
                                         period_s=period_s, tau_ns=tau_ns, delay_ns=delay_ns,
                                         level_db=float(level), slow=slow)
        arrivals.append(sr.arrival_row(
            time=second, reporter_id=reporter_id, group_key=gkey, centre_hz=float(centre_hz),
            rate_hz_per_s=kappa, delay_ns=delay_ns, level_db=float(level),
            # A CODAR sweep spans tens of kHz, so its delay belongs to the centre.
            freq_hz=float(centre_hz),
            margin_db=float(level), doppler_hz=doppler, clean_tag="A",
            carrier_phase_rad=phase_mid,
            s4=float(scint.s4_index) if known else None,
            sigma_phi_rad=float(scint.sigma_phi_rad) if known else None,
            detector_version=detector_version,
            engineering={"radiod": radiod, "sweep_repetition_hz": srf_text,
                         "phase_coherent": "1" if coherent else "0",
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
        engineering={"radiod": radiod, "t_level": t_level or "",
                     "sweep_repetition_hz": srf_text}, **prov)
    return capture, arrivals


class PhaseStore:
    """Per-sweep phase and amplitude series, kept on the station (Michael,
    2026-11-12).  Troyer et al. (2025) track a trace's unwrapped phase sweep by
    sweep, d = c * phi / (4 pi f), to meter resolution.  sigmondb gets one phase
    per CPI and a coherence mark; the series stay here, fetched for event
    windows and campaigns.  One JSON line per arrival per CPI, one file per
    sweep group per UTC day, under <root>/<radiod>/phase/<group>/; days older
    than keep_days go at the first write of each new day."""

    def __init__(self, root, *, radiod: str = "radiod", keep_days: int = PHASE_KEEP_DAYS):
        self.root = Path(root)
        self.radiod = radiod or "radiod"
        self.keep_days = int(keep_days)
        self._lock = threading.Lock()
        self._pruned_for: Optional[date] = None

    @staticmethod
    def _safe(group: str) -> str:
        return re.sub(r"[^0-9A-Za-z.+-]+", "_", group)

    def write(self, *, group, cpi_start_utc, radiod="", sweep_start_ns, bin, period_s, tau_ns,
              delay_ns, level_db, slow) -> str:
        day = cpi_start_utc.date()              # cpi_start_utc is tz-aware UTC
        base = self.root / self._safe(radiod or self.radiod) / "phase"
        path = base / self._safe(group) / f"{day.isoformat()}.jsonl"
        rec = {"cpi_start_utc": cpi_start_utc.isoformat(), "sweep_start_ns": sweep_start_ns,
               "group": group, "bin": int(bin), "period_s": period_s, "tau_ns": int(tau_ns),
               "delay_ns": int(delay_ns), "level_db": round(level_db, 2),
               "phase_rad": [round(float(x), 4) for x in np.angle(slow)],
               "amp_db": [round(float(x), 2) for x in
                          20 * np.log10(np.maximum(np.abs(slow), 1e-12))]}
        with self._lock:
            if self._pruned_for != day:
                self.prune(today=day)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a") as fh:
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        return str(path.relative_to(self.root))

    def prune(self, *, today: date) -> None:
        self._pruned_for = today
        cutoff = today - timedelta(days=self.keep_days)
        for f in self.root.glob("*/phase/*/*.jsonl"):
            try:
                if date.fromisoformat(f.stem) < cutoff:
                    f.unlink()
            except (ValueError, OSError):
                continue


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

    def __init__(self, writers, *, reporter_id: str, detector_version: str,
                 phase_store: Optional[PhaseStore] = None):
        self.arrival_w, self.capture_w = writers
        self.phase_store = phase_store
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
                radiod=radiod, phase_store=self.phase_store)
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
