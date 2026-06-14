#!/usr/bin/env python3
"""Unit tests for codar-sounder's AuthorityReader + the canonical
timing-provenance block (shared method across all sigmond clients)."""

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from codar_sounder.core.authority_reader import (
    AuthorityReader,
    AuthoritySnapshot,
    standalone_timing_authority,
)


def _good(**overrides) -> dict:
    base = {
        "schema": "v1",
        "utc_published": "2026-04-23T12:00:00.000000Z",
        "a_level": "A1",
        "t_level_active": "T6",
        "t_level_available": ["T6", "T5"],
        "t_level_witnesses": ["T5"],
        "rtp_to_utc_offset_ns": 4250,
        "sigma_ns": 1000,
        "stations_contributing": [],
        "last_transition_utc": None,
        "disagreement_flags": ["TIMING_DISAGREEMENT"],
        "governor_radiod": "bee3-rx888",
    }
    base.update(overrides)
    return base


class TestCanonicalTimingAuthority(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "authority.json"
        self.now = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _snap(self, **overrides) -> AuthoritySnapshot:
        with self.path.open("w") as f:
            json.dump(_good(**overrides), f)
        s = AuthorityReader(path=self.path, now_fn=lambda: self.now).read()
        assert s is not None
        return s

    def test_to_timing_authority_block(self) -> None:
        b = self._snap().to_timing_authority(client_radiod="bee3-rx888")
        self.assertEqual(b["source"], "hf-timestd-authority")
        self.assertEqual(b["schema"], "v1")
        self.assertEqual(b["t_level_active"], "T6")
        self.assertEqual(b["rtp_to_utc_offset_ns"], 4250)
        self.assertEqual(b["sigma_ns"], 1000)
        self.assertEqual(b["t_level_witnesses"], ["T5"])
        self.assertEqual(b["disagreement_flags"], ["TIMING_DISAGREEMENT"])
        self.assertEqual(b["governor_radiod"], "bee3-rx888")
        self.assertEqual(b["client_radiod"], "bee3-rx888")

    def test_standalone_block(self) -> None:
        b = standalone_timing_authority(client_radiod="bee3-rx888")
        self.assertEqual(b["source"], "standalone-fallback")
        self.assertIsNone(b["t_level_active"])
        self.assertIsNone(b["rtp_to_utc_offset_ns"])
        self.assertEqual(b["disagreement_flags"], [])
        self.assertEqual(b["client_radiod"], "bee3-rx888")

    def test_both_blocks_share_keys(self) -> None:
        self.assertEqual(
            set(self._snap().to_timing_authority("r").keys()),
            set(standalone_timing_authority("r").keys()),
        )


if __name__ == "__main__":
    unittest.main()
