"""Synthetic controls for mounting clearances through the PCB audit and preview."""

import pytest
from kiutils.board import Board

from cloud_pcb.general_solver.assembly import assembly_scene
from cloud_pcb.general_solver.compare_reference import _extract_metrics
from cloud_pcb.general_solver.run_blind import _build_blind_kicad_board
from cloud_pcb.general_solver.semantics import component_elevation, mechanical_check


def footprint(ref, *, side, height=1.0, standoff=0):
    return {
        "refdes": ref, "footprint_name": "Synthetic:Enclosure", "value": "enclosure",
        "side": side, "body_bbox_mm": [-1, -1, 1, 1], "height_mm": height,
        "standoff_mm": standoff, "pads": [],
    }


def specimen(components, keepouts):
    return {
        "board_outline_mm": {"width": 20, "height": 20},
        "components": components, "nets": {}, "edge_connectors": [], "mounting_holes": [],
        "design_requirements": {"power_rails": [], "differential_pairs": []},
        "mechanical_keepouts": keepouts,
    }


def test_thick_pcb_standoff_detects_bottom_collision_in_scene_and_comparator(tmp_path):
    spec = specimen([footprint("part", side="bottom", standoff=.5)], [{
        "polygon_mm": [(7, 7), (9, 7), (9, 9), (7, 9)],
        "z_range_mm": [-3.8, -3.2],
    }])
    poses = {"part": (8, 8, 37)}
    board = _build_blind_kicad_board(spec, poses)[0]
    # The same XY geometry clears the obstacle on the original 1.6-mm board.
    assert assembly_scene(board, spec, poses)["mechanical"]["passed"]

    board.general.thickness = 2.4
    path = tmp_path / "thick-board.kicad_pcb"
    board.to_file(str(path))
    scene = assembly_scene(Board.from_file(str(path)), spec, poses)
    body = scene["components"][0]
    assert scene["thickness_mm"] == 2.4
    assert body["position"] == (8, 8, 37)
    assert body["base_z_mm"] == pytest.approx(-2.9)
    assert body["standoff_mm"] == .5
    assert body["z_range_mm"] == pytest.approx((-3.9, -2.9))
    assert scene["mechanical"]["collisions"] == [{
        "kind": "mechanical_keepout", "refdes": "part", "keepout_index": 0,
    }]
    metrics = _extract_metrics(path, "synthetic", spec)
    assert metrics["independent_4layer"]["mechanical"] == scene["mechanical"]


def test_top_standoff_clears_shallow_keepout_and_legacy_bottom_interval_is_preserved():
    spec = specimen([footprint("front", side="top"), footprint("back", side="bottom")], [{
        "polygon_mm": [(7, 7), (9, 7), (9, 9), (7, 9)],
        "z_range_mm": [.05, .2],
    }])
    poses = {"front": (8, 8, 0), "back": (8, 8, 0)}
    result = mechanical_check(spec, poses)
    assert result == mechanical_check(spec, poses, board_thickness_mm=1.6)
    assert [x["kind"] for x in result["collisions"]] == ["mechanical_keepout"]
    assert component_elevation(side="bottom", board_thickness_mm=1.6, height_mm=1.0)["z_range_mm"] == (-2.6, -1.6)

    spec["components"][0]["standoff_mm"] = .5
    board = _build_blind_kicad_board(spec, poses)[0]
    scene = assembly_scene(board, spec, poses)
    assert scene["mechanical"]["passed"]
    assert scene["components"][0]["z_range_mm"] == (.5, 1.5)


@pytest.mark.parametrize(("field", "value"), [
    ("side", "sideways"), ("board_thickness_mm", 0), ("board_thickness_mm", float("nan")),
    ("height_mm", 0), ("standoff_mm", -.1), ("standoff_mm", float("inf")),
])
def test_invalid_z_inputs_cannot_produce_valid_envelope(field, value):
    args = dict(side="top", board_thickness_mm=1.6, height_mm=1.0, standoff_mm=0)
    args[field] = value
    with pytest.raises(ValueError):
        component_elevation(**args)
