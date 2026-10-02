"""Validated fixture manifest contract; entries alone do not prove support."""

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .models import CapabilityState, Collector, LogGrammar, Vendor, VMFamily


@dataclass(frozen=True, slots=True)
class FixtureManifestEntry:
    id: str
    path: str
    golden_path: str
    source_kind: str
    provenance: str
    license: str
    vendor: Vendor
    vm_family: VMFamily
    java_version: str | None
    collector: Collector
    grammar: LogGrammar
    logging_options: tuple[str, ...]
    features: tuple[str, ...]
    expected_unknown_gc_records: int | None
    capability_state: CapabilityState

    def __post_init__(self) -> None:
        for path in (self.path, self.golden_path):
            candidate = PurePosixPath(path)
            if not path or candidate.is_absolute() or ".." in candidate.parts or "\\" in path:
                raise ValueError("fixture paths must be relative and cannot traverse")
        if not self.id or not self.provenance or not self.license:
            raise ValueError("fixture identity, provenance, and license are required")
        if self.source_kind not in {"captured", "documentation", "faithfully_captured"}:
            raise ValueError("unsupported fixture source kind")
        if (self.expected_unknown_gc_records is not None
                and (type(self.expected_unknown_gc_records) is not int or self.expected_unknown_gc_records < 0)):
            raise ValueError("unknown record count must be nonnegative integer")


def load_manifest(path: Path) -> tuple[FixtureManifestEntry, ...]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("fixtures"), list):
        raise ValueError("unsupported fixture manifest schema")
    entries = []
    ids = set()
    for raw in data["fixtures"]:
        values = dict(raw)
        for key, enum in (("vendor", Vendor), ("vm_family", VMFamily),
                          ("collector", Collector), ("grammar", LogGrammar),
                          ("capability_state", CapabilityState)):
            values[key] = enum(values[key])
        for key in ("logging_options", "features"):
            if not isinstance(values[key], list) or not all(isinstance(v, str) for v in values[key]):
                raise ValueError(f"{key} must be a string list")
            values[key] = tuple(values[key])
        entry = FixtureManifestEntry(**values)
        if entry.id in ids:
            raise ValueError("duplicate fixture ID")
        ids.add(entry.id)
        entries.append(entry)
    return tuple(entries)
