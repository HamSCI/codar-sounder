#!/usr/bin/env python3
"""Rebuild data/codar-stations.toml from HFRNet's live national station list.

    curl -sS -o /tmp/stationList.csv 'https://hfradar.ioos.us/hfrnet/sitediag/stationList.php?output=CSV'
    python3 scripts/rebuild-catalogue.py /tmp/stationList.csv

Why: the catalogue shipped from 2014 until 2026-11-13.  Rutgers' long-range group had moved from
4.513 to 4.463 MHz, its 13 MHz group from 13.45 MHz at one sweep a second to 13.475 MHz at two, so
the unattended picker tuned W2NAF, N8UR and others to frequencies where no radar transmits.

Keeps CODAR SeaSondes (WERA radars use another waveform) whose last radial file falls within
--max-age-days of the list's newest one, so a site HFRNet still lists but no longer hears drops out.
HFRNet's Bandwidth is signed (negative = down-sweep) in kHz, and SweepRate counts sweeps a second.
"""
import argparse
import csv
import sys
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "codar-stations.toml"
# Section headings only; core/stations.py BANDS decides membership for the picker.
BANDS = [(4.0, 5.2, "4-5 MHz (long-range)"), (5.2, 5.5, "5 MHz (long-range)"),
         (11.0, 12.9, "12 MHz (mid-range)"), (13.0, 14.0, "13 MHz (mid-range)"),
         (16.0, 16.5, "16 MHz (mid-range)"), (23.0, 27.0, "24-26 MHz (short-range)"),
         (40.0, 44.0, "40-44 MHz (very-short-range)")]


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv", help="HFRNet stationList.php?output=CSV")
    ap.add_argument("--max-age-days", type=int, default=90)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    rows = list(csv.DictReader(open(a.csv, newline="")))
    lasts = [r["Last Radial File"] for r in rows if r.get("Last Radial File")]
    newest = date.fromisoformat(max(lasts))
    keep, dropped = [], 0
    for r in rows:
        if "CODAR" not in r.get("Manufacturer", ""):
            continue
        try:
            last = date.fromisoformat(r["Last Radial File"])
            f, bw, rep = float(r["Frequency"]), float(r["Bandwidth"]), float(r["SweepRate"])
            lat, lon = float(r["Latitude"]), float(r["Longitude"])
        except (KeyError, ValueError):
            dropped += 1
            continue
        if (newest - last).days > a.max_age_days or rep <= 0 or bw == 0:
            dropped += 1
            continue
        keep.append((f, r["Station"], bw, rep, lat, lon, r.get("Network", "")))
    out = [f"# Known CODAR transmitter sites: `codar-sounder config init` reads this file to offer",
           f"# an operator picker keyed on (receiver_lat, receiver_lon) great-circle distance, and to",
           f"# populate [[radiod.transmitter]] defaults.",
           f"#",
           f"# Source: HFRNet national station list, last radial file {newest.isoformat()}",
           f"# (https://hfradar.ioos.us/hfrnet/sitediag/stationList.php?output=CSV), rebuilt by",
           f"# scripts/rebuild-catalogue.py: CODAR SeaSondes heard within {a.max_age_days} days of that date.",
           f"# Regenerate rather than edit.  WERA stations use a waveform the dechirper does not handle.",
           f"#",
           f"# Per-station fields:",
           f"#   freq_mhz              radiated centre frequency (MHz)",
           f"#   sweep_rate_hz_per_s   chirp slope, signed (negative = down-chirp)",
           f"#   sweep_bw_hz           sweep bandwidth (Hz)",
           f"#   sweep_repetition_hz   sweeps per second",
           f"#   tx_lat_deg, tx_lon_deg  antenna location (WGS84)",
           f"#   association           operating network (informational)", ""]
    other = [k for k in keep if not any(lo <= k[0] <= hi for lo, hi, _ in BANDS)]
    for lo, hi, label in BANDS + [(None, None, "other bands")]:
        band = sorted(other if lo is None else [k for k in keep if lo <= k[0] <= hi])
        if not band:
            continue
        out += ["# " + "-" * 75, f"# {label}", "# " + "-" * 75, ""]
        for f, sid, bw, rep, lat, lon, net in band:
            out += [f"[stations.{sid}]", f"freq_mhz            = {f:.5f}",
                    f"sweep_rate_hz_per_s = {bw * 1000 * rep:.3f}",
                    f"sweep_bw_hz         = {abs(bw) * 1000:.0f}",
                    f"sweep_repetition_hz = {rep:g}",
                    f"tx_lat_deg          = {lat:.6f}", f"tx_lon_deg          = {lon:.6f}",
                    f"association         = {_q(net)}", ""]
    Path(a.out).write_text("\n".join(out))
    print(f"wrote {a.out}: {len(keep)} sites (list newest {newest}), {dropped} dropped as stale or malformed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
