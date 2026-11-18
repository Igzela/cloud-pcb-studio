"""Checks for conservative geometry and input validation in direct repairs."""

from dataclasses import FrozenInstanceError, replace

import pytest
from kiutils.board import Board
from kiutils.footprint import DrillDefinition, Footprint, Pad
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Coordinate, Net, Position
from kiutils.items.zones import FilledPolygon, KeepoutSettings, Zone, ZonePolygon
from shapely.geometry import Polygon, box

from cloud_pcb.general_solver.direct_repair import DirectRepair, propose_direct_repairs
from contracts.schemas import NetClassRule


RULE = NetClassRule("DEFAULT", .16, .125, .3, .55, .5)
LAYERS = {"DEFAULT": ("F.Cu", "In2.Cu", "B.Cu")}


def footprint(board, ref, x, y):
    fp = Footprint(position=Position(X=x, Y=y), properties={"Reference": ref})
    board.footprints.append(fp)
    return fp


def pad(fp, label, x, y, net, *, kind="thru_hole", shape="circle", size=1.2):
    plated = kind == "thru_hole"
    item = Pad(number=label, type=kind, shape=shape, position=Position(X=x, Y=y),
               size=Position(X=size, Y=size), net=net,
               layers=["*.Cu", "*.Mask"] if plated or kind == "np_thru_hole" else ["F.Cu", "F.Mask"],
               drill=DrillDefinition(diameter=.6) if plated or kind == "np_thru_hole" else None)
    fp.pads.append(item)
    return item


def connection(net, component, pin_a, xy_a, pin_b, xy_b):
    return {
        "net": net,
        "from": {"kind": "pin", "component": component, "pin": pin_a},
        "to": {"kind": "pin", "component": component, "pin": pin_b},
        "from_xy": list(xy_a),
        "to_xy": list(xy_b),
    }


@pytest.fixture
def clear_pair():
    board = Board.create_new()
    board.nets = [Net(0, ""), Net(1, "signal"), Net(2, "foreign")]
    fp = footprint(board, "socket", 4., 5.)
    pad(fp, "one", 0., 0., board.nets[1], shape="rect")
    pad(fp, "two", 3., 0., board.nets[1])
    report = {"unrouted": [connection("signal", "socket", "one", (4., 5.), "two", (7., 5.))]}
    return board, fp, report


def propose(clear_pair, *, outline=None, keepouts=(), layers=LAYERS, rule=RULE, assignment=None):
    board, _, report = clear_pair
    return propose_direct_repairs(
        board, box(0, 0, 12, 12) if outline is None else outline, keepouts,
        {"DEFAULT": rule, "OTHER": replace(RULE, name="OTHER", clearance_mm=.25)},
        assignment or {}, layers, report,
    )


def test_full_width_proposal_is_immutable_and_leaves_inputs_untouched(clear_pair):
    board, _, report = clear_pair
    cooked = propose(clear_pair, rule=replace(RULE, trace_width_mm=.1601))
    assert cooked == [DirectRepair("signal", "F.Cu", .161, (4., 5.), (7., 5.))]
    with pytest.raises(FrozenInstanceError):
        cooked[0].layer = "B.Cu"
    assert not board.traceItems
    assert report["unrouted"][0]["from_xy"] == [4., 5.]


def test_endpoint_matches_specctra_placement_and_local_micron_rounding(clear_pair):
    _, fp, report = clear_pair
    fp.position.X += .00049
    fp.pads[1].position.X += .00049
    assert propose(clear_pair)[0].end_xy == (7., 5.)
    report["unrouted"][0]["to_xy"][0] += .001
    assert propose(clear_pair) == []


@pytest.mark.parametrize("fault", ["duplicate_pin", "duplicate_ref", "wrong_net",
                                   "stale_coordinate", "cross_footprint", "smd",
                                   "non_45", "bad_drill", "different_class", "pair",
                                   "layer_limited"])
def test_uncertain_or_constrained_connections_do_not_qualify(clear_pair, fault):
    board, fp, report = clear_pair
    kwargs = {}
    end = report["unrouted"][0]
    if fault == "duplicate_pin":
        pad(fp, "two", 0., 3., board.nets[1])
    elif fault == "duplicate_ref":
        footprint(board, "socket", 9., 9.)
    elif fault == "wrong_net":
        fp.pads[1].net = board.nets[2]
    elif fault == "stale_coordinate":
        end["to_xy"][1] += .015
    elif fault == "cross_footprint":
        other = footprint(board, "other", 4., 5.)
        other.pads.append(fp.pads.pop())
        end["to"]["component"] = "other"
    elif fault == "smd":
        fp.pads[1].type = "smd"
    elif fault == "non_45":
        fp.pads[1].position.Y = 1.
        end["to_xy"][1] = 6.
    elif fault == "bad_drill":
        fp.pads[1].drill.diameter = 1.2
    elif fault == "different_class":
        kwargs["assignment"] = {"signal": "OTHER"}
    elif fault == "pair":
        kwargs["rule"] = replace(RULE, impedance_ohm=90.)
    elif fault == "layer_limited":
        kwargs["layers"] = {"DEFAULT": ("Unknown.Cu",)}
    assert propose(clear_pair, **kwargs) == []


def test_layer_fallback_uses_only_configured_layers_and_keeps_class_clearance(clear_pair):
    board, _, _ = clear_pair
    # 0.205 mm actual gap is sufficient at the 0.15 mm minimum, but not
    # against a foreign class that asks for 0.25 mm clearance.
    board.traceItems.append(Segment(start=Position(X=5.2, Y=5.385), end=Position(X=5.8, Y=5.385),
                                    width=.2, layer="F.Cu", net=2))
    assert propose(clear_pair)[0].layer == "F.Cu"
    assert propose(clear_pair, assignment={"foreign": "OTHER"})[0].layer == "In2.Cu"
    assert propose(clear_pair, assignment={"foreign": "OTHER"}, layers={"DEFAULT": ("F.Cu",)}) == []
    assert propose(clear_pair, assignment={"foreign": "OTHER"}, rule=replace(RULE, zero_via_allowed=False)) == []
    assert propose(clear_pair, layers={"DEFAULT": ("B.Cu",)},
                   rule=replace(RULE, zero_via_allowed=False)) == []


@pytest.mark.parametrize("blocker", ["via", "slot", "outline_hole", "board_edge",
                                     "rf_polygon", "filled_zone", "track_keepout_zone",
                                     "unknown_pad_shape"])
def test_exact_path_checks_sweep_holes_and_copper(clear_pair, blocker):
    board, fp, _ = clear_pair
    kwargs = {}
    if blocker == "via":
        board.traceItems.append(Via(position=Position(X=5.5, Y=5.2), size=.55, drill=.30,
                                    layers=["F.Cu", "B.Cu"], net=2))
    elif blocker == "slot":
        hole = pad(fp, "slot", 1.5, 1., None, kind="np_thru_hole", size=.4)
        hole.drill = DrillDefinition(oval=True, diameter=.4, width=2.1)
    elif blocker == "outline_hole":
        kwargs["outline"] = Polygon(box(0, 0, 12, 12).exterior.coords,
                                    [box(5.4, 5.3, 5.6, 5.6).exterior.coords])
    elif blocker == "board_edge":
        kwargs["outline"] = box(0, 4.45, 12, 12)
    elif blocker == "rf_polygon":
        kwargs["keepouts"] = [
            {"kind": "rf", "polygon_mm": [(5.4, 4.8), (5.7, 4.8), (5.5, 5.3)],
             "layers": ["*.Cu"]}
        ]
    elif blocker == "filled_zone":
        points = [Coordinate(X=x, Y=y) for x, y in box(5.2, 4.9, 5.8, 5.2).exterior.coords]
        board.zones.append(
            Zone(net=2, filledPolygons=[FilledPolygon(layer="F.Cu", coordinates=points)])
        )
        kwargs["layers"] = {"DEFAULT": ("F.Cu",)}
    elif blocker == "track_keepout_zone":
        points = [Coordinate(X=x, Y=y) for x, y in box(5.2, 4.9, 5.8, 5.2).exterior.coords]
        board.zones.append(Zone(layers=["*.Cu"], keepoutSettings=KeepoutSettings(tracks="not_allowed"),
                                polygons=[ZonePolygon(coordinates=points)]))
    else:
        pad(fp, "custom", 1.5, 0., board.nets[2], kind="smd", shape="custom")
    assert propose(clear_pair, **kwargs) == []


def test_via_only_keepout_does_not_block_a_via_free_repair(clear_pair):
    keepout = dict(dsn_kind="via_keepout", layers=["*.Cu"],
                   x_min=5.2, x_max=5.8, y_min=4.9, y_max=5.2)
    assert propose(clear_pair, keepouts=[keepout])[0].layer == "F.Cu"
    keepout["dsn_kind"] = "wire_keepout"
    assert propose(clear_pair, keepouts=[keepout]) == []


def test_crossing_proposals_are_checked_together_in_deterministic_order(clear_pair):
    board, fp, report = clear_pair
    pad(fp, "three", 1.5, -1.5, board.nets[2])
    pad(fp, "four", 1.5, 1.5, board.nets[2])
    report["unrouted"].append(connection("foreign", "socket", "three", (5.5, 3.5), "four", (5.5, 6.5)))
    agreed = [DirectRepair("foreign", "F.Cu", .16, (5.5, 3.5), (5.5, 6.5))]
    assert propose(clear_pair, layers={"DEFAULT": ("F.Cu",)}) == agreed
    report["unrouted"].reverse()
    assert propose(clear_pair, layers={"DEFAULT": ("F.Cu",)}) == agreed
    assert propose(clear_pair) == agreed + [DirectRepair("signal", "In2.Cu", .16, (4., 5.), (7., 5.))]
