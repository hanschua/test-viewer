"""Grammar-neutral references to the original source, without raw-log copies."""

from dataclasses import dataclass
from enum import StrEnum


class OffsetSpace(StrEnum):
    ORIGINAL_FILE = "original_file"
    EXPANDED_STREAM = "expanded_stream"
    WORKING_COPY = "working_copy"


class ValueSource(StrEnum):
    LOGGED = "logged"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    USER_SUPPLIED = "user_supplied"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ByteSpan:
    """Half-open byte range in an explicitly named coordinate system."""

    start: int
    end: int
    space: OffsetSpace

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("invalid byte range")


@dataclass(frozen=True, slots=True)
class SourceSpan:
    source_id: str
    start_line: int | None = None
    end_line: int | None = None
    byte_span: ByteSpan | None = None
    grammar_locator: str | None = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id must not be empty")
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("line range requires both endpoints")
        if self.start_line is not None:
            if self.start_line < 1 or self.end_line < self.start_line:
                raise ValueError("invalid inclusive line range")


@dataclass(frozen=True, slots=True)
class Provenance:
    source_spans: tuple[SourceSpan, ...] = ()
    derivation: str | None = None


@dataclass(frozen=True, slots=True)
class MetricValue:
    value: int | float | None
    source: ValueSource
    provenance: Provenance = Provenance()
