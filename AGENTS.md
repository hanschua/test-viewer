# Repository instructions

## Plans and progress

- The design is `plans/plan.md`; progress and acceptance gates are in
  `plans/universal-java-gc-viewer-checklist.md`. Read the relevant gate and latest
  **Progress Notes** before implementation; distinguish planned features from code.
- Follow the earliest unfinished prerequisite unless explicitly directed otherwise.
  Mark checklist items complete only after acceptance checks pass, and record
  partial/blocking work and the next exact task in Progress Notes.
- Historical “not a Git repository” / lockfile-commit blockers are stale: this
  repository already tracks `uv.lock`.
- Phase 2 still carries XML logical-record retention and vendor-startup fixture
  gaps. The required first vertical slice needs its own JDK 17 G1 capture; the
  existing JDK 25 capture does not establish JDK 17 support.
- Before freezing DuckDB analytical schemas, the plan requires representative
  G1, ZGC and OpenJ9 fixtures demonstrating separate STW and concurrent work
  (`plans/plan.md`, Phase 14).

## Development commands

Python 3.12+ and `uv` are required. Populate dependencies/build requirements with
`uv sync` on a connected machine before using the frozen offline workflow.
Run from the repository root:

```sh
uv sync --offline --frozen
uv run --offline --frozen pytest
uv run --offline --frozen pytest tests/test_unified.py tests/test_detection.py
uv run --offline --frozen pytest tests/test_unified.py::test_corpus_canonical_goldens
uv run --offline --frozen gcviewer inspect corpus/detection/jdk25-g1.log
uv run --offline --frozen gcviewer parse corpus/detection/jdk25-g1.log
```

- Actual dev dependencies are pytest and Hypothesis; the plan's Ruff/mypy/
  pytest-cov setup is not installed/configured in `pyproject.toml`.
- The explicit benchmark generates/deletes 2 GiB and measures ingestion only:
  `uv run --offline --frozen python tests/benchmark_ingest.py --gib 2 --directory /existing/temp/dir`.

## Execution and data contracts

- `gcviewer.cli:main` exposes `inspect` (bounded initial sample) and `parse`
  (streaming JSON lines). Storage, analytics, the viewer and other planned CLI
  commands are pending; the current package has no runtime dependencies.
- `src/gcviewer/pipeline.py` connects ingestion, per-segment detection/validation,
  syntax in `src/gcviewer/parsers/hotspot/unified.py`, and semantics in
  `src/gcviewer/assembly/hotspot.py`. Collector expansion must update
  `detection.default_registry()` and the pipeline's hardwired `UnifiedParser` /
  `G1Assembler`, as well as parser/assembler behavior.
- Consume `UnifiedPipeline(InputSource(path)).stream()` incrementally; a pipeline
  is single-use and final coverage requires exhaustion. Keep lookahead, assembly
  state and diagnostic previews bounded, and gzip detection/parsing single-pass.
- `line_lookahead` reserves `InputLimits.max_line_bytes` before advancing. An
  `inspect --sample-bytes` budget below the default 1 MiB record limit yields an
  empty sample even if actual lines are short.
- Canonical times/durations are integer nanoseconds, memory is bytes, and missing
  values stay `None`. Use the distinct size profiles in `units.py` and exact time
  parsers in `timestamps.py`; nonzero sub-nanosecond precision is rejected.
- Keep `GCCycle -> GCOperation -> GCPhase` separate. Pause totals derive from
  explicitly STW work, never whole-cycle elapsed time; rounded uptime deltas can
  differ from logged durations. Keep core models/primitives independent of the UI.
- Preserve non-contiguous source spans and their byte-offset coordinate space.
  `read_source_spans()` navigates plain originals; expanded gzip offsets require
  a managed working copy, which is still pending.
- Select grammars from evidence, retaining ambiguity/contradictions. Vendor and
  Java version alone cannot select a parser; OpenJDK/OpenJ9 identity does not
  establish a distributor. Surface unknown GC-like records with stable diagnostics;
  coverage/validation count logical GC-like records, not application/physical lines.

## Fixture-first parser work

- Production syntax needs captured/documentation-backed fixtures, provenance and
  licensing, normalized goldens, malformed variations and source-span assertions.
  Synthetic ranking/boundary/negative tests do not establish runtime support.
- `corpus/manifest.json` governs parser capabilities; `corpus/detection/manifest.json`
  and detection goldens verify detection separately. Paths are relative to each
  manifest's directory. Update capability metadata when support changes; current
  parsing is `PARTIALLY_VERIFIED` for captured JDK 25 G1 young/System.gc full pauses.
- Retain captured logs verbatim: goldens assert exact durations and source lines.
  `.gitignore` ignores `*.log`; new captures need `git add -f <fixture.log>` to stage.
- Tests use retained captures and need no JVM. A JDK is needed only for capture;
  see `corpus/detection/README.md` and `tools/CaptureGC.java` for reproduction.
