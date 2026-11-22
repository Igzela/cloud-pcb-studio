"""Obtain fresh fastroute evidence for proposed fixed inter-pad traces."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path

from cloud_pcb.general_solver.router_logs import save_router_log
from cloud_pcb.layout_route.dsn_ses_bridge import _quote, parse_ses


def _fixed_wiring(dsn_text, repairs):
    wiring_open = "  (wiring\n"
    if dsn_text.count(wiring_open) != 1:
        raise ValueError("Expected a single DSN wiring section")
    start = dsn_text.index(wiring_open)
    body_start = start + len(wiring_open)
    wiring_close = "  )"
    close_offset = None
    offset = body_start
    for line in dsn_text[body_start:].splitlines(keepends=True):
        if line.rstrip("\r\n") == wiring_close:
            close_offset = offset
            break
        offset += len(line)
    if close_offset is None:
        raise ValueError("Expected a single closed DSN wiring section")
    if dsn_text[close_offset + len(wiring_close):].strip() != ")":
        raise ValueError("Expected DSN wiring section at the end of the DSN")
    wires = []
    for repair in repairs:
        width = round(1000 * repair.width_mm)
        x1, y1 = repair.start_xy
        x2, y2 = repair.end_xy
        points = f"{round(1000 * x1)} {round(-1000 * y1)} {round(1000 * x2)} {round(-1000 * y2)}"
        wires.append(f"    (wire (path {repair.layer} {width} {points}) (net {_quote(repair.net)}) (type fix))")
    insertion = "".join(f"{wire}\n" for wire in wires)
    return dsn_text[:close_offset] + insertion + dsn_text[close_offset:]


def _copper_multisets(ses_path):
    session = parse_ses(ses_path)
    return tuple(
        Counter(tuple(sorted(item.items())) for item in session[item_kind])
        for item_kind in ("segments", "vias")
    )


def _unchanged_replay(before, control, trial, link_count):
    original, reproduced, proposed = (report["stats"] for report in (before, control, trial))
    if not all(original[key] == reproduced[key] == proposed[key] for key in ("nets", "pins", "connections", "vias")):
        raise ValueError("Replay changed the router's network, pin, connection or via counts")
    if reproduced["unrouted"] != original["unrouted"]:
        raise ValueError("Replay did not reproduce the original unrouted count")
    if not 0 < reproduced["unrouted"] - proposed["unrouted"] <= link_count:
        raise ValueError("Replay did not show the expected connectivity gain")
    if (
        reproduced["violations"] != original["violations"]
        or proposed["violations"] > reproduced["violations"]
        or any(violation not in control["clearance_violations"] for violation in trial["clearance_violations"])
    ):
        raise ValueError("Replay introduced a clearance violation or failed to reproduce counts")


def replay_fixed_wires(dsn, session, original_report, repairs, command, project_root, outdir, router_processes):
    """Compare matched check-only runs using the selected session and same rules."""
    directory = outdir / "direct_repair_replay"
    directory.mkdir(exist_ok=True)
    trial_dsn = directory / "fixed.dsn"
    trial_dsn.write_text(_fixed_wiring(dsn.read_text(encoding="utf-8"), repairs), encoding="utf-8")
    base = [
        arg for arg in command if not arg.startswith(("--report=", "--pairs=", "--tune=", "--multi-start="))
    ]
    base.extend([
        "--multi-start=1", f"--initial-session={session}", "--router.autorouter.enabled=false",
        "--router.fanout.enabled=false", "--router.optimizer.enabled=false",
    ])
    reports = []
    sessions = []
    for name, input_dsn in (("control", dsn), ("fixed", trial_dsn)):
        ses = directory / f"{name}.ses"
        report = directory / f"{name}.json"
        ses.unlink(missing_ok=True)
        report.unlink(missing_ok=True)
        args = list(base)
        args[args.index("-de") + 1] = str(input_dsn)
        args[args.index("-do") + 1] = str(ses)
        args.append(f"--report={report}")
        log = directory / f"{name}.log"
        try:
            proc = subprocess.run(args, cwd=project_root, capture_output=True, text=True, timeout=20, check=False)
        except subprocess.TimeoutExpired as exc:
            record = save_router_log(log, args, -1, exc.stdout, exc.stderr, timed_out=True)
            record["log"] = str(log.relative_to(outdir))
            router_processes.append(record)
            raise ValueError(f"Direct-repair {name} replay timed out") from exc
        record = save_router_log(log, args, proc.returncode, proc.stdout, proc.stderr)
        record["log"] = str(log.relative_to(outdir))
        router_processes.append(record)
        if proc.returncode:
            raise ValueError(f"Direct-repair {name} replay failed")
        reports.append(json.loads(report.read_text(encoding="utf-8")))
        sessions.append(ses)
    _unchanged_replay(original_report, *reports, len(repairs))
    if _copper_multisets(sessions[0]) != _copper_multisets(sessions[1]):
        raise ValueError("The replay changed existing SES copper geometry")
    return reports[1]
