"""Parts selection, JLCPCB DFM physics, and ngspice verification for Cloud PCB Studio."""

from __future__ import annotations

from cloud_pcb.parts_dfm.catalog import (
    CatalogPart,
    PartCatalog,
    estimate_effective_cap_ratio,
    estimate_pcba_cost,
    export_bom_and_cpl,
    generate_board1_placements,
    select_board1_bom,
)
from cloud_pcb.parts_dfm.dfm_physics import (
    SUBSTRATE_PROFILES,
    SURFACE_FINISHES,
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
    run_board1_dfm_analysis,
    select_stackup_and_finish,
    solve_diff_pair_width_mil,
    solve_microstrip_width_mil,
)
from cloud_pcb.parts_dfm.spice_sim import (
    run_all_spice_verifications,
    run_buck_ldo_power_sim,
    run_esd_clamp_sim,
    run_rc_filter_monte_carlo_sim,
    sanitize_ngspice_ac_data,
)

__all__ = [
    "CatalogPart",
    "PartCatalog",
    "estimate_effective_cap_ratio",
    "estimate_pcba_cost",
    "export_bom_and_cpl",
    "generate_board1_placements",
    "select_board1_bom",
    "SUBSTRATE_PROFILES",
    "SURFACE_FINISHES",
    "build_board1_net_rules",
    "calc_diff_pair_impedance",
    "calc_ipc2152_trace_width_mil",
    "calc_ipc2221_trace_width_mil",
    "calc_microstrip_impedance",
    "calc_thermal_via_array_c_per_w",
    "calc_via_barrel_ampacity_a",
    "check_ldo_vs_buck_thermal",
    "check_mlcc_dc_bias",
    "check_usbc_and_esd_rules",
    "run_board1_dfm_analysis",
    "select_stackup_and_finish",
    "solve_diff_pair_width_mil",
    "solve_microstrip_width_mil",
    "run_all_spice_verifications",
    "run_buck_ldo_power_sim",
    "run_esd_clamp_sim",
    "run_rc_filter_monte_carlo_sim",
    "sanitize_ngspice_ac_data",
]
