"""Comprehensive tests for cloud_pcb.parts_dfm (Catalog, DFM Physics, and ngspice 41 SPICE)."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from cloud_pcb.parts_dfm.catalog import (
    PROJECT_ROOT,
    PartCatalog,
    estimate_effective_cap_ratio,
    estimate_pcba_cost,
    generate_board1_placements,
    normalize_lcsc_code,
    select_board1_bom,
)
from cloud_pcb.parts_dfm.cli import run_pipeline
from cloud_pcb.parts_dfm.dfm_physics import (
    MM_TO_MIL,
    OZ_TO_MIL,
    BoardStackupRequirements,
    build_board1_net_rules,
    calc_diff_pair_impedance,
    calc_ipc2152_trace_width_mil,
    calc_ipc2221_trace_width_mil,
    calc_microstrip_impedance,
    calc_thermal_via_array_c_per_w,
    calc_via_barrel_ampacity_a,
    check_ldo_vs_buck_thermal,
    check_mlcc_dc_bias,
    check_usbc_and_esd_rules,
    select_stackup_and_finish,
    solve_diff_pair_width_mil,
    solve_microstrip_width_mil,
)
from cloud_pcb.parts_dfm.spice_sim import (
    extract_corner_frequency_hz,
    run_buck_ldo_power_sim,
    run_esd_clamp_sim,
    run_rc_filter_monte_carlo_sim,
    sanitize_ngspice_ac_data,
)
from contracts.schemas import RailSpec, SelectedPart


def test_catalog_loading_and_offline_resilience(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = PartCatalog.load()
    assert len(catalog) > 1500
    assert normalize_lcsc_code("23186") == "C23186"
    r5k1 = catalog.by_lcsc("C23186")
    assert r5k1 is not None
    assert r5k1.tier == "Basic"
    assert r5k1.stock > 100

    # Verify jlcsearch fallback works cleanly when network raises URLError
    import urllib.request

    def _boom(*args, **kwargs):
        raise OSError("Simulated offline mode")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    fallback_hits = catalog.enrich_from_jlcsearch("USBLC6-2SC6")
    assert len(fallback_hits) >= 1
    assert any(p.lcsc_code == "C5261088" for p in fallback_hits)


def test_board1_bom_selection_and_cost_math() -> None:
    catalog = PartCatalog.load()
    parts = select_board1_bom(catalog, min_stock=101)

    # Every selected part must have stock > 100 and valid C-prefixed LCSC code
    assert all(p.stock > 100 for p in parts)
    assert all(p.lcsc_code.startswith("C") and p.lcsc_code[1:].isdigit() for p in parts)

    # Verify critical Board 1 slots exist
    roles = {p.role for p in parts}
    for required_role in (
        "usbc_receptacle_16p",
        "usbc_cc1_pulldown",
        "usbc_cc2_pulldown",
        "usb_esd_tvs",
        "buck_3v3_sync_reg",
        "buck_power_inductor",
        "ldo_2v5_low_noise",
        "mcu_qfn_usb_pd",
        "hse_crystal_12mhz",
        "coil_driver_nmos",
    ):
        assert required_role in roles

    # Dual independent 5.1k CC1 & CC2 resistors
    cc1 = [p for p in parts if p.role == "usbc_cc1_pulldown"][0]
    cc2 = [p for p in parts if p.role == "usbc_cc2_pulldown"][0]
    assert cc1.refdes != cc2.refdes
    assert "5.1k" in cc1.value and "5.1k" in cc2.value

    # Majority Basic/Preferred placements (> 80%)
    cost = estimate_pcba_cost(parts, board_qty=5)
    assert cost["basic_preferred_placement_ratio"] >= 0.80
    assert cost["setup_fee_usd"] == 8.00
    assert cost["stencil_fee_usd"] == 1.50
    expected_joint_fee = round(cost["total_solder_joints_per_board"] * 0.0017 * 5, 4)
    assert cost["joint_fee_usd"] == pytest.approx(expected_joint_fee, rel=1e-6)
    expected_ext_fee = round(cost["unique_extended_parts"] * 3.00, 2)
    assert cost["extended_feeder_fee_usd"] == pytest.approx(expected_ext_fee, rel=1e-6)
    assert cost["total_pcba_cost_usd"] == pytest.approx(
        8.00 + 1.50 + expected_joint_fee + expected_ext_fee + cost["components_cost_usd"],
        abs=1e-4,
    )


def test_stackup_finish_state_machine() -> None:
    res_b1 = select_stackup_and_finish(BoardStackupRequirements())
    assert res_b1["selected_material_label"] == "FR4-MidTg (JLC04161H-7628)"
    assert res_b1["stackup"].id == "JLC04161H-7628"
    assert res_b1["stackup"].surface_finish == "ENIG"
    assert res_b1["stackup"].layers == 4

    # Flex requirement must select Polyimide-Flex
    res_flex = select_stackup_and_finish(
        BoardStackupRequirements(
            layers_required=2,
            controlled_impedance_required=False,
            flex_required=True,
        )
    )
    assert res_flex["selected_material_label"] == "Polyimide-Flex"

    # High-power MCPCB requirement must select Al-MCPCB
    res_mcpcb = select_stackup_and_finish(
        BoardStackupRequirements(
            layers_required=1,
            controlled_impedance_required=False,
            min_component_pitch_mm=1.27,
            high_power_mcpcb_required=True,
        )
    )
    assert res_mcpcb["selected_material_label"] == "Al-MCPCB"


def test_impedance_calibration_and_ampacity_physics() -> None:
    res_b1 = select_stackup_and_finish(BoardStackupRequirements())
    stackup = res_b1["stackup"]
    h_mil = stackup.prepreg_thickness_mm * MM_TO_MIL
    t_mil = stackup.outer_cu_oz * OZ_TO_MIL

    se = calc_microstrip_impedance(12.06, h_mil, t_mil, stackup.prepreg_er)
    assert 49.0 <= se["z0_masked_ohm"] <= 51.0
    assert 6.5 <= se["solder_mask_drop_ohm"] <= 8.5

    diff = calc_diff_pair_impedance(8.31, 5.0, h_mil, t_mil, stackup.prepreg_er)
    assert 88.5 <= diff["zdiff_masked_ohm"] <= 91.5
    assert diff["solder_mask_drop_ohm"] > 8.0

    # Inverse solvers match JLCPCB JLC04161H-7628 targets closely
    assert solve_microstrip_width_mil(50.0, stackup) == pytest.approx(12.06, abs=0.4)
    assert solve_diff_pair_width_mil(90.0, 5.0, stackup) == pytest.approx(8.31, abs=0.4)

    # IPC-2221 vs IPC-2152 (plane-coupled 4L board requires narrower trace than IPC-2221)
    w_2221 = calc_ipc2221_trace_width_mil(2.0, temp_rise_c=10.0, cu_oz=1.0)
    w_2152 = calc_ipc2152_trace_width_mil(2.0, temp_rise_c=10.0, cu_oz=1.0)
    assert 20.0 <= w_2221 <= 40.0
    assert 0.5 * w_2221 < w_2152 < w_2221

    # 0.30 mm via barrel current ~0.73-0.80 A @ dT=10C
    via = calc_via_barrel_ampacity_a(drill_mm=0.30, plating_um=20.32, temp_rise_c=10.0)
    assert 0.73 <= via["ampacity_a"] <= 0.80

    # Thermal via array drops R_theta dramatically vs single via
    t_via = calc_thermal_via_array_c_per_w(num_vias=9, drill_mm=0.30, plating_um=25.0)
    assert t_via["array_r_theta_c_per_w"] < t_via["single_via_r_theta_c_per_w"] / 5.0


def test_ai_trap_rejections_and_board1_passes() -> None:
    catalog = PartCatalog.load()
    parts = select_board1_bom(catalog, min_stock=101)
    placements = generate_board1_placements(parts)

    # Trap 1: 10uF 0402 16V X5R on 20V rail must fail MLCC DC-bias derating
    bad_cap = SelectedPart(
        refdes="C99",
        role="vbus_bad_bulk",
        lcsc_code="C15525",
        mpn="CL05A106MQ5NUNC",
        package="0402",
        value="10uF 16V X5R",
        jlcpcb_tier="Basic",
        stock=100000,
        unit_price_usd=0.02,
        rated_voltage_v=16.0,
        dielectric="X5R",
        effective_cap_ratio=estimate_effective_cap_ratio("X5R", 20.0, 16.0, "0402", 10.0),
    )
    mlcc_bad = check_mlcc_dc_bias([bad_cap], {"C99": 20.0})
    assert mlcc_bad["passed"] is False
    assert len(mlcc_bad["violations"]) == 1

    # Trap 2: X7R crystal timing cap must fail C0G/NP0 rule
    bad_xtal_cap = SelectedPart(
        refdes="C98",
        role="crystal_load_cap",
        lcsc_code="C14663",
        mpn="CC0603KRX7R9BB104",
        package="0603",
        value="100nF 50V X7R",
        jlcpcb_tier="Basic",
        stock=100000,
        unit_price_usd=0.01,
        rated_voltage_v=50.0,
        dielectric="X7R",
        effective_cap_ratio=0.98,
    )
    assert check_mlcc_dc_bias([bad_xtal_cap], {"C98": 1.8})["passed"] is False

    # Trap 3: LDO 5V->3.3V @ 300mA SOT-23-5 fails thermal (Tj > 125C), while post-LDO 3.3V->2.5V @ 40mA passes (~32mW)
    trap_ldo = check_ldo_vs_buck_thermal(
        RailSpec("TRAP", 5.0, 5.0, 5.0, 3.3, 0.30, ambient_temp_c=40.0),
        topology="LDO",
        package="SOT-23-5",
    )
    assert trap_ldo["passed"] is False
    assert trap_ldo["junction_temp_c"] > 125.0

    good_post_ldo = check_ldo_vs_buck_thermal(
        RailSpec("ANA_2V5", 3.2, 3.3, 3.3, 2.5, 0.04, ambient_temp_c=40.0, low_noise_required=True),
        topology="LDO",
        package="SOT-23-5",
    )
    assert good_post_ldo["passed"] is True
    assert 30.0 <= good_post_ldo["power_dissipation_mw"] <= 35.0
    assert good_post_ldo["junction_temp_c"] < 60.0

    # Trap 4: USB-C tied CC nets, ESD TVS > 5mm, or >0 via must fail
    assert check_usbc_and_esd_rules(parts, placements, esd_trace_via_count=0)["passed"] is True
    assert check_usbc_and_esd_rules(parts, placements, esd_trace_via_count=1)["passed"] is False
    assert check_usbc_and_esd_rules(parts, placements, cc_nets=("CC_SHARED", "CC_SHARED"))["passed"] is False


def test_ngspice_ac_imag_frequency_sanitizer() -> None:
    # Simulate ngspice 41 AC wrdata rows containing -1.8e199 and subnormal denormals in imaginary frequency col 2
    corrupted_wrdata = "\n".join(
        [
            " 1.00000000e+01  1.00000000e+01 -1.80000000e+199  1.00000000e+01  9.99960523e-01 -6.28293727e-03",
            " 1.00000000e+02  1.00000000e+02  4.94065646e-324  1.00000000e+02  9.96067682e-01 -6.25847783e-02",
            " 1.59154943e+03  1.59154943e+03 -1.80143985e+199  1.59154943e+03  5.00000000e-01 -5.00000000e-01",
            " 1.00000000e+04  1.00000000e+04  0.00000000e+00   1.00000000e+04  2.47045230e-02 -1.55223096e-01",
        ]
    )
    clean = sanitize_ngspice_ac_data(corrupted_wrdata, has_explicit_frequency_vec=True)
    assert clean["imag_column_masked"] is True
    assert clean["masked_imag_anomalies"] == 3
    assert clean["points_kept"] == 4
    fc = extract_corner_frequency_hz(clean["freq_hz"], clean["mag_v"])
    assert fc == pytest.approx(1591.55, rel=0.02)


def test_ngspice_simulations_and_cli_artifacts(tmp_path: Path) -> None:
    # 1. Power stage Buck + LDO transient
    p_res = run_buck_ldo_power_sim()
    assert p_res["passed"] is True
    assert 3.25 <= p_res["metrics"]["v3v3_steady_mean_v"] <= 3.35
    assert p_res["metrics"]["v3v3_steady_ripple_pp_mv"] < 35.0
    assert 2.48 <= p_res["metrics"]["v2v5_steady_mean_v"] <= 2.52
    assert p_res["metrics"]["v2v5_steady_ripple_pp_mv"] < 1.0

    # 2. ESD clamp + 1k series limiter
    esd_res = run_esd_clamp_sim()
    assert esd_res["passed"] is True
    assert esd_res["metrics"]["protected_1k_ic_clamp_peak_ma"] < 5.0
    assert esd_res["metrics"]["unprotected_1ohm_ic_clamp_peak_ma"] > 100.0

    # 3. AC filter Monte Carlo N=30 variance attribution
    mc_res = run_rc_filter_monte_carlo_sim(num_runs=30)
    assert mc_res["passed"] is True
    assert mc_res["nominal"]["nominal_error_pct"] < 0.1
    assert mc_res["monte_carlo_summary"]["variance_attribution_c_pct"] > 95.0
    assert mc_res["monte_carlo_summary"]["variance_attribution_r_pct"] < 5.0

    # 4. Full pipeline run to tmp_path & verify exported artifacts in artifacts/spec
    summary = run_pipeline(out_dir=tmp_path, board_qty=5, mc_runs=30)
    assert summary["dfm_all_safety_checks_passed"] is True
    assert summary["spice_power_passed"] is True
    assert summary["spice_esd_passed"] is True

    for fname in (
        "bom_verified.json",
        "bom_jlcpcb.csv",
        "cpl_jlcpcb.csv",
        "dfm_budget.json",
        "net_rules.json",
        "spice_power_results.json",
        "spice_esd_results.json",
    ):
        assert (tmp_path / fname).exists()
        assert (PROJECT_ROOT / "artifacts" / "spec" / fname).exists()

    # Validate JLCPCB CSV headers
    with (tmp_path / "bom_jlcpcb.csv").open(encoding="utf-8") as f:
        header = next(csv.reader(f))
        assert header == ["Designator", "Footprint", "Comment", "LCSC Part #"]
    with (tmp_path / "cpl_jlcpcb.csv").open(encoding="utf-8") as f:
        header = next(csv.reader(f))
        assert header == ["Designator", "Mid X", "Mid Y", "Layer", "Rotation"]

    net_rules = json.loads((tmp_path / "net_rules.json").read_text(encoding="utf-8"))
    assert len(net_rules) >= 8
    assert any(r["name"] == "USB_DP_DM_90DIFF" and r["impedance_ohm"] == 90.0 for r in net_rules)
