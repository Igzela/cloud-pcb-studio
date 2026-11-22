"""Mechanical obstacles in the opt-in Freerouting DSN export."""

import math

import pytest
from kiutils.board import Board
from kiutils.footprint import DrillDefinition, Footprint, Pad
from kiutils.items.common import Net, Position
from shapely.geometry import Point, Polygon

from cloud_pcb.layout_route.dsn_ses_bridge import _parse_sexpr, board_to_dsn
from contracts.schemas import NetClassRule


def _node(parent, name):
    return next(child for child in parent if isinstance(child, list) and child[0] == name)


def _export(tmp_path, *, enabled, missing_drill=False):
    board = Board.create_new()
    signal = Net(1, "signal")
    board.nets.append(signal)
    hole = Footprint(
        position=Position(X=13.1234, Y=16.4567, angle=90),
        properties={"Reference": "Mechanical 1"},
        pads=[
            Pad(
                number="1", type="np_thru_hole", shape="oval",
                position=Position(X=1, Y=2, angle=30),
                size=Position(X=3, Y=1.2), layers=["*.Cu", "*.Mask"],
                drill=None if missing_drill else DrillDefinition(
                    oval=True, diameter=3, width=1.2, offset=Position(X=.3, Y=-.1)
                ),
            )
        ],
    )
    board.footprints.append(hole)
    for index, x in enumerate((5, 20)):
        pads = [
            Pad(
                number="1", type="smd", shape="rect",
                position=Position(X=0, Y=0), size=Position(X=1, Y=1),
                layers=["F.Cu"], net=signal,
            )
        ]
        if index == 0:
            # Verify NPTH in a mixed electrical/mechanical image is also protected.
            pads.append(
                Pad(
                    number="2", type="np_thru_hole", shape="circle",
                    position=Position(X=0, Y=3), size=Position(X=2, Y=2),
                    layers=["*.Cu", "*.Mask"], drill=DrillDefinition(diameter=2),
                ),
            )
        board.footprints.append(
            Footprint(position=Position(X=x, Y=5),
                      properties={"Reference": f"signal{index}"}, pads=pads)
        )
    before = board.to_sexpr()
    path = board_to_dsn(
        board, tmp_path / "board.dsn", {"DEFAULT": NetClassRule("DEFAULT", .2, .15, .3, .55, .5)},
        {}, protect_mechanical_holes=enabled,
    )
    assert board.to_sexpr() == before
    return _parse_sexpr(path.read_text())[0]


def test_mechanical_images_resolve_all_placements_without_electrical_pins(tmp_path):
    legacy = _export(tmp_path, enabled=False)
    dsn = _export(tmp_path, enabled=True)
    images = {image[1]: image[2:] for image in _node(dsn, "library")[1:] if image[0] == "image"}
    placed_images = {component[1] for component in _node(dsn, "placement")[1:]}
    assert images.keys() == placed_images
    assert not images["IMG_Mechanical 1"]
    assert len(images["IMG_signal0"]) == 1
    assert _node(dsn, "placement") == _node(legacy, "placement")
    assert _node(dsn, "network") == _node(legacy, "network")
    assert _node(_node(_node(dsn, "network"), "net"), "pins") == ["pins", "signal0-1", "signal1-1"]


def test_npth_round_hole_and_rotated_offset_slot_have_conservative_four_layer_guards(tmp_path):
    legacy = _export(tmp_path, enabled=False)
    assert not any(child[0] == "keepout" for child in _node(legacy, "structure")[1:])
    dsn = _export(tmp_path, enabled=True)
    guards = [child[2] for child in _node(dsn, "structure")[1:] if child[0] == "keepout"]
    assert len(guards) == 8
    layers = {"F.Cu", "In1.Cu", "In2.Cu", "B.Cu"}
    assert {guard[1] for guard in guards[:4]} == layers
    assert {guard[1] for guard in guards[4:]} == layers

    # Compute actual drill centers in DSN's y-up frame. Include the noninteger
    # footprint center, rotation, drill offset and both ends of the slot sweep.
    theta = math.radians(30)
    center_x = 13.1234 + 2 + .3 * math.cos(theta) - .1 * math.sin(theta)
    center_y = -16.4567 + 1 + .3 * math.sin(theta) + .1 * math.cos(theta)
    ends = [
        (center_x + sign * .9 * math.cos(theta), center_y + sign * .9 * math.sin(theta))
        for sign in (-1, 1)
    ]
    for index, guard in enumerate(guards):
        assert guard[0] == "polygon"
        x = [int(value) / 1000 for value in guard[3::2]]
        y = [int(value) / 1000 for value in guard[4::2]]
        polygon = Polygon(zip(x, y))
        assert polygon.is_valid and polygon.equals(polygon.convex_hull)
        # Containing the two full end disks plus convexity proves that each
        # guard also contains the whole swept drill with 0.50 mm clearance.
        is_slot = index < 4
        for x_mm, y_mm in ends if is_slot else [(5, -8)]:
            center = Point(x_mm, y_mm)
            clearance_radius = 1.1 if is_slot else 1.5
            assert polygon.contains(center)
            assert center.distance(polygon.boundary) >= clearance_radius


def test_mechanical_export_refuses_missing_drill_geometry(tmp_path):
    with pytest.raises(ValueError, match="NPTH pad has no drill"):
        _export(tmp_path, enabled=True, missing_drill=True)
