import codecs
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "transcript.py"
spec = importlib.util.spec_from_file_location("transcript", SCRIPT)
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "原稿.txt"
        self.output = self.root / "整理.txt"

    def docx(self, inner):
        source = self.root / "原稿.docx"
        xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:body>' + inner + '</w:body></w:document>')
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("word/document.xml", xml)
        return source

    def test_speaker_only_at_line_start(self):
        self.assertEqual(t.normalize("• Speaker 1 00:01\r\n提到 Speaker 2\n"),
                         "• 发言者 1 00:01\n提到 Speaker 2\n")

    def test_plain_english_without_speaker_is_valid(self):
        self.source.write_bytes(b"A lecture with no speaker labels.\r\n")
        result = t.extract(self.source, self.output)
        self.assertEqual(result["status"], "written")
        self.assertEqual(self.output.read_bytes(), b"A lecture with no speaker labels.\n")

    def test_utf8_bom_and_utf16_bom(self):
        for encoding in ("utf-8-sig", "utf-16"):
            self.assertEqual(t.decode_text("中文".encode(encoding)), "中文")

    def test_explicit_legacy_encoding(self):
        raw = "中文".encode("gb18030")
        with self.assertRaises(t.TranscriptError):
            t.decode_text(raw)
        self.assertEqual(t.decode_text(raw, "gb18030"), "中文")

    def test_utf32_requires_explicit_choice(self):
        with self.assertRaises(t.TranscriptError):
            t.decode_text("文字".encode("utf-32"))

    def test_empty_and_nul_rejected(self):
        for value in (" \n", "abc\x00"):
            with self.assertRaises(t.TranscriptError):
                t.normalize(value)

    def test_idempotent_and_no_overwrite(self):
        self.source.write_text("原文", encoding="utf-8")
        t.extract(self.source, self.output)
        self.assertEqual(t.extract(self.source, self.output)["status"], "unchanged")
        self.source.write_text("新版", encoding="utf-8")
        with self.assertRaises(t.TranscriptError):
            t.extract(self.source, self.output)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "原文\n")
        t.extract(self.source, self.output, overwrite=True)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "新版\n")

    def test_bad_input_does_not_change_existing_output(self):
        self.output.write_bytes(b"keep")
        self.source.write_bytes(b"\xff")
        with self.assertRaises(t.TranscriptError):
            t.extract(self.source, self.output, overwrite=True)
        self.assertEqual(self.output.read_bytes(), b"keep")

    def test_input_cannot_be_output(self):
        self.source.write_bytes(b"source")
        with self.assertRaises(t.TranscriptError):
            t.extract(self.source, self.source, overwrite=True)
        self.assertEqual(self.source.read_bytes(), b"source")

    def test_docx_table_order_tabs_and_breaks(self):
        source = self.docx('<w:p><w:r><w:t>Speaker 1</w:t><w:tab/><w:t>00:00</w:t>'
                           '<w:br/><w:t>第一段</w:t></w:r></w:p>'
                           '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                           '<w:p><w:r><w:t>末段</w:t></w:r></w:p>')
        t.extract(source, self.output)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "发言者 1\t00:00\n第一段\n表格\n末段\n")

    def test_unsupported_docx_structures_warn_without_duplicate_text(self):
        source = self.docx('<w:p><w:r><w:t>正文</w:t><w:drawing><w:txbxContent>'
                           '<w:p><w:r><w:t>框内</w:t></w:r></w:p></w:txbxContent></w:drawing>'
                           '<w:footnoteReference w:id="1"/></w:r></w:p>'
                           '<w:ins><w:p><w:r><w:t>修订</w:t></w:r></w:p></w:ins>')
        result = t.extract(source, self.output)
        self.assertTrue(result["warnings"])
        self.assertEqual(self.output.read_text(encoding="utf-8"), "正文\n")

    def test_corrupt_or_empty_docx_preserves_output(self):
        for data in (b"not a zip", b""):
            source = self.root / "bad.docx"
            source.write_bytes(data)
            self.output.write_bytes(b"keep")
            with self.assertRaises(t.TranscriptError):
                t.extract(source, self.output, overwrite=True)
            self.assertEqual(self.output.read_bytes(), b"keep")

    def test_math_is_not_silently_lost(self):
        source = self.docx('<w:p xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
                           '<w:r><w:t>Formula:</w:t></w:r>'
                           '<m:oMath><m:r><m:t>E=mc2</m:t></m:r></m:oMath></w:p>')
        result = t.extract(source, self.output)
        self.assertTrue(any("OMML" in item for item in result["warnings"]))
        self.assertEqual(self.output.read_text(encoding="utf-8"), "Formula:\n")

    def test_dtd_rejected_in_utf8_and_utf16(self):
        for encoding in ("utf-8", "utf-16"):
            source = self.root / "entity.docx"
            xml = ('<?xml version="1.0" encoding="' + encoding + '"?>'
                   '<!DOCTYPE x [<!ENTITY payload "injected">]>'
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   '<w:body><w:p><w:r><w:t>&payload;</w:t></w:r></w:p></w:body></w:document>')
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("word/document.xml", xml.encode(encoding))
            with self.assertRaises(t.TranscriptError):
                t.extract(source, self.output)
            self.assertFalse(self.output.exists())

    def test_deep_nesting_cli_has_explained_error(self):
        source = self.docx('<w:sdt>' * 1100 + '<w:p><w:r><w:t>deep</w:t></w:r></w:p>' + '</w:sdt>' * 1100)
        result = subprocess.run([sys.executable, str(SCRIPT), str(source), '--output', str(self.output)],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.output.exists())

    def test_large_xml_rejected(self):
        source = self.docx('<w:p><w:r><w:t>data</w:t></w:r></w:p>')
        with patch.object(t, "MAX_TEXT_BYTES", 10):
            with self.assertRaises(t.TranscriptError):
                t.extract(source, self.output)
        self.assertFalse(self.output.exists())

    def test_failed_publish_leaves_existing_content_and_cleans_temp(self):
        self.source.write_bytes(b"new")
        self.output.write_bytes(b"old")
        with patch.object(t.os, "replace", side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                t.extract(self.source, self.output, overwrite=True)
        self.assertEqual(self.output.read_bytes(), b"old")
        self.assertEqual(list(self.root.glob(".transcript-*")), [])

    def test_concurrent_writer_is_not_overwritten(self):
        def race(src, dst):
            Path(dst).write_bytes(b"someone else")
            raise FileExistsError()
        with patch.object(t, "publish_no_clobber", side_effect=race):
            with self.assertRaises(t.TranscriptError):
                t.atomic_write(self.output, b"ours")
        self.assertEqual(self.output.read_bytes(), b"someone else")
        self.assertFalse(list(self.root.glob(".transcript-*")))

    def test_cli_success_and_invalid_input(self):
        self.source.write_text("中文", encoding="utf-8")
        command = [sys.executable, str(SCRIPT), str(self.source), "--output", str(self.output)]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
        result = subprocess.run(command[:-3] + [str(self.root / "missing.txt"), "--output", str(self.output)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
