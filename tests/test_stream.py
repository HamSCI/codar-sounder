"""Tests for RadiodIQSource filter-edge defaulting.

CODAR chirps span tens of kHz — far wider than the `iq` preset's
default ±5 kHz audio filter.  RadiodIQSource auto-computes near-Nyquist
filter edges (matching the hfdl-recorder pattern in
hfdl-recorder/src/hfdl_recorder/core/daemon.py:97) so the captured IQ
spans the full sample-rate bandwidth and the dechirp's matched filter
can concentrate signal energy across the chirp's full bandwidth.

Live ensure_channel(low_edge=...) plumbing — the actual TLV exchange
with radiod — is exercised in the live smoke test on bee1-rx888;
this module covers only the Python-side defaulting logic.
"""
from __future__ import annotations

import pytest


# ka9q-python must be importable for RadiodIQSource construction.
ka9q = pytest.importorskip("ka9q.control")
from codar_sounder.core.stream import RadiodIQSource


def _src(**kw) -> RadiodIQSource:
    """Construct a RadiodIQSource with default mandatory args + overrides."""
    base = dict(
        radiod_status_dns="test.local",
        channel_name="codar-test",
        sample_rate_hz=64000,
        cpi_seconds=60.0,
        center_freq_hz=4_500_000,
    )
    base.update(kw)
    return RadiodIQSource(**base)


class TestFilterDefaulting:

    def test_default_filter_is_near_nyquist(self):
        """Default behaviour: ±sample_rate/2 ∓ 1500 Hz guard."""
        src = _src()
        assert src._filter_low_edge_hz == pytest.approx(-30500.0)
        assert src._filter_high_edge_hz == pytest.approx(+30500.0)

    def test_default_scales_with_sample_rate(self):
        """Higher sample rate → wider filter (still leaving 1500 Hz guard)."""
        src = _src(sample_rate_hz=192_000)
        assert src._filter_low_edge_hz == pytest.approx(-94500.0)
        assert src._filter_high_edge_hz == pytest.approx(+94500.0)

    def test_explicit_edges_override_default(self):
        """Operator can request a tighter filter for SNR optimisation."""
        src = _src(
            filter_low_edge_hz=-15000.0,
            filter_high_edge_hz=+15000.0,
        )
        assert src._filter_low_edge_hz == pytest.approx(-15000.0)
        assert src._filter_high_edge_hz == pytest.approx(+15000.0)

    def test_partial_override_falls_back_to_default_for_unspecified_edge(self):
        src = _src(filter_low_edge_hz=-10000.0)    # only low explicitly set
        assert src._filter_low_edge_hz == pytest.approx(-10000.0)
        # high_edge falls back to default Nyquist - guard
        assert src._filter_high_edge_hz == pytest.approx(+30500.0)

    def test_custom_guard_band(self):
        """Tighter guard band (e.g. 500 Hz) widens the captured spectrum."""
        src = _src(filter_guard_hz=500.0)
        assert src._filter_low_edge_hz == pytest.approx(-31500.0)
        assert src._filter_high_edge_hz == pytest.approx(+31500.0)

    def test_explicit_edges_ignore_guard(self):
        """When explicit edges are given, filter_guard_hz has no effect."""
        src = _src(
            filter_low_edge_hz=-12000.0,
            filter_high_edge_hz=+12000.0,
            filter_guard_hz=10_000.0,    # would clobber the default but is ignored
        )
        assert src._filter_low_edge_hz == pytest.approx(-12000.0)
        assert src._filter_high_edge_hz == pytest.approx(+12000.0)


# ---------------------------------------------------------------------------
# The source keeps the AnchorUTC it pins (CLIENT-CONTRACT §18.5: the
# daemon reports the registration its labels ride, not a fresh read).
# ---------------------------------------------------------------------------

from types import SimpleNamespace
from unittest import mock

import codar_sounder.core.stream as _stream_mod


class _FullSnap:
    """AuthoritySnapshot stand-in with the fields the §3 report needs."""
    offset_usable = True
    offset_seconds = 0.004250
    rtp_to_utc_offset_ns = 4_250_000
    t_level_active = "T6"
    sigma_ns = 4210
    governor_radiod = "AC0G-B4-status.local"
    utc_published = None
    host_clock = {"verdict": "ok"}


class _FullReader:
    def read(self):
        return _FullSnap()


class _NoAuthority:
    def read(self):
        return None


class TestAnchorKeptForTheReport:

    def test_anchor_is_none_before_the_first_anchor(self):
        assert _src().anchor is None

    def test_compute_anchor_keeps_the_anchor_and_returns_its_datetime(self):
        src = _src(authority_reader=_FullReader())
        src._anchor_first_rtp = 1_000_000
        src._channel_info = SimpleNamespace(ssrc=1)
        with mock.patch("ka9q.rtp_to_utc", return_value=1_700_000_500.0):
            dt = src._compute_anchor_utc()
        assert src.anchor is not None
        assert src.anchor.offset_ns == 4_250_000
        assert src.anchor.source == "rtp_to_utc+authority"
        assert src.anchor.datetime == dt
        block = src.anchor.timing_authority_applied(client_radiod="rx")
        assert block["tier"] == "T6"

    def test_anchor_without_authority_has_no_offset(self):
        src = _src(authority_reader=_NoAuthority())
        src._anchor_first_rtp = 1_000_000
        src._channel_info = SimpleNamespace(ssrc=1)
        with mock.patch("ka9q.rtp_to_utc", return_value=1_700_000_500.0):
            src._compute_anchor_utc()
        assert src.anchor.offset_ns is None
        assert src.anchor.timing_authority_applied(client_radiod="rx") is None

    def test_reprovisioning_drops_the_anchor(self):
        """``__iter__`` provisions a fresh channel; the old anchor names a
        stream that no longer exists."""
        src = _src(authority_reader=_FullReader())
        src._anchor = object()
        src._stopped.set()                      # loop exits at once
        fake_ci = SimpleNamespace(ssrc=7, multicast_address="239.0.0.1", port=5004)
        fake_control = mock.Mock()
        fake_control.ensure_channel.return_value = fake_ci
        with mock.patch.object(_stream_mod, "_ka9q_RadiodControl",
                               return_value=fake_control, create=True), \
             mock.patch.object(_stream_mod, "_ka9q_RadiodStream",
                               return_value=mock.Mock(), create=True):
            assert list(iter(src)) == []
        assert src.anchor is None


class TestSyntheticSourceHasNoAnchor:

    def test_synthetic_anchor_is_none(self):
        from codar_sounder.core.stream import SyntheticIQSource
        s = SyntheticIQSource(
            sample_rate_hz=64000, cpi_seconds=1.0,
            sweep_rate_hz_per_s=-25733.913, sweep_repetition_hz=1.0,
            target_group_range_km=500.0,
        )
        assert s.anchor is None


class TestAgcPolicy:
    """Policy 2026-10-23: no per-channel AGC.  The `iq` preset defaults to
    agc=yes, so the channel request must carry agc_enable=0 and gain 0 dB."""

    def test_channel_request_sends_agc_off_and_zero_gain(self):
        from types import SimpleNamespace
        src = _src()
        src._stopped.set()                      # loop exits at once
        fake_ci = SimpleNamespace(ssrc=7, multicast_address="239.0.0.1", port=5004)
        fake_control = mock.Mock()
        fake_control.ensure_channel.return_value = fake_ci
        with mock.patch.object(_stream_mod, "_ka9q_RadiodControl",
                               return_value=fake_control, create=True), \
             mock.patch.object(_stream_mod, "_ka9q_RadiodStream",
                               return_value=mock.Mock(), create=True):
            list(iter(src))
        kw = fake_control.ensure_channel.call_args.kwargs
        assert kw["agc_enable"] == 0
        assert kw["gain"] == 0.0


# ---------------------------------------------------------------------------
# CPI timing: each CPI is labelled by its true sample position.
# W2NAF/B1A/SIGMA/AB4EJ, 2026-11-18: the callback dropped queue chunks while
# the daemon processed a CPI, and CPIs were labelled anchor + yield_count *
# cpi_seconds, so every drop made all later labels early (6-8 h after two
# days).  CPIs now assemble in the callback, carry their sample index, and a
# CPI the consumer cannot take is dropped whole; later labels stay true.
# ---------------------------------------------------------------------------

import itertools
from datetime import datetime, timedelta, timezone

import numpy as np

ANCHOR = datetime(2026, 11, 18, 0, 0, 0, tzinfo=timezone.utc)


def _small(**kw):
    # 1000 Hz x 1 s CPIs keep the arrays tiny.
    return _src(sample_rate_hz=1000, cpi_seconds=1.0, **kw)


def _feed(src, n_samples, chunk=300, value=None, rtp=None):
    """Feed samples in chunks.  value=None feeds a running ramp (sample i
    carries i % 97, under the sanitiser's |x| > 100 clip), so content and
    order are checkable.  rtp, when given, is the
    delivered_rtp_start of the first chunk; later chunks follow on."""
    start = getattr(src, "_fed", 0)
    left = n_samples
    pos = start
    while left > 0:
        k = min(chunk, left)
        arr = ((np.arange(pos, pos + k) % 97).astype(np.float32).astype(np.complex64)
               if value is None else np.full(k, value, dtype=np.complex64))
        q = SimpleNamespace(first_rtp_timestamp=5_000,
                            delivered_rtp_start=None if rtp is None else rtp + (pos - start))
        src._on_samples(arr, q)
        pos += k
        left -= k
    src._fed = pos


def _iterate(src, n):
    fake_ci = SimpleNamespace(ssrc=7, multicast_address="239.0.0.1", port=5004)
    fake_control = mock.Mock()
    fake_control.ensure_channel.return_value = fake_ci
    with mock.patch.object(_stream_mod, "_ka9q_RadiodControl",
                           return_value=fake_control, create=True), \
         mock.patch.object(_stream_mod, "_ka9q_RadiodStream",
                           return_value=mock.Mock(), create=True), \
         mock.patch.object(type(src), "_compute_anchor_utc", return_value=ANCHOR):
        return list(itertools.islice(iter(src), n))


class TestCpiTiming:

    def test_cpis_assemble_in_the_callback_with_their_index(self):
        src = _small()
        _feed(src, 2500)                          # 2.5 CPIs
        got = [src._cpi_queue.get_nowait() for _ in range(src._cpi_queue.qsize())]
        assert [k for _, k, *_ in got] == [0, 1]
        assert all(buf.size == 1000 for buf, *_ in got)
        assert src._filled == 500                 # the third CPI is half full

    def test_a_cpi_the_consumer_cannot_take_is_dropped_whole(self):
        src = _small()
        _feed(src, 1000 * (src.CPI_QUEUE_MAX + 2))
        kept = [k for _, k, *_ in (src._cpi_queue.get_nowait()
                               for _ in range(src._cpi_queue.qsize()))]
        assert kept == list(range(src.CPI_QUEUE_MAX))
        assert src.dropped_cpis == 2

    def test_labels_follow_the_sample_index_across_a_drop(self):
        src = _small()
        _feed(src, 1000 * (src.CPI_QUEUE_MAX + 2))     # CPIs 0..Q-1 kept, Q and Q+1 dropped
        # The consumer frees the queue; streaming goes on.
        for _ in range(src._cpi_queue.qsize()):
            src._cpi_queue.get_nowait()
        _feed(src, 1000)                                # CPI Q+2
        src._stopped.clear()
        got = _iterate_prefilled(src, 1)
        (_, t), = got
        assert t == ANCHOR + timedelta(seconds=src.CPI_QUEUE_MAX + 2)

    def test_labels_are_anchor_plus_index_times_cpi(self):
        src = _small()
        _feed(src, 3000)
        got = _iterate_prefilled(src, 3)
        assert [t for _, t in got] == [ANCHOR + timedelta(seconds=k) for k in range(3)]

    def test_samples_are_still_sanitised(self):
        src = _small()
        q = SimpleNamespace(first_rtp_timestamp=5_000)
        bad = np.full(1000, 1.0, dtype=np.complex64)
        bad[3] = np.nan
        bad[4] = 1e30
        src._on_samples(bad, q)
        buf, *_ = src._cpi_queue.get_nowait()
        assert buf[3] == 0 and buf[4] == 0 and np.all(np.isfinite(buf))

    def test_reprovisioning_resets_the_assembly(self):
        src = _small()
        _feed(src, 1500)
        src._stopped.set()
        _iterate(src, 1)
        assert src._filled == 0 and src._next_index == 0
        assert src._anchor_first_rtp is None
        assert src._cpi_queue.qsize() == 0


def _iterate_prefilled(src, n):
    """Iterate without re-provisioning wiping what the test fed: feed into a
    fresh source's queue only after __iter__ has provisioned."""
    fed = []
    while src._cpi_queue.qsize():
        fed.append(src._cpi_queue.get_nowait())
    fake_ci = SimpleNamespace(ssrc=7, multicast_address="239.0.0.1", port=5004)
    fake_control = mock.Mock()
    fake_control.ensure_channel.return_value = fake_ci

    def _stream(*a, **kw):
        for item in fed:
            src._cpi_queue.put_nowait(item)
        return mock.Mock()

    with mock.patch.object(_stream_mod, "_ka9q_RadiodControl",
                           return_value=fake_control, create=True), \
         mock.patch.object(_stream_mod, "_ka9q_RadiodStream",
                           side_effect=_stream, create=True), \
         mock.patch.object(type(src), "_compute_anchor_utc", return_value=ANCHOR):
        return list(itertools.islice(iter(src), n))


class TestCpiContentAndContinuity:

    def test_each_cpi_holds_its_own_samples_in_order(self):
        src = _small()
        _feed(src, 3000, chunk=377)               # chunks straddle CPI edges
        for _ in range(3):
            buf, k, *_ = src._cpi_queue.get_nowait()
            assert np.array_equal(buf.real, np.arange(k * 1000, (k + 1) * 1000) % 97)

    def test_continuous_rtp_keeps_one_epoch(self):
        src = _small()
        _feed(src, 2500, rtp=9_000)
        assert src.rtp_discontinuities == 0
        assert src._epoch == 0 and src._epoch_rtp == 9_000

    def test_an_rtp_jump_discards_the_partial_cpi_and_starts_a_new_epoch(self):
        src = _small()
        _feed(src, 1500, chunk=500, rtp=9_000)    # CPI 0 complete, CPI 1 half full
        # The next batch claims a later RTP than the count expects (a gap
        # the resequencer did not fill): 20,000 samples missing.
        q = SimpleNamespace(first_rtp_timestamp=5_000,
                            delivered_rtp_start=9_000 + 1500 + 20_000)
        src._on_samples(np.zeros(1000, dtype=np.complex64), q)
        assert src.rtp_discontinuities == 1
        items = [src._cpi_queue.get_nowait() for _ in range(src._cpi_queue.qsize())]
        assert [(k, e) for _, k, e, *_ in items] == [(0, 0), (0, 1)]
        assert items[1][3] == 9_000 + 1500 + 20_000      # the new epoch's anchor RTP

    def test_a_new_epoch_labels_from_its_own_anchor(self):
        src = _small()
        later_ts = (ANCHOR + timedelta(hours=1)).timestamp()
        src._wall = lambda: later_ts + 0.5        # the re-anchor agrees with the wall clock
        _feed(src, 1000, rtp=9_000)
        q = SimpleNamespace(first_rtp_timestamp=5_000, delivered_rtp_start=50_000)
        src._on_samples(np.zeros(2000, dtype=np.complex64), q)
        later = ANCHOR + timedelta(hours=1)
        anchors = {9_000: ANCHOR, 50_000: later}
        fed = [src._cpi_queue.get_nowait() for _ in range(src._cpi_queue.qsize())]
        fake_ci = SimpleNamespace(ssrc=7, multicast_address="239.0.0.1", port=5004)
        fake_control = mock.Mock()
        fake_control.ensure_channel.return_value = fake_ci

        def _stream(*a, **kw):
            for item in fed:
                src._cpi_queue.put_nowait(item)
            return mock.Mock()

        with mock.patch.object(_stream_mod, "_ka9q_RadiodControl",
                               return_value=fake_control, create=True), \
             mock.patch.object(_stream_mod, "_ka9q_RadiodStream",
                               side_effect=_stream, create=True), \
             mock.patch.object(type(src), "_compute_anchor_utc",
                               side_effect=lambda first_rtp=None, samples_behind=0: anchors[first_rtp]):
            got = list(itertools.islice(iter(src), 3))
        assert [t for _, t in got] == [ANCHOR, later, later + timedelta(seconds=1)]

    def test_rtp_wrap_is_not_a_discontinuity(self):
        src = _small()
        _feed(src, 2000, chunk=500, rtp=2**32 - 700)
        assert src.rtp_discontinuities == 0

    def test_without_delivered_rtp_start_nothing_is_checked(self):
        src = _small()
        _feed(src, 2000)                          # rtp=None: older ka9q-python
        assert src.rtp_discontinuities == 0 and src._epoch == 0

    def test_label_uses_samples_not_cpi_seconds(self):
        # 1000.5 Hz x 1 s: N = round(1000.5) = 1000 samples, so CPI k starts
        # at k * 1000 / 1000.5 s, not k * 1 s.
        src = _src(sample_rate_hz=1000.5, cpi_seconds=1.0)
        _feed(src, 3000)
        got = _iterate_prefilled(src, 3)
        assert got[2][1] == ANCHOR + timedelta(seconds=2 * 1000 / 1000.5)

    def test_a_reanchor_far_from_the_wall_clock_is_refused(self):
        src = _small()
        src._wall = lambda: (ANCHOR + timedelta(hours=1)).timestamp()
        _feed(src, 1000, rtp=9_000)
        q = SimpleNamespace(first_rtp_timestamp=5_000, delivered_rtp_start=50_000)
        src._on_samples(np.zeros(2000, dtype=np.complex64), q)
        # The stale mapping names the dead session: 3 h away from the wall clock.
        anchors = {9_000: ANCHOR, 50_000: ANCHOR - timedelta(hours=2)}
        fed = [src._cpi_queue.get_nowait() for _ in range(src._cpi_queue.qsize())]
        fake_ci = SimpleNamespace(ssrc=7, multicast_address="239.0.0.1", port=5004)
        fake_control = mock.Mock()
        fake_control.ensure_channel.return_value = fake_ci

        def _stream(*a, **kw):
            for item in fed:
                src._cpi_queue.put_nowait(item)
            return mock.Mock()

        with mock.patch.object(_stream_mod, "_ka9q_RadiodControl",
                               return_value=fake_control, create=True), \
             mock.patch.object(_stream_mod, "_ka9q_RadiodStream",
                               side_effect=_stream, create=True), \
             mock.patch.object(type(src), "_compute_anchor_utc",
                               side_effect=lambda first_rtp=None, samples_behind=0: anchors[first_rtp]):
            # Once the queue runs dry, stop: epoch 1's CPIs are refused, so
            # the generator would otherwise wait for more.
            real_get = src._cpi_queue.get

            def get(timeout=None):
                if src._cpi_queue.qsize() == 0:
                    src._stopped.set()
                    raise Exception("empty")
                return real_get(timeout=timeout)
            src._cpi_queue.get = get
            got = list(iter(src))
            first, rest = got[0], got[1:]
        assert first[1] == ANCHOR
        assert rest == []
        assert src.refused_epochs == 1
        # The channel status was read again before re-anchoring.
        assert fake_control.ensure_channel.call_count == 2

