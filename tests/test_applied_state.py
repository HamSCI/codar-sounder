"""The §3 ``timing_authority_applied`` report for one codar daemon.

One daemon serves one radiod through one IQ source, so the report stands
for one anchor.  CLIENT-CONTRACT §18.5 (amendment 2026-09-04): the field
describes the labels the running client writes.  The daemon leaves the
block at ``<output_dir>/<radiod_id>/timing-authority.json`` at most once
a minute; ``inventory --json`` reads it back.  Both sides take the path
from one helper, and a test here pins that.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from hamsci_dsp.timing import AnchorUTC, read_applied_state, write_applied_state

from codar_sounder.contract import build_inventory
from codar_sounder.core.applied_state import applied_state_for, applied_state_path
from codar_sounder.core.daemon import SounderDaemon


def _anchor(offset_ns, source="rtp_to_utc+authority"):
    snap = SimpleNamespace(
        t_level_active="T6", sigma_ns=4210, governor_radiod="gov",
        utc_published=None, host_clock=None,
    ) if offset_ns is not None else None
    return AnchorUTC(
        utc=1_700_000_500.0, source=source,
        offset_seconds=(offset_ns or 0) / 1e9, offset_ns=offset_ns,
        snapshot=snap, rtp_referenced=True,
    )


def _config(output_dir: Path) -> dict:
    return {
        "station": {"callsign": "TEST", "receiver_lat": 0.0, "receiver_lon": 0.0},
        "paths": {"output_dir": str(output_dir)},
        "processing": {"force_synthetic": True, "coherent_seconds": 60},
    }


def _radiod_block() -> dict:
    return {
        "status": "test.local",
        "channel_name": "codar-test",
        "transmitter": [
            {"id": "DUCK", "center_freq_hz": 4_537_180,
             "sweep_rate_hz_per_s": -25733.913, "sweep_bw_hz": 25734,
             "sweep_repetition_hz": 1, "tx_lat_deg": 36.18, "tx_lon_deg": -75.75},
            {"id": "LISL", "center_freq_hz": 4_575_000,
             "sweep_rate_hz_per_s": -25733.913, "sweep_bw_hz": 25734,
             "sweep_repetition_hz": 1, "tx_lat_deg": 36.69, "tx_lon_deg": -75.92},
        ],
    }


def _daemon(tmp_path: Path, anchor) -> SounderDaemon:
    d = SounderDaemon(_config(tmp_path), _radiod_block())
    d.iq_source = SimpleNamespace(anchor=anchor)
    return d


class TestPathRule:

    def test_one_rule(self, tmp_path):
        assert applied_state_path(tmp_path, "rx.local") == (
            tmp_path / "rx.local" / "timing-authority.json"
        )
        assert applied_state_path(str(tmp_path), "rx.local") == (
            tmp_path / "rx.local" / "timing-authority.json"
        )


class TestAppliedStateFor:

    def test_one_source_one_channel(self):
        block = applied_state_for(SimpleNamespace(anchor=_anchor(4_250_000)), "rx")
        assert block["tier"] == "T6"
        assert block["radiod_id"] == "rx"
        assert block["channels"] == {"total": 1, "anchored": 1, "applied": 1}
        json.dumps(block)

    def test_source_without_anchor_attribute_reports_null(self):
        assert applied_state_for(object(), "rx") is None


class TestDaemonWriter:

    def test_corrected_anchor_writes_a_populated_block(self, tmp_path):
        d = _daemon(tmp_path, _anchor(4_250_000))
        d._write_applied_state(now_fn=lambda: 1000.0)
        block = read_applied_state(
            applied_state_path(tmp_path, "test.local"), now_fn=lambda: 1000.0,
        )
        assert block["tier"] == "T6"
        assert block["radiod_id"] == "test.local"
        assert block["channels"] == {"total": 1, "anchored": 1, "applied": 1}

    def test_uncorrected_anchor_writes_null(self, tmp_path):
        d = _daemon(tmp_path, _anchor(None, source="rtp_to_utc"))
        d._write_applied_state(now_fn=lambda: 1000.0)
        path = applied_state_path(tmp_path, "test.local")
        assert path.exists()                       # running, default mode
        assert read_applied_state(path, now_fn=lambda: 1000.0) is None

    def test_no_anchor_yet_writes_null(self, tmp_path):
        d = _daemon(tmp_path, None)
        d._write_applied_state(now_fn=lambda: 1000.0)
        path = applied_state_path(tmp_path, "test.local")
        assert path.exists()
        assert read_applied_state(path, now_fn=lambda: 1000.0) is None

    def test_time_gate_writes_at_most_once_per_minute(self, tmp_path):
        d = _daemon(tmp_path, _anchor(4_250_000))
        with mock.patch("hamsci_dsp.timing.write_applied_state") as w:
            d._write_applied_state(now_fn=lambda: 1000.0)
            d._write_applied_state(now_fn=lambda: 1030.0)
            d._write_applied_state(now_fn=lambda: 1059.9)
            assert w.call_count == 1
            d._write_applied_state(now_fn=lambda: 1060.0)
            assert w.call_count == 2

    def test_writer_failure_never_escapes(self, tmp_path):
        d = _daemon(tmp_path, _anchor(4_250_000))
        with mock.patch("hamsci_dsp.timing.write_applied_state",
                        side_effect=RuntimeError("disk gone")):
            d._write_applied_state(now_fn=lambda: 1000.0)   # must not raise


class TestDaemonAndContractAgree:
    """What the daemon writes, inventory reads, on every tx instance of
    that radiod.  A path rule that drifted on either side would fail here."""

    def test_inventory_reports_what_the_daemon_wrote(self, tmp_path):
        d = _daemon(tmp_path, _anchor(4_250_000))
        d._write_applied_state(now_fn=lambda: 1000.0)
        with mock.patch("hamsci_dsp.timing.time.time", return_value=1001.0):
            inv = build_inventory(
                {**_config(tmp_path), "radiod": [_radiod_block()]},
                tmp_path / "c.toml",
            )
        assert len(inv["instances"]) == 2
        for inst in inv["instances"]:
            assert inst["uses_timing_calibration"] is True
            assert inst["timing_authority_applied"]["tier"] == "T6"
            assert inst["timing_authority_applied"]["channels"]["total"] == 1

    def test_stale_file_reads_null(self, tmp_path):
        d = _daemon(tmp_path, _anchor(4_250_000))
        d._write_applied_state(now_fn=lambda: 1000.0)
        with mock.patch("hamsci_dsp.timing.time.time", return_value=1000.0 + 3600):
            inv = build_inventory(
                {**_config(tmp_path), "radiod": [_radiod_block()]},
                tmp_path / "c.toml",
            )
        for inst in inv["instances"]:
            assert inst["timing_authority_applied"] is None
