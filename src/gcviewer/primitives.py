"""Small lexical primitives; no collector interpretation."""

import re
from dataclasses import dataclass

from .errors import PrimitiveParseError
from .models import MemoryUsage, WallClockValue
from .timestamps import parse_uptime_ns, parse_wall_clock
from .units import parse_gc_log_size

TRANSITION = re.compile(r"(?P<before>[0-9]+(?:\.[0-9]+)?[kKmMgG]?)->(?P<after>[0-9]+(?:\.[0-9]+)?[kKmMgG]?)\((?P<capacity>[0-9]+(?:\.[0-9]+)?[kKmMgG]?)\)")
GROUP = re.compile(r"\[([^\[\]]*)\]")


def parse_memory_transition(text: str) -> MemoryUsage:
    match = TRANSITION.fullmatch(text) if len(text) <= 384 else None
    if not match:
        raise PrimitiveParseError("invalid memory transition")
    return MemoryUsage(before_bytes=parse_gc_log_size(match["before"]).bytes,
                       after_bytes=parse_gc_log_size(match["after"]).bytes,
                       capacity_bytes=parse_gc_log_size(match["capacity"]).bytes)


@dataclass(frozen=True, slots=True)
class ArgumentToken:
    ordinal: int
    raw: str
    value: str


def tokenize_jvm_arguments(text: str) -> tuple[ArgumentToken, ...]:
    """Retain exact token spelling, including quotes; expose decoded value separately.

    This is command-line quoting, not shell expansion or Java @argfile syntax.
    """
    tokens = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        start, quote, chars = index, None, []
        while index < len(text):
            char = text[index]
            if quote is None and char.isspace():
                break
            if char in "\"'" and (quote is None or quote == char):
                quote = None if quote == char else char
            elif char == "\\" and index + 1 < len(text) and text[index + 1] in "\\\"'":
                index += 1
                chars.append(text[index])
            else:
                chars.append(char)
            index += 1
        if quote is not None:
            raise PrimitiveParseError("unterminated argument quote")
        tokens.append(ArgumentToken(len(tokens), text[start:index], "".join(chars)))
    return tuple(tokens)


@dataclass(frozen=True, slots=True)
class UnifiedEnvelope:
    decorators: tuple[str, ...]
    unknown_decorators: tuple[str, ...]
    level: str | None
    tags: frozenset[str]
    payload: str
    uptime_ns: int | None
    wall_clock: WallClockValue | None


def parse_unified_envelope(text: str) -> UnifiedEnvelope:
    groups, position = [], 0
    while position < len(text) and text[position] == "[":
        match = GROUP.match(text, position)
        if match is None:
            raise PrimitiveParseError("unclosed Unified Logging decorator")
        groups.append(match[1])
        position = match.end()
    if not groups:
        raise PrimitiveParseError("Unified Logging envelope required")
    level, tags, uptime, wall = None, frozenset(), None, None
    unknown = []
    for raw in groups:
        value = raw.strip()
        if value in {"trace", "debug", "info", "warning", "error", "off"}:
            level = value
        elif "gc" in value.split(",") or "safepoint" in value.split(","):
            tags = frozenset(value.split(","))
        elif re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:s|ms|ns)", value):
            uptime = parse_uptime_ns(value)
        elif re.match(r"\d{4}-\d{2}-\d{2}T", value):
            wall = parse_wall_clock(value)
        else:
            # Bare numeric decorators can be pid/tid/timemillis; do not guess.
            unknown.append(raw)
    return UnifiedEnvelope(tuple(groups), tuple(unknown), level, tags,
                           text[position:].lstrip(), uptime, wall)
