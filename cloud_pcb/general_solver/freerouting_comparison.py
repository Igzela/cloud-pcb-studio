"""Validate the secondary engine's output before using it as evidence."""

from __future__ import annotations

import math
import re
from pathlib import Path

from cloud_pcb.layout_route.dsn_ses_bridge import parse_ses


def completed_session(path: Path, returncode: int, log: str, allowed_nets: set[str]):
    """Return parsed copper and the engine's reported counts, or a precise failure.

    Freerouting can exit or leave a session file after an unsuccessful job.
    The score, explicit completion and readable copper are all required.
    """
    if returncode != 0:
        raise ValueError(f"Freerouting process did not finish successfully (rc={returncode})")
    if not re.search(r"\bfinished with state:\s+COMPLETED\b", log):
        raise ValueError("missing Freerouting COMPLETED job state")
    scores = re.findall(
        r"final score:[^\n]*?\((\d+) unrouted(?:\s+and\s+|,\s+)(\d+) violations\)",
        log,
    )
    if not scores:
        raise ValueError("missing Freerouting final routing score")
    if re.search(r"Network\.insert_component: component package not found", log):
        raise ValueError("Freerouting discarded a DSN component")
    parsed = parse_ses(path)
    if set(parsed["routed_net_names"]) - allowed_nets:
        raise ValueError("Freerouting session contains foreign nets")
    for segment in parsed["segments"]:
        if segment["layer"] not in {"F.Cu", "In1.Cu", "In2.Cu", "B.Cu"}:
            raise ValueError("Freerouting session contains an unknown copper layer")
        if segment["width_mm"] <= 0 or not all(
            math.isfinite(segment[key]) for key in ("width_mm", "x1", "y1", "x2", "y2")
        ):
            raise ValueError("Freerouting session contains invalid segment geometry")
    for via in parsed["vias"]:
        if not all(math.isfinite(via[key]) for key in ("x", "y", "pad_mm", "drill_mm")) or not (
            via["pad_mm"] > via["drill_mm"] > 0
        ):
            raise ValueError("Freerouting session contains invalid via geometry")
    unrouted, violations = map(int, scores[-1])
    return parsed, {"engine_unrouted_connections": unrouted, "engine_clearance_violations": violations}
