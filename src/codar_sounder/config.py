"""TOML config loader for codar-sounder.

Config layout (per-host, multi-radiod aware):

    [station]            — operator identity + receiver location
    [paths]              — output_dir, log_dir
    [[radiod]]           — one block per radiod channel (one centre frequency)
        status          = "..."   (radiod mDNS status name)
        channel_name    = "..."   (unique among blocks of one radiod)
        [[radiod.transmitter]]
            id, center_freq_hz, sweep_rate_hz_per_s,
            sweep_bw_hz, sweep_repetition_hz,
            tx_lat_deg, tx_lon_deg
    [processing]         — coherent_seconds, range bounds, snr threshold

Every transmitter in a block shares the block's centre frequency
(within CENTER_TOLERANCE_HZ): the block opens one radiod channel tuned
there.  A radiod that serves several centres carries several blocks
with the same ``status``.  The daemon is started with --radiod-id <id>;
resolve_radiod_blocks() returns every matching block and the daemon
records them all.  Multiple daemons can run on one host, one per radiod.
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path
from typing import Any, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]


DEFAULT_CONFIG_PATH = Path("/etc/codar-sounder/codar-sounder-config.toml")
PER_INSTANCE_CONFIG_DIR = Path("/etc/codar-sounder")


def resolve_config_path(
    instance: Optional[str] = None,
    explicit_path: Optional[Path] = None,
) -> Path:
    """Resolve the config file path per sigmond MULTI-INSTANCE-ARCHITECTURE.md §4.

    Precedence: explicit_path > $CODAR_SOUNDER_CONFIG > per-instance
    /etc/codar-sounder/<instance>.toml (when given and exists) > legacy
    /etc/codar-sounder/codar-sounder-config.toml (with DeprecationWarning
    when --instance was given but the per-instance file is missing).
    """
    if explicit_path is not None:
        return Path(explicit_path)
    env_override = os.environ.get("CODAR_SOUNDER_CONFIG")
    if env_override:
        return Path(env_override)
    if instance:
        per_instance = PER_INSTANCE_CONFIG_DIR / f"{instance}.toml"
        if per_instance.exists():
            return per_instance
        warnings.warn(
            f"per-instance config {per_instance} not found; falling "
            f"back to legacy shared config {DEFAULT_CONFIG_PATH}. "
            f"Migrate this host with `sudo smd instance migrate` "
            f"(MULTI-INSTANCE-ARCHITECTURE.md §6).",
            DeprecationWarning,
            stacklevel=2,
        )
    return DEFAULT_CONFIG_PATH


def extract_reporter_id(config_or_path) -> Optional[str]:
    """Read reporter_id from a per-instance config's [instance] block.

    Accepts either a parsed TOML dict or a Path.  Returns None when no
    [instance] block is present (legacy shared-config world).  Callers
    should NOT fall back to the systemd %i / --instance value as the
    reporter_id during the cutover — %i is typically a radiod identifier,
    not a reporter ID; using it would propagate a misleading value into
    rows.  Leave reporter_id None; row construction falls back to
    radiod_id (the existing legacy `instance` field's semantic).
    """
    if isinstance(config_or_path, dict):
        raw = config_or_path
    else:
        path = Path(config_or_path)
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                raw = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            return None
    inst = raw.get("instance")
    if not isinstance(inst, dict):
        return None
    rid = inst.get("reporter_id")
    if not isinstance(rid, str) or not rid:
        return None
    return rid


def load_config(path: Path) -> dict:
    """Load and parse the TOML config; raises FileNotFoundError if missing."""
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    with open(path, "rb") as f:
        return tomllib.load(f)


def radiod_blocks(config: dict) -> list[dict]:
    """Return the list of [[radiod]] blocks (always a list, even if one)."""
    blocks = config.get("radiod", [])
    if isinstance(blocks, dict):
        return [blocks]
    return list(blocks)


def resolve_radiod_block(config: dict, radiod_id: str | None) -> dict:
    """Pick the [[radiod]] block matching radiod_id.

    Phase 6 cutover (RADIOD-IDENTIFICATION.md §3.1): the mDNS
    multicast status name is the only identifier.  Operators with
    legacy `id`/`status_dns` configs must run
    ``sudo smd radiod migrate --yes``.

    If radiod_id is None, returns the first block (single-radiod hosts).
    Raises ValueError if no match.
    """
    blocks = radiod_blocks(config)
    if not blocks:
        raise ValueError("config has no [[radiod]] blocks")
    if radiod_id is None:
        return blocks[0]
    for block in blocks:
        if block.get("status") == radiod_id:
            return block
    have = ", ".join(b.get("status", "<unnamed>") for b in blocks)
    raise ValueError(
        f"no [[radiod]] block with status={radiod_id!r}; have: {have}"
    )


def resolve_radiod_blocks(config: dict, radiod_id: str | None) -> list[dict]:
    """Return every [[radiod]] block whose ``status`` equals radiod_id.

    With radiod_id None, returns every block (single-radiod hosts and
    the per-instance config, where the file itself names the source).
    Raises ValueError if no block matches.
    """
    blocks = radiod_blocks(config)
    if not blocks:
        raise ValueError("config has no [[radiod]] blocks")
    if radiod_id is None:
        return blocks
    matched = [b for b in blocks if b.get("status") == radiod_id]
    if matched:
        return matched
    have = ", ".join(b.get("status", "<unnamed>") for b in blocks)
    raise ValueError(
        f"no [[radiod]] block with status={radiod_id!r}; have: {have}"
    )


#: Every transmitter in a [[radiod]] block must sit within this many Hz of
#: the block's centre (its first transmitter's ``center_freq_hz``).  The
#: block opens one 64 kHz channel there; a chirp centred further out is
#: partly or wholly outside it.  This bounds what the channel can HEAR,
#: not what the dechirp ranges correctly: dechirp assumes each chirp sits
#: at the channel centre, so an offset of df Hz shifts that transmitter's
#: group range by c * df / |sweep_rate| (about 580 km per 50 Hz at
#: 4.5 MHz).  The picker therefore groups by EXACT centre.
CENTER_TOLERANCE_HZ = 2000


def block_center_freq_hz(block: dict) -> Optional[int]:
    """The centre the block's channel tunes to: its first transmitter's."""
    for tx in transmitters(block):
        if "center_freq_hz" in tx:
            return int(tx["center_freq_hz"])
    return None


def radiod_block_errors(blocks: list[dict]) -> list[str]:
    """One-line reasons to refuse a set of [[radiod]] blocks.

    Refuses a block whose transmitters do not all share its centre
    (within CENTER_TOLERANCE_HZ), and two blocks of one radiod that
    share a ``channel_name``.  Config apply, ``validate`` and daemon
    start all run this check.
    """
    errors: list[str] = []
    seen: dict[tuple[str, str], int] = {}
    for block in blocks:
        status = str(block.get("status", "<unnamed>"))
        name = str(block.get("channel_name", ""))
        if name:
            seen[(status, name)] = seen.get((status, name), 0) + 1
        centre = block_center_freq_hz(block)
        if centre is None:
            continue
        off = [
            f"{t.get('id', '<unnamed>')} ({int(t['center_freq_hz'])} Hz)"
            for t in transmitters(block)
            if "center_freq_hz" in t
            and abs(int(t["center_freq_hz"]) - centre) > CENTER_TOLERANCE_HZ
        ]
        if off:
            errors.append(
                f"[[radiod]] block {name or '<no channel_name>'} "
                f"(status={status}) tunes to {centre} Hz; transmitters "
                f"more than {CENTER_TOLERANCE_HZ} Hz away cannot be heard "
                f"on it: {', '.join(off)}"
            )
    for (status, name), n in seen.items():
        if n > 1:
            errors.append(
                f"channel_name {name!r} appears in {n} [[radiod]] blocks "
                f"for status={status}; each block needs its own"
            )
    return errors


def transmitters(block: dict) -> list[dict]:
    """Return the [[radiod.transmitter]] list for a radiod block."""
    txs = block.get("transmitter", [])
    if isinstance(txs, dict):
        return [txs]
    return list(txs)


def transmitter_freqs(block: dict) -> list[int]:
    """Centre frequencies (Hz) of every transmitter in a radiod block."""
    return sorted({int(t["center_freq_hz"]) for t in transmitters(block)
                   if "center_freq_hz" in t})


REQUIRED_TX_FIELDS = (
    "id",
    "center_freq_hz",
    "sweep_rate_hz_per_s",
    "sweep_bw_hz",
    "sweep_repetition_hz",
    "tx_lat_deg",
    "tx_lon_deg",
)


def missing_tx_fields(tx: dict) -> list[str]:
    """Return required fields absent from a [[radiod.transmitter]] block."""
    return [f for f in REQUIRED_TX_FIELDS if f not in tx]


# TX-RX geometry comes from the shared suite library (hamsci-dsp), which
# is backed by geographiclib (Karney geodesics on the WGS-84 ellipsoid).
# These thin wrappers keep codar's historical call sites (contract.py,
# cli.py tdma-scan) working while the actual math lives in one place for
# the whole suite.  Switching from the former local haversine shifts
# distances ~0.5% (spherical → ellipsoidal) — immaterial to codar's
# 50/2000 km validate thresholds and sample-offset estimates.
from hamsci_dsp.geometry import bearing_deg, great_circle_km


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Geodesic (WGS-84) TX-RX distance in km — delegates to hamsci-dsp.

    Name retained for backwards compatibility with existing call sites;
    the implementation is now the suite-shared geodesic, not haversine.
    """
    return great_circle_km(lat1, lon1, lat2, lon2)


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial geodesic bearing (degrees, 0–360) from point 1 toward point 2.

    Delegates to hamsci-dsp.  Annotates each transmitter candidate with
    the compass direction an operator should expect it from (mirrors
    superdarn-sounder's core/radars.py).  Informational only — codar
    reception is omnidirectional so bearing doesn't gate selection.
    """
    return bearing_deg(lat1, lon1, lat2, lon2)
