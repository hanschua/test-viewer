# Universal Java GC Viewer


## Revision notes

This revision incorporates the DuckDB/storage review and hardens the plan for weaker fully offline implementation models. In particular it uses integer-nanosecond canonical timing, grammar-neutral/multi-span provenance, sparse raw-source checkpoints, bounded unknown-record storage, one-pass gzip detection, and explicit DuckDB offline/resource/security rules.

## Reviewed Implementation Plan for an Offline Python/uv Implementation

**Audience:** a weaker implementation model working fully offline  
**Implementation language:** Python 3.12+  
**Package manager:** `uv`  
**Primary goal:** parse, normalize, analyze, and visualize Java garbage-collection logs across JVM families, Java releases, collectors, vendors, and logging configurations without requiring network access.

---

# 1. Review verdict

The original plan has the correct core architecture: **detect a log grammar, parse into a canonical model, then analyze the canonical model**. That is the right design for a universal GC viewer.

The following changes are required before implementation:

1. **Do not model support as `vendor × Java version × collector`.** Those dimensions are not independent. Use VM family, log grammar, collector, version, and vendor as separate metadata.
2. **Add Java 15.** The requested list skipped Java 15. The target should be Java 1.2 through Java 27 inclusive.
3. **Add per-segment detection.** A single input file may contain rotated logs concatenated together, multiple JVM runs, or mixed application output. Do not assume one parser applies to the entire file.
4. **Distinguish vendor from VM implementation.** For example, Azul Zulu and Azul Prime are both Azul products but have very different GC behavior and logging. Oracle historically shipped both HotSpot and JRockit.
5. **Treat JRockit as a separate VM/log family.** If Oracle support is claimed broadly, Oracle JRockit logs must not be silently parsed as HotSpot.
6. **Treat historical Sun JVMs separately where necessary.** Java 1.2-era through pre-Oracle Java logs may identify Sun rather than Oracle. Preserve the actual vendor instead of rewriting it to Oracle.
7. **Define “full support” as verified grammar support, not as a marketing claim.** A parser is supported only when real fixtures and golden expected results exist.
8. **Do not promise that every Java version/vendor/collector combination exists.** The capability matrix must support `NOT_APPLICABLE`.
9. **Preserve unknown JVM arguments exactly.** “All memory arguments” cannot realistically mean the program has semantic knowledge of every historical or vendor-specific flag. It should normalize known flags and preserve unknown flags losslessly.
10. **Separate parsing from semantic interpretation.** The grammar parser should parse what is written. Collector-specific code should interpret what those parsed records mean.
11. **Use a bounded-memory streaming design from the beginning.** Large production GC logs can be gigabytes.
12. **Add explicit provenance to every parsed value.** A user should be able to trace a chart point or warning back to exact source lines.
13. **Add ambiguity handling.** Detection should be evidence-based and may return multiple candidates rather than guessing.
14. **Add a capability registry.** The program should be able to report exactly which grammars, collectors, versions, and features were verified by the bundled test corpus.
15. **Add acceptance gates and performance targets.** A weaker implementation model needs objective rules for when a phase is finished.
16. **Use integer nanoseconds for canonical time/duration values.** Do not make floating-point seconds the authoritative representation.
17. **Use grammar-neutral, multi-span source provenance.** Concurrent GC records may be interleaved, and XML logical records do not map cleanly to one physical line range.
18. **Use sparse source checkpoints, not one database row per input line.** Exact source offsets belong on parsed records/events; line navigation uses bounded sparse checkpoints.
19. **Bound unknown-record persistence.** Store source spans, stable reason/signature fields, and a bounded preview rather than copying arbitrary raw input into DuckDB.
20. **Keep DuckDB analysis files application-owned.** Do not treat arbitrary external `.duckdb` files as safe GC-viewer documents. Disable network-dependent extension installation in normal offline operation.
21. **Configure resource limits explicitly.** Bound input record size, gzip expansion, DuckDB memory, worker threads, and temporary disk usage.
22. **Use one-pass bounded lookahead/replay detection.** Do not repeatedly decompress gzip input while probing candidate parsers.
23. **Treat `OpenJDK` and `Eclipse OpenJ9` as build/VM identity evidence, not automatically as runtime vendors.** Leave vendor/distributor unknown when the log does not identify it.

The rest of this document is the revised implementation plan.

---

# 2. Product objective

Create a fully offline application that can open Java GC logs, automatically identify the most likely JVM/log grammar, parse supported records, normalize them into one internal representation, calculate metrics, and display the result in a local viewer.

Typical usage:

```bash
uv run gcviewer analyze /path/to/gc.log
```

or:

```bash
uv run gcviewer serve /path/to/gc.log
```

The normal workflow must be:

```text
input file
    -> streaming reader
    -> segment discovery
    -> format/VM detector
    -> syntax parser
    -> event assembler
    -> semantic normalizer
    -> canonical event model
    -> DuckDB
    -> analytics
    -> local viewer
```

The program must work without:

- Internet access
- Cloud APIs
- External telemetry
- Remote JavaScript/CDN assets
- A running JVM
- A locally installed JDK

The user should not normally need to specify:

- Java version
- JVM vendor
- JVM implementation
- GC algorithm
- GC log format

Manual overrides may exist for ambiguous or damaged logs.

---

# 3. Scope

## 3.1 Java versions

Target Java versions:

```text
1.2
1.3
1.4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
21
22
23
24
25
26
27
```

Internally normalize versions into a structured model rather than comparing version strings lexicographically.

Example:

```python
JavaVersion(
    feature=8,
    update=382,
    build=5,
    raw="1.8.0_382-b05",
)
```

For historical version strings such as `1.4.2_19`, normalize the feature version to `4` but preserve the original text.

Never require a version to be detected. A GC log can be valid even when it contains no JVM startup header.

---

# 4. Supported dimensions must be modeled independently

Do **not** use this architecture:

```python
parser = PARSERS[vendor][java_version][collector]
```

That becomes unmaintainable and is factually incorrect for many vendor/version combinations.

Represent at least these dimensions:

```text
vendor/distributor, when actually known
VM implementation/family
Java version
log grammar
collector or GC policy
logging configuration
feature capabilities
raw runtime/build identity
```

`OpenJDK` by itself is not a vendor. A banner such as `OpenJDK 64-Bit Server VM` identifies a build family/VM implementation but may not identify who distributed the runtime. Keep vendor/distributor unknown unless there is explicit evidence.

Example metadata:

```python
RuntimeIdentity(
    vendor=Vendor.AZUL,
    vm_family=VMFamily.HOTSPOT,
    java_version=JavaVersion(feature=17),
    collector=Collector.G1,
    log_grammar=LogGrammar.HOTSPOT_UNIFIED,
)
```

versus:

```python
RuntimeIdentity(
    vendor=Vendor.AZUL,
    vm_family=VMFamily.AZUL_PRIME,
    java_version=JavaVersion(feature=17),
    collector=Collector.AZUL_C4,
    log_grammar=LogGrammar.AZUL_PRIME_UNIFIED,
)
```

These are both Azul runtimes but they are not the same parser target.

---

# 5. Vendor model

At minimum:

```python
class Vendor(Enum):
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
```

This enum represents the runtime vendor/distributor when the log actually provides enough evidence. It is not a list of VM implementations.

Important rules:

- Do not convert `Sun Microsystems` to `Oracle` internally.
- Do not convert `BEA JRockit` to HotSpot.
- Do not treat `OpenJDK` as a vendor/distributor.
- Do not treat `Eclipse OpenJ9` as a vendor; it identifies a VM implementation/project.
- If a log says only `OpenJDK 64-Bit Server VM`, normally record `vm_family=HOTSPOT` and leave `vendor=UNKNOWN`.
- Preserve exact detected vendor/build text in metadata.

Keep raw identity fields such as:

```python
vendor_raw: str | None
runtime_name_raw: str | None
runtime_version_raw: str | None
build_info_raw: str | None
```

Examples of raw strings include:

```text
Oracle Corporation
IBM Corporation
Eclipse OpenJ9
Azul Systems, Inc.
Sun Microsystems Inc.
OpenJDK 64-Bit Server VM
```

Do not force a raw runtime string into the wrong normalized enum merely to avoid `UNKNOWN`.

---

# 6. VM-family model

At minimum:

```python
class VMFamily(Enum):
    HOTSPOT = "hotspot"
    JROCKIT = "jrockit"
    J9_OPENJ9 = "j9_openj9"
    AZUL_PRIME = "azul_prime"
    SUN_CLASSIC = "sun_classic"
    UNKNOWN = "unknown"
```

Do not add a VM family merely because a vendor name exists. Add one when the implementation/log grammar is materially different.

Historical families should only be claimed as supported when actual fixtures exist.

---

# 7. Collector/policy model

## 7.1 HotSpot collectors

Required:

```text
Serial GC
Parallel GC / Parallel Scavenge
CMS
G1
ZGC
Shenandoah
```

Represent explicitly:

```python
SERIAL
PARALLEL
CMS
G1
ZGC
SHENANDOAH
```

Do not hide important generations/modes in a single string. Use attributes such as:

```python
collector_mode = {
    "generational": True,
    "mode": "...",
}
```

where the source log supports such a distinction.

## 7.2 OpenJ9 policies

OpenJ9 policies are not aliases for HotSpot collectors.

Represent at least:

```text
gencon
balanced
optthruput
optavgpause
metronome
nogc, where supported by the VM/log format
unknown OpenJ9 policy
```

Example enum values:

```python
OPENJ9_GENCON
OPENJ9_BALANCED
OPENJ9_OPTTHRUPUT
OPENJ9_OPTAVGPAUSE
OPENJ9_METRONOME
OPENJ9_NOGC
OPENJ9_OTHER
```

Never map:

```text
OpenJ9 gencon -> G1
OpenJ9 optthruput -> HotSpot Parallel GC
```

## 7.3 Azul Prime

Represent:

```python
AZUL_C4
```

Do not map C4 to ZGC, Shenandoah, G1, or another HotSpot collector.

## 7.4 JRockit

Do not force JRockit strategies into HotSpot collector names.

Represent its detected memory-management strategy separately, for example:

```python
JROCKIT
```

with attributes such as:

```python
strategy_raw = "Generational Parallel Mark & Sweep"
```

Only introduce finer JRockit strategy enums when fixtures demonstrate that they are useful and stable.

---

# 8. Log grammar model

At minimum:

```python
class LogGrammar(Enum):
    HOTSPOT_LEGACY = "hotspot_legacy"
    HOTSPOT_UNIFIED = "hotspot_unified"

    JROCKIT_VERBOSE = "jrockit_verbose"

    OPENJ9_XML = "openj9_xml"
    OPENJ9_DEPRECATED = "openj9_deprecated"

    AZUL_PRIME_LEGACY = "azul_prime_legacy"
    AZUL_PRIME_UNIFIED = "azul_prime_unified"

    SUN_LEGACY_UNKNOWN = "sun_legacy_unknown"
    UNKNOWN = "unknown"
```

The exact list may expand only when fixtures justify it.

---

# 9. Definition of “full support”

Do not define full support as:

> The code contains a parser class with that name.

Define it as:

> For the claimed grammar/feature combination, the repository contains representative real or faithfully captured fixtures, expected normalized output, automated tests, and no unexplained GC-related records in those fixtures.

A source log may not contain every metric.

For example, old logs may not provide:

- wall-clock timestamps
- GC IDs
- CPU timing
- metaspace
- per-phase timing
- JVM flags
- committed versus reserved memory

If the source does not contain a value, use `None` or an explicit unknown state.

Never fabricate values.

The capability state should support:

```text
VERIFIED
PARTIALLY_VERIFIED
FORMAT_COMPATIBLE
EXPERIMENTAL
NOT_TESTED
NOT_APPLICABLE
UNSUPPORTED
```

---

# 10. Core architectural rule

The parser architecture must be based on **log grammar**, not Java version.

Incorrect:

```python
if java_version >= 9:
    parser = HotSpotUnifiedParser()
```

Correct concept:

```text
source sample
    -> evidence detectors
    -> candidate grammars
    -> best-supported parser candidate
    -> parse attempt
    -> confidence validation
```

Java version is evidence that may increase or decrease confidence. It is not the sole parser selector.

This matters because:

- vendor distributions can backport functionality
- Azul Prime supports unified-style logging on Java 8+
- logs can begin after startup metadata
- logs can be truncated
- logs can be concatenated
- a log file can contain application output mixed with GC output
- historical runtimes do not map cleanly to modern vendor labels

---

# 11. Input pipeline

Use this pipeline:

```text
InputSource
    |
    +--> bounded binary sample --> Detector
    |
    +--> line-oriented view ------> Segmenter/line parsers
    |
    +--> binary stream -----------> structured parsers such as OpenJ9 XML
                                   |
                                   v
                              ParsedRecord stream
                                   |
                                   v
                              EventAssembler
                                   |
                                   v
                             Canonical Model
                                   |
                    +--------------+-------------+
                    |                            |
                    v                            v
                 DuckDB                      Coverage
                    |
             +------+------+
             |             |
             v             v
         Analytics      Local UI
```

The **Segmenter** is required. Do not assume one process run per file.

`InputSource` must expose both a binary stream and a line-oriented view. Do not force structured grammars such as OpenJ9 XML through a line-only parser API. Detection may use bounded replayable samples, but the selected parser must consume the representation appropriate to its grammar.

---

# 12. Source-line model

Every physical source line should be representable as:

```python
@dataclass(slots=True)
class SourceLine:
    line_number: int
    byte_offset: int | None
    text: str
    source_id: str
    raw_length_bytes: int | None = None
    newline_kind: str | None = None
```

Use a grammar-neutral source-span abstraction for provenance:

```python
class OffsetSpace(Enum):
    ORIGINAL_FILE = "original_file"
    EXPANDED_STREAM = "expanded_stream"
    WORKING_COPY = "working_copy"

@dataclass(frozen=True, slots=True)
class ByteSpan:
    start: int
    end: int
    space: OffsetSpace

@dataclass(frozen=True, slots=True)
class SourceSpan:
    source_id: str
    start_line: int | None = None
    end_line: int | None = None
    byte_span: ByteSpan | None = None
    grammar_locator: str | None = None
```

`grammar_locator` is optional metadata for formats where a line range is not the best logical locator, for example an OpenJ9 XML element/cycle identifier.

Byte offsets must always declare their coordinate system. For compressed input, an offset in the compressed file, the decompressed stream, and a managed decompressed working copy are different values and must never be mixed. A weaker implementation must never seek into a `.gz` file using an `EXPANDED_STREAM` or `WORKING_COPY` offset.

An event may have **multiple source spans**. Unified Logging and concurrent collectors can interleave records from different GC IDs, so a single inclusive line range can accidentally include unrelated records.

The parser must retain enough provenance to navigate from a normalized event back to the original log. Do not duplicate every raw line into every event object. Store source spans/references.

---

# 13. Segment discovery

A file may contain:

- multiple JVM launches appended to one file
- rotated logs concatenated by an operator
- startup banners in the middle of a file
- multiple XML documents
- mixed stdout/stderr
- non-GC application messages

Create:

```python
@dataclass
class LogSegment:
    segment_id: int
    start_line: int
    end_line: int | None
    start_offset: int | None
    end_offset: int | None
    detection: DetectionResult | None
```

Segmentation signals may include:

```text
new JVM version banner
new "Using G1"/collector startup declaration
new unified-log start/header
new OpenJ9 <verbosegc ...> root
new JRockit startup memory description
new Azul GC header
large timestamp reset from high uptime to near zero
explicit process startup markers
```

A timestamp reset alone is insufficient evidence. It should contribute to a segment-boundary score.

If segmentation is uncertain, prefer one segment over destructively splitting events.

### Streaming segment detection

Detection must remain one-pass even for gzip input. At the beginning of the file and after a probable segment boundary, use a **bounded lookahead/replay buffer**:

```text
read bounded source records
    -> collect detector evidence
    -> choose parser or mark ambiguity
    -> replay the buffered records into the selected parser
    -> continue streaming
```

The lookahead limit must be bounded by both record count and bytes. Do not reread/decompress an entire `.gz` source for each detector or segment. If evidence is insufficient within the lookahead window, continue with explicit ambiguity/unknown handling rather than buffering without limit.

---

# 14. Detection architecture

Each detector returns evidence and a score.

Conceptual API:

```python
@dataclass
class Evidence:
    code: str
    weight: float
    description: str
    line_number: int | None = None

@dataclass
class DetectionCandidate:
    grammar: LogGrammar
    vm_family: VMFamily | None
    vendor: Vendor | None
    collector: Collector | None
    java_version: JavaVersion | None
    score: float
    evidence: list[Evidence]
    contradictions: list[Evidence]
```

Do not return just a boolean.

Example:

```text
Candidate: HOTSPOT_UNIFIED
Score: 0.98
Evidence:
  + [info][gc] tag structure
  + GC(42) event identifier
  + "Using G1"
  + uptime decorator
Contradictions:
  none
```

If two candidates are close, report ambiguity.

For example:

```text
HOTSPOT_UNIFIED   0.72
AZUL_PRIME_UNIFIED 0.68
```

Then use additional payload evidence before deciding.

Do not guess merely to obtain a parser.

---

# 15. Parser interface

Use a small stable interface.

```python
class ParserInputMode(Enum):
    LINES = "lines"
    BYTE_STREAM = "byte_stream"

class GCLogParser(Protocol):
    grammar: LogGrammar
    input_mode: ParserInputMode

    def probe(self, sample: "LogSample") -> DetectionCandidate:
        ...

    def parse(
        self,
        source: "ParserSource",
        context: "ParseContext",
    ) -> Iterator["ParsedRecord"]:
        ...
```

`ParserSource` must provide bounded access to the selected segment in both forms when practical:

```python
class ParserSource(Protocol):
    def iter_lines(self) -> Iterator[SourceLine]: ...
    def open_binary(self) -> BinaryIO: ...
```

Line-oriented grammars such as HotSpot legacy/unified, JRockit, and text Azul formats should normally use `iter_lines()`. Structured XML grammars should consume `open_binary()` directly with a secure streaming parser. Do not decode XML into arbitrary lines and then reconstruct XML for `iterparse()`.

The syntax parser should emit records, not final analytics objects.

Example parsed-record types:

```text
MetadataRecord
JvmArgumentRecord
CollectorDeclarationRecord
EventStartRecord
EventEndRecord
MemoryRecord
PhaseRecord
CpuTimeRecord
SafepointRecord
WarningRecord
UnknownGCRecord
```

This avoids forcing every grammar into the same line shape.

---

# 16. Separate syntax parsing from event assembly

A single GC event can span many lines.

Do not make every syntax parser construct a final canonical GC cycle.

Use:

```text
syntax parser
    -> ParsedRecord stream
    -> EventAssembler
    -> GCCycle / GCOperation / GCPhase stream
```

The assembler can correlate records by:

- GC ID
- timestamp range
- XML nesting
- explicit event start/end markers
- collector-specific state-machine rules

This is especially important for:

- Unified Logging with many `GC(n)` records
- G1 phase detail
- ZGC concurrent cycles
- Shenandoah concurrent phases
- OpenJ9 XML cycles
- Azul C4 multi-line statistics

---

# 17. Canonical model

Use dataclasses unless Pydantic provides a concrete benefit. Avoid making core parsing dependent on the web framework.

The canonical hierarchy must distinguish a **GC cycle** from the operations and phases that occur inside it. This distinction is required for concurrent collectors. A logical cycle may contain zero, one, or multiple stop-the-world operations plus concurrent work. Do not model the entire cycle as one pause.

Recommended structure:

```python
@dataclass(frozen=True, slots=True)
class WallClockValue:
    epoch_ns: int | None
    utc_offset_minutes: int | None
    raw: str | None

class ExecutionMode(Enum):
    STOP_THE_WORLD = "stop_the_world"
    CONCURRENT = "concurrent"
    UNKNOWN = "unknown"

@dataclass(slots=True)
class GCPhase:
    phase_key: str
    name: str
    execution_mode: ExecutionMode
    start_ns: int | None
    end_ns: int | None
    duration_ns: int | None
    source_spans: tuple[SourceSpan, ...]
    attributes: dict[str, ScalarValue]

@dataclass(slots=True)
class GCOperation:
    operation_key: str
    cycle_key: str
    operation_type: OperationType
    execution_mode: ExecutionMode

    uptime_start_ns: int | None
    uptime_end_ns: int | None
    duration_ns: int | None

    source_spans: tuple[SourceSpan, ...]
    heap: MemoryUsage | None
    young: MemoryUsage | None
    old: MemoryUsage | None
    metaspace: MemoryUsage | None
    compressed_class_space: MemoryUsage | None
    permgen: MemoryUsage | None

    phases: list[GCPhase]
    cpu: CpuTimes | None
    attributes: dict[str, ScalarValue]
    warning_codes: list[str]

@dataclass(slots=True)
class GCCycle:
    cycle_key: str
    segment_id: int
    gc_id: int | str | None

    source_spans: tuple[SourceSpan, ...]

    uptime_start_ns: int | None
    uptime_end_ns: int | None
    wall_clock_start: WallClockValue | None
    wall_clock_end: WallClockValue | None

    collector: Collector
    cycle_type: CycleType
    cause: str | None

    operations: list[GCOperation]

    # Derived aggregates only; never confuse these with one logged pause.
    elapsed_ns: int | None
    total_pause_ns: int | None
    concurrent_ns: int | None

    attributes: dict[str, ScalarValue]
    warning_codes: list[str]
```

For a simple Serial young collection, a cycle may contain one stop-the-world operation. For G1, ZGC, Shenandoah, CMS, OpenJ9, or C4, a cycle may contain multiple stop-the-world and concurrent operations.

`elapsed_ns`, `total_pause_ns`, and `concurrent_ns` on `GCCycle` are derived aggregates when sufficient information exists. They must carry derived-value provenance in persistence/analytics and must not be treated as directly logged unless the source explicitly reports an equivalent value.

Use **integer nanoseconds** for JVM-relative timestamps and durations. Do not store canonical timing values as binary floating-point seconds. Convert to milliseconds/seconds only at presentation boundaries.

`WallClockValue.epoch_ns` preserves nanosecond precision when present. Python `datetime` may be exposed as a convenience representation, but it is not the canonical precision-bearing field. In DuckDB, preserve nanosecond wall-clock values as `BIGINT epoch_ns` when timezone/offset fidelity matters; optionally maintain a derived timestamp column for convenient SQL/UI queries.

Use stable string keys such as `cycle_key`, `operation_key`, and `phase_key` because not every runtime exposes numeric GC IDs.

---

# 18. Value provenance

Important numeric values should support provenance.

For example:

```python
@dataclass(slots=True)
class Provenance:
    source_spans: tuple[SourceSpan, ...]
    raw_text_preview: str | None = None
    derivation: str | None = None
```

Keep raw previews bounded. Full raw text should normally be retrieved from the original source using the source spans.

For derived analytics distinguish:

```text
LOGGED
DERIVED
ESTIMATED
USER_SUPPLIED
UNKNOWN
```

Example:

```python
MetricValue(
    value=123_456_789,
    source=ValueSource.LOGGED,
)
```

versus:

```python
MetricValue(
    value=220_000_000,
    source=ValueSource.ESTIMATED,
    derivation="delta between consecutive post-GC heap samples",
)
```

Do not display an estimate as though it were directly logged.

---

# 19. Memory representation

Use bytes internally.

```python
@dataclass(slots=True)
class MemoryUsage:
    before_bytes: int | None = None
    after_bytes: int | None = None
    used_bytes: int | None = None
    committed_bytes: int | None = None
    capacity_bytes: int | None = None
    reserved_bytes: int | None = None
    max_bytes: int | None = None
```

Do not use `0` for unknown.

The model intentionally allows multiple concepts because JVM families use terms differently.

Do not silently equate:

```text
capacity == committed == reserved == max
```

unless the grammar explicitly defines them that way.

---

# 20. Memory-size parser

Create one central module, but allow syntax profiles.

The following syntaxes may occur:

```text
1024
1024K
1024k
1024M
1024G
1024KiB
1024MiB
1024GiB
0x40000000
```

Do not assume every suffix has the same semantics across arbitrary external formats.

Provide parsing functions such as:

```python
parse_jvm_size(...)
parse_gc_log_size(...)
parse_explicit_iec_size(...)
```

For JVM `-Xmx2g`-style options use JVM flag semantics.

Return:

```python
ParsedSize(
    bytes=2147483648,
    raw="2g",
    unit="g",
)
```

All overflow/error behavior must be tested.

---

# 21. Timestamp model

Keep these independent:

```text
JVM uptime
event start uptime
event end uptime
wall-clock start
wall-clock end
duration
```

Never infer that a number ending in `s` is necessarily wall-clock time.

Support representative forms such as:

```text
123.456:
123.456s
2026-10-02T12:34:56.789+0800
2026-10-02T12:34:56.789+08:00
```

Parse JVM-relative times and durations into **integer nanoseconds**. Preserve the original textual token so exact parser behavior can be audited.

For absolute time, normalize into `WallClockValue` with `epoch_ns`, original UTC offset, and raw text. A timezone-aware Python `datetime` may be exposed as a convenience object when a timezone exists, but it is not the canonical precision-bearing representation.

Derived wall-clock times may be calculated only when a trustworthy relationship between process start and uptime exists.

Record the derivation and distinguish logged timestamps from derived timestamps.

---

# 22. HotSpot Unified Logging parser

This is a major reusable grammar.

Do not build six unrelated unified parsers for six collectors.

Use layers:

```text
Unified Logging envelope parser
    -> decorators
    -> level
    -> tag set
    -> payload

payload dispatcher
    -> generic GC payload
    -> collector-specific payload interpreter
```

The envelope parser must not assume the default decorator set.

Possible decorators include variants such as:

```text
time
uptime
timemillis
uptimemillis
timenanos
uptimenanos
pid
tid
level
tags
hostname, where supported
```

Treat bracket groups generically.

Example:

```text
[2.345s][info][gc,start] GC(7) Pause Young ...
```

becomes conceptually:

```python
UnifiedEnvelope(
    uptime_ns=2_345_000_000,
    uptime_raw="2.345s",
    level="info",
    tags={"gc", "start"},
    payload="GC(7) Pause Young ...",
)
```

The payload layer then parses `GC(7)` and the collector-specific text. Parse decimal time tokens directly into integer nanoseconds using exact string/decimal arithmetic; do not round-trip through binary `float`.

Do not require decorators to appear in one fixed order unless the JVM documentation/fixture proves the order for that format.

Preserve unknown decorators.

---

# 23. HotSpot Unified Logging event correlation

Use `GC(n)` as a strong correlation key where present.

Maintain an event assembly map:

```python
open_cycles: dict[int, PartialGCCycle]
```

Handle:

- event starts
- event summaries
- phase records
- heap records
- age tables
- reference processing
- CPU records
- concurrent-cycle records

Do not assume every `GC(n)` line is part of a stop-the-world event.

A GC ID may represent a concurrent cycle containing multiple operations and phases. The assembler must attach stop-the-world and concurrent operations to the same cycle without collapsing the whole cycle into one pause.

---

# 24. HotSpot legacy parser

Target pre-Unified-Logging HotSpot output and legacy flags.

Representative inputs are affected by flags such as:

```text
-verbose:gc
-Xloggc:<file>
-XX:+PrintGC
-XX:+PrintGCDetails
-XX:+PrintGCTimeStamps
-XX:+PrintGCDateStamps
-XX:+PrintHeapAtGC
-XX:+PrintTenuringDistribution
-XX:+PrintGCApplicationStoppedTime
-XX:+PrintGCApplicationConcurrentTime
```

Do not make one regex for each complete possible line.

Build primitives:

```text
legacy timestamp prefix
absolute date prefix
event opener/event closer
collector token
cause token
memory transition
memory-pool section
duration
CPU-time section
phase section
```

Reusable memory transition example:

```text
12345K->6789K(32768K)
```

should become:

```python
MemoryTransition(
    before_bytes=...,
    after_bytes=...,
    capacity_bytes=...,
)
```

The same primitive can be reused inside many collector grammars.

---

# 25. Collector-specific semantic interpreters

After syntax parsing, interpret records according to collector semantics.

## 25.1 Serial

Recognize where fixtures support it:

- young collection
- full collection
- tenured/old collection details
- cause
- heap transitions
- pause duration

## 25.2 Parallel

Recognize:

- young collection
- old/full collection
- PSYoungGen
- ParOldGen or equivalent historical output
- adaptive-sizing information where logged

## 25.3 CMS

Recognize:

- initial mark
- concurrent mark
- preclean
- abortable preclean where applicable
- remark
- concurrent sweep
- reset
- promotion failure
- concurrent mode failure
- Full GC fallback

CMS is historically complex. Implement only from fixtures.

## 25.4 G1

Recognize:

- young pause
- mixed pause
- concurrent cycle
- remark
- cleanup
- Full GC
- evacuation failure
- to-space exhaustion where logged
- humongous-region information
- region/heap summaries

Do not assume the exact phrase remains identical across every Java release.

## 25.5 ZGC

Recognize based on version-specific fixtures:

- GC cycles
- pause phases
- concurrent phases
- allocation stalls
- heap statistics
- generation where explicitly present

The canonical model must be capable of representing both historical non-generational and modern generational ZGC logs.

Do not infer generational mode from Java version alone when the log itself can provide stronger evidence.

## 25.6 Shenandoah

Recognize based on fixtures:

- concurrent cycles
- pause phases
- evacuation/update phases
- degenerated GC
- Full GC
- generation/mode when logged

Do not assume that every Shenandoah log corresponds to the same mode or upstream JDK timeline because vendor backports exist.

---

# 26. OpenJ9 / IBM parser

OpenJ9 verbose GC uses structured XML for its current verbose format.

Implement it as a separate parser.

Use streaming parsing rather than reading the entire XML tree.

Conceptually:

```python
for event, elem in iterparse(...):
    ...
```

Important information may include:

```text
<initialized>
GC policy
system information
VM arguments
allocation failure triggers
cycle starts/ends
GC operations
concurrent increments
memory values
```

Also support the documented deprecated format as a distinct grammar.

OpenJ9 has an option for a deprecated verbose-GC format corresponding to older IBM J9 behavior. Treat that as separate from the current XML grammar.

## 26.1 XML safety

Reject or fail safely on XML declarations/features that are unnecessary for GC logs, including unexpected DTD/entity constructs.

Never fetch external entities.

The application is offline anyway, but the parser must not attempt local-file entity resolution either.

## 26.2 Truncated XML

A JVM crash may leave the final XML document incomplete.

Implement a recovery mode that parses complete events already observed before the truncation and reports:

```text
TRUNCATED_XML
```

Do not silently pretend the document was complete.

---

# 27. JRockit parser

Oracle JRockit verbose GC is a distinct grammar and must not be passed to HotSpot parsing code.

Initial support should target documented/fixture-backed output from:

```text
-Xverbose:gc
-Xverbose:memory
-Xverbose:gcpause
-Xverbose:gcreport
```

Example concepts found in JRockit verbose output include:

```text
[memory ]
[gcpause]
GC strategy declarations
heap size
nursery size
seconds since JVM start
before/after heap values
pause duration
GC report summaries
```

Model JRockit strategy text losslessly.

Do not invent mappings from JRockit strategy names to HotSpot collectors.

If a JRockit format/version has no fixture, classify it as not tested rather than claiming Oracle universal support.

---

# 28. Azul support

Split Azul into at least two runtime paths:

```text
Azul Zulu / normal HotSpot-derived OpenJDK
Azul Prime / Zing / C4
```

Zulu logs may follow normal HotSpot grammars.

Azul Prime has C4-specific GC behavior and payloads.

Modern Azul Prime supports unified-style GC logging even on Java versions where stock HotSpot would not use the same version rule. Therefore parser selection must remain evidence-based.

For Azul Prime support:

```text
legacy Prime/Zing GC logs
C4-specific header/statistics records
Prime unified GC logging
GC configuration lines
system-statistic lines where documented
```

Preserve Prime-specific metrics in generic attributes until enough stable fixtures exist to promote them into typed canonical fields.

Example:

```python
event.attributes["azul.relocation_rate"] = ...
```

Do not force every vendor-specific metric into a universal field.

---

# 29. HP/HPE support

Do not create a large HPE-specific parser without evidence.

Start by collecting verified HP-UX/HPE JVM fixtures.

For each fixture:

1. identify VM family
2. determine whether HotSpot legacy parsing succeeds
3. record actual deviations
4. implement narrowly scoped extensions only for observed differences

The capability matrix should distinguish:

```text
HPE + HotSpot-compatible format
```

from:

```text
HPE-specific grammar
```

if such a distinction is demonstrated by fixtures.

---

# 30. Historical Sun support

Java 1.2 through early Java releases require special care.

Do not assume every old Sun GC log is equivalent to later Oracle HotSpot output.

Use actual fixtures to identify:

- Sun Classic VM output, if present in scope
- early HotSpot output
- client/server VM differences
- historical option differences

Preserve actual runtime identification.

If a historical grammar cannot be verified, report `NOT_TESTED` rather than filling the gap with a guessed regex.

---

# 31. JVM argument subsystem

JVM argument parsing must be independent of GC event parsing.

Arguments may come from:

```text
startup headers
OpenJ9 <vmargs>
Azul headers
JRockit startup output
user-provided --jvm-args
user-provided argument file
```

Always preserve the raw ordered argument list.

```python
@dataclass
class JVMArgument:
    ordinal: int
    raw: str
    name: str | None
    raw_value: str | None
    normalized_value: object | None
    recognized: bool
    category: str | None
```

Never discard unknown flags.

---

# 32. “All memory settings” contract

Do not claim semantic understanding of every memory option ever implemented by every JVM.

Use this contract:

> Every supplied or logged JVM argument is preserved losslessly. Known memory-related arguments are normalized into typed settings. Unknown arguments remain visible and searchable and do not prevent analysis.

Known HotSpot settings should include, where applicable:

```text
-Xms
-Xmx
-Xmn
-Xss

-XX:InitialHeapSize
-XX:MaxHeapSize
-XX:MinHeapSize
-XX:NewSize
-XX:MaxNewSize
-XX:NewRatio
-XX:SurvivorRatio

-XX:PermSize
-XX:MaxPermSize

-XX:MetaspaceSize
-XX:MaxMetaspaceSize
-XX:CompressedClassSpaceSize

-XX:MaxDirectMemorySize

-XX:MaxRAM
-XX:InitialRAMPercentage
-XX:MinRAMPercentage
-XX:MaxRAMPercentage

-XX:LargePageSizeInBytes

-XX:ParallelGCThreads
-XX:ConcGCThreads

-XX:MaxGCPauseMillis
-XX:GCTimeRatio

-XX:+UseSerialGC
-XX:+UseParallelGC
-XX:+UseConcMarkSweepGC
-XX:+UseG1GC
-XX:+UseZGC
-XX:+UseShenandoahGC
```

OpenJ9 should have a separate registry including fixture-backed options such as:

```text
-Xgcpolicy:
-Xms
-Xmx
-Xmn
-Xsoftmx
-Xgcthreads
```

Azul Prime and JRockit should use separate registries.

Do not put all vendor flags into one enormous conditional function.

---

# 33. Argument precedence

If multiple equivalent settings appear, preserve all originals and compute an effective value only when precedence is known.

For example:

```python
EffectiveJvmSetting(
    key="max_heap_bytes",
    value=...,
    winning_argument_ordinal=12,
    confidence=Confidence.VERIFIED,
)
```

Do not silently remove overridden options from the UI.

The user may need to see that contradictory flags were supplied.

---

# 34. Input formats

Initial required file formats:

```text
plain text
.log
.txt
.gz
```

File extension must not be the sole detector.

A `.txt` file may be XML OpenJ9 output.

ZIP support may be added later, but should not be part of the first implementation unless required.

Use streaming decompression for gzip. Enforce a configurable `max_expanded_input_bytes`/analysis-size policy and track expanded bytes while reading so a gzip bomb or unexpectedly huge expansion cannot consume unlimited disk/CPU. Analysis may stop cleanly with a resource-limit diagnostic when the configured limit is reached.

Also enforce a configurable `max_line_bytes` or equivalent bounded-record policy. Oversized records must produce a controlled diagnostic rather than unbounded buffer growth. XML parsing requires an analogous bounded strategy for retained element/text payloads.

Never do:

```python
content = Path(path).read_text()
```

for general analysis.

---

# 35. Character encoding

Default strategy:

1. detect UTF BOMs
2. otherwise attempt UTF-8
3. preserve undecodable bytes with a controlled replacement/error strategy
4. report encoding warnings

Do not let one invalid byte terminate analysis of a multi-gigabyte log.

Store the chosen decoding mode in metadata.

---

# 36. Storage

Use a **file-backed DuckDB database** as the normalized analytical event store and query cache.

The original GC log remains the source of truth. The DuckDB file is a disposable, reproducible analysis artifact that may be deleted and rebuilt from the source log whenever the storage schema changes.

This workload is a good match for DuckDB because the dominant operations are:

```text
append normalized event batches
scan events by time range
filter by collector/event type/cause
aggregate pause and memory metrics
compute percentiles and distributions
downsample large time series
feed analytical query results to the local UI
```

Suggested tables:

```text
analysis_run
source_file
log_segment
runtime_metadata
jvm_argument
jvm_setting
source_checkpoint
gc_cycle
gc_cycle_source_span
gc_operation
gc_operation_source_span
gc_phase
memory_sample
event_attribute
parse_warning
unknown_record
capability_observation
```

Do not store one `raw_line_index` row for every physical source line. For very large mixed application/GC logs that can create hundreds of millions of low-value rows.

Instead use `source_checkpoint` as a **sparse** line-to-byte-offset index, for example every 1,000-10,000 physical lines, plus exact source spans/offsets for parsed GC records and events. The checkpoint interval must be configurable and benchmarked.

Do not store redundant full raw line text in every table. Store source spans/offset references and keep the original log as the canonical raw source.

Use separate cycle/operation source-span tables because a cycle and its operations can each be backed by multiple non-contiguous source spans.

For sparse vendor/collector-specific normalized values, prefer a typed `event_attribute` table over serializing an unrestricted Python dictionary into one opaque column. Attributes may belong to a cycle, operation, or phase, so do not assume one flat event key. A practical shape is:

```text
owner_kind      # cycle | operation | phase
owner_key
namespace
name
value_type
int_value
double_value
text_value
bool_value
```

Canonical high-value cycle fields such as collector, cause, cycle type, elapsed time, and derived total pause time remain first-class typed columns in `gc_cycle`. Operation-level execution mode, duration, and memory transitions remain first-class typed columns in `gc_operation`. Do not flatten concurrent and stop-the-world operations into one event row.

Warnings must use stable machine-readable codes plus optional human-readable detail. Do not persist only free-form warning strings; the UI and tests need stable identifiers such as `UNKNOWN_GC_RECORD`, `TRUNCATED_EVENT`, or `AMBIGUOUS_GRAMMAR`.

Recommended cache layout:

```text
~/.cache/gcviewer/
    <source-fingerprint>.duckdb
```

The source fingerprint should include enough information to detect stale caches, for example:

```text
absolute/canonical source identity where appropriate
file size
modification time
optional sampled/content hash
parser schema version
canonical model version
```

Do not require the DuckDB file to be portable across incompatible schema versions. It is acceptable to invalidate and rebuild it.

---

# 37. DuckDB ingestion and query strategy

DuckDB is optimized for bulk and analytical operations. Do **not** implement ingestion as millions of small row inserts.

Use bounded columnar batches. Recommended implementation:

```text
stream source log
    -> parser/event assembler
    -> accumulate bounded Python rows per destination table
    -> convert batch to PyArrow Table/RecordBatch
    -> register/query Arrow batch from DuckDB
    -> INSERT INTO target SELECT ... FROM batch
    -> discard Python/Arrow batch
    -> continue
```

Conceptual example:

```python
import duckdb
import pyarrow as pa

con = duckdb.connect(database_path)

rows = [
    {"segment_id": 1, "cycle_key": "1:42", "total_pause_ns": 12_300_000},
    # bounded batch only
]

batch = pa.Table.from_pylist(rows)
con.register("event_batch", batch)
con.execute("""
    INSERT INTO gc_cycle (segment_id, cycle_key, total_pause_ns)
    SELECT segment_id, cycle_key, total_pause_ns
    FROM event_batch
""")
con.unregister("event_batch")
```

The exact batch size must be benchmarked. Start with a bounded target such as a few thousand to tens of thousands of records, then tune using peak RSS and ingestion throughput.

Avoid `executemany()` for large bulk ingestion. It may be used only for small metadata/control tables where performance is irrelevant.

Keep rows naturally ordered whenever practical, especially by:

```text
segment_id
event time / uptime
source line
```

Canonical time/duration persistence rules:

```text
uptime_*_ns              -> BIGINT
operation_duration_ns    -> BIGINT
cycle_elapsed_ns         -> BIGINT
total_pause_ns           -> BIGINT
concurrent_ns            -> BIGINT
wall_clock_epoch_ns      -> BIGINT when exact nanosecond preservation is required
wall_clock_offset_min    -> INTEGER
```

A derived DuckDB timestamp column may be added for convenient date/time filtering, but must not replace the precision-bearing raw `epoch_ns` value.

DuckDB automatically maintains min-max/zonemap metadata. Ordered data makes those zonemaps much more useful for range filtering.

Do **not** create a large collection of secondary indexes by default.

DuckDB ART indexes should be added only for proven, highly selective point-lookups after benchmarking. They consume additional memory and can slow loading. Candidates, if profiling proves they are useful, include a single-column lookup on a stable event key.

Do not add primary-key/foreign-key constraints merely for relational neatness if doing so creates unnecessary ART index overhead. The analysis database is a derived cache; consistency should primarily be guaranteed by the parser/storage writer and tests.

Analytics should execute directly in DuckDB whenever practical, especially:

```text
GROUP BY
window functions
quantiles/percentiles
time buckets
range filters
summary aggregations
chart downsampling prequeries
```

Keep Python responsible for domain logic that is difficult to express or verify in SQL, but avoid fetching millions of rows into Python just to aggregate them.

### Connection/concurrency and offline-security rules

Use explicit `duckdb.connect(...)` connection objects. Do not use the module-global default connection from library code.

The application must not depend on DuckDB extension downloads. At initialization, disable extension auto-installation/community extension loading unless a future feature has a reviewed, packaged-local need for an extension. Conceptually configure:

```sql
SET autoinstall_known_extensions = false;
SET allow_community_extensions = false;
```

Do not call `INSTALL` against network repositories in application code. Any future extension must be packaged and reviewed for offline use.

Configure resource limits from application settings rather than leaving very large imports unconstrained. Expose conservative defaults and allow overrides for:

```text
memory_limit
threads
max_temp_directory_size
temp_directory
```

The application must fail with a clear resource-limit error rather than consuming unbounded memory or temporary disk.

Preferred lifecycle:

```text
analysis/import stage:
    one owning process
    one primary write connection
    bulk append batches

viewer stage after import:
    read/query connections scoped safely to the serving process/thread model
```

Do not design v1 around multiple independent writer processes targeting the same `.duckdb` file.

If the viewer performs queries from multiple Python threads, give each thread/request execution context its own DuckDB connection/cursor rather than sharing one global connection object unsafely.

---

# 38. Raw source navigation

A key user feature is:

> Click a GC event and see the exact original records that produced it.

Persist one or more `SourceSpan` records per event. Where available, store exact byte offsets for parsed records. Also maintain a sparse `source_checkpoint` table for efficient line navigation without creating one database row per physical line.

For an uncompressed source, raw navigation can seek to an exact byte offset or to the nearest checkpoint and scan forward.

For compressed input, random seeking into the original gzip stream is not reliable enough for the primary navigation design. Separate two modes:

```text
analyze-only:
    stream .gz directly
    do not require a decompressed copy

interactive raw navigation:
    optionally create a managed decompressed working copy in the analysis cache
    build offsets/checkpoints against that working copy
```

Do not silently decompress a very large source merely because analysis was requested. Before creating a decompressed working copy, check configured disk limits and expose the expected/observed expanded size.

Do not duplicate the entire raw log into DuckDB merely to solve source navigation.

Do not compromise parser correctness to optimize raw navigation prematurely.

---

# 39. Coverage report

Every analysis run must produce a parsing coverage report.

Required fields:

```text
total physical lines, when meaningful for the source format
blank lines
application/non-GC lines
recognized JVM metadata logical records
recognized GC-related logical records
unknown GC-like logical records
parsed events
complete events
incomplete events
parse warnings
parse errors
ambiguous records
segments detected
```

Also calculate:

```text
recognized GC-like logical records / total GC-like logical records
```

Coverage is primarily based on **logical grammar records/elements**, not only physical lines. This matters for XML and any future structured/multiline formats where one logical record may span multiple lines or multiple logical records may appear on one line.

Physical-line statistics remain useful diagnostics, but do not use total lines as the primary support-coverage denominator because application output can dominate mixed logs.

The algorithm that decides whether input is “GC-like” must itself be conservative and testable.

---

# 40. Unknown record handling

Unknown input is expected.

Represent it explicitly without duplicating arbitrary amounts of raw input into DuckDB:

```python
@dataclass(slots=True)
class UnknownRecord:
    segment_id: int
    source_spans: tuple[SourceSpan, ...]
    reason_code: str
    reason_detail: str | None
    gc_like: bool
    signature: str | None
    text_preview: str | None
```

`text_preview` must be bounded by configuration, for example 1-4 KiB. `signature` should be a normalized diagnostic fingerprint suitable for grouping repeated unknown forms. The full source remains in the original log/working copy and is retrieved on demand.

Never silently drop an unknown GC-looking logical record.

The UI should provide:

```text
Unknown GC Records
```

with grouping by similar prefix/pattern so developers can add future parser support.

---

# 41. Analytics layer

Analytics must use canonical data only.

Do not parse strings again inside analytics.

Common metrics, where data exists:

```text
log-covered runtime
GC event count
STW event count
concurrent-cycle count
full-collection count
young-collection count

total stop-the-world pause time
maximum stop-the-world pause
mean pause
median pause
p90
p95
p99
p99.9, if sample count is meaningful

pause-time percentage
GC frequency

heap before GC
heap after GC
heap reclaimed
post-GC live-set trend

allocation-rate estimate
promotion-rate estimate

concurrent-cycle duration
```

Every metric must expose availability.

Example:

```python
MetricResult(
    available=False,
    reason="source format does not expose per-event heap-after values",
)
```

Do not return zero when unavailable.

---

# 42. Percentile definition

Choose one percentile algorithm and test it.

Document it.

Do not rely on whatever default NumPy/Pandas happens to use if those libraries are later introduced.

Prefer DuckDB-side percentile/quantile calculations for large event sets so the application does not materialize all pause values in Python. For tiny hand-calculable unit fixtures, a small reference percentile helper may be kept only to verify the SQL result.

Document the exact quantile function/definition used by the product and lock it with tests so a dependency upgrade cannot silently change displayed semantics.

---

# 43. Estimated allocation/promotion rates

These metrics can be misleading if inferred incorrectly.

Only calculate them when the source sequence provides sufficient memory observations.

Mark them:

```text
ESTIMATED
```

Include the derivation in diagnostics.

Do not show an allocation rate when events are too sparse or memory samples are incomplete.

---

# 44. Evidence-based problem indicators

The program may flag observations such as:

```text
CMS concurrent-mode failure
promotion failure
G1 evacuation failure
G1 to-space exhaustion
repeated Full GC
metaspace-triggered collection
humongous-region pressure
ZGC allocation stalls
Shenandoah degenerated GC
Shenandoah Full GC
large pause outliers
rising post-GC live set
high GC-time fraction
```

Each indicator must link to evidence.

Example:

```text
Observation: G1 evacuation failure
Count: 7
First event: segment 1, GC(182), line 45,221
Last event: segment 1, GC(251), line 62,804
```

Avoid absolute judgments such as:

```text
"Your GC is bad"
```

Prefer:

```text
"Pause time exceeded the configured 500 ms threshold 14 times."
```

Thresholds must be configurable.

---

# 45. Viewer

Use a local web application.

Recommended dependencies:

```text
fastapi
uvicorn
plotly
```

The parser core should not import FastAPI.

The local server must bind by default to:

```text
127.0.0.1
```

not `0.0.0.0`.

Provide an explicit option if remote binding is ever desired.

No telemetry.

No CDN assets.

Bundle or locally serve all JavaScript/CSS.

---

# 46. Viewer pages

Recommended sections:

```text
Overview
Timeline
Heap
Pauses
Collectors / Policies
Events
Phases
JVM Configuration
Parsing Diagnostics
Unknown Records
Raw Log
Capability Report
```

---

# 47. Charts

At minimum:

```text
heap used over time
post-GC heap over time
pause duration over time
pause distribution
GC frequency over time
concurrent-cycle duration
```

Collector-specific charts may be added only after canonical support exists.

Do not force a G1 region chart onto OpenJ9 or C4 data.

---

# 48. Downsampling

Do not send millions of points to the browser.

Use server-side downsampling.

For pause charts preserve outliers.

A mean-only aggregation is unacceptable because a single severe pause can disappear.

For each bucket retain useful values such as:

```text
first
last
minimum
maximum
count
```

For heap charts consider a method that preserves shape and extrema.

Keep the first implementation simple and testable.

---

# 49. CLI design

Suggested commands:

```bash
gcviewer inspect <log>
gcviewer analyze <log>
gcviewer serve <log>
gcviewer export <log> --format json
gcviewer verify-corpus
gcviewer capabilities
```

## `inspect`

Fast pre-scan only.

Output:

```text
segments
candidate grammars
VM family
vendor
Java version
collector/policy
confidence
important evidence
```

## `analyze`

Parse and create a file-backed DuckDB analysis database/cache.

## `serve`

Analyze if needed, then run local viewer.

## `export`

Export canonical data/metrics, not raw parser internals.

## `verify-corpus`

Run compatibility fixtures and produce a human-readable support report.

---

# 50. Manual overrides

Support optional advanced overrides:

```text
--grammar
--vm-family
--vendor
--java-version
--collector
--jvm-args
--jvm-args-file
```

Overrides must be visible in metadata as user supplied.

Do not disguise them as auto-detected values.

If an override contradicts strong log evidence, report a warning.

---

# 51. Project structure

Use approximately:

```text
universal-java-gc-viewer/
├── pyproject.toml
├── uv.lock
├── README.md
├── LICENSE
├── src/
│   └── gcviewer/
│       ├── __init__.py
│       ├── cli.py
│       ├── ingest.py
│       ├── segment.py
│       ├── detection.py
│       ├── models.py
│       ├── provenance.py
│       ├── units.py
│       ├── timestamps.py
│       ├── versions.py
│       │
│       ├── parsers/
│       │   ├── base.py
│       │   ├── registry.py
│       │   │
│       │   ├── hotspot/
│       │   │   ├── legacy.py
│       │   │   ├── unified.py
│       │   │   ├── primitives.py
│       │   │   ├── serial.py
│       │   │   ├── parallel.py
│       │   │   ├── cms.py
│       │   │   ├── g1.py
│       │   │   ├── zgc.py
│       │   │   └── shenandoah.py
│       │   │
│       │   ├── jrockit/
│       │   │   ├── verbose.py
│       │   │   └── primitives.py
│       │   │
│       │   ├── openj9/
│       │   │   ├── xml.py
│       │   │   └── deprecated.py
│       │   │
│       │   └── azul/
│       │       ├── legacy.py
│       │       ├── unified.py
│       │       └── primitives.py
│       │
│       ├── assembly/
│       │   ├── base.py
│       │   ├── hotspot.py
│       │   ├── openj9.py
│       │   ├── azul.py
│       │   └── jrockit.py
│       │
│       ├── args/
│       │   ├── common.py
│       │   ├── hotspot.py
│       │   ├── openj9.py
│       │   ├── azul.py
│       │   └── jrockit.py
│       │
│       ├── analysis/
│       │   ├── common.py
│       │   ├── pauses.py
│       │   ├── heap.py
│       │   ├── allocation.py
│       │   ├── indicators.py
│       │   └── percentiles.py
│       │
│       ├── storage/
│       │   ├── schema.sql
│       │   ├── duckdb.py
│       │   └── batches.py
│       │
│       └── web/
│           ├── app.py
│           ├── queries.py
│           └── static/
│               ├── index.html
│               ├── js/
│               └── css/
│
├── tests/
│   ├── unit/
│   ├── parser/
│   ├── integration/
│   ├── fuzz/
│   └── fixtures/
│
└── corpus/
    ├── manifest.json
    ├── hotspot/
    ├── jrockit/
    ├── openj9/
    └── azul/
```

Do not create a file merely to mirror this structure. Create modules when behavior justifies them.

---

# 52. Fixture-first development rule

This rule is non-negotiable:

> Never implement a production parser for a GC-log syntax for which there is no fixture.

Every supported parser behavior must have:

```text
input fixture
expected parsed records or events
unit/integration test
capability metadata
```

Do not invent historical syntax from memory.

Do not copy an internet regex library without fixtures demonstrating correctness.

---

# 53. Corpus manifest

Use a machine-readable manifest.

Example:

```json
{
  "id": "hotspot-jdk8-g1-basic-001",
  "path": "hotspot/jdk8/g1/basic.log",
  "source_kind": "captured",
  "vendor": "oracle",
  "vm_family": "hotspot",
  "java_version": "8u382",
  "collector": "g1",
  "grammar": "hotspot_legacy",
  "logging_options": [
    "-XX:+PrintGCDetails",
    "-XX:+PrintGCTimeStamps"
  ],
  "features": [
    "uptime",
    "young_gc",
    "full_gc",
    "heap_transition"
  ],
  "expected_unknown_gc_records": 0
}
```

Add provenance/licensing notes when fixtures come from third-party documentation.

Prefer self-generated or safely redistributable fixtures for the repository.

---

# 54. Golden expected output

Do not test only that parsing does not throw.

Each fixture should have normalized expectations.

Example:

```json
{
  "cycles": 17,
  "collector": "g1",
  "unknown_gc_records": 0,
  "first_cycle": {
    "cycle_type": "young_collection",
    "total_pause_ns": 12400000,
    "operations": [
      {
        "execution_mode": "stop_the_world",
        "duration_ns": 12400000,
        "heap": {
          "before_bytes": 536870912,
          "after_bytes": 268435456
        }
      }
    ]
  }
}
```

For complex fixtures, expected output may contain selected assertions rather than reproducing every parsed record.

The expected data must still verify all important grammar features.

---

# 55. Compatibility corpus strategy

Do not try to obtain one fixture for every theoretical Cartesian combination.

Build fixtures around **grammar transitions and meaningful output differences**.

For HotSpot, representative checkpoints should include, where real fixtures are available:

```text
historical 1.2/1.3/1.4
5
6
7
8
9 transition to Unified Logging
11
17
21
24/25
26
27
```

Then add intermediate versions only when output differences require them.

Collectors must have their own timeline-specific fixtures.

For example, a Java 27 CMS combination is `NOT_APPLICABLE`, not missing test coverage.

---

# 56. Logging-configuration corpus

For each grammar collect variations such as:

```text
minimal GC logging
detailed GC logging
uptime timestamps
date timestamps
no timestamps
heap-at-GC detail
age/tenuring detail
reference-processing detail
CPU timing
log rotation
logs beginning mid-event
logs ending mid-event
mixed application output
CRLF
LF
very long lines
unknown future tags
```

Do not assume one “typical” command line represents a grammar.

---

# 57. Concatenation tests

Create integration fixtures containing:

```text
HotSpot legacy run + another HotSpot legacy run
HotSpot legacy + HotSpot unified
OpenJ9 XML document + OpenJ9 XML document
JRockit + application output
Azul Prime + later restarted Azul Prime
```

Expected behavior:

- discover distinct segments where evidence is strong
- parse each segment with the correct grammar
- keep segment-local uptime separate
- never merge GC IDs across JVM restarts

---

# 58. Corruption and resilience tests

Test:

```text
truncated final line
truncated event
truncated XML
invalid XML token
missing bracket
extra bracket
malformed memory value
unknown suffix
extremely large integer
negative invalid memory value
invalid timestamp
invalid UTF-8 byte
line longer than normal application limits
unknown GC tag
interleaved output
```

One malformed line must not normally abort a multi-gigabyte analysis.

Abort only when safe continuation is impossible.

---

# 59. Property/fuzz testing

Use Hypothesis for parser primitives.

Good targets:

```text
size parser
timestamp parser
Java version parser
Unified Logging decorator tokenizer
legacy memory-transition parser
argument parser
percentile helper
segment-boundary scoring
```

Fuzz tests should assert properties such as:

```text
never hang
never consume unbounded memory for one line
never return negative bytes from a valid positive size
never throw an unexpected exception type
round-trip preserved raw values where applicable
```

---

# 60. Performance targets

Define measurable non-functional targets.

Initial targets:

```text
Memory usage should be bounded primarily by parser state and batch buffers,
not proportional to total input-file size.

A multi-gigabyte text log must be parseable without loading the whole file.

The browser must never receive all events when a chart only needs a summary.
```

Add a benchmark fixture of at least several gigabytes synthetically repeated from valid records.

Do not include the huge generated file in source control. Generate it during benchmark setup.

Record:

```text
MB/s parsing throughput
peak RSS
DuckDB database growth
chart query latency
```

Avoid setting a hard throughput promise until measurements exist on a reference machine.

---

# 61. Parser confidence validation

Detection should not stop after selecting a parser.

After parsing an initial sample, validate the choice.

Example:

```text
Detector chose HOTSPOT_UNIFIED with 0.91 confidence.
First 500 GC-like records:
  496 recognized
  4 unknown
Parser validation score remains high.
```

If recognition collapses:

```text
Detector chose HOTSPOT_UNIFIED with 0.62 confidence.
First 500 GC-like records:
  90 recognized
  410 unknown
```

retry another candidate or mark the segment ambiguous.

Do not continue a clearly wrong parser just because the detector initially chose it.

---

# 62. Capability registry

The application must ship a generated capability report derived from the fixture manifest and tests.

Example:

```text
HotSpot legacy / G1 / JDK 8: VERIFIED
HotSpot unified / G1 / JDK 17: VERIFIED
HotSpot unified / ZGC / JDK 21 generational: VERIFIED
OpenJ9 XML / gencon: VERIFIED
JRockit verbose memory / R27: VERIFIED
Azul Prime legacy C4: VERIFIED
Azul Prime unified C4: VERIFIED
HPE historical variation X: NOT_TESTED
```

Do not manually maintain a marketing table that can drift from tests.

Generate it from corpus metadata.

---

# 63. Version applicability table

Maintain structured rules only for known impossibilities or strong consistency checks.

For example, version metadata may detect contradictions such as:

```text
collector claims CMS
runtime claims a version where that specific implementation cannot exist
```

However, because vendor backports exist, rules must include VM family/vendor where necessary.

A contradiction should normally become:

```text
DetectionWarning
```

before it becomes a hard failure.

---

# 64. Security requirements

Even though the program is local/offline:

- bind the UI to loopback by default
- escape raw log text in HTML
- never execute content from a GC log
- do not evaluate Python expressions from logs
- do not resolve XML external entities
- prevent path traversal in raw-file endpoints
- cap individual HTTP response sizes
- validate user-supplied paths
- do not accept arbitrary user-supplied `.duckdb` files as analysis documents in v1
- only reopen caches created by this application after validating application magic/schema version/source fingerprint
- do not execute SQL, macros, views, extensions, or other database-defined behavior from externally supplied DuckDB files
- configure DuckDB memory/thread/temp-disk limits
- disable network-dependent extension auto-installation/community extension loading for the offline application
- enforce bounded raw-line/record previews and decompression limits

DuckDB database files are application cache/state, not a safe interchange/document format. If importing third-party DuckDB files is ever added, it requires a separate threat model and sandboxing decision.

Gzip expansion is already in v1, so protect against gzip bombs/unbounded expansion now. If ZIP support is added later, also protect against zip bombs and traversal.

---

# 65. Offline dependency policy

Keep dependencies small.

Recommended runtime dependencies:

```text
duckdb
pyarrow
fastapi
uvicorn
plotly
```

`duckdb` is the embedded analytical database. `pyarrow` is used for efficient bounded columnar transfer from the streaming parser into DuckDB. Do not add Pandas merely as an ingestion bridge.

Prefer Python standard library for:

```text
dataclasses
re
gzip
xml.etree.ElementTree
statistics
argparse or a small CLI layer
```

Use additional dependencies only when they solve a concrete problem.

Development dependencies:

```text
pytest
pytest-cov
hypothesis
```

Optional static checks:

```text
ruff
mypy
```

Avoid requiring Pandas/NumPy unless profiling shows they materially simplify or improve analytics.

---

# 66. uv setup

Initial setup:

```bash
uv init --package universal-java-gc-viewer
uv add duckdb pyarrow fastapi uvicorn plotly
uv add --dev pytest pytest-cov hypothesis ruff mypy
uv lock
```

Commit:

```text
pyproject.toml
uv.lock
```

For a disconnected target machine, dependencies must already be available in the cache or a prepared wheelhouse/environment.

Use:

```bash
uv sync --offline --frozen
```

Do not assume `uv` can install uncached packages while offline.

For distribution to an air-gapped environment, prepare and document one of:

```text
pre-populated uv cache
local wheelhouse
prebuilt application bundle
internal artifact directory copied with the project
```

The build/release process should verify installation with networking disabled.

---

# 67. Local frontend assets

Prefer a packaged static frontend (`index.html` + local JS/CSS) served by FastAPI JSON endpoints. Do not introduce a server-side template engine unless a concrete requirement justifies it. This keeps the runtime dependency set smaller and the offline boundary easier to verify.

Never reference:

```html
<script src="https://...">
<link href="https://...">
```

All UI assets must be packaged locally.

If Plotly Python emits references to CDN assets by default, configure/export it so the JavaScript bundle is local.

Add an automated offline UI test that fails when generated HTML contains `http://` or `https://` external asset references, excluding plain documentation links shown as text.

---

# 68. Development phases

## Phase 0: repository and contracts

Implement:

```text
pyproject.toml
uv.lock
package skeleton
core enums
canonical cycle/operation/phase dataclasses
provenance model
error/warning model
fixture manifest schema
```

Acceptance gate:

- package installs with `uv sync`
- tests run
- canonical model has no dependency on FastAPI
- canonical model explicitly separates GC cycles, operations, and phases
- pause duration exists at operation/phase level; cycle total pause is a derived aggregate

---

## Phase 1: primitive parsers

Implement and heavily test:

```text
size parser
Java version parser
timestamp parser
legacy memory transition parser
JVM argument tokenizer
Unified Logging envelope tokenizer
```

Acceptance gate:

- property tests pass
- malformed input returns controlled errors
- time/duration parsing preserves exact integer-nanosecond values where the source provides that precision
- no collector-specific logic here

---

## Phase 2: streaming ingestion and segmentation

Implement:

```text
plain binary reader
gzip binary reader
line-oriented view for text grammars
structured-stream access for XML grammars
source-line numbering
offset tracking where practical
sparse source checkpoints
bounded lookahead/replay buffer
pre-scan
segment boundary scoring
max line/record byte limits
max expanded-input accounting
```

Acceptance gate:

- can stream a generated multi-gigabyte log with bounded memory
- concatenated fixture produces expected segment count
- gzip input is detected/parsed in one decompression pass
- OpenJ9 XML can be parsed directly from a bounded binary/decompressed stream without reconstructing XML from `SourceLine` objects
- lookahead memory is bounded by configured record and byte limits
- oversized line/record and excessive gzip expansion fail with controlled diagnostics

---

## Phase 3: detection framework

Implement:

```text
detector registry
candidate scoring
evidence/contradiction output
ambiguity handling
post-selection parser validation
```

Acceptance gate:

- all current fixtures identify the expected grammar
- intentionally ambiguous fixtures report ambiguity instead of arbitrary certainty

---

## Phase 4: HotSpot Unified Logging foundation

Implement:

```text
envelope parsing
GC ID extraction
metadata records
generic event assembly
unknown tag preservation
```

Start with one collector such as G1 because it has rich phase output.

Acceptance gate:

- selected G1 fixture parses with zero unexpected GC-like records
- raw source navigation works for parsed events

---

## Phase 5: HotSpot Unified collectors

Add, fixture by fixture:

```text
Serial
Parallel
G1
ZGC
Shenandoah
CMS only where an actual unified-era fixture/applicability exists
```

Do not create empty collector modules merely to check boxes.

Acceptance gate per collector:

- fixtures
- golden output
- coverage report
- semantic event types
- chart/analytics sanity tests

---

## Phase 6: HotSpot legacy Java 5-8

Implement reusable grammar components, then collectors:

```text
Serial
Parallel
CMS
G1
```

Acceptance gate:

- multiple logging-option combinations parse
- minimal and detailed logging both work
- no reliance on startup header

---

## Phase 7: historical HotSpot/Sun Java 1.2-1.4

Only begin after obtaining real fixtures.

Identify whether each fixture is:

```text
Sun Classic
HotSpot
other
```

Acceptance gate:

- support claims reflect exact verified family/grammar
- unavailable fields remain unknown rather than synthesized

---

## Phase 8: ZGC/Shenandoah version depth

Add targeted fixtures for major format/semantic transitions.

For ZGC include representative:

```text
early experimental/production-era logs where in scope
generational ZGC
modern post-non-generational-removal output
```

For Shenandoah include:

```text
classic concurrent mode
vendor backport examples where required
generational mode where available
```

Acceptance gate:

- mode/generation is taken from evidence when possible
- no version-only inference used as primary truth

---

## Phase 9: OpenJ9 / IBM

Implement:

```text
current XML verbose GC
deprecated verbose format
policy identification
VM args extraction
truncated XML recovery
```

Acceptance gate:

- XML fixture for each claimed OpenJ9 policy
- deprecated format fixtures
- no external entity resolution

---

## Phase 10: JRockit

Implement fixture-backed JRockit verbose grammar.

Start with:

```text
memory/gc
gcpause
gcreport
```

Acceptance gate:

- JRockit is never detected as HotSpot on corpus fixtures
- strategy text and pauses are preserved correctly

---

## Phase 11: Azul Prime

Implement:

```text
legacy C4 logs
Prime headers
C4 statistics
unified Prime logging
```

Acceptance gate:

- Azul Zulu HotSpot fixture follows HotSpot path
- Azul Prime fixture follows Prime path
- neither is confused with the other

---

## Phase 12: HP/HPE

Collect fixtures first.

Implement only observed differences.

Acceptance gate:

- capability report identifies exactly which HPE grammar/version examples are verified

---

## Phase 13: JVM argument normalization

Implement registries for:

```text
HotSpot
OpenJ9
JRockit
Azul Prime
```

Acceptance gate:

- every raw argument preserved
- known size arguments normalized
- unknown flags retained
- conflicting flags visible

---

## Phase 14: DuckDB analytical persistence

**Schema-freeze prerequisite:** do not freeze `gc_cycle`, `gc_operation`, `gc_phase`, or related analytical schemas until the canonical hierarchy has passed at least one representative G1 fixture, one ZGC fixture, and one OpenJ9 fixture. These fixtures must demonstrate that stop-the-world and concurrent work can coexist without flattening semantic distinctions.

Implement the DuckDB schema, bounded Arrow batch ingestion, typed event attributes, multiple event source spans, sparse source checkpoints, analytical query APIs, cache fingerprinting, resource/security configuration, and query/downsampling paths.

Do not make generic ART indexes part of the first implementation. Preserve natural time/source ordering and benchmark zonemap-assisted scans first. Add a selective ART index only when an actual UI query is measurably improved.

Acceptance gate:

- parser memory remains bounded on the large generated corpus
- normalized rows are ingested in bounded columnar batches
- no per-event transaction/commit loop exists
- large bulk ingestion does not use `executemany()`
- re-opening the `.duckdb` analysis cache reproduces metrics without reparsing the source when raw navigation is not required
- time-window and aggregate queries operate directly in DuckDB without materializing the complete event table in Python
- stale cache detection invalidates a database when the source identity or schema version changes
- concurrent viewer queries do not share an unsafe module-global DuckDB connection
- no arbitrary external `.duckdb` file is accepted as an analysis document
- DuckDB extension auto-installation/community extension loading is disabled for normal offline execution
- configured memory/thread/temp-directory limits are applied
- source navigation does not require one database row per physical log line
- persisted cycle/operation/phase relationships reproduce the in-memory canonical hierarchy without flattening concurrent work into one pause

---

## Phase 15: analytics

Implement common metrics first.

Then collector-specific observations.

Acceptance gate:

- metrics are tested with hand-calculable fixtures
- unavailable metrics report unavailable
- estimates are labeled

---

## Phase 16: viewer

Implement FastAPI JSON/query endpoints and a packaged static frontend with local assets.

Acceptance gate:

- works with network disabled
- binds to loopback by default
- large logs use downsampled/query-window data
- clicking an event displays its original source span(s), including non-contiguous spans when records were interleaved

---

## Phase 17: hardening and release report

Run:

```text
full corpus
fuzz/property tests
large-file benchmark
mixed/concatenated logs
corrupt/truncated logs
offline installation test
security checks
```

Generate capability report from corpus metadata.

Do not release under a “universal/full support” label if the capability report contains major unverified target families without an explicit disclosure.

---

# 69. Definition of done for one parser feature

A parser feature is done only when all are true:

```text
[ ] fixture exists
[ ] fixture identity is documented
[ ] detector identifies the grammar or ambiguity is expected
[ ] syntax records parse
[ ] event assembly succeeds
[ ] normalized values are asserted
[ ] source-span provenance is asserted
[ ] malformed variation is tested
[ ] coverage has no unexplained GC-like logical records
[ ] capability manifest updated
```

Do not count code presence as completion.

---

# 70. Definition of done for one runtime/collector claim

A claim such as:

```text
"HotSpot JDK 17 G1 Unified Logging supported"
```

requires at minimum:

```text
startup/header fixture where available
normal young collection
concurrent-cycle behavior where relevant
full/fallback collection if applicable
heap values
pause values
representative detailed logging
unknown-line coverage check
```

Not every fixture needs to contain every condition, but the claim must be backed by the set of fixtures.

---

# 71. Non-negotiable implementation rules

The weaker model must follow these rules exactly:

```text
DO NOT invent GC log syntax.

DO NOT synthesize unavailable values.

DO NOT use zero as unknown.

DO NOT store canonical durations or JVM-relative timestamps as floating-point seconds; use integer nanoseconds.

DO NOT equate a GC cycle with an application pause. A cycle may contain zero, one, or multiple stop-the-world operations plus concurrent operations.

DO NOT calculate pause analytics from total GC-cycle elapsed time. Pause analytics MUST use operations/phases explicitly known to stop application execution.

DO NOT silently ignore unknown GC-like logical records.

DO NOT select a parser from Java version alone.

DO NOT select a parser from vendor alone.

DO NOT equate vendor with VM implementation.

DO NOT equate OpenJ9 policies with HotSpot collectors.

DO NOT equate Azul C4 with ZGC, Shenandoah, G1, or Parallel GC.

DO NOT map JRockit collection strategies to HotSpot collectors without explicit evidence.

DO NOT write one giant regex for an entire family of GC logs.

DO NOT load the complete log into memory.

DO NOT create one DuckDB source-index row for every physical line by default.

DO NOT duplicate unbounded unknown/raw record text into DuckDB.

DO NOT make analytics parse raw strings.

DO NOT add parser behavior without a fixture.

DO NOT label an estimate as a logged value.

DO NOT claim a version/vendor/collector combination that is not represented in the capability matrix.

DO preserve source provenance.

DO preserve raw JVM arguments.

DO preserve unknown vendor-specific attributes.

DO report detector evidence and confidence.

DO retry/flag ambiguity when parser validation fails.

DO continue after recoverable malformed records.

DO use state machines/event assemblers when a cycle or operation spans multiple records.

DO preserve cycle -> operation -> phase relationships for concurrent collectors.

DO keep the core parser independent of the web UI.
```

---

# 72. Recommended first implementation slice

Do **not** start by attempting Java 1.2 through 27 simultaneously.

The first vertical slice should be:

```text
HotSpot Unified Logging
JDK 17
G1
plain text input
streaming parser
bounded Arrow batch ingestion
DuckDB output
basic metrics
minimal local UI
```

It must demonstrate the complete architecture:

```text
detection
parsing
event assembly
normalization
provenance
storage
analytics
viewer
coverage diagnostics
```

Then add grammars outward.

This is safer than first creating dozens of incomplete parser classes.

---

# 73. Second implementation slice

After the first slice is stable:

```text
HotSpot JDK 8 legacy
G1 + Parallel + CMS + Serial
```

This validates that the canonical model genuinely handles both legacy and unified grammars.

Only then proceed to low-latency collectors and vendor-specific VM families.

---

# 74. Recommended parser implementation style

Prefer small deterministic functions.

Good:

```python
def parse_memory_transition(text: str) -> MemoryTransition | None:
    ...
```

Good:

```python
def parse_unified_envelope(line: SourceLine) -> UnifiedEnvelope | None:
    ...
```

Good:

```python
class G1UnifiedInterpreter:
    def accept(self, record: ParsedRecord, state: EventState) -> None:
        ...
```

Avoid:

```python
def parse_everything(line):
    if "G1" in line:
        ...  # hundreds of branches
```

---

# 75. Regex policy

Regex is acceptable for local lexical patterns.

Use it for:

```text
memory transition token
GC ID
simple timestamp token
specific known payload phrase
```

Do not use a single enormous regex to parse an entire multi-field/multi-version event.

Compile frequently used patterns once.

Name capture groups.

Tests must include near-miss malformed strings.

---

# 76. Logging within the application

The analyzer itself should have structured diagnostic logging.

At minimum levels:

```text
ERROR
WARNING
INFO
DEBUG
TRACE-like parser diagnostic, optionally
```

Do not emit one application log line for every input GC line by default. That would make large-file parsing unusable.

Parser debug logging should be opt-in.

---

# 77. Failure model

Use typed errors/warnings.

Examples:

```text
InputOpenError
UnsupportedCompressionError
EncodingWarning
DetectionAmbiguousWarning
UnknownGrammarError
ParseWarning
TruncatedEventWarning
TruncatedXmlWarning
ContradictoryMetadataWarning
UnsupportedRecordWarning
```

A parse warning is not automatically a fatal failure.

At the end, summarize warnings by type.

---

# 78. Export model

Allow exporting normalized data to JSON initially.

Optional CSV exports can follow.

JSON should include a schema version:

```json
{
  "schema_version": 1,
  "runtime": {},
  "segments": [],
  "metrics": {},
  "events": []
}
```

Do not expose physical DuckDB row positions or implementation-specific internal identifiers as stable external identifiers.

Use stable event keys derived from segment/event identity.

---

# 79. Schema/version migration

Version both:

```text
canonical export schema
DuckDB analysis schema
```

Early development can recreate databases freely.

Because the DuckDB file is a derived cache, prefer explicit invalidation/rebuild across incompatible analysis-schema versions. Add migrations only if preserving previously generated analysis files becomes a real product requirement.

---

# 80. Final product claim policy

The UI/README should avoid saying:

```text
"Supports every Java GC log ever produced."
```

unless the capability evidence truly justifies it.

Prefer:

> Universal parser architecture with verified support across the listed JVM/log families. Exact verified combinations are shown by `gcviewer capabilities`.

If the product name remains **Universal Java GC Viewer**, the capability report is essential so “universal” describes the architecture/goal rather than an unverifiable promise.

---

# 81. Official references for implementation

Use official documentation as reference material while building fixtures and parsers. Because the implementation model is expected to run offline, copy only the necessary internal notes/spec observations into the repository before disconnecting; do not make runtime behavior depend on these websites.

## Oracle / OpenJDK

- Oracle JDK 27 `java` command and Unified Logging documentation:  
  https://docs.oracle.com/en/java/javase/27/docs/specs/man/java.html

- Oracle migration documentation covering changes from legacy GC logging to Unified Logging:  
  https://docs.oracle.com/en/java/javase/25/migrate/migrating-jdk-8-later-jdk-releases.html

- OpenJDK Unified GC Logging work is associated with JEP 271.

## OpenJ9

- OpenJ9 verbose GC logs:  
  https://eclipse.dev/openj9/docs/vgclog/

- OpenJ9 GC policies:  
  https://eclipse.dev/openj9/docs/gc/

- OpenJ9 `-Xgc` including the deprecated verbose format option:  
  https://eclipse.dev/openj9/docs/xgc/

- OpenJ9 `-Xlog` compatibility behavior:  
  https://eclipse.dev/openj9/docs/xlog/

## Oracle JRockit

- Oracle JRockit Diagnostics Guide, verbose memory/GC output:  
  https://docs.oracle.com/cd/E13150_01/jrockit_jvm/jrockit/geninfo/diagnos/verbose.html

## Azul Prime

- Azul Prime Unified GC Logging:  
  https://docs.azul.com/prime/Unified-GC-Logging.html

- Azul Prime C4 garbage collector:  
  https://docs.azul.com/prime/c4-garbage-collection.html

- Azul Prime GC log examples/details:  
  https://docs.azul.com/prime/Appendix-GC-Log-File

---

# 82. Final architecture summary

The final architecture should be understood as:

```text
                         INPUT
                           |
                           v
                       InputSource
                    /             \
                   v               v
          line-oriented view    binary stream
                   \               /
                    v             v
                       Segmenter
                           |
            +--------------+--------------+
            |                             |
            v                             v
       Segment N                       Segment N+1
            |                             |
            v                             v
      Evidence Detector             Evidence Detector
            |                             |
            v                             v
       Grammar Parser                 Grammar Parser
            |                             |
            v                             v
      Parsed Records                 Parsed Records
            |                             |
            +-------------+---------------+
                          |
                          v
                    Event Assembly
                          |
                          v
                 Semantic Normalization
                          |
                          v
                    Canonical Model
                          |
             +------------+------------+
             |            |            |
             v            v            v
           DuckDB      Coverage      Diagnostics
             |
       +-----+-----+
       |           |
       v           v
    Analytics    Local UI
```

The fundamental design rule is:

> **Parse the grammar that is actually present, preserve what the log actually says, normalize only when semantics are known, and prove every support claim with fixtures.**

That design can grow from a reliable JDK 17/G1 implementation into historical HotSpot, modern ZGC/Shenandoah, OpenJ9/IBM, Oracle JRockit, HP/HPE variants, and Azul Prime C4 without turning into an unmaintainable collection of version-specific regular expressions.
