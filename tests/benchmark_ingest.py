"""Generate and delete a multi-GiB source; report bounded Python memory and RSS.

Run explicitly: uv run python tests/benchmark_ingest.py --gib 2 --directory PATH
"""

import argparse
import json
import resource
import tempfile
import time
import tracemalloc
from pathlib import Path

from gcviewer.ingest import InputSource


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gib", type=int, default=2)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    record = b"application output " + b"x" * (4096 - 20) + b"\n"
    assert len(record) == 4096
    chunk = record * 256
    with tempfile.TemporaryDirectory(dir=args.directory) as directory:
        path = Path(directory) / "generated.log"
        with path.open("wb") as stream:
            for _ in range(args.gib * 1024):
                stream.write(chunk)
        tracemalloc.start()
        start = time.monotonic()
        count = sum(1 for _ in InputSource(path).iter_lines())
        elapsed = time.monotonic() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert count == args.gib * 1024 * 256
        assert peak < 8 * 1024**2, "parser memory exceeded bounded target"
        print(json.dumps({"input_bytes": path.stat().st_size, "lines": count,
                          "seconds": elapsed, "MiB_per_second": args.gib * 1024 / elapsed,
                          "peak_python_bytes": peak,
                          "peak_RSS_native_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))


if __name__ == "__main__":
    main()
