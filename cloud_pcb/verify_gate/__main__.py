"""CLI entry point for `python -m cloud_pcb.verify_gate`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cloud_pcb.verify_gate.gatekeeper import run_verify_gate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Cloud PCB Studio independent verification gate.")
    parser.add_argument("--artifacts-root", type=Path, default=None, help="Path to artifacts/ root")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any CRITICAL or HIGH VerifyTicket is raised",
    )
    args = parser.parse_args(argv)

    summary = run_verify_gate(artifacts_root=args.artifacts_root)
    print(
        json.dumps(
            {
                "overall_passed": summary["overall_passed"],
                "total_checks_run": summary["total_checks_run"],
                "total_checks_passed": summary["total_checks_passed"],
                "critical_count": summary["critical_count"],
                "high_count": summary["high_count"],
                "ticket_count": len(summary["tickets"]),
                "chromium_passed": summary["chromium_inspect"]["passed"],
                "workbench_html": summary["workbench_html"],
            },
            indent=2,
        )
    )
    if args.strict and not summary["overall_passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
