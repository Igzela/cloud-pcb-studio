"""CLI entry point for cloud_pcb.parts_dfm pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cloud_pcb.parts_dfm.catalog import (
    PROJECT_ROOT,
    PartCatalog,
    estimate_pcba_cost,
    export_bom_and_cpl,
    generate_board1_placements,
    select_board1_bom,
)
from cloud_pcb.parts_dfm.dfm_physics import run_board1_dfm_analysis
from cloud_pcb.parts_dfm.spice_sim import run_all_spice_verifications


def run_pipeline(
    out_dir: str | Path = PROJECT_ROOT / "artifacts" / "spec",
    board_qty: int = 5,
    enrich_live: bool = False,
    mc_runs: int = 30,
) -> dict[str, object]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    catalog = PartCatalog.load(enrich_live=enrich_live)
    parts = select_board1_bom(catalog, min_stock=101)
    placements = generate_board1_placements(parts)
    cost = estimate_pcba_cost(parts, board_qty=board_qty)
    bom_paths = export_bom_and_cpl(parts, placements, cost, out_dir=out)

    dfm_budget = run_board1_dfm_analysis(parts, placements, out_dir=out)
    spice_res = run_all_spice_verifications(out_dir=out, mc_runs=mc_runs)

    return {
        "out_dir": str(out),
        "artifacts": {
            **{k: str(v) for k, v in bom_paths.items()},
            "dfm_budget_json": str(out / "dfm_budget.json"),
            "net_rules_json": str(out / "net_rules.json"),
            "spice_power_results_json": str(out / "spice_power_results.json"),
            "spice_esd_results_json": str(out / "spice_esd_results.json"),
        },
        "bom_summary": {
            "total_placements": cost["total_placements_per_board"],
            "unique_parts": cost["unique_parts"],
            "unique_extended_parts": cost["unique_extended_parts"],
            "basic_preferred_placement_ratio": cost["basic_preferred_placement_ratio"],
            "total_pcba_cost_usd": cost["total_pcba_cost_usd"],
            "per_board_cost_usd": cost["per_board_cost_usd"],
        },
        "dfm_all_safety_checks_passed": dfm_budget["all_safety_checks_passed"],
        "spice_power_passed": spice_res["spice_power_results"]["all_passed"],
        "spice_esd_passed": spice_res["spice_esd_results"]["all_passed"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cloud PCB Studio Parts/DFM/SPICE Pipeline")
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "artifacts" / "spec"), help="Output directory for spec artifacts")
    parser.add_argument("--qty", type=int, default=5, help="Board quantity for PCBA cost estimation")
    parser.add_argument("--enrich-live", action="store_true", help="Attempt live jlcsearch enrichment with offline fallback")
    parser.add_argument("--mc-runs", type=int, default=30, help="Monte Carlo runs for AC filter simulation")
    args = parser.parse_args(argv)

    summary = run_pipeline(
        out_dir=args.out_dir,
        board_qty=args.qty,
        enrich_live=args.enrich_live,
        mc_runs=args.mc_runs,
    )
    print(json.dumps(summary, indent=2))
    ok = (
        bool(summary["dfm_all_safety_checks_passed"])
        and bool(summary["spice_power_passed"])
        and bool(summary["spice_esd_passed"])
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
