import codecs
import gzip
import io
import xml.etree.ElementTree as ET

import pytest

from gcviewer.errors import InputOpenError, ResourceLimitError
from gcviewer.ingest import InputLimits, InputSource, binary_lookahead, line_lookahead
from gcviewer.provenance import OffsetSpace
from gcviewer.segment import discover_boundaries


def test_line_offsets_sparse_checkpoints_and_invalid_bytes(tmp_path):
    path = tmp_path / "input.log"
    path.write_bytes(codecs.BOM_UTF8 + b"first\r\nsecond\n\xfflast")
    source = InputSource(path, InputLimits(checkpoint_interval=2))
    checkpoints = []
    lines = list(source.iter_lines(checkpoints.append))
    assert [line.byte_offset for line in lines] == [0, 10, 17]
    assert [line.raw_length_bytes for line in lines] == [10, 7, 5]
    assert lines[0].text == "first"
    assert lines[0].newline_kind == "crlf"
    assert lines[-1].newline_kind is None
    assert source.encoding_warning_count == 1
    assert [point.line_number for point in checkpoints] == [1, 3]
    assert all(line.offset_space == OffsetSpace.ORIGINAL_FILE for line in lines)


@pytest.mark.parametrize("encoding,bom", [("utf-16-le", codecs.BOM_UTF16_LE),
                                         ("utf-16-be", codecs.BOM_UTF16_BE),
                                         ("utf-32-le", codecs.BOM_UTF32_LE),
                                         ("utf-32-be", codecs.BOM_UTF32_BE)])
def test_bom_line_views(tmp_path, encoding, bom):
    path = tmp_path / "encoded"
    path.write_bytes(bom + "a\r\nb\n".encode(encoding))
    source = InputSource(path)
    lines = list(source.iter_lines())
    assert [line.text for line in lines] == ["a", "b"]
    assert lines[1].byte_offset == len(bom) + len("a\r\n".encode(encoding))


def test_gzip_signature_replay_and_single_open(tmp_path, monkeypatch):
    path = tmp_path / "misleading.txt"
    path.write_bytes(gzip.compress(b"a\nb\nc\n"))
    source = InputSource(path)
    calls = []
    original = gzip.GzipFile

    def tracked(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(gzip, "GzipFile", tracked)
    sample, replay = line_lookahead(source.iter_lines(), InputLimits(lookahead_records=1))
    assert [line.text for line in sample] == ["a"]
    assert [line.text for line in replay] == ["a", "b", "c"]
    assert len(calls) == 1
    assert source.expanded_bytes == 6
    assert sample[0].offset_space == OffsetSpace.EXPANDED_STREAM


def test_bounded_lookahead_large_record_and_byte_count(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(b"abc\ndefgh\nlast\n")
    sample, replay = line_lookahead(InputSource(path).iter_lines(),
                                    InputLimits(lookahead_bytes=9, max_line_bytes=6))
    assert [line.text for line in sample] == ["abc"]
    assert sum(line.raw_length_bytes for line in sample) <= 9
    assert [line.text for line in replay] == ["abc", "defgh", "last"]
    sample, replay = line_lookahead(InputSource(path).iter_lines(), InputLimits(lookahead_bytes=1))
    assert not sample
    assert len(list(replay)) == 3


def test_direct_xml_binary_replay(tmp_path):
    path = tmp_path / "xml.txt"
    payload = b'<verbosegc>\n<cycle-start id="7"/>\n</verbosegc>'
    path.write_bytes(gzip.compress(payload))
    source = InputSource(path)
    with source.open_binary() as stream:
        sample, replay = binary_lookahead(stream, InputLimits(lookahead_bytes=10))
        assert len(sample) == 10
        with replay:
            tags = [element.tag for _, element in ET.iterparse(replay)]
    assert tags == ["cycle-start", "verbosegc"]
    assert source.expanded_bytes == len(payload)


def test_limits_are_controlled(tmp_path):
    path = tmp_path / "oversized"
    path.write_bytes(b"x" * 100)
    with pytest.raises(ResourceLimitError, match="line bytes"):
        list(InputSource(path, InputLimits(max_line_bytes=10)).iter_lines())
    path.write_bytes(gzip.compress(b"x" * 100))
    with pytest.raises(ResourceLimitError, match="expanded input"):
        list(InputSource(path, InputLimits(max_expanded_input_bytes=20)).iter_lines())
    path.write_bytes(b"\x1f\x8bcorrupt")
    with pytest.raises(InputOpenError):
        list(InputSource(path).iter_lines())


def test_conservative_segments(tmp_path):
    path = tmp_path / "concatenated"
    path.write_text('openjdk version "17"\n[0.001s][info][gc] Using G1\n'
                    '[70.000s][info][gc] GC(7) payload\n'
                    '[0.001s][info][gc] GC(8) payload\n'
                    'openjdk version "17"\n[0.001s][info][gc] Using G1\n'
                    '[1.000s][info][gc] GC(0) payload\n')
    boundaries = list(discover_boundaries(InputSource(path).iter_lines()))
    assert [boundary.start_line for boundary in boundaries] == [1, 5]
    assert boundaries[1].evidence[0].code == "JVM_VERSION_BANNER"


def test_binary_lookahead_record_limit():
    sample, replay = binary_lookahead(io.BytesIO(b"a\nb\nc"), InputLimits(lookahead_records=1))
    with replay:
        assert sample == b"a\n"
        assert replay.read() == b"a\nb\nc"


def test_xml_root_boundaries(tmp_path):
    path = tmp_path / "roots"
    path.write_text('<verbosegc version="test">\n<cycle-start id="1"/>\n</verbosegc>\n'
                    '<verbosegc version="test">\n<cycle-start id="2"/>\n</verbosegc>\n')
    boundaries = list(discover_boundaries(InputSource(path).iter_lines()))
    assert [boundary.start_line for boundary in boundaries] == [1, 4]
    assert boundaries[1].evidence[0].code == "OPENJ9_XML_ROOT"
