"""Several [[radiod]] blocks per radiod — one daemon instance records them all.

A [[radiod]] block opens one radiod channel at one centre frequency.  A
station that hears CODAR sites on several centres (KFS: 12 centres, 25
transmitters) lists one block per centre, all with the same ``status``.
These tests pin:

  * block resolution — every block whose ``status`` matches runs, and
    with no radiod-id every block runs;
  * block validation — a block whose transmitters sit on different
    centres, or two blocks sharing a ``channel_name`` on one radiod,
    are refused at config apply, ``validate`` and daemon start;
  * the supervising daemon — one source per block at the block's own
    centre, one block's failure leaves the others running, and the
    watchdog only fires while some block is healthy;
  * the whiptail picker's payload builder groups by exact centre.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from codar_sounder import config as cfg
from codar_sounder import configurator
from codar_sounder.contract import build_validate
from codar_sounder.core import daemon as daemon_mod


REPO = Path(__file__).resolve().parent.parent


def _tx(tx_id: str, freq_hz: int) -> dict:
    return {
        "id": tx_id,
        "center_freq_hz": freq_hz,
        "sweep_rate_hz_per_s": -25733.913,
        "sweep_bw_hz": 25734,
        "sweep_repetition_hz": 1.0,
        "tx_lat_deg": 36.18,
        "tx_lon_deg": -75.75,
    }


def _block(channel: str, *txs: dict, status: str = "kfs-status.local") -> dict:
    return {"status": status, "channel_name": channel, "transmitter": list(txs)}


def _config(*blocks: dict, output_dir: str = "/tmp/codar-sounder-test") -> dict:
    return {
        "station": {"callsign": "KFS", "receiver_lat": 37.39,
                    "receiver_lon": -122.41},
        "paths": {"output_dir": output_dir},
        "processing": {"coherent_seconds": 1, "sample_rate_hz": 64000},
        "radiod": list(blocks),
    }


# ---------------------------------------------------------------------------
# Block resolution
# ---------------------------------------------------------------------------

class TestResolveBlocks:

    def test_every_matching_block_is_returned(self):
        a = _block("codar-4550khz", _tx("A", 4_550_000))
        b = _block("codar-4785khz", _tx("B", 4_785_000))
        other = _block("codar-5mhz", _tx("C", 5_000_000), status="x.local")
        got = cfg.resolve_radiod_blocks(_config(a, other, b), "kfs-status.local")
        assert [g["channel_name"] for g in got] == ["codar-4550khz", "codar-4785khz"]

    def test_no_radiod_id_returns_every_block(self):
        a = _block("codar-4550khz", _tx("A", 4_550_000))
        b = _block("codar-4785khz", _tx("B", 4_785_000), status="x.local")
        got = cfg.resolve_radiod_blocks(_config(a, b), None)
        assert len(got) == 2

    def test_no_match_raises(self):
        a = _block("codar-4550khz", _tx("A", 4_550_000))
        with pytest.raises(ValueError, match="no \\[\\[radiod\\]\\] block"):
            cfg.resolve_radiod_blocks(_config(a), "nope.local")


# ---------------------------------------------------------------------------
# Block validation
# ---------------------------------------------------------------------------

class TestBlockErrors:

    def test_shared_centre_within_tolerance_is_fine(self):
        b = _block("codar-4537khz", _tx("A", 4_537_180), _tx("B", 4_538_000))
        assert cfg.radiod_block_errors([b]) == []

    def test_mixed_centres_refused_naming_block_and_tx(self):
        b = _block("codar-4mhz", _tx("A", 4_550_000), _tx("B", 4_785_000),
                   _tx("C", 4_550_500), _tx("D", 4_900_000))
        errs = cfg.radiod_block_errors([b])
        assert len(errs) == 1
        assert "\n" not in errs[0]
        assert "codar-4mhz" in errs[0]
        assert "B" in errs[0] and "D" in errs[0]
        assert not re.search(r"\bC\b", errs[0])

    def test_duplicate_channel_name_on_one_radiod_refused(self):
        a = _block("codar-4mhz", _tx("A", 4_550_000))
        b = _block("codar-4mhz", _tx("B", 4_785_000))
        errs = cfg.radiod_block_errors([a, b])
        assert len(errs) == 1 and "codar-4mhz" in errs[0]

    def test_same_channel_name_on_two_radiods_is_fine(self):
        a = _block("codar-4mhz", _tx("A", 4_550_000))
        b = _block("codar-4mhz", _tx("B", 4_550_000), status="other.local")
        assert cfg.radiod_block_errors([a, b]) == []

    def test_validate_reports_fail(self):
        a = _block("codar-4mhz", _tx("A", 4_550_000), _tx("B", 4_785_000))
        payload = build_validate(_config(a))
        assert payload["ok"] is False
        assert any("codar-4mhz" in i["message"] and i["severity"] == "fail"
                   for i in payload["issues"])

    def test_config_apply_refuses_and_leaves_file(self, tmp_path, monkeypatch):
        path = tmp_path / "c.toml"
        path.write_text("[station]\ncallsign = \"OLD\"\n")
        a = _block("codar-4mhz", _tx("A", 4_550_000), _tx("B", 4_785_000))
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
            {"radiod": [a]})))
        rc = configurator.cmd_config_apply(SimpleNamespace(config=path))
        assert rc == 2
        assert path.read_text() == "[station]\ncallsign = \"OLD\"\n"

    def test_daemon_start_refuses(self):
        a = _block("codar-4mhz", _tx("A", 4_550_000), _tx("B", 4_785_000))
        with pytest.raises(ValueError, match="codar-4mhz"):
            daemon_mod.MultiBlockDaemon(_config(a), [a])


# ---------------------------------------------------------------------------
# The supervising daemon
# ---------------------------------------------------------------------------

class _FakeSource:
    """Stand-in for RadiodIQSource: yields ``n_cpis`` CPIs, or raises."""

    def __init__(self, n_cpis: int = 3, raise_exc: bool = False,
                 delay_s: float = 0.0, **kw):
        self.kw = kw
        self.delay_s = delay_s
        self.n_cpis = n_cpis
        self.raise_exc = raise_exc
        self.anchor = None
        self._stopped = threading.Event()

    def __iter__(self):
        if self.raise_exc:
            raise RuntimeError("radiod went away")
        for _ in range(self.n_cpis):
            time.sleep(self.delay_s)
            if self._stopped.is_set():
                return
            yield (np.zeros(16, dtype=np.complex64),
                   datetime.now(timezone.utc))

    def stop(self):
        self._stopped.set()


@pytest.fixture
def fake_sources(monkeypatch):
    """Patch make_iq_source; the centre frequency picks each source's fate."""
    made: list[_FakeSource] = []
    fates: dict[int, dict] = {}

    def _factory(**kw):
        src = _FakeSource(**fates.get(int(kw["center_freq_hz"]), {}), **kw)
        made.append(src)
        return src

    processed: list[tuple[str, int]] = []

    def _process(self, rx_samples, cpi_start_utc):
        processed.append((self.station_id, self.center_freq_hz))
        return None

    monkeypatch.setattr(daemon_mod, "make_iq_source", _factory)
    monkeypatch.setattr(daemon_mod._TransmitterPipeline, "process_cpi", _process)
    notes: list[str] = []
    monkeypatch.setattr(daemon_mod, "_sd_notify", notes.append)
    return SimpleNamespace(made=made, fates=fates, processed=processed,
                           notes=notes)


class TestMultiBlockDaemon:

    def test_one_source_per_block_at_its_own_centre(self, fake_sources, tmp_path):
        a = _block("codar-4550khz", _tx("A1", 4_550_000), _tx("A2", 4_550_000))
        b = _block("codar-4785khz", _tx("B1", 4_785_000))
        d = daemon_mod.MultiBlockDaemon(
            _config(a, b, output_dir=str(tmp_path)), [a, b])
        centres = sorted(int(s.kw["center_freq_hz"]) for s in fake_sources.made)
        assert centres == [4_550_000, 4_785_000]
        names = sorted(s.kw["channel_name"] for s in fake_sources.made)
        assert names == ["codar-4550khz", "codar-4785khz"]
        assert len(d.blocks) == 2

    def test_every_block_processes(self, fake_sources, tmp_path):
        a = _block("codar-4550khz", _tx("A1", 4_550_000))
        b = _block("codar-4785khz", _tx("B1", 4_785_000))
        d = daemon_mod.MultiBlockDaemon(
            _config(a, b, output_dir=str(tmp_path)), [a, b],
            restart_backoff_s=None, poll_s=0.01)
        d.run()
        ids = {sid for sid, _ in fake_sources.processed}
        assert ids == {"A1", "B1"}

    def test_one_block_raising_leaves_the_other_running(self, fake_sources,
                                                        tmp_path):
        fake_sources.fates[4_550_000] = {"raise_exc": True}
        # B outlives A's failure by a wide margin.
        fake_sources.fates[4_785_000] = {"n_cpis": 5, "delay_s": 0.05}
        a = _block("codar-4550khz", _tx("A1", 4_550_000))
        b = _block("codar-4785khz", _tx("B1", 4_785_000))
        d = daemon_mod.MultiBlockDaemon(
            _config(a, b, output_dir=str(tmp_path)), [a, b],
            restart_backoff_s=None, poll_s=0.01)
        d.run()
        assert [p for p in fake_sources.processed if p[0] == "B1"] == \
            [("B1", 4_785_000)] * 5
        assert not any(p[0] == "A1" for p in fake_sources.processed)

    def test_failed_block_restarts(self, fake_sources, tmp_path):
        a = _block("codar-4550khz", _tx("A1", 4_550_000))
        fake_sources.fates[4_550_000] = {"n_cpis": 1}
        d = daemon_mod.MultiBlockDaemon(
            _config(a, output_dir=str(tmp_path)), [a],
            restart_backoff_s=0.0, poll_s=0.01)
        t = threading.Thread(target=d.run, daemon=True)
        t.start()
        deadline = time.monotonic() + 5
        while len(fake_sources.processed) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        d.stop()
        t.join(5)
        assert len(fake_sources.processed) >= 3

    def test_no_radiod_id_runs_every_block(self, fake_sources, tmp_path):
        a = _block("codar-4550khz", _tx("A1", 4_550_000))
        b = _block("codar-4785khz", _tx("B1", 4_785_000), status="x.local")
        conf = _config(a, b, output_dir=str(tmp_path))
        d = daemon_mod.MultiBlockDaemon(
            conf, cfg.resolve_radiod_blocks(conf, None),
            restart_backoff_s=None, poll_s=0.01)
        d.run()
        assert {sid for sid, _ in fake_sources.processed} == {"A1", "B1"}


class TestWatchdog:
    """Health: a block is healthy while its loop runs and its last CPI (or
    its start, before the first CPI) lies within ``stale_after_s``.  The
    supervisor pets the watchdog only while at least one block is healthy."""

    def _daemon(self, fake_sources, tmp_path):
        a = _block("codar-4550khz", _tx("A1", 4_550_000))
        b = _block("codar-4785khz", _tx("B1", 4_785_000))
        return daemon_mod.MultiBlockDaemon(
            _config(a, b, output_dir=str(tmp_path)), [a, b])

    def test_pets_while_one_block_healthy(self, fake_sources, tmp_path):
        d = self._daemon(fake_sources, tmp_path)
        now = 1000.0
        d.blocks[0].running = True
        d.blocks[0].last_progress = now - 1
        d.blocks[1].running = False
        d._check_health(now)
        assert "WATCHDOG=1" in fake_sources.notes

    def test_silent_when_every_block_down_or_stale(self, fake_sources, tmp_path):
        d = self._daemon(fake_sources, tmp_path)
        now = 1000.0
        d.blocks[0].running = True
        d.blocks[0].last_progress = now - d.stale_after_s - 1   # stale
        d.blocks[1].running = False
        d._check_health(now)
        assert "WATCHDOG=1" not in fake_sources.notes

    def test_state_change_logged_once(self, fake_sources, tmp_path, caplog):
        d = self._daemon(fake_sources, tmp_path)
        d.blocks[0].running = True
        d.blocks[0].last_progress = 1000.0
        d.blocks[1].running = False
        with caplog.at_level("WARNING", logger=daemon_mod.log.name):
            for k in range(5):
                d._check_health(1000.0 + k)
        down = [r for r in caplog.records if "codar-4785khz" in r.getMessage()]
        assert len(down) == 1


# ---------------------------------------------------------------------------
# The whiptail picker's payload builder (scripts/config-wizard.sh)
# ---------------------------------------------------------------------------

def _picker_payload_source() -> str:
    """The python heredoc the wizard pipes into ``config apply``."""
    text = (REPO / "scripts" / "config-wizard.sh").read_text()
    m = re.search(r"^PAYLOAD=\$\(.*?python3 <<'PYEOF'\n(.*?)\nPYEOF\n",
                  text, re.S | re.M)
    assert m, "payload heredoc not found in config-wizard.sh"
    return m.group(1)


def run_picker_payload(stations_json: str, picked: list[str], *,
                       call="KFS", grid="CM87tj", lat=37.39, lon=-122.41,
                       status="kfs-status.local") -> dict:
    env = dict(os.environ, STN_JSON=stations_json, CALL=call, GRID=grid,
               RX_LAT=str(lat), RX_LON=str(lon), STATUS=status,
               PICKED=" ".join(picked))
    out = subprocess.run([sys.executable, "-c", _picker_payload_source()],
                         env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


class TestPickerPayload:

    def _stations(self, *rows):
        return json.dumps({"transmitters": [
            {"id": i, "center_freq_hz": f, "band": "4–5 MHz (long-range)",
             "sweep_rate_hz_per_s": -25733.913, "sweep_bw_hz": 25734.0,
             "sweep_repetition_hz": 1.0, "tx_lat_deg": 36.0,
             "tx_lon_deg": -122.0}
            for i, f in rows]})

    def test_groups_by_exact_centre(self):
        stn = self._stations(("A", 4_550_000), ("B", 4_785_000),
                             ("C", 4_550_000))
        p = run_picker_payload(stn, ["A", "B", "C"])
        blocks = {b["channel_name"]: [t["id"] for t in b["transmitter"]]
                  for b in p["radiod"]}
        assert blocks == {"codar-4550khz": ["A", "C"],
                          "codar-4785khz": ["B"]}
        assert {b["status"] for b in p["radiod"]} == {"kfs-status.local"}
        assert cfg.radiod_block_errors(p["radiod"]) == []

    def test_khz_name_is_integer(self):
        stn = self._stations(("A", 4_537_180))
        p = run_picker_payload(stn, ["A"])
        assert p["radiod"][0]["channel_name"] == "codar-4537khz"
