"""Filesystem-based tests for the read-only classroom-note link checker."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_links.py"
SPEC = importlib.util.spec_from_file_location("classroom_check_links", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINKS)


class LinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("KETANG_TEST_TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.note = self.make("课程/课堂重点/10-23-笔记.md", "# Test\n")

    def make(self, path: str, content: str = "fixture") -> Path:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def check(self, text: str, *, root: Path | None = None) -> dict:
        self.note.write_text(text, encoding="utf-8")
        return LINKS.check_note(self.note, root or self.root)

    def kinds(self, report: dict) -> list[str]:
        return [item["kind"] for item in report["issues"]]

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8"
        )

    def test_inline_links_and_images(self) -> None:
        self.make("课程/课程录音/1023.txt")
        self.make("课程/课堂重点/images/10-23/090000.jpg")
        report = self.check("[转写稿](../课程录音/1023.txt)\n![照片](images/10-23/090000.jpg)")
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checked"], 2)

    def test_percent_decoding_spaces_unicode_and_literal_hash(self) -> None:
        self.make("课程/课程录音/1023 课名#1.txt")
        report = self.check("[转写稿](../课程录音/1023%20课名%231.txt#section)")
        self.assertTrue(report["ok"], report)

    def test_markdown_entity_destination(self) -> None:
        self.make("课程/课堂重点/a&b.txt")
        self.assertTrue(self.check("[文件](a&amp;b.txt)")["ok"])

    def test_raw_spaces_and_balanced_parentheses(self) -> None:
        self.make("课程/课堂重点/a (b(c)).txt")
        self.assertTrue(self.check("[test](a (b(c)).txt)")["ok"])

    def test_escaped_parentheses_and_bracket_labels(self) -> None:
        self.make("课程/课堂重点/a(b).txt")
        report = self.check(r"[outer [inner] label](a\(b\).txt)")
        self.assertTrue(report["ok"], report)

    def test_angle_destination_with_special_characters_and_title(self) -> None:
        self.make("课程/课堂重点/a b(c).txt")
        report = self.check('[文件](<a b(c).txt> "A title")')
        self.assertTrue(report["ok"], report)

    def test_quoted_and_parenthesized_titles(self) -> None:
        self.make("课程/课堂重点/a.txt")
        for title in ['"title (with parentheses)"', "'title'", "(title)"]:
            with self.subTest(title=title):
                self.assertTrue(self.check(f"[test](a.txt {title})")["ok"])

    def test_reference_full_collapsed_shortcut_case_and_whitespace(self) -> None:
        self.make("课程/课堂重点/a b.txt")
        report = self.check(
            '[visible][FILE ID]\n[file   id][]\n[file id]\n\n[file id]: <a b.txt> "title"'
        )
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checked"], 3)

    def test_reference_image_and_first_definition_wins(self) -> None:
        self.make("课程/课堂重点/image.jpg")
        report = self.check("![caption][pic]\n[pic]: image.jpg\n[pic]: missing.jpg")
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checked"], 1)

    def test_undefined_explicit_and_collapsed_references(self) -> None:
        report = self.check("[visible][missing]\n![photo][]")
        self.assertEqual(self.kinds(report), ["undefined_reference", "undefined_reference"])
        self.assertEqual([item["line"] for item in report["issues"]], [1, 2])

    def test_undefined_shortcut_is_plain_text_and_obsidian_callout(self) -> None:
        report = self.check("> [!note] 教材补充\n普通 [文本]。")
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checked"], 0)

    def test_wikilink_unique_extension_inference_and_embed(self) -> None:
        self.make("资料/唯一笔记.md")
        self.make("资料/photo.jpg")
        report = self.check("[[唯一笔记#标题|别名]]\n![[photo.jpg]]")
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checked"], 2)

    def test_wikilink_path_is_relative_to_root(self) -> None:
        self.make("资料/笔记.md")
        self.assertTrue(self.check("[[资料/笔记#heading]]")["ok"])

    def test_duplicate_wikilink_name_is_ambiguous(self) -> None:
        self.make("甲/同名.md")
        self.make("乙/同名.md")
        report = self.check("[[同名]]")
        self.assertEqual(self.kinds(report), ["ambiguous"])

    def test_wikilink_missing_is_reported(self) -> None:
        self.assertEqual(self.kinds(self.check("[[不存在]]")), ["missing"])

    def test_audio_over_an_hour_and_anchor_only(self) -> None:
        self.make("课程/课程录音/1023 课名.m4a")
        report = self.check(
            "[[1023 课名.m4a#t=01:07:07|1:07:07–1:10:00]]\n[section](#not-validated)"
        )
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checked"], 2)

    def test_external_urls_skipped_without_network(self) -> None:
        report = self.check(
            "[one](https://example.invalid/x)\n[mail](mailto:x@example.invalid)\n[cdn](//example.invalid/x)\n<https://example.invalid>\n<x@example.invalid>"
        )
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checked"], 0)
        self.assertEqual(report["skipped"], 5)

    def test_fenced_inline_code_and_comments_are_ignored(self) -> None:
        report = self.check(
            "```markdown\n[bad](missing.txt)\n```\n~~~\n[[missing]]\n~~~\n`[bad](missing)` and ``![[missing]]``\n<!-- [bad](missing)\n[[missing]] -->"
        )
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checked"], 0)

    def test_fence_closer_must_match_character_and_length(self) -> None:
        report = self.check("````\n```\n[[missing]]\n~~~~\n````\n[bad](real-missing.txt)")
        self.assertEqual(report["checked"], 1)
        self.assertEqual(report["issues"][0]["line"], 6)

    def test_multiline_inline_code_is_ignored_within_a_paragraph(self) -> None:
        report = self.check("inline `code\n[bad](missing)\nends here`")
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checked"], 0)

    def test_unmatched_inline_backtick_does_not_mask_next_paragraph(self) -> None:
        report = self.check("unclosed `code\n\n[bad](missing)\n\nclosing ` later")
        self.assertEqual(self.kinds(report), ["missing"])

    def test_comment_opener_inside_inline_code_does_not_hide_later_link(self) -> None:
        report = self.check("`<!--`\n[broken](missing.md)\n")
        self.assertEqual(report["checked"], 1)
        self.assertEqual(self.kinds(report), ["missing"])
        self.assertEqual(report["issues"][0]["line"], 2)

    def test_comment_markers_inside_multiline_code_are_literal(self) -> None:
        report = self.check("`<!--\n[ignored](inside-code.md)\n-->` [broken](missing.md)")
        self.assertEqual(report["checked"], 1)
        self.assertEqual(self.kinds(report), ["missing"])
        self.assertEqual(report["issues"][0]["line"], 3)

    def test_backticks_inside_comments_do_not_hide_later_link(self) -> None:
        for text in [
            "<!-- ` --> [broken](missing.md)\n`",
            "<!--\n` [ignored](inside-comment.md)\n-->\n[broken](missing.md)\n`",
            "<!--\n```\n-->\n[broken](missing.md)\n",
        ]:
            with self.subTest(text=text):
                report = self.check(text)
                self.assertEqual(report["checked"], 1)
                self.assertEqual(self.kinds(report), ["missing"])

    def test_comment_openers_inside_fences_do_not_escape_the_fence(self) -> None:
        for opening in ["```", "~~~", "```html <!--"]:
            with self.subTest(opening=opening):
                closing = "~~~" if opening == "~~~" else "```"
                report = self.check(
                    f"{opening}\n<!--\n[ignored](inside-fence.md)\n{closing}\n[broken](missing.md)"
                )
                self.assertEqual(report["checked"], 1)
                self.assertEqual(self.kinds(report), ["missing"])

    def test_inline_code_does_not_consume_a_tick_inside_a_later_fence(self) -> None:
        report = self.check("unclosed `\n```text\nclosing `\n```\n[broken](missing.md)")
        self.assertEqual(report["checked"], 1)
        self.assertEqual(self.kinds(report), ["missing"])

    def test_escape_sequence_does_not_create_a_link(self) -> None:
        self.assertEqual(self.check(r"\[bad](missing)")["checked"], 0)

    def test_missing_file_includes_line_number(self) -> None:
        report = self.check("# Title\n\n[bad](missing.md)")
        self.assertEqual(self.kinds(report), ["missing"])
        self.assertEqual(report["issues"][0]["line"], 3)

    def test_parent_path_inside_root_is_legal(self) -> None:
        self.make("课程/shared.txt")
        self.assertTrue(self.check("[shared](../shared.txt)")["ok"])

    def test_parent_path_and_percent_encoded_traversal_outside_root_fail(self) -> None:
        for target in ["../../../outside.txt", "%2E%2E/%2E%2E/%2E%2E/outside.txt"]:
            with self.subTest(target=target):
                self.assertEqual(self.kinds(self.check(f"[bad]({target})")), ["outside_root"])

    def test_default_root_is_note_parent(self) -> None:
        self.note.write_text("[bad](../outside.md)", encoding="utf-8")
        self.assertEqual(self.kinds(LINKS.check_note(self.note)), ["outside_root"])

    def test_directory_destination_is_not_a_file(self) -> None:
        self.assertEqual(self.kinds(self.check("[directory](.)")), ["unsupported"])

    def test_symbolic_link_cannot_escape_root(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("KETANG_TEST_TMPDIR")) as outside:
            outside_file = Path(outside) / "private.txt"
            outside_file.write_text("private", encoding="utf-8")
            link = self.note.parent / "escape.txt"
            try:
                link.symlink_to(outside_file)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable on this host")
            self.assertEqual(self.kinds(self.check("[bad](escape.txt)")), ["outside_root"])

    def test_malformed_percent_query_and_file_url_report_unsupported(self) -> None:
        for target in [
            "bad%ZZ.md",
            "file.md?download=1",
            "file:///tmp/x",
            "C:/data/file.txt",
            "%FF.txt",
        ]:
            with self.subTest(target=target):
                self.assertEqual(self.kinds(self.check(f"[bad]({target})")), ["unsupported"])

    def test_malformed_and_multiline_links_report_unsupported(self) -> None:
        for text in [
            "[bad](missing",
            "[bad](<missing)",
            "[bad](<x> junk)",
            "[[bad",
            "[bad\n](missing)",
            "[bad]:\n  missing.md",
            "[bad]: a(b",
        ]:
            with self.subTest(text=text):
                report = self.check(text)
                self.assertIn("unsupported", self.kinds(report))
                self.assertFalse(report["ok"])

    def test_local_html_links_are_explicitly_unsupported(self) -> None:
        report = self.check('<a href="local.md">link</a>\n<img src="photo.jpg">')
        self.assertEqual(self.kinds(report), ["unsupported", "unsupported"])

    def test_external_html_link_is_skipped(self) -> None:
        report = self.check('<a href="https://example.invalid">link</a>')
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["skipped"], 1)

    def test_nested_image_in_link_checks_both_targets(self) -> None:
        self.make("课程/课堂重点/image.jpg")
        self.make("课程/课堂重点/target.md")
        report = self.check("[![caption](image.jpg)](target.md)")
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checked"], 2)

    def test_bom_input_and_no_mutation(self) -> None:
        self.note.write_bytes(b"\xef\xbb\xbf# Test\n[anchor](#test)\n")
        before = self.note.read_bytes()
        self.assertTrue(LINKS.check_note(self.note, self.root)["ok"])
        self.assertEqual(before, self.note.read_bytes())

    def test_invalid_inputs_raise_value_error(self) -> None:
        for note, root in [
            (self.root / "missing.md", self.root),
            (self.root, self.root),
            (self.note, self.note),
            (self.note, self.root / "missing"),
        ]:
            with self.subTest(note=note, root=root):
                with self.assertRaises(ValueError):
                    LINKS.check_note(note, root)

    def test_note_outside_root_and_non_utf8_fail_as_input(self) -> None:
        (self.root / "other").mkdir()
        with self.assertRaises(ValueError):
            LINKS.check_note(self.note, self.root / "other")
        self.note.write_bytes(b"\xff\xfeinvalid")
        with self.assertRaises(ValueError):
            LINKS.check_note(self.note, self.root)

    def test_cli_success_json_schema_and_anchor_notice(self) -> None:
        self.note.write_text("[anchor](#heading)", encoding="utf-8")
        result = self.cli(str(self.note), "--root", str(self.root), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(set(report), {"ok", "checked", "skipped", "issues"})
        self.assertTrue(report["ok"])
        self.assertIn("not validated", result.stderr)

    def test_cli_link_issue_exit_one_and_input_exit_two(self) -> None:
        self.note.write_text("[bad](missing.md)", encoding="utf-8")
        result = self.cli(str(self.note), "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["issues"][0]["kind"], "missing")
        result = self.cli(str(self.note.parent / "missing.md"), "--json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["issues"][0]["kind"], "input")


if __name__ == "__main__":
    unittest.main()
