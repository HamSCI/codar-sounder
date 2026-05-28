"""`codar-sounder config init|edit` — first-run wizard + edit flow (CONTRACT §14).

v0.1: minimal — copy the template into place, populate STATION_* and
SIGMOND_RADIOD_STATUS env-bag defaults if available, and tell the operator
to finish editing manually.  An interactive station picker driven by
``data/codar-stations.toml`` lands in v0.2.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from codar_sounder.config import DEFAULT_CONFIG_PATH

_REPO = Path(__file__).resolve().parent.parent.parent
_TEMPLATE = _REPO / "config" / "codar-sounder-config.toml.template"


def cmd_config_init(args) -> int:
    target = Path(getattr(args, "config", None) or DEFAULT_CONFIG_PATH)
    reconfig = bool(getattr(args, "reconfig", False))

    if target.exists() and not reconfig:
        print(f"codar-sounder: config exists at {target}; use --reconfig to overwrite")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    if not _TEMPLATE.exists():
        print(f"codar-sounder: template missing: {_TEMPLATE}")
        return 1

    shutil.copy(str(_TEMPLATE), str(target))
    print(f"codar-sounder: wrote {target}")
    print("Edit the file to set:")
    print("  [station] callsign / grid_square / receiver_lat / receiver_lon")
    print("  [[radiod]] status / channel_name")
    print("    (`status` is the mDNS multicast name of the radiod —")
    print("     RADIOD-IDENTIFICATION.md §3.1)")
    print("  [[radiod.transmitter]] for each CODAR station you want to monitor")
    print("Then enable a service instance:  sudo systemctl enable codar-sounder@<reporter-id>")

    # RADIOD-IDENTIFICATION.md §4 — surface discoverable radiods so
    # the operator can paste a name straight into [[radiod]] status
    # without having to remember/look up the multicast hostname.
    discovered = _discover_radiods()
    if discovered:
        print("")
        print("Radiods broadcasting on the LAN (paste one into "
              "[[radiod]] status):")
        for svc in discovered:
            print(f"  status       = \"{svc['hostname']}\"   "
                  f"# advertised: {svc['name']!r}")
    else:
        print("")
        print("\033[33m⚠\033[0m  No radiod instances broadcasting on the "
              "local network.  Install + start radiod before the daemon")
        print("   can connect:  sudo smd install ka9q-radio")

    # Surface contract §14.3 env-bag values when present (operator-friendly).
    call = os.environ.get("STATION_CALL")
    grid = os.environ.get("STATION_GRID")
    if call or grid:
        print("")
        print("Sigmond env-bag values you can paste into [station]:")
        if call: print(f"  callsign = \"{call}\"")
        if grid: print(f"  grid_square = \"{grid}\"")
    return 0


def _discover_radiods(timeout: float = 5.0) -> list[dict]:
    """Return discovered radiods or [] on failure.

    Per RADIOD-IDENTIFICATION.md §4 — used to surface the LAN's
    available radiods to the operator during `config init`.  Each
    entry is {"name", "hostname", "address", "port"}; `hostname` is
    the mDNS multicast name (the canonical identifier).
    """
    try:
        from ka9q.discovery import discover_radiod_services
        return discover_radiod_services(timeout=timeout) or []
    except Exception:
        return []


def cmd_config_edit(args) -> int:
    target = Path(getattr(args, "config", None) or DEFAULT_CONFIG_PATH)
    if not target.exists():
        print(f"codar-sounder: no config at {target}; run `codar-sounder config init` first")
        return 1

    if getattr(args, "non_interactive", False):
        print(target.read_text())
        return 0

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    import subprocess
    return subprocess.run([editor, str(target)], check=False).returncode


# ---------------------------------------------------------------------------
# CLIENT-CONTRACT §14 — JSON config-roundtrip surface.
#
# `codar-sounder config show --json [--defaults]`   reads the TOML file
#   on disk and emits it as JSON on stdout.  `--defaults` is accepted
#   but currently a no-op — codar-sounder doesn't carry a canonical
#   DEFAULTS dict; the on-disk file IS the source of truth.
#
# `codar-sounder config apply --json -`   reads a JSON dict from stdin,
#   deep-merges it into the existing TOML file, and atomically rewrites
#   the file.  Section whitelist + structural type checks only.
#
# Pattern lifted from wspr-recorder commit ad8f637 (the simpler of the
# two prior Phase 2 implementations — codar-sounder lacks a DEFAULTS
# dict, same as wspr).  Schema whitelist matches codar-sounder's
# actual sections: [station], [paths], [processing], [[radiod]],
# plus [instance] from the per-reporter migration.
#
# Follow-up worth noting: the `[[radiod.transmitter]]` blocks would
# benefit from a "pick from a known CODAR transmitter suite" UI in
# sigmond's wizard.  That requires codar-sounder to publish a
# canonical KNOWN_TRANSMITTERS table (id / freq / sweep_rate /
# location) as schema data; once that lands, the wizard's picker
# renders transmitters as a multi-select.  Not in this commit —
# Phase 2's goal is the JSON contract surface, not new wizard
# widgets.
# ---------------------------------------------------------------------------

import copy
import json
import sys
import tempfile

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

from .config import DEFAULT_CONFIG_PATH


_APPLY_ALLOWED_SECTIONS = {
    "instance", "station", "paths", "processing", "radiod",
}


def cmd_config_show(args) -> int:
    """Emit the on-disk TOML as JSON on stdout.

    `--defaults` is accepted for forward-compat but doesn't merge in a
    canonical defaults dict — codar-sounder doesn't carry one (the
    live file is the source of truth).  Sigmond's wizard tolerates
    this: keys not in the file simply don't appear in the form, which
    is the expected behavior for the edit-existing flow.
    """
    config_path = Path(getattr(args, "config", None) or DEFAULT_CONFIG_PATH)
    if not config_path.is_file():
        out: dict = {}
    else:
        try:
            with open(config_path, "rb") as f:
                out = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            print(f"config show: cannot read {config_path}: {exc}",
                  file=sys.stderr)
            return 2
    json.dump(out, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    return 0


def cmd_config_apply(args) -> int:
    """Read a JSON dict on stdin, validate, atomically write the TOML.

    Section whitelist + structural type checks (each section must be
    a table, except `radiod` which is a list of tables).  No per-key
    type enforcement — sigmond's wizard owns input typing on its end.
    """
    config_path = Path(getattr(args, "config", None) or DEFAULT_CONFIG_PATH)

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"config apply: stdin is not valid JSON: {exc}",
              file=sys.stderr)
        return 2

    if not isinstance(payload, dict):
        print(f"config apply: top-level JSON must be an object, "
              f"got {type(payload).__name__}", file=sys.stderr)
        return 2

    unknown = set(payload.keys()) - _APPLY_ALLOWED_SECTIONS
    if unknown:
        print(f"config apply: section(s) not writable via apply: "
              f"{sorted(unknown)} "
              f"(allowed: {sorted(_APPLY_ALLOWED_SECTIONS)})",
              file=sys.stderr)
        return 2

    for section, fields in payload.items():
        if section == "radiod":
            if not isinstance(fields, list):
                print(f"config apply: [[radiod]] must be a list, "
                      f"got {type(fields).__name__}", file=sys.stderr)
                return 2
            continue
        if not isinstance(fields, dict):
            print(f"config apply: [{section}] must be a table, "
                  f"got {type(fields).__name__}", file=sys.stderr)
            return 2

    if config_path.is_file():
        with open(config_path, "rb") as f:
            existing = tomllib.load(f)
    else:
        existing = {}
    merged = _deep_merge(existing, payload)

    text = _serialize_toml(merged)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(config_path.suffix + ".part")
    tmp.write_text(text, encoding="utf-8")
    try:
        tmp.chmod(0o644)
    except PermissionError:
        pass
    tmp.replace(config_path)
    print(f"wrote {config_path}")
    return 0


# ---------------------------------------------------------------------------
# Helpers — identical to the wspr-recorder / hfdl-recorder versions.
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _toml_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = repr(v)
        if "." not in s and "e" not in s and "E" not in s:
            s += ".0"
        return s
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    raise TypeError(f"unsupported TOML scalar type: {type(v).__name__}")


def _toml_inline_array(arr: list) -> str:
    parts = []
    for x in arr:
        if isinstance(x, (str, bool, int, float)):
            parts.append(_toml_scalar(x))
        else:
            parts.append(json.dumps(x))
    return "[" + ", ".join(parts) + "]"


def _serialize_toml(d: dict, parent: str = "") -> str:
    """Serialize ``d`` to a deterministic TOML string.

    Handles scalars, nested dicts (`[section.child]`), and arrays-of-
    tables (`[[section]]`).  Arrays of scalars render inline.  Keys
    sorted within each section for determinism.  Comments NOT preserved.
    """
    lines: list[str] = []
    scalars: list[tuple[str, object]] = []
    nested: list[tuple[str, dict]] = []
    array_of_tables: list[tuple[str, list]] = []
    for k in sorted(d.keys()):
        v = d[k]
        if isinstance(v, dict):
            nested.append((k, v))
        elif (isinstance(v, list) and v
              and all(isinstance(item, dict) for item in v)):
            array_of_tables.append((k, v))
        else:
            scalars.append((k, v))
    if scalars:
        if parent:
            lines.append(f"[{parent}]")
        for k, v in scalars:
            if isinstance(v, list):
                lines.append(f"{k} = {_toml_inline_array(v)}")
            else:
                lines.append(f"{k} = {_toml_scalar(v)}")
        lines.append("")
    for k, sub in nested:
        header = f"{parent}.{k}" if parent else k
        lines.append(_serialize_toml(sub, parent=header))
    for k, blocks in array_of_tables:
        header = f"{parent}.{k}" if parent else k
        for block in blocks:
            lines.append(f"[[{header}]]")
            for bk in sorted(block.keys()):
                bv = block[bk]
                if isinstance(bv, dict):
                    lines.append(_serialize_toml({bk: bv}, parent=header))
                elif isinstance(bv, list):
                    lines.append(f"{bk} = {_toml_inline_array(bv)}")
                else:
                    lines.append(f"{bk} = {_toml_scalar(bv)}")
            lines.append("")
    return "\n".join(lines)
