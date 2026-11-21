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


def test_power_class_repair_requires_opt_in_and_uses_class_width_and_layers():
    board = Board.create_new()
    vbus = Net(1, "VBUS")
    foreign = Net(2, "SIG_NEAR")
    board.nets = [Net(0, ""), vbus, foreign]
    fp = footprint(board, "J1", 4., 5.)
    pad(fp, "A4", 0., 0., vbus, size=1.2)
    pad(fp, "B9", 3., 0., vbus, size=1.2)
    report = {"unrouted": [connection("VBUS", "J1", "A4", (4., 5.), "B9", (7., 5.))]}

    power_rule = NetClassRule("POWER_002", .50, .20, .30, .55, 3.0)
    netclasses = {"DEFAULT": RULE, "POWER_002": power_rule}
    net_to_class = {"VBUS": "POWER_002"}
    layers = {"DEFAULT": ("F.Cu", "In2.Cu", "B.Cu"), "POWER_002": ("F.Cu", "B.Cu")}

    # Legacy default ignores POWER_002.
    assert propose_direct_repairs(
        board, box(0, 0, 12, 12), (), netclasses, net_to_class, layers, report,
    ) == []

    # Opt-in proposes full 0.50 mm repair on F.Cu.
    assert propose_direct_repairs(
        board, box(0, 0, 12, 12), (), netclasses, net_to_class, layers, report,
        allow_power_classes=True,
    ) == [DirectRepair("VBUS", "F.Cu", .50, (4., 5.), (7., 5.))]

    # A trace 0.53 mm away from centerline is blocked at width=0.50 + clearance=0.20
    # (0.25 + 0.20 + 0.10 = 0.55 mm required) on F.Cu, so POWER_002 falls back to B.Cu
    # (skipping In2.Cu because POWER_002 only allows F.Cu/B.Cu).
    board.traceItems.append(
        Segment(start=Position(X=5.0, Y=5.53), end=Position(X=6.0, Y=5.53),
                width=.20, layer="F.Cu", net=2)
    )
    assert propose_direct_repairs(
        board, box(0, 0, 12, 12), (), netclasses, net_to_class, layers, report,
        allow_power_classes=True,
    ) == [DirectRepair("VBUS", "B.Cu", .50, (4., 5.), (7., 5.))]


@pytest.mark.parametrize("cls_name,rule_override", [
    ("POWER_IMP", dict(impedance_ohm=50.0)),
    ("POWER_DIFF", dict(diff_pair_gap_mm=0.15)),
    ("USB_DIFF", dict(diff_pair_gap_mm=0.15)),
    ("OTHER", {}),
])
def test_impedance_diff_pair_and_non_power_classes_rejected_with_allow_power_classes(
    clear_pair, cls_name, rule_override,
):
    board, _, report = clear_pair
    netclasses = {
        "DEFAULT": RULE,
        cls_name: replace(RULE, name=cls_name, trace_width_mm=.50, **rule_override),
    }
    assert propose_direct_repairs(
        board, box(0, 0, 12, 12), (), netclasses, {"signal": cls_name}, LAYERS, report,
        allow_power_classes=True,
    ) == []


def test_multi_link_power_connector_proposes_both_links_and_respects_obstacles():
    board = Board.create_new()
    vbus = Net(1, "VBUS")
    cc1 = Net(2, "CC1")
    blocker = Net(3, "BLOCKER")
    board.nets = [Net(0, ""), vbus, cc1, blocker]

    fp = footprint(board, "J_USB", 2., 3.)
    # 4-pin TH power connector: two independent VBUS pairs (A4-B9 and A9-B4)
    # plus a crossing signal pair (A5-B5) on CC1.
    pad(fp, "A4", 1., 0., vbus, size=1.1)
    pad(fp, "B9", 1., 3., vbus, size=1.1)
    pad(fp, "A9", 5., 0., vbus, size=1.1)
    pad(fp, "B4", 5., 3., vbus, size=1.1)
    pad(fp, "A5", 0., 1.5, cc1, size=1.0)
    pad(fp, "B5", 3., 1.5, cc1, size=1.0)

    report = {
        "unrouted": [
            connection("VBUS", "J_USB", "A4", (3., 3.), "B9", (3., 6.)),
            connection("VBUS", "J_USB", "A9", (7., 3.), "B4", (7., 6.)),
        ]
    }
    power_rule = NetClassRule("POWER_002", .50, .18, .30, .55, 3.0)
    netclasses = {"DEFAULT": RULE, "POWER_002": power_rule}
    net_to_class = {"VBUS": "POWER_002"}

    # Both independent VBUS links are proposed in one pass on F.Cu.
    both = propose_direct_repairs(
        board, box(0, 0, 14, 10), (), netclasses, net_to_class, LAYERS, report,
        allow_power_classes=True,
    )
    assert both == [
        DirectRepair("VBUS", "F.Cu", .50, (3., 3.), (3., 6.)),
        DirectRepair("VBUS", "F.Cu", .50, (7., 3.), (7., 6.)),
    ]

    # Adding a crossing CC1 repair verifies that VBUS link A4-B9 is recorded in
    # obstacles[layer], forcing CC1 onto In2.Cu (or rejecting it if only F.Cu is allowed).
    report_with_cross = {
        "unrouted": [
            connection("VBUS", "J_USB", "A4", (3., 3.), "B9", (3., 6.)),
            connection("VBUS", "J_USB", "A9", (7., 3.), "B4", (7., 6.)),
            connection("CC1", "J_USB", "A5", (2., 4.5), "B5", (5., 4.5)),
        ]
    }
    fcu_only = propose_direct_repairs(
        board, box(0, 0, 14, 10), (), netclasses, net_to_class,
        {"DEFAULT": ("F.Cu",), "POWER_002": ("F.Cu",)}, report_with_cross,
        allow_power_classes=True,
    )
    # CC1 sorts before VBUS alphabetically at equal length 3.0 mm, so CC1 takes F.Cu,
    # blocking A4-B9 on F.Cu while A9-B4 still succeeds on F.Cu.
    assert fcu_only == [
        DirectRepair("CC1", "F.Cu", .16, (2., 4.5), (5., 4.5)),
        DirectRepair("VBUS", "F.Cu", .50, (7., 3.), (7., 6.)),
    ]
    # With multi-layer fallback, all three repairs are proposed without conflict.
    multi = propose_direct_repairs(
        board, box(0, 0, 14, 10), (), netclasses, net_to_class, LAYERS, report_with_cross,
        allow_power_classes=True,
    )
    assert multi == [
        DirectRepair("CC1", "F.Cu", .16, (2., 4.5), (5., 4.5)),
        DirectRepair("VBUS", "In2.Cu", .50, (3., 3.), (3., 6.)),
        DirectRepair("VBUS", "F.Cu", .50, (7., 3.), (7., 6.)),
    ]

    # An existing foreign obstacle on F.Cu across A9-B4 moves A9-B4 to In2.Cu
    # while A4-B9 stays on F.Cu.
    board.traceItems.append(
        Segment(start=Position(X=6.2, Y=4.5), end=Position(X=7.8, Y=4.5),
                width=.20, layer="F.Cu", net=3)
    )
    dodged = propose_direct_repairs(
        board, box(0, 0, 14, 10), (), netclasses, net_to_class, LAYERS, report,
        allow_power_classes=True,
    )
    assert dodged == [
        DirectRepair("VBUS", "F.Cu", .50, (3., 3.), (3., 6.)),
        DirectRepair("VBUS", "In2.Cu", .50, (7., 3.), (7., 6.)),
    ]


def test_plane_ground_net_skips_only_plane_ground_and_repairs_isolated_agnd():
    board = Board.create_new()
    gnd = Net(1, "GND")
    agnd = Net(2, "AGND")
    board.nets = [Net(0, ""), gnd, agnd]
    fp = footprint(board, "J_RET", 4., 4.)
    pad(fp, "1", 0., 0., gnd)
    pad(fp, "2", 3., 0., gnd)
    pad(fp, "3", 0., 3., agnd)
    pad(fp, "4", 3., 3., agnd)

    report = {
        "unrouted": [
            connection("GND", "J_RET", "1", (4., 4.), "2", (7., 4.)),
            connection("AGND", "J_RET", "3", (4., 7.), "4", (7., 7.)),
        ]
    }
    netclasses = {"DEFAULT": RULE}

    # Legacy behavior (plane_ground_net=None) skips all ground-named nets.
    assert propose_direct_repairs(
        board, box(0, 0, 12, 12), (), netclasses, {}, LAYERS, report,
    ) == []

    # Specifying plane_ground_net="GND" skips only "GND" and allows repairing "AGND".
    assert propose_direct_repairs(
        board, box(0, 0, 12, 12), (), netclasses, {}, LAYERS, report,
        plane_ground_net="GND",
    ) == [DirectRepair("AGND", "F.Cu", .16, (4., 7.), (7., 7.))]


def test_collinear_blocked_power_pins_use_three_segment_orthogonal_c_bypass():
    board = Board.create_new()
    vbus = Net(2, "VBUS")
    sig = Net(3, "SIG")
    board.nets = [Net(0, ""), Net(1, "GND"), vbus, sig]
    fp = footprint(board, "J_COL", 4., 2.)
    pad(fp, "B9", 0., 1., vbus, size=0.8)
    pad(fp, "B7", 0., 2., sig, size=0.8)
    pad(fp, "B5", 0., 3., sig, size=0.8)
    pad(fp, "B4", 0., 4., vbus, size=0.8)

    report = {
        "unrouted": [
            connection("VBUS", "J_COL", "B9", (4., 3.), "B4", (4., 6.)),
        ]
    }
    power_rule = NetClassRule("POWER_002", .50, .15, .30, .55, 3.0)
    netclasses = {"DEFAULT": RULE, "POWER_002": power_rule}
    net_to_class = {"VBUS": "POWER_002"}

    # Without allow_power_classes, nothing is proposed.
    assert propose_direct_repairs(
        board, box(0, 0, 12, 10), (), netclasses, net_to_class, LAYERS, report,
        allow_power_classes=False,
    ) == []

    # With allow_power_classes, the straight vertical line (4,3)->(4,6) is blocked by B7 and B5,
    # so the 3-segment orthogonal C-bypass at x = 3.25 (offset -0.75 mm) is proposed on F.Cu.
    c_repairs = propose_direct_repairs(
        board, box(0, 0, 12, 10), (), netclasses, net_to_class, LAYERS, report,
        allow_power_classes=True,
    )
    assert len(c_repairs) == 3
    assert all(r.net == "VBUS" and r.layer == "F.Cu" and r.width_mm == 0.50 for r in c_repairs)
    assert c_repairs[0].start_xy == (4.0, 3.0)
    assert c_repairs[0].end_xy == c_repairs[1].start_xy
    assert c_repairs[1].end_xy == c_repairs[2].start_xy
    assert c_repairs[2].end_xy == (4.0, 6.0)
    assert c_repairs[0].end_xy[0] < 4.0

