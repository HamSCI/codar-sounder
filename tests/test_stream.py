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
