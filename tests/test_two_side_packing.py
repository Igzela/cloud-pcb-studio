"""Synthetic adversarial tests; no reference board or holdout dependency."""
from copy import deepcopy
import json
from pathlib import Path
import random
import time

import pytest
from shapely.affinity import translate
from shapely.geometry import Point, box

from cloud_pcb.general_solver.placement_recovery import PackingExhausted
from cloud_pcb.general_solver.run_blind import (
    PlacementExhausted, _build_blind_kicad_board, _classify_components_and_place,
    _pad_world_polygon_kicad,
)
from cloud_pcb.general_solver.two_side_packing import (
    COMPACT_COURTYARD_MM, component_z_range, in_outline, local_occupancy,
    mechanical_keepouts, mounting_guard_radius, noninterfering, pack_two_sided,
    repack_blockers, side_options,
)


def smd(num, x, y=0, *, w=.6, h=.5, angle=0, net=""):
    return dict(num=str(num), type="smd", x_rel_mm=x, y_rel_mm=y, w_mm=w, h_mm=h, shape="rect",
                angle_rel_deg=angle, layers=["F.Cu"], net=net)


def passive():
    return {"refdes": "R", "value": "100", "body_bbox_mm": [-.5,-.4,.5,.4],
            "pads": [smd(1,-.46, net="A"), smd(2,.46, net="B")]}


def blocked_spec():
    fixed = {"refdes":"J", "value":"Synthetic fixed interface", "side":"top",
             "body_bbox_mm":[0,-3.7,7.4,3.7],
             "pads":[smd(1,3.3,-1.2,net="A"), smd(2,3.3,1.2,net="B")]}
    return {"project_name":"Synthetic two-sided clearance", "repo_url":"", "commit_sha":"",
            "board_outline_mm":{"width":9,"height":9}, "components":[fixed, passive()],
            "edge_connectors":[{"refdes":"J","x_mm":.8,"y_mm":4.5,"angle_deg":0.,"allow_body_overhang":False}],
            "nets":{"A":[["J","1"],["R","1"]],"B":[["J","2"],["R","2"]]},
            "design_requirements":{}}


def test_only_flexible_passive_is_moved_in_exact_recovery():
    spec = blocked_spec()
    with pytest.raises(PlacementExhausted):
        _classify_components_and_place(spec, packing_order="area", packing_compact=True, protect_sides=True)
    placed, meta = _classify_components_and_place(
        spec, packing_order="area", packing_compact=True, packing_two_sided=True, protect_sides=True,
    )
    assert placed["J"] == (.8, 4.5, 0.)
    assert meta["placement_recovery"]["auto_bottom"] == ["R"]
    assert meta["component_sides"] == {"J":"top", "R":"bottom"}
    forced = blocked_spec()
    forced["components"][1]["side"] = "top"
    with pytest.raises(PlacementExhausted):
        _classify_components_and_place(
            forced, packing_order="area", packing_compact=True, packing_two_sided=True, protect_sides=True,
        )
    assert forced["components"][1]["side"] == "top"


@pytest.mark.parametrize("kind", ["thru_hole", "np_thru_hole"])
def test_rotated_offset_hole_guard_conflicts_in_both_orders(kind):
    through = dict(type=kind, x_rel_mm=0, y_rel_mm=0, w_mm=2.4, h_mm=1.4, shape="oval",
                   angle_rel_deg=30, drill_shape="oval", drill_width_mm=1.6, drill_height_mm=.8,
                   drill_offset_mm=[.4,0], layers=["*.Cu"])
    connector = {"body_bbox_mm":[-1,-2,4,2], "pads":[through]}
    a = local_occupancy(connector,"top").rotate(90).translate(7,7)
    b = local_occupancy(passive(),"bottom").rotate(90).translate(7.15,6.55)
    far = local_occupancy(passive(),"bottom").translate(7,4)
    assert not noninterfering("top",a,"bottom",b)
    assert not noninterfering("bottom",b,"top",a)
    assert a.body.intersects(far.body)
    assert noninterfering("top",a,"bottom",far)
    assert noninterfering("bottom",far,"top",a)


def test_rotated_pad_has_independent_copper_edge_floor():
    board = box(0,0,12,12)
    part = {"body_bbox_mm":[-.3,-.2,.3,.2],"pads":[smd(1,0,.6,w=.7,h=.6,angle=90)]}
    shape = local_occupancy(part,"top").rotate(270)
    assert in_outline(board,board.boundary,shape.translate(1.452,6))
    assert not in_outline(board,board.boundary,shape.translate(1.449,6))
    over = local_occupancy({"body_bbox_mm":[-1,-.2,1,.2],"pads":[smd(1,.35)]},"top").translate(.9,6)
    assert over.copper.distance(board.boundary) > .5
    assert not in_outline(board,board.boundary,over)


def test_auto_bottom_accepts_only_unspecified_unprotected_simple_copper_smd():
    part = passive()
    assert side_options(part) == ("top","bottom")
    assert side_options(dict(part,side="top")) == ("top",)
    assert side_options(dict(part,side="bottom")) == ("bottom",)
    assert side_options(dict(part,value="antenna")) == ("top",)
    assert side_options(part, protected_nets=frozenset({"A"})) == ("top",)
    assert side_options(dict(part,pads=[smd(1,-.3),dict(smd(2,.3),layers=["F.Paste"])])) == ("top",)
    assert side_options(dict(part,pads=[smd(1,-.3),dict(smd(2,.3),type="thru_hole")])) == ("top",)


def test_bottom_geometry_matches_emitter_actual_pads():
    part = passive()
    part["side"] = "bottom"
    part["pads"][0].update(x_rel_mm=-.47,y_rel_mm=.23,angle_rel_deg=37,shape="oval")
    part["pads"][1].update(x_rel_mm=.56,y_rel_mm=-.36,angle_rel_deg=13)
    spec = {"project_name":"Synthetic", "board_outline_mm":{"width":12,"height":12},
            "components":[part],"nets":{},"design_requirements":{}}
    board,*_ = _build_blind_kicad_board(spec,{"R":(6,7,90)})
    footprint, = board.footprints
    shape = local_occupancy(part,"bottom").rotate(90).translate(6,7)
    assert footprint.layer == "B.Cu"
    for pad in footprint.pads:
        assert pad.layers == ["B.Cu"]
        assert shape.copper.covers(_pad_world_polygon_kicad(footprint,pad))


def test_bottom_oblong_through_slot_encloses_rotated_kicad_offset_drill():
    from cloud_pcb.general_solver.geometry import drill_geometry

    part = passive()
    part["side"] = "bottom"
    part["pads"][0].update(
        type="thru_hole", drill_shape="oval", drill_width_mm=1.4, drill_height_mm=.3,
        drill_offset_mm=[.27,-.11], x_rel_mm=-.47, y_rel_mm=.23, angle_rel_deg=37, shape="oval",
        w_mm=2.2, h_mm=.8,
    )
    spec = {"project_name":"Synthetic", "board_outline_mm":{"width":12,"height":12},
            "components":[part],"nets":{},"design_requirements":{}}
    board,*_ = _build_blind_kicad_board(spec,{"R":(6,7,90)})
    footprint, = board.footprints
    shape = local_occupancy(part,"bottom").rotate(90).translate(6,7)
    actual = drill_geometry(footprint, footprint.pads[0])
    assert shape.holes.covers(actual)
    assert shape.holes.boundary.distance(actual) >= .001 - 1e-7


def test_off_grid_support_probes_find_the_only_meaningful_same_side_gap():
    # Square part prevents a rotated narrow side from fitting. Copper extends
    # the search bbox farther than the mechanical body. The available strip
    # requires the full two-sided courtyard spacing at precisely x=8.123.
    device = passive()
    device["side"] = "top"
    device["body_bbox_mm"] = [-1,-1,1,1]
    parts = {"left":{"body_bbox_mm":[0,0,6.563,8],"pads":[]},
             "right":{"body_bbox_mm":[9.683,0,20,8],"pads":[]},"R":device}
    reserved = {"left":(0,0,0),"right":(0,0,0)}
    received = []
    pack_two_sided(parts, reserved, box(0,0,20,8), [], {"R":(8,4)},
                   lambda ref, pose, side: received.append((ref,pose,side)),
                   board_size=(20,8), mode="area", protected_nets=frozenset())
    assert received == [("R",(8.123,4.,0.),"top")]


def test_explicit_mechanical_constraints_keep_larger_fastener_and_own_z_region():
    assert mounting_guard_radius({"drill_mm":1.,"fastener_radius_mm":2.4}) == 2.4
    assert mounting_guard_radius({"drill_mm":1.,"fastener_radius_mm":1.2}) == 1.5
    obstruction = [{"polygon_mm":[[0,0],[9,0],[9,9],[0,9]],"z_range_mm":[.2,1.2]}]
    regions = mechanical_keepouts({"mechanical_keepouts":obstruction})
    device = dict(passive(),height_mm=.6,standoff_mm=.25)
    received = []
    pack_two_sided({"R":device}, {}, box(0,0,9,9), [], {"R":(4.5,4.5)},
                   lambda ref, pose, side: received.append((ref,pose,side)),
                   board_size=(9,9), mode="area", protected_nets=frozenset(), mechanical_regions=regions)
    assert received == [("R",(4.5,4.5,0.),"bottom")]


def _rollback_device(w: float, h: float) -> dict:
    return {"side": "top", "body_bbox_mm": [-w / 2, -h / 2, w / 2, h / 2], "pads": []}


def _crowded_rollback_scene():
    parts = {
        "big": _rollback_device(6, 5),
        "small": _rollback_device(4, 4),
        "pin": _rollback_device(0.4, 0.4),
    }
    fixed = {"pin": (11.3, 6.7, 0.0, "top")}
    return parts, fixed


@pytest.mark.parametrize("mode", ["area", "long-side"])
def test_both_production_greedy_orders_fail_but_rollback_finds_valid_pose(mode):
    parts, fixed = _crowded_rollback_scene()
    board = box(0, 0, 12, 7)

    # Without rollback, both greedy size orders fail on 'small' and remain transactional (no commits).
    committed_without_rb = []
    with pytest.raises(PackingExhausted, match="small"):
        pack_two_sided(
            parts,
            {ref: pose[:3] for ref, pose in fixed.items()},
            board,
            [],
            lambda _: (6, 3.5),
            lambda ref, where, side: committed_without_rb.append((ref, where, side)),
            board_size=board.bounds[2:],
            mode=mode,
            protected_nets=frozenset(),
            rollback_probe_budget=0,
        )
    assert committed_without_rb == []

    # Direct repack_blockers call on the stalled greedy incumbent recovers a legal pose deterministically.
    incumbent = dict(fixed, big=(6.0, 3.5, 0.0, "top"))
    untouched = deepcopy(incumbent)
    result = repack_blockers(parts, incumbent, "small", {"pin"}, board, (6, 3.5), probe_budget=5000)
    assert result.status == "repacked" and result.moved == ("big",)
    assert result.poses["pin"] == fixed["pin"] and incumbent == untouched
    assert result.probes <= 5000 and set(result.poses) == {"pin", "big", "small"}
    checks = []
    for ref, (x, y, a, side) in result.poses.items():
        occupancy = local_occupancy(parts[ref], side).rotate(a).translate(x, y)
        assert in_outline(board, board.boundary, occupancy)
        assert all(noninterfering(side, occupancy, *q) for q in checks)
        checks.append((side, occupancy))
    assert result == repack_blockers(parts, incumbent, "small", {"pin"}, board, (6, 3.5), probe_budget=5000)

    # Integrated pack_two_sided with default rollback succeeds end-to-end and commits atomically.
    final_poses = deepcopy(fixed)
    pack_two_sided(
        parts,
        {ref: pose[:3] for ref, pose in fixed.items()},
        board,
        [],
        lambda _: (6, 3.5),
        lambda ref, where, side: final_poses.__setitem__(ref, (*where, side)),
        board_size=board.bounds[2:],
        mode=mode,
        protected_nets=frozenset(),
    )
    assert set(final_poses) == {"pin", "big", "small"}
    assert final_poses["pin"] == fixed["pin"]
    integrated_checks = []
    for ref, (x, y, a, side) in final_poses.items():
        occupancy = local_occupancy(parts[ref], side).rotate(a).translate(x, y)
        assert in_outline(board, board.boundary, occupancy)
        assert all(noninterfering(side, occupancy, *q) for q in integrated_checks)
        integrated_checks.append((side, occupancy))


def test_impossible_same_side_body_area_does_not_commit_partial_choice():
    parts = {"big": _rollback_device(6, 5), "small": _rollback_device(5, 5)}
    board = box(0, 0, 8, 6)
    # The two body areas sum to 55 mm^2 in 48 mm^2; both sides are fixed top.
    assert 6 * 5 + 5 * 5 > board.area
    incumbent = {"big": (4.0, 3.0, 0.0, "top")}
    original = deepcopy(incumbent)
    result = repack_blockers(parts, incumbent, "small", set(), board, (4, 3), probe_budget=6500)
    assert result.status == "search_exhausted" and result.poses is None
    assert incumbent == original and result.probes <= 6500


def test_probe_limit_is_distinct_from_search_exhaustion_and_pins_are_never_repacked():
    parts, fixed = _crowded_rollback_scene()
    board = box(0, 0, 12, 7)
    fixed["big"] = (6.0, 3.5, 0.0, "top")
    original = deepcopy(fixed)
    tiny = repack_blockers(parts, fixed, "small", {"pin"}, board, (6, 3.5), probe_budget=1)
    assert tiny.status == "probe_budget_exhausted" and tiny.probes == 1 and tiny.poses is None
    pinned = repack_blockers(parts, fixed, "small", {"pin", "big"}, board, (6, 3.5), probe_budget=5000)
    assert pinned.status == "search_exhausted" and pinned.poses is None and pinned.branches == 0
    assert fixed == original


def test_pack_two_sided_is_transactional_on_exhaustion_with_flexible_passives():
    # First movable passive can fit on bottom, second top-only IC is too big for the board.
    # On exhaustion, commit must never be invoked so no partial pose or side update leaks.
    parts = {
        "J": {"side": "top", "body_bbox_mm": [0, -3.7, 7.4, 3.7], "pads": []},
        "R1": passive(),
        "U_BIG": {"side": "top", "body_bbox_mm": [-5.0, -5.0, 5.0, 5.0], "pads": []},
    }
    original = deepcopy(parts)
    commits = []
    with pytest.raises(PackingExhausted):
        pack_two_sided(
            parts,
            {"J": (0.8, 4.5, 0.0)},
            box(0, 0, 9, 9),
            [],
            lambda _: (4.5, 4.5),
            lambda ref, where, side: commits.append((ref, where, side)),
            board_size=(9, 9),
            mode="area",
            protected_nets=frozenset(),
        )
    assert commits == []
    assert parts == original


def test_repack_blockers_origin_tolerance_and_branch_probe_cap():
    parts, fixed = _crowded_rollback_scene()
    incumbent = dict(fixed, big=(6.0, 3.5, 0.0, "top"))
    # Sub-micron origin drift (<= 1e-6) is accepted; larger origin offset is rejected.
    drifted_board = translate(box(0, 0, 12, 7), xoff=1e-9, yoff=-1e-9)
    res = repack_blockers(parts, incumbent, "small", {"pin"}, drifted_board, (6, 3.5), probe_budget=5000)
    assert res.status == "repacked"
    with pytest.raises(ValueError, match="board-local frame"):
        repack_blockers(parts, incumbent, "small", {"pin"}, translate(box(0, 0, 12, 7), xoff=1e-4, yoff=0.0), (6, 3.5))

    # Per-branch probe cap prevents an unsolvable branch from starving remaining branches.
    two_blocker_parts = {
        "b1": _rollback_device(4.5, 4.5),
        "b2": _rollback_device(4.5, 4.5),
        "new": _rollback_device(4.0, 4.0),
    }
    board = box(0, 0, 11, 10)
    inc = {"b1": (3.2, 5.0, 0.0, "top"), "b2": (8.3, 5.0, 0.0, "top")}
    capped = repack_blockers(
        two_blocker_parts,
        inc,
        "new",
        set(),
        board,
        (5.5, 5.0),
        probe_budget=1500,
        branch_probe_cap=120,
        branch_cap=20,
        max_blockers=2,
    )
    assert capped.probes <= 1500
    assert capped.branches >= 1


def test_component_z_range_accepts_optional_board_thickness():
    part = dict(passive(), height_mm=0.8, standoff_mm=0.1)
    top_default = component_z_range(part, "top")
    bot_default = component_z_range(part, "bottom")
    bot_thin = component_z_range(part, "bottom", board_thickness_mm=0.8)
    assert top_default == component_z_range(part, "top", board_thickness_mm=0.8)
    assert bot_default == pytest.approx((-2.5, -1.7))
    assert bot_thin == pytest.approx((-1.7, -0.9))


def test_dense_25_plus_part_two_sided_board_packs_legally_under_3s():
    """30-part dense synthetic board proves AABB acceleration and full-board coverage (< 3.0s)."""
    board_w, board_h = 34.0, 26.0
    board = box(0, 0, board_w, board_h)
    mounting = [Point(2.5, 2.5).buffer(1.6, quad_segs=16), Point(31.5, 23.5).buffer(1.6, quad_segs=16)]

    parts: dict[str, dict] = {
        "J_EDGE": {
            "refdes": "J_EDGE",
            "side": "top",
            "body_bbox_mm": [-1.5, -4.0, 1.5, 4.0],
            "pads": [
                dict(num="1", type="thru_hole", x_rel_mm=0.0, y_rel_mm=-2.0, w_mm=1.4, h_mm=1.4,
                     shape="circle", drill_mm=0.8, layers=["*.Cu"]),
                dict(num="2", type="thru_hole", x_rel_mm=0.0, y_rel_mm=2.0, w_mm=1.4, h_mm=1.4,
                     shape="circle", drill_mm=0.8, layers=["*.Cu"]),
            ],
        },
        "U1": {
            "refdes": "U1",
            "side": "top",
            "body_bbox_mm": [-3.5, -3.5, 3.5, 3.5],
            "pads": [smd(i + 1, -3.2 if i < 4 else 3.2, -2.4 + (i % 4) * 1.6, w=0.8, h=0.45) for i in range(8)],
        },
        "U2": {
            "refdes": "U2",
            "side": "top",
            "body_bbox_mm": [-2.5, -2.0, 2.5, 2.0],
            "pads": [smd(i + 1, -2.2 if i < 2 else 2.2, -1.0 if i % 2 == 0 else 1.0, w=0.7, h=0.5) for i in range(4)],
        },
        "U3": {
            "refdes": "U3",
            "side": "top",
            "body_bbox_mm": [-2.0, -2.0, 2.0, 2.0],
            "pads": [smd(i + 1, -1.7 if i < 2 else 1.7, -0.9 if i % 2 == 0 else 0.9, w=0.6, h=0.45) for i in range(4)],
        },
    }
    # Add 26 two-pad SMD passives (mix of larger forced-top and flexible top/bottom passives),
    # all aiming at the crowded board center so probes must spread across the full board.
    for idx in range(1, 27):
        ref = f"R{idx}"
        half_x = 1.1 if idx <= 6 else 0.85
        half_y = 0.7 if idx <= 6 else 0.55
        comp = {
            "refdes": ref,
            "value": "10k",
            "body_bbox_mm": [-half_x, -half_y, half_x, half_y],
            "pads": [
                smd(1, -(half_x - 0.15), 0.0, w=0.55, h=0.5, net=f"N{idx}A"),
                smd(2, +(half_x - 0.15), 0.0, w=0.55, h=0.5, net=f"N{idx}B"),
            ],
        }
        if idx <= 8:
            comp["side"] = "top"
        parts[ref] = comp

    assert len(parts) == 30
    reserved = {"J_EDGE": (2.2, 13.0, 0.0)}
    placed = dict(reserved)
    sides = {"J_EDGE": "top"}

    def commit(ref, pose, side):
        placed[ref] = pose
        sides[ref] = side

    t0 = time.perf_counter()
    pack_two_sided(
        parts,
        reserved,
        board,
        mounting,
        lambda _: (board_w / 2, board_h / 2),
        commit,
        board_size=(board_w, board_h),
        mode="area",
        protected_nets=frozenset(),
    )
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0, f"Dense 30-part pack_two_sided took {elapsed:.3f}s (expected < 3.0s)"
    assert set(placed) == set(parts)

    # Independently verify every placed part with raw unaccelerated Shapely predicates.
    shapes = {}
    for ref, (x, y, angle) in placed.items():
        occ = local_occupancy(parts[ref], sides[ref]).rotate(angle).translate(x, y)
        shapes[ref] = occ
        assert board.covers(occ.body)
        if not occ.holes.is_empty:
            assert board.covers(occ.holes)
        if not occ.copper.is_empty:
            assert board.covers(occ.copper)
            assert board.boundary.distance(occ.copper) >= 0.50 - 1e-9
        if ref not in reserved:
            assert all(not occ.combined.intersects(guard) for guard in mounting)
    refs = sorted(shapes)
    for i, ref_a in enumerate(refs):
        for ref_b in refs[i + 1:]:
            sa, sb = sides[ref_a], sides[ref_b]
            oa, ob = shapes[ref_a], shapes[ref_b]
            if sa == sb:
                assert oa.combined.distance(ob.combined) >= 2 * COMPACT_COURTYARD_MM - 1e-9
            assert not oa.combined.intersects(ob.hole_guard)
            assert not ob.combined.intersects(oa.hole_guard)


def test_aabb_accelerated_noninterfering_matches_unaccelerated_shapely_exactly():
    """Verify AABB-accelerated noninterfering() is mathematically identical to raw Shapely."""
    def exact_shapely_noninterfering(first_side, first, other_side, other) -> bool:
        if (first_side == other_side
                and first.combined.distance(other.combined) < 2 * COMPACT_COURTYARD_MM - 1e-9):
            return False
        return not (
            first.combined.intersects(other.hole_guard)
            or other.combined.intersects(first.hole_guard)
        )

    smd_part = {
        "body_bbox_mm": [-1.0, -0.6, 1.0, 0.6],
        "pads": [
            dict(num="1", type="smd", x_rel_mm=-0.85, y_rel_mm=0.2, w_mm=0.7, h_mm=0.5,
                 shape="oval", angle_rel_deg=25, layers=["F.Cu"]),
            dict(num="2", type="smd", x_rel_mm=0.85, y_rel_mm=-0.2, w_mm=0.7, h_mm=0.5,
                 shape="roundrect", angle_rel_deg=15, layers=["F.Cu"]),
        ],
    }
    tht_part = {
        "body_bbox_mm": [-1.2, -0.9, 1.2, 0.9],
        "pads": [
            dict(num="1", type="thru_hole", x_rel_mm=0.3, y_rel_mm=-0.1, w_mm=2.0, h_mm=1.2,
                 shape="oval", angle_rel_deg=35, drill_shape="oval", drill_width_mm=1.2,
                 drill_height_mm=0.6, drill_offset_mm=[0.2, -0.1], layers=["*.Cu"]),
        ],
    }
    circle_part = {
        "body_bbox_mm": [-0.5, -0.5, 0.5, 0.5],
        "pads": [
            dict(num="1", type="smd", x_rel_mm=0.0, y_rel_mm=0.0, w_mm=1.8, h_mm=1.8,
                 shape="circle", layers=["F.Cu"]),
        ],
    }
    catalog = [smd_part, tht_part, circle_part]
    bases = {
        (idx, side, angle): local_occupancy(part, side).rotate(angle)
        for idx, part in enumerate(catalog)
        for side in ("top", "bottom")
        for angle in (0, 30, 90, 180, 270)
    }

    # 1. Exact boundary & near-clearance adversarial sweeps (touching, +/- 1e-8, +/- 1e-5, +/- 1e-3).
    base_sq = local_occupancy({"body_bbox_mm": [-1.0, -1.0, 1.0, 1.0], "pads": []}, "top")
    origin_sq = base_sq.translate(10.0, 10.0)
    clearance = 2 * COMPACT_COURTYARD_MM
    for delta in (-1e-3, -1e-5, -1e-7, -1e-8, 0.0, 1e-8, 1e-7, 1e-5, 1e-3):
        for dx, dy in ((2.0 + clearance + delta, 0.0), (0.0, 2.0 + clearance + delta),
                       (2.0 + clearance * 0.7 + delta, 2.0 + clearance * 0.7 + delta)):
            other = base_sq.translate(10.0 + dx, 10.0 + dy)
            assert noninterfering("top", origin_sq, "top", other) == exact_shapely_noninterfering(
                "top", origin_sq, "top", other
            )
            assert other.combined_bounds == pytest.approx(other.combined.bounds, abs=1e-12)

    # 2. Randomized stress test across rotated/curved/through-hole polygons on same & opposite sides.
    rng = random.Random(20261120)
    keys = list(bases)
    for _ in range(400):
        k1 = rng.choice(keys)
        k2 = rng.choice(keys)
        x1, y1 = rng.uniform(0.0, 5.0), rng.uniform(0.0, 5.0)
        x2, y2 = x1 + rng.uniform(-3.2, 3.2), y1 + rng.uniform(-3.2, 3.2)
        o1 = bases[k1].translate(x1, y1)
        o2 = bases[k2].translate(x2, y2)
        assert noninterfering(k1[1], o1, k2[1], o2) == exact_shapely_noninterfering(k1[1], o1, k2[1], o2)
        assert noninterfering(k2[1], o2, k1[1], o1) == exact_shapely_noninterfering(k2[1], o2, k1[1], o1)
        # Verify cached AABBs match Shapely .bounds on translated Occupancy.
        for attr, b_attr in (
            ("body", "body_bounds"),
            ("copper", "copper_bounds"),
            ("holes", "holes_bounds"),
            ("hole_guard", "hole_guard_bounds"),
            ("combined", "combined_bounds"),
        ):
            geom = getattr(o1, attr)
            cached = getattr(o1, b_attr)
            if geom.is_empty:
                assert cached is None
            else:
                assert cached == pytest.approx(geom.bounds, abs=1e-12)


if __name__ == "__main__":
    import sys
    Path(sys.argv[1]).write_text(json.dumps(blocked_spec(),indent=2)+"\n")
