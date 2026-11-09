"""tdma-scan reads samples from the IQ source's (samples, cpi_start_utc) tuples.

Found 2026-11-01 on AC0G-ND: the first live scan crashed with
"'tuple' object has no attribute 'dtype'", because the handler passed the
whole tuple to discover_tx_offsets.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import numpy as np

from codar_sounder import cli
from codar_sounder.core import stream

CONFIG = """
[station]
callsign = "TEST"
grid_square = "EN16ov"
receiver_lat = 46.9
receiver_lon = -96.8

[paths]
output_dir = "{out}"
log_dir = "{out}"

[[radiod]]
status = "test-status.local"
channel_name = "codar-13mhz"

[[radiod.transmitter]]
id = "SEAB"
center_freq_hz = 13450000
sweep_rate_hz_per_s = -49629.688
sweep_bw_hz = 49630
sweep_repetition_hz = 1
tx_lat_deg = 40.362633
tx_lon_deg = -73.972400

[processing]
sample_rate_hz = 8000
"""


class _TupleSource:
    """Mimics both real backends: yields (samples, cpi_start_utc)."""

    def __init__(self, n):
        rng = np.random.default_rng(1)
        self._samples = (rng.standard_normal(n)
                         + 1j * rng.standard_normal(n)).astype(np.complex64)

    def __iter__(self):
        yield (self._samples, datetime(2026, 11, 1, tzinfo=timezone.utc))

    def stop(self):
        pass


def test_tdma_scan_unpacks_the_sample_tuple(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "codar.toml"
    cfg.write_text(CONFIG.format(out=tmp_path))
    monkeypatch.setattr(stream, "make_iq_source",
                        lambda **kw: _TupleSource(8000 * 2))
    args = argparse.Namespace(config=cfg, radiod_id=None, seconds=2,
                              snr_threshold_db=10.0, json=True,
                              write_config=False, log_level=None)
    cli._handle_tdma_scan(args)
    assert '"capture_seconds": 2' in capsys.readouterr().out


SECOND_BLOCK = """
[[radiod]]
status = "test-status.local"
channel_name = "codar-4537khz"

[[radiod.transmitter]]
id = "DUCK"
center_freq_hz = 4537180
sweep_rate_hz_per_s = -25733.913
sweep_bw_hz = 25734
sweep_repetition_hz = 1
tx_lat_deg = 36.180350
tx_lon_deg = -75.750133
"""


def _two_block_scan(tmp_path, monkeypatch, capsys, channel_name):
    import json
    cfg = tmp_path / "codar.toml"
    cfg.write_text(CONFIG.format(out=tmp_path) + SECOND_BLOCK)
    seen = {}

    def _factory(**kw):
        seen.update(kw)
        return _TupleSource(8000 * 2)

    monkeypatch.setattr(stream, "make_iq_source", _factory)
    args = argparse.Namespace(config=cfg, radiod_id="test-status.local",
                              channel_name=channel_name, seconds=2,
                              snr_threshold_db=10.0, json=True,
                              write_config=False, log_level=None)
    cli._handle_tdma_scan(args)
    return seen, json.loads(capsys.readouterr().out)


def test_tdma_scan_defaults_to_the_first_block(tmp_path, monkeypatch, capsys):
    seen, out = _two_block_scan(tmp_path, monkeypatch, capsys, None)
    assert out["channel_name"] == "codar-13mhz"
    assert int(seen["center_freq_hz"]) == 13_450_000


def test_tdma_scan_channel_name_picks_the_block(tmp_path, monkeypatch, capsys):
    seen, out = _two_block_scan(tmp_path, monkeypatch, capsys, "codar-4537khz")
    assert out["channel_name"] == "codar-4537khz"
    assert int(seen["center_freq_hz"]) == 4_537_180
    assert [t["id"] for t in out["tx_assignments"]] == ["DUCK"]
