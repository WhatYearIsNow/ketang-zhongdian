import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_release.py"
spec = importlib.util.spec_from_file_location("release", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "source with spaces"
        self.root.mkdir()
        self.manifest = {
            "name": "ketang-zhongdian",
            "version": "0.2.1",
            "files": ["SKILL.md", "README.md", "LICENSE", "release-manifest.json"],
        }
        for name in self.manifest["files"]:
            (self.root / name).write_text("合成测试", encoding="utf-8")
        self.save()
        self.output = self.base / "release.zip"

    def save(self):
        (self.root / "release-manifest.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )

    def test_reproducible_and_checksummed(self):
        first, count = release.payload(self.root)
        (self.root / "SKILL.md").touch()
        second, _ = release.payload(self.root)
        self.assertEqual(first, second)
        self.assertEqual(count, 5)
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            self.assertIsNone(archive.testzip())
            sums = archive.read("ketang-zhongdian/MANIFEST.sha256").decode()
            for line in sums.splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(
                    digest, hashlib.sha256(archive.read("ketang-zhongdian/" + name)).hexdigest()
                )

    def test_unlisted_private_inputs_never_published(self):
        (self.root / "private-recording.m4a").write_bytes(b"private")
        data, _ = release.payload(self.root)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertFalse(any("private" in name for name in archive.namelist()))

    def test_unsafe_paths_and_collisions(self):
        for path in (
            "../secret",
            "/secret",
            "C:/secret",
            "a\\b",
            "a/./b",
            "README.md",
            "readme.md",
        ):
            with self.subTest(path=path):
                self.manifest["files"] = [
                    "SKILL.md",
                    "README.md",
                    "LICENSE",
                    "release-manifest.json",
                    path,
                ]
                self.save()
                with self.assertRaises(ValueError):
                    release.payload(self.root)

    def test_missing_file_does_not_touch_existing_release(self):
        (self.root / "LICENSE").unlink()
        self.output.write_bytes(b"keep old archive")
        with self.assertRaises(ValueError):
            release.build(self.root, self.output, overwrite=True)
        self.assertEqual(self.output.read_bytes(), b"keep old archive")

    def test_overwrite_requires_explicit_flag_and_is_idempotent(self):
        self.assertEqual(release.build(self.root, self.output)["status"], "written")
        self.assertEqual(release.build(self.root, self.output)["status"], "unchanged")
        (self.root / "SKILL.md").write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError):
            release.build(self.root, self.output)
        self.assertEqual(release.build(self.root, self.output, True)["status"], "written")

    def test_output_cannot_replace_source(self):
        with self.assertRaises(ValueError):
            release.build(self.root, self.root / "SKILL.md", True)
        self.assertEqual((self.root / "SKILL.md").read_text(encoding="utf-8"), "合成测试")

    def test_cli_reports_success_and_invalid_manifest(self):
        command = [
            sys.executable,
            str(SCRIPT),
            '--source',
            str(self.root),
            '--output',
            str(self.output),
        ]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'written')
        (self.root / 'release-manifest.json').write_text('invalid', encoding='utf-8')
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['status'], 'invalid')
        self.assertNotIn('Traceback', result.stderr)


if __name__ == "__main__":
    unittest.main()
