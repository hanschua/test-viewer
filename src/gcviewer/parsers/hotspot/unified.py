"""Captured Unified Logging primitives; no collector execution-mode inference."""

import re
from collections.abc import Iterator

from ...errors import PrimitiveParseError
from ...ingest import SourceLine
from ...models import CpuTimes, LogGrammar, MemoryUsage
from ...primitives import parse_memory_transition, parse_unified_envelope
from ...provenance import ByteSpan, SourceSpan
from ...records import ParsedRecord, RecordKind
from ...timestamps import parse_duration_ns
from ...units import parse_gc_log_size

ID = re.compile(r"^GC\((?P<id>[0-9]{1,19})\)\s+(?P<body>.*)$")
REGIONS = re.compile(r"(?P<pool>Eden|Survivor|Old|Humongous) regions: (?P<before>[0-9]{1,19})->(?P<after>[0-9]{1,19})(?:\((?P<capacity>[0-9]{1,19})\))?")
POOL = re.compile(r"(?P<pool>Metaspace|NonClass|Class): (?P<before>[0-9]+[KMG])\((?P<before_capacity>[0-9]+[KMG])\)->(?P<after>[0-9]+[KMG])\((?P<after_capacity>[0-9]+[KMG])\)")
CPU = re.compile(r"User=(?P<user>[0-9.]+)s Sys=(?P<system>[0-9.]+)s Real=(?P<real>[0-9.]+)s")
TASK = re.compile(r"Using (?P<workers>[0-9]{1,9}) workers of (?P<available>[0-9]{1,9}) for (?P<work>evacuation|full compaction)")
PHASES = {
    "Pre Evacuate Collection Set", "Merge Heap Roots", "Evacuate Collection Set",
    "Post Evacuate Collection Set", "Other", "Phase 1: Mark live objects",
    "Phase 2: Prepare compaction", "Phase 3: Adjust pointers", "Phase 4: Compact heap",
    "Phase 5: Reset Metadata",
}
METASPACE_CONFIG = (
    "CDS archive(s) mapped at:", "Compressed class space mapped at:",
    "UseCompressedClassPointers ", "Narrow klass pointer bits ", "Narrow klass base:",
    "Encoding Range:", "Klass Range:", "Klass ID Range:", "Protection zone:",
)
EXIT = ("Heap", "garbage-first heap ", "region size ", "Metaspace ", "class space ")


def source_span(line: SourceLine) -> SourceSpan:
    return SourceSpan(line.source_id, line.line_number, line.line_number,
                      ByteSpan(line.byte_offset, line.byte_offset + line.raw_length_bytes, line.offset_space))


def gc_like(text: str) -> bool:
    return bool(re.match(r"(?:\[[^\]\n]*\])*\[gc(?:,|\s|\]|$)", text)
                or re.match(r"^(?:\[[^\]\n]*\])*\s*GC\([0-9]+\)", text))


def _pause_name(body: str) -> bool:
    return len(body) <= 2048 and ((body.startswith("Pause Young (Normal) (") and body.endswith(")"))
            or body == "Pause Full (System.gc())")


def _count(text: str) -> int:
    value = int(text)
    if value > (1 << 63) - 1:
        raise PrimitiveParseError("region count exceeds signed BIGINT")
    return value


class UnifiedParser:
    grammar = LogGrammar.HOTSPOT_UNIFIED

    def parse(self, lines: Iterator[SourceLine]) -> Iterator[ParsedRecord]:
        for line in lines:
            yield self.parse_line(line)

    def parse_line(self, line: SourceLine) -> ParsedRecord:
        spans = (source_span(line),)
        if not line.text.strip():
            return ParsedRecord(RecordKind.BLANK, spans)
        try:
            envelope = parse_unified_envelope(line.text)
        except PrimitiveParseError:
            return ParsedRecord(RecordKind.UNKNOWN if gc_like(line.text) else RecordKind.APPLICATION, spans)
        common = dict(source_spans=spans, uptime_ns=envelope.uptime_ns,
                      wall_clock=envelope.wall_clock, decorators=envelope.decorators,
                      unknown_decorators=envelope.unknown_decorators,
                      tags=tuple(sorted(envelope.tags)), level=envelope.level)
        if "gc" not in envelope.tags:
            return ParsedRecord(RecordKind.UNKNOWN if gc_like(line.text) else RecordKind.APPLICATION, **common)
        payload = envelope.payload
        match = ID.fullmatch(payload)
        if not match:
            if payload == "Using G1" and envelope.tags == {"gc"}:
                return ParsedRecord(RecordKind.COLLECTOR, name="g1", **common)
            if envelope.tags == {"gc", "init"} and ": " in payload:
                name, raw = payload.split(": ", 1)
                return ParsedRecord(RecordKind.METADATA, name=name,
                                    attributes={"raw_value": raw}, **common)
            if envelope.tags == {"gc", "metaspace"} and payload.startswith(METASPACE_CONFIG):
                return ParsedRecord(RecordKind.METADATA, name="metaspace.configuration",
                                    attributes={"raw_value": payload}, **common)
            if envelope.tags == {"gc", "exit"} and payload.strip().startswith(EXIT):
                return ParsedRecord(RecordKind.METADATA, name="heap.exit",
                                    attributes={"raw_value": payload}, **common)
            return ParsedRecord(RecordKind.UNKNOWN, **common)
        gc_id = int(match["id"])
        if gc_id > (1 << 63) - 1:
            return ParsedRecord(RecordKind.UNKNOWN, **common)
        common["gc_id"] = gc_id
        body = match["body"].strip()
        tags = envelope.tags
        try:
            if tags == {"gc", "start"} and _pause_name(body):
                return ParsedRecord(RecordKind.START, name=body, **common)
            if tags == {"gc"}:
                pieces = body.rsplit(" ", 2)
                if len(pieces) == 3 and _pause_name(pieces[0]) and pieces[2].endswith("ms"):
                    return ParsedRecord(RecordKind.END, name=pieces[0],
                                        duration_ns=parse_duration_ns(pieces[2][:-2], "ms"),
                                        heap=parse_memory_transition(pieces[1]), **common)
            if tags == {"gc", "phases", "start"} and body in PHASES:
                return ParsedRecord(RecordKind.PHASE_START, name=body, **common)
            if tags == {"gc", "phases"}:
                pieces = body.rsplit(" ", 1)
                if len(pieces) == 2 and pieces[0].rstrip(":") in PHASES and pieces[1].endswith("ms"):
                    return ParsedRecord(RecordKind.PHASE_END, name=pieces[0].rstrip(":"),
                                        duration_ns=parse_duration_ns(pieces[1][:-2], "ms"), **common)
            if tags == {"gc", "heap"}:
                regions = REGIONS.fullmatch(body)
                if regions:
                    pool = regions["pool"].lower()
                    attrs = {f"g1.{pool}.before_regions": _count(regions["before"]),
                             f"g1.{pool}.after_regions": _count(regions["after"])}
                    if regions["capacity"] is not None:
                        attrs[f"g1.{pool}.capacity_regions"] = _count(regions["capacity"])
                    return ParsedRecord(RecordKind.ATTRIBUTE, attributes=attrs, **common)
            if tags == {"gc", "metaspace"}:
                parts = list(POOL.finditer(body))
                if (len(parts) == 3 and " ".join(part[0] for part in parts) == body
                        and [part["pool"] for part in parts] == ["Metaspace", "NonClass", "Class"]):
                    pools, attrs = {}, {}
                    for part in parts:
                        name = {"Metaspace": "metaspace", "NonClass": "nonclass", "Class": "compressed_class_space"}[part["pool"]]
                        pools[name] = MemoryUsage(before_bytes=parse_gc_log_size(part["before"]).bytes,
                                                 after_bytes=parse_gc_log_size(part["after"]).bytes,
                                                 committed_bytes=parse_gc_log_size(part["after_capacity"]).bytes)
                        attrs[f"{name}.before_committed_bytes"] = parse_gc_log_size(part["before_capacity"]).bytes
                    return ParsedRecord(RecordKind.MEMORY, pools=pools, attributes=attrs, **common)
            if tags == {"gc", "cpu"}:
                cpu = CPU.fullmatch(body)
                if cpu:
                    return ParsedRecord(RecordKind.CPU,
                                        cpu=CpuTimes(*(parse_duration_ns(cpu[name]) for name in ("user", "system", "real"))),
                                        **common)
            if tags == {"gc", "task"}:
                task = TASK.fullmatch(body)
                if task:
                    return ParsedRecord(RecordKind.ATTRIBUTE, attributes={
                        "g1.workers": int(task["workers"]), "g1.available_workers": int(task["available"]),
                        "g1.worker_task": task["work"]}, **common)
        except PrimitiveParseError:
            pass
        return ParsedRecord(RecordKind.UNKNOWN, **common)
