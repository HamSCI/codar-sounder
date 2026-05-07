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
- [ ] Bump pyproject + deploy.toml to 0.3.0; commit; push.

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
