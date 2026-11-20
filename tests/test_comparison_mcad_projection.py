"""Check the mechanical comparison with serialized, synthetic KiCad boards."""

import copy
import math

from cloud_pcb.general_solver.compare_reference import _extract_metrics
from cloud_pcb.general_solver.run_blind import _build_blind_kicad_board
from cloud_pcb.general_solver.semantics import mechanical_check


def package(ref, bounds, *, side=None, height=1.0):
    component = {
        "refdes": ref, "footprint_name": "Synthetic:Package", "value": ref,
        "body_bbox_mm": bounds, "height_mm": height,
        "pads": [{"num": "1", "type": "smd", "shape": "rect", "net": "",
                  "x_rel_mm": .2, "y_rel_mm": .1, "w_mm": .4, "h_mm": .6,
                  "layers": ["F.Cu"]}],
    }
    if side is not None:
        component["side"] = side
    return component


def specimen(components, keepouts=()):
    return {
        "board_outline_mm": {"width": 20, "height": 20},
        "components": components, "mounting_holes": [], "edge_connectors": [], "nets": {},
        "design_requirements": {"power_rails": [], "differential_pairs": []},
        "mechanical_keepouts": list(keepouts),
    }


def audit(path, board, spec):
    board.to_file(str(path))
    return _extract_metrics(path, "synthetic", spec)["independent_4layer"]["mechanical"]


def test_actual_sides_remove_spec_only_false_collision(tmp_path):
    spec = specimen([package("front", [-1, -1, 1, 1]), package("back", [-1, -1, 1, 1])])
    poses = {"front": (8, 8, 0), "back": (8, 8, 0)}
    effective = copy.deepcopy(spec)
    effective["components"][1]["side"] = "bottom"
    board = _build_blind_kicad_board(effective, poses)[0]

    assert mechanical_check(spec, poses)["collision_count"] == 1
    assert audit(tmp_path / "opposite_faces.kicad_pcb", board, spec)["passed"]
    assert all("side" not in component for component in spec["components"])


def test_rotated_asymmetric_bottom_body_hits_only_mirrored_keepout(tmp_path):
    origin = (8.0, 9.0)
    angle_deg = 37
    angle = math.radians(angle_deg)
    # The canonical body center is (1.25, 0.5) relative to its electrical origin.
    # KiCad rotates clockwise in board coordinates after flipping local X.
    def at_center(local_x):
        px = origin[0] + local_x * math.cos(angle) + .5 * math.sin(angle)
        py = origin[1] - local_x * math.sin(angle) + .5 * math.cos(angle)
        return {
            "polygon_mm": [(px - .1, py - .1), (px + .1, py - .1),
                           (px + .1, py + .1), (px - .1, py + .1)],
            "z_range_mm": [-2.4, -1.8],
        }

    spec = specimen([package("part", [.5, 0, 2, 1])], [at_center(-1.25), at_center(1.25)])
    poses = {"part": (*origin, angle_deg)}
    effective = copy.deepcopy(spec)
    effective["components"][0]["side"] = "bottom"
    board = _build_blind_kicad_board(effective, poses)[0]
    # Test board-frame normalization with a real serialized nonzero edge origin.
    for edge in board.graphicItems:
        for point in (edge.start, edge.end):
            point.X += 100
            point.Y += 50
    board.footprints[0].position.X += 100
    board.footprints[0].position.Y += 50

    assert mechanical_check(spec, poses)["collision_count"] == 0
    assert audit(tmp_path / "mirrored_body.kicad_pcb", board, spec)["collisions"] == [{
        "kind": "mechanical_keepout", "refdes": "part", "keepout_index": 0,
    }]

    # A legacy external bottom package could have preserved already mirrored
    # local coordinates. Without the solver transform contract it is unverified.
    board.generator = "pcbnew"
    mechanical = audit(tmp_path / "unknown_handedness.kicad_pcb", board, spec)
    assert mechanical["unverified_envelopes"] == ["part"]
    assert "handedness" in mechanical["unverified_board_projection"]["part"]
    assert not mechanical["passed"]


def test_missing_duplicate_extra_and_invalid_envelopes_remain_unverified(tmp_path):
    valid = [-1, -1, 1, 1]
    spec = specimen([package("missing", valid), package("duplicated", valid),
                     package("bad_box", [1, 0, -1, 1])])
    effective = specimen([package("duplicated", valid), package("bad_box", valid),
                          package("extra", valid)])
    poses = {"duplicated": (5, 5, 0), "bad_box": (10, 10, 0), "extra": (15, 15, 0)}
    board = _build_blind_kicad_board(effective, poses)[0]
    board.footprints.append(copy.deepcopy(board.footprints[0]))
    mechanical = audit(tmp_path / "incomplete_package_evidence.kicad_pcb", board, spec)
    assert set(mechanical["unverified_envelopes"]) == {"missing", "duplicated", "bad_box", "extra"}
    assert set(mechanical["unverified_board_projection"]) == set(mechanical["unverified_envelopes"])
    assert not mechanical["all_envelopes_verified"]
    assert not mechanical["passed"]
