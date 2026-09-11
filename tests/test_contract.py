"""Sigmond client contract v0.5 conformance tests.

Verifies:
  §3   inventory --json shape
  §4   stdout cleanliness (inventory, validate, version)
  §12  validate --json shape
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEST_CONFIG = REPO / "tests" / "fixtures" / "test-config.toml"


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run codar-sounder CLI as a subprocess and capture stdout/stderr."""
    cmd = [sys.executable, "-m", "codar_sounder.cli", *args,
           "--config", str(TEST_CONFIG)]
    env = {"PYTHONPATH": str(REPO / "src"),
           "PATH": "/usr/bin:/bin",
           "HOME": "/tmp"}
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=10, env=env,
    )


# ---------------------------------------------------------------------------
# §4 stdout cleanliness — JSON only, no banners or log lines
# ---------------------------------------------------------------------------

class TestStdoutCleanliness:

    def test_inventory_stdout_is_valid_json(self):
        proc = _run_cli("inventory", "--json")
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        data = json.loads(proc.stdout)
        assert isinstance(data, dict)

    def test_inventory_stdout_starts_with_brace(self):
        proc = _run_cli("inventory", "--json")
        stripped = proc.stdout.strip()
        assert stripped.startswith("{"), f"stdout: {stripped[:80]!r}"

    def test_validate_stdout_is_valid_json(self):
        proc = _run_cli("validate", "--json")
        # validate may return 1 if config has issues; that's OK as long as
        # the JSON is well-formed.
        data = json.loads(proc.stdout)
        assert isinstance(data, dict)

    def test_version_stdout_is_valid_json(self):
        proc = _run_cli("version", "--json")
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        data = json.loads(proc.stdout)
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# §3 inventory schema
# ---------------------------------------------------------------------------

class TestInventory:

    @classmethod
    def setup_class(cls):
        proc = _run_cli("inventory", "--json")
        cls.data = json.loads(proc.stdout)

    def test_client_name(self):
        assert self.data["client"] == "codar-sounder"

    def test_contract_version(self):
        assert self.data["contract_version"] == "0.8"

    def test_timing_authority_applied_null_when_no_daemon_runs(self):
        """CONTRACT §3/§18.5: the field describes the labels the RUNNING
        daemon writes.  No daemon has left an applied-state file for this
        test radiod, so every instance reports an explicit None (present,
        not missing: a pre-v0.7 client would omit the key)."""
        for inst in self.data["instances"]:
            assert "timing_authority_applied" in inst, (
                f"instance {inst.get('instance')} missing timing_authority_applied"
            )
            assert inst["timing_authority_applied"] is None

    def test_has_config_path(self):
        assert "config_path" in self.data

    def test_has_deploy_toml_path(self):
        assert "deploy_toml_path" in self.data

    def test_has_instances(self):
        assert "instances" in self.data
        assert isinstance(self.data["instances"], list)
        # test-config.toml declares 2 transmitters → 2 instances
        assert len(self.data["instances"]) == 2

    def test_instance_fields(self):
        inst = self.data["instances"][0]
        for required in ("instance", "radiod_id", "host", "frequencies_hz",
                         "ka9q_channels", "data_sinks"):
            assert required in inst, f"missing {required}"

    def test_data_sinks_v0_6(self):
        """CONTRACT v0.6 §17.3: each instance has a data_sinks array.

        Only `file` sinks are declared; ClickHouse is removed suite-wide
        so it must never appear as a sink kind.
        """
        for inst in self.data["instances"]:
            sinks = inst["data_sinks"]
            assert isinstance(sinks, list) and sinks
            kinds = {s["kind"] for s in sinks}
            assert "file" in kinds
            # ClickHouse is removed suite-wide; never a valid sink kind.
            assert "clickhouse" not in kinds
            assert kinds <= {"file"}
            for sink in sinks:
                for key in ("kind", "target", "retention_days", "mb_per_day"):
                    assert key in sink, f"sink missing {key}"

    def test_instance_ids_are_station_codes(self):
        ids = {inst["instance"] for inst in self.data["instances"]}
        assert ids == {"DUCK", "LISL"}

    def test_frequencies_match_config(self):
        for inst in self.data["instances"]:
            assert len(inst["frequencies_hz"]) == 1
            assert inst["frequencies_hz"][0] in (4537180, 4575000)

    def test_log_paths_present(self):
        assert "log_paths" in self.data

    def test_log_level_present(self):
        assert "log_level" in self.data

    def test_deps_present(self):
        assert "deps" in self.data
        assert "pypi" in self.data["deps"]

    def test_issues_is_list(self):
        assert isinstance(self.data["issues"], list)


# ---------------------------------------------------------------------------
# §12 validate
# ---------------------------------------------------------------------------

class TestValidate:

    def test_valid_config_returns_ok(self):
        proc = _run_cli("validate", "--json")
        data = json.loads(proc.stdout)
        assert data["ok"] is True, f"issues: {data.get('issues', [])}"
        assert "config_path" in data
        assert isinstance(data["issues"], list)

    def test_validate_catches_missing_receiver_location(self, tmp_path):
        """A config without receiver_lat must fail validation."""
        bad = tmp_path / "bad.toml"
        bad.write_text("""
[station]
callsign = "TEST"
[[radiod]]
status = "test.local"
channel_name = "codar-4mhz"
[[radiod.transmitter]]
id = "DUCK"
center_freq_hz = 4537180
sweep_rate_hz_per_s = -25733.913
sweep_bw_hz = 25734
sweep_repetition_hz = 1
tx_lat_deg = 36.18
tx_lon_deg = -75.75
""")
        cmd = [sys.executable, "-m", "codar_sounder.cli",
               "validate", "--json", "--config", str(bad)]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10,
            env={"PYTHONPATH": str(REPO / "src")},
        )
        data = json.loads(proc.stdout)
        assert data["ok"] is False
        assert any(
            "receiver_lat" in issue["message"]
            for issue in data["issues"]
        )


# ---------------------------------------------------------------------------
# Station table sanity
# ---------------------------------------------------------------------------

class TestStationTable:

    @classmethod
    def setup_class(cls):
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib
        with open(REPO / "data" / "codar-stations.toml", "rb") as f:
            cls.data = tomllib.load(f)

    def test_has_stations(self):
        assert "stations" in self.data
        assert len(self.data["stations"]) >= 50, \
            f"expected >=50 stations, got {len(self.data['stations'])}"

    def test_DUCK_matches_kaeppler_paper(self):
        """The DUCK transmitter's parameters should match Kaeppler 2022."""
        d = self.data["stations"]["DUCK"]
        assert d["freq_mhz"] == 4.53718
        assert d["sweep_rate_hz_per_s"] == -25733.913
        assert d["sweep_bw_hz"] == 25734
        assert d["sweep_repetition_hz"] == 1

    def test_all_stations_have_required_fields(self):
        required = ("freq_mhz", "sweep_rate_hz_per_s", "sweep_bw_hz",
                    "sweep_repetition_hz", "tx_lat_deg", "tx_lon_deg")
        for sid, station in self.data["stations"].items():
            for field in required:
                assert field in station, f"{sid} missing {field}"

    def test_frequencies_in_codar_bands(self):
        """All freqs should fall in the standard CODAR HF/VHF bands."""
        bands = [(4.0, 5.5), (8.0, 14.0), (12.0, 14.0), (16.0, 17.0),
                 (24.0, 27.5), (39.0, 50.0)]
        for sid, station in self.data["stations"].items():
            f = station["freq_mhz"]
            in_band = any(lo <= f <= hi for lo, hi in bands)
            assert in_band, f"{sid}: freq {f} MHz outside CODAR bands"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ---------------------------------------------------------------------------
# §18.5 — timing_authority_applied comes from the running daemon's file
# ---------------------------------------------------------------------------

class TestTimingAuthorityAppliedFromRuntimeState:
    """The daemon leaves its applied block at
    <output_dir>/<radiod_id>/timing-authority.json (one rule,
    core/applied_state.py); inventory, a separate process, reports it while
    fresh and None otherwise.  Every tx instance of the radiod shares it."""

    @staticmethod
    def _state_path() -> Path:
        from codar_sounder.core.applied_state import applied_state_path
        return applied_state_path("/tmp/codar-sounder-test/output", "test-status.local")

    def teardown_method(self):
        self._state_path().unlink(missing_ok=True)

    def _instances(self) -> list[dict]:
        proc = _run_cli("inventory", "--json")
        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        return json.loads(proc.stdout)["instances"]

    def test_fresh_state_file_is_reported_on_every_tx_instance(self):
        from hamsci_dsp.timing import write_applied_state
        block = {"source": "hf-timestd@gov", "tier": "T6", "sigma_ns": 4210,
                 "snapshot_age_s": 1.0, "radiod_id": "test-status.local",
                 "channels": {"total": 1, "anchored": 1, "applied": 1}}
        write_applied_state(self._state_path(), block)
        insts = self._instances()
        assert len(insts) == 2
        for inst in insts:
            assert inst["timing_authority_applied"] == block

    def test_stale_state_file_reports_null(self):
        import time
        from hamsci_dsp.timing import write_applied_state
        write_applied_state(self._state_path(), {"tier": "T6"},
                            now_fn=lambda: time.time() - 3600)
        for inst in self._instances():
            assert inst["timing_authority_applied"] is None

    def test_capability_is_declared(self):
        """§3: uses_timing_calibration names the capability.  codar anchors
        through acquire_anchor_utc, which applies the authority whenever
        hf-timestd publishes one."""
        for inst in self._instances():
            assert inst["uses_timing_calibration"] is True
