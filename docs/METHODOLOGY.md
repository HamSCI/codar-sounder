# codar-sounder methodology

This document describes the signal-processing and inversion
methodology that turns a CODAR (Coastal Ocean Dynamics
Applications Radar) high-frequency (HF) chirp received by `radiod`
into a per-peak JSON Lines (JSONL) time series of group range,
virtual height, equivalent vertical frequency, and scintillation
indices.

CODAR transmits a **linear frequency-modulated continuous-wave
(FMCW)** signal: the instantaneous transmit frequency sweeps
linearly across a 25–100 kHz band and repeats at the sweep
repetition frequency (SRF; typically 1 Hz), with timing
disciplined by the Global Positioning System (GPS).  See the
[`README.md`](../README.md) Background section for the full
operational context.

The core inversion method follows Kaeppler et al. (2022, *Atmos.
Meas. Tech.* 15:4531–4545); deviations from the paper, and the
additional processing layers (multi-hop interpretation, per-sweep
median-absolute-deviation (MAD) pre-filter, scintillation indices)
are called out where they appear.

`README.md` carries the background and operator-facing overview.
This file is the technical reference.

## Contents

1. [Pipeline overview](#1-pipeline-overview)
2. [Wideband IQ capture (stream.py)](#2-wideband-iq-capture-streampy)
3. [Dechirp and range-Doppler FFT (dechirp.py)](#3-dechirp-and-range-doppler-fft-dechirppy)
4. [Per-sweep MAD pre-filter (dechirp.py)](#4-per-sweep-mad-pre-filter-dechirppy)
5. [Ground-clutter mask and multi-peak trace (trace.py)](#5-ground-clutter-mask-and-multi-peak-trace-tracepy)
6. [Inversion: secant-law height, multi-hop, uncertainty (invert.py)](#6-inversion-secant-law-height-multi-hop-uncertainty-invertpy)
7. [Layer classification (invert.py)](#7-layer-classification-invertpy)
8. [Scintillation: S4 and σ_φ (scintillation.py)](#8-scintillation-s4-and-σ_φ-scintillationpy)
9. [TDMA handling (tdma.py)](#9-tdma-handling-tdmapy)
10. [Output schema (output.py)](#10-output-schema-outputpy)
11. [Calibration and monitoring tools](#11-calibration-and-monitoring-tools)
12. [Methodology evolution by release](#12-methodology-evolution-by-release)

---

## 1. Pipeline overview

The pipeline takes wideband in-phase / quadrature (I/Q) samples
from `radiod` and runs them through dechirp, trace, inversion,
and scintillation stages before writing the result to disk:

```
radiod (ka9q-radio, IQ preset)
  │   wideband I/Q for the full chirp band — bypasses the iq preset's
  │   ±5 kHz audio filter
  ▼
RadiodIQSource (core/stream.py)
  │   complex 32-bit float (CF32) I/Q → CPI (coherent processing
  │   interval) framing
  ▼
Dechirp (core/dechirp.py)                       — Kaeppler §2.1
  │   windowed quadratic-phase replica
  │     → fast-time fast Fourier transform (FFT; range per sweep)
  │     → slow-time FFT (Doppler per range bin)
  │   per-sweep MAD pre-filter zeroes anomalous sweeps before slow-time FFT
  ▼
Trace (core/trace.py)
  │   rolling-median ground-clutter mask
  │   find_f_region_peaks() — signal-to-noise-ratio (SNR) + minimum-
  │     separation peak detection, up to 4 peaks per CPI
  ▼
Invert (core/invert.py)
  │   secant-law virtual height + equivalent vertical frequency
  │   v0.7 multi-hop hypothesis selection
  │   Kaeppler Eq. 13/14 uncertainty propagation
  │   classify_layer() → E / F1 / F2 / F2_extreme / below_E
  ▼
Scintillation (core/scintillation.py)
  │   per peak per CPI: range-bin slow-time vector (pre-Doppler-FFT)
  │   → International Telecommunication Union Radiocommunication
  │     Sector (ITU-R) P.531 S4 (amplitude) + σ_φ (phase) indices
  │   severity bins HF-recalibrated against the planetary geomagnetic
  │     activity index (Kp)
  ▼
Output (core/output.py)
  │   daily-rotated JSONL — one record per detected peak
  │   additive HamSCI (Ham Radio Science Citizen Investigation) sink
  │     (an SQLite store-and-forward database; codar.spots table) when
  │     sigmond's sink.db is writable; silent no-op otherwise
```

A CPI is one coherent block of slow-time samples — `cpi_seconds`
of received I/Q — over which the dechirp/FFT pipeline operates.
Default CPI = 60 s.

## 2. Wideband IQ capture (`stream.py`)

`RadiodIQSource` subscribes to a wideband I/Q channel from
`radiod` via `ka9q-python` (≥3.14.0).  Two filter overrides are
essential:

- `filter_low_edge_hz` / `filter_high_edge_hz` — passed to
  `ensure_channel()` so the I/Q band spans the full chirp
  bandwidth.  Defaults compute a near-Nyquist band of
  `±sample_rate/2 ∓ filter_guard_hz`, where `filter_guard_hz =
  1500` by default.
- Without these overrides, the `iq` preset's default ±5 kHz audio
  filter clips the chirp edges and smears the dechirped range
  bins.

Per-CPI framing: `cpi_n_samples = int(sample_rate_hz × cpi_seconds)`
samples are buffered before emission.  The CPI is timestamped
with the Real-time Transport Protocol- (RTP-) anchored
Coordinated Universal Time (UTC) of its first sample (per the
timing invariant in `docs/METROLOGY.md` of the sigmond suite —
never the host wall clock).

## 3. Dechirp and range-Doppler FFT (`dechirp.py`)

`make_replica()` generates a phase-coherent quadratic-phase
replica of the transmitted linear-FMCW chirp:

```
s_ref(t) = exp(-j · 2π · (½ · κ · t² + f_start · t))
```

where `κ = sweep_rate_hz_per_s` is the chirp rate read from the
transmitter database (sign distinguishes up-chirps from down-
chirps) and `f_start` is the chirp's starting frequency within
the I/Q baseband.

The replica is multiplied against the received I/Q, then a Hann
window (default on; `window=True`) is applied per Kaeppler Eq. 6
to suppress fast-time FFT sidelobes.

Two FFTs follow:

1. **Fast-time FFT** per sweep — beat frequency → group-range
   conversion via the chirp rate:
   `group_range_km = (beat_hz / |κ|) · c / 2 · 1e-3`.
   FFT size = `n_samples = int(sample_rate_hz / sweep_repetition_hz)`
   per sweep.
2. **Slow-time FFT** across sweeps — yields Doppler per range bin.
   FFT size = `n_sweeps = int(cpi_seconds × sweep_repetition_hz)`.

`DechirpResult` carries:

- `range_doppler` — complex M×N range-Doppler map.
- `range_spectrum` — the pre-slow-time-FFT complex range-time
  matrix; scintillation reads each peak's row directly from here.
- `range_axis_km`, `doppler_axis_hz` — calibrated axes.
- `n_sweeps_rejected`, `bad_sweep_mask` — pre-filter output (next
  section).

## 4. Per-sweep MAD pre-filter (`dechirp.py`)

Added in v0.6.1.  Removes sferic-like impulses (broadband
radio-frequency impulses from distant lightning strokes
propagating in the Earth-ionosphere waveguide), discrete-tone
radio-frequency interference (RFI) bursts, and longer-duration
disturbances at the **sweep** level — before they corrupt the
range profile (for peak detection) or the per-peak slow-time
vectors (for scintillation).

Per-sweep total power is computed from the post-fast-time-FFT
spectrum.  Any sweep whose total power deviates by more than
`SWEEP_MAD_REJECTION_K · MAD(power)` is zeroed before the slow-time
FFT runs:

```
median_power = median(per_sweep_total_power)
mad          = median(|per_sweep_total_power - median_power|)
bad_sweep    = |per_sweep_total_power - median_power| > K · mad
```

`SWEEP_MAD_REJECTION_K = 4.0` (mirrors the scintillation per-peak
K).  When MAD = 0 (degenerate, equal-power sweeps) the fallback
uses mean absolute deviation (MeanAD) × 1.2533 so the test stays
well-defined.

`bad_sweep_mask` is propagated downstream to scintillation so the
per-peak MAD step doesn't see zero-valued samples from the
pre-filter.  The CPI-level count of zeroed sweeps is exposed on
each JSONL record as `dechirp_sweeps_rejected`.

This is complementary to the per-peak MAD inside scintillation
(§8) — defense in depth at two stages of the pipeline.

## 5. Ground-clutter mask and multi-peak trace (`trace.py`)

`GroundClutterMask` keeps a rolling median of recent range
profiles (default window = 20 CPIs).  Subtracting this median from
each new profile suppresses time-stable structure — the ground-wave
returns and direct-path leakage near range ≈ 0.

`find_f_region_peaks()` then detects all local maxima in a
user-configurable range window above an SNR threshold.  Rules:

- **SNR threshold** — operator-supplied via config
  (`snr_threshold_db`).  Each peak's SNR is computed in dB against
  the clutter-masked noise floor with a 60 dB ceiling to keep
  log-domain stable when the mask zeros the median.
- **Minimum separation** — `DEFAULT_MIN_PEAK_SEPARATION_KM = 12.0`.
  Closer than this collapses to the higher-SNR peak.  Roughly one
  FMCW range bin at 4.5 MHz sweep span.
- **Maximum peaks** — `DEFAULT_MAX_PEAKS = 4`.  Headroom above the
  typical real propagation set (1F2 high-ray + 1F2 low-ray + E or
  sporadic-E (Es) + 2F2).

Output is a sorted `List[TraceDetection]` with `group_range_km`,
`snr_db`, `power`, `bin_index` per peak.  Each detection becomes
its own JSONL record downstream, tagged with `peak_index` and
`peak_count`.

## 6. Inversion: secant-law height, multi-hop, uncertainty (`invert.py`)

For each detection the inverter computes a virtual reflection
height under the secant-law mirror model and an equivalent
vertical frequency.

### 6.1 Single-hop geometry

Let `P` be the slant group path, `D` the ground-distance between
transmitter and receiver:

```
h' = ½ · √(P² − D²)                   (Eq. 12)
f_v = f_o · √(P² − D²) / P            (Eq. 12b)
```

with `f_o` the chirp's centre frequency.  Both reduce to the
classic vertical-incidence forms when `D → 0`.

### 6.2 Multi-hop selection (v0.7)

When the apparent 1-hop `h'` lands above the F-region (≥ 500 km),
the more plausible interpretation is usually a multi-hop return at
typical F2 heights.  `select_n_hops()` chooses the smallest
`N ∈ {1, 2, 3, 4}` whose per-hop virtual height lies in the
F-region plausibility band:

```
_MULTIHOP_TRIGGER_H_KM = 500.0       # h_1 below this → keep N=1
_PLAUSIBLE_F_LOW_KM    = 150.0
_PLAUSIBLE_F_HIGH_KM   = 500.0
DEFAULT_MAX_HOPS       = 4
```

Selection rule:

1. Compute `h_1 = ½ · √(P² − D²)`.
2. If `h_1 < 500 km`, return `N = 1` (preserves prior behaviour
   for ordinary E/F1/F2 returns).
3. Otherwise iterate `N = 2 … max_hops`; for each `N` compute
   `h_N` from the `N`-hop secant law and accept the smallest `N`
   for which `h_N ∈ [150, 500] km`.
4. Fall back to `N = 1` if none qualify (rare; preserves the
   `F2_extreme` label for genuine anomalies).

`n_hops` appears on every record; `virtual_height_km` and
`mode_layer` reflect the `N`-corrected interpretation.  The
equivalent vertical frequency and takeoff zenith angle are
geometrically `N`-invariant — no semantic change in those fields
across the v0.6 / v0.7 boundary.

Archived records before v0.7 keep the 1-hop convention; consumers
should consult `processing_version` to know which epoch produced
each record.

### 6.3 Uncertainty propagation

Kaeppler Eq. 13 (height) and Eq. 14 (vertical frequency)
propagate the group-range measurement uncertainty `ΔP` (and
optionally the ground-distance uncertainty `ΔD`, default 0) into
the inverted products:

```
Δh' = (P / (4 · N² · h')) · √(P² · ΔP² + D² · ΔD²)
Δf_v = f_o · D² · ΔP / (P² · √(P² − D²))
```

`Δh'` carries the `1/N²` scaling so multi-hop interpretations have
correspondingly tighter height uncertainties.

## 7. Layer classification (`invert.py`)

`classify_layer()` assigns a virtual-height label per Davies
(1990) digisonde conventions:

| Label         | Range (km)      |
|---------------|-----------------|
| `below_E`     | (−∞, 90)        |
| `E`           | [90, 140)       |
| `F1`          | [140, 220)      |
| `F2`          | [220, 500)      |
| `F2_extreme`  | [500, ∞)        |

Sporadic-E (Es) is folded into the `E` label.  Single-frequency
oblique returns cannot reliably distinguish Es from regular E
without a maximum-usable-frequency (MUF) sweep; downstream
consumers must use other indicators (total electron content, TEC;
Doppler signature) to separate the two.

`F2_extreme` should be rare post-v0.7 — most 1-hop-apparent
`F2_extreme` returns are reclassified to `F2` at `N = 2` or
`N = 3` (see §12: the multi-hop diagnostic that drove the v0.7
inversion change).

## 8. Scintillation: S4 and σ_φ (`scintillation.py`)

For each detected peak, `compute_scintillation()` reads the
`M`-sample slow-time complex amplitude vector at that peak's
range bin (pre-Doppler-FFT) and computes two fluctuation indices
defined in ITU-R Recommendation P.531 (the ITU-R reference
document on ionospheric propagation effects relevant to
Earth-space radio systems).

```
M = int(coherent_seconds × sweep_repetition_hz)    # default M = 60
```

The vector is detrended and outlier-filtered before the indices
are computed.

### 8.1 Detrending

A second-order polynomial fit (over a zero-centred time axis) is
removed from the per-bin intensity / phase series.  The linear
coefficient of that fit equals the mode's Doppler slope and is
exposed as `mode_doppler_hz`.

A linear-only detrend variant is also computed and exposed as
`sigma_phi_linear_rad` for diagnostic comparison.  The canonical
`sigma_phi_rad` equals the quadratic value (and drives the
severity classification).

**Cadence caveat.**  At default CPI = 60 s, SRF = 1 Hz, the
linear component of the detrend acts as an effective ≈ 0.017 Hz
(= 1/CPI) high-pass — *not* the canonical ITU-R 0.1 Hz used by
Global Navigation Satellite System (GNSS) receivers.  The
quadratic component additionally rejects travelling-ionospheric-
disturbance- (TID-) scale curvature (5–60 min periods).
Cross-comparisons to GNSS σ_φ literature should account for both
differences.

### 8.2 Outlier rejection

Per-bin intensity samples whose deviation from the median exceeds
`MAD_REJECTION_K · MAD(intensity)` are dropped.  `MAD_REJECTION_K
= 4.0` (mirrors the per-sweep K in §4).  The MeanAD × 1.2533
fallback handles MAD = 0.

`n_outliers_rejected` is exposed on every record.

### 8.3 Indices

After detrending and outlier-rejection on the retained `n` samples:

```
S4²  = var(I) / mean(I)²       # amplitude scintillation index
σ_φ  = std(unwrap(phase))      # phase scintillation index, radians
```

S4 > 1.0 (saturated scintillation) is *not* clipped.

### 8.4 Severity bins (HF-recalibrated)

The severity thresholds are HF-multipath-aware, not the canonical
ITU-R P.531 values that were calibrated for narrowband
single-mode GNSS / super-high-frequency (SHF) signals.  Both
indices were tuned against the planetary geomagnetic activity
index (Kp) on SEAB / 13.45 MHz / 1416 km data in v0.6.2 / v0.6.3:

| Index   | weak       | moderate         | strong   |
|---------|------------|------------------|----------|
| S4      | `< 1.0`    | `< 1.5`          | `≥ 1.5`  |
| σ_φ     | `< 1.5` rad| `< 2.0` rad      | `≥ 2.0` rad |

Strict less-than at each boundary.  Event gate fires when
`S4 ≥ 1.0` or `σ_φ ≥ 1.5`.  See §11 for how the calibration was
derived and how to refresh it after a geomagnetic storm.

### 8.5 Confidence and sample floor

```
n_floor    = 10                      # below this → "unknown"
confidence = min(1.0, n_retained / 30)
```

Below 10 retained samples (after outlier rejection) — or when the
clutter mask zeroes the bin's mean intensity (`mean_intensity <
1e-30`) — the indices are zeroed and severities set to
`"unknown"`.  The confidence model saturates at 30 retained
samples; it deliberately does not penalise high S4, because that
would suppress confidence on real strong scintillation events.

### 8.6 Underfit ratio (TID detector)

```
sigma_phi_underfit_ratio = sigma_phi_linear_rad / sigma_phi_quadratic_rad
```

≈ 1.0 when slow-time phase has no curvature beyond a constant
Doppler (clean single-mode propagation).  ≫ 1 when residual
curvature exists (TIDs, multipath beating, accelerating
ionospheric Doppler).  Surfaces TIDs as an independent signal
from σ_φ severity classification.

## 9. TDMA handling (`tdma.py`)

Co-located CODAR transmitters share frequencies via time-division
multiple access (TDMA) slots.  Without slot-aware processing,
signals from multiple transmitters on the same band beat against
each other and corrupt the dechirped range profile.

`discover_tx_offsets()` performs an FFT-based linear
cross-correlation of multi-period RX samples against a
zero-offset chirp replica.  Defaults:

- `snr_threshold_db = 10.0`
- `min_separation_samples = 32` (≈ 150 km at 64 kHz / 1 Hz SRF)
- `max_peaks = 8`

The correlation is computed as a zero-padded FFT-based linear
correlation across multiple periods then folded coherently.
Peaks above SNR threshold and separated by at least
`min_separation_samples` are returned as candidate transmitter
slot offsets.

`offset_for_tx()` converts a correlation-peak time-of-arrival to
sweep-start phase offset by subtracting the direct-path delay
from the transmitter's known ground distance.  The dechirp
replica is then phase-rotated by this offset so the target
transmitter's chirp aligns coherently while co-band transmitters
are spread in beat-frequency and discarded.

Operators run `codar-sounder tdma-scan` to discover slot
assignments; `codar-sounder tdma-scan --write-config` persists
the discovered offsets in-place in the config TOML (atomic
write, comments preserved).

CODAR transmitters are GPS-disciplined, so dynamic re-lock is
unnecessary — slots are stable over the timescales the daemon
operates.

## 10. Output schema (`output.py`)

JSONL is the canonical L1 artefact, daily-rotated under:

```
/var/lib/codar-sounder/<radiod_id>/<station>/YYYY/MM/DD.jsonl
```

One record per detected peak per CPI.  Schema is
Kaeppler-compatible with additive fields per release:

- **Geometry** — `group_range_km`, `virtual_height_km`,
  `equivalent_vertical_freq_mhz`, `takeoff_zenith_angle_deg`,
  `n_hops`, `mode_layer`, `peak_index`, `peak_count`.
- **Uncertainties** — `group_range_uncertainty_km`,
  `virtual_height_uncertainty_km`,
  `equivalent_vertical_freq_uncertainty_mhz`.
- **Scintillation (v0.5+)** — `s4_index`, `s4_severity`,
  `sigma_phi_rad`, `sigma_phi_severity`, `sigma_phi_linear_rad`,
  `sigma_phi_quadratic_rad`, `sigma_phi_underfit_ratio`,
  `scintillation_event`, `scintillation_confidence`,
  `scintillation_samples`, `mode_doppler_hz`.
- **Diagnostics** — `dechirp_sweeps_rejected`, `snr_db`,
  `processing_version`, `contract_version`.

When sigmond's HamSCI sink (`/var/lib/sigmond/sink.db`) is
writable, every per-peak record is **also** written to
`codar.spots` via `sigmond.hamsci_sink.Writer`.  The sink path is
additive: hosts without sigmond's sink stay file-only with no
extra moving parts and no contract change.

## 11. Calibration and monitoring tools

Two re-runnable analysis utilities live under `scripts/`.  Neither
is imported by the daemon — they exist to validate methodology
choices against new data.

### 11.1 `scripts/kp_correlation_analysis.py`

Pulls the 30-day planetary-Kp JSON feed from the National Oceanic
and Atmospheric Administration's Space Weather Prediction Center
(NOAA SWPC), glob-streams JSONL records over a date range, buckets
them by 3-hour Kp window, and emits a Markdown report covering,
per bucket:

- `mode_layer` distribution + multi-hop % (v0.7+ records)
- mean SNR, virtual height, peak count
- scintillation event rate, σ_φ severity histogram, mean
  `underfit_ratio`, `dechirp_sweeps_rejected` per CPI

Plus an aggregation by Kp severity level (`quiet` → `G5`) and a
"recent buckets (Kp not yet published)" section for the most
recent 3 hours of data NOAA hasn't covered yet.

```
uv run python3 scripts/kp_correlation_analysis.py \
    --start 2026-05-14 --end 2026-05-21 \
    --output tasks/analysis/latest_kp_correlation.md
```

Saved reports in `tasks/analysis/`:

- `2026-05-21_kp_correlation.md` — first run; drove the v0.6.2
  σ_φ recalibration (σ_φ ≈ 1.27 rad at Kp=1.00, well above the
  v0.5.2 thresholds).
- `2026-05-21_kp_correlation_rerun.md` — post-v0.6.3 / v0.7.0
  re-run captured the full calibration cascade in a single
  Kp-pending bucket.

### 11.2 `scripts/multihop_diagnostic.py`

For each `F2_extreme` record in a date range, computes the
apparent virtual height under `N = 1, 2, 3, 4` hop hypotheses and
reports the climatological fit.  This diagnostic closed the
F2_extreme misclassification in v0.7.0: 100 % of pre-fix
`F2_extreme` records had a clean 3-hop interpretation at typical
F2 heights (median `h' = 263` km).

```
uv run python3 scripts/multihop_diagnostic.py \
    --start 2026-05-14 --end 2026-05-21
```

Re-run after any future change to `invert()`'s multi-hop
selection logic, or if the `F2_extreme` rate climbs unexpectedly
post-v0.7 (would suggest the selection bounds need tuning).
Saved report: `tasks/analysis/2026-05-21_f2_extreme_multihop_diagnostic.md`.

### 11.3 Open follow-up: Kp ≥ 5 storm-day calibration

The v0.6.2 / v0.6.3 σ_φ and S4 HF-recalibrated thresholds were
derived from a 12-hour window covering only Kp 1.0–3.0 (quiet →
unsettled).  **Final calibration awaits a Kp ≥ 5 storm with v0.5+
logging.**  When one occurs, re-run
`scripts/kp_correlation_analysis.py` over the storm window — the
Kp-grouped aggregate will surface the storm-day distribution and
let us decide if the σ_φ / S4 thresholds need another nudge.  No
scheduled job; re-run on demand when a storm appears in the NOAA
data.

### 11.4 Cadence at a glance

| Task                                      | When                              | Tool                          |
|-------------------------------------------|-----------------------------------|-------------------------------|
| Verify calibration drift                  | After accumulating new data       | `kp_correlation_analysis.py`  |
| Check `F2_extreme` rate hasn't crept up   | After any `invert.py` change      | `multihop_diagnostic.py`      |
| Storm-day final calibration               | When Kp ≥ 5 appears in NOAA data  | `kp_correlation_analysis.py`  |

## 12. Methodology evolution by release

### v0.4.0 — feature-complete single-antenna release

- Wideband filter wiring (`core/stream.py`) — `low_edge` /
  `high_edge` kwargs against ka9q-python ≥3.11 so the captured IQ
  spans the full chirp bandwidth and the dechirped range bins
  aren't smeared by the iq preset's ±5 kHz audio filter.
- Multi-peak detection — replaced the prior argmax single-peak
  pickup so high-ray / low-ray F2 returns and concurrent E-layer
  paths surface as distinct records.
- Layer classification — virtual-height bins per Davies (1990).
- `tdma-scan --write-config` — persists discovered slot offsets
  in-place in the config TOML.

### v0.5.0 — scintillation per peak per CPI

- S4 and σ_φ computed at each peak's range bin from the
  M-sample slow-time complex vector.
- Eight new wire fields: `s4_index`, `s4_severity`,
  `sigma_phi_rad`, `sigma_phi_severity`, `scintillation_event`,
  `scintillation_confidence`, `scintillation_samples`,
  `mode_doppler_hz`.
- Severity bins at ITU-R P.531 canonical values
  (S4: 0.3 / 0.6; σ_φ: 0.2 / 0.5 rad).

### v0.5.1 — per-peak MAD outlier rejection

- `MAD_REJECTION_K = 4.0` applied to per-bin intensity samples
  inside scintillation before S4 / σ_φ are computed.

### v0.5.2 — first σ_φ HF recalibration

- Severity bins raised to weak < 0.5 / moderate < 1.0 / strong
  ≥ 1.0 rad after the ITU-R 0.2 / 0.5 thresholds fired ~100 %
  of the time on real HF oblique data (intrinsic phase-incoherence
  floor of ~0.4–0.6 rad on quiet days).

### v0.6.0 — σ_φ diagnostic fields

- `sigma_phi_linear_rad` and `sigma_phi_quadratic_rad` both
  exposed; canonical `sigma_phi_rad` = quadratic.
- `sigma_phi_underfit_ratio = linear / quadratic` added as a TID
  detector independent of σ_φ severity classification.

### v0.6.1 — per-sweep MAD pre-filter

- New stage at the **sweep** level inside `dechirp.py`, before
  the slow-time FFT.  Catches sferic impulses, discrete-tone RFI,
  and longer-duration disturbances that the per-peak MAD inside
  scintillation can't see.
- `SWEEP_MAD_REJECTION_K = 4.0`; `dechirp_sweeps_rejected` wire
  field per record.
- Live-verification probe at 13.45 MHz showed ~80 % of CPIs had
  1–2 anomalous sweeps split across three populations: broadband
  impulses, discrete-tone RFI, and longer-duration persistent
  disturbances — all caught by the single MAD test on per-sweep
  total power.

### v0.6.2 — σ_φ thresholds Kp-calibrated

- 60-bucket Kp correlation analysis on bee1-rx888 SEAB data
  (`tasks/analysis/2026-05-21_kp_correlation.md`).  At Kp = 1.00
  (very quiet) production σ_φ averaged 1.27 rad with 77 % of
  peaks flagging "strong" under v0.5.2 — proof that v0.5.2's
  0.5 / 1.0 thresholds sat well below the HF intrinsic
  phase-incoherence floor at SEAB / 13.45 MHz / 1416 km.
- New thresholds: weak < 1.5 / moderate < 2.0 / strong ≥ 2.0 rad.
  Event gate at σ_φ ≥ 1.5.  S4 thresholds left at ITU-R canonical
  pending §11.3 storm-day data.

### v0.6.3 — S4 thresholds Kp-calibrated

- v0.6.2 left S4 at the canonical 0.3 / 0.6.  Live data showed
  event rate stuck at 94 % because S4 was alone driving events:
  at HF oblique with multipath the signal Rayleigh-fades, giving
  S4 ≈ 0.7–1.0 by construction with no real scintillation.
- Quiet-day S4 distribution on May 21 (Kp 1.0–3.0; 11,577
  records), percentiles 10 / 50 / 90 / 95: p10 = 0.56, p50 = 0.78,
  p90 = 1.05, p95 = 1.30.
- New thresholds: weak < 1.0 / moderate < 1.5 / strong ≥ 1.5.
  Event gate at S4 ≥ 1.0.  Mirror of the σ_φ recalibration.
- Both indices now in the same HF-recalibrated frame; absolute
  values remain comparable to other HF multipath sounders but
  **not** directly to GNSS scintillation literature.

### v0.7.0 — multi-hop inversion

- `invert()` now selects the most plausible hop count
  `N ∈ {1, 2, 3, 4}` from the geometry; prior versions always
  assumed `N = 1`.
- Driven by the diagnostic finding that 100 % of
  apparent-`F2_extreme` records on bee1-rx888 SEAB had a clean
  3-hop interpretation at typical F2 heights (median
  `h' = 263 km`), far more likely than persistent
  `F2_extreme` at 35 % rate.
- New wire field `n_hops` per record.  `virtual_height_km` and
  `mode_layer` reflect the `N`-corrected interpretation —
  semantic change from v0.6.x.  Archived records before v0.7
  keep 1-hop conventions; consumers should branch on
  `processing_version`.
- Equivalent vertical frequency and takeoff zenith angle are
  geometrically `N`-invariant — no semantic change.
- Contract version unchanged at 0.6 through this cascade (all
  schema changes additive); subsequently bumped to 0.7 to track
  the sigmond suite-wide v0.7 contract.

## Permanent non-goals

- **Dynamic TDMA re-lock.**  CODAR transmitters are
  GPS-disciplined; slot drift is negligible at the timescales
  the daemon operates.
- **High-Frequency Radar Network (HFRNet) table import.**
  Self-discovery via `tdma-scan` is sufficient and avoids an
  external-data dependency.

## Deferred work

- **Cross-loop / crossed-dipole angle-of-arrival (AOA)
  processing.**  Needs a second physical antenna to extract
  Stokes parameters.  Skipped for the v0.7 series; revisit when
  the second antenna is installed.

## Reference

- Kaeppler, S. R. et al. (2022).  "Demonstration of opportunistic
  ionospheric sounding using CODAR transmissions in the United
  States."  *Atmos. Meas. Tech.* 15, 4531–4545.
  [doi:10.5194/amt-15-4531-2022](https://doi.org/10.5194/amt-15-4531-2022).
  Source for §2.1 (dechirp), Eq. 13 / 14 (uncertainty
  propagation), and the data-product schema.
- ITU-R P.531 — ionospheric propagation effects on
  Earth-space paths; defines S4 and σ_φ.
- Davies, K. (1990).  *Ionospheric Radio.*  Peter Peregrinus.
  Source for the digisonde virtual-height layer conventions used
  in `classify_layer()`.
