# v0.3 — TDMA-aware dechirping

## Problem

Multiple CODAR transmitters share each band via TDMA-style sweep-start time
offsets:

- 4.537 MHz: DUCK, HATY (UNC group)
- 4.575 MHz: LISL, ASSA, CEDR (ODU group)
- 4.513 MHz: BLCK, BRIG, HOOK, LOVE, MRCH, MVCO, NANT, WILD (Rutgers group)
- 4.785 MHz: BMLR, SHEL, MAN1, PSG1, WIN1, YHL1 (West Coast)
- ... etc

All TXs in a band use the same sweep rate κ and SRF (1 Hz typical), so a
single replica dechirp cannot distinguish them.  Today v0.2 dechirps once
per CPI and reports the same group ranges for every TX in the config —
actively wrong output when a band has multiple TXs.

## Approach

Per-TX phase-offset replica.  Each TX has a unique sweep-start time offset
within the 1 s sweep period.  Build a replica that wraps the chirp at that
TX's offset; dechirp the same IQ once per TX; attribute peaks to the TX
whose replica produced them.

Offsets are GPS-disciplined and stable, so we discover-once-cache-forever
(re-lock daily or on signal loss).  Discovery: cross-correlate the IQ with
a single zero-offset replica — peaks in the cross-correlation give each
TX's sweep-start time within the period.

## Tasks

- [x] Write plan
- [x] `core/dechirp.py` — extend `make_replica()` with `phase_offset_samples`
      and `dechirp()` to pass it through.  Replica wraps modulo sweep period.
- [x] `core/tdma.py` (new) — `discover_tx_offsets(rx_samples, ...)` returns
      list of (offset_samples, snr_db) for each TX in the band.  Match
      offsets to known TXs by ascending ground-distance order.
- [x] `core/daemon.py` — read optional `tdma_offset_samples` per
      `[[radiod.transmitter]]` (default 0 = v0.2 behaviour).  Pass through
      to `dechirp(phase_offset_samples=...)`.
- [x] `cli.py` — new `codar-sounder tdma-scan` subcommand: capture IQ for
      N seconds, run discovery, print per-TX assignments.  Operator
      pastes the values into config.
- [x] `tests/test_dechirp.py` — extend `_synth_chirp` with
      `target_tdma_offsets_s`.  Add tests that two TXs at different offsets
      produce peaks in the *correct* replica's output and ≥10 dB
      cross-suppression in the *wrong* replica's output.
- [x] `tests/test_tdma.py` (new) — synthetic two-TX TDMA IQ →
      `discover_tx_offsets()` returns both offsets within ±2 samples.
- [x] Run full suite — 87 passed, 24 skipped (Kaeppler dataset; pre-existing).
- [x] Smoke-test `tdma-scan` on bee1-rx888 with the 4.575 MHz config —
      machinery runs end-to-end.  **Live discovery returned only 5 dB-SNR
      peaks at this hour, well below useful threshold.**  Either the ODU
      group isn't TDMA-distinguishable via single-period cross-correlation,
      or the band is too quiet right now.  See "Field results" below.
- [x] Bump pyproject + deploy.toml to 0.3.0; commit; push.

## Field results — 2026-04-29 evening

`codar-sounder tdma-scan --radiod-id ac0g-bee1-rx888 --seconds 10` ran
to completion against the 4.575 MHz channel.  Top peak: 5 dB above
median (correlation_power ~3e-4).  Real TDMA peaks would be 20–40 dB.
Two interpretations, neither yet refuted:

1. **The ODU LISL/ASSA/CEDR group transmits simultaneously** (FDMA at
   sub-kHz spacing within the 25 kHz BW, or co-incident sweep starts).
   Phase-offset dechirping cannot separate them in either case.  A
   cross-loop antenna (deferred to v0.4) gives AOA discrimination,
   which would separate them by bearing.

2. **The single-period cross-correlation is fragile to chirp wrap
   boundaries.**  A multi-period linear correlation (using rx of
   length 2T against a replica of length T) might lock more reliably.
   This is a v0.4 refinement.

Either way, **v0.3 doesn't auto-promote discovered offsets to the
daemon's runtime path** — the operator runs `tdma-scan`, reads the
output, and decides whether to add `tdma_offset_samples` to each
`[[radiod.transmitter]]` block.  The daemon honours those values when
present (falling back to 0 = v0.2 behaviour when absent).

## Out of scope (explicitly deferred)

- Cross-loop O/X polarization (Stokes V) — needs second physical antenna.
  Owner has agreed to procure one; revisit as v0.4.
- Re-lock cadence beyond "once per 24 h or on signal loss".  No predictive
  drift model — TXs are GPS-locked, drift is negligible.
- HFRNet TDMA-table import.  Self-discovery is sufficient and avoids an
  external-data dependency.


## v0.4.0 — multi-peak + layer classification + CH sink (2026-05-07)

Closes the v0.3 "deferred to v0.4" list except the cross-loop AOA item,
which stays out until a second antenna is procured.

### Tasks

- [x] `core/stream.py` — wire ka9q-python ≥3.11 `low_edge`/`high_edge`
      kwargs into `RadiodIQSource.ensure_channel`.  Default ±sample_rate/2
      ∓ 1500 Hz guard.  Optional override via constructor kwargs (kept
      out of config until field SNRs prove a per-station tightness is
      worth the knob).  Bumped ka9q-python pin to >=3.11.0.
- [x] `core/trace.py` — `find_f_region_peaks` (plural).  Local-max
      scan with SNR threshold + minimum-separation collapse; sorted
      by SNR descending; capped at `max_peaks` (default 4).  The old
      singular `find_f_region_peak` becomes a thin wrapper for
      backwards compat.
- [x] `core/invert.py` — `classify_layer(virtual_height_km)` returns
      one of `E`/`F1`/`F2`/`F2_extreme`/`below_E`/`unknown` per Davies
      (1990) digisonde altitudes.  `IonosphericFix` gains a
      `mode_layer` field set automatically by `invert()`.
- [x] `core/output.py` JSONL — adds `peak_index`, `peak_count`,
      `mode_layer` fields per record.
- [x] `core/daemon.py` `process_cpi` — emits one record per peak (was:
      single argmax peak); shared per-radiod CH writer initialised
      from `sigmond.hamsci_ch.Writer.from_env`; per-peak CH inserts
      run alongside the JSONL writes.  CH path failure is non-fatal.
- [x] `clickhouse/schema/codar/{000,001}_*.sql` — greenfield `codar`
      database; `codar.spots` is ReplacingMergeTree, monthly-partitioned,
      ORDER BY `(host_call, station_id, time, peak_index)`.
- [x] `deploy.toml` — bumped contract_version to `0.6`, version to
      `0.4.0`, added `[clickhouse]` block referencing
      `clickhouse/schema/codar`.
- [x] `contract.py` — bumped CONTRACT_VERSION to `0.6`; replaced
      `disk_writes` with `data_sinks` (file always; clickhouse appears
      when `SIGMOND_CLICKHOUSE_URL` is set).
- [x] `cli.py` `tdma-scan --write-config` — atomic in-place TOML edit
      that persists discovered offsets, replacing existing
      `tdma_offset_samples` lines or inserting new ones after the
      matching `id = "..."`.  Comments and unrelated formatting
      preserved.
- [x] Tests:
      - `test_stream.py` — 6 tests on filter-edge defaulting.
      - `test_multi_peak.py` — 25 tests covering `classify_layer`,
        `find_f_region_peaks`, per-peak CH row builder, end-to-end
        synthetic CPI emission to a fake CH writer.
      - `test_tdma_config_writer.py` — 9 tests on the in-place
        TOML rewriter (replace + insert + atomic-write + scope).

### Out of scope (still / again)

- **Cross-loop / crossed-dipole AOA** — needs a second physical antenna.
  Owner has only one; revisit when a second antenna arrives.
- **Dynamic TDMA re-lock** — confirmed unnecessary: CODAR TXs are
  GPS-disciplined, drift is negligible at the timescales we care about.
- **Auto-promoted TDMA offsets at daemon startup** — the field-test
  retro called this risky without operator review.  v0.4 adds
  `--write-config` so the operator can promote in one keystroke after
  eyeballing SNRs (typical real TDMA peaks are 20–40 dB; 5 dB is noise).
- **HFRNet table import** — self-discovery is sufficient; an
  external-data dependency would be a regression.

### Verification status

Unit tests: 158 passed locally (was 117 in v0.3.2; +41 new).  Live
verification on bee1-rx888 with the wideband filter: TBD post-deploy.
Expected: SNRs that were 5 dB on the 4.5 MHz band in v0.3 should rise
to 20+ dB now that the chirp is no longer being truncated by the
default ±5 kHz audio filter.


## v0.5.0 — ITU-R P.531 scintillation indices (2026-05-20)

Closes the identified gap recorded as
`project_codar_sounder_scintillation_gap` in memory: codar-sounder
already produces, but discards, the per-CPI per-mode complex amplitude
time series.  Adding S4 + σ_φ extends the L1 product to a
propagation-mode-resolved scintillation index — a companion to
hf-timestd's WWV-tone-only scintillation, with oblique geometry and
multiple modes per CPI.

### Tasks

- [x] Write plan (`/home/mjh/.claude/plans/functional-floating-garden.md`).
- [x] `core/scintillation.py` (new) — `ScintillationResult` dataclass
      (8 fields) + `compute_scintillation()`.  ITU-R P.531 severity
      bins (strict-less-than); event gate at S4 ≥ 0.3 or σ_φ ≥ 0.2;
      `confidence = min(1, n_samples/30)` with NaN/Inf guard; zero-
      signal short-circuit at `mean_intensity < 1e-30`.
- [x] `core/dechirp.py` — `DechirpResult` gains
      `range_spectrum: np.ndarray` (complex64, M×N, the pre-Doppler-
      FFT matched-filter output).  Cast to complex64 once after the
      fast-time FFT so the per-CPI memory cost is halved vs. numpy's
      default complex128.  Add `positive_to_raw_index_map(result)` and
      `raw_bin_from_positive(result, idx)` for the
      positive-sorted → raw FFT-bin lookup the scintillation slice
      needs.
- [x] `core/output.py` `JsonlWriter.write` — required new
      `scintillation` kwarg.  Adds 8 fields to the JSONL record.
      `s4_index` and `sigma_phi_rad` written full-precision (no
      rounding) so downstream consumers can reproduce the severity
      bin deterministically.  Schema docstring bumped to v0.5.
- [x] `core/daemon.py` `process_cpi` — compute the positive→raw
      index map once per CPI; per peak slice
      `range_spectrum[:, raw_indices[detection.bin_index]]` and pass
      the resulting `ScintillationResult` through to `JsonlWriter.write`
      and `_ch_row_for`.  Per-peak log line gains S4 + σ_φ + EVENT
      marker.
- [x] `core/daemon.py` `_ch_row_for` — gains `scintillation` kwarg;
      8 new fields with explicit casts (no rounding, matching the
      existing convention).
- [x] `tests/test_scintillation.py` (new) — 55 tests covering
      pure-CW baseline, S4 closed-form recovery, σ_φ closed-form
      recovery (period-4 phase pattern orthogonal to the linear
      detrend by construction), severity-helper unit tests at exact
      float64 boundaries, severity end-to-end tests offset 1e-4 from
      the boundary, event-gate triggers, quality gating (min_samples,
      zero signal, NaN/Inf), sample-rate scaling, dataclass field
      lock.
- [x] `tests/test_dechirp.py` — `range_spectrum` shape + complex64
      dtype assertions; `raw_bin_from_positive` round-trip; slow-time
      column at peak bin is finite complex64 M-vector.
- [x] `tests/test_multi_peak.py` — `expected_cols` schema set updated
      with the 8 new fields; row round-trip values asserted; daemon-
      level integration test asserts the scintillation fields land on
      the synthetic CPI's sink row with sane values
      (`scintillation_event` is False, severity classified, samples
      ≥ 10).
- [x] `README.md` — v0.5.0 highlights section (8 new fields, severity
      bins, cadence caveat, confidence model, no contract bump).
- [x] `pyproject.toml` + `deploy.toml` — version `0.4.0` → `0.5.0`;
      `contract_version` stays `0.6`.
- [x] Live verification on bee1-rx888 — superseded by the
      v0.5.1 + v0.5.2 cycle below; live deployment exposed real
      issues (single-bad-sweep contamination, ITU-R-vs-HF threshold
      mismatch) that drove two patches.  End-to-end data path
      verified; multi-day Kp correlation remains an open analysis
      task (not a coding task).

### Out of scope (deferred)

- **Cross-CPI rolling-window indices** — per-CPI is the v0.5 scope.
  Multi-CPI integration adds peak-bin tracking jitter (the peak
  migrates ±1 bin between CPIs); revisit if field data shows per-CPI
  noise dominates real scintillation signal.
- **0.1 Hz canonical ITU-R high-pass** — would need slow-time
  oversampling beyond the 1 Hz SRF, i.e. a fundamental architecture
  change.  The 1/CPI ≈ 0.017 Hz effective corner is documented in
  README so consumers don't cross-compare to GNSS σ_φ blind.
- **3-bin power-sum smoothing** — would bias S4 toward "weak" by
  integrating off-target noise.  Wrong for per-CPI; the right answer
  is per-peak single-bin within one CPI's matched-filter output.

### Verification status

Unit tests: 193 passed locally (was 158 in v0.4.0; +59 new — 56 in
`test_scintillation.py`, 3 in `test_dechirp.py` extensions, plus
schema-set extension in `test_multi_peak.py`'s
`test_row_columns_match_codar_spots_schema`).  24 pre-existing
Kaeppler Zenodo-dataset skips unchanged.


## v0.5.1 — MAD outlier rejection (2026-05-21)

Live verification on bee1-rx888 SEAB (13.45 MHz, CPI=15s) immediately
revealed v0.5.0 was producing **100% strong-event rate** on every
peak.  Root cause via a live-IQ probe: one sweep per CPI carried
broadband spectral leakage (FFT peak in the negative-range half — an
unusable matched-filter row, likely from an RFI burst or ka9q packet
duplication).  That bad sweep contributed one anomalously-large
intensity sample into every range bin's slow-time vector, inflating
S4 to ≈ √(M-1) ≈ 3.7 at M=15.

### Tasks

- [x] `core/scintillation.py` — add MAD-based outlier rejection in
      ``compute_scintillation`` before computing S4/σ_φ.  Reject
      samples with ``|I - median(I)| > 4·MAD(I)``; fall back to
      ``1.2533·MeanAD`` when MAD = 0 (Iglewicz-Hoaglin 1993).  Add
      ``n_outliers_rejected`` to ``ScintillationResult``; report
      retained count in ``n_samples``.  Re-check the ``min_samples``
      floor against the retained count (returns "unknown" if
      rejection drops below).
- [x] `core/output.py` + `core/daemon.py` — surface
      ``scintillation_outliers_rejected`` in JSONL records,
      hamsci_sink rows, and the per-peak log line (``n=14-1`` style
      marker).
- [x] Tests — 6 new tests covering single-outlier rejection, multiple
      outliers, no-outliers-on-clean, MAD=0 fallback, rejection
      below floor → unknown, field-simulation reproducing the
      production bad-sweep pattern.
- [x] Update `tests/test_multi_peak.py` `expected_cols` set.
- [x] `pyproject.toml` + `deploy.toml` — version 0.5.0 → 0.5.1.

### Verification status

199 tests pass (was 193 in v0.5.0).  Live verification: MAD
rejection fires 0-5 times per peak per CPI (mode 2 — matching the
"two adjacent RFI burst sweeps" the probe found later).  S4 still
mostly strong but moved from "always 3+" to "0.5-1.1 range"
(physically plausible now).


## v0.5.2 — quadratic detrend + HF-recalibrated σ_φ thresholds (2026-05-21)

After v0.5.1 fixed S4, σ_φ was still flagging strong everywhere.
A second probe captured 4 real F-region peaks at 60 dB SNR and
showed:

  - No Doppler aliasing (0 of 64 phase steps > π).
  - Linear detrend underfits for peaks with curved phase
    trajectories — quadratic detrend reduces σ_φ by 25-60% on those.
  - Even with perfect detrending, HF oblique multipath produces an
    intrinsic σ_φ floor of ~0.4-0.6 rad on quiet days — ITU-R
    P.531's 0.2/0.5 thresholds (calibrated for single-mode GNSS/SHF)
    misclassify it as "moderate"/"strong".

### Tasks

- [x] `core/scintillation.py`:
      - Change `polyfit(times, phases, deg=1)` → `deg=2`, with
        ``times`` centered before fitting so the linear coefficient
        is the average-Doppler slope at the CPI centroid.
      - Update ``mode_doppler_hz`` extraction: ``coeffs[1] / (2π)``
        (was ``coeffs[0]`` for deg=1).
      - Move ``SIGMA_PHI_WEAK_MAX``: 0.2 → 0.5; ``SIGMA_PHI_MODERATE_MAX``:
        0.5 → 1.0; ``SIGMA_PHI_EVENT_THRESHOLD``: 0.2 → 0.5.
      - Update module docstring with the HF-deviation rationale and
        the live-data evidence table.
- [x] `tests/test_scintillation.py`:
      - Replace ``_phase_pattern_orthogonal_to_linear`` with the new
        period-4 pattern ``(1/√5)·[-1, +3, -3, +1]`` (orthogonal to
        constant, linear, *and* quadratic over each 4-block).
      - Update boundary test parametrizations for the new thresholds.
      - Loosen the doppler-trend test tolerances (2e-2 rad for σ_φ,
        1e-4 Hz for doppler) to reflect complex64 precision at the
        38-rad phase range.
- [x] `README.md` — v0.5.2 highlights with the HF-recalibrated
      thresholds + rationale.
- [x] `pyproject.toml` + `deploy.toml` — version 0.5.1 → 0.5.2.

### Verification status

199 tests pass.  Live verification post-deploy:

  - **Mixed quiet F2 (h' ~ 500 km)**: σ_φ_quadratic ≈ 0.45-0.95 →
    weak / moderate (3 of 4 probe peaks).
  - **Disturbed F2_extreme (h' > 600 km)**: σ_φ_quadratic ≈ 1.2-1.8
    → strong.
  - Production daemon sees mostly F2_extreme right now (high local
    event rate consistent with real disturbed ionospheric conditions
    rather than calibration error; cross-check Kp/SWPC to confirm).
  - MAD rejection continues to fire 1-3× per peak.
  - All 9 scintillation fields present in JSONL + sink rows.


## v0.6.0 — σ_φ diagnostic fields (2026-05-21)

Follows up the v0.5.2 finding that linear detrend underfits real
F-region peaks with curved slow-time phase trajectories (TIDs,
multipath beating, accelerating Doppler).  Rather than choosing
linear *or* quadratic and hiding the other, expose both as wire
fields with the ratio as a self-contained underfit detector — a
TID/multipath-beating signature independent of the σ_φ severity
classification.

### Tasks

- [x] `core/scintillation.py`:
      - Compute both linear-detrend and quadratic-detrend σ_φ on each
        slow-time vector.
      - Canonical ``sigma_phi_rad`` stays = quadratic (matches v0.5.2
        production behaviour exactly — no break for downstream
        readers).
      - ``ScintillationResult`` gains ``sigma_phi_linear_rad``,
        ``sigma_phi_quadratic_rad``, ``sigma_phi_underfit_ratio``
        (= linear / quadratic; ≥ 1 by construction).
      - Pathological branches (degenerate fit, unknown result) return
        ratio = 1.0 by convention.
- [x] `core/output.py` + `core/daemon.py` — 3 new wire fields on
      JSONL records and ``codar.spots`` rows.
- [x] Tests:
      - 6 new ``TestUnderfitRatio`` tests covering: unity for pure
        CW; unity for constant Doppler; >> 1 for purely-quadratic
        phase; canonical = quadratic; ratio ≥ 1 across random
        inputs; unknown-result fallback.
      - ``test_multi_peak.py`` schema-set extension + integration
        assertions.
- [x] `README.md` v0.6.0 highlights.
- [x] `pyproject.toml` + `deploy.toml` — version 0.5.2 → 0.6.0.
      `contract_version` stays 0.6 (additive payload-schema only).

### Out of scope (still / again)

- **Multi-day Kp / SWPC baseline analysis** — confirm v0.5.2 σ_φ
  thresholds match real ionospheric activity over a week+ of
  observations.  Mostly an analysis task (not a code change).
- **Cross-CPI rolling-window scintillation** — smoother indices,
  adds peak-bin-tracking state.  Reconsider after underfit_ratio
  field-data gives us a sense of typical curvature scales.
- **Per-CPI RFI burst root cause** — currently masked by v0.5.1 MAD;
  upstream cause unknown (RFI?  ka9q packet duplication?  RX888
  saturation?).

### Verification status

205 unit tests pass (was 199 in v0.5.2; +6 new).  24 pre-existing
Kaeppler Zenodo-dataset skips unchanged.  Live verification on
bee1-rx888: TBD post-deploy — expect ``sigma_phi_underfit_ratio``
values to cluster around 1.0-1.5 on quiet F2 paths and >> 2 on
disturbed F2_extreme paths (per the probe data from 2026-05-21
showing linear → quadratic reductions of 25-60%).
