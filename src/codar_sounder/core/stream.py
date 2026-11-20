"""IQ source for the codar-sounder daemon.

Two backends are supported:

  * ``RadiodIQSource`` — the production path.  Subscribes to a radiod
    multicast IQ channel via ka9q-python's ``RadiodStream`` /
    ``ManagedStream`` (the same pattern psk-recorder, wspr-recorder,
    hf-timestd, hfdl-recorder use).  ka9q-python is imported lazily so
    the rest of the package remains usable on hosts that don't have it
    installed.

  * ``SyntheticIQSource`` — a deterministic synthetic chirp generator
    used by the end-to-end smoke test and by daemon dry-runs on hosts
    that have no live radiod.  Pretends to be a radiod channel emitting
    one configurable target at a fixed group range, plus optional
    additive complex noise.

Both backends present the same interface: an iterable that yields
``(samples, cpi_start_utc)`` tuples where ``samples`` is a contiguous
``numpy.complex64`` array of ``cpi_n_samples`` length and
``cpi_start_utc`` is a timezone-aware ``datetime`` labeling the UTC
moment of the first sample in that CPI.

The radiod backend derives ``cpi_start_utc`` from the RTP sample
counter plus an optional hf-timestd ``rtp_to_utc_offset_ns``, per
METROLOGY.md §4.5 RTP-reference labeling invariant — no wall-clock
read per CPI, no shadow timing diagnostics.  Each CPI carries its own
position in the sample stream, so a CPI dropped because the consumer
fell behind costs that CPI only, never the labels after it (W2NAF,
2026-11-18: dropped chunks under the old yield count put labels hours
early).  The synthetic backend
labels each CPI with ``datetime.now(timezone.utc)`` since it has no
authoritative time source; that's fine for tests and dry-runs.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Iterator, Optional, Tuple

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from hamsci_dsp.timing import AnchorUTC, AuthorityReader

log = logging.getLogger(__name__)

# Speed of light, km/s — keep in sync with core/invert.py and core/dechirp.py.
C_KM_PER_S = 299_792.458


# ---------------------------------------------------------------------------
# Synthetic source — used for tests and dry-runs on hosts without radiod.
# ---------------------------------------------------------------------------

class SyntheticIQSource:
    """Generate a synthetic CODAR-like FMCW IQ stream with one target.

    The generator emits exactly the kind of signal the dechirp engine
    expects: a continuously-transmitted FMCW chirp with one delayed
    echo.  Group range, amplitude and noise floor are operator-set.

    Real-time pacing: the iterator sleeps so that one CPI takes
    approximately ``cpi_seconds`` of wall time — useful for daemon
    dry-runs where you want to see the JSONL records accumulate at a
    realistic cadence.  Set ``realtime=False`` for tests that want the
    chunks as fast as numpy can generate them.
    """

    def __init__(
        self,
        *,
        sample_rate_hz: float,
        sweep_rate_hz_per_s: float,
        sweep_repetition_hz: float,
        cpi_seconds: float,
        target_group_range_km: float,
        target_amplitude: float = 1.0,
        noise_db: float = -40.0,
        realtime: bool = False,
        range_wobble_km: float = 30.0,
        wobble_period_s: float = 600.0,
        seed: int = 0,
    ):
        self.sample_rate_hz = float(sample_rate_hz)
        self.sweep_rate_hz_per_s = float(sweep_rate_hz_per_s)
        self.sweep_repetition_hz = float(sweep_repetition_hz)
        self.cpi_seconds = float(cpi_seconds)
        self.target_group_range_km = float(target_group_range_km)
        self.target_amplitude = float(target_amplitude)
        self.noise_amplitude = (
            10 ** (noise_db / 20.0) if noise_db > -200 else 0.0
        )
        # Real ionospheric F-region peaks wander 10–50 km on TID and
        # diurnal timescales.  A perfectly stationary synthetic target
        # gets removed by the daemon's rolling-median clutter mask
        # (correctly!) and disappears, which is *not* the
        # representative smoke-test scenario.  Adding a slow sinusoidal
        # wobble keeps the target visible to the F-region trace
        # extractor while letting the clutter mask still suppress
        # truly stationary energy (direct path, 0-Hz DC).
        self.range_wobble_km = float(range_wobble_km)
        self.wobble_period_s = max(float(wobble_period_s), 1.0)
        self.realtime = realtime
        self._rng = np.random.default_rng(seed=seed)
        self._t_offset_s = 0.0
        self._stopped = threading.Event()

    @property
    def cpi_n_samples(self) -> int:
        return int(round(self.sample_rate_hz * self.cpi_seconds))

    def _generate_one_cpi(self) -> np.ndarray:
        n = self.cpi_n_samples
        sweep_period = 1.0 / self.sweep_repetition_hz
        # Sinusoidal wobble centred on target_group_range_km — see __init__.
        wobble = 0.0
        if self.range_wobble_km > 0 and self.wobble_period_s > 0:
            import math
            wobble = self.range_wobble_km * math.sin(
                2 * math.pi * self._t_offset_s / self.wobble_period_s
            )
        delay_s = (self.target_group_range_km + wobble) / C_KM_PER_S

        t = self._t_offset_s + np.arange(n) / self.sample_rate_hz
        # Sweep wraps every sweep_period — modulo gives the in-sweep time.
        t_in_sweep_target = (t - delay_s) % sweep_period
        valid = t >= delay_s
        phase = (
            2.0 * np.pi * 0.5 * self.sweep_rate_hz_per_s * t_in_sweep_target ** 2
        )
        rx = self.target_amplitude * np.exp(1j * phase).astype(np.complex64)
        rx[~valid] = 0
        if self.noise_amplitude > 0:
            rx += self.noise_amplitude * (
                self._rng.standard_normal(n)
                + 1j * self._rng.standard_normal(n)
            ).astype(np.complex64)
        self._t_offset_s += n / self.sample_rate_hz
        return rx

    @property
    def anchor(self) -> None:
        """The synthetic source labels CPIs from the host wall clock and
        never pins an AnchorUTC, so the §3 report for it reads null."""
        return None

    def __iter__(self) -> Iterator[Tuple[np.ndarray, datetime]]:
        while not self._stopped.is_set():
            t0 = time.monotonic()
            # Synthetic source has no authoritative time — label each CPI
            # with wall-clock-now.  This backend is only used for tests
            # and dry-runs; production goes through RadiodIQSource.
            yield (self._generate_one_cpi(), datetime.now(timezone.utc))
            if self.realtime:
                elapsed = time.monotonic() - t0
                remaining = self.cpi_seconds - elapsed
                if remaining > 0:
                    self._stopped.wait(remaining)

    def stop(self) -> None:
        self._stopped.set()


# ---------------------------------------------------------------------------
# radiod source — production path via ka9q-python.
# ---------------------------------------------------------------------------

class RadiodIQSource:
    """Subscribe to a radiod IQ channel via ka9q-python.

    Timing: CPI ``k`` holds stream samples ``[k*N, (k+1)*N)`` counted from
    the first delivered sample (ka9q-python zero-fills lost packets, so the
    count follows radiod's sample clock) and is labelled
    ``anchor + k * cpi_seconds``.  A CPI the consumer has no room for is
    dropped whole and counted in ``dropped_cpis``; the labels after it stay
    true.

    On entry, calls ``RadiodControl.ensure_channel()`` to *provision*
    the channel (radiod has runtime channel control via its multicast
    control plane — no radiod restart needed).  Then attaches a
    ``RadiodStream`` whose ``on_samples`` callback feeds a thread-safe
    queue.  The iterator drains the queue into CPI-sized chunks and
    yields them as ``complex64`` arrays.

    Lazy-imports ``ka9q``-package modules at construction time so a
    host without ka9q-python installed can still load the rest of
    codar-sounder (e.g. ``inventory --json``, ``validate --json``).

    Raises:
        ModuleNotFoundError: if ka9q-python isn't importable.  The
            daemon catches this and falls back to ``SyntheticIQSource``
            with operator-visible warnings.
    """

    def __init__(
        self,
        *,
        radiod_status_dns: str,
        channel_name: str,                  # informational only; ka9q computes SSRC from params
        sample_rate_hz: float,
        cpi_seconds: float,
        center_freq_hz: float,
        preset: str = "iq",
        lifetime_frames: Optional[int] = None,
        filter_low_edge_hz: Optional[float] = None,
        filter_high_edge_hz: Optional[float] = None,
        filter_guard_hz: float = 1500.0,
        authority_reader: Optional["AuthorityReader"] = None,
    ):
        import queue as _q                   # stdlib

        # Imported lazily so the test suite + synthetic mode don't need
        # the authority machinery present to import this module.
        from hamsci_dsp.timing import AuthorityReader as _AR
        self._authority_reader = (
            authority_reader if authority_reader is not None else _AR()
        )

        self.radiod_status_dns = radiod_status_dns
        self.channel_name = channel_name
        self.sample_rate_hz = float(sample_rate_hz)
        self.cpi_seconds = float(cpi_seconds)
        self.center_freq_hz = float(center_freq_hz)
        self.preset = preset
        # Optional crash-safe channel cleanup (ka9q-python 7c6af73+,
        # radiod 0f8b622+).  When set, the channel is provisioned with
        # this lifetime in radiod main-loop frames (~50 Hz at default
        # blocktime) and the daemon refreshes it after every CPI so the
        # channel auto-destructs if the daemon dies.  None = leave the
        # channel infinite (v0.3 and earlier behaviour).
        self.lifetime_frames = lifetime_frames
        # Channel filter (ka9q-python ≥3.11.0).  CODAR chirps span tens
        # of kHz — far wider than the `iq` preset's default ±5 kHz audio
        # filter.  Without an explicit filter, the receiver truncates
        # the chirp's edges and the dechirped range bins smear.  Default
        # to a near-Nyquist filter (matches the hfdl-recorder pattern in
        # daemon.py:97) so the captured IQ spans the full sample-rate
        # bandwidth; the dechirp's matched-filter then concentrates
        # signal energy and rejects out-of-band noise downstream.
        self._filter_guard_hz = float(filter_guard_hz)
        self._filter_low_edge_hz = (
            float(filter_low_edge_hz) if filter_low_edge_hz is not None
            else -self.sample_rate_hz / 2 + self._filter_guard_hz
        )
        self._filter_high_edge_hz = (
            float(filter_high_edge_hz) if filter_high_edge_hz is not None
            else +self.sample_rate_hz / 2 - self._filter_guard_hz
        )
        self._control = None
        self._stream = None
        self._channel_info = None
        # CPIs assemble here, in ka9q's callback thread, which runs
        # continuously; the queue hands finished CPIs to the consumer.
        # The old design queued ~2 s of raw chunks and drained them only
        # when the consumer asked for its next CPI, so every chunk that
        # arrived while it processed one was dropped.
        self._cpi_queue: "_q.Queue[Tuple[np.ndarray, int]]" = _q.Queue(
            maxsize=self.CPI_QUEUE_MAX)
        self._reset_assembly()
        self._stopped = threading.Event()
        # Anchor state for RTP-derived CPI timestamps (METROLOGY.md §4.5
        # RTP-reference invariant).  Captured on the first packet's
        # quality block; consumed in __iter__ to compute each CPI's
        # start UTC by pure sample-count projection.
        self._anchor_first_rtp: Optional[int] = None
        # The AnchorUTC the CPI labels project from.  The daemon reads it
        # for the §3 timing_authority_applied report (CLIENT-CONTRACT
        # §18.5: the report describes the labels, so it comes from the
        # anchor that produced them, never from a fresh authority read).
        # None until the first CPI; reset when __iter__ re-provisions.
        # Each CPI re-pins the epoch's RTP, so this holds the latest CPI's
        # anchor; the CPI's own anchor travels with it out of __iter__.
        self._anchor: Optional["AnchorUTC"] = None
        # The anchor the current epoch took first (the re-pins' wrap hint
        # and their fallback), and the T-level the last CPI carried.
        self._epoch_anchor: Optional["AnchorUTC"] = None
        self._cpi_level: Optional[str] = None
        self._import_ka9q()                  # raises ModuleNotFoundError if missing

    # Finished CPIs waiting for the consumer: three 60 s CPIs (~92 MB at
    # 64 kHz complex64) let one slow CPI's processing finish without a drop.
    CPI_QUEUE_MAX = 3
    # A re-anchored epoch whose RTP-mapped anchor disagrees with the wall
    # clock by more than this is refused, not published: after a radiod
    # restart the mapping can name a dead session.
    REANCHOR_TOLERANCE_S = 2.0
    _wall = staticmethod(time.time)

    @property
    def cpi_n_samples(self) -> int:
        return int(round(self.sample_rate_hz * self.cpi_seconds))

    def _reset_assembly(self) -> None:
        """Start a fresh stream: no anchor packet, CPI 0, nothing queued."""
        self._anchor_first_rtp = None
        self._buf = np.empty(self.cpi_n_samples, dtype=np.complex64)
        self._filled = 0
        self._next_index = 0
        self.dropped_cpis = 0
        # Continuity epoch: the RTP of its first sample and the samples
        # delivered since.  A batch whose delivered_rtp_start disagrees
        # with that count starts a new epoch with its own anchor.
        self._epoch = 0
        self._epoch_rtp: Optional[int] = None
        self._epoch_count = 0
        self._epoch_wall: Optional[float] = None   # wall time of the epoch's first sample
        self.rtp_discontinuities = 0
        self.refused_epochs = 0
        while True:
            try:
                self._cpi_queue.get_nowait()
            except Exception:
                break

    @property
    def anchor(self) -> Optional["AnchorUTC"]:
        """The anchor every CPI label projects from (None until the first
        CPI).  Its ``timing_authority_applied()`` is this source's honest
        §3 report."""
        return self._anchor

    def _import_ka9q(self) -> None:
        global _ka9q_RadiodStream, _ka9q_RadiodControl
        from ka9q.stream import RadiodStream                 # type: ignore[import-not-found]
        from ka9q.control import RadiodControl              # type: ignore[import-not-found]
        _ka9q_RadiodStream = RadiodStream
        _ka9q_RadiodControl = RadiodControl

    def _on_samples(self, samples, quality) -> None:
        """ka9q-python callback — runs on the stream's RX thread.

        ka9q-python's resequencer occasionally produces garbage in
        gap-fill regions: most often NaN, but also occasionally
        finite-but-absurdly-large values (we've observed |x| ≈ 2×10³⁸,
        right at float32 overflow).  Either kind of garbage propagates
        through our coherent FFT — NaN poisons the entire range
        profile; large values overflow during FFT accumulation
        (numpy emits "overflow encountered in cast / invalid value
        encountered in fft" warnings).

        Sanitise here: replace non-finite with zero, then clip values
        whose magnitude exceeds a sanity threshold.  Real radiod
        s16-encoded IQ is normalised to roughly [-1, 1] in float32;
        anything > 100 is unambiguously junk.  Zero-fill replaces
        garbage with silence — the dechirper handles it gracefully.
        """
        if self._stopped.is_set():
            return
        # Capture the very first packet's RTP timestamp for CPI anchoring.
        # Done in the callback (not the iterator) because the first packet
        # may arrive before the iter loop has consumed anything.
        if self._anchor_first_rtp is None:
            first_rtp = getattr(quality, "first_rtp_timestamp", None)
            if first_rtp is not None:
                self._anchor_first_rtp = int(first_rtp)
        self._check_continuity(getattr(quality, "delivered_rtp_start", None),
                               len(samples) / self.sample_rate_hz)
        arr = np.asarray(samples, dtype=np.complex64)
        if not np.all(np.isfinite(arr)):
            arr = np.where(np.isfinite(arr), arr, np.complex64(0))
        # Magnitude-clip garbage (10x the largest plausible normalised IQ).
        too_large = np.abs(arr) > 100.0
        if np.any(too_large):
            arr = np.where(too_large, np.complex64(0), arr)
        self._epoch_count += arr.size
        idx = 0
        n = self.cpi_n_samples
        while idx < arr.size:
            take = min(arr.size - idx, n - self._filled)
            self._buf[self._filled:self._filled + take] = arr[idx:idx + take]
            self._filled += take
            idx += take
            if self._filled == n:
                k = self._next_index
                try:
                    self._cpi_queue.put_nowait(
                        (self._buf, k, self._epoch, self._epoch_rtp, self._epoch_wall))
                except Exception:
                    # The consumer is a whole queue behind: drop this CPI.
                    # Its successors keep their true labels.
                    self.dropped_cpis += 1
                    log.warning(
                        "RadiodIQSource: CPI %d dropped, the consumer is %d CPIs "
                        "behind (%d dropped since start); later CPIs keep their "
                        "true times", k, self.CPI_QUEUE_MAX, self.dropped_cpis)
                self._buf = np.empty(n, dtype=np.complex64)
                self._filled = 0
                self._next_index = k + 1

    def _check_continuity(self, rtp0, batch_s: float = 0.0) -> None:
        """Compare a batch's first-sample RTP with the samples counted
        since the epoch began; on disagreement, start a new epoch.

        ka9q-python zero-fills lost packets, but not every case: a gap past
        its cap (15 s at 64 kHz), a late packet re-emitted after recovery,
        a radiod restart that re-bases RTP.  Each makes the count differ
        from elapsed RTP, and every label after it would shift silently.
        Timing faults are exposed, never smoothed: the partial CPI is
        discarded, the fault is logged and counted, and later CPIs label
        from the new epoch's own anchor.  Without ``delivered_rtp_start``
        (older ka9q-python) there is nothing to check against."""
        if rtp0 is None:
            if self._epoch_rtp is None:
                self._epoch_rtp = self._anchor_first_rtp
                self._epoch_wall = self._wall() - batch_s
            return
        rtp0 = int(rtp0) & 0xFFFFFFFF
        if self._epoch_rtp is None:
            self._epoch_rtp = rtp0
            self._epoch_count = 0
            self._epoch_wall = self._wall() - batch_s
            return
        if (rtp0 - self._epoch_rtp) & 0xFFFFFFFF == self._epoch_count & 0xFFFFFFFF:
            return
        self.rtp_discontinuities += 1
        log.error(
            "RadiodIQSource: RTP discontinuity: batch starts at rtp %d, the "
            "sample count expects %d (%+d samples); CPI %d (%d samples) "
            "discarded, new anchor (%d discontinuities since start)",
            rtp0, (self._epoch_rtp + self._epoch_count) & 0xFFFFFFFF,
            ((rtp0 - self._epoch_rtp - self._epoch_count + 2**31) % 2**32) - 2**31,
            self._next_index, self._filled, self.rtp_discontinuities)
        self._epoch += 1
        self._epoch_rtp = rtp0
        self._epoch_count = 0
        self._epoch_wall = self._wall() - batch_s
        self._filled = 0
        self._next_index = 0

    def _anchor_epoch(self, epoch: int, epoch_rtp: Optional[int],
                      epoch_wall: Optional[float], *, reanchor: bool) -> Optional[datetime]:
        """The anchor for one epoch's labels, or None to withhold them.

        The first epoch anchors through the mapping provisioning just read.
        A later epoch follows an RTP discontinuity, perhaps a radiod restart,
        so the channel's status is read again first, and the anchor must
        agree with the wall clock within REANCHOR_TOLERANCE_S, else the
        epoch's CPIs are refused and counted in refused_epochs."""
        behind = 0
        if epoch_wall is not None:
            behind = max(0, int(round((self._wall() - epoch_wall) * self.sample_rate_hz)))
        if reanchor:
            try:
                self._channel_info = self._control.ensure_channel(**self._ensure_kwargs)
            except Exception as exc:
                log.error("RadiodIQSource: channel status re-read failed (%s); "
                          "epoch %d refused", exc, epoch)
                self.refused_epochs += 1
                return None
        anchor = self._compute_anchor_utc(first_rtp=epoch_rtp, samples_behind=behind)
        if reanchor and epoch_wall is not None:
            wall = datetime.fromtimestamp(epoch_wall, tz=timezone.utc)
            off = (anchor - wall).total_seconds()
            if abs(off) > self.REANCHOR_TOLERANCE_S:
                self.refused_epochs += 1
                log.error(
                    "RadiodIQSource: epoch %d anchor %s disagrees with the wall "
                    "clock by %+.3f s (tolerance %.1f s); its CPIs are refused "
                    "(%d refused epochs)", epoch, anchor.isoformat(), off,
                    self.REANCHOR_TOLERANCE_S, self.refused_epochs)
                return None
        return anchor

    def _compute_anchor_utc(self, first_rtp: Optional[int] = None,
                            samples_behind: int = 0) -> datetime:
        """Derive the UTC timestamp of the very first sample delivered.

        Delegates to the suite-shared hamsci_dsp.timing.acquire_anchor_utc
        helper: it pins the captured first_rtp_timestamp + channel_info
        (gps_time / rtp_timesnap) to UTC via ka9q.rtp_to_utc, then adds the
        rtp_to_utc_offset_ns published by hf-timestd if available.  This is
        the §4.5 RTP-reference invariant in concrete form: time is
        hf-timestd's product, the client just consumes it.  codar anchors
        the FIRST delivered sample (samples_behind=0) — it does not align to
        a UTC cadence grid.  This is the epoch's first anchor; each later
        CPI re-pins the same RTP with a fresh authority read (_repin).

        If the first RTP timestamp wasn't captured (no packet ever
        arrived — extremely unlikely by the time we're computing this)
        OR rtp_to_utc returns None, the helper falls back to wall-clock.
        """
        from hamsci_dsp.timing import acquire_anchor_utc
        from ka9q import rtp_to_utc  # type: ignore

        if first_rtp is None:
            first_rtp = self._anchor_first_rtp
        a = acquire_anchor_utc(
            first_rtp=first_rtp,
            channel_info=self._channel_info,
            rtp_to_utc=rtp_to_utc,
            authority_reader=self._authority_reader,
            # Samples delivered since first_rtp: the wall-clock fallback
            # subtracts them, so it names the epoch's first sample, not
            # the moment a CPI was dequeued (~one CPI later).
            samples_behind=int(samples_behind),
            sample_rate=int(self.sample_rate_hz),
        )
        log.info(
            "RadiodIQSource: CPI anchor %s (rtp=%s, source=%s, authority=%s, "
            "offset=%+.6fs)",
            a.datetime.isoformat(),
            first_rtp,
            a.source,
            (a.snapshot.t_level_active if a.snapshot else "unavailable"),
            a.offset_seconds,
        )
        self._anchor = a
        self._epoch_anchor = a
        return a.datetime

    def _repin(self, epoch_rtp: Optional[int], epoch_utc: datetime,
               epoch_anchor) -> Tuple[object, datetime]:
        """Re-pin the epoch's FIXED RTP with a fresh authority read; return
        (the CPI's AnchorUTC, the UTC of the epoch's first sample).

        The timing authority may change level at any time (T4 at a restart,
        T6 eight minutes later), and each CPI's label and T-level must say
        what it reads when that CPI is labelled.  AC0G-ND 2026-11-19: one
        anchor per epoch, taken on T4, labelled days of CPIs with the T4
        offset and kept every row off ``t6_anchor``.

        The wrap hint is the epoch anchor's own instant, never "now": the
        RTP ages, and a "now" hint past P/2 of uptime picks the wrong wrap
        (AC0G-B4 2026-09-08, +99.42 h).  If the re-pin is not
        RTP-referenced, the CPI keeps the epoch anchor's label and its
        anchor is marked not RTP-referenced, so its rows carry no T-level.
        """
        from dataclasses import replace
        from hamsci_dsp.timing import acquire_anchor_utc

        a = None
        try:
            from ka9q import rtp_to_utc  # type: ignore
            a = acquire_anchor_utc(
                first_rtp=epoch_rtp if epoch_rtp is not None else self._anchor_first_rtp,
                channel_info=self._channel_info,
                rtp_to_utc=rtp_to_utc,
                authority_reader=self._authority_reader,
                sample_rate=int(self.sample_rate_hz),
                anchor_hint_utc=epoch_utc.timestamp(),
            )
        except Exception as exc:  # noqa: BLE001 — never crash the RX path
            log.warning("RadiodIQSource: CPI re-pin failed (%s); the CPI keeps "
                        "the epoch anchor's label", exc)
        if a is not None and a.rtp_referenced:
            return a, a.datetime
        fallback = (replace(epoch_anchor, rtp_referenced=False)
                    if epoch_anchor is not None else None)
        return fallback, epoch_utc

    def _note_cpi_anchor(self, k: int, a) -> None:
        """DEBUG per CPI; INFO only when the T-level the rows carry changes."""
        snap = getattr(a, "snapshot", None)
        usable = (a is not None and a.rtp_referenced and snap is not None
                  and getattr(snap, "offset_usable", False))
        level = getattr(snap, "t_level_active", None) if usable else None
        log.debug("RadiodIQSource: CPI %d anchor %s (source=%s, t_level=%s)",
                  k, a.datetime.isoformat() if a is not None else None,
                  getattr(a, "source", None), level)
        if level != self._cpi_level:
            log.info("RadiodIQSource: CPI %d timing level %s -> %s (source=%s, "
                     "offset=%+.6fs)", k, self._cpi_level, level,
                     getattr(a, "source", None),
                     getattr(a, "offset_seconds", 0.0) if a is not None else 0.0)
            self._cpi_level = level

    def __iter__(self) -> Iterator[Tuple[np.ndarray, datetime, object]]:
        """Yield (samples, start UTC, AnchorUTC) per CPI.  The anchor is
        the one that labelled THIS CPI; the daemon stamps its rows with
        that anchor's T-level."""
        log.info(
            "RadiodIQSource: provisioning channel on %s freq=%d Hz "
            "preset=%s sample_rate=%d Hz encoding=F32LE filter=[%.0f,%.0f] Hz",
            self.radiod_status_dns,
            int(self.center_freq_hz),
            self.preset,
            int(self.sample_rate_hz),
            self._filter_low_edge_hz,
            self._filter_high_edge_hz,
        )
        # client_id makes ka9q-python derive a per-(client, radiod)
        # multicast destination so CODAR's IQ stream never shares a
        # multicast group with peer clients on the same radiod.
        # CONTRACT v0.3 §7 / ka9q-python ≥ 3.14.0.
        self._control = _ka9q_RadiodControl(  # type: ignore[name-defined]
            self.radiod_status_dns,
            client_id="codar-sounder",
        )
        # A fresh channel means a fresh stream; any earlier anchor named
        # labels that stream no longer produces, and its sample count
        # starts again at zero.
        self._anchor = None
        self._epoch_anchor = None
        self._cpi_level = None
        self._reset_assembly()
        # Force F32LE (encoding=4) IQ.  ka9q-python's default (S16BE)
        # appears to deliver byte-swap-corrupted samples from this
        # ka9q-python / radiod combination — magnitudes come out as
        # subnormal float32 garbage (~10⁻⁴¹) interspersed with values
        # that overflow to ~10³⁸.  F32LE delivers the underlying IQ
        # cleanly: every sample finite, magnitudes in the expected
        # ~10⁻⁵ to ~10⁻⁴ range, no decoder pathology.
        ensure_kwargs = dict(
            frequency_hz=float(self.center_freq_hz),
            preset=self.preset,
            sample_rate=int(self.sample_rate_hz),
            encoding=4,            # F32LE
            agc_enable=0,          # policy 2026-10-23: no per-channel AGC,
            gain=0.0,              # fixed 0 dB (the iq preset defaults agc=yes)
            low_edge=self._filter_low_edge_hz,
            high_edge=self._filter_high_edge_hz,
        )
        # Pass `lifetime` only when we have one — older ka9q-python without
        # the keep-alive feature would reject the unknown kwarg.
        if self.lifetime_frames is not None:
            ensure_kwargs["lifetime"] = int(self.lifetime_frames)
        self._ensure_kwargs = ensure_kwargs
        self._channel_info = self._control.ensure_channel(**ensure_kwargs)
        log.info(
            "RadiodIQSource: channel ready: ssrc=%s mcast=%s:%d lifetime=%s",
            self._channel_info.ssrc,
            self._channel_info.multicast_address,
            self._channel_info.port,
            "infinite" if self.lifetime_frames is None
            else f"{self.lifetime_frames} frames (refreshed per CPI)",
        )
        self._stream = _ka9q_RadiodStream(                  # type: ignore[name-defined]
            channel=self._channel_info,
            on_samples=self._on_samples,
        )
        self._stream.start()

        anchor_epoch: Optional[int] = None
        anchor_utc: Optional[datetime] = None
        epoch_anchor = None
        n = self.cpi_n_samples
        try:
            while not self._stopped.is_set():
                try:
                    buf, k, epoch, epoch_rtp, epoch_wall = self._cpi_queue.get(timeout=1.0)
                except Exception:
                    continue                 # timeout — just check stopped flag
                # Crash-safe keep-alive: if a finite lifetime was
                # configured, refresh it before yielding so the
                # channel survives the consumer's processing time.
                # Failure here (radiod restart, network blip) must
                # not crash the daemon — log and continue.
                if self.lifetime_frames is not None:
                    try:
                        self._control.set_channel_lifetime(
                            self._channel_info.ssrc,
                            int(self.lifetime_frames),
                        )
                    except Exception as exc:
                        log.warning(
                            "RadiodIQSource: lifetime refresh failed: %s",
                            exc,
                        )
                # Anchor (one-time): the first packet has set
                # self._anchor_first_rtp; derive the RTP-anchored
                # UTC of the first sample ever delivered, applying
                # hf-timestd's offset when available (§4.5).
                fresh = anchor_epoch != epoch
                if fresh:
                    self._epoch_anchor = None
                    anchor_utc = self._anchor_epoch(
                        epoch, epoch_rtp, epoch_wall, reanchor=anchor_epoch is not None)
                    anchor_epoch = epoch
                    epoch_anchor = self._epoch_anchor if anchor_utc is not None else None
                if anchor_utc is None:
                    continue                 # refused epoch: withhold, never mislabel
                # The epoch's first CPI rides the anchor just taken; each
                # later one re-pins the epoch's fixed RTP with a fresh
                # authority read, so its label and T-level follow the
                # authority as it stands now.
                if fresh:
                    cpi_anchor, base = epoch_anchor, anchor_utc
                else:
                    cpi_anchor, base = self._repin(epoch_rtp, anchor_utc, epoch_anchor)
                    if cpi_anchor is not None:
                        self._anchor = cpi_anchor    # the §3 report: latest CPI
                self._note_cpi_anchor(k, cpi_anchor)
                # The CPI's own position in the stream, never a count of
                # CPIs yielded: a dropped CPI shifts nothing after it.
                # Samples, not cpi_seconds, so a rate x CPI that is not an
                # integer cannot drift.
                yield (buf, base + timedelta(
                    seconds=k * n / self.sample_rate_hz), cpi_anchor)
        finally:
            try:
                self._stream.stop()
            except Exception as exc:
                log.warning("RadiodStream stop failed: %s", exc)

    def stop(self) -> None:
        self._stopped.set()


# ---------------------------------------------------------------------------
# Factory — try radiod first, fall back to synthetic with a clear warning.
# ---------------------------------------------------------------------------

def make_iq_source(
    *,
    radiod_status_dns: str,
    channel_name: str,
    sample_rate_hz: float,
    cpi_seconds: float,
    sweep_rate_hz_per_s: float,
    sweep_repetition_hz: float,
    center_freq_hz: float,
    preset: str = "iq",
    fallback_target_group_range_km: float = 500.0,
    force_synthetic: bool = False,
    lifetime_frames: Optional[int] = None,
    filter_low_edge_hz: Optional[float] = None,
    filter_high_edge_hz: Optional[float] = None,
    filter_guard_hz: float = 1500.0,
):
    """Construct an IQ source, falling back to synthetic if radiod isn't available.

    The fallback case is *deliberately noisy in the log* so an operator
    who expects real data sees the warning rather than wondering why
    the JSONL records all show the same target.
    """
    if not force_synthetic:
        try:
            return RadiodIQSource(
                radiod_status_dns=radiod_status_dns,
                channel_name=channel_name,
                sample_rate_hz=sample_rate_hz,
                cpi_seconds=cpi_seconds,
                center_freq_hz=center_freq_hz,
                preset=preset,
                lifetime_frames=lifetime_frames,
                filter_low_edge_hz=filter_low_edge_hz,
                filter_high_edge_hz=filter_high_edge_hz,
                filter_guard_hz=filter_guard_hz,
            )
        except ModuleNotFoundError as exc:
            log.warning(
                "ka9q-python not importable (%s) — falling back to "
                "SyntheticIQSource.  Install ka9q-python and restart for "
                "live data.", exc,
            )

    log.warning(
        "Using SyntheticIQSource: target group range = %.1f km, "
        "no real ionospheric data will be produced.",
        fallback_target_group_range_km,
    )
    return SyntheticIQSource(
        sample_rate_hz=sample_rate_hz,
        sweep_rate_hz_per_s=sweep_rate_hz_per_s,
        sweep_repetition_hz=sweep_repetition_hz,
        cpi_seconds=cpi_seconds,
        target_group_range_km=fallback_target_group_range_km,
        realtime=True,
    )
