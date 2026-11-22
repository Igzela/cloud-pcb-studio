"""Synthetic adversarial tests; no reference board or holdout dependency."""
import json
from pathlib import Path

import pytest
from shapely.geometry import box

from cloud_pcb.general_solver.run_blind import (
    PlacementExhausted, _build_blind_kicad_board, _classify_components_and_place,
    _pad_world_polygon_kicad,
)
from cloud_pcb.general_solver.two_side_packing import (
    in_outline, local_occupancy, mechanical_keepouts, mounting_guard_radius,
    noninterfering, pack_two_sided, side_options,
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


if __name__ == "__main__":
    import sys
    Path(sys.argv[1]).write_text(json.dumps(blocked_spec(),indent=2)+"\n")
