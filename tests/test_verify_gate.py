"""Comprehensive tests for Cloud PCB Studio VerifyGate, Gerber/Excellon DRC, Visual Inspect, and Workbench UI."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import numpy as np
from gerbonara.excellon import ExcellonFile
from PIL import Image

from cloud_pcb.verify_gate.gatekeeper import run_verify_gate
from cloud_pcb.verify_gate.gerber_drc import (
    _strict_pygerber_parse,
    _TicketCollector,
    audit_board1_pipeline_a,
    audit_board1_pipeline_b,
    audit_board1_spec,
    audit_board2_special,
)
from cloud_pcb.verify_gate.visual_inspect import (
    inspect_workbench_chromium,
    render_all_board_previews,
)
from cloud_pcb.workbench_ui.generate_workbench import generate_workbench_html

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = PROJECT_ROOT / "artifacts"


def test_strict_pygerber_rejects_malformed_g75_without_star(tmp_path: Path) -> None:
    bad_gbr = tmp_path / "Edge_Cuts.gbr"
    bad_gbr.write_text(
        "G04 Bad*\n%MOMM*%\n%FSLAX45Y45*%\n%IPPOS*%\nG75\n%LPD*%\nM02*\n",
        encoding="utf-8",
    )
    tc = _TicketCollector("synthetic", "layout_route")
    ok, _ = _strict_pygerber_parse(bad_gbr, tc, "gerber_Edge_Cuts")
    assert ok is False
    assert len(tc.tickets) == 1
    assert tc.tickets[0].severity == "CRITICAL"
    assert "G75" in tc.tickets[0].message


def test_spec_gate_rejects_low_stock_nan_mc_and_high_esd(tmp_path: Path) -> None:
    spec_tmp = tmp_path / "spec"
    shutil.copytree(ARTIFACTS / "spec", spec_tmp)

    # Corrupt stock, MC fc (-1.8e199 / NaN), and ESD current (>= 20mA)
    bom = json.loads((spec_tmp / "bom_verified.json").read_text(encoding="utf-8"))
    bom["parts"][0]["stock"] = 12
    (spec_tmp / "bom_verified.json").write_text(json.dumps(bom), encoding="utf-8")

    pwr = json.loads((spec_tmp / "spice_power_results.json").read_text(encoding="utf-8"))
    pwr["ac_filter_monte_carlo"]["runs"][0]["fc_hz"] = -1.8e199
    (spec_tmp / "spice_power_results.json").write_text(json.dumps(pwr), encoding="utf-8")

    esd = json.loads((spec_tmp / "spice_esd_results.json").read_text(encoding="utf-8"))
    esd["esd_surge_clamping"]["metrics"]["protected_1k_ic_clamp_peak_ma"] = 28.5
    (spec_tmp / "spice_esd_results.json").write_text(json.dumps(esd), encoding="utf-8")

    report = audit_board1_spec(spec_tmp)
    assert report.passed is False
    gates = {t.gate for t in report.tickets}
    assert "bom_stock_gt_100" in gates
    assert "spice_mc_fc_sanitized" in gates
    assert "spice_esd_limiter_lt_20ma" in gates


def test_board2_gate_rejects_tau_over_2pct_and_bad_edge_margin(tmp_path: Path) -> None:
    b2_tmp = tmp_path / "board2_special"
    shutil.copytree(ARTIFACTS / "board2_special", b2_tmp)

    spice = json.loads((b2_tmp / "special_coil_spice.json").read_text(encoding="utf-8"))
    spice["step_response"]["tau_error_pct"] = 4.2
    (b2_tmp / "special_coil_spice.json").write_text(json.dumps(spice), encoding="utf-8")

    coil = json.loads((b2_tmp / "coil_params.json").read_text(encoding="utf-8"))
    coil["axial_bz_canceling_per_amp_ut"] = -900.0
    (b2_tmp / "coil_params.json").write_text(json.dumps(coil), encoding="utf-8")

    report = audit_board2_special(b2_tmp)
    assert report.passed is False
    gates = {t.gate for t in report.tickets}
    assert "coil_spice_tau_error_lt_2pct" in gates
    assert "coil_constructive_flux_gt_10x" in gates


def test_real_board1_spec_and_board2_special_pass_all_gates() -> None:
    spec_rep = audit_board1_spec(ARTIFACTS / "spec")
    assert spec_rep.passed is True, spec_rep.tickets
    assert spec_rep.metrics["z0_masked_ohm"] >= 48.0
    assert spec_rep.metrics["z0_masked_ohm"] <= 52.0
    assert spec_rep.metrics["zdiff_masked_ohm"] >= 85.0
    assert spec_rep.metrics["zdiff_masked_ohm"] <= 95.0
    assert spec_rep.metrics["esd_protected_clamp_ma"] < 20.0

    b2_rep = audit_board2_special(ARTIFACTS / "board2_special")
    assert b2_rep.passed is True, b2_rep.tickets
    assert b2_rep.metrics["coil_flux_ratio"] > 10.0
    assert b2_rep.metrics["coil_tau_error_pct"] < 2.0
    assert b2_rep.metrics["coil_catch_diode_clamped"] is True
    assert min(b2_rep.metrics["copper_edge_margins_mm"].values()) >= 0.50


def test_real_board1_pipelines_excellon_and_bakeoff() -> None:
    # Verify Excellon files parse via gerbonara (never pygerber)
    for drl_path in (
        ARTIFACTS / "board1_kicad_fastroute" / "gerbers" / "PTH.drl",
        ARTIFACTS / "board1_kicad_fastroute" / "gerbers" / "NPTH.drl",
        ARTIFACTS / "board1_tscircuit" / "PTH.drl",
        ARTIFACTS / "board1_tscircuit" / "NPTH.drl",
        ARTIFACTS / "board2_special" / "gerbers" / "PTH.drl",
    ):
        ex = ExcellonFile.open(drl_path)
        assert len(ex.objects) >= 4, f"Unexpected hole count in {drl_path}"

    # Verify bakeoff invariants directly
    bakeoff = json.loads(
        (ARTIFACTS / "board1_kicad_fastroute" / "router_bakeoff.json").read_text(encoding="utf-8")
    )
    by_eng = {b["engine"]: b for b in bakeoff["benchmarks"]}
    assert by_eng["fastroute_v0.1.14"]["success"] is True
    assert by_eng["fastroute_v0.1.14"]["unrouted_nets"] == 0
    assert by_eng["tscircuit_capacity_autorouter"]["success"] is True
    assert by_eng["tscircuit_capacity_autorouter"]["unrouted_nets"] == 0
    assert by_eng["freerouting_cli_v2.5.0"]["runtime_ms"] > 0

    # Pipeline B & A gate reports capture exact real metrics & defects
    pb_rep = audit_board1_pipeline_b(ARTIFACTS / "board1_kicad_fastroute")
    assert pb_rep.checks_run >= 20
    assert pb_rep.metrics["copper_areas_mm2"]["In1_Cu.gbr"] > 1500.0
    assert pb_rep.metrics["copper_areas_mm2"]["In2_Cu.gbr"] > 1500.0
    assert pb_rep.metrics["npth_hole_count"] == 4

    pa_rep = audit_board1_pipeline_a(ARTIFACTS / "board1_tscircuit")
    assert pa_rep.checks_run >= 20
    assert pa_rep.metrics["tscircuit_error_count"] == 0
    assert pa_rep.metrics["copper_areas_mm2"]["In1_Cu.gbr"] > 1500.0
    assert pa_rep.metrics["copper_areas_mm2"]["In2_Cu.gbr"] > 1500.0


def test_full_verify_gate_renders_workbench_and_chromium() -> None:
    summary = run_verify_gate(ARTIFACTS)
    verify_dir = ARTIFACTS / "verify"
    renders_dir = verify_dir / "renders"

    assert (verify_dir / "gate_report.json").exists()
    assert (verify_dir / "defect_tickets.json").exists()
    assert (verify_dir / "chromium_inspect.json").exists()
    assert (verify_dir / "workbench.html").exists()

    for png_name in ("board1_top.png", "board1_bottom.png", "board2_top.png", "board2_inner1.png"):
        p = renders_dir / png_name
        assert p.exists(), f"Missing render {png_name}"
        img = Image.open(p).convert("RGB")
        assert float(np.asarray(img, dtype=np.float32).std()) > 5.0

    # Verify Workbench HTML constraints: ONLY allowed tailwind CDN & required CSS tokens
    html_text = (verify_dir / "workbench.html").read_text(encoding="utf-8")
    urls = re.findall(r'https?://[^\s"\'<>]+', html_text)
    assert urls == ["https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"], urls
    for token in ("--background", "--card", "--foreground", "--muted-foreground", "--border", "--primary"):
        assert token in html_text, f"Missing CSS token {token}"
    assert "data:image/png;base64," in html_text

    # Verify headless Chromium inspection passed cleanly
    chrom = summary["chromium_inspect"]
    assert chrom["passed"] is True, chrom
    assert chrom["console_errors"] == []
    assert chrom["dom_non_blank"] is True
    assert chrom["screenshot_pixel_std"] > 10.0


def test_kicad_drc_lvs_interior_epad_exemption_and_multilayer_zone_layer_filter(tmp_path: Path) -> None:
    from kiutils.board import Board
    from kiutils.footprint import Footprint, Pad
    from kiutils.items.brditems import Segment, Via
    from kiutils.items.common import Coordinate, Net, Position
    from kiutils.items.zones import FilledPolygon, Zone
    from cloud_pcb.verify_gate.gerber_drc import _check_kicad_drc_and_lvs

    b = Board.create_new()
    n_gnd = Net(number=1, name="GND")
    n_sig = Net(number=2, name="SIG")
    b.nets = [Net(number=0, name=""), n_gnd, n_sig]

    fp = Footprint()
    fp.entryName = "WQFN24"
    fp.position = Position(X=10.0, Y=10.0)
    # 6 perimeter pads + 1 interior thermal EPAD (1.7x1.7 mm)
    for idx, (px, py) in enumerate([(-2.0, -0.5), (-2.0, 0.5), (2.0, -0.5), (2.0, 0.5), (-0.5, 2.0), (0.5, 2.0)], start=1):
        p = Pad()
        p.number = str(idx)
        p.type = "smd"
        p.shape = "rect"
        p.position = Position(X=px, Y=py)
        p.size = Position(X=0.4, Y=0.2)
        p.layers = ["F.Cu"]
        p.net = n_sig if idx == 1 else n_gnd
        fp.pads.append(p)

    epad = Pad()
    epad.number = "7"
    epad.type = "smd"
    epad.shape = "rect"
    epad.position = Position(X=0.0, Y=0.0)
    epad.size = Position(X=1.7, Y=1.7)
    epad.layers = ["F.Cu"]
    epad.net = n_gnd
    fp.pads.append(epad)
    b.footprints = [fp]

    # Thermal via inside interior EPAD must NOT trigger via-in-pad violation
    v = Via()
    v.position = Position(X=10.0, Y=10.0)
    v.size = 0.55
    v.drill = 0.30
    v.net = 1
    v.tstamp = "via-1"
    b.traceItems.append(v)

    # Signal trace on F.Cu while multi-layer zone only fills B.Cu at that region
    s = Segment()
    s.start = Position(X=2.0, Y=2.0)
    s.end = Position(X=5.0, Y=2.0)
    s.width = 0.2
    s.layer = "F.Cu"
    s.net = 2
    s.tstamp = "seg-1"
    b.traceItems.append(s)

    z = Zone()
    z.net = 1
    z.netName = "GND"
    z.layers = ["F.Cu", "B.Cu"]
    z.tstamp = "zone-1"
    fpoly = FilledPolygon()
    fpoly.layer = "B.Cu"
    fpoly.coordinates = [Coordinate(X=1.0, Y=1.0), Coordinate(X=6.0, Y=1.0), Coordinate(X=6.0, Y=3.0), Coordinate(X=1.0, Y=3.0)]
    z.filledPolygons = [fpoly]
    b.zones.append(z)

    pcb = tmp_path / "test_board.kicad_pcb"
    b.to_file(str(pcb))

    res = _check_kicad_drc_and_lvs(pcb)
    assert res["via_in_pad_violations"] == []
    assert res["lvs_shorts"] == []

