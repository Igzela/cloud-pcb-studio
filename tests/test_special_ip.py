"""Comprehensive tests for Cloud PCB Studio Board 2 (Special IP):
4-Layer Planar Haptic Coil, Chevron CapTouch Slider, Curved/Meander Routing,
ngspice-41 RLC/PWM Flyback Verification, KiCad Serialization, and Gerbonara/PyGerber Export.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from gerbonara.excellon import ExcellonFile
from gerbonara.rs274x import GerberFile
from kiutils.board import Board
from kiutils.footprint import Footprint
from kiutils.items.brditems import Arc as KiArc
from kiutils.items.brditems import Segment as KiSegment
from kiutils.items.brditems import Via as KiVia
from kiutils.items.gritems import GrArc, GrPoly
from pygerber.gerberx3.api.v2 import GerberFile as PyGerberFile
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from cloud_pcb.special_ip import (
    PlanarCoilSpec,
    build_captouch_slider,
    build_planar_coil,
    chaikin_smooth,
    fillet_polyline_with_arcs,
    generate_serpentine_meander,
    run_coil_spice_simulation,
)
from cloud_pcb.special_ip.export_special_board import (
    BOARD_H_MM,
    BOARD_W_MM,
    assemble_board2,
    export_board2_artifacts,
)


def test_planar_coil_geometry_and_constructive_flux_invariant() -> None:
    coil = build_planar_coil()

    assert len(coil.layers) == 4
    assert [lyr.layer for lyr in coil.layers] == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]

    # Non-self-intersecting & DRC trace/space rules
    assert coil.spec.trace_width_mm >= 0.25
    assert coil.min_turn_clearance_mm >= 0.20
    for lyr in coil.layers:
        assert lyr.is_simple, f"Layer {lyr.layer} spiral self-intersects"
        assert lyr.min_turn_clearance_mm >= 0.20

    # Critical Physics Invariant: alternating winding chirality across inter-layer vias
    chiralities = [lyr.winding_chirality for lyr in coil.layers]
    assert chiralities == [1, -1, 1, -1]

    # Current circulates in identical angular direction -> same-sign axial Bz across all 4 layers
    bz_signs = [math.copysign(1.0, lyr.axial_bz_per_amp_ut) for lyr in coil.layers]
    assert len(set(bz_signs)) == 1
    assert all(lyr.current_angular_direction == "CW" for lyr in coil.layers)
    assert coil.constructive_flux_verified is True
    assert abs(coil.axial_bz_total_per_amp_ut) > 3.5 * min(abs(l.axial_bz_per_amp_ut) for l in coil.layers)
    assert abs(coil.axial_bz_total_per_amp_ut) > 10.0 * abs(coil.axial_bz_canceling_per_amp_ut)

    # Constructive mutual coupling >> canceling counterfactual
    assert coil.inductance_constructive_uh > 20.0
    assert coil.inductance_constructive_uh > 20.0 * coil.inductance_canceling_counterfactual_uh

    # Physical RLC, cross-sections & thermal safety
    assert coil.layers[0].copper_thickness_mm == pytest.approx(0.035)
    assert coil.layers[1].copper_thickness_mm == pytest.approx(0.0175)
    assert coil.r_dc_25c_ohm > 2.0
    assert coil.pwm_rms_current_a < coil.ampacity_inner_20c_a
    assert coil.thermal_rise_pwm_c < 40.0


def test_planar_coil_counterfactual_canceling_fails_flux_addition() -> None:
    """Prove via Biot-Savart that failing to invert chirality on inner/bottom layers cancels Bz."""
    coil = build_planar_coil()
    # If In1.Cu and B.Cu kept the same outward-to-inward geometry without polarity inversion,
    # their outward current flow reverses sign: F.Cu - In1.Cu + In2.Cu - B.Cu ~ 0
    bz_no_inversion = (
        coil.layers[0].axial_bz_per_amp_ut
        - coil.layers[1].axial_bz_per_amp_ut
        + coil.layers[2].axial_bz_per_amp_ut
        - coil.layers[3].axial_bz_per_amp_ut
    )
    assert abs(bz_no_inversion) < 0.05 * abs(coil.axial_bz_total_per_amp_ut)


def test_planar_coil_ngspice_step_and_pwm_flyback(tmp_path: Path) -> None:
    coil = build_planar_coil()
    spice_json = tmp_path / "special_coil_spice.json"
    res = run_coil_spice_simulation(coil, output_json_path=spice_json)

    assert spice_json.exists()
    loaded = json.loads(spice_json.read_text(encoding="utf-8"))
    assert "ngspice" in loaded["simulator"].lower()

    step = res["step_response"]
    assert step["tau_error_pct"] < 3.0
    assert step["i_steady_simulated_a"] == pytest.approx(step["i_steady_analytical_a"], rel=0.02)
    assert len(step["waveform"]) >= 50

    pwm = res["pwm_flyback"]
    assert pwm["catch_diode_clamped"] is True
    assert pwm["v_flyback_clamp_peak_v"] < coil.spec.drive_voltage_v + 0.8
    assert pwm["i_diode_flyback_peak_a"] > 0.1
    assert len(pwm["waveform"]) >= 50


def test_captouch_chevron_slider_clearances() -> None:
    ct = build_captouch_slider()

    assert len(ct.electrodes) == 4
    assert ct.all_polygons_valid is True
    assert ct.drc_passed is True
    assert ct.min_electrode_clearance_mm >= 0.20
    assert ct.min_guard_clearance_mm >= 0.50
    assert ct.min_via_other_net_clearance_mm >= 0.20
    assert ct.min_board_edge_clearance_mm >= 0.50

    # Balanced capacitance area across all 4 interlocking chevrons
    areas = [e.area_mm2 for e in ct.electrodes]
    assert max(areas) - min(areas) < 0.1


def test_curved_routing_chaikin_arcs_and_meander_obstacle_avoidance() -> None:
    pts = [(0.0, 0.0), (5.0, 0.0), (8.0, 4.0), (14.0, 4.0)]
    sm = chaikin_smooth(pts, iterations=3)
    assert sm[0] == pts[0]
    assert sm[-1] == pts[-1]
    assert len(sm) > len(pts)

    filleted = fillet_polyline_with_arcs(pts, fillet_radius_mm=1.0, width_mm=0.25, layer="F.Cu", net_name="SIG")
    assert filleted.arc_count == 2
    assert filleted.segment_count == 3
    raw_len = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))
    assert filleted.total_length_mm < raw_len

    # Place obstacle directly on preferred +Y side (y in [0.5, 2.1]) so meander MUST flip to -Y side cleanly
    obs_top = [Point(5.0, 1.3).buffer(0.8)]
    m = generate_serpentine_meander(
        start=(0.0, 0.0),
        end=(10.0, 0.0),
        target_length_mm=15.0,
        width_mm=0.25,
        min_clearance_mm=0.20,
        max_amplitude_mm=2.0,
        obstacles=obs_top,
        symmetric=False,
        preferred_side=1,
    )
    assert m.clearance_ok is True
    assert m.is_simple is True
    assert m.length_error_mm < 0.05
    assert m.min_obstacle_clearance_mm >= 0.20
    assert m.min_self_clearance_mm >= 0.20
    # Verify meander flipped into -Y region (y < 0) to avoid obs_top
    ys = [pt[1] for pt in m.trace_path.to_polyline()]
    assert min(ys) < -0.5 and max(ys) <= 0.01


def test_full_board_assembly_kicad_and_gerbonara_pygerber_roundtrip(tmp_path: Path) -> None:
    manifest = export_board2_artifacts(output_dir=tmp_path, run_spice=True)

    assert manifest["board_drc"]["passed"] is True
    assert manifest["board_drc"]["min_copper_to_edge_mm"] >= 0.50
    assert manifest["board_drc"]["min_inter_net_clearance_mm"] >= 0.20
    assert manifest["board_drc"]["min_drill_wall_clearance_mm"] >= 0.50

    for m_name, m_info in manifest["meanders"].items():
        assert m_info["clearance_ok"] is True, f"Meander {m_name} failed clearance check"
        assert m_info["length_error_mm"] < 0.05

    # Verify KiCad footprints and PCB parse cleanly via kiutils
    coil_fp = Footprint.from_file(manifest["kicad_files"]["planar_coil_mod"])
    assert "1,2" in coil_fp.netTiePadGroups
    assert len(coil_fp.pads) == 5

    ct_fp = Footprint.from_file(manifest["kicad_files"]["captouch_slider_mod"])
    gr_polys = [p for pad in ct_fp.pads for p in pad.customPadPrimitives if isinstance(p, GrPoly)]
    assert len(gr_polys) == 4

    pcb = Board.from_file(manifest["kicad_files"]["special_board_pcb"])
    sig_layers = [l.name for l in pcb.layers if l.type == "signal"]
    assert sig_layers == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    assert any(isinstance(t, KiArc) for t in pcb.traceItems)
    assert any(isinstance(t, KiSegment) for t in pcb.traceItems)
    assert any(isinstance(t, KiVia) for t in pcb.traceItems)
    assert any(isinstance(g, GrArc) for g in pcb.graphicItems)

    # Verify all 9 RS-274X Gerbers parse cleanly in both gerbonara and pygerber
    expected_gerbers = ["F_Cu", "In1_Cu", "In2_Cu", "B_Cu", "F_Mask", "B_Mask", "F_SilkS", "B_SilkS", "Edge_Cuts"]
    for gname in expected_gerbers:
        gpath = Path(manifest["gerber_files"][gname])
        assert gpath.exists()
        gb = GerberFile.open(str(gpath))
        assert len(gb.objects) > 0
        pg = PyGerberFile.from_file(str(gpath)).parse()
        info = pg.get_info()
        assert float(info.width_mm) > 0.0

    # Verify Edge_Cuts dimensions are 50 x 50 mm (+ 0.10mm line width)
    edge_pg = PyGerberFile.from_file(manifest["gerber_files"]["Edge_Cuts"]).parse().get_info()
    assert float(edge_pg.width_mm) == pytest.approx(BOARD_W_MM + 0.10, abs=0.02)
    assert float(edge_pg.height_mm) == pytest.approx(BOARD_H_MM + 0.10, abs=0.02)

    # Verify copper Gerbers stay >= 0.50mm inside 50 x 50 mm board
    for cu in ("F_Cu", "In1_Cu", "In2_Cu", "B_Cu"):
        gf = GerberFile.open(manifest["gerber_files"][cu])
        (minx, miny), (maxx, maxy) = gf.bounding_box()
        assert minx >= 0.50
        assert miny >= 0.50
        assert BOARD_W_MM - maxx >= 0.50
        assert BOARD_H_MM - maxy >= 0.50

    # Verify Excellon drills roundtrip
    drl = ExcellonFile.open(manifest["excellon_file"])
    assert sorted(round(float(d), 3) for d in drl.drill_sizes()) == [0.3, 1.0]
    assert sum(drl.hit_count().values()) >= 20
