# codar-sounder

Opportunistic ionospheric sounder for the HamSCI (Ham Radio
Science Citizen Investigation) sigmond suite.

`codar-sounder` receives CODAR (Coastal Ocean Dynamics
Applications Radar) high-frequency (HF) chirp transmissions via
`radiod`, dechirps them, and produces a JSON Lines (JSONL) time
series of group range, virtual height, equivalent vertical
frequency, and per-peak amplitude / phase scintillation indices
along each oblique propagation path.

## Background

CODAR transmitters along the US east and west coasts radiate
**linear frequency-modulated continuous-wave (FMCW)** chirps at
well-characterised frequencies (4–50 MHz) 24/7, with timing
disciplined by the Global Positioning System (GPS).  Each chirp
sweeps the transmit frequency linearly across a band of roughly
25–100 kHz and repeats at the sweep repetition frequency (SRF;
typically 1 Hz).  Some sites use up-chirps (instantaneous
frequency rising through the sweep), others down-chirps; the
sweep parameters — start frequency, bandwidth, sweep rate,
direction — are stable and well-documented per site.  Co-located
transmitters that share a band coordinate via time-division
multiple access (TDMA) slots, each transmitter starting its
chirp at a fixed phase within a shared repetition period.

Although the transmitters exist to image ocean surface currents,
the same signals are an excellent **opportunistic** source for
single-frequency oblique ionospheric sounding: the transmit power
is already paid for, the frequencies and chirp parameters are
public, and the coverage geometry is fixed and well-documented.
The FMCW waveform is well suited to dechirp-based ranging — beat
frequency after dechirping is proportional to the group-path
delay — so a receiver with the same chirp parameters and
coherent timing can recover ionospheric path information from a
transmitter it has no operational relationship with.

The instrument design follows Kaeppler et al. (2022, *Atmos.
Meas. Tech.* 15:4531–4545).  We add multi-hop hypothesis
selection, two stages of median-absolute-deviation (MAD)
interference rejection, and per-peak amplitude / phase
scintillation indices on top of the original method; see
[`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) for the technical
detail.

## What it produces

Daily-rotated JSONL under
`/var/lib/codar-sounder/<radiod_id>/<station>/YYYY/MM/DD.jsonl` —
one record per detected peak per coherent processing interval
(CPI; default 60 s).  Each record carries:

- inverted ionospheric geometry (group range, virtual height,
  equivalent vertical frequency, layer label, multi-hop count),
  with Kaeppler Eq. 13 / 14 uncertainty propagation,
- per-peak amplitude (S4) and phase (σ_φ) scintillation indices
  with HF-recalibrated severity bins,
- diagnostic fields covering interference rejection and detrend
  underfit (an indicator of travelling ionospheric disturbances —
  TIDs).

The JSONL is Kaeppler-compatible and Zenodo-ready.  On hosts
where sigmond's local sink (an SQLite store-and-forward database)
is enabled, every record is also written to the `codar.spots`
table — additive, with no contract change.

Under sigmond, the daemon also writes sigmondb arrival and capture
rows, one capture row per CPI and up to eight arrivals per sweep
group.  hs-uploader ships them in their own tars, through the
wsprdaemon.org gateways, to sigmondb (pipelines
`codar-sigmondb-arrival` and `codar-sigmondb-capture` in
`deploy.toml`).  These rows need hamsci-dsp 0.9.0 or later; an
older hamsci-dsp turns them off and keeps the JSONL.  The
per-sweep phase and amplitude series stay on the station, under
`<output_dir>/<radiod>/phase/`, for `processing.phase_series_days`
days (default 7).

## How it works

```
radiod  →  dechirp  →  trace  →  invert  →  scintillation  →  JSONL + sink
```

- **Dechirp** — windowed quadratic-phase replica plus
  range-Doppler fast Fourier transform (FFT; Kaeppler §2.1);
  per-sweep MAD pre-filter removes impulsive radio-frequency
  interference (RFI) before the slow-time FFT.
- **Trace** — rolling-median ground-clutter mask, then
  signal-to-noise-ratio- (SNR-) and separation-gated multi-peak
  detection (up to 4 peaks per CPI surface every open propagation
  mode).
- **Invert** — secant-law virtual height, equivalent vertical
  frequency, and a v0.7 multi-hop hypothesis selector that
  reclassifies 1-hop-apparent F2_extreme returns to plausible
  2- or 3-hop F2 returns.
- **Scintillation** — S4 and σ_φ computed at each peak's range
  bin, with severity thresholds calibrated against the planetary
  geomagnetic activity index (Kp) on real HF oblique data rather
  than the canonical Global Navigation Satellite System (GNSS)
  values recommended by the International Telecommunication
  Union Radiocommunication Sector (ITU-R).

The full algorithmic detail — formulas, defaults, thresholds,
and the rationale for every recalibration — lives in
[`docs/METHODOLOGY.md`](docs/METHODOLOGY.md).

## Status

v0.7.x — multi-hop inversion + HF-calibrated scintillation on top
of v0.4's feature-complete single-antenna release.  Implements
the sigmond client contract v0.8 (`inventory --json`, `validate
--json`, `version --json`, `stations`, `config init|edit`, `tdma-scan`).

The methodology document records the release-by-release evolution
of the science pipeline (`docs/METHODOLOGY.md` §12).

**Deferred:** cross-loop / crossed-dipole angle-of-arrival (AOA)
processing — needs a second physical antenna to extract Stokes
parameters.  Revisit when the second antenna is installed.

## Choosing which transmitters to record

The site database lists 139 CODAR sites; a given receiver hears only
some of them, and which ones depends on your location and the ionosphere.  You
don't hand-write transmitter blocks — use the picker:

```
smd config init codar-sounder        # whiptail multi-select picker
```

It lists the known sites ranked by distance from your receiver
(pre-checking those in the prime 200–2000 km one-hop window), groups
your choices by centre frequency, and writes a validated config.  To
preview the ranked inventory without configuring:

```
codar-sounder stations --receiver-lat <lat> --receiver-lon <lon>
codar-sounder stations --receiver-lat <lat> --receiver-lon <lon> --json   # for tooling
```

The site database (`data/codar-stations.toml`) is canonical: the picker
generates the MHz→Hz frequency and sweep parameters from it, so a
hand-typed transcription error can't slip into the config.
`scripts/rebuild-catalogue.py` regenerates it from HFRNet's live
station list (CODAR SeaSondes heard within 90 days); the current file
dates from the 2026-11-08 list.  Regenerate it rather than edit it.

### Unattended init under sigmond

`smd bringup` runs `config init --non-interactive`.  Under sigmond,
that init picks the transmitters nearest the station, grouped by
centre frequency, and stops at 2 channels.  Set `CODAR_MAX_CENTRES`
to record more.  Without a latitude and longitude it ranks from the
centre of the station's grid square.  It writes one block per radiod
that sigmond binds, and sets `processing.sample_rate_hz` (64, 96 or
128 kHz) to hold the widest chosen sweep.

The unattended init writes only a new file, the installer's untouched
template, or a file you name with `--reconfig`.  A config that names a
real radiod stays as it stands.

### How the config maps onto radiod channels

Each `[[radiod]]` block opens one radiod IQ channel (64 kHz wide by
default) tuned to its transmitters' shared centre frequency.  Sites in
one band can sit far apart: 4.550 MHz and 4.785 MHz lie 235 kHz apart,
so one channel cannot hear both.  The picker therefore writes one block
per centre, named `codar-<kHz>khz` (e.g. `codar-4785khz`), and lists
every site on that centre under it.  Centres within 2 kHz of each
other share one block.

All the blocks for one radiod carry the same `status`, and one daemon
instance (`codar-sounder@<radiod-id>`) records every one of them, each
in its own thread.  A block that fails restarts on its own after 30 s
without disturbing the others.  The daemon pets the systemd watchdog
while at least one block delivers data.  When every block falls
silent, the pets stop and systemd restarts the unit.  Any block that
stops or goes stale logs an ERROR once, when its state changes, and the
unit's status line shows how many channels are healthy.

`config apply`, `validate` and the daemon refuse a block whose
transmitters' centres differ by more than ±2 kHz, and two blocks of one
radiod that share a `channel_name`.  `codar-sounder tdma-scan` scans one
block at a time; pick it with `--channel-name` (default: the first).

## Install

Pattern A (sigmond-managed).  codar-sounder belongs to the dasi2
profile's core clients, so `smd bringup dasi2` installs and configures
it.  To add it by hand:

```
smd component install codar-sounder
smd apply        # writes the [[radiod.fragment]] channel into radiod
smd config init codar-sounder    # pick transmitters (see above)
smd start codar-sounder
```

`smd start` starts one `codar-sounder@<radiod>` instance for each
radiod the config's `[[radiod]]` blocks name.  Never run `smd` under
sudo; it raises itself to root when a verb needs it.

Standalone (without sigmond):

```
sudo ./scripts/install.sh
sudo systemctl start codar-sounder@<radiod-id>
```

The sigmond client contract is documented at
[`sigmond/docs/CLIENT-CONTRACT.md`](https://github.com/HamSCI/sigmond/blob/main/sigmond/docs/CLIENT-CONTRACT.md)
in the orchestrator repo.

## Further reading

- [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) — full
  signal-processing methodology, formulas, thresholds, and
  release-by-release evolution.
- Kaeppler, S. R. et al. (2022).  "Demonstration of opportunistic
  ionospheric sounding using CODAR transmissions in the United
  States."  *Atmos. Meas. Tech.* 15, 4531–4545.
  [doi:10.5194/amt-15-4531-2022](https://doi.org/10.5194/amt-15-4531-2022).

## License

MIT.
