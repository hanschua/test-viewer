"""Evidence-based grammar selection. Scores are heuristics, not probabilities."""

import math
import re
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import Protocol

from .errors import PrimitiveParseError
from .ingest import SourceLine
from .models import Collector, JavaVersion, LogGrammar, Vendor, VMFamily
from .primitives import parse_unified_envelope
from .provenance import ByteSpan, SourceSpan
from .versions import parse_java_version


class DetectionStatus(StrEnum):
    SELECTED = "selected"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Evidence:
    code: str
    weight: float
    description: str
    line_number: int | None = None
    source_spans: tuple[SourceSpan, ...] = ()

    def __post_init__(self) -> None:
        if not self.code or not math.isfinite(self.weight) or not 0 <= self.weight <= 1:
            raise ValueError("evidence requires a code and finite weight in [0, 1]")


@dataclass(frozen=True, slots=True)
class DetectionCandidate:
    grammar: LogGrammar
    vm_family: VMFamily = VMFamily.UNKNOWN
    vendor: Vendor = Vendor.UNKNOWN
    collector: Collector = Collector.UNKNOWN
    java_version: JavaVersion | None = None
    evidence: tuple[Evidence, ...] = ()
    contradictions: tuple[Evidence, ...] = ()

    @property
    def score(self) -> float:
        # Repetition must not turn a weak marker into arbitrary certainty.
        positive, negative = {}, {}
        for items, weights in ((self.evidence, positive), (self.contradictions, negative)):
            for item in items:
                weights[item.code] = max(weights.get(item.code, 0.0), item.weight)
        return min(1.0, max(0.0, sum(positive.values()) - sum(negative.values())))


@dataclass(frozen=True, slots=True)
class DetectionPolicy:
    minimum_score: float = 0.7
    ambiguity_margin: float = 0.1
    validation_minimum_records: int = 20
    validation_minimum_ratio: float = 0.8

    def __post_init__(self) -> None:
        for value in (self.minimum_score, self.ambiguity_margin, self.validation_minimum_ratio):
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("detection thresholds must be finite values in [0, 1]")
        if type(self.validation_minimum_records) is not int or self.validation_minimum_records < 1:
            raise ValueError("validation sample size must be a positive integer")


@dataclass(frozen=True, slots=True)
class DetectionResult:
    status: DetectionStatus
    candidates: tuple[DetectionCandidate, ...]
    selected: DetectionCandidate | None
    reason_code: str

    def to_dict(self) -> dict:
        """JSON-ready diagnostics retain both evidence and computed scores."""
        data = asdict(self)
        for value, candidate in zip(data["candidates"], self.candidates):
            value["score"] = candidate.score
        if self.selected is not None:
            data["selected"]["score"] = self.selected.score
        return data


class Detector(Protocol):
    grammar: LogGrammar

    def probe(self, sample: tuple[SourceLine, ...]) -> DetectionCandidate: ...


def rank_candidates(candidates) -> tuple[DetectionCandidate, ...]:
    """Deterministic ordering is for display only; a tie cannot select a parser."""
    return tuple(sorted(candidates, key=lambda candidate: (-candidate.score, candidate.grammar.value)))


def select_candidate(candidates, policy: DetectionPolicy = DetectionPolicy()) -> DetectionResult:
    ranked = rank_candidates(candidates)
    if not ranked or ranked[0].score == 0:
        return DetectionResult(DetectionStatus.UNKNOWN, ranked, None, "NO_GRAMMAR_EVIDENCE")
    top = ranked[0]
    if top.score < policy.minimum_score:
        return DetectionResult(DetectionStatus.UNKNOWN, ranked, None, "INSUFFICIENT_GRAMMAR_EVIDENCE")
    if (len(ranked) > 1 and ranked[1].score > 0
            and top.score - ranked[1].score <= policy.ambiguity_margin + 1e-12):
        return DetectionResult(DetectionStatus.AMBIGUOUS, ranked, None, "AMBIGUOUS_GRAMMAR")
    if top.contradictions:
        return DetectionResult(DetectionStatus.AMBIGUOUS, ranked, None, "CONTRADICTORY_EVIDENCE")
    return DetectionResult(DetectionStatus.SELECTED, ranked, top, "GRAMMAR_SELECTED")


class DetectorRegistry:
    def __init__(self, detectors=()):
        self._detectors: dict[LogGrammar, Detector] = {}
        for detector in detectors:
            self.register(detector)

    def register(self, detector: Detector) -> None:
        if detector.grammar == LogGrammar.UNKNOWN or detector.grammar in self._detectors:
            raise ValueError("detector grammar must be known and unique")
        self._detectors[detector.grammar] = detector

    def detect(self, sample: tuple[SourceLine, ...], policy: DetectionPolicy = DetectionPolicy()):
        candidates = []
        for detector in self._detectors.values():
            candidate = detector.probe(sample)
            if candidate.grammar != detector.grammar:
                raise ValueError("detector returned the wrong grammar")
            candidates.append(candidate)
        return select_candidate(candidates, policy)


def _evidence(line: SourceLine, code: str, weight: float, detail: str) -> Evidence:
    span = SourceSpan(line.source_id, line.line_number, line.line_number,
                      ByteSpan(line.byte_offset, line.byte_offset + line.raw_length_bytes, line.offset_space))
    return Evidence(code, weight, detail, line.line_number, (span,))


GC_ID = re.compile(r"^GC\([0-9]+\)(?:\s|$)")


class HotSpotUnifiedDetector:
    """Only currently captured payload evidence earns family-specific confidence."""

    grammar = LogGrammar.HOTSPOT_UNIFIED

    def probe(self, sample: tuple[SourceLine, ...]) -> DetectionCandidate:
        evidence, contradictions = {}, {}
        collector, version = Collector.UNKNOWN, None
        declaration = None
        for line in sample:
            try:
                envelope = parse_unified_envelope(line.text)
            except PrimitiveParseError:
                continue
            if "gc" not in envelope.tags:
                continue
            evidence.setdefault("UNIFIED_GC_ENVELOPE", _evidence(
                line, "UNIFIED_GC_ENVELOPE", 0.55, "Bracketed envelope with gc tag"))
            if GC_ID.match(envelope.payload):
                evidence.setdefault("UNIFIED_GC_ID", _evidence(
                    line, "UNIFIED_GC_ID", 0.05, "GC(n) correlation identifier"))
            if envelope.payload == "Using G1":
                collector = Collector.G1
                evidence.setdefault("G1_DECLARATION", _evidence(
                    line, "G1_DECLARATION", 0.3, "Explicit G1 collector declaration"))
            if envelope.payload.startswith("Using "):
                if declaration is not None and declaration != envelope.payload:
                    contradictions.setdefault("CONFLICTING_COLLECTOR", _evidence(
                        line, "CONFLICTING_COLLECTOR", 0.3,
                        "Different collector startup declarations within one sample"))
                declaration = envelope.payload
            if "CONFLICTING_COLLECTOR" in contradictions:
                collector = Collector.UNKNOWN
            if envelope.payload.startswith("Version: ") and "init" in envelope.tags:
                raw = envelope.payload.removeprefix("Version: ").split(" ", 1)[0]
                try:
                    parsed = parse_java_version(raw)
                except PrimitiveParseError:
                    contradictions.setdefault("MALFORMED_VERSION", _evidence(
                        line, "MALFORMED_VERSION", 0.0, "Unrecognized startup version token"))
                    continue
                if version is not None and version != parsed:
                    contradictions.setdefault("CONFLICTING_VERSION", _evidence(
                        line, "CONFLICTING_VERSION", 0.1, "Different startup versions within one sample"))
                    version = None
                elif "CONFLICTING_VERSION" not in contradictions:
                    version = parsed
                    # Version metadata never increases a grammar score.
                    evidence.setdefault("JAVA_VERSION", _evidence(
                        line, "JAVA_VERSION", 0.0, "Logged runtime version token"))
        return DetectionCandidate(self.grammar, VMFamily.HOTSPOT, collector=collector,
                                  java_version=version, evidence=tuple(evidence.values()),
                                  contradictions=tuple(contradictions.values()))


def default_registry() -> DetectorRegistry:
    return DetectorRegistry((HotSpotUnifiedDetector(),))


@dataclass(frozen=True, slots=True)
class RecognitionCounts:
    """Count logical GC-like records only; application output is not a denominator."""

    recognized: int
    unknown: int

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in (self.recognized, self.unknown)):
            raise ValueError("recognition counts must be nonnegative integers")

    @property
    def ratio(self) -> float | None:
        total = self.recognized + self.unknown
        return self.recognized / total if total else None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    detection: DetectionResult
    accepted: bool | None
    counts: RecognitionCounts
    retry_grammars: tuple[LogGrammar, ...]
    reason_code: str


def validate_selection(result: DetectionResult, counts: RecognitionCounts,
                       policy: DetectionPolicy = DetectionPolicy()) -> ValidationResult:
    """Flag a failed parser; never switch to another unvalidated parser silently."""
    if result.selected is None:
        return ValidationResult(result, None, counts, (), "NO_SELECTED_GRAMMAR")
    if counts.recognized + counts.unknown < policy.validation_minimum_records:
        return ValidationResult(result, None, counts, (), "INSUFFICIENT_VALIDATION_RECORDS")
    if counts.ratio >= policy.validation_minimum_ratio:
        return ValidationResult(result, True, counts, (), "PARSER_VALIDATED")
    failed = result.selected
    contradiction = Evidence("PARSER_RECOGNITION_COLLAPSE", 1.0,
                             f"Recognized {counts.recognized} of {counts.recognized + counts.unknown} GC-like records")
    failed = replace(failed, contradictions=failed.contradictions + (contradiction,))
    ranked = rank_candidates(failed if candidate.grammar == failed.grammar else candidate
                             for candidate in result.candidates)
    revised = DetectionResult(DetectionStatus.AMBIGUOUS, ranked, None, "PARSER_VALIDATION_FAILED")
    retries = tuple(candidate.grammar for candidate in ranked
                    if candidate.grammar != failed.grammar and candidate.score > 0)
    return ValidationResult(revised, False, counts, retries, "PARSER_VALIDATION_FAILED")
