import pytest
from hypothesis import given, strategies as st

from gcviewer.errors import PrimitiveParseError
from gcviewer.primitives import parse_memory_transition, parse_unified_envelope, tokenize_jvm_arguments
from gcviewer.timestamps import parse_duration_ns, parse_uptime_ns, parse_wall_clock
from gcviewer.units import MAX_BYTES, parse_explicit_iec_size, parse_gc_log_size, parse_jvm_size
from gcviewer.versions import parse_java_version


@given(st.integers(min_value=0, max_value=MAX_BYTES // 1024))
def test_size_round_trip(value):
    parsed = parse_jvm_size(f"{value}k")
    assert parsed.bytes == value * 1024
    assert parsed.raw == f"{value}k"


def test_size_profiles_and_boundaries():
    assert parse_gc_log_size("1.5M").bytes == 1572864
    assert parse_explicit_iec_size("2GiB").bytes == 2147483648
    assert parse_jvm_size("0x40000000").bytes == 1073741824
    assert parse_jvm_size(str(MAX_BYTES)).bytes == MAX_BYTES
    for bad in ("-1", "1KB", "1.5g", "1GiB", "2gX", str(MAX_BYTES + 1), "9" * 200):
        with pytest.raises(PrimitiveParseError):
            parse_jvm_size(bad)
    with pytest.raises(PrimitiveParseError):
        parse_gc_log_size("0.1")
    with pytest.raises(PrimitiveParseError):
        parse_explicit_iec_size("2G")


@given(st.integers(min_value=0, max_value=MAX_BYTES))
def test_exact_nanosecond_round_trip(value):
    assert parse_duration_ns(f"{value // 10**9}.{value % 10**9:09d}") == value


def test_time_forms_and_wall_clock():
    assert parse_uptime_ns("123.456:") == 123456000000
    assert parse_uptime_ns("1.000000001s") == 1000000001
    assert parse_duration_ns("0.123456", "ms") == 123456
    a = parse_wall_clock("2026-10-02T12:34:56.123456789+0800")
    b = parse_wall_clock("2026-10-02T04:34:56.123456789Z")
    assert a.epoch_ns == b.epoch_ns
    assert a.epoch_ns % 10**9 == 123456789
    assert a.utc_offset_minutes == 480
    assert parse_wall_clock("2026-10-02T12:34:56").epoch_ns is None
    assert parse_wall_clock("1969-12-31T23:59:59.999999999Z").epoch_ns == -1


@pytest.mark.parametrize("text", ["-1", "NaN", "1e3", "1.0000000001", "9" * 100, "1x"])
def test_malformed_durations(text):
    with pytest.raises(PrimitiveParseError):
        parse_duration_ns(text)


@pytest.mark.parametrize("text", ["2026-02-30T00:00:00Z", "2026-10-02T12:00:00+2460", "garbage"])
def test_malformed_wall_clocks(text):
    with pytest.raises(PrimitiveParseError):
        parse_wall_clock(text)


def test_java_versions():
    old = parse_java_version("1.8.0_382-b05")
    assert (old.feature, old.update, old.build, old.raw) == (8, 382, 5, "1.8.0_382-b05")
    assert parse_java_version("1.4.2_19").feature == 4
    assert parse_java_version("8u382").update == 382
    assert parse_java_version("17.0.12+7").build == 7
    assert parse_java_version("27-ea+3").feature == 27
    with pytest.raises(PrimitiveParseError):
        parse_java_version("17garbage")


@given(st.integers(min_value=2, max_value=99))
def test_version_feature_round_trip(feature):
    assert parse_java_version(str(feature)).feature == feature


@given(st.integers(0, 1000000), st.integers(0, 1000000), st.integers(0, 1000000))
def test_transition_round_trip(before, after, capacity):
    memory = parse_memory_transition(f"{before}K->{after}K({capacity}K)")
    assert (memory.before_bytes, memory.after_bytes, memory.capacity_bytes) == (
        before * 1024, after * 1024, capacity * 1024)
    assert memory.committed_bytes is None


@pytest.mark.parametrize("text", ["1K->2K(3K)x", "-1K->2K(3K)", "1K->2K[3K]", "1Q->2K(3K)"])
def test_near_miss_transition(text):
    with pytest.raises(PrimitiveParseError):
        parse_memory_transition(text)


def test_argument_spelling_unknowns_and_quotes():
    text = '-Xmx2g  -Dname="a b" -XX:Unknown=x -Xmx4g'
    tokens = tokenize_jvm_arguments(text)
    assert [t.ordinal for t in tokens] == [0, 1, 2, 3]
    assert tokens[1].raw == '-Dname="a b"'
    assert tokens[1].value == '-Dname=a b'
    assert tokens[2].raw == '-XX:Unknown=x'
    assert tokens[3].raw == '-Xmx4g'
    with pytest.raises(PrimitiveParseError):
        tokenize_jvm_arguments('-Dname="unfinished')


@given(st.lists(st.from_regex(r"-X[a-z]{1,8}=[0-9]{1,4}", fullmatch=True), max_size=20))
def test_argument_round_trip(args):
    assert [token.raw for token in tokenize_jvm_arguments(" ".join(args))] == args


def test_envelope_generic_order_and_unknowns():
    envelope = parse_unified_envelope('[gc,start][host][info][2.345s][42] GC(7) Pause Young')
    assert envelope.uptime_ns == 2345000000
    assert envelope.tags == {"gc", "start"}
    assert envelope.level == "info"
    assert envelope.unknown_decorators == ("host", "42")
    assert envelope.payload == "GC(7) Pause Young"
    assert parse_unified_envelope('[gc] no timestamps').uptime_ns is None
    with pytest.raises(PrimitiveParseError):
        parse_unified_envelope('[info][gc broken')


@given(st.text(max_size=200))
def test_arbitrary_input_only_has_controlled_errors(text):
    for parser in (parse_jvm_size, parse_duration_ns, parse_java_version,
                   parse_memory_transition, parse_unified_envelope, tokenize_jvm_arguments):
        try:
            parser(text)
        except PrimitiveParseError:
            pass
