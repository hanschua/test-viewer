"""Structured Java version tokens, independent of grammar selection."""

import re

from .errors import PrimitiveParseError
from .models import JavaVersion

OLD = re.compile(r"1\.(?P<feature>[2-8])(?:\.\d+)?(?:_(?P<update>\d+))?(?:-b(?P<build>\d+))?")
UPDATE = re.compile(r"(?P<feature>[5-8])u(?P<update>\d+)(?:-b(?P<build>\d+))?")
MODERN = re.compile(r"(?P<feature>[1-9]\d*)(?:\.(?P<interim>\d+))?(?:\.(?P<update>\d+))?(?:\.(?P<patch>\d+))?(?:-(?:ea|internal))?(?:\+(?P<build>\d+)(?:-[A-Za-z0-9.-]+)?)?")


def parse_java_version(text: str) -> JavaVersion:
    if len(text) > 128:
        raise PrimitiveParseError("version token too long")
    for pattern in (OLD, UPDATE, MODERN):
        match = pattern.fullmatch(text)
        if match:
            values = {key: int(value) if value is not None else None
                      for key, value in match.groupdict().items()}
            return JavaVersion(raw=text, **values)
    raise PrimitiveParseError("invalid Java version token")
