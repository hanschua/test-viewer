# Universal Java GC Viewer

An offline Java GC analysis project implementing `plan.md`. “Universal” describes
the architecture and goal. Current parsing support is partially verified for the
captured HotSpot JDK 25 G1 young/System.gc full-pause fixture. Other grammars and
broader collector/version coverage remain unverified.

## Development

Python 3.12+ and `uv` are required. Prepare dependencies on a connected machine:

```sh
uv sync
```

With the populated cache, install and test without network access:

```sh
uv sync --offline --frozen
uv run --offline --frozen pytest
uv run --offline --frozen gcviewer --version
```

The core models have no runtime dependencies and do not import a web framework.
Canonical relative time is integer nanoseconds and memory is bytes. Missing values
remain `None`. Cycles contain separate operations and phases; cycle total pause is
a derived aggregate, never the duration of the entire concurrent cycle. Source
ranges can be non-contiguous and byte offsets always name their coordinate space.

`corpus/manifest.json` is the versioned fixture manifest. Fixture identity,
provenance, licensing, expected goldens and capability state are required. No
support claim should be added before fixture and golden verification.

Implementation progress and acceptance results are recorded in
`universal-java-gc-viewer-checklist.md`.

## Implemented primitives and ingestion

Size parsing uses separate JVM, HotSpot GC, and explicit IEC profiles. Tokens
outside signed 64-bit storage bounds produce controlled parse errors. Decimal
time conversion uses integer arithmetic and rejects nonzero sub-nanosecond
precision instead of rounding. Wall-clock tokens without a UTC offset retain
their text but do not acquire an invented epoch.

Input uses signature-based gzip detection, a single decompression pass, explicit
expanded-byte and physical-record limits, BOM-aware decoding, sparse checkpoint
callbacks, and bounded replay. Line lookahead reserves the maximum record size
before advancing its iterator; a byte budget smaller than that size returns an
empty sample, never an oversized buffer. Structured grammars can consume a binary
replay stream directly. Structured XML record retention/security is not yet
implemented and no OpenJ9 parser support is claimed.

Run the explicit generated-input benchmark (the temporary input is deleted):

```sh
uv run --offline --frozen python tests/benchmark_ingest.py --gib 2 --directory /path/to/temp
```

The initial 2 GiB run on this machine processed 524,288 physical lines at 395
MiB/s under allocation tracing, with 405,801 bytes of peak traced Python memory
and 22,528,000 bytes peak RSS. This measures ingestion only, not GC parsing or
DuckDB throughput.

## Grammar detection

```sh
uv run --offline --frozen gcviewer inspect corpus/detection/jdk25-g1.log
```

`inspect` probes only a bounded initial sample and reports JSON candidate scores,
evidence, contradictions, identity metadata, and exact source spans. It does not
yet scan/re-detect every segment in the file. The default registry currently
contains the captured-fixture-backed HotSpot Unified/G1 detector only. Unknown
grammars remain unknown. A generic GC envelope without family-specific evidence
is insufficient to select a parser.

Scores are additive, capped heuristic weights, not calibrated probabilities.
Repeated evidence codes contribute only their maximum weight. Default selection
requires a score of at least 0.7; another nonzero candidate within 0.1 prevents
selection. Contradictions also prevent selection. Java version and vendor alone
cannot select a grammar. The captured JDK 25 log has a version token but no
distributor declaration, so vendor remains unknown.

The parser-validation API consumes recognized/unknown **logical GC-like record**
counts. At least 20 such records are required, with a minimum recognition ratio
of 0.8. Insufficient data remains unvalidated. Recognition collapse clears the
selection, retains a stable diagnostic, and suggests other candidates for retry;
it never silently switches to an unvalidated parser. The streaming pipeline now
wires these counters into validation, using the first 500 GC-like records (or a
larger user-configured minimum). On recognition collapse it clears the selection
and stops semantic assembly for that segment; subsequent records remain visible
as unknowns. Shorter segments are validated at EOF when enough records exist.

`corpus/detection/manifest.json` and its detection goldens are separate from the
root parser capability manifest. The root manifest now references the same
unchanged capture and a separate canonical golden for partially verified G1
foundation features. Detection-only claims do not imply event coverage.

## Streaming G1 parsing

```sh
uv run --offline --frozen gcviewer parse corpus/detection/jdk25-g1.log
```

`parse` emits JSON lines containing canonical cycles, metadata records, bounded
unknown records, typed warnings, segment reports and final coverage. It does not
yet create a DuckDB analysis cache or serve a viewer. The API is
`UnifiedPipeline(InputSource(path)).stream()`; consume the iterator rather than
collecting a large log into a list.

The captured fixture yields six cycles, six explicitly STW operations, and 30
phases, with zero unexplained GC records. Logged durations, CPU times and heap
transitions remain separate from derived aggregates. In particular, rounded
uptime deltas need not equal the logged pause duration. Missing CPU/timestamp
values remain unknown; region counts are not silently converted to generation
memory sizes. Full parsing claims are limited to the observed System.gc pause,
not every possible G1 fallback or cause.

Assembly is bounded by configurable open-cycle and per-cycle record limits.
Evicted or truncated state produces diagnostic-bearing events. GC IDs correlate
interleaved records without combining their source spans into inclusive ranges.
Unknown tags and malformed payloads remain source-backed diagnostics. Unknown
decorators are preserved in syntax records; canonical events retain bounded
previews plus exact source references for full retrieval.

The pipeline detects captured startup boundaries, re-detects each segment, and
keeps repeated GC IDs across restarts separate. Plain and gzip input use one
streaming pass. `read_source_spans()` provides capped, exact navigation into a
plain original source. It rejects expanded-stream offsets against original gzip
files; managed decompressed working-copy navigation remains pending.
