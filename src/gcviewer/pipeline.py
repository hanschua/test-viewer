"""Streaming Phase 4 parse/assemble pipeline with bounded diagnostics."""

import hashlib
import re
from dataclasses import dataclass

from .assembly.hotspot import G1Assembler
from .detection import DetectionPolicy, DetectionStatus, RecognitionCounts, default_registry, validate_selection
from .errors import ParseWarning, WarningCode
from .ingest import InputSource, line_lookahead
from .models import GCCycle
from .parsers.hotspot.unified import UnifiedParser, source_span
from .records import RecordKind, UnknownGCRecord
from .segment import boundary_evidence


@dataclass(slots=True)
class Coverage:
    total_physical_lines: int = 0
    blank_lines: int = 0
    application_lines: int = 0
    recognized_metadata_records: int = 0
    recognized_gc_records: int = 0
    unknown_gc_records: int = 0
    parsed_events: int = 0
    complete_events: int = 0
    incomplete_events: int = 0
    parse_warnings: int = 0
    parse_errors: int = 0
    ambiguous_records: int = 0
    segments_detected: int = 0

    @property
    def gc_recognition_ratio(self):
        recognized = self.recognized_gc_records + self.recognized_metadata_records
        total = recognized + self.unknown_gc_records
        return recognized / total if total else None


@dataclass(frozen=True, slots=True)
class SegmentReport:
    segment_id: int
    detection: dict
    validation_reason: str
    validation_accepted: bool | None


def _segments(lines):
    """Yield lazily consumed segments, retaining only one boundary record."""
    pending, exhausted = None, False
    while not exhausted:
        first = pending if pending is not None else next(lines, None)
        pending = None
        if first is None:
            return

        def segment():
            nonlocal pending, exhausted
            activity = bool(re.match(r"^(?:\[[^\]]*\])*\s*GC\([0-9]+\)", first.text))
            yield first
            for line in lines:
                evidence, _ = boundary_evidence(line)
                if activity and any(item.weight >= 1 for item in evidence):
                    pending = line
                    return
                activity |= bool(re.match(r"^(?:\[[^\]]*\])*\s*GC\([0-9]+\)", line.text))
                yield line
            exhausted = True

        yield segment()


class UnifiedPipeline:
    def __init__(self, source: InputSource, preview_bytes: int = 1024,
                 max_open_cycles: int = 64, max_records_per_cycle: int = 4096,
                 policy: DetectionPolicy = DetectionPolicy()):
        if type(preview_bytes) is not int or preview_bytes < 1:
            raise ValueError("preview limit must be positive")
        # Validate assembly limits before opening the input.
        G1Assembler(max_open_cycles=max_open_cycles, max_records_per_cycle=max_records_per_cycle)
        self.source = source
        self.preview_bytes = preview_bytes
        self.max_open_cycles = max_open_cycles
        self.max_records_per_cycle = max_records_per_cycle
        self.policy = policy
        self.coverage = Coverage()
        self._used = False

    def _output(self, item):
        if isinstance(item, GCCycle):
            self.coverage.parsed_events += 1
            if WarningCode.TRUNCATED_EVENT.value in item.warning_codes:
                self.coverage.incomplete_events += 1
            else:
                self.coverage.complete_events += 1
        elif isinstance(item, ParseWarning):
            self.coverage.parse_warnings += 1
        return item

    def stream(self):
        if self._used:
            raise ValueError("a pipeline is single-pass and cannot be reused")
        self._used = True
        lines = self.source.iter_lines()
        try:
            for segment_id, segment in enumerate(_segments(lines), 1):
                self.coverage.segments_detected += 1
                buffered, replay = line_lookahead(segment, self.source.limits)
                detection = default_registry().detect(buffered, self.policy)
                assembler = G1Assembler(segment_id, self.max_open_cycles, self.max_records_per_cycle, self.preview_bytes)
                parser = UnifiedParser()
                recognized, unknown = 0, 0
                validation_recognized, validation_unknown = 0, 0
                validation = None
                validation_warning_emitted = False
                validation_limit = max(500, self.policy.validation_minimum_records)
                for line in replay:
                    self.coverage.total_physical_lines += 1
                    if line.decoding_error:
                        yield self._output(ParseWarning(WarningCode.ENCODING_WARNING,
                                                        source_spans=(source_span(line),), segment_id=segment_id))
                    record = parser.parse_line(line)
                    if record.kind == RecordKind.BLANK:
                        self.coverage.blank_lines += 1
                        continue
                    if record.kind == RecordKind.APPLICATION:
                        self.coverage.application_lines += 1
                        continue
                    if detection.selected is None or record.kind == RecordKind.UNKNOWN:
                        unknown += 1
                        self.coverage.unknown_gc_records += 1
                        if detection.status == DetectionStatus.AMBIGUOUS:
                            self.coverage.ambiguous_records += 1
                        preview = line.text.encode("utf-8")[:self.preview_bytes].decode("utf-8", errors="ignore")
                        normalized = re.sub(r"[0-9]+(?:\.[0-9]+)?", "#", preview)
                        signature = hashlib.sha256(normalized.encode()).hexdigest()[:24]
                        yield UnknownGCRecord(segment_id, record.source_spans,
                                              "UNKNOWN_GC_RECORD" if detection.selected else detection.reason_code,
                                              signature, preview)
                        yield self._output(ParseWarning(WarningCode.UNKNOWN_GC_RECORD,
                                                        source_spans=record.source_spans, segment_id=segment_id))
                        for item in assembler.note_unknown(record):
                            yield self._output(item)
                    else:
                        recognized += 1
                        if record.kind in {RecordKind.METADATA, RecordKind.COLLECTOR}:
                            self.coverage.recognized_metadata_records += 1
                            yield record
                        else:
                            self.coverage.recognized_gc_records += 1
                            for item in assembler.accept(record):
                                yield self._output(item)
                    if validation_recognized + validation_unknown < validation_limit:
                        validation_recognized, validation_unknown = recognized, unknown
                    if validation is None and validation_recognized + validation_unknown >= validation_limit:
                        validation = validate_selection(detection, RecognitionCounts(validation_recognized, validation_unknown), self.policy)
                        if validation.accepted is False:
                            for item in assembler.finish(truncated=True):
                                yield self._output(item)
                            detection = validation.detection
                            validation_warning_emitted = True
                            yield self._output(ParseWarning(WarningCode.AMBIGUOUS_GRAMMAR,
                                                            detail=validation.reason_code, segment_id=segment_id))
                for item in assembler.finish():
                    yield self._output(item)
                if validation is None:
                    validation = validate_selection(detection, RecognitionCounts(validation_recognized, validation_unknown), self.policy)
                if not validation_warning_emitted and (validation.accepted is False or detection.status == DetectionStatus.AMBIGUOUS):
                    yield self._output(ParseWarning(WarningCode.AMBIGUOUS_GRAMMAR,
                                                    detail=validation.reason_code if detection.selected else detection.reason_code,
                                                    segment_id=segment_id))
                elif detection.selected is None and not validation_warning_emitted:
                    yield self._output(ParseWarning(WarningCode.UNSUPPORTED_RECORD, detail=detection.reason_code,
                                                    segment_id=segment_id))
                yield SegmentReport(segment_id, validation.detection.to_dict(), validation.reason_code, validation.accepted)
        finally:
            lines.close()
