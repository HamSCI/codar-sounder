"""Unattended transmitter choice (Michael, 2026-11-12): codar-sounder is a
core sigmond client, so `smd bringup` must leave it recording something.
Under sigmond, `config init --non-interactive` picks the nearest transmitters
and stops at a cap on centre frequencies, one channel each: the station's
load budget."""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from codar_sounder import configurator
from codar_sounder.core.stations import audible_transmitters, pick_centres

# KFS (Half Moon Bay hills) — many Bay Area CODAR sites lie within 300 km.
KFS = (37.40, -122.15)


def test_pick_stops_at_the_cap_and_keeps_every_site_on_a_chosen_centre():
    cands = audible_transmitters(*KFS)
    picked = pick_centres(cands, max_centres=3)
    centres = {c.center_freq_hz for c in picked}
    assert len(centres) == 3
    # Nearest site first: its centre is always chosen.
    assert cands[0].center_freq_hz in centres
    # Every candidate on a chosen centre comes along (one channel hears them all).
    assert {c.id for c in cands if c.center_freq_hz in centres} == {c.id for c in picked}


def test_pick_of_zero_or_nothing_in_range_is_empty():
    assert pick_centres(audible_transmitters(*KFS), max_centres=0) == []
    assert pick_centres([], max_centres=4) == []


def _init(tmp_path, env):
    target = tmp_path / "codar.toml"
    args = SimpleNamespace(non_interactive=True, reconfig=False, config=str(target))
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("SIGMOND_", "STATION_", "CODAR_"))}
    with mock.patch.dict(os.environ, {**base, **env}, clear=True), \
         mock.patch.object(configurator, "_discover_radiods", return_value=[]), \
         mock.patch.object(configurator, "COORDINATION_ENV", tmp_path / "absent.env"):
        rc = configurator.cmd_config_init(args)
    return rc, (tomllib.loads(target.read_text()) if target.exists() else None)


def test_under_sigmond_init_writes_capped_blocks_for_the_bound_radiod(tmp_path):
    rc, cfg = _init(tmp_path, {
        "SIGMOND_RADIOD_COUNT": "1", "SIGMOND_RADIOD_STATUS": "kfs-omni-status.local",
        "STATION_CALL": "KFS", "STATION_GRID": "CM87ui",
        "STATION_LAT": str(KFS[0]), "STATION_LON": str(KFS[1]),
        "CODAR_MAX_CENTRES": "2"})
    assert rc == 0
    blocks = cfg["radiod"]
    assert len(blocks) == 2
    assert {b["status"] for b in blocks} == {"kfs-omni-status.local"}
    assert "DUCK" not in {t["id"] for b in blocks for t in b["transmitter"]}  # template example gone
    assert cfg["station"]["receiver_lat"] == KFS[0]


def test_without_a_cap_init_records_two_centres(tmp_path):
    rc, cfg = _init(tmp_path, {
        "SIGMOND_RADIOD_COUNT": "1", "SIGMOND_RADIOD_STATUS": "kfs-omni-status.local",
        "STATION_CALL": "KFS", "STATION_GRID": "CM87ui",
        "STATION_LAT": str(KFS[0]), "STATION_LON": str(KFS[1])})
    assert rc == 0
    assert len(cfg["radiod"]) == 2


def test_standalone_init_still_copies_the_template(tmp_path):
    rc, cfg = _init(tmp_path, {"STATION_LAT": str(KFS[0]), "STATION_LON": str(KFS[1])})
    assert rc == 0
    assert "DUCK" in {t["id"] for b in cfg["radiod"] for t in b["transmitter"]}


def test_under_sigmond_without_coordinates_or_grid_keeps_the_template(tmp_path, capsys):
    rc, cfg = _init(tmp_path, {"SIGMOND_RADIOD_COUNT": "1",
                               "SIGMOND_RADIOD_STATUS": "x-status.local"})
    assert rc == 0
    assert "DUCK" in {t["id"] for b in cfg["radiod"] for t in b["transmitter"]}


def test_sample_rate_holds_the_widest_sweep():
    from codar_sounder.core.stations import sample_rate_for
    assert sample_rate_for([25_734]) == 64_000
    assert sample_rate_for([25_734, 49_630]) == 64_000
    assert sample_rate_for([25_734, 99_259]) == 128_000
    assert sample_rate_for([]) == 64_000


def test_unattended_init_sets_the_sample_rate_for_what_it_picked(tmp_path):
    from codar_sounder.core.stations import sample_rate_for
    rc, cfg = _init(tmp_path, {
        "SIGMOND_RADIOD_COUNT": "1", "SIGMOND_RADIOD_STATUS": "kfs-omni-status.local",
        "STATION_LAT": str(KFS[0]), "STATION_LON": str(KFS[1]), "CODAR_MAX_CENTRES": "6"})
    assert rc == 0
    bws = [t["sweep_bw_hz"] for b in cfg["radiod"] for t in b["transmitter"]]
    assert cfg["processing"]["sample_rate_hz"] == sample_rate_for(bws)


def test_unattended_init_skips_sweeps_no_channel_can_hold(tmp_path):
    rc, cfg = _init(tmp_path, {
        "SIGMOND_RADIOD_COUNT": "1", "SIGMOND_RADIOD_STATUS": "kfs-omni-status.local",
        "STATION_LAT": str(KFS[0]), "STATION_LON": str(KFS[1]), "CODAR_MAX_CENTRES": "40"})
    assert rc == 0
    assert all(1.2 * abs(t["sweep_bw_hz"]) <= 128_000
               for b in cfg["radiod"] for t in b["transmitter"])


def test_under_sigmond_the_grid_centre_serves_without_coordinates(tmp_path):
    # AC1SN, 2026-11-12: coordination held grid FN42HN and no lat/lon.
    rc, cfg = _init(tmp_path, {"SIGMOND_RADIOD_COUNT": "1",
                               "SIGMOND_RADIOD_STATUS": "AC1SN-status.local",
                               "STATION_GRID": "FN42HN"})
    assert rc == 0
    assert cfg["station"]["receiver_lat"] == 42.5625
    assert cfg["station"]["receiver_lon"] == -71.375
    assert "DUCK" not in {t["id"] for b in cfg["radiod"] for t in b["transmitter"]}


def test_two_radiods_get_the_same_channels_each(tmp_path):
    # AC0G-B2 reads B1A and B1B, on different antennas; sigmond leaves
    # SIGMOND_RADIOD_STATUS empty and publishes RADIOD_*_STATUS for each.
    rc, cfg = _init(tmp_path, {
        "SIGMOND_RADIOD_COUNT": "2", "STATION_GRID": "EM38ww",
        "RADIOD_AC0G_B1_A_STATUS_LOCAL_STATUS": "ac0g-b1-a-status.local",
        "RADIOD_AC0G_B1_B_STATUS_LOCAL_STATUS": "ac0g-b1-b-status.local",
        "CODAR_MAX_CENTRES": "2"})
    assert rc == 0
    by = {}
    for b in cfg["radiod"]:
        by.setdefault(b["status"], []).append(b["channel_name"])
    assert set(by) == {"ac0g-b1-a-status.local", "ac0g-b1-b-status.local"}
    assert by["ac0g-b1-a-status.local"] == by["ac0g-b1-b-status.local"]
    assert len(by["ac0g-b1-a-status.local"]) == 2


def test_radiods_come_from_coordination_env_when_the_environment_lacks_them(tmp_path):
    f = tmp_path / "coordination.env"
    f.write_text("RADIOD_X_STATUS=x-status.local\nRADIOD_Y_STATUS=y-status.local\nOTHER=1\n")
    assert configurator._sigmond_radiods({}, f) == ["x-status.local", "y-status.local"]
    assert configurator._sigmond_radiods({"SIGMOND_RADIOD_STATUS": "z.local"}, f) == ["z.local"]


def test_a_configured_file_is_never_picked_over(tmp_path):
    # W2NAF, UACNJ, AC0G/ND kept hand-built configs from the capture work; an
    # unattended init must leave a config that names a real radiod alone.
    target = tmp_path / "codar.toml"
    target.write_text('[station]\ncallsign = "W2NAF"\n\n[[radiod]]\nstatus = "w2naf-status.local"\n'
                      'channel_name = "codar-4mhz"\n\n[[radiod.transmitter]]\nid = "HOOK"\n'
                      'center_freq_hz = 4463000\nsweep_rate_hz_per_s = -25733.913\nsweep_bw_hz = 25734\n'
                      'sweep_repetition_hz = 1.0\ntx_lat_deg = 40.4\ntx_lon_deg = -73.9\n')
    before = target.read_text()
    args = SimpleNamespace(non_interactive=True, reconfig=False, config=str(target))
    with mock.patch.dict(os.environ, {"SIGMOND_RADIOD_COUNT": "1",
                                      "SIGMOND_RADIOD_STATUS": "w2naf-status.local",
                                      "STATION_GRID": "FN21ei"}), \
         mock.patch.object(configurator, "_discover_radiods", return_value=[]):
        assert configurator.cmd_config_init(args) == 0
    assert target.read_text() == before


def test_the_installer_template_is_picked_over(tmp_path):
    target = tmp_path / "codar.toml"
    template = (Path(configurator.__file__).resolve().parents[2] / "config"
                / "codar-sounder-config.toml.template").read_text()
    target.write_text(template)
    args = SimpleNamespace(non_interactive=True, reconfig=False, config=str(target))
    with mock.patch.dict(os.environ, {"SIGMOND_RADIOD_COUNT": "1",
                                      "SIGMOND_RADIOD_STATUS": "sigma-status.local",
                                      "STATION_GRID": "EM38ww"}), \
         mock.patch.object(configurator, "_discover_radiods", return_value=[]), \
         mock.patch.object(configurator, "COORDINATION_ENV", tmp_path / "absent.env"):
        assert configurator.cmd_config_init(args) == 0
    import tomllib
    cfg = tomllib.loads(target.read_text())
    assert {b["status"] for b in cfg["radiod"]} == {"sigma-status.local"}
