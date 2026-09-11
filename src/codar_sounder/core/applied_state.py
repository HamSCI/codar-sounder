"""The §3 ``timing_authority_applied`` report for one codar daemon.

CLIENT-CONTRACT §18.5 (amendment 2026-09-04): the field describes the
LABELS a client writes, not its reading habits.  One codar daemon serves
one radiod through one IQ source, and that source pins one
:class:`hamsci_dsp.timing.AnchorUTC` at stream start.  Every CPI label
projects from that anchor, so the daemon's report stands for exactly one
anchor.  The channel counts travel with the block anyway (total=1) so a
reader sees the same shape psk, meteor and wspr produce.

The daemon writes the result at most once a minute to
``<output_dir>/<radiod_id>/timing-authority.json``
(``hamsci_dsp.timing.write_applied_state``).  ``codar-sounder inventory
--json`` reads it back.  Both sides take the path from
:func:`applied_state_path`, so the rule lives here and nowhere else.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from hamsci_dsp.timing import applied_state_for_anchors

APPLIED_STATE_FILENAME = "timing-authority.json"


def applied_state_path(output_dir, radiod_id: str) -> Path:
    """Where the daemon for ``radiod_id`` leaves its applied block."""
    return Path(output_dir) / radiod_id / APPLIED_STATE_FILENAME


def applied_state_for(
    source,
    client_radiod: str,
    now_fn: Optional[Callable[[], float]] = None,
) -> Optional[dict]:
    """The §3 block for the daemon's single IQ source, or None.

    ``source.anchor`` holds the AnchorUTC the source pinned (None before
    the first CPI, and always None for the synthetic source).  The
    suite-shared rule in ``hamsci_dsp.timing.applied_state_for_anchors``
    populates the block only when that anchor carries the authority
    offset, and attaches ``channels: {total: 1, ...}``.
    """
    return applied_state_for_anchors(
        [getattr(source, "anchor", None)],
        client_radiod=client_radiod, now_fn=now_fn,
    )
