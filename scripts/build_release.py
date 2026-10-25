#!/usr/bin/env python3
"""Build a deterministic ZIP from an explicit, reviewed release manifest."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import zipfile


def payload(root: Path) -> tuple[bytes, int]:
    root = root.resolve()
    manifest = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("name") != "ketang-zhongdian":
        raise ValueError("manifest name must be ketang-zhongdian")
    if not isinstance(manifest.get("version"), str) or not re.fullmatch(
        r"\d+\.\d+\.\d+", manifest["version"]
    ):
        raise ValueError("manifest version must be major.minor.patch")
    names = manifest.get("files")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise ValueError("manifest files must be a nonempty list of paths")
    if len({n.casefold() for n in names}) != len(names):
        raise ValueError("duplicate or case-colliding release path")
    required = {"SKILL.md", "README.md", "LICENSE", "release-manifest.json"}
    if not required.issubset(names):
        raise ValueError("manifest must include SKILL.md, README.md, LICENSE and itself")
    entries = {}
    for name in sorted(names):
        rel = PurePosixPath(name)
        if (
            rel.is_absolute()
            or any(p in ("..", ".", "") for p in name.split("/"))
            or "\\" in name
            or ":" in name
        ):
            raise ValueError(f"unsafe release path: {name}")
        source = root / name
        if not source.resolve().is_relative_to(root):
            raise ValueError(f"release path escapes source: {name}")
        if any((root.joinpath(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts) + 1)):
            raise ValueError(f"symlinks are not supported in releases: {name}")
        if not source.is_file():
            raise ValueError(f"missing release file: {name}")
        entries[name] = source.read_bytes()
    if "MANIFEST.sha256" in entries:
        raise ValueError("MANIFEST.sha256 is generated, not a source file")
    checksums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in entries.items()
    )
    entries["MANIFEST.sha256"] = checksums.encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo("ketang-zhongdian/" + name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return output.getvalue(), len(entries)


def build(root: Path, output: Path, overwrite: bool = False) -> dict:
    root, output = root.resolve(), output.absolute()
    if output.resolve().is_relative_to(root):
        raise ValueError("release output must be outside the source directory")
    if output.is_symlink():
        raise ValueError("output symlinks are not supported")
    data, count = payload(root)
    if output.exists():
        if output.read_bytes() == data:
            return {
                "status": "unchanged",
                "files": count,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        if not overwrite:
            raise ValueError("output differs; review before using --overwrite")
    fd, temporary_name = tempfile.mkstemp(
        prefix=".ketang-release-", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary, output)
        elif os.name == "nt":
            os.rename(temporary, output)
        else:
            os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {"status": "written", "files": count, "sha256": hashlib.sha256(data).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.source, args.output, args.overwrite)))
    except (OSError, ValueError, RecursionError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
