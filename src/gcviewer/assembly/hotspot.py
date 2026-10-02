"""Bounded GC-ID correlation for the currently captured G1 pause records."""

from collections import OrderedDict
from dataclasses import dataclass, field

from ..errors import ParseWarning, WarningCode
from ..models import Collector, CycleType, ExecutionMode, GCCycle, GCOperation, GCPhase, OperationType
from ..provenance import Provenance
from ..records import ParsedRecord, RecordKind


@dataclass(slots=True)
class PartialCycle:
    cycle: GCCycle
    operation: GCOperation
    records: int = 0
    spans: list = field(default_factory=list)
    phase_starts: dict[str, tuple] = field(default_factory=dict)
    ended: bool = False


class G1Assembler:
    def __init__(self, segment_id: int = 1, max_open_cycles: int = 64, max_records_per_cycle: int = 4096,
                 decorator_preview_bytes: int = 1024):
        if any(type(value) is not int or value < 1 for value in (segment_id, max_open_cycles, max_records_per_cycle, decorator_preview_bytes)):
            raise ValueError("assembler limits and segment ID must be positive integers")
        self.segment_id = segment_id
        self.max_open_cycles = max_open_cycles
        self.max_records_per_cycle = max_records_per_cycle
        self.decorator_preview_bytes = decorator_preview_bytes
        self.open_cycles = OrderedDict()
        self.sequence = 0

    def _finish(self, gc_id, truncated=False):
        state = self.open_cycles.pop(gc_id)
        cycle, op = state.cycle, state.operation
        cycle.source_spans = op.source_spans = tuple(state.spans)
        if truncated or not state.ended or state.phase_starts:
            cycle.warning_codes.append(WarningCode.TRUNCATED_EVENT.value)
            op.warning_codes.append(WarningCode.TRUNCATED_EVENT.value)
        if state.ended:
            cycle.total_pause_ns = op.duration_ns
            cycle.value_provenance["total_pause_ns"] = Provenance(
                op.value_provenance["duration_ns"].source_spans,
                "sum of explicitly stop-the-world operation durations")
        if op.uptime_start_ns is not None and op.uptime_end_ns is not None:
            if op.uptime_end_ns >= op.uptime_start_ns:
                cycle.elapsed_ns = op.uptime_end_ns - op.uptime_start_ns
                cycle.value_provenance["elapsed_ns"] = Provenance(cycle.source_spans, "logged end uptime minus logged start uptime")
            else:
                cycle.warning_codes.append(WarningCode.CONTRADICTORY_METADATA.value)
        yield cycle
        for code in cycle.warning_codes:
            if code == WarningCode.UNKNOWN_GC_RECORD.value:
                continue  # Already emitted per unknown logical record by the pipeline.
            yield ParseWarning(WarningCode(code), source_spans=cycle.source_spans, segment_id=self.segment_id)

    def accept(self, record: ParsedRecord):
        if record.gc_id is None or record.kind in {RecordKind.UNKNOWN, RecordKind.APPLICATION, RecordKind.BLANK}:
            return
        gc_id = record.gc_id
        if gc_id in self.open_cycles and self.open_cycles[gc_id].records >= self.max_records_per_cycle:
            yield from self._finish(gc_id, truncated=True)
        if gc_id not in self.open_cycles:
            if len(self.open_cycles) >= self.max_open_cycles:
                yield from self._finish(next(iter(self.open_cycles)), truncated=True)
            self.sequence += 1
            key = f"{self.segment_id}:{gc_id}:{self.sequence}"
            op = GCOperation(f"{key}:op:0", key, OperationType.COLLECTION)
            cycle = GCCycle(key, self.segment_id, gc_id=gc_id, collector=Collector.G1, operations=[op])
            self.open_cycles[gc_id] = PartialCycle(cycle, op)
        state = self.open_cycles[gc_id]
        cycle, op = state.cycle, state.operation
        state.records += 1
        state.spans.extend(record.source_spans)
        self.open_cycles.move_to_end(gc_id)
        if record.kind in {RecordKind.START, RecordKind.END}:
            # Only captured young/full pause titles acquire STW semantics.
            op.execution_mode = ExecutionMode.STOP_THE_WORLD
            full = record.name.startswith("Pause Full")
            cycle.cycle_type = CycleType.FULL_COLLECTION if full else CycleType.YOUNG_COLLECTION
            cycle.cause = "System.gc()" if full else record.name[len("Pause Young (Normal) ("):-1]
            cycle.value_provenance["cause"] = Provenance(record.source_spans)
            if record.kind == RecordKind.START:
                if op.uptime_start_ns is not None:
                    cycle.warning_codes.append(WarningCode.CONTRADICTORY_METADATA.value)
                op.uptime_start_ns = cycle.uptime_start_ns = record.uptime_ns
                cycle.wall_clock_start = record.wall_clock
                op.value_provenance["uptime_start_ns"] = Provenance(record.source_spans)
                cycle.value_provenance["uptime_start_ns"] = Provenance(record.source_spans)
            else:
                state.ended = True
                op.uptime_end_ns = cycle.uptime_end_ns = record.uptime_ns
                cycle.wall_clock_end = record.wall_clock
                op.duration_ns, op.heap = record.duration_ns, record.heap
                for name in ("duration_ns", "heap", "uptime_end_ns"):
                    op.value_provenance[name] = Provenance(record.source_spans)
                cycle.value_provenance["uptime_end_ns"] = Provenance(record.source_spans)
        elif record.kind == RecordKind.PHASE_START:
            state.phase_starts[record.name] = (record.uptime_ns, record.source_spans)
        elif record.kind == RecordKind.PHASE_END:
            start = state.phase_starts.pop(record.name, None)
            phase = GCPhase(f"{op.operation_key}:phase:{len(op.phases)}", record.name, op.execution_mode,
                            start_ns=start[0] if start else None, end_ns=record.uptime_ns,
                            duration_ns=record.duration_ns,
                            source_spans=(start[1] if start else ()) + record.source_spans)
            phase.value_provenance["duration_ns"] = Provenance(record.source_spans)
            phase.value_provenance["end_ns"] = Provenance(record.source_spans)
            if start is not None:
                phase.value_provenance["start_ns"] = Provenance(start[1])
            op.phases.append(phase)
        elif record.kind == RecordKind.MEMORY:
            op.metaspace = record.pools.get("metaspace")
            op.compressed_class_space = record.pools.get("compressed_class_space")
            for pool, memory in record.pools.items():
                if pool == "nonclass":
                    for name in ("before_bytes", "after_bytes", "committed_bytes"):
                        op.attributes[f"metaspace.nonclass.{name}"] = getattr(memory, name)
                        op.value_provenance[f"metaspace.nonclass.{name}"] = Provenance(record.source_spans)
                op.value_provenance[pool] = Provenance(record.source_spans)
        elif record.kind == RecordKind.CPU:
            op.cpu = record.cpu
            op.value_provenance["cpu"] = Provenance(record.source_spans)
        op.attributes.update(record.attributes)
        for name in record.attributes:
            op.value_provenance[name] = Provenance(record.source_spans)
        if record.unknown_decorators:
            name = f"unified.unknown_decorators_preview.{state.records}"
            op.attributes[name] = "|".join(record.unknown_decorators).encode()[:self.decorator_preview_bytes].decode(errors="ignore")
            op.value_provenance[name] = Provenance(record.source_spans)
        if state.ended:
            for phase in op.phases:
                phase.execution_mode = op.execution_mode
        if record.kind == RecordKind.CPU and state.ended:
            yield from self._finish(gc_id)

    def finish(self, truncated=False):
        while self.open_cycles:
            yield from self._finish(next(iter(self.open_cycles)), truncated=truncated)

    def note_unknown(self, record: ParsedRecord):
        """Correlate unknown evidence only to existing cycles, without inventing work."""
        if record.gc_id in self.open_cycles:
            state = self.open_cycles[record.gc_id]
            if state.records >= self.max_records_per_cycle:
                yield from self._finish(record.gc_id, truncated=True)
                return
            state.records += 1
            state.spans.extend(record.source_spans)
            if WarningCode.UNKNOWN_GC_RECORD.value not in state.cycle.warning_codes:
                state.cycle.warning_codes.append(WarningCode.UNKNOWN_GC_RECORD.value)
                state.operation.warning_codes.append(WarningCode.UNKNOWN_GC_RECORD.value)
