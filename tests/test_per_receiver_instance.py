"""One instance per receiver, keyed by reporter
(tasks/transporter-reporters/2026-11-18-design.md).

A receiver (antenna + RX888 + radiod) names every row it produces.  On a
transporter (AC0G-B2 for B1A and B1B) codar-sounder runs one instance per
receiver, codar-sounder@AC0G-B1A, from /etc/codar-sounder/AC0G-B1A.toml:
[instance] reporter_id = "AC0G/B1A" and only that receiver's [[radiod]]
blocks.  A station still on the legacy shared config keeps today's
behaviour: radiod-keyed instance, empty sigmondb reporter.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from codar_sounder import cli, config as cfgmod, configurator
from codar_sounder.core import daemon as daemon_mod
from codar_sounder.core import sigmondb as sdb

KFS = (37.40, -122.15)

TX = '''
[[radiod.transmitter]]
id                  = "{id}"
center_freq_hz      = {hz}
sweep_rate_hz_per_s = -25733.913
sweep_bw_hz         = 25734
sweep_repetition_hz = 1
tx_lat_deg          = 36.69
tx_lon_deg          = -75.92
'''


def _block(status, hz, tx_id):
    return (f'[[radiod]]\nstatus = "{status}"\n'
            f'channel_name = "codar-{hz // 1000}khz"\n'
            + TX.format(id=tx_id, hz=hz))


STATION = ('[station]\ncallsign = "AC0G"\ngrid_square = "EM38"\n'
           'receiver_lat = 38.9\nreceiver_lon = -92.3\n'
           '[paths]\noutput_dir = "{out}"\nlog_dir = "{out}"\n')


@pytest.fixture
def etc(tmp_path, monkeypatch):
    """A config dir standing in for /etc/codar-sounder."""
    d = tmp_path / "etc"
    d.mkdir()
    monkeypatch.setattr(cfgmod, "PER_INSTANCE_CONFIG_DIR", d)
    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG_PATH", d / "codar-sounder-config.toml")
    monkeypatch.setattr(configurator, "DEFAULT_CONFIG_PATH", d / "codar-sounder-config.toml")
    monkeypatch.delenv("CODAR_SOUNDER_CONFIG", raising=False)
    return d


def _write_instance(etc, key, rid, status, tmp_path):
    p = etc / f"{key}.toml"
    p.write_text(f'[instance]\nreporter_id = "{rid}"\n\n'
                 + STATION.format(out=tmp_path)
                 + _block(status, 4463000, "LISL")
                 + _block(status, 4537000, "CEDR"))
    return p


def _write_shared(etc, tmp_path):
    p = etc / "codar-sounder-config.toml"
    p.write_text(STATION.format(out=tmp_path)
                 + _block("b1a-status.local", 4463000, "LISL")
                 + _block("b1b-status.local", 4463000, "LISL"))
    return p


# --- config resolution --------------------------------------------------------

def test_reporter_instance_resolves_its_own_file(etc, tmp_path):
    p = _write_instance(etc, "AC0G-B1A", "AC0G/B1A", "b1a-status.local", tmp_path)
    assert cfgmod.resolve_config_path(instance="AC0G-B1A") == p
    # The pre-key unit spelling still finds the key-named file.
    assert cfgmod.resolve_config_path(instance="AC0G\\x3dB1A") == p


def test_radiod_instance_without_a_file_falls_back_to_shared(etc, tmp_path):
    shared = _write_shared(etc, tmp_path)
    with pytest.warns(DeprecationWarning):
        assert cfgmod.resolve_config_path(instance="b1a-status.local") == shared


def test_reporter_and_radiod_instance_names():
    assert cfgmod.is_reporter_instance("AC0G-B1A")
    assert cfgmod.is_reporter_instance("AC0G\\x3dB1A")
    assert not cfgmod.is_reporter_instance("b1a-status.local")
    assert not cfgmod.is_reporter_instance("my-rx888")
    assert not cfgmod.is_reporter_instance("")


def test_per_instance_paths_skip_the_shared_file(etc, tmp_path):
    _write_shared(etc, tmp_path)
    a = _write_instance(etc, "AC0G-B1A", "AC0G/B1A", "b1a-status.local", tmp_path)
    b = _write_instance(etc, "AC0G-B1B", "AC0G/B1B", "b1b-status.local", tmp_path)
    (etc / "stray.toml").write_text('[station]\ncallsign = "X"\n')
    assert cfgmod.per_instance_config_paths() == [a, b]


# --- daemon: blocks and reporter ---------------------------------------------

def _run_daemon(argv, monkeypatch):
    seen = {}

    class FakeDaemon:
        def __init__(self, config, blocks, reporter_id=None):
            seen.update(blocks=blocks, reporter_id=reporter_id)

        def run(self):
            pass

    monkeypatch.setattr(daemon_mod, "MultiBlockDaemon", FakeDaemon)
    monkeypatch.setattr(cli, "_install_sighup_handler", lambda: None)
    monkeypatch.setattr(sys, "argv", ["codar-sounder", *argv])
    cli.main()
    return seen


def test_daemon_for_a_reporter_instance(etc, tmp_path, monkeypatch):
    _write_shared(etc, tmp_path)          # a shared config beside it is not read
    _write_instance(etc, "AC0G-B1A", "AC0G/B1A", "b1a-status.local", tmp_path)
    seen = _run_daemon(["daemon", "--instance", "AC0G-B1A",
                        "--radiod-id", "AC0G-B1A"], monkeypatch)
    assert seen["reporter_id"] == "AC0G/B1A"
    assert [b["status"] for b in seen["blocks"]] == ["b1a-status.local"] * 2


def test_daemon_legacy_radiod_instance_unchanged(etc, tmp_path, monkeypatch):
    _write_shared(etc, tmp_path)
    with pytest.warns(DeprecationWarning):
        seen = _run_daemon(["daemon", "--instance", "b1b-status.local",
                            "--radiod-id", "b1b-status.local"], monkeypatch)
    assert seen["reporter_id"] is None
    assert [b["status"] for b in seen["blocks"]] == ["b1b-status.local"]


def test_legacy_shared_config_keeps_strict_radiod_matching(etc, tmp_path):
    shared = _write_shared(etc, tmp_path)
    config = cfgmod.load_config(shared)
    with pytest.raises(ValueError):
        cli._daemon_blocks(config, shared, "nope.local", None, None)


# --- sigmondb rows carry the receiver's reporter ------------------------------

class _Rows:
    def __init__(self):
        self.rows = []

    def insert(self, rows):
        self.rows.extend(rows)

    def close(self):
        pass


def _emit(reporter_id, tmp_path, monkeypatch):
    from codar_sounder.core.dechirp import dechirp
    import numpy as np
    arr, cap = _Rows(), _Rows()
    monkeypatch.setattr(sdb, "make_writers", lambda: (arr, cap))
    em = daemon_mod._make_sigmondb_emitter(
        reporter_id, {"paths": {"output_dir": str(tmp_path)}})
    fs, kappa = 64_000.0, -25_733.913
    t = np.arange(8 * 64_000) / fs - 0.0012
    tin = np.mod(t, 1.0)
    rx = np.exp(1j * np.pi * kappa * tin * tin).astype(np.complex64)
    res = dechirp(rx, sample_rate_hz=fs, sweep_rate_hz_per_s=kappa,
                  sweep_repetition_hz=1.0)
    em.emit(result=res,
            cpi_start_utc=datetime(2026, 11, 18, 12, 0, 7, tzinfo=timezone.utc),
            tdma_offset_samples=0, centre_hz=4_463_000.0, period_s=1.0,
            expected=[], duration_s=8.0, anchor=None, radiod="b1a-status.local")
    assert arr.rows and cap.rows
    return {r["reporter_id"] for r in arr.rows + cap.rows}


def test_rows_carry_the_instance_reporter(tmp_path, monkeypatch):
    assert _emit("AC0G/B1A", tmp_path, monkeypatch) == {"AC0G/B1A"}


def test_legacy_shared_config_rows_stay_empty(tmp_path, monkeypatch):
    assert _emit(None, tmp_path, monkeypatch) == {""}


# --- inventory / validate -----------------------------------------------------

def _contract(argv, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["codar-sounder", *argv])
    try:
        cli.main()
    except SystemExit:
        pass
    return json.loads(capsys.readouterr().out)


def test_inventory_for_one_instance(etc, tmp_path, monkeypatch, capsys):
    _write_instance(etc, "AC0G-B1A", "AC0G/B1A", "b1a-status.local", tmp_path)
    inv = _contract(["inventory", "--json", "--instance", "AC0G-B1A"],
                    monkeypatch, capsys)
    assert {i["reporter_id"] for i in inv["instances"]} == {"AC0G/B1A"}
    assert {i["radiod_id"] for i in inv["instances"]} == {"b1a-status.local"}
    assert all(v["process"].endswith("/AC0G-B1A.log")
               for v in inv["log_paths"].values())


def test_migrated_station_inventories_every_receiver(etc, tmp_path, monkeypatch, capsys):
    _write_instance(etc, "AC0G-B1A", "AC0G/B1A", "b1a-status.local", tmp_path)
    _write_instance(etc, "AC0G-B1B", "AC0G/B1B", "b1b-status.local", tmp_path)
    inv = _contract(["inventory", "--json"], monkeypatch, capsys)
    assert {(i["reporter_id"], i["radiod_id"]) for i in inv["instances"]} == {
        ("AC0G/B1A", "b1a-status.local"), ("AC0G/B1B", "b1b-status.local")}
    assert len(inv["config_paths"]) == 2
    val = _contract(["validate", "--json"], monkeypatch, capsys)
    assert val["ok"] is True


def test_legacy_inventory_reads_the_shared_config(etc, tmp_path, monkeypatch, capsys):
    shared = _write_shared(etc, tmp_path)
    _write_instance(etc, "AC0G-B1A", "AC0G/B1A", "b1a-status.local", tmp_path)
    inv = _contract(["inventory", "--json"], monkeypatch, capsys)
    assert inv["config_path"] == str(shared)
    assert {i["reporter_id"] for i in inv["instances"]} == {None}


# --- config init for one receiver ---------------------------------------------

def _init(etc, tmp_path, env, **kw):
    args = SimpleNamespace(non_interactive=True, reconfig=False, config=None,
                           instance=None, reporter_id=None, radiod_status=None,
                           max_centres=None)
    for k, v in kw.items():
        setattr(args, k, v)
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("SIGMOND_", "STATION_", "CODAR_"))}
    with mock.patch.dict(os.environ, {**base, **env}, clear=True), \
         mock.patch.object(configurator, "_discover_radiods", return_value=[]), \
         mock.patch.object(configurator, "COORDINATION_ENV", tmp_path / "absent.env"):
        return configurator.cmd_config_init(args)


SIGMOND_ENV = {"SIGMOND_RADIOD_COUNT": "2", "STATION_CALL": "AC0G",
               "STATION_LAT": str(KFS[0]), "STATION_LON": str(KFS[1]),
               "STATION_REPORTER_ID": "AC0G/B2",
               # Every radiod on the station: the instance must take only its own.
               "RADIOD_B1A_STATUS": "b1a-status.local",
               "RADIOD_B1B_STATUS": "b1b-status.local"}


def test_init_writes_the_instance_block_and_only_its_radiod(etc, tmp_path):
    rc = _init(etc, tmp_path, {**SIGMOND_ENV, "SIGMOND_INSTANCE": "AC0G-B1A",
                               "SIGMOND_RADIOD_STATUS": "b1a-status.local"},
               reporter_id="AC0G/B1A")
    assert rc == 0
    cfg = tomllib.loads((etc / "AC0G-B1A.toml").read_text())
    assert cfg["instance"]["reporter_id"] == "AC0G/B1A"
    assert cfg["radiod"]
    assert {b["status"] for b in cfg["radiod"]} == {"b1a-status.local"}
    assert not (etc / "codar-sounder-config.toml").exists()


def test_init_takes_station_reporter_when_its_key_is_the_instance(etc, tmp_path):
    rc = _init(etc, tmp_path, {**SIGMOND_ENV, "STATION_REPORTER_ID": "AC0G/B4"},
               instance="AC0G-B4", radiod_status="b4-status.local")
    assert rc == 0
    cfg = tomllib.loads((etc / "AC0G-B4.toml").read_text())
    assert cfg["instance"]["reporter_id"] == "AC0G/B4"


def test_init_refuses_the_transporters_reporter_for_a_receiver(etc, tmp_path):
    rc = _init(etc, tmp_path, SIGMOND_ENV, instance="AC0G-B1A",
               radiod_status="b1a-status.local")
    assert rc == 1
    assert not (etc / "AC0G-B1A.toml").exists()


def test_init_refuses_without_the_receivers_radiod(etc, tmp_path):
    rc = _init(etc, tmp_path, SIGMOND_ENV, instance="AC0G-B1A",
               reporter_id="AC0G/B1A")
    assert rc == 1


def test_init_refuses_a_reporter_of_another_key(etc, tmp_path):
    rc = _init(etc, tmp_path, SIGMOND_ENV, instance="AC0G-B1A",
               reporter_id="AC0G/B1B", radiod_status="b1a-status.local")
    assert rc == 1


def _centres(etc, key):
    cfg = tomllib.loads((etc / f"{key}.toml").read_text())
    return len(cfg["radiod"])


def test_max_centres_argument_sets_the_cap(etc, tmp_path):
    for n in (1, 3):
        rc = _init(etc, tmp_path, SIGMOND_ENV, instance="AC0G-B1A",
                   reporter_id="AC0G/B1A", radiod_status="b1a-status.local",
                   max_centres=n, reconfig=True)
        assert rc == 0
        assert _centres(etc, "AC0G-B1A") == n


def test_max_centres_argument_beats_the_env(etc, tmp_path):
    rc = _init(etc, tmp_path, {**SIGMOND_ENV, "CODAR_MAX_CENTRES": "3"},
               instance="AC0G-B1A", reporter_id="AC0G/B1A",
               radiod_status="b1a-status.local", max_centres=1)
    assert rc == 0
    assert _centres(etc, "AC0G-B1A") == 1


def test_legacy_init_without_an_instance_is_unchanged(etc, tmp_path):
    rc = _init(etc, tmp_path, {**SIGMOND_ENV,
                               "SIGMOND_RADIOD_STATUS": "kfs-omni-status.local"})
    assert rc == 0
    cfg = tomllib.loads((etc / "codar-sounder-config.toml").read_text())
    assert "instance" not in cfg
    assert {b["status"] for b in cfg["radiod"]} == {"kfs-omni-status.local"}


def test_cli_parses_max_centres_and_instance(monkeypatch):
    seen = {}
    monkeypatch.setattr(configurator, "cmd_config_init",
                        lambda a: seen.update(vars(a)) or 0)
    monkeypatch.setattr(sys, "argv", ["codar-sounder", "config", "init",
                                      "--non-interactive", "--instance", "AC0G-B1A",
                                      "--reporter-id", "AC0G/B1A",
                                      "--radiod-status", "b1a-status.local",
                                      "--max-centres", "4"])
    with pytest.raises(SystemExit):
        cli.main()
    assert seen["max_centres"] == 4 and seen["instance"] == "AC0G-B1A"
    assert seen["reporter_id"] == "AC0G/B1A"
    assert seen["radiod_status"] == "b1a-status.local"
