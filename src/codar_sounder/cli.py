"""codar-sounder CLI entry point.

Subcommands:
    inventory   — contract v0.8 §3 inventory JSON
    validate    — contract v0.8 §12 config validation
    version     — version + git block
    daemon      — long-running sounder (v0.1: stub)
    config init|edit — first-run wizard / edit (contract §14)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path


def _resolve_log_level() -> int:
    """Per contract v0.8 §11 precedence."""
    for env_key in ("CODAR_SOUNDER_LOG_LEVEL", "CLIENT_LOG_LEVEL"):
        val = os.environ.get(env_key, "").upper().strip()
        if val and hasattr(logging, val):
            return getattr(logging, val)
    return logging.INFO


def _install_sighup_handler() -> None:
    """SIGHUP → re-read log level from env (contract §11)."""
    def _on_sighup(signum, frame):
        level = _resolve_log_level()
        logging.getLogger().setLevel(level)
        logging.getLogger(__name__).info(
            "SIGHUP: log level set to %s", logging.getLevelName(level)
        )
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _on_sighup)


def main():
    # Contract §4: inventory/validate/version must emit clean JSON.
    # Quiet logging early so no INFO lines leak before argparse runs.
    _contract_quiet = any(
        arg in ("inventory", "validate", "version")
        for arg in sys.argv[1:3]
    )

    root = logging.getLogger()
    root.setLevel(logging.WARNING if _contract_quiet else _resolve_log_level())

    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(levelname)s:%(name)s:%(message)s")
        )
        root.addHandler(handler)

    parser = argparse.ArgumentParser(
        prog="codar-sounder",
        description="Opportunistic ionospheric sounder using CODAR chirps",
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    def _common(p):
        p.add_argument("--config", type=Path, default=None,
                       help="Path to codar-sounder-config.toml")
        p.add_argument("--log-level", default=None,
                       help="Override log level (DEBUG/INFO/WARNING/ERROR)")

    def _instance(p):
        p.add_argument("--instance", default=None,
                       help="Receiver instance, keyed by reporter (AC0G-B1A): "
                            "reads /etc/codar-sounder/<instance>.toml")

    p_inv = sub.add_parser("inventory", help="Contract v0.7 §3 inventory")
    p_inv.add_argument("--json", action="store_true", default=True)
    _common(p_inv)
    _instance(p_inv)

    p_val = sub.add_parser("validate", help="Contract v0.7 §12 validation")
    p_val.add_argument("--json", action="store_true", default=True)
    _common(p_val)
    _instance(p_val)

    p_ver = sub.add_parser("version", help="Version + git provenance")
    p_ver.add_argument("--json", action="store_true", default=True)
    _common(p_ver)

    p_dae = sub.add_parser("daemon", help="Run sounder daemon")
    p_dae.add_argument("--instance", default=None,
                       help="Reporter-ID instance (loads /etc/codar-sounder/"
                            "<instance>.toml when present; falls back to "
                            "shared config otherwise). See sigmond's "
                            "MULTI-INSTANCE-ARCHITECTURE.md §6.")
    p_dae.add_argument("--radiod-id", default=None,
                       help="status of the [[radiod]] blocks to record "
                            "(every block with that status; default: "
                            "every block)")
    _common(p_dae)

    p_tdma = sub.add_parser(
        "tdma-scan",
        help="Capture IQ from a radiod and discover TDMA TX offsets in the band",
    )
    p_tdma.add_argument("--radiod-id", default=None,
                        help="status of the [[radiod]] blocks to choose from")
    p_tdma.add_argument("--channel-name", default=None,
                        help="channel_name of the [[radiod]] block to scan "
                             "(default: the first block for the radiod)")
    p_tdma.add_argument("--seconds", type=int, default=10,
                        help="Capture duration before discovery (default 10 s)")
    p_tdma.add_argument("--snr-threshold-db", type=float, default=10.0,
                        help="Minimum SNR for a peak to be reported")
    p_tdma.add_argument("--json", action="store_true",
                        help="Emit JSON instead of human-readable output")
    p_tdma.add_argument(
        "--write-config", action="store_true",
        help="Persist discovered tdma_offset_samples back into the config "
             "TOML, in-place per [[radiod.transmitter]] block.  Existing "
             "comments and formatting outside the touched lines are "
             "preserved.  Use this only after eyeballing the discovered "
             "SNRs (typical TDMA peaks are 20–40 dB; 5 dB is noise).",
    )
    _common(p_tdma)
    _instance(p_tdma)

    p_stn = sub.add_parser(
        "stations",
        help="List known CODAR transmitters ranked by distance from the receiver",
    )
    p_stn.add_argument("--receiver-lat", type=float, default=None,
                       help="Receiver latitude (deg); default reads [station] from config")
    p_stn.add_argument("--receiver-lon", type=float, default=None,
                       help="Receiver longitude (deg); default reads [station] from config")
    p_stn.add_argument("--band", default=None,
                       help="Restrict to one band label (e.g. '4–5 MHz (long-range)')")
    p_stn.add_argument("--min-range-km", type=float, default=0.0)
    p_stn.add_argument("--max-range-km", type=float, default=4000.0)
    p_stn.add_argument("--prime-only", action="store_true",
                       help="Only sites in the prime one-hop window (200–2000 km)")
    p_stn.add_argument("--json", action="store_true",
                       help="Emit JSON instead of a human-readable table")
    _common(p_stn)
    _instance(p_stn)

    p_cfg = sub.add_parser("config", help="Configure codar-sounder")
    cfg_sub = p_cfg.add_subparsers(dest="config_command")
    p_init = cfg_sub.add_parser("init", help="write fresh config from template")
    p_init.add_argument("--reconfig", action="store_true",
                        help="overwrite existing config")
    p_init.add_argument("--non-interactive", action="store_true",
                        help="use env-var defaults, do not prompt")
    p_init.add_argument("--instance", default=None,
                        help="receiver instance keyed by reporter (AC0G-B1A); "
                             "writes /etc/codar-sounder/<instance>.toml with "
                             "[instance] reporter_id and only that receiver's "
                             "radiod (default: $SIGMOND_INSTANCE)")
    p_init.add_argument("--reporter-id", default=None,
                        help="the receiver's reporter id (AC0G/B1A); default "
                             "$STATION_REPORTER_ID when its key is the instance")
    p_init.add_argument("--radiod-status", default=None,
                        help="the receiver's radiod status name; default "
                             "$SIGMOND_RADIOD_STATUS")
    p_init.add_argument("--max-centres", type=int, default=None,
                        help="channels (centre frequencies) an unattended "
                             "init records (default $CODAR_MAX_CENTRES, else 2)")
    _common(p_init)
    p_edit = cfg_sub.add_parser("edit", help="review/update existing config")
    p_edit.add_argument("--non-interactive", action="store_true")
    p_edit.add_argument("--radiod-id", default=None)
    _common(p_edit)
    _instance(p_edit)

    # CLIENT-CONTRACT §14 JSON-roundtrip surface.  Sigmond's in-TUI
    # Textual config wizard requires `show --json` + `apply --json -`;
    # without them sigmond falls back to whiptail.
    p_show = cfg_sub.add_parser("show",
                                help="emit current config (TOML→JSON) on stdout")
    p_show.add_argument("--json", action="store_true", default=True)
    p_show.add_argument("--defaults", action="store_true",
                        help="(accepted for forward-compat; currently a no-op)")
    _common(p_show)
    _instance(p_show)

    p_apply = cfg_sub.add_parser("apply",
                                 help="apply a JSON payload (stdin) to the config")
    p_apply.add_argument("--json", action="store_true", default=True)
    p_apply.add_argument("input", nargs="?", default="-",
                         help="JSON payload path or `-` for stdin (default)")
    _common(p_apply)
    _instance(p_apply)

    args = parser.parse_args()

    if not _contract_quiet and getattr(args, "log_level", None):
        level_name = args.log_level.upper()
        if hasattr(logging, level_name):
            root.setLevel(getattr(logging, level_name))

    if args.command == "inventory":
        _handle_inventory(args)
    elif args.command == "validate":
        _handle_validate(args)
    elif args.command == "version":
        _handle_version(args)
    elif args.command == "daemon":
        _handle_daemon(args)
    elif args.command == "tdma-scan":
        _handle_tdma_scan(args)
    elif args.command == "stations":
        _handle_stations(args)
    elif args.command == "config":
        _handle_config(args)
    else:
        parser.print_help()
        sys.exit(1)


def _resolved_config_path(args) -> Path:
    """--config, else $CODAR_SOUNDER_CONFIG, else the --instance file, else
    the legacy shared config.  An --instance names that receiver's file
    only: no fallback to the shared config."""
    if getattr(args, "config", None):
        return args.config
    env = os.environ.get("CODAR_SOUNDER_CONFIG")
    if env:
        return Path(env)
    instance = getattr(args, "instance", None)
    if instance:
        from codar_sounder.config import per_instance_config_path
        return per_instance_config_path(instance)
    from codar_sounder.config import DEFAULT_CONFIG_PATH
    return DEFAULT_CONFIG_PATH


def _contract_config_paths(args) -> list:
    """The configs inventory and validate report on.  One path when the
    caller names one (--config, $CODAR_SOUNDER_CONFIG, --instance) or the
    legacy shared config exists.  On a station migrated to per-receiver
    instances (no shared config), every /etc/codar-sounder/<instance>.toml."""
    path = _resolved_config_path(args)
    named = (getattr(args, "config", None) or getattr(args, "instance", None)
             or os.environ.get("CODAR_SOUNDER_CONFIG"))
    if named or path.exists():
        return [path]
    from codar_sounder.config import per_instance_config_paths
    return per_instance_config_paths() or [path]


def _handle_inventory(args):
    from codar_sounder.config import load_config
    from codar_sounder.contract import CONTRACT_VERSION, build_inventory

    paths = _contract_config_paths(args)
    payloads = []
    for config_path in paths:
        try:
            config = load_config(config_path)
        except FileNotFoundError:
            payloads.append({
                "client": "codar-sounder",
                "version": "0.1.0",
                "contract_version": CONTRACT_VERSION,
                "config_path": str(config_path),
                "instances": [],
                "issues": [{
                    "severity": "fail", "instance": "all",
                    "message": f"config not found: {config_path}",
                }],
            })
            continue
        payloads.append(build_inventory(config, config_path))
    print(json.dumps(_merge_payloads(payloads), indent=2))


def _merge_payloads(payloads: list) -> dict:
    """One payload from one per config.  The first carries the header;
    instances, issues and log paths add up; config_paths lists them all."""
    if len(payloads) == 1:
        return payloads[0]
    out = dict(payloads[0])
    out["config_paths"] = [p["config_path"] for p in payloads]
    for key in ("instances", "issues"):
        out[key] = [item for p in payloads for item in p.get(key, [])]
    if any("log_paths" in p for p in payloads):
        out["log_paths"] = {k: v for p in payloads
                            for k, v in p.get("log_paths", {}).items()}
    if any("ok" in p for p in payloads):
        out["ok"] = all(p.get("ok", True) for p in payloads)
    return out


def _handle_validate(args):
    from codar_sounder.config import load_config
    from codar_sounder.contract import build_validate

    payloads = []
    for config_path in _contract_config_paths(args):
        try:
            config = load_config(config_path)
        except FileNotFoundError:
            payloads.append({
                "ok": False,
                "config_path": str(config_path),
                "issues": [{
                    "severity": "fail", "instance": "all",
                    "message": f"config not found: {config_path}",
                }],
            })
            continue
        payloads.append(build_validate(config, config_path))
    payload = _merge_payloads(payloads)
    print(json.dumps(payload, indent=2))
    if not payload["ok"]:
        sys.exit(1)


def _handle_version(args):
    from codar_sounder import __version__
    from codar_sounder.version import GIT_INFO
    payload = {"client": "codar-sounder", "version": __version__}
    if GIT_INFO:
        payload["git"] = GIT_INFO
    print(json.dumps(payload, indent=2))


def _handle_daemon(args):
    _install_sighup_handler()
    from codar_sounder.config import (
        extract_reporter_id, load_config, resolve_config_path,
    )
    from codar_sounder.core.daemon import MultiBlockDaemon

    # Per-instance config first (sigmond MULTI-INSTANCE-ARCHITECTURE.md §4):
    # /etc/codar-sounder/<instance>.toml when it exists, else the legacy
    # shared config with a DeprecationWarning.
    instance = _unescape(getattr(args, "instance", None))
    radiod_id = _unescape(args.radiod_id)
    config_path = resolve_config_path(
        instance=instance,
        explicit_path=args.config,
    )
    config = load_config(config_path)
    # The instance's reporter id, from its [instance] block.  None for a
    # legacy shared config: its rows keep an empty sigmondb reporter (the
    # server files them under the uploading site) and codar.spots falls
    # back to radiod_id.
    reporter_id = extract_reporter_id(config)
    blocks = _daemon_blocks(config, config_path, radiod_id, instance,
                            reporter_id)

    log = logging.getLogger("codar_sounder.daemon")
    log.info(
        "starting codar-sounder daemon for radiod %s, %d block(s) "
        "(config=%s, reporter_id=%s)",
        ",".join(sorted({b.get("status", "<unconfigured>") for b in blocks})),
        len(blocks), config_path,
        reporter_id or "<none: legacy shared config>",
    )

    try:
        daemon = MultiBlockDaemon(config, blocks, reporter_id=reporter_id)
    except ValueError as exc:
        log.error("refusing to start: %s", exc)
        sys.exit(2)
    daemon.run()


def _unescape(name):
    """%i keeps systemd's spelling (AC0G\\x3dB1A); read it as the name."""
    if not name:
        return name
    from codar_sounder._reporter_id import unescape_systemd_instance
    return unescape_systemd_instance(name)


def _daemon_blocks(config, config_path, radiod_id, instance, reporter_id):
    """The [[radiod]] blocks this daemon records.

    A per-receiver config (its [instance] block names a reporter) holds only
    that receiver's blocks, so it records all of them; the unit's
    --radiod-id %i then carries the reporter key (AC0G-B1A), not a radiod,
    and selects nothing.  It still narrows the blocks when it does name one
    of the config's radiods.  A legacy shared config keeps strict matching:
    --radiod-id picks the blocks of that radiod (codar-sounder@<radiod>).
    """
    from codar_sounder.config import radiod_blocks, resolve_radiod_blocks
    log = logging.getLogger("codar_sounder.daemon")
    if reporter_id:
        blocks = radiod_blocks(config)
        if not blocks:
            raise ValueError(f"config {config_path} has no [[radiod]] blocks")
        if radiod_id is not None:
            named = [b for b in blocks if b.get("status") == radiod_id]
            if named:
                return named
        return blocks
    try:
        return resolve_radiod_blocks(config, radiod_id)
    except ValueError:
        # A per-instance file without an [instance] block, started by a
        # reporter-keyed unit: the file still names this instance's
        # radiod, so record every block in it.  Warn: the file should
        # carry [instance] reporter_id.  A legacy shared config started
        # without --instance keeps strict matching.
        if radiod_id is None or instance is None:
            raise
        log.warning(
            "--radiod-id=%r did not match any [[radiod]] block in %s "
            "(--instance=%r); recording every block in it.  Give the "
            "file an [instance] reporter_id.",
            radiod_id, config_path, instance,
        )
        return resolve_radiod_blocks(config, None)


def _handle_tdma_scan(args):
    """Capture IQ for one CPI and run TDMA offset discovery on it.

    Output (human-readable):
        TDMA scan on radiod=bee1-rx888 channel=codar-4mhz κ=-25733.9 Hz/s
        capture: 10 s = 640000 samples at 64000 Hz
        discovered 3 peaks (snr_threshold=10 dB):
          peak 1: offset_samples=  256  snr=42.3 dB
          peak 2: offset_samples=21504  snr=38.1 dB
          peak 3: offset_samples=43008  snr=35.7 dB
        per-TX TDMA offsets (after subtracting ground delay):
          LISL (D=380 km, 81 samples): tdma_offset_samples=175
          ASSA (D=560 km, 119 samples): tdma_offset_samples=21385
          CEDR (D=580 km, 124 samples): tdma_offset_samples=42884

    Operator pastes the per-TX values into the [[radiod.transmitter]]
    blocks of /etc/codar-sounder/codar-sounder-config.toml.
    """
    _install_sighup_handler()
    from codar_sounder.config import (
        haversine_km, load_config, resolve_radiod_blocks, transmitters,
    )
    from codar_sounder.core.stream import make_iq_source
    from codar_sounder.core.tdma import (
        discover_tx_offsets, match_peaks_to_txs,
    )

    config_path = _resolved_config_path(args)
    config = load_config(config_path)
    blocks = resolve_radiod_blocks(config, args.radiod_id)
    # One capture covers one channel: scan the block the operator names,
    # else the first.
    channel_name = getattr(args, "channel_name", None)
    if channel_name is None:
        block = blocks[0]
    else:
        named = [b for b in blocks if b.get("channel_name") == channel_name]
        if not named:
            have = ", ".join(str(b.get("channel_name", "?")) for b in blocks)
            sys.stderr.write(
                f"no [[radiod]] block with channel_name="
                f"{channel_name!r}; have: {have}\n"
            )
            sys.exit(2)
        block = named[0]

    txs = list(transmitters(block))
    if not txs:
        sys.stderr.write(
            f"No [[transmitter]] blocks found for radiod "
            f"{block.get('status', '?')}\n"
        )
        sys.exit(2)

    # Open an IQ source against the radiod (or synthetic fallback) for
    # one capture window, then close it.
    sample_rate_hz = float(
        config.get("processing", {}).get("sample_rate_hz", 64000)
    )
    first_tx = txs[0]
    iq_source = make_iq_source(
        radiod_status_dns=str(block.get("status", "")),
        channel_name=str(block.get("channel_name", "codar")),
        sample_rate_hz=sample_rate_hz,
        cpi_seconds=float(args.seconds),
        sweep_rate_hz_per_s=float(first_tx["sweep_rate_hz_per_s"]),
        sweep_repetition_hz=float(first_tx["sweep_repetition_hz"]),
        center_freq_hz=float(first_tx["center_freq_hz"]),
        preset=str(block.get("preset", "iq")),
        fallback_target_group_range_km=500.0,
        force_synthetic=False,
    )

    log = logging.getLogger("codar_sounder.tdma_scan")
    log.info(
        "tdma-scan: radiod=%s channel=%s capture=%d s sample_rate=%d Hz",
        block.get("status", "?"), block.get("channel_name", "?"),
        args.seconds, int(sample_rate_hz),
    )

    try:
        # Both IQ backends yield (samples, cpi_start_utc); the scan wants
        # only the samples.
        rx_samples, _cpi_start_utc = next(iter(iq_source))
    except StopIteration:
        sys.stderr.write("IQ source produced no samples\n")
        sys.exit(3)
    finally:
        if hasattr(iq_source, "stop"):
            iq_source.stop()

    sweep_rate_hz_per_s = float(first_tx["sweep_rate_hz_per_s"])
    sweep_repetition_hz = float(first_tx["sweep_repetition_hz"])
    n_per_sweep = int(round(sample_rate_hz / sweep_repetition_hz))

    peaks = discover_tx_offsets(
        rx_samples,
        sample_rate_hz=sample_rate_hz,
        sweep_rate_hz_per_s=sweep_rate_hz_per_s,
        sweep_repetition_hz=sweep_repetition_hz,
        snr_threshold_db=float(args.snr_threshold_db),
    )

    receiver_lat = float(config.get("station", {}).get("receiver_lat", 0.0))
    receiver_lon = float(config.get("station", {}).get("receiver_lon", 0.0))
    tx_distances = {
        tx["id"]: haversine_km(
            receiver_lat, receiver_lon,
            float(tx["tx_lat_deg"]), float(tx["tx_lon_deg"]),
        )
        for tx in txs
    }
    tdma_offsets = match_peaks_to_txs(
        peaks, tx_distances,
        sample_rate_hz=sample_rate_hz,
        n_per_sweep=n_per_sweep,
    )

    if args.json:
        out = {
            "radiod_id": block.get("status", ""),
            "channel_name": block.get("channel_name", ""),
            "sample_rate_hz": int(sample_rate_hz),
            "sweep_rate_hz_per_s": sweep_rate_hz_per_s,
            "sweep_repetition_hz": sweep_repetition_hz,
            "n_per_sweep": n_per_sweep,
            "capture_seconds": int(args.seconds),
            "peaks": [
                {
                    "offset_samples": p.offset_samples,
                    "snr_db": round(p.snr_db, 2),
                    "correlation_power": p.correlation_power,
                }
                for p in peaks
            ],
            "tx_assignments": [
                {
                    "id": tx_id,
                    "ground_distance_km": round(tx_distances[tx_id], 2),
                    "tdma_offset_samples": tdma_offsets[tx_id],
                }
                for tx_id in tdma_offsets
            ],
        }
        print(json.dumps(out, indent=2))
        return

    print(
        f"TDMA scan on radiod={block.get('status','?')} "
        f"channel={block.get('channel_name','?')} "
        f"κ={sweep_rate_hz_per_s:.1f} Hz/s SRF={sweep_repetition_hz:.1f} Hz"
    )
    print(
        f"capture: {args.seconds} s = {len(rx_samples)} samples at "
        f"{int(sample_rate_hz)} Hz"
    )
    print(
        f"discovered {len(peaks)} peak(s) "
        f"(snr_threshold={args.snr_threshold_db:g} dB):"
    )
    for i, p in enumerate(peaks, start=1):
        print(
            f"  peak {i}: offset_samples={p.offset_samples:>7d}  "
            f"snr={p.snr_db:.1f} dB"
        )
    print("per-TX TDMA offsets (after subtracting ground delay):")
    for tx_id, offset in tdma_offsets.items():
        D = tx_distances[tx_id]
        if offset is None:
            print(f"  {tx_id}: D={D:.0f} km — no peak matched")
        else:
            print(
                f"  {tx_id}: D={D:.0f} km → tdma_offset_samples={offset}"
            )

    if args.write_config:
        # Persist offsets back to the config TOML (operator-supplied
        # opt-in; default is to print only).  Only TXs with non-None
        # discovered offsets are written.
        offsets_to_write = {
            tx_id: off for tx_id, off in tdma_offsets.items()
            if off is not None
        }
        if not offsets_to_write:
            print("\n--write-config: no offsets discovered, leaving config unchanged",
                  file=sys.stderr)
            return
        from codar_sounder.tdma_config_writer import update_tdma_offsets_in_toml
        config_path = _resolved_config_path(args)
        radiod_id = block.get("status", "")
        n_changed, n_inserted = update_tdma_offsets_in_toml(
            config_path, radiod_id, offsets_to_write,
        )
        print(
            f"\n--write-config: updated {config_path} "
            f"({n_changed} replaced, {n_inserted} inserted)",
            file=sys.stderr,
        )


def _handle_stations(args):
    """List known CODAR transmitters ranked by distance from the receiver.

    Receiver coordinates come from --receiver-lat/--receiver-lon when given,
    otherwise from the config's [station] block.  This is the machine-readable
    inventory behind the `config init` picker and sigmond's TUI multi-select.
    """
    from codar_sounder.core.stations import (
        PRIME_MAX_KM, PRIME_MIN_KM, audible_transmitters,
    )

    rx_lat = args.receiver_lat
    rx_lon = args.receiver_lon
    if rx_lat is None or rx_lon is None:
        from codar_sounder.config import load_config
        try:
            config = load_config(_resolved_config_path(args))
            station = config.get("station", {})
            if rx_lat is None:
                rx_lat = station.get("receiver_lat")
            if rx_lon is None:
                rx_lon = station.get("receiver_lon")
        except FileNotFoundError:
            pass
    if rx_lat is None or rx_lon is None:
        sys.stderr.write(
            "stations: receiver location unknown — pass --receiver-lat / "
            "--receiver-lon, or set [station] receiver_lat/receiver_lon in "
            "the config.\n"
        )
        sys.exit(2)

    min_km = PRIME_MIN_KM if args.prime_only else args.min_range_km
    max_km = PRIME_MAX_KM if args.prime_only else args.max_range_km
    cands = audible_transmitters(
        float(rx_lat), float(rx_lon),
        min_range_km=min_km, max_range_km=max_km,
        bands=[args.band] if args.band else None,
    )

    if args.json:
        print(json.dumps({
            "receiver_lat": float(rx_lat),
            "receiver_lon": float(rx_lon),
            "prime_window_km": [PRIME_MIN_KM, PRIME_MAX_KM],
            "count": len(cands),
            "transmitters": [c.to_dict() for c in cands],
        }, indent=2))
        return

    if not cands:
        print("No CODAR transmitters match the given filters.")
        return
    print(f"{len(cands)} CODAR transmitter(s) from "
          f"({float(rx_lat):.3f}, {float(rx_lon):.3f}), nearest first "
          f"(★ = prime one-hop {PRIME_MIN_KM:.0f}–{PRIME_MAX_KM:.0f} km):")
    print(f"  {'id':5} {'freq':>8}  {'dist':>7}  {'brg':>5}  {'band':24} assoc")
    for c in cands:
        star = "★" if c.in_prime_range else " "
        print(f"{star} {c.id:5} {c.freq_mhz:7.3f}M {c.distance_km:6.0f}km "
              f"{c.bearing_deg:5.0f}° {c.band:24} {c.association}")


def _handle_config(args):
    from codar_sounder import configurator
    sub = getattr(args, "config_command", None)
    if sub == "init":
        sys.exit(configurator.cmd_config_init(args))
    if sub == "edit":
        sys.exit(configurator.cmd_config_edit(args))
    if sub == "show":
        sys.exit(configurator.cmd_config_show(args))
    if sub == "apply":
        sys.exit(configurator.cmd_config_apply(args))
    print("usage: codar-sounder config {init|edit|show|apply}")
    sys.exit(2)


if __name__ == "__main__":
    main()
