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
