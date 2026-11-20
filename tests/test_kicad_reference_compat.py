
import pytest
from kiutils.board import Board
from kiutils.utils.sexpr import parse_sexp

from scripts.kicad_reference_compat import normalize_named_reference
from scripts.intake_holdout import prepare_spec

from test_holdout_protocol import synthetic_board


def test_mixed_named_and_numeric_reference_preserves_connectivity_and_geometry():
    numeric = synthetic_board()
    source = numeric.replace(b'(net 1 "sig")', b'(net "sig")', 3)
    source = source.replace(b'(net 1)', b'(net "sig")').replace(b'(net_name "sig") ', b'')
    source = source.replace(b'(net "sig")\n', b'(net "sig") (net 7 "other")\n', 1)
    normalized, counts = normalize_named_reference(source)
    assert counts == {"pad": 2, "segment": 1, "via": 1, "zone": 1}
    assert prepare_spec(normalized, 6) == prepare_spec(source, 6)
    before = Board.from_sexpr(parse_sexp(numeric.decode()))
    after = Board.from_sexpr(parse_sexp(normalized.decode()))
    signal, = [net for net in after.nets if net.name == "sig"]
    assert signal.number == 8
    assert all(item.net == signal.number for item in after.traceItems)
    assert [item.netName for item in after.zones] == ["sig"]
    assert [item.position for item in after.footprints] == [item.position for item in before.footprints]
    assert [(part.type, part.shape, part.position, part.size)
            for fp in after.footprints for part in fp.pads] == [
                (part.type, part.shape, part.position, part.size)
                for fp in before.footprints for part in fp.pads
            ]
    def without_nets(node):
        if not isinstance(node, list):
            return node
        return [without_nets(item) for item in node if not (
            isinstance(item, list) and item[0] in ("net", "net_name")
        )]
    assert without_nets(parse_sexp(source.decode())) == without_nets(parse_sexp(normalized.decode()))


def test_legacy_input_is_byte_preserved():
    raw = synthetic_board()
    assert normalize_named_reference(raw) == (raw, {})


def test_reject_ambiguous_zone_assignment():
    raw = synthetic_board().replace(b'(net 1) (net_name "sig")', b'(net "other") (net_name "sig")')
    with pytest.raises(ValueError, match="Zone name"):
        normalize_named_reference(raw)


def test_reject_conflicting_named_and_numbered_pad():
    raw = synthetic_board().replace(b'(net 1 "sig") (pinfunction', b'(net 1 "surprise") (pinfunction')
    raw = raw.replace(b'(net 1))', b'(net "sig"))', 1)
    with pytest.raises(ValueError, match="ambiguous"):
        normalize_named_reference(raw)


def test_precise_poses_and_escaped_label_survive_serialization():
    raw = synthetic_board().replace(b'(net 1 "sig")', b'(net "sig")')
    raw = raw.replace(b'(net 1)', b'(net "sig")')
    raw = raw.replace(b'(start 21 10)', b'(start 0.000001 10)')
    raw = raw.replace(b'source-only annotations', r'unicode μ \ path \"quoted\"'.encode())
    normalized, counts = normalize_named_reference(raw)
    board = Board.from_sexpr(parse_sexp(normalized.decode()))
    assert counts["segment"] == 1
    assert board.traceItems[0].start.X == 0.000001
    assert board.graphicItems[1].text == 'unicode μ \\ path "quoted"'
