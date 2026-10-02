import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from gcviewer.corpus import load_manifest
from gcviewer.detection import (
    DetectionCandidate, DetectionPolicy, DetectionStatus, DetectorRegistry,
    Evidence, HotSpotUnifiedDetector, RecognitionCounts, default_registry,
    select_candidate, validate_selection,
)
from gcviewer.ingest import InputLimits, InputSource, SourceLine, line_lookahead
from gcviewer.models import Collector, LogGrammar, Vendor, VMFamily
from gcviewer.provenance import OffsetSpace

ROOT = Path(__file__).parents[1]


def sample(*texts):
    lines, offset = [], 0
    for number, text in enumerate(texts, 1):
        size = len(text.encode()) + 1
        lines.append(SourceLine(number, offset, text, "test", size, "lf", OffsetSpace.ORIGINAL_FILE))
        offset += size
    return tuple(lines)


def candidate(grammar, score):
    return DetectionCandidate(grammar, evidence=(Evidence("TEST_GRAMMAR", score, "test-only evidence"),))


def test_captured_fixture_detection_golden_and_provenance():
    directory = ROOT / "corpus/detection"
    entries = load_manifest(directory / "manifest.json")
    assert entries
    for entry in entries:
        source = InputSource(directory / entry.path)
        lines = source.iter_lines()
        try:
            buffered, _ = line_lookahead(lines, InputLimits())
            result = default_registry().detect(buffered)
        finally:
            lines.close()
        golden = json.loads((directory / entry.golden_path).read_text())
        selected = result.selected
        assert selected is not None
        assert result.status == golden["status"]
        for name in ("grammar", "vm_family", "vendor", "collector"):
            assert getattr(selected, name) == golden[name]
        assert selected.java_version.feature == golden["java_feature"]
        assert selected.java_version.raw == golden["java_version_raw"]
        assert selected.score == pytest.approx(golden["score"])
        assert [item.code for item in selected.evidence] == golden["evidence_codes"]
        assert list(selected.contradictions) == golden["contradictions"]
        with source.path.open("rb") as stream:
            for item in selected.evidence:
                span = item.source_spans[0]
                stream.seek(span.byte_span.start)
                raw = stream.read(span.byte_span.end - span.byte_span.start).decode().rstrip("\r\n")
                assert raw == buffered[item.line_number - 1].text
        assert entry.expected_unknown_gc_records is None  # no fabricated coverage
        assert entry.capability_state.value != "verified"


def test_ambiguous_candidates_do_not_use_display_order_as_tiebreaker():
    a = candidate(LogGrammar.HOTSPOT_UNIFIED, 0.72)
    b = candidate(LogGrammar.AZUL_PRIME_UNIFIED, 0.68)
    for values in ((a, b), (b, a)):
        result = select_candidate(values)
        assert result.status == DetectionStatus.AMBIGUOUS
        assert result.selected is None
        assert result.candidates[0].grammar == LogGrammar.HOTSPOT_UNIFIED
    tied = select_candidate((candidate(LogGrammar.HOTSPOT_UNIFIED, 0.8),
                             candidate(LogGrammar.AZUL_PRIME_UNIFIED, 0.8)))
    assert tied.status == DetectionStatus.AMBIGUOUS


def test_ranking_fixture_goldens():
    fixture = json.loads((ROOT / "corpus/detection/ranking-cases.json").read_text())
    for case in fixture["cases"]:
        candidates = tuple(candidate(LogGrammar(item["grammar"]), item["score"])
                           for item in case["candidates"])
        for ordering in (candidates, candidates[::-1]):
            result = select_candidate(ordering)
            assert result.status == case["expected_status"], case["id"]
            assert (result.selected.grammar if result.selected else None) == case["expected_selected"]


def test_unknown_and_insufficient_evidence():
    assert DetectorRegistry().detect(()).reason_code == "NO_GRAMMAR_EVIDENCE"
    assert default_registry().detect(sample("application GC(7) hello")).selected is None
    # Generic unified-shaped syntax is shared with other implementations.
    result = default_registry().detect(sample("[1s][info][gc] GC(7) future payload"))
    assert result.status == DetectionStatus.UNKNOWN
    assert result.reason_code == "INSUFFICIENT_GRAMMAR_EVIDENCE"


@pytest.mark.parametrize("text", ['openjdk version "17"', 'java version "1.8.0_382"',
                                  "Oracle Corporation", "Azul Systems, Inc.", "Eclipse OpenJ9",
                                  "OpenJDK 64-Bit Server VM"])
def test_identity_alone_cannot_choose_grammar(text):
    result = default_registry().detect(sample(text))
    assert result.selected is None
    assert all(item.score == 0 for item in result.candidates)


def test_java_version_is_metadata_not_score_and_vendor_is_not_inferred():
    base = sample("[1s][info][gc] Using G1")
    a = default_registry().detect(base)
    b = default_registry().detect(base + sample("[1s][info][gc,init] Version: 25.0.3+9-LTS (release)"))
    assert a.selected.score == b.selected.score
    assert b.selected.vendor == Vendor.UNKNOWN
    assert b.selected.vm_family == VMFamily.HOTSPOT
    assert b.selected.collector == Collector.G1


def test_conflicts_retain_diagnostic_evidence_and_cannot_select():
    result = default_registry().detect(sample("[1s][info][gc] Using G1",
                                              "[1s][info][gc,init] Version: 25.0.3+9-LTS (release)",
                                              "[1s][info][gc,init] Version: 17.0.12+7 (release)"))
    assert result.status == DetectionStatus.AMBIGUOUS
    assert result.selected is None
    assert result.candidates[0].java_version is None
    assert result.candidates[0].contradictions[0].code == "CONFLICTING_VERSION"
    diagnostics = json.loads(json.dumps(result.to_dict()))
    assert diagnostics["candidates"][0]["score"] == pytest.approx(0.75)
    assert diagnostics["candidates"][0]["contradictions"][0]["source_spans"]


def test_registry_contracts():
    detector = HotSpotUnifiedDetector()
    registry = DetectorRegistry((detector,))
    with pytest.raises(ValueError, match="unique"):
        registry.register(detector)

    class WrongDetector:
        grammar = LogGrammar.HOTSPOT_LEGACY

        def probe(self, sample):
            return candidate(LogGrammar.HOTSPOT_UNIFIED, 0.9)

    with pytest.raises(ValueError, match="wrong grammar"):
        DetectorRegistry((WrongDetector(),)).detect(())


def test_conflicting_collector_declarations_are_not_hidden():
    result = default_registry().detect(sample("[1s][info][gc] Using G1",
                                              "[2s][info][gc] Using UnknownFutureCollector"))
    assert result.selected is None
    assert result.candidates[0].collector == Collector.UNKNOWN
    assert result.candidates[0].contradictions[0].code == "CONFLICTING_COLLECTOR"


def test_duplicate_evidence_weight_is_order_independent():
    a = Evidence("SIGNAL", 0.2, "weak")
    b = Evidence("SIGNAL", 0.4, "strong")
    for items in ((a, b), (b, a)):
        assert DetectionCandidate(LogGrammar.HOTSPOT_UNIFIED, evidence=items).score == 0.4


def test_detection_replays_captured_gzip_without_second_decompression(tmp_path, monkeypatch):
    original_bytes = (ROOT / "corpus/detection/jdk25-g1.log").read_bytes()
    path = tmp_path / "captured.txt"
    path.write_bytes(gzip.compress(original_bytes))
    calls = []
    original = gzip.GzipFile

    def tracked(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(gzip, "GzipFile", tracked)
    limits = InputLimits(lookahead_records=4)
    source = InputSource(path, limits)
    buffered, replay = line_lookahead(source.iter_lines(), limits)
    result = default_registry().detect(buffered)
    assert result.selected.grammar == LogGrammar.HOTSPOT_UNIFIED
    # Consume the same source as a later parser would, including the prefix.
    assert [line.text for line in replay] == original_bytes.decode().splitlines()
    assert len(calls) == 1
    assert source.expanded_bytes == len(original_bytes)
    assert result.selected.evidence[0].source_spans[0].byte_span.space == OffsetSpace.EXPANDED_STREAM


def test_post_selection_validation_and_retry_suggestions():
    initial = select_candidate((candidate(LogGrammar.HOTSPOT_UNIFIED, 0.91),
                                candidate(LogGrammar.AZUL_PRIME_UNIFIED, 0.62)))
    assert validate_selection(initial, RecognitionCounts(496, 4)).accepted is True
    failed = validate_selection(initial, RecognitionCounts(90, 410))
    assert failed.accepted is False
    assert failed.detection.status == DetectionStatus.AMBIGUOUS
    assert failed.detection.selected is None
    assert failed.retry_grammars == (LogGrammar.AZUL_PRIME_UNIFIED,)
    original = next(item for item in failed.detection.candidates if item.grammar == LogGrammar.HOTSPOT_UNIFIED)
    assert original.contradictions[-1].code == "PARSER_RECOGNITION_COLLAPSE"
    assert initial.selected.score == 0.91  # no mutation of original evidence
    assert validate_selection(initial, RecognitionCounts(0, 0)).accepted is None
    assert validate_selection(initial, RecognitionCounts(1, 0)).reason_code == "INSUFFICIENT_VALIDATION_RECORDS"
    assert RecognitionCounts(0, 0).ratio is None


def test_validation_policy_and_invalid_counts():
    initial = select_candidate((candidate(LogGrammar.HOTSPOT_UNIFIED, 0.9),))
    assert validate_selection(initial, RecognitionCounts(16, 4)).accepted is True
    assert validate_selection(initial, RecognitionCounts(15, 5)).accepted is False
    policy = DetectionPolicy(validation_minimum_records=4, validation_minimum_ratio=0.5)
    assert validate_selection(initial, RecognitionCounts(2, 2), policy).accepted is True
    for value in (-1, True, 1.5):
        with pytest.raises(ValueError):
            RecognitionCounts(value, 0)
    with pytest.raises(ValueError):
        DetectionPolicy(minimum_score=float("nan"))


@given(st.integers(min_value=1, max_value=1000))
def test_repeated_evidence_does_not_inflate_score(repetitions):
    proof = Evidence("ONE_SIGNAL", 0.55, "same marker")
    assert DetectionCandidate(LogGrammar.HOTSPOT_UNIFIED, evidence=(proof,) * repetitions).score == 0.55


@given(st.text(max_size=1000))
def test_arbitrary_sample_has_bounded_and_serializable_diagnostics(text):
    result = default_registry().detect(sample(text))
    assert all(0 <= item.score <= 1 for item in result.candidates)
    json.dumps(result.to_dict())


def test_inspect_cli_and_controlled_errors():
    command = [sys.executable, "-c", "from gcviewer.cli import main; main()", "inspect"]
    result = subprocess.run(command + [str(ROOT / "corpus/detection/jdk25-g1.log")],
                            capture_output=True, text=True, check=True)
    value = json.loads(result.stdout)
    assert value["scope"] == "initial_sample"
    assert value["sample_records"] <= 256
    assert value["detection"]["selected"]["score"] == pytest.approx(0.9)
    failed = subprocess.run(command + ["/nonexistent/gc.log"], capture_output=True, text=True)
    assert failed.returncode == 2
    assert "Traceback" not in failed.stderr
    failed = subprocess.run(command + [str(ROOT / "corpus/detection/jdk25-g1.log"), "--sample-records", "0"],
                            capture_output=True, text=True)
    assert failed.returncode == 2
    assert "positive integers" in failed.stderr
