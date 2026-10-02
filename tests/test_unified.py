import gzip
import json
import subprocess
import sys
import tracemalloc
from dataclasses import asdict
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from gcviewer.assembly.hotspot import G1Assembler
from gcviewer.corpus import load_manifest
from gcviewer.errors import InputOpenError, ParseWarning, ResourceLimitError, WarningCode
from gcviewer.ingest import InputSource, SourceLine
from gcviewer.models import Collector, ExecutionMode, GCCycle
from gcviewer.parsers.hotspot.unified import UnifiedParser
from gcviewer.pipeline import SegmentReport, UnifiedPipeline
from gcviewer.provenance import ByteSpan, OffsetSpace, SourceSpan, ValueSource
from gcviewer.records import ParsedRecord, RecordKind, UnknownGCRecord
from gcviewer.source import read_source_spans

ROOT = Path(__file__).parents[1]
CAPTURE = ROOT / "corpus/detection/jdk25-g1.log"


def line(text, number=1):
    return SourceLine(number, 0, text, "test", len(text.encode()) + 1, "lf", OffsetSpace.ORIGINAL_FILE)


def run(path, **kwargs):
    pipeline = UnifiedPipeline(InputSource(path), **kwargs)
    output = list(pipeline.stream())
    return pipeline, output, [item for item in output if isinstance(item, GCCycle)]


def test_corpus_canonical_goldens():
    for entry in load_manifest(ROOT / "corpus/manifest.json"):
        golden = json.loads((ROOT / "corpus" / entry.golden_path).read_text())
        pipeline, output, cycles = run(ROOT / "corpus" / entry.path)
        for name, value in golden["coverage"].items():
            assert getattr(pipeline.coverage, name) == value, name
        assert pipeline.coverage.unknown_gc_records == entry.expected_unknown_gc_records
        assert pipeline.coverage.gc_recognition_ratio == 1
        assert len(cycles) == len(golden["cycles"])
        for cycle, expected in zip(cycles, golden["cycles"]):
            op = cycle.operations[0]
            assert cycle.gc_id == expected["gc_id"]
            assert cycle.cycle_type == expected["cycle_type"]
            assert cycle.collector == Collector.G1
            assert cycle.total_pause_ns == op.duration_ns == expected["duration_ns"]
            assert op.execution_mode == ExecutionMode.STOP_THE_WORLD
            assert op.heap.before_bytes == expected["before_mib"] * 1024**2
            assert op.heap.after_bytes == expected["after_mib"] * 1024**2
            assert op.heap.capacity_bytes == 32 * 1024**2
            assert op.heap.committed_bytes is None
            assert len(op.phases) == expected["phase_count"]
            assert all(phase.execution_mode == ExecutionMode.STOP_THE_WORLD for phase in op.phases)
            assert cycle.source_spans[0].start_line == expected["start_line"]
            assert cycle.source_spans[-1].end_line == expected["end_line"]
            assert op.cycle_key == cycle.cycle_key
            assert cycle.aggregate_source == ValueSource.DERIVED
            assert not cycle.warning_codes
            assert "sum" in cycle.value_provenance["total_pause_ns"].derivation
        first = cycles[0]
        op = first.operations[0]
        expected = golden["first_cycle"]
        for name in ("uptime_start_ns", "uptime_end_ns", "elapsed_ns", "cause"):
            assert getattr(first, name) == expected[name]
        assert first.total_pause_ns != first.elapsed_ns  # rounded uptime is not pause duration
        assert op.metaspace.before_bytes == expected["metaspace_before_bytes"]
        assert op.metaspace.committed_bytes == expected["metaspace_committed_bytes"]
        assert op.compressed_class_space.before_bytes == expected["class_space_before_bytes"]
        assert [phase.duration_ns for phase in op.phases] == expected["phase_durations_ns"]
        for name in ("user", "system", "real"):
            assert getattr(op.cpu, f"{name}_ns") == expected[f"cpu_{name}_ns"]
        assert op.attributes["g1.eden.before_regions"] == 17
        full = cycles[-1]
        assert full.cause == golden["full_cycle"]["cause"]
        assert [p.start_ns for p in full.operations[0].phases] == golden["full_cycle"]["phase_start_ns"]
        assert [p.duration_ns for p in full.operations[0].phases] == golden["full_cycle"]["phase_durations_ns"]
        report = next(item for item in output if isinstance(item, SegmentReport))
        assert report.validation_accepted is True
        assert report.detection["selected"]["vendor"] == "unknown"


def test_original_source_navigation_matches_every_cycle():
    _, _, cycles = run(CAPTURE)
    physical = CAPTURE.read_text().splitlines(keepends=True)
    for cycle in cycles:
        excerpts = read_source_spans(CAPTURE, cycle.source_spans)
        assert [item.text for item in excerpts] == [physical[span.start_line - 1] for span in cycle.source_spans]
        assert len(excerpts) == (14 if cycle.gc_id != 5 else 19)
        for field in ("heap", "duration_ns", "cpu", "metaspace"):
            assert cycle.operations[0].value_provenance[field].source_spans


def test_interleaved_real_records_have_noncontiguous_spans(tmp_path):
    captured = CAPTURE.read_text().splitlines(keepends=True)
    first, second = captured[26:40], captured[40:54]
    interleaved = [value for pair in zip(first, second) for value in pair]
    path = tmp_path / "interleaved.log"
    path.write_text("".join(captured[:26] + interleaved))
    _, _, cycles = run(path)
    assert [cycle.gc_id for cycle in cycles] == [0, 1]
    for cycle in cycles:
        excerpts = read_source_spans(path, cycle.source_spans)
        assert len(excerpts) == 14
        assert all(f"GC({cycle.gc_id})" in excerpt.text for excerpt in excerpts)
        assert all(b.start_line - a.start_line == 2 for a, b in zip(cycle.source_spans, cycle.source_spans[1:]))


def test_unknown_concurrent_work_is_not_converted_to_pause_time(tmp_path):
    captured = CAPTURE.read_text().splitlines(keepends=True)
    path = tmp_path / "concurrent-future.log"
    # Negative future-format variation: deliberately not a supported log grammar.
    future = "[0.370s][info][gc,future] GC(99) Future Concurrent Work 100ms\n"
    path.write_text("".join(captured[:28] + [future] + captured[28:40]))
    pipeline, output, cycles = run(path)
    assert len(cycles) == 1
    assert cycles[0].total_pause_ns == 4735000
    assert cycles[0].concurrent_ns is None
    assert pipeline.coverage.unknown_gc_records == 1
    assert any(isinstance(item, UnknownGCRecord) for item in output)
    assert all(span.start_line != 29 for span in cycles[0].source_spans)


def test_unknown_future_tag_preserves_preview_and_event_evidence(tmp_path):
    captured = CAPTURE.read_text().splitlines(keepends=True)
    # Explicitly injected malformed/future variation, not a supported syntax claim.
    future = "[0.370s][info][gc,future] GC(0) " + "λ" * 2000 + "\n"
    path = tmp_path / "future.log"
    path.write_text("".join(captured[:28] + [future] + captured[28:]))
    pipeline, output, cycles = run(path, preview_bytes=64)
    unknowns = [item for item in output if isinstance(item, UnknownGCRecord)]
    assert len(unknowns) == pipeline.coverage.unknown_gc_records == 1
    assert len(unknowns[0].text_preview.encode()) <= 64
    assert unknowns[0].source_spans[0].start_line == 29
    assert len(unknowns[0].signature) == 24
    assert WarningCode.UNKNOWN_GC_RECORD.value in cycles[0].warning_codes
    assert 29 in [span.start_line for span in cycles[0].source_spans]
    assert cycles[0].total_pause_ns == 4735000


def test_malformed_duration_does_not_abort_later_events(tmp_path):
    captured = CAPTURE.read_text()
    path = tmp_path / "malformed.log"
    path.write_text(captured.replace("4.735ms", "NaNms", 1))
    pipeline, output, cycles = run(path)
    assert pipeline.coverage.unknown_gc_records == 1
    assert pipeline.coverage.parsed_events == 6
    incomplete = next(cycle for cycle in cycles if cycle.gc_id == 0)
    assert incomplete.total_pause_ns is None
    assert WarningCode.TRUNCATED_EVENT.value in incomplete.warning_codes
    assert next(cycle for cycle in cycles if cycle.gc_id == 5).total_pause_ns == 4711000


def test_truncated_event_and_missing_cpu_are_not_fabricated(tmp_path):
    captured = CAPTURE.read_text().splitlines(keepends=True)
    path = tmp_path / "truncated.log"
    path.write_text("".join(captured[:30]))
    pipeline, _, cycles = run(path)
    assert pipeline.coverage.incomplete_events == 1
    assert cycles[0].total_pause_ns is None
    assert cycles[0].operations[0].heap is None
    assert cycles[0].operations[0].cpu is None
    assert cycles[0].concurrent_ns is None
    path.write_text("".join(captured[:39]))  # summary is complete; CPU logging may be absent
    _, _, cycles = run(path)
    assert cycles[0].total_pause_ns == 4735000
    assert cycles[0].operations[0].cpu is None


def test_no_timestamp_decorators_leave_absolute_and_relative_times_unknown(tmp_path):
    import re

    path = tmp_path / "no-time.log"
    path.write_text(re.sub(r"\[[0-9.]+s\]", "", CAPTURE.read_text()))
    _, _, cycles = run(path)
    assert len(cycles) == 6
    for cycle in cycles:
        assert cycle.uptime_start_ns is None
        assert cycle.uptime_end_ns is None
        assert cycle.elapsed_ns is None
        assert cycle.wall_clock_start is None
        assert cycle.total_pause_ns is not None


def test_invalid_encoding_warns_on_exact_line_and_continues(tmp_path):
    path = tmp_path / "encoding.log"
    path.write_bytes(CAPTURE.read_bytes().replace(b"CPUs: 10", b"CPUs: \xff10", 1))
    pipeline, output, cycles = run(path)
    warnings = [item for item in output if isinstance(item, ParseWarning)]
    assert len(cycles) == 6
    assert pipeline.source.encoding_warning_count == 1
    assert len(warnings) == 1
    assert warnings[0].code == WarningCode.ENCODING_WARNING
    assert warnings[0].source_spans[0].start_line == 4


def test_unknown_decorators_and_near_miss_records():
    parser = UnifiedParser()
    record = parser.parse_line(line("[hostname][0.369s][info][gc,start] GC(0) Pause Young (Normal) (G1 Evacuation Pause)"))
    assert record.kind == RecordKind.START
    assert record.unknown_decorators == ("hostname",)
    for text in ("[1s][info][gc,cpu] GC(0) User=NaNs Sys=0.0s Real=0.0s",
                 "[1s][info][gc,heap] GC(0) Eden regions: " + "9" * 5000 + "->0(1)",
                 "[1s][info][gc,phases] GC(0) Unsupported Phase: 2ms",
                 "[1s][info][gc,broken GC(0) incomplete"):
        assert parser.parse_line(line(text)).kind == RecordKind.UNKNOWN
    assert parser.parse_line(line("application GC(0) message")).kind == RecordKind.APPLICATION
    assert parser.parse_line(line("  ")).kind == RecordKind.BLANK


def test_decorator_retention_is_bounded_and_source_backed():
    parser = UnifiedParser()
    assembler = G1Assembler(decorator_preview_bytes=32)
    text = "[" + "host" * 10000 + "][0.369s][info][gc,start] GC(0) Pause Young (Normal) (G1 Evacuation Pause)"
    record = parser.parse_line(line(text))
    assert len(record.unknown_decorators[0]) == 40000
    list(assembler.accept(record))
    cycle = next(item for item in assembler.finish() if isinstance(item, GCCycle))
    attributes = cycle.operations[0].attributes
    key = next(name for name in attributes if name.startswith("unified.unknown_decorators_preview"))
    assert len(attributes[key].encode()) <= 32
    assert cycle.operations[0].value_provenance[key].source_spans == record.source_spans


def test_unsupported_collector_does_not_get_g1_semantics(tmp_path):
    path = tmp_path / "unsupported.log"
    path.write_text(CAPTURE.read_text().replace("Using G1", "Using UnknownFutureCollector", 1))
    pipeline, output, cycles = run(path)
    assert not cycles
    assert pipeline.coverage.unknown_gc_records == 120
    report = next(item for item in output if isinstance(item, SegmentReport))
    assert report.detection["selected"] is None
    assert report.detection["status"] == "unknown"
    assert pipeline.coverage.ambiguous_records == 0


def test_bounded_assembler_emits_evicted_state_with_diagnostics():
    parser = UnifiedParser()
    assembler = G1Assembler(max_open_cycles=2, max_records_per_cycle=2)
    start = CAPTURE.read_text().splitlines()[26]
    results = []
    for identifier in range(10):
        results.extend(assembler.accept(parser.parse_line(line(start.replace("GC(0)", f"GC({identifier})")))))
        assert len(assembler.open_cycles) <= 2
    results.extend(assembler.finish())
    cycles = [item for item in results if isinstance(item, GCCycle)]
    assert len(cycles) == 10
    assert all(WarningCode.TRUNCATED_EVENT.value in cycle.warning_codes for cycle in cycles)
    assembler = G1Assembler(max_records_per_cycle=1)
    list(assembler.accept(parser.parse_line(line(start))))
    results = list(assembler.accept(parser.parse_line(line(start))))
    assert any(isinstance(item, GCCycle) for item in results)
    assert len(assembler.open_cycles) == 1


def test_concatenated_runs_keep_ids_and_spans_segment_local(tmp_path):
    path = tmp_path / "concatenated.log"
    path.write_bytes(CAPTURE.read_bytes() * 2)
    pipeline, output, cycles = run(path)
    assert pipeline.coverage.segments_detected == 2
    assert pipeline.coverage.total_physical_lines == 240
    assert len(cycles) == 12
    assert {cycle.segment_id for cycle in cycles[:6]} == {1}
    assert {cycle.segment_id for cycle in cycles[6:]} == {2}
    assert len({cycle.cycle_key for cycle in cycles}) == 12
    assert all(report.validation_accepted for report in output if isinstance(report, SegmentReport))
    assert cycles[6].source_spans[0].start_line == 147


def test_gzip_pipeline_one_pass_and_no_unsafe_raw_seek(tmp_path, monkeypatch):
    path = tmp_path / "input.txt"
    path.write_bytes(gzip.compress(CAPTURE.read_bytes()))
    original, calls = gzip.GzipFile, []

    def tracked(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(gzip, "GzipFile", tracked)
    pipeline, _, cycles = run(path)
    assert len(calls) == 1
    assert len(cycles) == 6
    assert pipeline.coverage.unknown_gc_records == 0
    assert cycles[0].source_spans[0].byte_span.space == OffsetSpace.EXPANDED_STREAM
    with pytest.raises(InputOpenError, match="cannot seek original gzip"):
        read_source_spans(path, cycles[0].source_spans)


def test_source_navigation_bounds_and_identity():
    _, _, cycles = run(CAPTURE)
    with pytest.raises(ResourceLimitError):
        read_source_spans(CAPTURE, cycles[0].source_spans, max_bytes=1)
    with pytest.raises(InputOpenError):
        read_source_spans(CAPTURE, (SourceSpan("wrong", byte_span=ByteSpan(0, 1, OffsetSpace.ORIGINAL_FILE)),))
    outside = SourceSpan(str(CAPTURE.resolve()), byte_span=ByteSpan(10**8, 10**8 + 1, OffsetSpace.ORIGINAL_FILE))
    with pytest.raises(InputOpenError):
        read_source_spans(CAPTURE, (outside,))


def test_parser_validation_uses_unknown_gc_records_not_application_lines(tmp_path):
    path = tmp_path / "collapse.log"
    path.write_text(CAPTURE.read_text() + "[1s][info][gc,future] GC(99) unknown\n" * 200
                    + "application message\n" * 1000)
    pipeline, output, _ = run(path)
    report = next(item for item in output if isinstance(item, SegmentReport))
    assert report.validation_accepted is False
    assert report.detection["status"] == "ambiguous"
    assert report.detection["selected"] is None
    assert pipeline.coverage.unknown_gc_records == 200
    assert pipeline.coverage.application_lines == 1000
    assert any(isinstance(item, ParseWarning) and item.code == WarningCode.AMBIGUOUS_GRAMMAR for item in output)


def test_recognition_collapse_stops_semantic_assembly_before_eof(tmp_path):
    path = tmp_path / "early-collapse.log"
    captured = CAPTURE.read_text().splitlines(keepends=True)
    path.write_text("".join(captured) + "[1s][info][gc,future] GC(99) unknown\n" * 400
                    + "".join(captured[26:115]))
    pipeline, output, cycles = run(path)
    assert len(cycles) == 6  # Valid-looking later records are not fed to a failed parser.
    assert pipeline.coverage.unknown_gc_records == 489
    report = next(item for item in output if isinstance(item, SegmentReport))
    assert report.validation_accepted is False
    assert report.detection["reason_code"] == "PARSER_VALIDATION_FAILED"
    warnings = [item for item in output if isinstance(item, ParseWarning) and item.code == WarningCode.AMBIGUOUS_GRAMMAR]
    assert len(warnings) == 1


def test_parse_cli_streams_canonical_json_lines():
    result = subprocess.run([sys.executable, "-c", "from gcviewer.cli import main; main()", "parse", str(CAPTURE)],
                            capture_output=True, text=True, check=True)
    entries = [json.loads(value) for value in result.stdout.splitlines()]
    assert sum(item["kind"] == "GCCycle" for item in entries) == 6
    assert entries[-1]["kind"] == "Coverage"
    assert entries[-1]["data"]["unknown_gc_records"] == 0
    assert all(item["schema_version"] == 1 for item in entries)


def test_streaming_many_runs_has_bounded_memory(tmp_path):
    path = tmp_path / "many.log"
    path.write_bytes(CAPTURE.read_bytes() * 100)
    pipeline = UnifiedPipeline(InputSource(path))
    tracemalloc.start()
    try:
        count = sum(isinstance(item, GCCycle) for item in pipeline.stream())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert count == 600
    assert pipeline.coverage.segments_detected == 100
    assert peak < 8 * 1024**2


@given(st.text(max_size=1000))
def test_parser_arbitrary_lines_do_not_raise(text):
    record = UnifiedParser().parse_line(line(text))
    assert isinstance(record, ParsedRecord)
    json.dumps(asdict(record))
