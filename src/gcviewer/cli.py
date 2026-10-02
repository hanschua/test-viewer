"""Command-line entry point; support is reported from the fixture corpus."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .detection import default_registry
from .errors import GCViewerError
from .ingest import InputLimits, InputSource, line_lookahead
from .pipeline import UnifiedPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline Java GC viewer")
    parser.add_argument("--version", action="version", version="gcviewer 0.1.0")
    subcommands = parser.add_subparsers(dest="command")
    inspect = subcommands.add_parser("inspect", help="Inspect a bounded initial sample for grammar evidence")
    inspect.add_argument("log", type=Path)
    inspect.add_argument("--sample-records", type=int, default=256)
    inspect.add_argument("--sample-bytes", type=int, default=2 * 1024 * 1024)
    parse = subcommands.add_parser("parse", help="Stream captured-fixture-backed canonical G1 data as JSON lines")
    parse.add_argument("log", type=Path)
    args = parser.parse_args()
    if args.command == "inspect":
        try:
            limits = InputLimits(lookahead_records=args.sample_records, lookahead_bytes=args.sample_bytes)
            source = InputSource(args.log, limits)
            lines = source.iter_lines()
            try:
                sample, _ = line_lookahead(lines, limits)
                result = default_registry().detect(sample)
            finally:
                lines.close()
            print(json.dumps({"scope": "initial_sample", "sample_records": len(sample),
                              "sample_bytes": sum(line.raw_length_bytes for line in sample),
                              "detection": result.to_dict()}, indent=2))
        except (GCViewerError, ValueError) as exc:
            parser.exit(2, f"gcviewer: {exc}\n")
    elif args.command == "parse":
        try:
            pipeline = UnifiedPipeline(InputSource(args.log))
            for item in pipeline.stream():
                print(json.dumps({"schema_version": 1, "kind": type(item).__name__, "data": asdict(item)}))
            print(json.dumps({"schema_version": 1, "kind": "Coverage", "data": {
                **asdict(pipeline.coverage), "gc_recognition_ratio": pipeline.coverage.gc_recognition_ratio}}))
        except (GCViewerError, ValueError) as exc:
            parser.exit(2, f"gcviewer: {exc}\n")
    else:
        parser.print_help()
