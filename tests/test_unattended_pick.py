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
         mock.patch.object(configurator, "_discover_radiods", return_value=[]):
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


def test_standalone_init_still_copies_the_template(tmp_path):
    rc, cfg = _init(tmp_path, {"STATION_LAT": str(KFS[0]), "STATION_LON": str(KFS[1])})
    assert rc == 0
    assert "DUCK" in {t["id"] for b in cfg["radiod"] for t in b["transmitter"]}


def test_under_sigmond_without_coordinates_keeps_the_template(tmp_path, capsys):
    rc, cfg = _init(tmp_path, {"SIGMOND_RADIOD_COUNT": "1",
                               "SIGMOND_RADIOD_STATUS": "x-status.local"})
    assert rc == 0
    assert "DUCK" in {t["id"] for b in cfg["radiod"] for t in b["transmitter"]}
