"""End-to-end verification gatekeeper orchestrating Gerber/Excellon/LVS/Spec audits, rendering, and Chromium inspection."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from contracts.schemas import GateReport, VerifyTicket, save_json
from cloud_pcb.verify_gate.gerber_drc import (
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_verify_gate(artifacts_root: Path | None = None) -> dict[str, Any]:
    """Run all independent verification audits, renders, Workbench HTML generation, and Chromium checks."""
    root = Path(artifacts_root) if artifacts_root is not None else PROJECT_ROOT / "artifacts"
    verify_dir = root / "verify"
    renders_dir = verify_dir / "renders"
    verify_dir.mkdir(parents=True, exist_ok=True)

    reports: list[GateReport] = [
        audit_board1_spec(root / "spec"),
        audit_board1_pipeline_b(root / "board1_kicad_fastroute"),
        audit_board1_pipeline_a(root / "board1_tscircuit"),
        audit_board2_special(root / "board2_special"),
    ]

    render_results = render_all_board_previews(root, renders_dir)

    all_tickets: list[VerifyTicket] = []
    for rep in reports:
        all_tickets.extend(rep.tickets)

    for key, info in render_results.items():
        if not info.get("non_blank"):
            all_tickets.append(
                VerifyTicket(
                    ticket_id=f"VISUAL-{key.upper()}",
                    severity="HIGH",
                    owner_worker="verify_gate",
                    board=key,
                    gate="gerber_composite_render",
                    message=f"Rendered preview {key} failed non-blank pixel variance check",
                    measured_value=f"std={info.get('pixel_std')}",
                    required_value="std > 5.0, >= 400x200",
                    artifact_path=str(info.get("path", "")),
                )
            )

    total_checks_run = sum(r.checks_run for r in reports)
    total_checks_passed = sum(r.checks_passed for r in reports)
    crit_count = sum(1 for t in all_tickets if t.severity == "CRITICAL")
    high_count = sum(1 for t in all_tickets if t.severity == "HIGH")

    pre_summary: dict[str, Any] = {
        "overall_passed": (crit_count == 0 and high_count == 0),
        "total_checks_run": total_checks_run,
        "total_checks_passed": total_checks_passed,
        "critical_count": crit_count,
        "high_count": high_count,
        "reports": [asdict(r) for r in reports],
        "renders": render_results,
        "tickets": [asdict(t) for t in all_tickets],
    }

    workbench_path = verify_dir / "workbench.html"
    generate_workbench_html(root, pre_summary, workbench_path)

    chromium_res = inspect_workbench_chromium(workbench_path, verify_dir / "workbench_screenshot.png")
    if not chromium_res.get("passed"):
        all_tickets.append(
            VerifyTicket(
                ticket_id="CHROMIUM-001",
                severity="HIGH",
                owner_worker="verify_gate",
                board="workbench_ui",
                gate="chromium_headless_inspect",
                message=f"Chromium headless inspection failed: errors={chromium_res.get('console_errors')}",
                measured_value=f"dom={chromium_res.get('dom_length')}, std={chromium_res.get('screenshot_pixel_std')}",
                required_value="dom>2000, console_errors=0, std>10.0",
                artifact_path=str(workbench_path),
            )
        )
        # Regenerate workbench if a chromium ticket was added
        pre_summary["tickets"] = [asdict(t) for t in all_tickets]
        pre_summary["high_count"] = sum(1 for t in all_tickets if t.severity == "HIGH")
        pre_summary["overall_passed"] = (crit_count == 0 and pre_summary["high_count"] == 0)
        generate_workbench_html(root, pre_summary, workbench_path)

    pre_summary["chromium_inspect"] = chromium_res
    pre_summary["workbench_html"] = str(workbench_path)

    save_json(verify_dir / "gate_report.json", pre_summary)
    save_json(verify_dir / "defect_tickets.json", [asdict(t) for t in all_tickets])
    save_json(verify_dir / "chromium_inspect.json", chromium_res)
    return pre_summary
