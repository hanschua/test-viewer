# Universal Java GC Viewer — Implementation Tracking Checklist

> **Purpose:** This file is a progress tracker for a weaker implementation model working fully offline.
>
> **Source of truth:** `plan.md` remains the design/specification. This checklist tracks implementation progress only. If this checklist conflicts with the plan, follow `plan.md`.
>
> **Rule:** Do not mark an item complete just because code exists. Mark it complete only when its acceptance checks pass.

---

## How to use this checklist

For every implementation session:

- [ ] Read the current phase and its acceptance gate before changing code.
- [ ] Work only on the earliest incomplete prerequisite unless the plan explicitly allows parallel work.
- [ ] Change `[ ]` to `[x]` only after the item is implemented **and tested**.
- [ ] If blocked, leave the item unchecked and add a short `BLOCKED:` note beneath it.
- [ ] If partially complete, leave it unchecked and add a short `PARTIAL:` note beneath it.
- [ ] Do not mark a phase complete until every required implementation item and acceptance check is complete.
- [ ] Update the capability manifest whenever parser support changes.
- [ ] Add or update fixtures before claiming parser/runtime/collector support.
- [ ] Run the relevant tests before ending the session.
- [ ] Record the current stopping point in **Progress Notes** at the end of this file.

### Status convention

```text
[ ] Not done
[x] Done

PARTIAL: <what remains>
BLOCKED: <why it cannot proceed>
NOTE: <important implementation detail>
```

---

# 0. Permanent Guardrails

These apply to **every phase**.

## Parsing and modeling

- [ ] Never invent GC log syntax.
- [ ] Never synthesize unavailable values.
- [ ] Never use `0` to represent unknown.
- [ ] Store canonical durations and JVM-relative timestamps as integer nanoseconds.
- [ ] Keep GC cycle, GC operation, and GC phase as separate concepts.
- [ ] Never treat entire GC-cycle elapsed time as application pause time.
- [ ] Calculate pause analytics only from explicitly stop-the-world operations/phases.
- [ ] Never silently discard unknown GC-like logical records.
- [ ] Never choose a parser from Java version alone.
- [ ] Never choose a parser from vendor alone.
- [ ] Keep vendor and VM implementation as separate dimensions.
- [ ] Never map OpenJ9 policies to HotSpot collectors.
- [ ] Never map Azul C4 to ZGC, Shenandoah, G1, Parallel, or other HotSpot collectors.
- [ ] Never map JRockit strategies to HotSpot collectors without explicit evidence.
- [ ] Avoid giant all-purpose GC regexes.
- [ ] Use state machines/event assemblers when events span multiple records.
- [ ] Preserve `cycle -> operation -> phase` relationships.
- [ ] Preserve source provenance.
- [ ] Preserve raw JVM arguments.
- [ ] Preserve unknown vendor-specific attributes.
- [ ] Report detector evidence/confidence.
- [ ] Flag/retry ambiguity when parser validation fails.
- [ ] Continue after recoverable malformed records.
- [ ] Keep parser core independent from FastAPI/web UI.

## Streaming, storage, and analytics

- [ ] Never load the complete GC log into memory.
- [ ] Keep lookahead/replay buffers bounded.
- [ ] Keep unknown/raw previews bounded.
- [ ] Do not create one DuckDB source-index row per physical input line by default.
- [ ] Do not duplicate unbounded raw source text into DuckDB.
- [ ] Do not make analytics reparse raw GC strings.
- [ ] Do not use per-event DuckDB commits for bulk ingestion.
- [ ] Do not use large `executemany()` ingestion paths.
- [ ] Do not add parser behavior without a fixture.
- [ ] Label estimates as estimates.
- [ ] Do not claim unsupported/unverified combinations.
- [ ] Treat the original GC log as source of truth.
- [ ] Treat the DuckDB database as a disposable/rebuildable analysis artifact.

---

# 1. Phase 0 — Repository and Contracts

## Implementation

- [x] Create `pyproject.toml`.
- [ ] Create and commit `uv.lock`.
  PARTIAL: Lock generated and frozen offline sync verified. Commit blocked: workspace is not a Git repository.
- [x] Create package skeleton.
- [x] Define core enums.
- [x] Define canonical `GCCycle` model.
- [x] Define canonical `GCOperation` model.
- [x] Define canonical `GCPhase` model.
- [x] Define provenance/source-span model.
- [x] Define error/warning model.
- [x] Define fixture manifest schema.

## Acceptance gate

- [x] `uv sync` succeeds offline using the prepared dependency environment/cache.
- [x] Test suite can be executed.
- [x] Canonical model has no FastAPI dependency.
- [x] Canonical model explicitly separates cycles, operations, and phases.
- [x] Pause duration belongs to operation/phase-level data.
- [x] Cycle total pause is represented as a derived aggregate.

## Phase complete

- [ ] **PHASE 0 COMPLETE**

---

# 2. Phase 1 — Primitive Parsers

## Implementation

- [x] Implement JVM/GC memory-size parser.
- [x] Implement Java version parser.
- [x] Implement exact timestamp/duration parser.
- [x] Implement legacy memory-transition parser.
- [x] Implement JVM argument tokenizer.
- [x] Implement Unified Logging envelope tokenizer.

## Tests

- [x] Add unit tests for normal values.
- [x] Add boundary tests.
- [x] Add malformed-input tests.
- [x] Add overflow/error tests for memory sizes.
- [x] Add exact nanosecond conversion tests.
- [x] Add near-miss regex/token tests.
- [x] Add property tests where appropriate.

## Acceptance gate

- [x] Property tests pass.
- [x] Malformed input produces controlled errors/`None` as designed, not crashes.
- [x] Exact source precision is retained using integer nanoseconds.
- [x] Primitive modules contain no collector-specific semantic logic.

## Phase complete

- [x] **PHASE 1 COMPLETE**

---

# 3. Phase 2 — Streaming Ingestion and Segmentation

## Input implementation

- [x] Implement plain binary reader.
- [x] Implement gzip binary reader.
- [x] Detect gzip by content/signature rather than filename alone.
- [x] Implement line-oriented view for text grammars.
- [x] Implement structured binary-stream access for XML grammars.
- [x] Track source-line numbers.
- [x] Track byte offsets where meaningful.
- [x] Distinguish original/expanded/working-copy offset spaces.
- [x] Implement sparse source checkpoints.
- [x] Implement bounded lookahead/replay buffer.
- [ ] Implement configurable maximum line/record bytes.
  PARTIAL: Physical-line limits implemented/tested; XML logical-record/text retention limits still required.
- [x] Implement configurable maximum expanded gzip input.
- [x] Implement expansion byte accounting.

## Segmentation

- [x] Implement segment-boundary evidence/scoring.
- [x] Detect probable new JVM startup banners.
- [x] Detect collector/startup declarations.
- [x] Detect new Unified Logging startup/header evidence.
- [x] Detect OpenJ9 XML roots.
- [ ] Detect JRockit startup markers where supported.
  BLOCKED: No captured/documentation-backed JRockit startup fixtures in the workspace.
- [ ] Detect Azul startup/header evidence where supported.
  BLOCKED: No captured/documentation-backed Prime startup fixtures in the workspace.
- [x] Treat large timestamp reset only as evidence, not proof.
- [x] Prefer one segment over destructive splitting when uncertain.

## Acceptance gate

- [x] Stream a generated multi-gigabyte log with bounded memory.
  NOTE: 2 GiB / 524,288 lines; 5.184 s, 395 MiB/s, 405,801 peak traced Python bytes, 22,528,000 peak RSS bytes on macOS. Ingestion only.
- [x] Concatenated fixture produces expected segment count.
  NOTE: Synthetic boundary-only tests, not evidence for a runtime/collector support claim.
- [x] Gzip input is decompressed only once during normal detection/parsing.
  NOTE: Verified for one-pass lookahead/replay ingestion; production detector/parser integration is still pending.
- [x] OpenJ9 XML can consume the binary/decompressed stream directly.
  NOTE: Direct streaming XML access is demonstrated, not production OpenJ9 parsing/security.
- [x] XML is not reconstructed from `SourceLine` objects.
- [x] Lookahead is bounded by record count.
- [x] Lookahead is bounded by byte count.
- [ ] Oversized records fail with controlled diagnostics.
  PARTIAL: Oversized physical lines are tested; structured XML logical-record limits remain pending.
- [x] Excessive gzip expansion fails with controlled diagnostics.

## Phase complete

- [ ] **PHASE 2 COMPLETE**

---

# 4. Phase 3 — Detection Framework

## Implementation

- [x] Implement detector registry.
- [x] Implement evidence objects.
- [x] Implement contradiction objects.
- [x] Implement candidate scoring.
- [x] Implement confidence handling.
- [x] Implement ambiguity handling.
- [x] Implement parser candidate ranking.
- [x] Implement post-selection parser validation.
  NOTE: Recognition-count validation API and failure/retry policy are tested. Wiring to the production parser's logical-record counters is part of Phase 4.
- [x] Preserve detection evidence for diagnostics/UI.
  NOTE: JSON diagnostics include scores, stable evidence/contradiction codes, line numbers, and exact source spans. `inspect` is an initial bounded sample only.

## Acceptance gate

- [x] Every currently supported fixture identifies the expected grammar.
  NOTE: Captured Temurin JDK 25 G1 detection-only fixture passes its golden. Root parser capability manifest remains empty; no normalized-event support is claimed.
- [x] Expected ambiguous fixtures remain ambiguous.
  NOTE: Framework-only scored-candidate fixture covers the plan's 0.72/0.68 example, exact ties, a distinct winner, and insufficient evidence. It does not claim Prime grammar support.
- [x] Detector does not choose arbitrary certainty for close candidates.
- [x] Java version alone cannot force a parser.
- [x] Vendor alone cannot force a parser.
- [x] Detection output includes evidence and contradictions.

## Phase complete

- [x] **PHASE 3 COMPLETE**
  NOTE: Framework and currently captured detection scope complete. Phase 2's previously recorded gaps remain open; proceeding with this phase was explicitly requested by the user.

---

# 5. Phase 4 — HotSpot Unified Logging Foundation

## Implementation

- [x] Parse Unified Logging bracket/decorator envelope generically.
- [x] Preserve unknown decorators.
  NOTE: Syntax records preserve decorators exactly; canonical events retain bounded previews with exact source provenance for full retrieval.
- [x] Parse log level.
- [x] Parse tag set.
- [x] Extract payload.
- [x] Extract `GC(n)` identifiers.
- [x] Parse metadata records.
- [x] Implement generic event assembly.
  NOTE: Separate bounded GC-ID assembler emits cycle/operation/phase hierarchy for captured G1 young and System.gc full pauses. Concurrent-cycle semantics remain a Phase 5 task.
- [x] Preserve unknown GC tag/payload records.
- [x] Start with a fixture-backed G1 implementation.
  NOTE: Unchanged JDK 25 capture is referenced by the root corpus manifest with a separate canonical golden and PARTIALLY_VERIFIED capability state. JDK 17 is not claimed.

## Provenance

- [x] Parsed records retain source spans.
- [x] Event assembly supports multiple/non-contiguous source spans.
- [x] Parsed events can navigate back to original source.
  NOTE: Exact, capped navigation is verified for plain sources. Expanded gzip offsets are rejected against the original compressed file; managed working-copy navigation remains a later task.

## Acceptance gate

- [x] Selected G1 fixture parses successfully.
  NOTE: 120 physical lines, 31 metadata records, 89 event-related records, six complete cycles/operations and 30 phases.
- [x] Selected G1 fixture has zero unexplained GC-like records.
- [x] Raw source navigation works for parsed events.
- [x] Concurrent/interleaved records are not flattened into one line range.
  NOTE: Interleaved captured GC(0)/GC(1) records navigate independently. Unknown future concurrent work stays explicit unknown data and contributes no fabricated pause time.

## Phase complete

- [x] **PHASE 4 COMPLETE**

---

# 6. Phase 5 — HotSpot Unified Collectors

Implement collectors **fixture by fixture**. Do not create empty placeholder modules.

## Serial

- [ ] Fixtures added.
- [ ] Golden outputs added.
- [ ] Semantic events implemented.
- [ ] Coverage clean.
- [ ] Analytics/chart sanity tests pass.
- [ ] Serial Unified support claim added to capability registry.

## Parallel

- [ ] Fixtures added.
- [ ] Golden outputs added.
- [ ] Semantic events implemented.
- [ ] Coverage clean.
- [ ] Analytics/chart sanity tests pass.
- [ ] Parallel Unified support claim added to capability registry.

## G1

- [ ] Fixtures added.
- [ ] Golden outputs added.
- [ ] Young pause semantics implemented.
- [ ] Mixed pause semantics implemented.
- [ ] Concurrent-cycle semantics implemented.
- [ ] Remark/cleanup semantics implemented where fixture-backed.
- [ ] Full/fallback collection semantics implemented where fixture-backed.
- [ ] Evacuation/to-space conditions preserved where fixture-backed.
- [ ] Coverage clean.
- [ ] Analytics/chart sanity tests pass.
- [ ] G1 Unified support claim added to capability registry.

## ZGC

- [ ] Fixtures added.
- [ ] Golden outputs added.
- [ ] Cycle semantics implemented.
- [ ] Pause phases represented separately.
- [ ] Concurrent phases represented separately.
- [ ] Allocation stalls handled where fixture-backed.
- [ ] Heap statistics handled where fixture-backed.
- [ ] Generation/mode taken from evidence where possible.
- [ ] Coverage clean.
- [ ] Analytics/chart sanity tests pass.
- [ ] ZGC Unified support claim added to capability registry.

## Shenandoah

- [ ] Fixtures added.
- [ ] Golden outputs added.
- [ ] Concurrent cycles represented.
- [ ] Pause phases represented.
- [ ] Degenerated GC represented where fixture-backed.
- [ ] Full GC represented where fixture-backed.
- [ ] Generation/mode taken from evidence where possible.
- [ ] Coverage clean.
- [ ] Analytics/chart sanity tests pass.
- [ ] Shenandoah Unified support claim added to capability registry.

## CMS

- [ ] Confirm an actual unified-era/applicable fixture exists before implementation.
- [ ] Implement only fixture-backed behavior.
- [ ] Update capability state accurately.

## Phase complete

- [ ] **PHASE 5 COMPLETE**

---

# 7. Phase 6 — HotSpot Legacy Java 5–8

## Shared legacy grammar

- [ ] Parse legacy timestamp prefix.
- [ ] Parse absolute date prefix.
- [ ] Parse event open/close structure.
- [ ] Parse collector tokens.
- [ ] Parse cause tokens.
- [ ] Parse memory transitions.
- [ ] Parse memory-pool sections.
- [ ] Parse duration.
- [ ] Parse CPU-time sections.
- [ ] Parse phase sections.

## Serial

- [ ] Fixture set exists.
- [ ] Minimal logging works.
- [ ] Detailed logging works.
- [ ] Multiple logging option combinations work.
- [ ] Golden output passes.
- [ ] Coverage clean.

## Parallel

- [ ] Fixture set exists.
- [ ] Minimal logging works.
- [ ] Detailed logging works.
- [ ] Multiple logging option combinations work.
- [ ] Golden output passes.
- [ ] Coverage clean.

## CMS

- [ ] Fixture set exists.
- [ ] Initial mark handled where present.
- [ ] Concurrent mark handled where present.
- [ ] Preclean handled where present.
- [ ] Remark handled where present.
- [ ] Sweep/reset handled where present.
- [ ] Promotion failure handled where present.
- [ ] Concurrent-mode failure handled where present.
- [ ] Full-GC fallback handled where present.
- [ ] Golden output passes.
- [ ] Coverage clean.

## G1

- [ ] Fixture set exists.
- [ ] Minimal logging works.
- [ ] Detailed logging works.
- [ ] Multiple logging option combinations work.
- [ ] Golden output passes.
- [ ] Coverage clean.

## Acceptance gate

- [ ] Parsing does not require startup header.
- [ ] Supported logging-option combinations are fixture-backed.
- [ ] Capability registry updated.

## Phase complete

- [ ] **PHASE 6 COMPLETE**

---

# 8. Phase 7 — Historical HotSpot/Sun Java 1.2–1.4

> Do not start implementation without real or faithfully captured fixtures.

## Fixture acquisition/classification

- [ ] Obtain Java 1.2-era fixture(s).
- [ ] Obtain Java 1.3-era fixture(s).
- [ ] Obtain Java 1.4-era fixture(s).
- [ ] Identify each fixture as Sun Classic, HotSpot, or other.
- [ ] Preserve exact vendor/runtime text.

## Implementation

- [ ] Implement only observed grammar differences.
- [ ] Keep unsupported/unverified formats explicitly unverified.
- [ ] Leave absent fields unknown.
- [ ] Do not synthesize modern fields into historical records.

## Acceptance gate

- [ ] Every support claim names the exact verified VM family/grammar.
- [ ] No guessed grammar is marked verified.
- [ ] Missing fields remain unknown.

## Phase complete

- [ ] **PHASE 7 COMPLETE**

---

# 9. Phase 8 — ZGC and Shenandoah Version Depth

## ZGC transitions

- [ ] Add early-era fixture(s) where in scope.
- [ ] Add representative production-era fixture(s).
- [ ] Add generational ZGC fixture(s).
- [ ] Add modern post-transition fixture(s).
- [ ] Verify mode/generation from log evidence where possible.
- [ ] Verify no primary inference from Java version alone.

## Shenandoah transitions

- [ ] Add classic concurrent-mode fixture(s).
- [ ] Add vendor-backport fixture(s) where required.
- [ ] Add generational-mode fixture(s) where available.
- [ ] Verify mode/generation from log evidence where possible.
- [ ] Verify no primary inference from Java version alone.

## Phase complete

- [ ] **PHASE 8 COMPLETE**

---

# 10. Phase 9 — OpenJ9 / IBM

## Current XML format

- [ ] Implement secure streaming XML parser.
- [ ] Parse initialization metadata.
- [ ] Parse GC policy.
- [ ] Parse system/runtime metadata.
- [ ] Parse VM arguments.
- [ ] Parse allocation-failure triggers where available.
- [ ] Parse cycle starts/ends.
- [ ] Parse GC operations.
- [ ] Parse concurrent increments.
- [ ] Parse memory values.
- [ ] Preserve XML element/cycle provenance.

## XML safety

- [ ] External entities are never fetched.
- [ ] Local-file entity resolution is disabled.
- [ ] Unexpected DTD/entity constructs fail safely.
- [ ] Retained element/text payloads are bounded.

## Truncated XML

- [ ] Recover complete events before truncation.
- [ ] Emit stable `TRUNCATED_XML` diagnostic.
- [ ] Never silently report incomplete XML as complete.

## Deprecated format

- [ ] Add fixture(s).
- [ ] Implement as a distinct grammar.
- [ ] Keep detection distinct from current XML.

## Policy coverage

- [ ] `gencon` fixture/claim.
- [ ] `balanced` fixture/claim.
- [ ] `optthruput` fixture/claim.
- [ ] `optavgpause` fixture/claim.
- [ ] `metronome` fixture/claim.
- [ ] `nogc` fixture/claim if supported by actual fixtures.
- [ ] Unknown OpenJ9 policy remains representable.

## Acceptance gate

- [ ] Every claimed policy has fixture evidence.
- [ ] Deprecated format has fixture evidence.
- [ ] No external entity resolution is possible.
- [ ] OpenJ9 is never mislabeled as a HotSpot collector family.

## Phase complete

- [ ] **PHASE 9 COMPLETE**

---

# 11. Phase 10 — JRockit

## Fixtures

- [ ] Obtain `-Xverbose:gc` / memory-GC fixture(s).
- [ ] Obtain `-Xverbose:gcpause` fixture(s).
- [ ] Obtain `-Xverbose:gcreport` fixture(s).

## Implementation

- [ ] Implement JRockit-specific grammar.
- [ ] Parse startup memory information where present.
- [ ] Parse heap/nursery information where present.
- [ ] Parse JVM-relative times.
- [ ] Parse before/after heap values.
- [ ] Parse pause durations.
- [ ] Parse report summaries.
- [ ] Preserve raw strategy text losslessly.
- [ ] Do not map strategy names to HotSpot collectors.

## Acceptance gate

- [ ] JRockit fixtures are never detected as HotSpot.
- [ ] Strategy text is preserved.
- [ ] Pause values match golden output.
- [ ] Capability registry reflects only fixture-backed support.

## Phase complete

- [ ] **PHASE 10 COMPLETE**

---

# 12. Phase 11 — Azul Prime

## Runtime distinction

- [ ] Add Azul Zulu/HotSpot fixture.
- [ ] Add Azul Prime/C4 fixture.
- [ ] Detector distinguishes Zulu HotSpot from Prime.

## Legacy Prime/C4

- [ ] Parse Prime headers.
- [ ] Parse legacy C4 logs.
- [ ] Parse C4 statistics where fixture-backed.
- [ ] Preserve Prime-specific attributes.

## Unified Prime

- [ ] Add Prime unified-log fixtures.
- [ ] Implement Prime unified grammar/payload differences.
- [ ] Do not assume stock HotSpot Java-version rules.

## Acceptance gate

- [ ] Zulu HotSpot fixture follows HotSpot path.
- [ ] Prime fixture follows Prime path.
- [ ] Zulu and Prime are not confused.
- [ ] C4 is never mapped to a HotSpot collector.

## Phase complete

- [ ] **PHASE 11 COMPLETE**

---

# 13. Phase 12 — HP/HPE

## Evidence first

- [ ] Collect verified HP-UX/HPE fixture(s).
- [ ] Identify VM family for each fixture.
- [ ] Test whether HotSpot legacy parser already works.
- [ ] Document actual deviations.

## Implementation

- [ ] Implement only observed HPE-specific differences.
- [ ] Avoid speculative HPE parser branches.

## Acceptance gate

- [ ] Capability report states exactly which HPE examples are verified.
- [ ] HPE HotSpot-compatible and genuinely HPE-specific grammar claims remain distinct.

## Phase complete

- [ ] **PHASE 12 COMPLETE**

---

# 14. Phase 13 — JVM Argument Normalization

## Shared argument handling

- [ ] Preserve every raw argument in order.
- [ ] Preserve argument ordinal.
- [ ] Parse argument name/value when recognized.
- [ ] Retain unknown flags.
- [ ] Keep category metadata.
- [ ] Preserve overridden/conflicting arguments.

## HotSpot registry

- [ ] Normalize `-Xms`.
- [ ] Normalize `-Xmx`.
- [ ] Normalize `-Xmn`.
- [ ] Normalize `-Xss`.
- [ ] Add fixture-backed `-XX:` memory settings.
- [ ] Normalize collector-selection flags.
- [ ] Normalize relevant GC thread/pause settings.

## OpenJ9 registry

- [ ] Normalize `-Xgcpolicy:`.
- [ ] Normalize `-Xms`.
- [ ] Normalize `-Xmx`.
- [ ] Normalize `-Xmn`.
- [ ] Normalize `-Xsoftmx`.
- [ ] Normalize `-Xgcthreads`.
- [ ] Add other options only when fixture-backed.

## JRockit registry

- [ ] Add fixture-backed known settings.
- [ ] Preserve all unknown settings.

## Azul Prime registry

- [ ] Add fixture-backed known settings.
- [ ] Preserve all unknown settings.

## Effective-value handling

- [ ] Compute an effective setting only when precedence is known.
- [ ] Record the winning raw argument.
- [ ] Keep losing/overridden arguments visible.

## Acceptance gate

- [ ] Every raw argument is preserved.
- [ ] Known size arguments normalize correctly.
- [ ] Unknown flags remain visible/searchable.
- [ ] Conflicting flags remain visible.
- [ ] No giant cross-vendor argument conditional is used.

## Phase complete

- [ ] **PHASE 13 COMPLETE**

---

# 15. Phase 14 — DuckDB Analytical Persistence

## Schema-freeze prerequisite

Do **not** freeze the analytical schema until all three are demonstrated:

- [ ] Representative G1 fixture passes through canonical hierarchy.
- [ ] Representative ZGC fixture passes through canonical hierarchy.
- [ ] Representative OpenJ9 fixture passes through canonical hierarchy.
- [ ] These examples demonstrate coexistence of STW and concurrent work without flattening.

## Schema

- [ ] `analysis_run`
- [ ] `source_file`
- [ ] `log_segment`
- [ ] `runtime_metadata`
- [ ] `jvm_argument`
- [ ] `jvm_setting`
- [ ] `source_checkpoint`
- [ ] `gc_cycle`
- [ ] `gc_cycle_source_span`
- [ ] `gc_operation`
- [ ] `gc_operation_source_span`
- [ ] `gc_phase`
- [ ] `memory_sample`
- [ ] `event_attribute`
- [ ] `parse_warning`
- [ ] `unknown_record`
- [ ] `capability_observation`

## Persistence rules

- [ ] Persist nanosecond timing as `BIGINT`.
- [ ] Preserve wall-clock epoch nanoseconds where needed.
- [ ] Preserve wall-clock UTC offset separately.
- [ ] Preserve multiple source spans.
- [ ] Use typed sparse `event_attribute` values.
- [ ] Use stable machine-readable warning codes.
- [ ] Store sparse source checkpoints, not one row per input line.
- [ ] Keep raw unknown-record preview bounded.
- [ ] Keep original source log outside DuckDB as source of truth.

## Ingestion

- [ ] Implement bounded Python row batches.
- [ ] Convert batches to PyArrow table/record batch.
- [ ] Bulk insert through DuckDB query from registered Arrow data.
- [ ] Discard batch after insertion.
- [ ] Benchmark batch sizes.
- [ ] Keep rows naturally ordered where practical.
- [ ] Avoid large `executemany()`.
- [ ] Avoid per-event transaction/commit loops.

## Query behavior

- [ ] Time-range queries execute in DuckDB.
- [ ] Aggregations execute in DuckDB where practical.
- [ ] Quantiles execute in DuckDB.
- [ ] Downsampling prequeries execute in DuckDB.
- [ ] Large event tables are not fully materialized in Python.

## Cache

- [ ] Implement source fingerprint.
- [ ] Include source identity.
- [ ] Include file size.
- [ ] Include modification time.
- [ ] Include hash/sample hash as designed.
- [ ] Include parser schema version.
- [ ] Include canonical model version.
- [ ] Invalidate stale caches.

## Security/offline behavior

- [ ] Use explicit DuckDB connection objects.
- [ ] Disable known-extension auto-installation.
- [ ] Disable community extension loading.
- [ ] Application does not issue network `INSTALL`.
- [ ] Arbitrary external `.duckdb` files are not treated as trusted GC Viewer analysis documents.

## Resource limits

- [ ] Configure DuckDB `memory_limit`.
- [ ] Configure DuckDB thread limit.
- [ ] Configure temp directory.
- [ ] Configure maximum temp-directory size.
- [ ] Resource-limit failures produce clear diagnostics.

## Concurrency

- [ ] One primary writer owns import.
- [ ] Viewer connections/cursors are scoped safely.
- [ ] No unsafe module-global shared connection.
- [ ] V1 does not depend on multiple independent writer processes.

## Acceptance gate

- [ ] Large generated corpus stays within bounded parser memory.
- [ ] Bounded columnar ingestion works.
- [ ] No per-event commit loop exists.
- [ ] Large ingestion does not use `executemany()`.
- [ ] Cache can reopen and reproduce metrics without reparsing when raw navigation is not required.
- [ ] Time/aggregate queries remain database-side.
- [ ] Stale cache is detected.
- [ ] Concurrent viewer querying follows safe connection rules.
- [ ] DuckDB extension auto-install/community loading is disabled.
- [ ] Configured resource limits are applied.
- [ ] Source navigation does not require per-line database indexing.
- [ ] Persisted hierarchy faithfully reconstructs cycle/operation/phase semantics.

## Phase complete

- [ ] **PHASE 14 COMPLETE**

---

# 16. Phase 15 — Analytics

## Common metrics

- [ ] Log-covered runtime.
- [ ] GC cycle count.
- [ ] STW operation/event count.
- [ ] Concurrent-cycle count.
- [ ] Full-collection count.
- [ ] Young-collection count.
- [ ] Total STW pause time.
- [ ] Maximum STW pause.
- [ ] Mean pause.
- [ ] Median pause.
- [ ] p90.
- [ ] p95.
- [ ] p99.
- [ ] p99.9 only when sample count is meaningful.
- [ ] Pause-time percentage.
- [ ] GC frequency.
- [ ] Heap-before trend.
- [ ] Heap-after trend.
- [ ] Heap reclaimed.
- [ ] Post-GC live-set trend.
- [ ] Concurrent-cycle duration where meaningful.

## Estimates

- [ ] Allocation-rate estimate only when sufficient evidence exists.
- [ ] Promotion-rate estimate only when sufficient evidence exists.
- [ ] Estimates are tagged `ESTIMATED`.
- [ ] Derivation is available.
- [ ] Sparse/incomplete data results in unavailable, not fake zero.

## Percentiles

- [ ] Select and document one quantile definition.
- [ ] Lock quantile semantics with tests.
- [ ] Verify DuckDB result with hand-calculable fixtures.

## Collector-specific observations

- [ ] CMS concurrent-mode failure.
- [ ] Promotion failure.
- [ ] G1 evacuation failure.
- [ ] G1 to-space exhaustion.
- [ ] Repeated Full GC.
- [ ] Metaspace-triggered collection.
- [ ] Humongous-region pressure.
- [ ] ZGC allocation stalls.
- [ ] Shenandoah degenerated GC.
- [ ] Shenandoah Full GC.
- [ ] Large pause outliers.
- [ ] Rising post-GC live set.
- [ ] High GC-time fraction.
- [ ] Every observation links back to evidence/source spans.
- [ ] Thresholds are configurable.

## Acceptance gate

- [ ] Common metrics pass hand-calculable fixture tests.
- [ ] Unavailable metrics explicitly report unavailable.
- [ ] Estimated metrics are clearly labeled.
- [ ] Pause analytics use STW operations/phases, not cycle elapsed time.

## Phase complete

- [ ] **PHASE 15 COMPLETE**

---

# 17. Phase 16 — Viewer

## Server

- [ ] Implement FastAPI JSON/query endpoints.
- [ ] Bind to `127.0.0.1` by default.
- [ ] Remote binding requires explicit opt-in.
- [ ] No telemetry.
- [ ] No CDN dependency.
- [ ] Network access is not required.

## Static frontend

- [ ] Package JavaScript locally.
- [ ] Package CSS locally.
- [ ] Package chart assets locally.
- [ ] Viewer works with network disabled.

## Large-log behavior

- [ ] UI requests time/query windows.
- [ ] UI uses downsampled datasets for large series.
- [ ] UI does not fetch the entire event table by default.
- [ ] Chart downsampling preserves important pause outliers.

## Source navigation

- [ ] Clicking an event displays original source span(s).
- [ ] Non-contiguous/interleaved source spans are supported.
- [ ] Uncompressed source can seek via offsets/checkpoints.
- [ ] Compressed analyze-only mode does not force decompressed copy.
- [ ] Optional interactive gzip navigation can use managed decompressed working copy.
- [ ] Working-copy creation respects disk limits.

## Phase complete

- [ ] **PHASE 16 COMPLETE**

---

# 18. Phase 17 — Hardening and Release Report

## Test matrix

- [ ] Run full fixture corpus.
- [ ] Run all golden-output tests.
- [ ] Run property tests.
- [ ] Run fuzz tests.
- [ ] Run malformed-input tests.
- [ ] Run mixed application/GC-log tests.
- [ ] Run concatenated-log tests.
- [ ] Run rotated/multi-run tests.
- [ ] Run corrupt-input tests.
- [ ] Run truncated-event tests.
- [ ] Run truncated OpenJ9 XML tests.
- [ ] Run gzip/resource-limit tests.
- [ ] Run large-file benchmark.
- [ ] Run offline installation test.
- [ ] Run network-disabled viewer test.
- [ ] Run security checks.

## Capability report

- [ ] Generate capability report from corpus metadata.
- [ ] Every VERIFIED claim has fixtures and goldens.
- [ ] Unsupported combinations are explicit.
- [ ] NOT_TESTED combinations are explicit.
- [ ] NOT_APPLICABLE combinations are explicit.
- [ ] Experimental/partial support is labeled accurately.
- [ ] Vendor is not inferred when only VM/build identity is known.

## Product claims

- [ ] README avoids claiming every GC log ever produced is supported.
- [ ] UI exposes exact verified capability combinations.
- [ ] `gcviewer capabilities` or equivalent exposes exact support state.
- [ ] “Universal” is presented as architecture/goal unless evidence supports broader claim.
- [ ] Major unverified target families are disclosed before release.

## Phase complete

- [ ] **PHASE 17 COMPLETE**
- [ ] **RELEASE HARDENING COMPLETE**

---

# 19. Required First Vertical Slice

Complete this before broad parser expansion:

```text
HotSpot Unified Logging
JDK 17
G1
plain text
streaming ingestion
bounded Arrow batch ingestion
DuckDB persistence
basic analytics
minimal local UI
```

## Vertical-slice checklist

- [ ] JDK 17 G1 fixture exists.
- [ ] Grammar detection works.
- [ ] Syntax parsing works.
- [ ] Event assembly works.
- [ ] Canonical normalization works.
- [ ] Source provenance works.
- [ ] DuckDB storage works.
- [ ] Basic analytics work.
- [ ] Minimal viewer works.
- [ ] Coverage diagnostics work.
- [ ] Unknown GC-like records are surfaced.
- [ ] End-to-end test passes.

- [ ] **FIRST VERTICAL SLICE COMPLETE**

---

# 20. Required Second Vertical Slice

After the first slice is stable:

```text
HotSpot JDK 8 legacy
G1
Parallel
CMS
Serial
```

## Checklist

- [ ] JDK 8 G1 legacy fixture support.
- [ ] JDK 8 Parallel legacy fixture support.
- [ ] JDK 8 CMS legacy fixture support.
- [ ] JDK 8 Serial legacy fixture support.
- [ ] Shared canonical model handles both Unified and legacy grammars.
- [ ] Shared analytics work across both grammar families.
- [ ] Shared storage schema works across both grammar families.
- [ ] Coverage/capability reporting distinguishes verified combinations.

- [ ] **SECOND VERTICAL SLICE COMPLETE**

---

# 21. Definition of Done — One Parser Feature

Copy this section for every new parser feature.

## Feature

```text
Name:
Grammar:
VM family:
Collector/policy:
Java/runtime version(s):
Fixture(s):
```

- [ ] Fixture exists.
- [ ] Fixture identity is documented.
- [ ] Detector identifies expected grammar, or ambiguity is intentionally expected.
- [ ] Syntax records parse.
- [ ] Event assembly succeeds.
- [ ] Normalized values are asserted.
- [ ] Source-span provenance is asserted.
- [ ] Malformed variation is tested.
- [ ] Coverage contains no unexplained GC-like logical records for the claim.
- [ ] Golden expected output exists.
- [ ] Capability manifest is updated.
- [ ] No unsupported fields are fabricated.
- [ ] Regression tests pass.

**Feature status:** `[ ] DONE`

---

# 22. Definition of Done — One Runtime/Collector Claim

Use this before marking a combination `VERIFIED`.

## Claim

```text
Example:
HotSpot JDK 17 G1 Unified Logging

VM family:
Vendor/distributor, if known:
Java version:
Grammar:
Collector/policy:
Capability state:
```

Evidence set:

- [ ] Startup/header fixture where available.
- [ ] Normal collection fixture.
- [ ] Concurrent-cycle fixture where relevant.
- [ ] Full/fallback collection fixture where applicable.
- [ ] Heap values represented where source provides them.
- [ ] Pause values represented where source provides them.
- [ ] Representative detailed logging fixture.
- [ ] Unknown-GC-record coverage checked.
- [ ] Golden outputs pass.
- [ ] Detection behaves correctly.
- [ ] Runtime/collector semantics behave correctly.
- [ ] Capability manifest accurately describes limitations.

**Claim status:** `[ ] VERIFIED`

---

# 23. Fixture Checklist

Use for each fixture added to the corpus.

```text
Fixture ID:
File:
Source/runtime:
VM family:
Vendor/distributor:
Java version:
Grammar:
Collector/policy:
Logging options:
Expected capability state:
```

- [ ] Fixture identity documented.
- [ ] Licensing/provenance is acceptable for repository use.
- [ ] Fixture is not silently modified after golden output is generated.
- [ ] Manifest entry exists.
- [ ] Expected detection result exists.
- [ ] Expected normalized output exists.
- [ ] Expected warning/unknown-record state exists.
- [ ] Source spans are testable.
- [ ] Fixture participates in corpus verification.

---

# 24. Session Completion Checklist

Run this before stopping work.

- [x] Relevant unit tests pass.
- [x] Relevant integration tests pass.
- [x] Relevant golden tests pass.
- [x] No new unexplained GC-like records were introduced.
- [x] Capability manifest updated if support changed.
- [x] Checklist updated.
- [x] Incomplete work remains unchecked.
- [x] Partial/blocking notes are recorded.
- [x] No support claim was added without fixture evidence.
- [x] No guardrail from Section 0 was violated.
- [x] Progress note added below.

---

# 25. Progress Notes

Append one entry after each implementation session.

## Entry template

```text
Date:
Current phase:
Completed:
- ...

Still incomplete:
- ...

Blocked:
- ...

Tests run:
- ...

Next exact task:
- ...
```

---

## Initial implementation session (Phases 0–2)

Date: 2026-10-02
Current phase: Phase 2 (partial)
Completed:
- Phase 0 implementation and acceptance checks: package, canonical hierarchy, provenance, identity enums, typed diagnostics, manifest loader, and generated lockfile.
- Phase 1 implementation and acceptance checks: exact sizes/times/versions, memory transitions, lossless argument tokens, generic Unified Logging envelopes, property/malformed/boundary tests.
- Phase 2 foundations: one-pass plain/gzip binary and line views, BOM decoding, explicit offset spaces, sparse checkpoints, conservative boundaries, bounded replay, input/line limits.
- Generated and streamed a 2 GiB application-output source with bounded memory; benchmark input removed automatically.

Still incomplete:
- Phase 0 lockfile commit; Phase 0 completion deliberately remains unchecked.
- Phase 2 structured-record retention bounds and fixture-backed JRockit/Azul startup markers.
- Phases 3–17 and both end-to-end vertical slices. There is no production GC parser, DuckDB analytical persistence, or viewer yet.

Blocked:
- Workspace is not a Git repository, so the required lockfile commit cannot be performed here.
- No real GC captures were supplied. Installed JVM is Temurin JDK 25, not the required first-slice JDK 17; historical and vendor-family fixtures are also absent.

Tests run:
- Bootstrapped uv 0.12.22 in an isolated temporary venv; populated development/build dependencies with an initial connected sync.
- `uv sync --offline --frozen` passed.
- `uv sync --offline --frozen --reinstall` passed, including rebuilding the local package from cached build dependencies.
- `uv run --offline --frozen pytest`: 49 passed, including Hypothesis and streaming/segmentation checks.
- `uv run --offline --frozen gcviewer --version`: gcviewer 0.1.0.
- `uv run --offline --frozen python tests/benchmark_ingest.py --gib 2 --directory <temporary directory>`: passed, 2,147,483,648 bytes, 524,288 lines, bounded memory.
- Commands used the bootstrapped uv executable under the harness temporary directory; uv is not installed globally.

Next exact task:
- Finish Phase 2 with bounded XML logical-record retention and fixture-backed startup markers; obtain captured JDK 17 G1 and vendor startup examples with provenance before expanding production grammar behavior.

## Phase 3 detection framework session

Date: 2026-10-02
Current phase: Phase 3 complete; Phase 2 gaps remain open

Completed:
- User requested proceeding with the next phase; implemented the detection framework while carrying forward the earlier prerequisite gaps.
- Registry with duplicate/wrong-grammar checks; evidence and contradiction objects; order-independent deduplicated scoring; configurable confidence thresholds; explicit ambiguity/unknown states.
- HotSpot Unified/G1 detector backed by a locally captured Temurin JDK 25.0.3+9 log, with detection golden and provenance/licensing notes.
- Exact evidence source spans, unknown distributor semantics, version metadata without version-based grammar selection, and conflicting startup metadata diagnostics.
- Post-selection recognition validation: insufficient evidence remains unvalidated; recognition collapse marks ambiguity and provides retry candidates without silently switching parser.
- Bounded `gcviewer inspect <log>` JSON command; one-pass gzip lookahead/detection/replay integration test.
- Framework-only ranking fixtures, captured detection golden, property tests, malformed-input and CLI error checks.

Still incomplete:
- Phase 2 XML logical-record bounds and fixture-backed vendor startup markers.
- Phase 0 lockfile commit (workspace still is not a Git repository).
- Phase 4 production syntax records, event assembly, canonical normalization and parser-validation integration.
- `inspect` currently probes the initial segment sample only; whole-file per-segment detection is not yet wired into a parser pipeline.
- JDK 17 first-slice capture and broader runtime/collector fixtures; no production GC parsing capability claim has been added.

Blocked:
- Vendor-family startup fixtures and the required JDK 17 fixture remain unavailable locally. The added JDK 25 capture does not substitute for a JDK 17 support claim.

Tests run:
- `uv sync --offline --frozen`: passed.
- `uv run --offline --frozen pytest`: 70 passed, including detection goldens, Hypothesis, CLI integration, source-provenance checks, and one-pass gzip detection/replay.
- `uv run --offline --frozen gcviewer inspect corpus/detection/jdk25-g1.log --sample-records 4`: selected HotSpot Unified / G1, Java 25, vendor unknown; sample bounded to 4 records / 192 bytes.
- JVM used only to generate the development fixture. Detection/tests use the retained log and do not require a JVM at runtime.

Next exact task:
- Phase 4: build fixture-backed Unified Logging syntax records and a separate G1 event assembler, preserving non-contiguous spans and exposing unknown GC records; wire logical-record counts into the validation API.
- Resolve the carried-forward Phase 2 structured-record bounds and vendor-marker gaps before marking Phase 2 complete.

## Phase 4 Unified Logging foundation session

Date: 2026-10-02
Current phase: Phase 4 complete; Phase 2 gaps remain open

Completed:
- Unified syntax records and a separate G1 assembler: startup metadata, collector declaration, event starts/summaries, named phases, region counts, metaspace/class-space transitions, workers and CPU timing.
- Captured G1 fixture canonical golden: five young pauses and one System.gc full pause, six operations and 30 phases, zero unexplained GC-like records.
- Bounded assembly with GC-ID correlation, distinct segment-local event keys, per-cycle record/open-cycle limits and diagnostic-bearing eviction/truncation.
- Multi-span/value provenance, bounded unknown previews/signatures, raw plain-source navigation, and safe rejection of gzip expanded offsets against the original compressed file.
- Streaming `gcviewer parse <log>` JSON-lines command with cycles, metadata, unknowns, warnings, segment validation reports and coverage.
- Per-segment detection/replay and repeated-startup handling demonstrated with concatenated captured runs; gzip analysis decompressed once.
- Integrated recognition validation. After the initial 500 GC-like records, a failed selection stops semantic assembly in that segment. Short segments validate at EOF; application output does not dilute the denominator.
- Encoding warnings carry exact source lines; recoverable malformed records do not abort later collections.
- Root corpus manifest now describes observed G1 features as PARTIALLY_VERIFIED. Missing CPU/timestamps remain None; logged pause durations are not substituted with rounded uptime deltas.

Still incomplete:
- Phase 5 collector expansion and G1 mixed/concurrent/remark/cleanup/failure coverage.
- Phase 2 XML logical-record retention bounds and fixture-backed vendor startup markers.
- Phase 0 lockfile commit; workspace still is not a Git repository.
- JDK 17 first vertical-slice fixture, DuckDB persistence, analytics and the viewer.
- Managed decompressed working-copy navigation for gzip sources; current gzip mode is analyze-only.

Blocked:
- JDK 17 and historical/vendor-family captures are not available locally. JDK 25 foundation coverage does not imply coverage for those targets.

Tests run:
- `uv sync --offline --frozen`: passed.
- `uv run --offline --frozen pytest`: 91 passed, including canonical/detection goldens, property tests, interleaving, malformed/truncated records, source navigation, missing timestamps/CPU, encoding recovery, gzip one-pass analysis, concatenated runs, bounded state, recognition-collapse handling and JSON-lines CLI integration.
- A streamed 100-run corpus produced 600 cycles while remaining below the test's 8 MiB traced Python-memory budget; results were consumed rather than retained.

Next exact task:
- Phase 5: capture representative Serial and Parallel Unified logs from the available JVM before adding collector-specific semantics and goldens; expand G1 fixture evidence for mixed/concurrent operations before claiming those features.
- Resolve carried-forward Phase 2 gaps before marking Phase 2 complete; retain the JDK 17 fixture requirement for the first end-to-end slice.
