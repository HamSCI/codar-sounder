"""codar-sounder's sigmondb rows (tasks/chirp-registry/2026-11-12-codar-sigmondb-design.md).

The rows keep absolute time: a CPI starts wherever its anchor fell, the
replica starts at CPI start minus the TDMA offset, and an arrival's delay
folds CPI start - TDMA offset + tau into the sweep period from the UTC second.
A synthetic chirp at a known delay must come back at that delay, and the
search must see the whole period, ground wave included.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from codar_sounder.core.dechirp import dechirp
from codar_sounder.core import sigmondb as sdb
from hamsci_dsp import sigmondb_rows as sr

FS = 64_000.0
KAPPA = -25_733.913      # CODAR 4.5 MHz down-chirp
SRF = 1.0
N = int(FS / SRF)


def _signal(delay_s, n_sweeps=8, start_into_sweep_s=0.0, snr_amp=1.0, seed=0):
    """Received chirp: the radar's sweeps start on the UTC second; our first
    sample lands start_into_sweep_s after one of them; the path adds delay_s."""
    t = np.arange(n_sweeps * N) / FS + start_into_sweep_s - delay_s
    tin = np.mod(t, 1.0 / SRF)
    s = snr_amp * np.exp(1j * np.pi * KAPPA * tin * tin)
    rng = np.random.default_rng(seed)
    noise = (rng.standard_normal(s.size) + 1j * rng.standard_normal(s.size)) * 0.05
    return (s + noise).astype(np.complex64)


@pytest.mark.parametrize("delay_ms,start_ms", [(1.2, 0.0), (1.2, 317.0), (40.0, 905.5)])
def test_a_known_delay_comes_back_on_the_utc_second(delay_ms, start_ms):
    second = datetime(2026, 11, 12, 12, 0, 7, tzinfo=timezone.utc)
    cpi_start = second.timestamp() + start_ms / 1000
    # The radar's sweep phase at our first sample = start_ms; tdma 0 means the
    # replica assumes phase 0, so the peak lands at (delay - start) mod period.
    rx = _signal(delay_ms / 1000, start_into_sweep_s=start_ms / 1000)
    res = dechirp(rx, sample_rate_hz=FS, sweep_rate_hz_per_s=KAPPA,
                  sweep_repetition_hz=SRF)
    cap, arrs = sdb.rows_for_cpi(
        result=res, cpi_start_utc=datetime.fromtimestamp(cpi_start, tz=timezone.utc),
        tdma_offset_samples=0, centre_hz=4_463_000.0, period_s=1.0 / SRF,
        reporter_id="", t_level="T6", expected=["BLCK"], detector_version="codar-sounder/test",
        duration_s=8.0)
    assert arrs, "no arrival found"
    best = max(arrs, key=lambda a: a["level_db"])
    assert abs(best["delay_ns"] - delay_ms * 1e6) < 25_000          # one range bin ~ 15.6 us
    assert best["group_key"] == sr.group_key(4_463_000.0, KAPPA)
    assert best["origin"] == "t6_anchor"
    assert best["freq_hz"] == 4_463_000.0
    assert tuple(best) == sr.ARRIVAL_COLUMNS and tuple(cap) == sr.CAPTURE_COLUMNS
    assert cap["n_found"] == len(arrs) and cap["expected"] == {"BLCK": 1.0}


def test_the_tdma_offset_shifts_the_replica_not_the_answer():
    second = datetime(2026, 11, 12, 12, 0, 7, tzinfo=timezone.utc)
    rx = _signal(0.0012, start_into_sweep_s=0.2)
    out = []
    for tdma in (0, 6400):
        res = dechirp(rx, sample_rate_hz=FS, sweep_rate_hz_per_s=KAPPA,
                      sweep_repetition_hz=SRF, phase_offset_samples=tdma)
        _, arrs = sdb.rows_for_cpi(
            result=res, cpi_start_utc=datetime.fromtimestamp(second.timestamp() + 0.2,
                                                             tz=timezone.utc),
            tdma_offset_samples=tdma, centre_hz=4_463_000.0, period_s=1.0,
            reporter_id="", t_level="T3", expected=[], detector_version="t", duration_s=8.0)
        out.append(max(arrs, key=lambda a: a["level_db"])["delay_ns"])
    assert abs(out[0] - out[1]) < 25_000
    assert abs(out[0] - 1_200_000) < 25_000


def test_noise_alone_finds_nothing():
    rng = np.random.default_rng(3)
    rx = ((rng.standard_normal(8 * N) + 1j * rng.standard_normal(8 * N)) * 0.05).astype(np.complex64)
    res = dechirp(rx, sample_rate_hz=FS, sweep_rate_hz_per_s=KAPPA, sweep_repetition_hz=SRF)
    cap, arrs = sdb.rows_for_cpi(
        result=res, cpi_start_utc=datetime(2026, 11, 12, tzinfo=timezone.utc),
        tdma_offset_samples=0, centre_hz=4_463_000.0, period_s=1.0, reporter_id="",
        t_level=None, expected=[], detector_version="t", duration_s=8.0)
    assert arrs == [] and cap["n_found"] == 0 and cap["origin"] == ""


def test_only_the_first_transmitter_of_each_sweep_group_writes_rows(tmp_path):
    """Transmitters sharing a group differ only by TDMA offset and see the
    same arrivals; a second writer would double every arrival in sigmondb."""
    from codar_sounder.core import daemon as daemon_mod

    class FakeEmitter:
        active = True
        def __init__(self):
            self.calls = []
        def emit(self, **kw):
            self.calls.append(kw)
        def close(self):
            pass

    def tx(i, rate=-25733.913, tdma=0):
        return {"id": i, "center_freq_hz": 4_537_180, "sweep_rate_hz_per_s": rate,
                "sweep_bw_hz": 25734, "sweep_repetition_hz": 1.0, "tx_lat_deg": 36.18,
                "tx_lon_deg": -75.75, "tdma_offset_samples": tdma}

    block = {"status": "kfs-status.local", "channel_name": "codar-4537khz",
             "transmitter": [tx("DUCK"), tx("HATY", tdma=32000), tx("OSU", rate=-47791.553)]}
    config = {"station": {"callsign": "KFS", "receiver_lat": 37.39, "receiver_lon": -122.41},
              "paths": {"output_dir": str(tmp_path)},
              "processing": {"coherent_seconds": 1, "sample_rate_hz": 64000,
                             "force_synthetic": True},
              "radiod": [block]}
    em = FakeEmitter()
    d = daemon_mod.SounderDaemon(config, block, sink_writer=None, sigmondb_emitter=em)
    hooks = {p.station_id: p.sigmondb_hook for p in d.pipelines}
    assert hooks["DUCK"] is not None and hooks["OSU"] is not None
    assert hooks["HATY"] is None
    hooks["DUCK"]("result", "start")
    assert em.calls[0]["expected"] == ["DUCK", "HATY"]
    assert em.calls[0]["centre_hz"] == 4_537_180.0 and em.calls[0]["period_s"] == 1.0


def _rows(tmp_path=None, delay_ms=1.2, srf=SRF, store=None):
    rx = _signal(delay_ms / 1000, n_sweeps=32)   # scintillation wants >= its minimum
    res = dechirp(rx, sample_rate_hz=FS, sweep_rate_hz_per_s=KAPPA, sweep_repetition_hz=srf)
    return sdb.rows_for_cpi(
        result=res, cpi_start_utc=datetime(2026, 11, 12, 12, 0, 7, tzinfo=timezone.utc),
        tdma_offset_samples=0, centre_hz=4_463_000.0, period_s=1.0 / srf,
        reporter_id="", t_level="T6", expected=[], detector_version="t", duration_s=32.0,
        phase_store=store)


def test_arrivals_carry_scintillation_phase_and_the_sweep_rate():
    cap, arrs = _rows()
    best = max(arrs, key=lambda a: a["level_db"])
    assert best["s4"] is not None and best["sigma_phi_rad"] is not None
    assert best["carrier_phase_rad"] is not None and -np.pi <= best["carrier_phase_rad"] <= np.pi
    assert best["engineering"]["sweep_repetition_hz"] == "1"
    assert best["engineering"]["phase_coherent"] == "1"       # a clean synthetic chirp
    assert cap["engineering"]["sweep_repetition_hz"] == "1"


def test_phase_series_stay_on_the_station_and_the_capture_points_at_them(tmp_path):
    import json
    store = sdb.PhaseStore(tmp_path, keep_days=7)
    cap, arrs = _rows(store=store)
    files = list(tmp_path.rglob("*.jsonl"))
    assert len(files) == 1 and cap["ring_ref"] == str(files[0].relative_to(tmp_path))
    rec = [json.loads(l) for l in files[0].read_text().splitlines()]
    assert len(rec) == len(arrs)
    assert len(rec[0]["phase_rad"]) == 32 and len(rec[0]["amp_db"]) == 32  # one per sweep
    assert rec[0]["delay_ns"] in {a["delay_ns"] for a in arrs}


def test_the_phase_store_prunes_days_past_its_keep(tmp_path):
    store = sdb.PhaseStore(tmp_path, radiod="r", keep_days=2)
    old = tmp_path / "r" / "phase" / "g" / "2026-11-01.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text("{}\n")
    store.prune(today=datetime(2026, 11, 12, tzinfo=timezone.utc).date())
    assert not old.exists()
