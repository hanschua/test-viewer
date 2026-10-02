import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

from gcviewer.corpus import load_manifest
from gcviewer.errors import ParseWarning, WarningCode
from gcviewer.models import (
    Collector, ExecutionMode, GCCycle, GCOperation, GCPhase, MemoryUsage,
    RuntimeIdentity, Vendor, VMFamily,
)
from gcviewer.provenance import ByteSpan, OffsetSpace, SourceSpan, ValueSource


def test_concurrent_cycle_keeps_distinct_pause_operations_and_phases():
    spans = (SourceSpan("log", 2, 2), SourceSpan("log", 6, 6))
    pause = GCOperation("1:7:pause", "1:7", execution_mode=ExecutionMode.STOP_THE_WORLD,
                        duration_ns=2_000_000, source_spans=spans,
                        phases=[GCPhase("1:7:phase", "mark", ExecutionMode.STOP_THE_WORLD,
                                        duration_ns=1_000_000)])
    work = GCOperation("1:7:work", "1:7", execution_mode=ExecutionMode.CONCURRENT,
                       duration_ns=98_000_000)
    cycle = GCCycle("1:7", 1, operations=[pause, work], elapsed_ns=100_000_000,
                    total_pause_ns=2_000_000)
    assert cycle.total_pause_ns != cycle.elapsed_ns
    assert cycle.aggregate_source == ValueSource.DERIVED
    assert cycle.operations[0].phases[0].duration_ns == 1_000_000
    assert cycle.operations[0].source_spans == spans
    assert "duration_ns" not in {f.name for f in dataclasses.fields(GCCycle)}


def test_unknowns_and_independent_identity():
    identity = RuntimeIdentity(vm_family=VMFamily.HOTSPOT, runtime_name_raw="OpenJDK 64-Bit Server VM")
    assert identity.vendor == Vendor.UNKNOWN
    assert identity.collector == Collector.UNKNOWN
    assert MemoryUsage().used_bytes is None
    assert GCCycle("1:unknown", 1).total_pause_ns is None
    assert GCOperation("op", "cycle").duration_ns is None
    assert Collector.OPENJ9_GENCON != Collector.G1
    assert Collector.AZUL_C4 != Collector.ZGC


@pytest.mark.parametrize("value", [1.5, -1, True])
def test_canonical_time_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        GCOperation("op", "cycle", duration_ns=value)


def test_parent_keys_and_mutable_defaults():
    with pytest.raises(ValueError):
        GCCycle("one", 1, operations=[GCOperation("op", "other")])
    first, second = GCCycle("one", 1), GCCycle("two", 1)
    first.attributes["vendor.metric"] = 42
    assert not second.attributes


def test_span_coordinate_spaces_and_stable_diagnostics():
    span = SourceSpan("gzip", byte_span=ByteSpan(4, 12, OffsetSpace.EXPANDED_STREAM),
                      grammar_locator="cycle[7]")
    assert span.byte_span.space != OffsetSpace.ORIGINAL_FILE
    warning = ParseWarning(WarningCode.TRUNCATED_XML, source_spans=(span,))
    assert warning.code.value == "TRUNCATED_XML"


@pytest.mark.parametrize("kwargs", [{"start_line": 0, "end_line": 1},
                                    {"start_line": 5, "end_line": 4}, {"start_line": 1}])
def test_bad_spans(kwargs):
    with pytest.raises(ValueError):
        SourceSpan("log", **kwargs)


def test_manifest_contract(tmp_path):
    entry = dict(id="g1", path="hotspot/g1.log", golden_path="hotspot/g1.json",
                 source_kind="captured", provenance="self-generated", license="CC0-1.0",
                 vendor="unknown", vm_family="hotspot", java_version="17", collector="g1",
                 grammar="hotspot_unified", logging_options=["-Xlog:gc"], features=["uptime"],
                 expected_unknown_gc_records=0, capability_state="experimental")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": 1, "fixtures": [entry]}))
    assert load_manifest(path)[0].collector == Collector.G1
    entry["path"] = "../escape.log"
    path.write_text(json.dumps({"schema_version": 1, "fixtures": [entry]}))
    with pytest.raises(ValueError):
        load_manifest(path)


def test_corpus_has_no_full_support_claims():
    entries = load_manifest(Path(__file__).parents[1] / "corpus/manifest.json")
    assert all(entry.capability_state.value != "verified" for entry in entries)


def test_core_import_does_not_import_web_framework():
    subprocess.run([sys.executable, "-c",
                    "import sys; import gcviewer.models; assert 'fastapi' not in sys.modules"],
                   check=True)
