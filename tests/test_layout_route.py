"""Comprehensive unit and integration tests for Cloud PCB Studio layout & routing (Pipeline A & B)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from gerbonara.excellon import ExcellonFile
from gerbonara.rs274x import GerberFile
from kiutils.board import Board
from kiutils.items.brditems import LayerToken, Segment, Via
from kiutils.items.common import Net
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union

from cloud_pcb.layout_route.board1_kicad_fastroute import (
    BOARD_H,
    BOARD_W,
    BOARD1_STACKUP,
    DEFAULT_OUTDIR as PIPELINE_B_DIR,
    _inject_gnd_zones,
)
from cloud_pcb.layout_route.board1_tscircuit import (
    DEFAULT_OUTDIR as PIPELINE_A_DIR,
    sanitize_kicad_sexpr,
)
from cloud_pcb.layout_route.dsn_ses_bridge import parse_ses

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_sanitize_kicad_sexpr_strips_bare_models_preserves_valid() -> None:
    sample = """(footprint "test"
  (pad "1" smd rect (at 0 0) (size 0.5 0.5) (layers "F.Cu"))
  (model "resistors/0402.step")
  (model "valid.step"
    (offset (xyz 0 0 0))
    (scale (xyz 1 1 1))
    (rotate (xyz 0 0 0))
  )
)"""
    cleaned = sanitize_kicad_sexpr(sample)
    assert 'resistors/0402.step' not in cleaned
    assert 'valid.step' in cleaned
    assert '(offset (xyz 0 0 0))' in cleaned


def test_parse_ses_coordinate_and_resolution_scaling(tmp_path: Path) -> None:
    ses = tmp_path / "sample.ses"
    ses.write_text(
        """(session "sample"
  (base_design "sample")
  (routes
    (resolution um 10)
    (library_out
      (padstack "Via[0-3]_550:300_um"
        (shape (circle F.Cu 5500 0 0))
        (attach off)
      )
    )
    (network_out
      (net "VBUS"
        (wire (path F.Cu 20000 100000 -200000 150000 -200000))
        (via "Via[0-3]_550:300_um" 150000 -200000)
      )
    )
  )
)""",
        encoding="utf-8",
    )
    parsed = parse_ses(ses)
    assert parsed["routed_nets"] == 1
    assert parsed["via_count"] == 1
    assert parsed["usb_path_vias"] == 0
    assert abs(parsed["total_wirelength_mm"] - 5.0) < 1e-4
    seg = parsed["segments"][0]
    assert seg["width_mm"] == 2.0
    assert seg["x1"] == 10.0 and seg["y1"] == 20.0
    assert seg["x2"] == 15.0 and seg["y2"] == 20.0
    via = parsed["vias"][0]
    assert via["x"] == 15.0 and via["y"] == 20.0
    assert via["pad_mm"] == 0.55 and via["drill_mm"] == 0.30


def test_board1_saved_inner_ground_fills_preserve_antipads_and_island_cutoff(tmp_path: Path) -> None:
    board = Board.create_new()
    board.layers[1:1] = [LayerToken(ordinal=1, name="In1.Cu", type="power"),
                         LayerToken(ordinal=2, name="In2.Cu", type="power")]
    board.nets = [Net(0, ""), Net(1, "GND")]
    plane = box(1, 1, 8, 7).difference(unary_union([box(2, 2, 3, 3), box(5, 4, 6, 5)]))
    retained = box(9, 1, 10, 1.25)
    discarded = box(11, 1, 11.25, 1.25)
    _inject_gnd_zones(board, MultiPolygon([plane, retained, discarded]))

    path = tmp_path / "inner-ground.kicad_pcb"
    board.to_file(str(path))
    saved = Board.from_file(str(path))
    assert [zone.layers for zone in saved.zones] == [["In1.Cu"], ["In2.Cu"]]
    for zone in saved.zones:
        assert zone.netName == "GND"
        assert len(zone.polygons) == 1
        assert zone.filledAreasThickness == "no"
        assert zone.fillSettings.yes and zone.fillSettings.mode is None
        assert {piece.layer for piece in zone.filledPolygons} == set(zone.layers)
        copper = unary_union([Polygon([(point.X, point.Y) for point in piece.coordinates])
                             for piece in zone.filledPolygons])
        assert copper.equals(MultiPolygon([plane, retained]))


def test_pipeline_a_tscircuit_artifacts_and_physics() -> None:
    assert PIPELINE_A_DIR.is_dir(), "Run board1_tscircuit before testing"
    expected = [
        "circuit.json",
        "pcb.svg",
        "schematic.svg",
        "board1.kicad_pcb",
        "board1.kicad_sch",
        "F_Cu.gbr",
        "B_Cu.gbr",
        "In1_Cu.gbr",
        "In2_Cu.gbr",
        "F_Mask.gbr",
        "B_Mask.gbr",
        "F_SilkS.gbr",
        "B_SilkS.gbr",
        "F_Paste.gbr",
        "B_Paste.gbr",
        "Edge_Cuts.gbr",
        "PTH.drl",
        "NPTH.drl",
        "tscircuit_report.json",
    ]
    for name in expected:
        p = PIPELINE_A_DIR / name
        assert p.exists(), f"Missing Pipeline A artifact: {p}"
        assert p.stat().st_size > 0, f"Empty Pipeline A artifact: {p}"

    report = json.loads((PIPELINE_A_DIR / "tscircuit_report.json").read_text(encoding="utf-8"))
    assert report["runner_summary"]["error_count"] == 0, report["runner_summary"]["errors"]

    phys = report["physics"]
    assert phys["pcb_traces"] > 20
    assert phys["pcb_copper_pours"] == 2
    assert phys["pcb_keepouts"] >= 1
    assert phys["usb_tvs_distance_mm"] is not None and phys["usb_tvs_distance_mm"] <= 5.0
    assert phys["usb_path_vias"] == 0
    assert phys["cc_nets_distinct"] is True
    assert phys["vbus_max_width_mm"] >= 2.0
    assert abs(phys["rf_max_width_mm"] - 0.306) < 0.02

    # KiCad PCB roundtrip via kiutils
    board = Board.from_file(str(PIPELINE_A_DIR / "board1.kicad_pcb"))
    assert len(board.footprints) >= 30
    assert len(board.zones) >= 2

    # Gerbers & Drills parse cleanly via gerbonara and pours are non-empty
    for gbr_name in ("F_Cu.gbr", "In1_Cu.gbr", "In2_Cu.gbr", "B_Cu.gbr", "Edge_Cuts.gbr"):
        gf = GerberFile.open(PIPELINE_A_DIR / gbr_name)
        assert len(gf.objects) > 0, f"{gbr_name} has zero objects"

    pth = ExcellonFile.open(PIPELINE_A_DIR / "PTH.drl")
    npth = ExcellonFile.open(PIPELINE_A_DIR / "NPTH.drl")
    assert len(pth.objects) >= 14
    assert len(npth.objects) == 4


def test_pipeline_b_kicad_fastroute_artifacts_and_physics() -> None:
    assert PIPELINE_B_DIR.is_dir(), "Run board1_kicad_fastroute before testing"
    gerber_dir = PIPELINE_B_DIR / "gerbers"
    for fname in [
        "board_unrouted.kicad_pcb",
        "board.dsn",
        "board_fastroute.ses",
        "board_freerouting.ses",
        "fastroute_report.json",
        "router_bakeoff.json",
        "routed.kicad_pcb",
        "schematic.svg",
        "pipeline_b_report.json",
    ]:
        p = PIPELINE_B_DIR / fname
        assert p.exists(), f"Missing Pipeline B artifact: {p}"
        assert p.stat().st_size > 0, f"Empty Pipeline B artifact: {p}"

    for gname in [
        "F_Cu.gbr",
        "In1_Cu.gbr",
        "In2_Cu.gbr",
        "B_Cu.gbr",
        "F_Mask.gbr",
        "B_Mask.gbr",
        "F_Paste.gbr",
        "B_Paste.gbr",
        "F_SilkS.gbr",
        "B_SilkS.gbr",
        "Edge_Cuts.gbr",
        "PTH.drl",
        "NPTH.drl",
    ]:
        p = gerber_dir / gname
        assert p.exists(), f"Missing Gerber/Drill: {p}"
        assert p.stat().st_size > 0, f"Empty Gerber/Drill: {p}"

    # Verify KiCad stackup & routed board
    board = Board.from_file(str(PIPELINE_B_DIR / "routed.kicad_pcb"))
    assert len(board.setup.stackup.layers) == 7
    assert len(board.zones) >= 3  # RF keepout + In1.Cu GND + In2.Cu GND

    # Verify copper-to-edge clearance >= 0.50 mm on all routed traces & vias
    edge_min = BOARD1_STACKUP.copper_to_edge_mm - 1e-4
    for item in board.traceItems:
        if isinstance(item, Via):
            r = float(item.size) / 2.0
            assert item.position.X - r >= edge_min
            assert item.position.X + r <= BOARD_W - edge_min
            assert item.position.Y - r >= edge_min
            assert item.position.Y + r <= BOARD_H - edge_min
        elif isinstance(item, Segment):
            hw = float(item.width) / 2.0
            for pt in (item.start, item.end):
                assert pt.X - hw >= edge_min - 0.05
                assert pt.X + hw <= BOARD_W - edge_min + 0.05
                assert pt.Y - hw >= edge_min - 0.05
                assert pt.Y + hw <= BOARD_H - edge_min + 0.05

    # Verify Gerber objects and non-empty L2/L3 GND pours
    for gbr_name in ("F_Cu.gbr", "In1_Cu.gbr", "In2_Cu.gbr", "B_Cu.gbr", "Edge_Cuts.gbr"):
        gf = GerberFile.open(gerber_dir / gbr_name)
        assert len(gf.objects) > 0, f"{gbr_name} has zero objects"

    pth = ExcellonFile.open(gerber_dir / "PTH.drl")
    npth = ExcellonFile.open(gerber_dir / "NPTH.drl")
    assert len(npth.objects) == 4
    thermal_hits = [o for o in pth.objects if abs(o.aperture.diameter - 0.30) < 0.02]
    assert len(thermal_hits) >= 4

    report = json.loads((PIPELINE_B_DIR / "pipeline_b_report.json").read_text(encoding="utf-8"))
    pour = report["gnd_pour_stats"]
    assert pour["is_empty"] is False
    assert pour["pour_area_mm2"] > 1800.0
    assert pour["fill_ratio"] > 0.75

    phys = report["physics"]
    assert phys["usb_tvs_distance_mm"] <= 5.0
    assert phys["usb_path_vias"] == 0
    assert phys["cc_nets_distinct"] is True
    assert phys["vbus_max_width_mm"] >= 2.0
    assert abs(phys["rf_max_width_mm"] - 0.306) < 0.005
    assert phys["qfn_thermal_vias"] == 4
    assert phys["via_in_smt_pad_violations"] == []


def test_autorouter_bakeoff_comparison() -> None:
    bakeoff_path = PIPELINE_B_DIR / "router_bakeoff.json"
    assert bakeoff_path.exists()
    data = json.loads(bakeoff_path.read_text(encoding="utf-8"))
    by_engine = {b["engine"]: b for b in data["benchmarks"]}
    assert "fastroute_v0.1.14" in by_engine
    assert "freerouting_cli_v2.5.0" in by_engine
    assert data["selected_winner"] == "fastroute_v0.1.14"

    fr = by_engine["fastroute_v0.1.14"]
    assert fr["success"] is True, f"fastroute failed: {fr}"
    assert fr["unrouted_nets"] == 0
    assert fr["runtime_ms"] > 0
    assert fr["total_wirelength_mm"] > 0

    free = by_engine["freerouting_cli_v2.5.0"]
    assert free["runtime_ms"] > 0
    assert free["total_wirelength_mm"] > 0
    assert free["routed_nets"] >= 20


def test_board_to_dsn_skips_non_copper_paste_pads_and_uses_pad_angle(tmp_path: Path) -> None:
    from kiutils.footprint import Footprint, Pad
    from kiutils.items.common import Net, Position
    from contracts.schemas import NetClassRule
    from cloud_pcb.layout_route.dsn_ses_bridge import board_to_dsn

    b = Board.create_new()
    n_gnd = Net(number=1, name="GND")
    n_sig = Net(number=2, name="SIG")
    b.nets = [Net(number=0, name=""), n_gnd, n_sig]

    fp = Footprint()
    fp.properties = {"Reference": "U1", "Value": "QFN24"}
    fp.position = Position(X=10.0, Y=10.0, angle=90.0)

    # Non-copper F.Paste aperture pad inside EPAD (must NOT become F.Cu copper!)
    p_paste = Pad()
    p_paste.number = ""
    p_paste.type = "smd"
    p_paste.shape = "rect"
    p_paste.position = Position(X=0.0, Y=0.0, angle=90.0)
    p_paste.size = Position(X=0.9, Y=0.9)
    p_paste.layers = ["F.Paste"]

    # Real copper EPAD with explicit pad angle=90.0 (footprint angle=90.0 -> world 2.0x3.0, NOT double-rotated)
    p_epad = Pad()
    p_epad.number = "25"
    p_epad.type = "smd"
    p_epad.shape = "rect"
    p_epad.position = Position(X=0.0, Y=0.0, angle=90.0)
    p_epad.size = Position(X=3.0, Y=2.0)
    p_epad.layers = ["F.Cu", "F.Mask"]
    p_epad.net = n_gnd

    # Signal pad
    p_sig = Pad()
    p_sig.number = "1"
    p_sig.type = "smd"
    p_sig.shape = "rect"
    p_sig.position = Position(X=-2.0, Y=0.0, angle=90.0)
    p_sig.size = Position(X=0.6, Y=0.2)
    p_sig.layers = ["F.Cu", "F.Paste", "F.Mask"]
    p_sig.net = n_sig

    fp.pads = [p_paste, p_epad, p_sig]
    b.footprints = [fp]

    dsn = tmp_path / "out.dsn"
    rules = {"DEFAULT": NetClassRule(name="DEFAULT", trace_width_mm=0.2, clearance_mm=0.15, via_drill_mm=0.3, via_pad_mm=0.55, max_current_a=0.5)}
    board_to_dsn(b, dsn, rules, {"GND": "DEFAULT", "SIG": "DEFAULT"}, board_bounds_mm=(0.0, 0.0, 20.0, 20.0))
    txt = dsn.read_text(encoding="utf-8")
    assert "P1" not in txt
    assert "900x900" not in txt
    assert "Rect_T_2000x3000_um" in txt
    assert "Rect_T_200x600_um" in txt

