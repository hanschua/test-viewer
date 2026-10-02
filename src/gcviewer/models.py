"""Canonical contracts. Missing data stays None; time is integer nanoseconds."""

from dataclasses import dataclass, field
from enum import StrEnum

from .provenance import Provenance, SourceSpan, ValueSource

ScalarValue = int | float | str | bool | None


class Vendor(StrEnum):
    ORACLE = "oracle"
    IBM = "ibm"
    HPE = "hpe"
    AZUL = "azul"
    SUN = "sun"
    BEA = "bea"
    RED_HAT = "red_hat"
    ADOPTIUM = "adoptium"
    AMAZON = "amazon"
    MICROSOFT = "microsoft"
    SAP = "sap"
    UNKNOWN = "unknown"


class VMFamily(StrEnum):
    HOTSPOT = "hotspot"
    JROCKIT = "jrockit"
    J9_OPENJ9 = "j9_openj9"
    AZUL_PRIME = "azul_prime"
    SUN_CLASSIC = "sun_classic"
    UNKNOWN = "unknown"


class Collector(StrEnum):
    SERIAL = "serial"
    PARALLEL = "parallel"
    CMS = "cms"
    G1 = "g1"
    ZGC = "zgc"
    SHENANDOAH = "shenandoah"
    OPENJ9_GENCON = "openj9_gencon"
    OPENJ9_BALANCED = "openj9_balanced"
    OPENJ9_OPTTHRUPUT = "openj9_optthruput"
    OPENJ9_OPTAVGPAUSE = "openj9_optavgpause"
    OPENJ9_METRONOME = "openj9_metronome"
    OPENJ9_NOGC = "openj9_nogc"
    OPENJ9_OTHER = "openj9_other"
    AZUL_C4 = "azul_c4"
    JROCKIT = "jrockit"
    UNKNOWN = "unknown"


class LogGrammar(StrEnum):
    HOTSPOT_LEGACY = "hotspot_legacy"
    HOTSPOT_UNIFIED = "hotspot_unified"
    JROCKIT_VERBOSE = "jrockit_verbose"
    OPENJ9_XML = "openj9_xml"
    OPENJ9_DEPRECATED = "openj9_deprecated"
    AZUL_PRIME_LEGACY = "azul_prime_legacy"
    AZUL_PRIME_UNIFIED = "azul_prime_unified"
    SUN_LEGACY_UNKNOWN = "sun_legacy_unknown"
    UNKNOWN = "unknown"


class CapabilityState(StrEnum):
    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    FORMAT_COMPATIBLE = "format_compatible"
    EXPERIMENTAL = "experimental"
    NOT_TESTED = "not_tested"
    NOT_APPLICABLE = "not_applicable"
    UNSUPPORTED = "unsupported"


class ExecutionMode(StrEnum):
    STOP_THE_WORLD = "stop_the_world"
    CONCURRENT = "concurrent"
    UNKNOWN = "unknown"


class CycleType(StrEnum):
    YOUNG_COLLECTION = "young_collection"
    MIXED_COLLECTION = "mixed_collection"
    FULL_COLLECTION = "full_collection"
    CONCURRENT_CYCLE = "concurrent_cycle"
    UNKNOWN = "unknown"


class OperationType(StrEnum):
    COLLECTION = "collection"
    MARK = "mark"
    REMARK = "remark"
    CLEANUP = "cleanup"
    EVACUATION = "evacuation"
    ALLOCATION_STALL = "allocation_stall"
    UNKNOWN = "unknown"


def _validate_times(*values: int | None) -> None:
    for value in values:
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("canonical relative time must be nonnegative integer ns")


@dataclass(frozen=True, slots=True)
class JavaVersion:
    feature: int
    raw: str
    update: int | None = None
    build: int | None = None
    interim: int | None = None
    patch: int | None = None


@dataclass(slots=True)
class RuntimeIdentity:
    vendor: Vendor = Vendor.UNKNOWN
    vm_family: VMFamily = VMFamily.UNKNOWN
    java_version: JavaVersion | None = None
    collector: Collector = Collector.UNKNOWN
    log_grammar: LogGrammar = LogGrammar.UNKNOWN
    vendor_raw: str | None = None
    runtime_name_raw: str | None = None
    runtime_version_raw: str | None = None
    build_info_raw: str | None = None
    collector_mode: dict[str, ScalarValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WallClockValue:
    epoch_ns: int | None
    utc_offset_minutes: int | None
    raw: str | None


@dataclass(slots=True)
class MemoryUsage:
    before_bytes: int | None = None
    after_bytes: int | None = None
    used_bytes: int | None = None
    committed_bytes: int | None = None
    capacity_bytes: int | None = None
    reserved_bytes: int | None = None
    max_bytes: int | None = None


@dataclass(slots=True)
class CpuTimes:
    user_ns: int | None = None
    system_ns: int | None = None
    real_ns: int | None = None


@dataclass(slots=True)
class GCPhase:
    phase_key: str
    name: str
    execution_mode: ExecutionMode = ExecutionMode.UNKNOWN
    start_ns: int | None = None
    end_ns: int | None = None
    duration_ns: int | None = None
    source_spans: tuple[SourceSpan, ...] = ()
    attributes: dict[str, ScalarValue] = field(default_factory=dict)
    value_provenance: dict[str, Provenance] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_times(self.start_ns, self.end_ns, self.duration_ns)


@dataclass(slots=True)
class GCOperation:
    operation_key: str
    cycle_key: str
    operation_type: OperationType = OperationType.UNKNOWN
    execution_mode: ExecutionMode = ExecutionMode.UNKNOWN
    uptime_start_ns: int | None = None
    uptime_end_ns: int | None = None
    duration_ns: int | None = None
    source_spans: tuple[SourceSpan, ...] = ()
    heap: MemoryUsage | None = None
    young: MemoryUsage | None = None
    old: MemoryUsage | None = None
    metaspace: MemoryUsage | None = None
    compressed_class_space: MemoryUsage | None = None
    permgen: MemoryUsage | None = None
    phases: list[GCPhase] = field(default_factory=list)
    cpu: CpuTimes | None = None
    attributes: dict[str, ScalarValue] = field(default_factory=dict)
    warning_codes: list[str] = field(default_factory=list)
    value_provenance: dict[str, Provenance] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_times(self.uptime_start_ns, self.uptime_end_ns, self.duration_ns)


@dataclass(slots=True)
class GCCycle:
    cycle_key: str
    segment_id: int
    gc_id: int | str | None = None
    source_spans: tuple[SourceSpan, ...] = ()
    uptime_start_ns: int | None = None
    uptime_end_ns: int | None = None
    wall_clock_start: WallClockValue | None = None
    wall_clock_end: WallClockValue | None = None
    collector: Collector = Collector.UNKNOWN
    cycle_type: CycleType = CycleType.UNKNOWN
    cause: str | None = None
    operations: list[GCOperation] = field(default_factory=list)
    elapsed_ns: int | None = None
    total_pause_ns: int | None = None
    concurrent_ns: int | None = None
    aggregate_source: ValueSource = field(default=ValueSource.DERIVED, init=False)
    attributes: dict[str, ScalarValue] = field(default_factory=dict)
    warning_codes: list[str] = field(default_factory=list)
    value_provenance: dict[str, Provenance] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_times(self.uptime_start_ns, self.uptime_end_ns, self.elapsed_ns,
                        self.total_pause_ns, self.concurrent_ns)
        if any(op.cycle_key != self.cycle_key for op in self.operations):
            raise ValueError("operation belongs to a different cycle")
