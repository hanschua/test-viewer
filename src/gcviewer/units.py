"""Exact memory-size primitives with explicit syntax profiles."""

import re
from dataclasses import dataclass
from decimal import Decimal

from .errors import PrimitiveParseError

MAX_BYTES = (1 << 63) - 1
SIZE = re.compile(r"(?P<number>[0-9]+(?:\.[0-9]+)?)(?P<unit>[kKmMgGtT]?(?:iB)?)")


@dataclass(frozen=True, slots=True)
class ParsedSize:
    bytes: int
    raw: str
    unit: str


def _parse(text: str, profile: str) -> ParsedSize:
    if len(text) > 128:
        raise PrimitiveParseError("size token too long")
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", text) and profile != "iec":
        value, unit = int(text, 16), ""
    else:
        match = SIZE.fullmatch(text)
        if not match:
            raise PrimitiveParseError("invalid memory size")
        number, unit = match.group("number", "unit")
        if profile == "jvm" and ("." in number or "iB" in unit):
            raise PrimitiveParseError("invalid JVM size syntax")
        if profile == "iec" and unit not in {"KiB", "MiB", "GiB", "TiB"}:
            raise PrimitiveParseError("explicit IEC suffix required")
        power = "kmgt".index(unit[0].lower()) + 1 if unit else 0
        # Token lengths are bounded; widen context so no decimal rounding occurs.
        from decimal import localcontext
        with localcontext() as context:
            context.prec = 160
            exact = Decimal(number) * (1024 ** power)
            if exact != exact.to_integral_value():
                raise PrimitiveParseError("fractional byte count")
            value = int(exact)
    if value > MAX_BYTES:
        raise PrimitiveParseError("memory size exceeds signed BIGINT")
    return ParsedSize(value, text, unit)


def parse_jvm_size(text: str) -> ParsedSize:
    return _parse(text, "jvm")


def parse_gc_log_size(text: str) -> ParsedSize:
    """HotSpot-style binary K/M/G profile, not arbitrary SI input."""
    return _parse(text, "gc")


def parse_explicit_iec_size(text: str) -> ParsedSize:
    return _parse(text, "iec")
