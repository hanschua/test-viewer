"""One-pass bounded input views. A source offers one consuming view at a time."""

import codecs
import gzip
import io
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

from .errors import InputOpenError, ResourceLimitError, UnsupportedCompressionError
from .provenance import OffsetSpace


@dataclass(frozen=True, slots=True)
class InputLimits:
    max_line_bytes: int = 1024 * 1024
    max_expanded_input_bytes: int = 64 * 1024**3
    checkpoint_interval: int = 4096
    lookahead_records: int = 256
    lookahead_bytes: int = 2 * 1024 * 1024

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 1 for value in (
            self.max_line_bytes, self.max_expanded_input_bytes, self.checkpoint_interval,
            self.lookahead_records, self.lookahead_bytes,
        )):
            raise ValueError("input limits must be positive integers")


@dataclass(frozen=True, slots=True)
class SourceLine:
    line_number: int
    byte_offset: int
    text: str
    source_id: str
    raw_length_bytes: int
    newline_kind: str | None
    offset_space: OffsetSpace
    decoding_error: bool = False


@dataclass(frozen=True, slots=True)
class SourceCheckpoint:
    line_number: int
    byte_offset: int
    offset_space: OffsetSpace


class BoundedReader(io.RawIOBase):
    """Caps every binary read and accounts for actual decompressed bytes."""

    def __init__(self, stream: BinaryIO, maximum: int):
        super().__init__()
        self.stream = stream
        self.maximum = maximum
        self.expanded_bytes = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        self._checkClosed()
        # Read at most one byte beyond the remaining budget to distinguish EOF.
        allowed = min(len(buffer), max(1, self.maximum - self.expanded_bytes + 1))
        try:
            data = self.stream.read(allowed)
        except (OSError, EOFError) as exc:
            raise InputOpenError(f"corrupt or unreadable input: {exc}") from exc
        self.expanded_bytes += len(data)
        if self.expanded_bytes > self.maximum:
            raise ResourceLimitError("maximum expanded input bytes exceeded")
        buffer[:len(data)] = data
        return len(data)


class ReplayReader(io.RawIOBase):
    """Replay a bounded binary prefix, then continue the same stream."""

    def __init__(self, prefix: bytes, stream: BinaryIO):
        super().__init__()
        self.prefix = memoryview(prefix)
        self.position = 0
        self.stream = stream

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        self._checkClosed()
        remaining = len(self.prefix) - self.position
        if remaining:
            count = min(len(buffer), remaining)
            buffer[:count] = self.prefix[self.position:self.position + count]
            self.position += count
            return count
        data = self.stream.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


def binary_lookahead(stream: BinaryIO, limits: InputLimits) -> tuple[bytes, BinaryIO]:
    """Structured grammars receive bytes directly, never rebuilt SourceLines."""
    prefix = bytearray()
    records = 0
    while len(prefix) < limits.lookahead_bytes and records < limits.lookahead_records:
        # Byte-at-a-time lookahead avoids consuming past a logical physical line cap.
        value = stream.read(1)
        if not value:
            break
        prefix.extend(value)
        records += value == b"\n"
    result = bytes(prefix)
    return result, io.BufferedReader(ReplayReader(result, stream))


def line_lookahead(lines: Iterator[SourceLine], limits: InputLimits):
    """Return a bounded sample and a replay iterator; no second decompression."""
    sample = []
    size = 0
    for _ in range(limits.lookahead_records):
        # Reserve the maximum possible next record before advancing the source.
        # This avoids a hidden oversized pending record outside the byte budget.
        if size + limits.max_line_bytes > limits.lookahead_bytes:
            break
        line = next(lines, None)
        if line is None:
            break
        if line.raw_length_bytes > limits.max_line_bytes:
            raise ResourceLimitError("lookahead record exceeds configured maximum line bytes")
        sample.append(line)
        size += line.raw_length_bytes

    def replay():
        yield from sample
        yield from lines

    return tuple(sample), replay()


class InputSource:
    def __init__(self, path: Path, limits: InputLimits = InputLimits()):
        self.path = Path(path)
        self.source_id = str(self.path.resolve())
        self.limits = limits
        self.compressed = False
        self.encoding = "utf-8"
        self.encoding_warning_count = 0
        self.expanded_bytes = 0

    @property
    def offset_space(self) -> OffsetSpace:
        return OffsetSpace.EXPANDED_STREAM if self.compressed else OffsetSpace.ORIGINAL_FILE

    @contextmanager
    def open_binary(self):
        try:
            raw = self.path.open("rb")
        except OSError as exc:
            raise InputOpenError(str(exc)) from exc
        with raw:
            signature = raw.read(4)
            raw.seek(0)
            if signature.startswith((b"PK\x03\x04", b"BZh", b"\xfd7z")):
                raise UnsupportedCompressionError("only plain input and gzip are supported")
            self.compressed = signature.startswith(b"\x1f\x8b")
            stream = gzip.GzipFile(fileobj=raw) if self.compressed else raw
            reader = BoundedReader(stream, self.limits.max_expanded_input_bytes)
            buffered = io.BufferedReader(reader)
            try:
                yield buffered
            finally:
                self.expanded_bytes = reader.expanded_bytes
                buffered.close()
                if self.compressed:
                    stream.close()

    def iter_lines(self, checkpoint_sink=None) -> Iterator[SourceLine]:
        """Stream physical lines with original byte lengths and sparse callbacks.

        Checkpoints are emitted to a caller-owned sink, never retained in an
        input-sized Python collection. Oversized records stop with a typed error.
        """
        with self.open_binary() as stream:
            prefix = stream.peek(4)[:4]
            bom_length, width = 0, 1
            if prefix.startswith(codecs.BOM_UTF32_LE):
                self.encoding, bom_length, width = "utf-32-le", 4, 4
            elif prefix.startswith(codecs.BOM_UTF32_BE):
                self.encoding, bom_length, width = "utf-32-be", 4, 4
            elif prefix.startswith(codecs.BOM_UTF16_LE):
                self.encoding, bom_length, width = "utf-16-le", 2, 2
            elif prefix.startswith(codecs.BOM_UTF16_BE):
                self.encoding, bom_length, width = "utf-16-be", 2, 2
            elif prefix.startswith(codecs.BOM_UTF8):
                self.encoding, bom_length = "utf-8", 3
            newline = "\n".encode(self.encoding)
            offset, number = 0, 1
            while True:
                if width == 1:
                    data = stream.readline(self.limits.max_line_bytes + 1)
                else:
                    retained = bytearray()
                    while len(retained) <= self.limits.max_line_bytes:
                        chunk = stream.read(width)
                        retained.extend(chunk)
                        if not chunk or chunk == newline:
                            break
                    data = bytes(retained)
                if not data:
                    break
                if len(data) > self.limits.max_line_bytes:
                    raise ResourceLimitError(f"maximum line bytes exceeded at line {number}")
                if checkpoint_sink and (number - 1) % self.limits.checkpoint_interval == 0:
                    checkpoint_sink(SourceCheckpoint(number, offset, self.offset_space))
                payload = data[bom_length:] if number == 1 else data
                decoding_error = False
                try:
                    text = payload.decode(self.encoding)
                except UnicodeDecodeError:
                    self.encoding_warning_count += 1
                    decoding_error = True
                    text = payload.decode(self.encoding, errors="replace")
                kind = "crlf" if text.endswith("\r\n") else "lf" if text.endswith("\n") else None
                text = text[:-2] if kind == "crlf" else text[:-1] if kind == "lf" else text
                yield SourceLine(number, offset, text, self.source_id, len(data), kind, self.offset_space, decoding_error)
                offset += len(data)
                number += 1
