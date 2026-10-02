"""Stable diagnostics shared by parsing, persistence, and presentation."""

from dataclasses import dataclass
from enum import StrEnum

from .provenance import SourceSpan


class WarningCode(StrEnum):
    ENCODING_WARNING = "ENCODING_WARNING"
    UNKNOWN_GC_RECORD = "UNKNOWN_GC_RECORD"
    TRUNCATED_EVENT = "TRUNCATED_EVENT"
    TRUNCATED_XML = "TRUNCATED_XML"
    AMBIGUOUS_GRAMMAR = "AMBIGUOUS_GRAMMAR"
    CONTRADICTORY_METADATA = "CONTRADICTORY_METADATA"
    UNSUPPORTED_RECORD = "UNSUPPORTED_RECORD"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"


@dataclass(frozen=True, slots=True)
class ParseWarning:
    code: WarningCode
    detail: str | None = None
    source_spans: tuple[SourceSpan, ...] = ()
    segment_id: int | None = None


class GCViewerError(Exception):
    """Base for controlled application failures."""


class InputOpenError(GCViewerError):
    pass


class UnsupportedCompressionError(GCViewerError):
    pass


class UnknownGrammarError(GCViewerError):
    pass


class ResourceLimitError(GCViewerError):
    pass


class PrimitiveParseError(GCViewerError, ValueError):
    pass
