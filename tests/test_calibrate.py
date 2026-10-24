"""Synthetic calibration tests: no classroom recordings or personal material."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "calibrate.py"
SPEC = importlib.util.spec_from_file_location("calibrate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
calibrate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(calibrate)


def anchor(photo_time="2026-10-23T09:00:00", transcript_time=60, evidence="Unique diagram label A"):
    return {"photo_time": photo_time, "transcript_time": transcript_time, "evidence": evidence}


class AnalyzeTests(unittest.TestCase):
    def test_single_anchor_is_provisional(self):
        result = calibrate.analyze({"anchors": [anchor()]})
        self.assertEqual(result["status"], "provisional")
        self.assertEqual(result["candidate_recording_start"], "2026-10-23T08:59:00")
        self.assertEqual(result["max_residual_seconds"], 0)
        self.assertTrue(any("not actual timing uncertainty" in line for line in result["limitations"]))

    def test_multi_anchor_median_and_residuals(self):
        result = calibrate.analyze({"anchors": [
            anchor("2026-10-23T09:00:00", 60),
            anchor("2026-10-23T09:01:02", 120),
            anchor("2026-10-23T09:02:01", 180),
        ]}, tolerance=1)
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["candidate_recording_start"], "2026-10-23T08:59:01")
        self.assertEqual([item["residual_seconds"] for item in result["anchors"]], [-1, 1, 0])
        self.assertEqual(result["max_residual_seconds"], 1)

    def test_cross_midnight_and_uniform_timezone(self):
        result = calibrate.analyze({"anchors": [
            anchor("2026-10-24T00:00:30+08:00", "01:30"),
            anchor("2026-10-23T23:59:30+08:00", "00:30"),
        ]})
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["candidate_recording_start"], "2026-10-23T23:59:00+08:00")

    def test_zero_offsets_accept_z_and_explicit_utc(self):
        result = calibrate.analyze({"anchors": [
            anchor("2026-10-23T09:00:00Z", 0),
            anchor("2026-10-23T09:01:00+00:00", 60),
        ]})
        self.assertEqual(result["status"], "candidate")

    def test_long_duration_formats_and_fractional_seconds(self):
        for duration in ("125:30", "2:05:30", 7530):
            with self.subTest(duration=duration):
                result = calibrate.analyze({"anchors": [anchor(transcript_time=duration)]})
                self.assertEqual(result["candidate_recording_start"], "2026-10-23T06:54:30")
        self.assertEqual(calibrate.parse_duration("00:00.5"), 0.5)
        self.assertEqual(calibrate.parse_duration("1:00:00.25"), 3600.25)

    def test_reject_invalid_inputs(self):
        invalid_inputs = [
            None, [], {}, {"anchors": []}, {"anchors": {}}, {"anchors": [None]},
            {"anchors": [anchor(evidence=" \t")]},
            {"anchors": [anchor(evidence=42)]},
            {"anchors": [anchor(photo_time="09:00:00")]},
            {"anchors": [anchor(photo_time="2026-10-23")]},
            {"anchors": [anchor(photo_time="2026-02-30T09:00:00")]},
            {"anchors": [anchor(photo_time="2026-10-23T25:00:00")]},
            {"anchors": [anchor(photo_time="2026-10-23T09:00:00+08:60")]},
            {"anchors": [anchor(), anchor()]},
            {"anchors": [anchor(), anchor("2026-10-23T09:01:00+08:00")]},
            {"anchors": [anchor("2026-10-23T09:00:00Z"), anchor("2026-10-23T09:01:00+08:00")]},
        ]
        for data in invalid_inputs:
            with self.subTest(data=data), self.assertRaises(calibrate.ValidationError):
                calibrate.analyze(data)

    def test_reject_invalid_durations(self):
        for duration in (True, False, -1, float("inf"), float("nan"), "-1:00", "1:60", "1:60:00", "1:00:60", "3.5", None, [], "x:y", 1e100):
            with self.subTest(duration=duration), self.assertRaises(calibrate.ValidationError):
                calibrate.analyze({"anchors": [anchor(transcript_time=duration)]})

    def test_reject_invalid_tolerance(self):
        for tolerance in (True, -1, float("inf"), float("nan"), "120"):
            with self.subTest(tolerance=tolerance), self.assertRaises(calibrate.ValidationError):
                calibrate.analyze({"anchors": [anchor()]}, tolerance=tolerance)

    def test_extreme_duration_never_leaks_overflow(self):
        for duration in (10**1000, "9" * 500 + ":00", 1e300):
            with self.subTest(duration=str(duration)[:30]), self.assertRaises(calibrate.ValidationError):
                calibrate.analyze({"anchors": [anchor(transcript_time=duration)]})


class CLITests(unittest.TestCase):
    def run_cli(self, data, *arguments, raw=False):
        with tempfile.TemporaryDirectory() as directory:
            input_file = Path(directory) / "anchors.json"
            input_file.write_text(data if raw else json.dumps(data), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(input_file), *arguments],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
        self.assertNotIn("Traceback", result.stderr)
        return result

    def test_cli_cross_midnight_and_long_duration(self):
        result = self.run_cli({"anchors": [
            anchor("2026-10-24T00:01:00+08:00", "125:30"),
            anchor("2026-10-24T00:02:00+08:00", "2:06:30"),
        ]})
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["candidate_recording_start"], "2026-10-23T21:55:30+08:00")

    def test_cli_inconsistent_exit_one(self):
        result = self.run_cli({"anchors": [
            anchor("2026-10-23T09:00:00", 0),
            anchor("2026-10-23T09:10:00", 0),
        ]}, "--tolerance", "120")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["status"], "inconsistent")

    def test_cli_invalid_inputs_exit_two(self):
        for raw in ("{", '{"anchors":[{"photo_time":"09:00:00"}]}', '{"anchors": NaN}', '[]', '{"anchors":[]}'):
            with self.subTest(raw=raw):
                result = self.run_cli(raw, raw=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["status"], "invalid")

    def test_cli_rejects_boolean_duration_and_nonfinite_tolerance(self):
        for data, arguments in (({"anchors": [anchor(transcript_time=True)]}, ()), ({"anchors": [anchor()]}, ("--tolerance", "nan"))):
            result = self.run_cli(data, *arguments)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["status"], "invalid")

    def test_cli_preserves_unicode_evidence(self):
        result = self.run_cli({"anchors": [anchor(evidence="独特公式 A 与逐字稿一致")]})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["anchors"][0]["evidence"], "独特公式 A 与逐字稿一致")

    def test_cli_invalid_json_numeric_range_does_not_traceback(self):
        result = self.run_cli('{"anchors":' + '9' * 5000 + '}', raw=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "invalid")

    def test_cli_missing_file_exit_two(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(Path(directory) / "missing.json")],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "invalid")
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
