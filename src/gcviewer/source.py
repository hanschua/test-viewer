"""Bounded raw navigation using exact original-file provenance."""

from dataclasses import dataclass
from pathlib import Path

from .errors import InputOpenError, ResourceLimitError
from .provenance import OffsetSpace, SourceSpan


@dataclass(frozen=True, slots=True)
class SourceExcerpt:
    span: SourceSpan
    text: str


def read_source_spans(path: Path, spans: tuple[SourceSpan, ...], max_bytes: int = 64 * 1024,
                      encoding: str = "utf-8") -> tuple[SourceExcerpt, ...]:
    path = Path(path).resolve()
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("source response limit must be positive")
    total = 0
    for span in spans:
        if span.source_id != str(path) or span.byte_span is None:
            raise InputOpenError("source span does not identify this source and an exact byte range")
        if span.byte_span.space != OffsetSpace.ORIGINAL_FILE:
            raise InputOpenError("expanded/working-copy offsets require a managed working copy; cannot seek original gzip")
        total += span.byte_span.end - span.byte_span.start
    if total > max_bytes:
        raise ResourceLimitError("raw source response exceeds configured byte limit")
    try:
        with path.open("rb") as stream:
            if stream.read(2) == b"\x1f\x8b":
                raise InputOpenError("gzip navigation requires a managed expanded working copy")
            excerpts = []
            for span in spans:
                stream.seek(span.byte_span.start)
                length = span.byte_span.end - span.byte_span.start
                raw = stream.read(length)
                if len(raw) != length:
                    raise InputOpenError("source span is outside the current file")
                excerpts.append(SourceExcerpt(span, raw.decode(encoding, errors="replace")))
            return tuple(excerpts)
    except OSError as exc:
        raise InputOpenError(str(exc)) from exc
