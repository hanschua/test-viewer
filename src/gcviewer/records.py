"""Grammar records: syntax parsing is independent of canonical event assembly."""

from dataclasses import dataclass, field
from enum import StrEnum

from .models import CpuTimes, MemoryUsage, ScalarValue, WallClockValue
from .provenance import SourceSpan


class RecordKind(StrEnum):
    BLANK = "blank"
    APPLICATION = "application"
    METADATA = "metadata"
    COLLECTOR = "collector"
    START = "start"
    END = "end"
    PHASE_START = "phase_start"
    PHASE_END = "phase_end"
    MEMORY = "memory"
    ATTRIBUTE = "attribute"
    CPU = "cpu"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class ParsedRecord:
    kind: RecordKind
    source_spans: tuple[SourceSpan, ...]
    gc_id: int | None = None
    uptime_ns: int | None = None
    wall_clock: WallClockValue | None = None
    name: str | None = None
    duration_ns: int | None = None
    heap: MemoryUsage | None = None
    pools: dict[str, MemoryUsage] = field(default_factory=dict)
    cpu: CpuTimes | None = None
    attributes: dict[str, ScalarValue] = field(default_factory=dict)
    decorators: tuple[str, ...] = ()
    unknown_decorators: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    level: str | None = None


@dataclass(frozen=True, slots=True)
class UnknownGCRecord:
    segment_id: int
    source_spans: tuple[SourceSpan, ...]
    reason_code: str
    signature: str
    text_preview: str
    gc_like: bool = True
