#!/usr/bin/env python3
"""Estimate a candidate recording start from independently matched anchors.

Usage: python scripts/calibrate.py anchors.json [--tolerance 120]
Only the consistency of supplied anchors is checked; visual matches and the
actual timing uncertainty cannot be verified by this deterministic helper.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
import re
import statistics
import sys
from typing import Any


class ValidationError(ValueError):
    """The supplied data cannot safely be used for calibration."""


_PHOTO_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)
_DURATION = re.compile(r"\d+:\d{2}(?:\.\d+)?|\d+:\d{2}:\d{2}(?:\.\d+)?")


def _finite_nonnegative(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label} must be a nonnegative finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValidationError(f"{label} must be a nonnegative finite number") from exc
    if not math.isfinite(result) or result < 0:
        raise ValidationError(f"{label} must be a nonnegative finite number")
    return result


def parse_duration(value: Any) -> float:
    """Parse numeric seconds, mm:ss, or h:mm:ss without guessing wall time."""
    if not isinstance(value, str):
        return _finite_nonnegative(value, "transcript_time")
    if not _DURATION.fullmatch(value):
        raise ValidationError("transcript_time must be numeric seconds, mm:ss, or h:mm:ss")
    parts = value.split(":")
    seconds = float(parts[-1])
    if seconds >= 60:
        raise ValidationError("transcript_time seconds must be less than 60")
    try:
        if len(parts) == 2:
            result = int(parts[0]) * 60 + seconds
        else:
            minutes = int(parts[1])
            if minutes >= 60:
                raise ValidationError("h:mm:ss minutes must be less than 60")
            result = int(parts[0]) * 3600 + minutes * 60 + seconds
    except (ValueError, OverflowError) as exc:
        raise ValidationError("transcript_time is outside the supported numeric range") from exc
    return _finite_nonnegative(result, "transcript_time")


def _parse_photo_time(value: Any) -> datetime:
    if not isinstance(value, str) or not _PHOTO_TIME.fullmatch(value):
        raise ValidationError(
            "photo_time must be a complete ISO date and time: YYYY-MM-DDTHH:MM:SS"
        )
    if value[-6:-5] in ("+", "-"):
        if int(value[-5:-3]) >= 24 or int(value[-2:]) >= 60:
            raise ValidationError("photo_time UTC offset hours and minutes are out of range")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"invalid photo_time: {value}") from exc


def analyze(data: Any, tolerance: float = 120) -> dict[str, Any]:
    """Return candidate timing and residuals, never a verified calibration.

    Offsets are seconds from the earliest photo date's local midnight. All
    photos must be naive, or use the same explicit UTC offset. A residual is
    the supplied anchor's offset minus the median offset, not measured error.
    """
    tolerance_seconds = _finite_nonnegative(tolerance, "tolerance")
    if not isinstance(data, dict):
        raise ValidationError("input must be an object containing anchors")
    raw_anchors = data.get("anchors")
    if not isinstance(raw_anchors, list) or not raw_anchors:
        raise ValidationError("anchors must be a nonempty list")

    parsed: list[tuple[datetime, float, str]] = []
    seen: set[datetime] = set()
    timezone_offsets: set[timedelta | None] = set()
    for number, item in enumerate(raw_anchors, start=1):
        if not isinstance(item, dict):
            raise ValidationError(f"anchor {number} must be an object")
        try:
            photo_time = _parse_photo_time(item.get("photo_time"))
            transcript_seconds = parse_duration(item.get("transcript_time"))
            evidence = item.get("evidence")
            if not isinstance(evidence, str) or not evidence.strip():
                raise ValidationError("evidence must be a nonempty description")
        except ValidationError as exc:
            raise ValidationError(f"anchor {number}: {exc}") from exc
        timezone_offsets.add(photo_time.utcoffset())
        if len(timezone_offsets) > 1:
            raise ValidationError("photo_time values must all be naive or use the same UTC offset")
        if photo_time in seen:
            raise ValidationError(f"anchor {number}: duplicate photo_time")
        seen.add(photo_time)
        parsed.append((photo_time, transcript_seconds, evidence.strip()))

    reference_time = min(item[0] for item in parsed).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    offsets = [
        (photo_time - reference_time).total_seconds() - transcript_seconds
        for photo_time, transcript_seconds, _ in parsed
    ]
    median_offset = statistics.median(offsets)
    residuals = [offset - median_offset for offset in offsets]
    max_residual = max(abs(value) for value in residuals)
    try:
        candidate_start = reference_time + timedelta(seconds=median_offset)
        individual_starts = [
            photo_time - timedelta(seconds=transcript_seconds)
            for photo_time, transcript_seconds, _ in parsed
        ]
    except (OverflowError, ValueError) as exc:
        raise ValidationError("candidate recording start is outside the supported date range") from exc

    status = (
        "provisional"
        if len(parsed) == 1
        else "candidate" if max_residual <= tolerance_seconds else "inconsistent"
    )
    return {
        "status": status,
        "anchor_count": len(parsed),
        "tolerance_seconds": tolerance_seconds,
        "offset_reference_time": reference_time.isoformat(),
        "median_offset_seconds": median_offset,
        "candidate_recording_start": candidate_start.isoformat(),
        "max_residual_seconds": max_residual,
        "anchors": [
            {
                "photo_time": photo_time.isoformat(),
                "transcript_seconds": transcript_seconds,
                "evidence": evidence,
                "offset_seconds": offset,
                "candidate_recording_start": individual_start.isoformat(),
                "residual_seconds": residual,
            }
            for (photo_time, transcript_seconds, evidence), offset, individual_start, residual
            in zip(parsed, offsets, individual_starts, residuals)
        ],
        "limitations": [
            "Assumes one constant offset; pauses, edits, clock drift, and multiple recordings may require separate segments.",
            "Content matches and photo clock accuracy must be checked by a human or a model that inspected each image.",
            "Residuals describe consistency of the supplied anchors, not actual timing uncertainty or a verified error bound.",
            "One anchor provides a provisional candidate and cannot independently establish consistency.",
        ],
    }


def _reject_json_constant(value: str) -> None:
    raise ValidationError(f"non-finite JSON number is not allowed: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON object containing matched anchors")
    parser.add_argument("--tolerance", type=float, default=120, help="maximum absolute residual in seconds (default: 120)")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.input.read_text(encoding="utf-8-sig"), parse_constant=_reject_json_constant)
        result = analyze(data, args.tolerance)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=True))
        return 2
    # ASCII JSON remains valid UTF-8 and also works in legacy Windows consoles.
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    return 1 if result["status"] == "inconsistent" else 0


if __name__ == "__main__":
    sys.exit(main())
