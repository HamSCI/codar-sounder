#!/usr/bin/env python3
"""Correlate codar-sounder JSONL records against NOAA Kp index buckets.

Validates whether scintillation event rate / proxy disturbance metrics
track real ionospheric activity (Kp).  Runs against any JSONL files
in the standard codar-sounder layout and produces a Markdown table
binned by 3-hour Kp interval.

Two analysis epochs (auto-detected per record from field presence):

  * **Pre-v0.5** (records lack ``s4_index``): proxies = F2_extreme
    classification rate, mean virtual_height_km, mean peak_count.
    Tests the disturbance-detection front-end on the May 15-16 G2
    storm (Kp ≥ 6) versus quiet days.

  * **v0.5+** (records carry full scintillation fields):
    direct scintillation_event rate, sigma_phi_severity histogram,
    underfit_ratio distribution.  Tests whether the calibrated
    severity bins flag events when Kp is elevated and stay quiet
    when Kp is low.

Usage:
    python3 scripts/kp_correlation_analysis.py \\
        --start 2026-05-14 --end 2026-05-21 \\
        --jsonl-root /var/lib/codar-sounder/ac0g-bee1-rx888/SEAB

NOAA Kp source: services.swpc.noaa.gov 30-day planetary index JSON.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)


NOAA_KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"


def fetch_kp_series() -> dict[dt.datetime, float]:
    """Fetch NOAA's 30-day planetary K-index and return
    {bucket_start_utc: Kp}.  bucket_start_utc is the UTC datetime
    of the 3-hour bucket's beginning."""
    log.info("fetching NOAA Kp data...")
    with urllib.request.urlopen(NOAA_KP_URL, timeout=30) as r:
        rows = json.load(r)
    series: dict[dt.datetime, float] = {}
    for row in rows:
        ts = dt.datetime.fromisoformat(row["time_tag"]).replace(tzinfo=dt.timezone.utc)
        series[ts] = float(row["Kp"])
    log.info("got %d Kp entries from %s to %s",
             len(series), min(series), max(series))
    return series


def kp_bucket(timestamp: dt.datetime) -> dt.datetime:
    """Return the start of the 3-hour Kp bucket containing ``timestamp``."""
    bucket_hour = (timestamp.hour // 3) * 3
    return timestamp.replace(
        hour=bucket_hour, minute=0, second=0, microsecond=0,
    )


def kp_storm_level(kp: float) -> str:
    """ITU/NOAA G-scale level for a Kp value."""
    if kp >= 9:
        return "G5"
    if kp >= 8:
        return "G4"
    if kp >= 7:
        return "G3"
    if kp >= 6:
        return "G2"
    if kp >= 5:
        return "G1"
    if kp >= 4:
        return "active"
    if kp >= 3:
        return "unsettled"
    return "quiet"


@dataclass
class BucketStats:
    """Aggregated record counts + sums for one 3-hour Kp bucket."""
    kp: float = 0.0
    n_records: int = 0
    n_cpis: int = 0
    # mode_layer histogram (proxy for ionospheric disturbance — all
    # epochs).
    mode_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # Sums for averages (proxy metrics).
    sum_virtual_height_km: float = 0.0
    sum_peak_count: int = 0
    sum_snr_db: float = 0.0
    # Scintillation fields (v0.5+ only; n_scint_records counts the
    # subset where these are present).
    n_scint_records: int = 0
    s4_severity_counts: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )
    sigma_phi_severity_counts: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )
    n_events: int = 0
    sum_sigma_phi_rad: float = 0.0
    sum_underfit_ratio: float = 0.0
    n_dechirp_rejected_total: int = 0


def iter_records(jsonl_root: Path, start: dt.date, end: dt.date) -> Iterable[dict]:
    """Stream-parse every JSONL record between start and end inclusive."""
    current = start
    while current <= end:
        path = (jsonl_root
                / f"{current.year:04d}"
                / f"{current.month:02d}"
                / f"{current.day:02d}.jsonl")
        if path.exists():
            log.info("reading %s", path)
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as exc:
                        log.warning("bad JSON line in %s: %s", path, exc)
        else:
            log.info("skipping (missing) %s", path)
        current += dt.timedelta(days=1)


def aggregate(
    records: Iterable[dict], kp_series: dict[dt.datetime, float],
) -> dict[dt.datetime, BucketStats]:
    """Walk records, bucket them by Kp 3-hour window, accumulate stats."""
    buckets: dict[dt.datetime, BucketStats] = {}
    last_cpi_key: tuple[dt.datetime, str] | None = None
    for rec in records:
        ts = dt.datetime.fromisoformat(rec["timestamp"]).astimezone(dt.timezone.utc)
        bucket = kp_bucket(ts)
        if bucket not in kp_series:
            continue   # Kp data doesn't cover this bucket
        b = buckets.setdefault(
            bucket, BucketStats(kp=kp_series[bucket]),
        )
        b.n_records += 1
        # CPI counting via (timestamp, station_id) tuple — peak_index
        # varies but timestamp is identical across a CPI's peak records.
        cpi_key = (ts, rec.get("station_id", ""))
        if cpi_key != last_cpi_key:
            b.n_cpis += 1
            last_cpi_key = cpi_key
        b.mode_counts[rec.get("mode_layer", "unknown")] += 1
        b.sum_virtual_height_km += float(rec.get("virtual_height_km", 0.0))
        b.sum_peak_count += int(rec.get("peak_count", 0))
        b.sum_snr_db += float(rec.get("snr_db", 0.0))
        # Scintillation fields (v0.5+ only).
        if "s4_index" in rec:
            b.n_scint_records += 1
            b.s4_severity_counts[rec.get("s4_severity", "unknown")] += 1
            b.sigma_phi_severity_counts[
                rec.get("sigma_phi_severity", "unknown")
            ] += 1
            if rec.get("scintillation_event", False):
                b.n_events += 1
            b.sum_sigma_phi_rad += float(rec.get("sigma_phi_rad", 0.0))
            b.sum_underfit_ratio += float(rec.get("sigma_phi_underfit_ratio", 1.0))
            b.n_dechirp_rejected_total += int(
                rec.get("dechirp_sweeps_rejected", 0)
            )
    return buckets


def _pct(n: int, d: int) -> float:
    return 100.0 * n / d if d > 0 else 0.0


def render_markdown(
    buckets: dict[dt.datetime, BucketStats], jsonl_root: Path,
) -> str:
    lines: list[str] = []
    lines.append(
        "# codar-sounder × Kp correlation analysis\n"
    )
    lines.append(
        f"Analysis window: {min(buckets):%Y-%m-%d %H:%M} to "
        f"{max(buckets):%Y-%m-%d %H:%M} UTC; source: {jsonl_root}\n"
    )

    # ── Section 1: proxy metrics across all buckets (mode_layer rates,
    # virtual_height, etc.) — works for all epochs.
    lines.append("## Proxy metrics by 3-hour Kp bucket\n")
    lines.append(
        "| UTC bucket | Kp | level | CPIs | recs | F2_extr% | mean h' (km) | mean SNR | mean peaks |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|---|---|"
    )
    for ts in sorted(buckets):
        b = buckets[ts]
        f2e_pct = _pct(b.mode_counts.get("F2_extreme", 0), b.n_records)
        mean_h = b.sum_virtual_height_km / b.n_records if b.n_records else 0.0
        mean_snr = b.sum_snr_db / b.n_records if b.n_records else 0.0
        mean_peaks = b.sum_peak_count / b.n_records if b.n_records else 0.0
        lines.append(
            f"| {ts:%Y-%m-%d %H:00} | {b.kp:.2f} | {kp_storm_level(b.kp):>9} "
            f"| {b.n_cpis} | {b.n_records} "
            f"| {f2e_pct:5.1f} | {mean_h:6.1f} | {mean_snr:5.1f} "
            f"| {mean_peaks:.2f} |"
        )

    # ── Section 2: scintillation metrics for buckets that have them
    # (v0.5+ records only).
    scint_buckets = {ts: b for ts, b in buckets.items() if b.n_scint_records > 0}
    if scint_buckets:
        lines.append("\n## Scintillation metrics by 3-hour Kp bucket (v0.5+ data only)\n")
        lines.append(
            "| UTC bucket | Kp | level | scint recs | event% | σ_φ_str% "
            "| mean σ_φ | mean ratio | dechirp_rej/CPI |"
        )
        lines.append(
            "|---|---|---|---|---|---|---|---|---|"
        )
        for ts in sorted(scint_buckets):
            b = scint_buckets[ts]
            ev_pct = _pct(b.n_events, b.n_scint_records)
            sphi_str_pct = _pct(
                b.sigma_phi_severity_counts.get("strong", 0), b.n_scint_records,
            )
            mean_sphi = b.sum_sigma_phi_rad / b.n_scint_records
            mean_ratio = b.sum_underfit_ratio / b.n_scint_records
            # dechirp_sweeps_rejected is the SAME value across every peak
            # of a CPI, so summing across peak records overcounts by the
            # peak factor.  Dividing total by n_records (= n_cpis ·
            # peaks_per_cpi) gives the per-CPI mean directly.
            dechirp_per_cpi = (
                b.n_dechirp_rejected_total / b.n_records
                if b.n_records else 0.0
            )
            lines.append(
                f"| {ts:%Y-%m-%d %H:00} | {b.kp:.2f} | {kp_storm_level(b.kp):>9} "
                f"| {b.n_scint_records} "
                f"| {ev_pct:5.1f} | {sphi_str_pct:5.1f} "
                f"| {mean_sphi:.3f} | {mean_ratio:.3f} | {dechirp_per_cpi:.2f} |"
            )

    # ── Section 3: Kp-grouped summary
    lines.append("\n## Aggregated by Kp severity\n")
    groups: dict[str, list[BucketStats]] = defaultdict(list)
    for ts, b in buckets.items():
        groups[kp_storm_level(b.kp)].append(b)
    lines.append(
        "| Kp level | n buckets | total CPIs | F2_extr% | mean h' (km) | "
        "scint recs | event% | strong σ_φ% |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|---|"
    )
    # Sort by Kp severity ladder for readable ordering.
    severity_order = ["quiet", "unsettled", "active", "G1", "G2", "G3", "G4", "G5"]
    for level in severity_order:
        if level not in groups:
            continue
        bs = groups[level]
        total_recs = sum(b.n_records for b in bs)
        total_cpis = sum(b.n_cpis for b in bs)
        total_f2e = sum(b.mode_counts.get("F2_extreme", 0) for b in bs)
        sum_h = sum(b.sum_virtual_height_km for b in bs)
        total_scint = sum(b.n_scint_records for b in bs)
        total_events = sum(b.n_events for b in bs)
        total_str = sum(
            b.sigma_phi_severity_counts.get("strong", 0) for b in bs
        )
        f2e_pct = _pct(total_f2e, total_recs)
        mean_h = sum_h / total_recs if total_recs else 0.0
        ev_pct = _pct(total_events, total_scint) if total_scint else 0.0
        sphi_str_pct = _pct(total_str, total_scint) if total_scint else 0.0
        lines.append(
            f"| {level:>9} | {len(bs)} | {total_cpis} | {f2e_pct:5.1f} "
            f"| {mean_h:6.1f} | {total_scint} | {ev_pct:5.1f} "
            f"| {sphi_str_pct:5.1f} |"
        )

    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--start", required=True,
        type=lambda s: dt.date.fromisoformat(s),
        help="Start date (YYYY-MM-DD, UTC).",
    )
    p.add_argument(
        "--end", required=True,
        type=lambda s: dt.date.fromisoformat(s),
        help="End date (YYYY-MM-DD, UTC) inclusive.",
    )
    p.add_argument(
        "--jsonl-root", type=Path,
        default=Path("/var/lib/codar-sounder/ac0g-bee1-rx888/SEAB"),
        help="Path to per-day JSONL tree (path/YYYY/MM/DD.jsonl).",
    )
    p.add_argument(
        "--output", type=Path, default=None,
        help="Optional Markdown output file; defaults to stdout.",
    )
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    kp_series = fetch_kp_series()
    buckets = aggregate(
        iter_records(args.jsonl_root, args.start, args.end), kp_series,
    )
    if not buckets:
        log.error("no records matched the Kp series window")
        sys.exit(1)
    log.info("aggregated %d buckets", len(buckets))
    report = render_markdown(buckets, args.jsonl_root)
    if args.output:
        args.output.write_text(report)
        log.info("wrote %s", args.output)
    else:
        print(report)


if __name__ == "__main__":
    main()
