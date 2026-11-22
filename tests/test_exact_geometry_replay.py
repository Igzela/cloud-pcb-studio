"""Independent tests for strictly-circumscribed curved/rotated pad polygons and exact-geometry replay."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kiutils.board import Board
from kiutils.footprint import Footprint, Pad
from kiutils.items.common import Net, Position
from kiutils.items.fpitems import FpText
from shapely.affinity import rotate as shp_rotate
from shapely.geometry import LineString, Point, Polygon, box

from cloud_pcb.general_solver.exact_geometry_replay import has_non_rectangular_pads, replay_exact_pad_geometry
from cloud_pcb.layout_route.dsn_ses_bridge import _circumscribed_pad_polygon_um, board_to_dsn
from contracts.schemas import NetClassRule

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _true_pad_polygon_specctra_um(shape: str, sx_mm: float, sy_mm: float, angle_deg: float, rr: float = 0.25) -> Polygon:
    hx = sx_mm * 500.0
    hy = sy_mm * 500.0
    if shape == "oval":
        r = min(hx, hy)
        if hx >= hy:
            g = LineString([(-(hx - r), 0.0), (hx - r, 0.0)]).buffer(r, resolution=64)
        else:
            g = LineString([(0.0, -(hy - r)), (0.0, hy - r)]).buffer(r, resolution=64)
    elif shape == "roundrect":
        r = 2.0 * min(hx, hy) * rr
        g = box(-(hx - r), -(hy - r), hx - r, hy - r).buffer(r, resolution=64)
    else:
        g = box(-hx, -hy, hx, hy)
    return shp_rotate(g, angle_deg, origin=(0.0, 0.0)) if abs(angle_deg) > 1e-9 else g


def test_circumscribed_pad_polygon_strictly_contains_true_copper():
    cases = [
        ("oval", 0.9, 2.4, -90.0, 0.25),
        ("oval", 1.025, 2.035, 37.5, 0.25),
        ("roundrect", 3.5, 3.5, 0.0, 0.25),
        ("roundrect", 1.125, 2.075, 45.0, 0.20),
        ("rect", 1.025, 0.515, 30.0, 0.0),
        ("rect", 2.0, 0.8, 135.0, 0.0),
    ]
    for shape, sx, sy, angle, rr in cases:
        pts = _circumscribed_pad_polygon_um(shape, sx, sy, angle, roundrect_ratio=rr)
        poly = Polygon(pts)
        assert poly.is_valid and poly.area > 0
        true_poly = _true_pad_polygon_specctra_um(shape, sx, sy, angle, rr=rr)
        uncovered = true_poly.difference(poly).area
        assert uncovered == 0.0, f"Uncovered copper {uncovered} um^2 for {(shape, sx, sy, angle, rr)}"


def _make_curved_pad_board() -> Board:
    board = Board.create_new()
    board.nets = [Net(number=0, name=""), Net(number=1, name="N1"), Net(number=2, name="N2")]

    def _fp(ref: str, x: float, y: float, pads: list[Pad]) -> Footprint:
        fp = Footprint()
        fp.entryName = ref
        fp.layer = "F.Cu"
        fp.position = Position(X=x, Y=y, angle=0.0)
        fp.properties = {"Reference": ref}
        fp.graphicItems.append(FpText(type="reference", text=ref, position=Position(X=0, Y=0)))
        fp.pads = pads
        return fp

    p_rr = Pad()
    p_rr.number = "1"
    p_rr.type = "thru_hole"
    p_rr.shape = "roundrect"
    p_rr.roundrectRatio = 0.25
    p_rr.position = Position(X=0.0, Y=0.0, angle=0.0)
    p_rr.size = Position(X=3.5, Y=3.5)
    p_rr.layers = ["*.Cu", "*.Mask"]
    p_rr.net = Net(number=1, name="N1")

    p_rr_2 = Pad()
    p_rr_2.number = "2"
    p_rr_2.type = "thru_hole"
    p_rr_2.shape = "circle"
    p_rr_2.position = Position(X=8.0, Y=0.0, angle=0.0)
    p_rr_2.size = Position(X=1.0, Y=1.0)
    p_rr_2.layers = ["*.Cu", "*.Mask"]
    p_rr_2.net = Net(number=1, name="N1")

    p_ov = Pad()
    p_ov.number = "1"
    p_ov.type = "thru_hole"
    p_ov.shape = "oval"
    p_ov.position = Position(X=0.0, Y=0.0, angle=-90.0)
    p_ov.size = Position(X=0.9, Y=2.4)
    p_ov.layers = ["*.Cu", "*.Mask"]
    p_ov.net = Net(number=2, name="N2")

    p_ov_2 = Pad()
    p_ov_2.number = "2"
    p_ov_2.type = "thru_hole"
    p_ov_2.shape = "circle"
    p_ov_2.position = Position(X=8.0, Y=0.0, angle=0.0)
    p_ov_2.size = Position(X=1.0, Y=1.0)
    p_ov_2.layers = ["*.Cu", "*.Mask"]
    p_ov_2.net = Net(number=2, name="N2")

    # True analytical gap between roundrect (7.16, 8.9495) and oval (5.077, 11.223) is 0.1466 mm.
    board.footprints = [
        _fp("U1", 7.160, 8.9495, [p_rr, p_rr_2]),
        _fp("U2", 5.077, 11.223, [p_ov, p_ov_2]),
    ]
    return board


def test_exact_geometry_replay_clears_false_45deg_artifact_and_catches_real_violation(tmp_path: Path):
    board = _make_curved_pad_board()
    assert has_non_rectangular_pads(board) is True

    # 1. Required clearance 0.130 mm < true 0.1466 mm gap: 45deg/rect fails, exact-geometry replay passes.
    netclasses = {
        "DEFAULT": NetClassRule(
            name="DEFAULT", trace_width_mm=0.20, clearance_mm=0.130,
            via_drill_mm=0.25, via_pad_mm=0.50, max_current_a=0.5, zero_via_allowed=True,
        )
    }
    net_to_class = {"N1": "DEFAULT", "N2": "DEFAULT"}
    export_kwargs = dict(
        netclasses=netclasses,
        net_to_class=net_to_class,
        board_bounds_mm=(0.0, 0.0, 20.0, 20.0),
        safe_pin_references=True,
        honor_rule_via_geometry=True,
        align_structure_clearance=True,
    )
    dsn = tmp_path / "board.dsn"
    ses = tmp_path / "board.ses"
    rep_path = tmp_path / "report.json"
    board_to_dsn(board, dsn, **export_kwargs)
    cmd = [
        "fastroute", "-de", str(dsn), "-do", str(ses), f"--report={rep_path}",
        "--max-time=10", "--multi-start=1", "--router.autorouter.max_threads=1",
    ]
    proc = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30, check=False)
    assert proc.returncode == 0
    orig_rep = json.loads(rep_path.read_text(encoding="utf-8"))
    assert orig_rep["stats"]["unrouted"] == 0
    assert orig_rep["stats"]["violations"] == 4

    procs: list[dict] = []
    exact_rep = replay_exact_pad_geometry(
        board, dsn, ses, orig_rep, cmd, PROJECT_ROOT, tmp_path, procs, dsn_export_kwargs=export_kwargs
    )
    assert exact_rep["stats"]["unrouted"] == 0
    assert exact_rep["stats"]["violations"] == 0

    # 2. Required clearance 0.150 mm > true 0.1466 mm gap: exact-geometry MUST still flag all 4 layers.
    strict_dir = tmp_path / "strict"
    strict_dir.mkdir()
    strict_classes = {
        "DEFAULT": NetClassRule(
            name="DEFAULT", trace_width_mm=0.20, clearance_mm=0.150,
            via_drill_mm=0.25, via_pad_mm=0.50, max_current_a=0.5, zero_via_allowed=True,
        )
    }
    strict_dsn = strict_dir / "strict.dsn"
    strict_ses = strict_dir / "strict.ses"
    strict_rep = strict_dir / "strict.json"
    board_to_dsn(
        board,
        strict_dsn,
        netclasses=strict_classes,
        net_to_class=net_to_class,
        board_bounds_mm=(0.0, 0.0, 20.0, 20.0),
        safe_pin_references=True,
        honor_rule_via_geometry=True,
        align_structure_clearance=True,
        exact_pad_shapes=True,
        snap_angle="none",
    )
    strict_cmd = [
        "fastroute", "-de", str(strict_dsn), "-do", str(strict_ses), f"--report={strict_rep}",
        "--multi-start=1", f"--initial-session={ses}",
        "--router.autorouter.enabled=false", "--router.fanout.enabled=false", "--router.optimizer.enabled=false",
    ]
    proc2 = subprocess.run(strict_cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30, check=False)
    assert proc2.returncode == 0
    strict_data = json.loads(strict_rep.read_text(encoding="utf-8"))
    assert strict_data["stats"]["violations"] >= 4


def test_verify_conservative_ses_copper_rejects_narrowing_and_path_drift(tmp_path: Path):
    import pytest
    from cloud_pcb.general_solver.exact_geometry_replay import _verify_conservative_ses_copper

    def _write_ses(path: Path, width_um: int, x2_um: int = 1000) -> None:
        path.write_text(
            "(session test.ses\n"
            "  (base_design test.dsn)\n"
            "  (routes\n"
            "    (resolution um 10)\n"
            "    (network_out\n"
            "      (net N1\n"
            f"        (wire (path F.Cu {width_um * 10} 0 0 {x2_um * 10} 0))\n"
            "      )\n"
            "    )\n"
            "  )\n"
            ")\n",
            encoding="utf-8",
        )

    ctrl = tmp_path / "ctrl.ses"
    wider = tmp_path / "wider.ses"
    narrower = tmp_path / "narrower.ses"
    shifted = tmp_path / "shifted.ses"
    _write_ses(ctrl, 325)
    _write_ses(wider, 327)
    _write_ses(narrower, 323)
    _write_ses(shifted, 325, x2_um=1010)

    _verify_conservative_ses_copper(ctrl, wider)
    with pytest.raises(ValueError, match="outside conservative bound"):
        _verify_conservative_ses_copper(ctrl, narrower)
    with pytest.raises(ValueError, match="endpoints"):
        _verify_conservative_ses_copper(ctrl, shifted)

