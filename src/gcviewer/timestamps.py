"""Integer arithmetic preserves nanosecond precision without float conversion."""

import re
from datetime import datetime, timedelta, timezone

from .errors import PrimitiveParseError
from .models import WallClockValue

MAX_NS = (1 << 63) - 1
DECIMAL = re.compile(r"(?P<whole>[0-9]+)(?:\.(?P<fraction>[0-9]+))?")
WALL = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(?P<fraction>\d{1,9}))?(?P<zone>Z|[+-]\d{2}:?\d{2})?")


def parse_duration_ns(text: str, unit: str = "s") -> int:
    scales = {"s": 9, "ms": 6, "us": 3, "ns": 0}
    match = DECIMAL.fullmatch(text) if len(text) <= 64 else None
    if match is None or unit not in scales:
        raise PrimitiveParseError("invalid duration")
    digits = scales[unit]
    fraction = match.group("fraction") or ""
    if any(ch != "0" for ch in fraction[digits:]):
        raise PrimitiveParseError("duration has sub-nanosecond precision")
    value = int(match.group("whole")) * 10 ** digits
    value += int(fraction[:digits].ljust(digits, "0") or "0")
    if value > MAX_NS:
        raise PrimitiveParseError("duration exceeds signed BIGINT")
    return value


def parse_uptime_ns(text: str) -> int:
    if text.endswith(":"):
        return parse_duration_ns(text[:-1])
    for unit in ("ms", "ns", "s"):
        if text.endswith(unit):
            return parse_duration_ns(text[:-len(unit)], unit)
    raise PrimitiveParseError("uptime unit required")


def parse_wall_clock(text: str) -> WallClockValue:
    match = WALL.fullmatch(text) if len(text) <= 64 else None
    if match is None:
        raise PrimitiveParseError("invalid wall clock")
    zone = match.group("zone")
    try:
        base = datetime.strptime(match.group("date"), "%Y-%m-%dT%H:%M:%S")
        if zone is None:
            return WallClockValue(None, None, text)
        offset = 0
        if zone != "Z":
            compact = zone[1:].replace(":", "")
            hours, minutes = int(compact[:2]), int(compact[2:])
            if hours > 23 or minutes > 59:
                raise ValueError("invalid UTC offset")
            offset = (hours * 60 + minutes) * (-1 if zone[0] == "-" else 1)
        base = base.replace(tzinfo=timezone(timedelta(minutes=offset)))
        delta = base - datetime(1970, 1, 1, tzinfo=timezone.utc)
        epoch = (delta.days * 86400 + delta.seconds) * 1_000_000_000
        epoch += int((match.group("fraction") or "").ljust(9, "0"))
        if not -(1 << 63) <= epoch <= MAX_NS:
            raise ValueError("epoch exceeds signed BIGINT")
        return WallClockValue(epoch, offset, text)
    except ValueError as exc:
        raise PrimitiveParseError(str(exc)) from exc
