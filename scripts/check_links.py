#!/usr/bin/env python3
"""Read-only local-link checker for classroom notes (Python 3.10+).

Usage: python scripts/check_links.py NOTE [--root VAULT] [--json]

Supported Markdown is intentionally finite: single-line inline links/images,
single-line reference definitions and full/collapsed/defined shortcut references,
balanced destination parentheses, angle destinations, and optional quoted or
parenthesized titles. Raw spaces in destinations are accepted for Obsidian.
Obsidian [[path#anchor|alias]] links and embeds are supported. Bare wikilink
filenames must be unique within the vault; path-qualified wikilinks are relative
to the vault root. Markdown paths are relative to the note. Missing file
extensions are inferred as .md only for wikilinks. Fenced and inline code and
HTML comments are ignored. Anchors, including audio times, are NEVER validated.

This is not a complete CommonMark parser. Wrapped link labels/destinations,
multiline definitions, nested Markdown links, and local HTML href/src links are
reported as unsupported when encountered. Use fenced code blocks: indentation
alone does not suppress link checks. Undefined shortcut [text] is plain text
(CommonMark behavior); explicit [text][missing] is an error. Non-link Markdown,
footnotes, and template languages are not interpreted. The checker does not
fetch URLs, validate source fidelity, inspect images, or modify any file.

Exit status: 0 = all supported local links resolve; 1 = link issues;
2 = bad input or unreadable note/root. JSON is emitted only on stdout.
"""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import string
import sys
from urllib.parse import unquote


_PUNCTUATION = frozenset(string.punctuation)
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HTML_LINK = re.compile(r"<(a|img)\b", re.IGNORECASE)
_HTML_TARGET = re.compile(
    r"\b(?:href|src)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.IGNORECASE
)


def _unescape(value: str) -> str:
    result: list[str] = []
    pos = 0
    while pos < len(value):
        if value[pos] == "\\" and pos + 1 < len(value) and value[pos + 1] in _PUNCTUATION:
            pos += 1
        result.append(value[pos])
        pos += 1
    return "".join(result)


def _reference(value: str) -> str:
    return " ".join(_unescape(value).split()).casefold()


def _closing_bracket(text: str, start: int) -> int | None:
    depth = 1
    pos = start + 1
    while pos < len(text):
        char = text[pos]
        if char == "\\":
            pos += 2
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return pos
        pos += 1
    return None


def _title_suffix(text: str) -> str:
    """Remove a conventional optional title, leaving raw-space paths intact."""
    for pos, char in enumerate(text):
        if pos == 0 or not text[pos - 1].isspace() or char not in "\"'(":
            continue
        tail = text[pos:].strip()
        if char in "\"'" and len(tail) >= 2 and tail[-1] == char:
            escaped = False
            valid = True
            for inner in tail[1:-1]:
                if inner == char and not escaped:
                    valid = False
                    break
                escaped = inner == "\\" and not escaped
            if valid:
                return text[:pos].rstrip()
        if char == "(" and tail.endswith(")"):
            depth = 0
            valid = True
            for index, inner in enumerate(tail):
                if inner == "(":
                    depth += 1
                elif inner == ")":
                    depth -= 1
                    if depth == 0 and index != len(tail) - 1:
                        valid = False
                        break
            if valid and depth == 0:
                return text[:pos].rstrip()
    return text


def _destination(text: str) -> str:
    value = text.strip()
    if not value:
        return ""  # An empty Markdown destination refers to the current note.
    if value.startswith("<"):
        pos = 1
        while pos < len(value):
            if value[pos] == "\\":
                pos += 2
                continue
            if value[pos] == ">":
                suffix = value[pos + 1 :].strip()
                if suffix and _title_suffix("x " + suffix) != "x":
                    raise ValueError("Unexpected content after an angle destination")
                return html.unescape(_unescape(value[1:pos]))
            if value[pos] == "<":
                raise ValueError("Nested angle destinations are unsupported")
            pos += 1
        raise ValueError("Unclosed angle destination; wrapped destinations are unsupported")
    value = _title_suffix(value)
    if "<" in value or ">" in value or '"' in value:
        raise ValueError("Malformed destination/title; use <path> for special characters")
    depth = 0
    pos = 0
    while pos < len(value):
        if value[pos] == "\\":
            pos += 2
            continue
        if value[pos] == "(":
            depth += 1
        elif value[pos] == ")":
            depth -= 1
            if depth < 0:
                raise ValueError(
                    "Unbalanced destination parentheses; use <path> or percent escapes"
                )
        pos += 1
    if depth:
        raise ValueError("Unbalanced destination parentheses; use <path> or percent escapes")
    return html.unescape(_unescape(value))


def _inline_destination(text: str, start: int) -> tuple[str, int]:
    depth = 1
    angle = False
    quote: str | None = None
    pos = start + 1
    first = True
    while pos < len(text):
        char = text[pos]
        if char == "\\":
            pos += 2
            continue
        if angle:
            if char == ">":
                angle = False
        elif quote:
            if char == quote:
                quote = None
        elif first and char == "<":
            angle = True
        elif char in "\"'" and pos > start + 1 and text[pos - 1].isspace():
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return _destination(text[start + 1 : pos]), pos + 1
        if not char.isspace():
            first = False
        pos += 1
    raise ValueError("Unclosed destination; multiline links are unsupported")


def _visible_lines(text: str) -> list[str]:
    """Lex ignored regions in source order, retaining original line numbers.

    A comment marker inside a code span is literal text, and backticks inside
    comments or fences cannot start a span. Independent masking passes violate
    these rules and can accidentally hide real links after an inline example.
    """
    masked = list(text)

    def mask(start: int, end: int) -> None:
        for index in range(start, end):
            if text[index] not in "\r\n":
                masked[index] = " "

    def line_end(start: int) -> int:
        end = text.find("\n", start)
        return len(text) if end < 0 else end + 1

    def opening_fence(start: int) -> re.Match[str] | None:
        match = _FENCE.match(text[start : line_end(start)].rstrip("\r\n"))
        if match and not (match[1][0] == "`" and "`" in match[2]):
            return match
        return None

    pos = 0
    while pos < len(text):
        if pos == 0 or text[pos - 1] == "\n":
            fence = opening_fence(pos)
            if fence:
                end = line_end(pos)
                while end < len(text):
                    stop = line_end(end)
                    closer = _FENCE.match(text[end:stop].rstrip("\r\n"))
                    if (
                        closer
                        and closer[1][0] == fence[1][0]
                        and len(closer[1]) >= len(fence[1])
                        and not closer[2].strip()
                    ):
                        end = stop
                        break
                    end = stop
                mask(pos, end)
                pos = end
                continue
        if text.startswith("<!--", pos):
            end = text.find("-->", pos + 4)
            end = len(text) if end < 0 else end + 3
            mask(pos, end)
            pos = end
            continue
        if text[pos] == "\\":
            pos += 2
            continue
        if text[pos] != "`":
            pos += 1
            continue
        stop = pos + 1
        while stop < len(text) and text[stop] == "`":
            stop += 1
        width = stop - pos
        search = stop
        boundary_match = re.search(r"\n[ \t\r]*\n", text[stop:])
        boundary = stop + boundary_match.start() if boundary_match else len(text)
        # A later fenced block starts a new block, even if an earlier inline
        # backtick was unmatched. Do not consume a closing tick inside it.
        next_line = line_end(pos)
        while next_line < boundary:
            if opening_fence(next_line):
                boundary = next_line
                break
            next_line = line_end(next_line)
        found = False
        while search < boundary:
            candidate = text.find("`" * width, search, boundary)
            if candidate < 0:
                break
            after = candidate + width
            if (candidate == 0 or text[candidate - 1] != "`") and (
                after == len(text) or text[after] != "`"
            ):
                mask(pos, after)
                pos, found = after, True
                break
            search = after
        if not found:
            pos = stop
    return "".join(masked).splitlines()


class LinkChecker:
    def __init__(self, note: Path, root: Path):
        self.note = note
        self.root = root
        self.checked = 0
        self.skipped = 0
        self.issues: list[dict[str, object]] = []
        self.references: dict[str, str] = {}
        self._files: dict[str, list[Path]] | None = None

    def issue(self, line: int, target: str, kind: str, message: str) -> None:
        self.issues.append({"line": line, "target": target, "kind": kind, "message": message})

    @staticmethod
    def external(target: str) -> bool:
        return (
            bool(_SCHEME.match(target))
            and not _DRIVE.match(target)
            and not target.casefold().startswith("file:")
        ) or target.startswith("//")

    def file_index(self) -> dict[str, list[Path]]:
        if self._files is None:
            self._files = {}

            def fail(error: OSError) -> None:
                raise error

            for parent, _dirs, names in os.walk(self.root, followlinks=False, onerror=fail):
                for name in names:
                    self._files.setdefault(name.casefold(), []).append(Path(parent) / name)
        return self._files

    def target(self, line: int, raw_target: str, wiki: bool = False) -> None:
        if self.external(raw_target):
            self.skipped += 1
            return
        self.checked += 1
        try:
            if raw_target.casefold().startswith("file:") or _DRIVE.match(raw_target):
                raise ValueError(
                    "file: URLs and drive-letter destinations are unsupported; use a relative path"
                )
            raw_path = raw_target.split("#", 1)[0]
            if "?" in raw_path:
                raise ValueError(
                    "Local URL queries are unsupported; percent-encode a literal question mark"
                )
            if re.search(r"%(?![0-9A-Fa-f]{2})", raw_path):
                raise ValueError("Malformed percent escape in destination")
            path = unquote(raw_path, errors="strict").replace("\\", "/")
            if "\x00" in path:
                raise ValueError("NUL is not a valid path character")
            if not path:
                candidates = [self.note]
            elif wiki and "/" not in path:
                index = self.file_index()
                candidates = list(index.get(path.casefold(), []))
                if not Path(path).suffix:
                    candidates += index.get((path + ".md").casefold(), [])
                if len(candidates) > 1:
                    self.issue(
                        line,
                        raw_target,
                        "ambiguous",
                        "Wikilink filename is not unique within the root; add a vault-relative path",
                    )
                    return
            else:
                base = self.root if wiki else self.note.parent
                candidate = Path(path) if Path(path).is_absolute() else base / path
                candidates = [candidate]
                if wiki and not candidate.suffix and not candidate.exists():
                    candidates = [candidate.with_suffix(".md")]
            if not candidates:
                self.issue(line, raw_target, "missing", "No matching file exists within the root")
                return
            resolved = candidates[0].resolve()
            if not resolved.is_relative_to(self.root):
                self.issue(
                    line,
                    raw_target,
                    "outside_root",
                    "Destination resolves outside the permitted root",
                )
            elif not resolved.exists():
                self.issue(line, raw_target, "missing", "Destination file does not exist")
            elif not resolved.is_file():
                self.issue(line, raw_target, "unsupported", "Destination is not a regular file")
        except (ValueError, OSError, RuntimeError) as error:
            self.issue(line, raw_target, "unsupported", str(error))

    def scan(self, text: str, line: int, depth: int = 0) -> None:
        if depth > 32:
            self.issue(
                line, text[:100], "unsupported", "Markdown nesting exceeds the supported depth"
            )
            return
        pos = 0
        while pos < len(text):
            if text[pos] == "\\":
                pos += 2
                continue
            if text.startswith("[[", pos):
                end = text.find("]]", pos + 2)
                if end < 0:
                    self.issue(
                        line,
                        text[pos:],
                        "unsupported",
                        "Unclosed wikilink; multiline wikilinks are unsupported",
                    )
                    return
                target = text[pos + 2 : end].split("|", 1)[0].strip()
                if "[" in target or "]" in target or not target:
                    self.issue(line, target, "unsupported", "Malformed wikilink target")
                else:
                    self.target(line, _unescape(target), wiki=True)
                pos = end + 2
                continue
            if text[pos] == "[":
                end = _closing_bracket(text, pos)
                if end is None:
                    # A bracket that continues on the next line is not accepted.
                    if pos + 1 < len(text):
                        self.issue(
                            line,
                            text[pos:],
                            "unsupported",
                            "Unclosed bracket label; wrapped link labels are unsupported",
                        )
                    return
                label = text[pos + 1 : end]
                after = end + 1
                if after < len(text) and text[after] == "(":
                    try:
                        target, stop = _inline_destination(text, after)
                    except ValueError as error:
                        self.issue(line, text[pos:], "unsupported", str(error))
                        return
                    if re.search(r"(?<!!)\[[^\]]*\]\(", label):
                        self.issue(
                            line, label, "unsupported", "Nested Markdown links are unsupported"
                        )
                    else:
                        self.scan(label, line, depth + 1)
                    self.target(line, target)
                    pos = stop
                    continue
                if after < len(text) and text[after] == "[":
                    ref_end = _closing_bracket(text, after)
                    if ref_end is None:
                        self.issue(line, text[pos:], "unsupported", "Unclosed reference label")
                        return
                    reference = text[after + 1 : ref_end] or label
                    key = _reference(reference)
                    if key not in self.references:
                        self.checked += 1
                        self.issue(
                            line,
                            reference,
                            "undefined_reference",
                            "No definition exists for this explicit reference",
                        )
                    else:
                        self.target(line, self.references[key])
                    self.scan(label, line, depth + 1)
                    pos = ref_end + 1
                    continue
                if _reference(label) in self.references:
                    self.target(line, self.references[_reference(label)])
                else:
                    self.scan(label, line, depth + 1)
                pos = after
                continue
            if text.startswith("](", pos):
                self.issue(
                    line,
                    text[pos:],
                    "unsupported",
                    "Unmatched link label; wrapped labels are unsupported",
                )
                return
            if text[pos] == "<":
                html = _HTML_LINK.match(text, pos)
                if html:
                    end = text.find(">", pos)
                    if end < 0:
                        self.issue(
                            line, text[pos:], "unsupported", "Multiline HTML links are unsupported"
                        )
                        return
                    tag = text[pos : end + 1]
                    attribute = _HTML_TARGET.search(tag)
                    if attribute:
                        target = next(value for value in attribute.groups() if value is not None)
                        if self.external(target):
                            self.skipped += 1
                        else:
                            self.issue(
                                line,
                                target,
                                "unsupported",
                                "Local HTML href/src links are unsupported; use Markdown",
                            )
                    elif html[1].casefold() == "img":
                        self.issue(
                            line, tag, "unsupported", "HTML image has no supported single-line src"
                        )
                    pos = end + 1
                    continue
                end = text.find(">", pos)
                if end >= 0:
                    target = text[pos + 1 : end]
                    if self.external(target) or re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", target):
                        self.skipped += 1
                        pos = end + 1
                        continue
            pos += 1

    def run(self, text: str) -> dict[str, object]:
        lines = _visible_lines(text)
        definitions: set[int] = set()
        for number, line in enumerate(lines, 1):
            value = line.lstrip(" ")
            if len(line) - len(value) > 3 or not value.startswith("[") or value.startswith("[["):
                continue
            end = _closing_bracket(value, 0)
            if end is None or not value[end + 1 :].startswith(":"):
                continue
            definitions.add(number)
            try:
                raw = value[end + 2 :].strip()
                if not raw:
                    raise ValueError(
                        "Missing reference destination; multiline definitions are unsupported"
                    )
                key = _reference(value[1:end])
                if not key:
                    raise ValueError("Empty reference labels are unsupported")
                self.references.setdefault(key, _destination(raw))
            except ValueError as error:
                self.issue(number, value, "unsupported", str(error))
        for number, line in enumerate(lines, 1):
            if number not in definitions:
                self.scan(line, number)
        return {
            "ok": not self.issues,
            "checked": self.checked,
            "skipped": self.skipped,
            "issues": self.issues,
        }


def check_note(note: Path | str, root: Path | str | None = None) -> dict[str, object]:
    """Return a report; raise ValueError for invalid or unreadable inputs."""
    try:
        note_path = Path(note).resolve(strict=True)
        root_path = Path(root).resolve(strict=True) if root is not None else note_path.parent
        if not root_path.is_dir():
            raise ValueError("Root is not a directory")
        if not note_path.is_file():
            raise ValueError("Note is not a regular file")
        if not note_path.is_relative_to(root_path):
            raise ValueError("Note must be located inside the root")
        content = note_path.read_text(encoding="utf-8-sig")
    except (OSError, RuntimeError, UnicodeError) as error:
        raise ValueError(str(error)) from error
    return LinkChecker(note_path, root_path).run(content)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("note", type=Path)
    parser.add_argument("--root", type=Path, help="Permitted vault root (default: NOTE.parent)")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        report = check_note(args.note, args.root)
    except ValueError as error:
        report = {
            "ok": False,
            "checked": 0,
            "skipped": 0,
            "issues": [
                {"line": 0, "target": str(args.note), "kind": "input", "message": str(error)}
            ],
        }
        status = 2
    else:
        status = 0 if report["ok"] else 1
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"{'PASS' if report['ok'] else 'FAIL'}: checked={report['checked']}, skipped={report['skipped']}"
        )
        for issue in report["issues"]:
            print(f"Line {issue['line']} [{issue['kind']}] {issue['target']}: {issue['message']}")
    print(
        "Anchors (including audio timestamps) are not validated; external links are not fetched.",
        file=sys.stderr,
    )
    return status


if __name__ == "__main__":
    sys.exit(main())
