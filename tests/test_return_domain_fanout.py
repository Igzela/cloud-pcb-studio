"""Check that other ground domains are foreign copper during ground fanout."""

from kiutils.board import Board
from kiutils.footprint import DrillDefinition, Footprint, Pad
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Net, Position
from shapely.geometry import LineString, Point, box

from cloud_pcb.general_solver.run_blind import _add_gnd_fanout_vias, _build_4layer_pours_and_stitching
from cloud_pcb.verify_gate.gerber_drc import audit_general_board


def _board(other_name="PGND"):
    board = Board.create_new()
    board.nets = [Net(0, ""), Net(10, "GND"), Net(20, other_name)]
    return board


def _footprint(board, ref, origin, pads):
    footprint = Footprint(
        entryName="test:return_domain",
        position=Position(X=origin[0], Y=origin[1]),
        properties={"Reference": ref},
    )
    for index, (net, x, y, size, plated) in enumerate(pads, 1):
        pad = Pad(
            number=str(index), type="thru_hole" if plated else "smd", shape="rect",
            position=Position(X=x, Y=y), size=Position(X=size, Y=size),
            net=board.nets[net], layers=["*.Cu", "*.Mask"] if plated else ["F.Cu"],
        )
        if plated:
            pad.drill = DrillDefinition(diameter=0.3)
        footprint.pads.append(pad)
    board.footprints.append(footprint)


def _fanout(board, keepouts=None):
    return _add_gnd_fanout_vias(board, box(0, 0, 30, 30), keepouts if keepouts is not None else [])


def _records(board):
    return [
        ("via", item.net, item.position.X, item.position.Y, item.size)
        if isinstance(item, Via) else
        ("stub", item.net, item.start.X, item.start.Y, item.end.X, item.end.Y, item.width)
        for item in board.traceItems
    ]


def _copper(item):
    if isinstance(item, Via):
        return Point(item.position.X, item.position.Y).buffer(item.size / 2)
    assert isinstance(item, Segment)
    return LineString([(item.start.X, item.start.Y), (item.end.X, item.end.Y)]).buffer(item.width / 2)


def _with_nearby_surface_pad(other_name, include_other=True):
    board = _board(other_name)
    _footprint(board, "selected", (9, 10), [(1, 1, 0, .4, False)])
    if include_other:
        _footprint(board, "foreign", (11.41, 10), [(2, 0, 0, .3, False)])
    return board


def test_other_return_smd_pad_is_foreign_copper_not_a_fanout_target():
    # The unobstructed preferred via clears the generic pad guard but violates
    # the stricter foreign-net copper clearance. This catches filtering the
    # fanout targets without also making the other domain an obstacle.
    unobstructed = _with_nearby_surface_pad("signal", include_other=False)
    _fanout(unobstructed)
    foreign_pad = box(11.26, 9.85, 11.56, 10.15)
    lone_via, = [item for item in unobstructed.traceItems if isinstance(item, Via)]
    assert .08 < _copper(lone_via).distance(foreign_pad) < .165

    independent_return = _with_nearby_surface_pad("PGND")
    unrelated_signal = _with_nearby_surface_pad("signal")
    skipped, overrides, stats = _fanout(independent_return)
    _fanout(unrelated_signal)

    assert skipped == {"GND"}
    assert not overrides
    assert stats["gnd_smd_pads_total"] == 1
    assert stats["fanout_vias_added"] == 1
    assert _records(independent_return) == _records(unrelated_signal)
    for item in independent_return.traceItems:
        minimum = .165 if isinstance(item, Via) else .16
        assert _copper(item).distance(foreign_pad) >= minimum - 1e-9
    assert audit_general_board(independent_return, {"components": []}, {}, [])["lvs_shorts"] == []


def test_other_return_plated_pad_on_same_footprint_cannot_be_bridge_target():
    board = _board()
    _footprint(board, "mixed", (9, 10), [(1, 1, 0, .4, False), (2, 1.7, 0, .6, True)])
    _, _, stats = _fanout(board)

    assert stats["fanout_vias_added"] == 1
    foreign_pad = box(10.4, 9.7, 11., 10.3)
    for item in board.traceItems:
        assert item.net == 10
        assert _copper(item).distance(foreign_pad) >= .16 - 1e-9
    assert audit_general_board(board, {"components": []}, {}, [])["lvs_shorts"] == []


def test_blocked_selected_pad_uses_same_net_plated_pin_as_dsn_anchor():
    board = _board()
    _footprint(board, "foreign", (4, 4), [(2, 0, 0, .6, True)])
    _footprint(board, "owned", (15, 15), [(1, 0, 0, .6, True)])
    _footprint(board, "blocked", (7, 7), [(1, 0, 0, .4, False)])
    keepouts = [dict(kind="rf", x_min=5, y_min=5, x_max=9, y_max=9)]

    skipped, overrides, stats = _fanout(board, keepouts)

    assert stats["unvia_gnd_pins"] == ["blocked-1"]
    assert not board.traceItems
    assert not skipped
    assert overrides == {"GND": ["blocked-1", "owned-1"]}


def test_unused_ground_label_cannot_steal_fanout_or_copper_pours():
    board = _board()
    _footprint(board, "used", (6, 6), [(2, 1, 0, .4, False)])
    skipped, overrides, stats = _fanout(board)

    assert skipped == {"PGND"}
    assert not overrides
    assert stats["fanout_vias_added"] == 1
    assert {item.net for item in board.traceItems} == {20}

    _build_4layer_pours_and_stitching(board, box(0, 0, 12, 12))

    assert {layer for zone in board.zones for layer in zone.layers} == {
        "F.Cu", "In1.Cu", "In2.Cu", "B.Cu"
    }
    assert {(zone.net, zone.netName) for zone in board.zones} == {(20, "PGND")}
    assert {item.net for item in board.traceItems} == {20}
