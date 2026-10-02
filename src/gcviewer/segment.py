"""Conservative boundary evidence; timestamp resets cannot split alone."""

import re
from dataclasses import dataclass
from typing import Iterator

from .ingest import SourceLine
from .primitives import parse_unified_envelope
from .errors import PrimitiveParseError


@dataclass(frozen=True, slots=True)
class BoundaryEvidence:
    code: str
    weight: float
    line_number: int


@dataclass(frozen=True, slots=True)
class SegmentBoundary:
    segment_id: int
    start_line: int
    start_offset: int
    evidence: tuple[BoundaryEvidence, ...]


def boundary_evidence(line: SourceLine, previous_uptime_ns: int | None = None):
    text = line.text
    evidence = []
    if re.search(r'^(?:java|openjdk) version "', text):
        evidence.append(BoundaryEvidence("JVM_VERSION_BANNER", 1.0, line.line_number))
    if re.match(r"\s*<verbosegc(?:\s|>)", text):
        evidence.append(BoundaryEvidence("OPENJ9_XML_ROOT", 1.0, line.line_number))
    uptime = None
    try:
        envelope = parse_unified_envelope(text)
        uptime = envelope.uptime_ns
        if envelope.tags == {"gc", "init"} and envelope.payload.startswith("CardTable entry size: "):
            evidence.append(BoundaryEvidence("UNIFIED_STARTUP_HEADER", 1.0, line.line_number))
        if "gc" in envelope.tags and envelope.payload.startswith("Using "):
            evidence.append(BoundaryEvidence("COLLECTOR_STARTUP", 1.0, line.line_number))
    except PrimitiveParseError:
        pass
    if (previous_uptime_ns is not None and uptime is not None
            and previous_uptime_ns > 60_000_000_000 and uptime < 1_000_000_000):
        evidence.append(BoundaryEvidence("UPTIME_RESET", 0.3, line.line_number))
    return tuple(evidence), uptime


def discover_boundaries(lines: Iterator[SourceLine]) -> Iterator[SegmentBoundary]:
    segment, previous, seen_activity = 0, None, False
    for line in lines:
        evidence, uptime = boundary_evidence(line, previous)
        strong = any(item.weight >= 1.0 for item in evidence)
        if segment == 0 or (strong and seen_activity):
            segment += 1
            yield SegmentBoundary(segment, line.line_number, line.byte_offset, evidence)
            seen_activity = False
            previous = None
        # Group contiguous startup declarations into the same launch.
        if not strong and ("GC(" in line.text or "<cycle-start" in line.text or "<gc " in line.text):
            seen_activity = True
        if uptime is not None:
            previous = uptime
