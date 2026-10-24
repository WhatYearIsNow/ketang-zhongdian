#!/usr/bin/env python3
"""Extract local TXT/DOCX transcripts without modifying the input (stdlib only)."""

from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

MAX_TEXT_BYTES = 32 * 1024 * 1024
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
SPEAKER = re.compile(r"^(\s*(?:[•●]\s*)?)Speaker[ \t]+(\d+)\b", re.MULTILINE | re.IGNORECASE)
OMITTED = {W + x for x in ("txbxContent", "drawing", "pict", "del", "ins", "moveFrom", "moveTo")}


class TranscriptError(ValueError):
    """Invalid or unsupported input; originals are left intact."""


class SafeTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise TranscriptError("DTD/entities are not supported in DOCX XML")


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise TranscriptError("NUL characters found; specify the correct text encoding")
    if not text.strip():
        raise TranscriptError("transcript is empty")
    text = SPEAKER.sub(lambda m: m.group(1) + "发言者 " + m.group(2), text)
    return text.rstrip() + "\n"


def decode_text(data: bytes, encoding: str | None = None) -> str:
    if len(data) > MAX_TEXT_BYTES:
        raise TranscriptError("text exceeds 32 MiB limit")
    if encoding is None:
        if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            raise TranscriptError("UTF-32 requires explicit --encoding utf-32")
        encoding = "utf-16" if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8-sig"
    try:
        return data.decode(encoding, errors="strict")
    except (UnicodeError, LookupError) as exc:
        raise TranscriptError("cannot decode input; use --encoding for a known encoding") from exc


def extract_docx(path: Path) -> tuple[str, list[str]]:
    try:
        with zipfile.ZipFile(path) as archive:
            matches = [i for i in archive.infolist() if i.filename == "word/document.xml"]
            if len(matches) != 1:
                raise TranscriptError("DOCX must contain exactly one word/document.xml")
            if matches[0].file_size > MAX_TEXT_BYTES:
                raise TranscriptError("DOCX document.xml exceeds 32 MiB limit")
            data = archive.read(matches[0])
        # Parser-level refusal also covers UTF-16 XML, unlike a byte substring.
        root = ET.fromstring(data, parser=ET.XMLParser(target=SafeTreeBuilder()))
    except (zipfile.BadZipFile, ET.ParseError, RuntimeError, NotImplementedError) as exc:
        raise TranscriptError("invalid, encrypted, or unsupported DOCX") from exc
    body = root.find(W + "body")
    if body is None:
        raise TranscriptError("DOCX has no main document body")
    warnings = []
    tags = {node.tag for node in body.iter()}
    if tags & OMITTED:
        warnings.append("drawings/textboxes/tracked revisions omitted; read the original before claiming full coverage")
    if tags & {W + "footnoteReference", W + "endnoteReference", W + "altChunk"}:
        warnings.append("footnotes/endnotes/embedded content omitted; inspect with a document tool")
    if tags & {M + "oMath", M + "oMathPara"}:
        warnings.append("OMML math formulas omitted; inspect and transcribe equations from the original")
    if any(i.tag == W + "instrText" for i in body.iter()):
        warnings.append("field instructions omitted; verify displayed field values in the original")

    def inline(node: ET.Element) -> str:
        if node.tag in OMITTED:
            return ""
        if node.tag == W + "t":
            return node.text or ""
        if node.tag == W + "tab":
            return "\t"
        if node.tag in {W + "br", W + "cr"}:
            return "\n"
        return "".join(inline(child) for child in node)

    def paragraphs(node: ET.Element):
        if node.tag in OMITTED:
            return
        if node.tag == W + "p":
            yield inline(node)
        else:
            for child in node:
                yield from paragraphs(child)

    try:
        return "\n".join(paragraphs(body)), warnings
    except RecursionError as exc:
        raise TranscriptError("DOCX XML nesting is too deep; use a document tool") from exc


def publish_no_clobber(temporary: Path, path: Path) -> None:
    if os.name == "nt":
        # Unlike POSIX rename, Windows rename fails if the destination exists.
        os.rename(temporary, path)
    else:
        os.link(temporary, path)


def atomic_write(path: Path, data: bytes, overwrite: bool = False) -> str:
    """Publish a fully written sibling file; never truncate an existing file."""
    if path.exists():
        if path.read_bytes() == data:
            return "unchanged"
        if not overwrite:
            raise TranscriptError("output exists with different content; use --overwrite after review")
    if not path.parent.is_dir():
        raise TranscriptError("output parent directory does not exist")
    fd, name = tempfile.mkstemp(prefix=".transcript-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            # Publication fails if another process created the destination.
            try:
                publish_no_clobber(temporary, path)
            except FileExistsError as exc:
                if path.is_file() and path.read_bytes() == data:
                    return "unchanged"
                raise TranscriptError("output was created concurrently; no overwrite performed") from exc
        return "written"
    finally:
        temporary.unlink(missing_ok=True)


def extract(source: Path, output: Path, *, encoding: str | None = None, overwrite: bool = False) -> dict:
    if source.resolve() == output.resolve():
        raise TranscriptError("input and output must be different files")
    if output.is_symlink():
        raise TranscriptError("output symlinks are not supported")
    if output.suffix.lower() != ".txt":
        raise TranscriptError("output must have a .txt extension")
    warnings: list[str] = []
    if source.suffix.lower() == ".docx":
        if encoding is not None:
            raise TranscriptError("--encoding applies only to TXT input")
        raw, warnings = extract_docx(source)
    elif source.suffix.lower() == ".txt":
        if source.stat().st_size > MAX_TEXT_BYTES:
            raise TranscriptError("text exceeds 32 MiB limit")
        raw = decode_text(source.read_bytes(), encoding)
    else:
        raise TranscriptError("supported input extensions: .txt, .docx")
    content = normalize(raw)
    data = content.encode("utf-8")
    status = atomic_write(output, data, overwrite)
    return {"ok": True, "status": status, "characters": len(content),
            "sha256": hashlib.sha256(data).hexdigest(), "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--encoding")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = extract(args.input, args.output, encoding=args.encoding, overwrite=args.overwrite)
    except (TranscriptError, OSError, ValueError, OverflowError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
