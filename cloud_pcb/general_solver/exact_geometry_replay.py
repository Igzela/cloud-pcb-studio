"""Check-only FastRoute replay with strictly-circumscribed curved/rotated pad polygons."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from collections import Counter, defaultdict

from cloud_pcb.general_solver.direct_repair_replay import _fixed_wiring
from cloud_pcb.general_solver.router_logs import save_router_log
from cloud_pcb.layout_route.dsn_ses_bridge import board_to_dsn, parse_ses


def has_non_rectangular_pads(board: Any) -> bool:
    """Return True when the board contains any curved or non-90-degree rotated copper pad."""
    for fp in getattr(board, "footprints", []):
        for pad in getattr(fp, "pads", []):
            if pad.type == "np_thru_hole":
                continue
            shape = getattr(pad, "shape", "rect")
            angle = float(getattr(getattr(pad, "position", None), "angle", 0.0) or 0.0)
            if shape in ("oval", "roundrect"):
                return True
            if shape != "circle" and abs(angle % 90.0) > 1e-6:
                return True
    return False


def _validate_exact_replay(before: dict[str, Any], control: dict[str, Any], exact: dict[str, Any]) -> None:
    original, reproduced, verified = (report["stats"] for report in (before, control, exact))
    for key in ("nets", "pins", "connections", "vias", "unrouted"):
        if not (original[key] == reproduced[key] == verified[key]):
            raise ValueError(f"Exact-geometry replay changed router count {key!r}")
    if reproduced["violations"] != original["violations"]:
        raise ValueError("Exact-geometry control replay did not reproduce original violation count")
    if verified["violations"] > reproduced["violations"]:
        raise ValueError("Exact-geometry replay increased violation count")


def _verify_conservative_ses_copper(control_ses: Path, exact_ses: Path, *, max_width_inflation_mm: float = 0.01) -> None:
    """Verify exact SES preserves every via and segment path without ever narrowing copper.

    Outward +1 um polygon inflation on curved/rotated pads can cause FastRoute's pad-entry
    stub joiner to emit a conservatively wider (+2 um) stub at the exact same coordinates.
    """
    ctrl = parse_ses(control_ses)
    ex = parse_ses(exact_ses)
    ctrl_vias = Counter(tuple(sorted(v.items())) for v in ctrl["vias"])
    ex_vias = Counter(tuple(sorted(v.items())) for v in ex["vias"])
    if ctrl_vias != ex_vias:
        raise ValueError("Exact-geometry replay changed SES via geometry")

    def _by_path(segs: list[dict[str, Any]]) -> dict[tuple[Any, ...], list[float]]:
        grouped: dict[tuple[Any, ...], list[float]] = defaultdict(list)
        for seg in segs:
            key = (seg["layer"], seg["net"], seg["x1"], seg["y1"], seg["x2"], seg["y2"])
            grouped[key].append(float(seg["width_mm"]))
        for widths in grouped.values():
            widths.sort()
        return grouped

    ctrl_segs = _by_path(ctrl["segments"])
    ex_segs = _by_path(ex["segments"])
    if ctrl_segs.keys() != ex_segs.keys():
        raise ValueError("Exact-geometry replay changed SES segment endpoints, layers, or nets")
    for key, ctrl_widths in ctrl_segs.items():
        ex_widths = ex_segs[key]
        if len(ctrl_widths) != len(ex_widths):
            raise ValueError("Exact-geometry replay changed SES segment multiplicity")
        for cw, ew in zip(ctrl_widths, ex_widths):
            if ew < cw - 1e-6 or ew > cw + max_width_inflation_mm + 1e-6:
                raise ValueError(
                    f"Exact-geometry replay changed segment width outside conservative bound: {cw} -> {ew}"
                )


def replay_exact_pad_geometry(
    board: Any,
    control_dsn: Path,
    session: Path,
    original_report: dict[str, Any],
    command: list[str],
    project_root: Path,
    outdir: Path,
    router_processes: list[dict[str, Any]],
    *,
    dsn_export_kwargs: dict[str, Any],
    repairs: list[Any] | None = None,
) -> dict[str, Any]:
    """Re-verify a routed session using strictly-circumscribed pad polygons and snap_angle none."""
    directory = outdir / "exact_geometry_replay"
    directory.mkdir(exist_ok=True)
    exact_dsn = directory / "exact.dsn"
    board_to_dsn(
        board,
        exact_dsn,
        **dsn_export_kwargs,
        exact_pad_shapes=True,
        snap_angle="none",
    )
    if repairs:
        exact_dsn.write_text(_fixed_wiring(exact_dsn.read_text(encoding="utf-8"), repairs), encoding="utf-8")

    base = [
        arg for arg in command if not arg.startswith(("--report=", "--pairs=", "--tune=", "--multi-start="))
    ]
    base.extend([
        "--multi-start=1",
        f"--initial-session={session}",
        "--router.autorouter.enabled=false",
        "--router.fanout.enabled=false",
        "--router.optimizer.enabled=false",
    ])

    reports: list[dict[str, Any]] = []
    sessions: list[Path] = []
    for name, input_dsn in (("control", control_dsn), ("exact", exact_dsn)):
        ses = directory / f"{name}.ses"
        report_path = directory / f"{name}.json"
        ses.unlink(missing_ok=True)
        report_path.unlink(missing_ok=True)
        args = list(base)
        args[args.index("-de") + 1] = str(input_dsn)
        args[args.index("-do") + 1] = str(ses)
        args.append(f"--report={report_path}")
        log = directory / f"{name}.log"
        try:
            proc = subprocess.run(args, cwd=project_root, capture_output=True, text=True, timeout=30, check=False)
        except subprocess.TimeoutExpired as exc:
            record = save_router_log(log, args, -1, exc.stdout, exc.stderr, timed_out=True)
            record["log"] = str(log.relative_to(outdir))
            router_processes.append(record)
            raise ValueError(f"Exact-geometry {name} replay timed out") from exc
        record = save_router_log(log, args, proc.returncode, proc.stdout, proc.stderr)
        record["log"] = str(log.relative_to(outdir))
        router_processes.append(record)
        if proc.returncode:
            raise ValueError(f"Exact-geometry {name} replay failed")
        reports.append(json.loads(report_path.read_text(encoding="utf-8")))
        sessions.append(ses)

    _validate_exact_replay(original_report, reports[0], reports[1])
    _verify_conservative_ses_copper(sessions[0], sessions[1])
    return reports[1]
