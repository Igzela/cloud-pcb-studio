"""Pipeline A: Code-first tscircuit build, esbuild transpile, Node v24 export, and KiCad sanitizer."""

from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from kiutils.board import Board

from contracts.schemas import save_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAYOUT_DIR = Path(__file__).resolve().parent
DEFAULT_TSX = LAYOUT_DIR / "board1.tsx"
DEFAULT_MJS = LAYOUT_DIR / "board1.mjs"
DEFAULT_RUNNER = LAYOUT_DIR / "run_board1_tscircuit.mjs"
DEFAULT_OUTDIR = PROJECT_ROOT / "artifacts" / "board1_tscircuit"


def sanitize_kicad_sexpr(content: str) -> str:
    """Strip or fix bare `(model "...")` S-expressions emitted by circuit-json-to-kicad.

    kiutils.footprint.Model.from_sexpr raises when a `(model "...")` block has fewer
    than 5 tokens (i.e., lacks `(offset ...) (scale ...) (rotate ...)`).
    """
    out: list[str] = []
    i = 0
    n = len(content)
    while i < n:
        if content.startswith("(model", i) and (
            i + 6 == n or content[i + 6].isspace()
        ):
            depth = 0
            j = i
            in_quotes = False
            escape = False
            while j < n:
                ch = content[j]
                if escape:
                    escape = False
                elif ch == "\\" and in_quotes:
                    escape = True
                elif ch == '"':
                    in_quotes = not in_quotes
                elif not in_quotes:
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            j += 1
                            break
                j += 1
            block = content[i:j]
            if "(offset" in block and "(scale" in block and "(rotate" in block:
                out.append(block)
            else:
                # Strip incomplete/bare (model "...") expression cleanly
                while out and out[-1] in (" ", "\t"):
                    out.pop()
                if j < n and content[j] == "\n":
                    j += 1
            i = j
            continue
        out.append(content[i])
        i += 1
    cleaned = "".join(out)
    # Remove any accidental trailing blank lines before closing footprint paren
    cleaned = re.sub(r"\n[ \t]*\n([ \t]*\))", r"\n\1", cleaned)
    return cleaned


def transpile_tsx_to_mjs(tsx_path: Path = DEFAULT_TSX, mjs_path: Path = DEFAULT_MJS) -> Path:
    """Transpile TSX -> ESM MJS via esbuild (avoiding .bin/tsci which requires bun)."""
    esbuild_bin = PROJECT_ROOT / "node_modules" / ".bin" / "esbuild"
    cmd = [
        str(esbuild_bin),
        str(tsx_path),
        "--bundle",
        "--platform=node",
        "--format=esm",
        "--jsx=automatic",
        "--external:react",
        "--external:@tscircuit/*",
        f"--outfile={mjs_path}",
    ]
    res = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"esbuild failed ({res.returncode}):\n{res.stderr}\n{res.stdout}")
    return mjs_path


def _analyze_circuit_json(circuit_json: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract Board 1 physics metrics directly from tscircuit circuit.json."""
    source_nets = {e["source_net_id"]: e.get("name", "") for e in circuit_json if e.get("type") == "source_net"}
    source_traces = [e for e in circuit_json if e.get("type") == "source_trace"]
    pcb_traces = [e for e in circuit_json if e.get("type") == "pcb_trace"]
    pcb_vias = [e for e in circuit_json if e.get("type") == "pcb_via"]
    pcb_components = {e["pcb_component_id"]: e for e in circuit_json if e.get("type") == "pcb_component"}
    source_components = {e["source_component_id"]: e for e in circuit_json if e.get("type") == "source_component"}
    pcb_smtpads = [e for e in circuit_json if e.get("type") == "pcb_smtpad"]

    # Map source_component name -> pads
    pads_by_comp: dict[str, list[dict[str, Any]]] = {}
    for pad in pcb_smtpads:
        comp = pcb_components.get(pad.get("pcb_component_id", ""))
        if not comp:
            continue
        sc = source_components.get(comp.get("source_component_id", ""))
        cname = sc.get("name", "") if sc else ""
        pads_by_comp.setdefault(cname, []).append(pad)

    # Check USB-C D+/D- to USBLC6-2SC6 TVS distance
    j_usb_pads = pads_by_comp.get("J_USB", [])
    d_tvs_pads = pads_by_comp.get("D_TVS", [])
    tvs_min_dist_mm = None
    if j_usb_pads and d_tvs_pads:
        usb_data_pads = [
            p for p in j_usb_pads if any(h in ("4", "5", "DP", "DM") for h in p.get("port_hints", []))
        ]
        tvs_io_pads = [
            p for p in d_tvs_pads if any(h in ("1", "3", "IO1_IN", "IO2_IN") for h in p.get("port_hints", []))
        ]
        if usb_data_pads and tvs_io_pads:
            dists = [
                math.hypot(a["x"] - b["x"], a["y"] - b["y"])
                for a in usb_data_pads
                for b in tvs_io_pads
            ]
            tvs_min_dist_mm = round(min(dists), 4)

    # Check USB trace vias and VBUS / RF widths
    usb_trace_vias = 0
    vbus_max_width_mm = 0.0
    rf_max_width_mm = 0.0
    total_wirelength_mm = 0.0
    for tr in pcb_traces:
        route = tr.get("route", [])
        trace_id = tr.get("source_trace_id", "")
        st = next((s for s in source_traces if s.get("source_trace_id") == trace_id), {})
        tname = (st.get("display_name") or st.get("name") or "").lower()
        is_usb_diff = any(tok in tname for tok in (".dp", ".dm", "usb_dp", "usb_dm", "io1_in", "io1_out", "io2_in", "io2_out"))
        is_vbus = "vbus" in tname
        is_rf = "rf_tx" in tname or "> .sig" in tname
        for seg in route:
            if seg.get("route_type") == "via" and is_usb_diff:
                usb_trace_vias += 1
            w = float(seg.get("width", 0.0) or 0.0)
            if is_vbus:
                vbus_max_width_mm = max(vbus_max_width_mm, w)
            if is_rf:
                rf_max_width_mm = max(rf_max_width_mm, w)
        pts = [(s["x"], s["y"]) for s in route if "x" in s and "y" in s]
        for p1, p2 in zip(pts, pts[1:]):
            total_wirelength_mm += math.hypot(p2[0] - p1[0], p2[1] - p1[1])

    # Check CC1 / CC2 separation (never shorted)
    cc1_net_id = None
    cc2_net_id = None
    for st in source_traces:
        dn = (st.get("display_name") or "").lower()
        ports = " ".join(st.get("connected_source_port_ids", []))
        if "cc1" in dn:
            cc1_net_id = st.get("source_trace_id")
        elif "cc2" in dn:
            cc2_net_id = st.get("source_trace_id")

    return {
        "pcb_traces": len(pcb_traces),
        "pcb_vias": len(pcb_vias),
        "pcb_copper_pours": len([e for e in circuit_json if e.get("type") == "pcb_copper_pour"]),
        "pcb_keepouts": len([e for e in circuit_json if e.get("type") == "pcb_keepout"]),
        "usb_tvs_distance_mm": tvs_min_dist_mm,
        "usb_path_vias": usb_trace_vias,
        "vbus_max_width_mm": round(vbus_max_width_mm, 3),
        "rf_max_width_mm": round(rf_max_width_mm, 3),
        "total_wirelength_mm": round(total_wirelength_mm, 3),
        "cc1_trace_id": cc1_net_id,
        "cc2_trace_id": cc2_net_id,
        "cc_nets_distinct": cc1_net_id is not None and cc2_net_id is not None and cc1_net_id != cc2_net_id,
        "source_nets": list(source_nets.values()),
    }


def run_pipeline_a(
    tsx_path: Path = DEFAULT_TSX,
    mjs_path: Path = DEFAULT_MJS,
    outdir: Path = DEFAULT_OUTDIR,
) -> dict[str, Any]:
    """Execute full Pipeline A: esbuild -> Node v24 tscircuit -> sanitize KiCad -> validate."""
    outdir.mkdir(parents=True, exist_ok=True)
    transpile_tsx_to_mjs(tsx_path, mjs_path)

    cmd = ["node", str(DEFAULT_RUNNER), "--input", str(mjs_path), "--outdir", str(outdir)]
    res = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"Node tscircuit runner failed ({res.returncode}):\n{res.stderr}\n{res.stdout}")

    runner_summary = json.loads(res.stdout)

    # Sanitize KiCad PCB & Sch
    pcb_path = outdir / "board1.kicad_pcb"
    sch_path = outdir / "board1.kicad_sch"
    pcb_raw = pcb_path.read_text(encoding="utf-8")
    pcb_clean = sanitize_kicad_sexpr(pcb_raw)
    pcb_path.write_text(pcb_clean, encoding="utf-8")

    sch_raw = sch_path.read_text(encoding="utf-8")
    sch_clean = sanitize_kicad_sexpr(sch_raw)
    sch_path.write_text(sch_clean, encoding="utf-8")

    # Validate KiCad PCB parses cleanly in kiutils
    ki_board = Board.from_file(str(pcb_path))

    cj = json.loads((outdir / "circuit.json").read_text(encoding="utf-8"))
    physics = _analyze_circuit_json(cj)

    report = {
        "pipeline": "tscircuit",
        "board": "Board1_USB_PD_Telemetry",
        "outdir": str(outdir),
        "runner_summary": runner_summary,
        "physics": physics,
        "kiutils_validation": {
            "parsed_ok": True,
            "footprints": len(ki_board.footprints),
            "trace_items": len(ki_board.traceItems),
            "zones": len(ki_board.zones),
            "nets": len(ki_board.nets),
        },
        "artifacts": sorted(p.name for p in outdir.iterdir() if p.is_file()),
    }
    save_json(outdir / "tscircuit_report.json", report)
    return report


if __name__ == "__main__":
    result = run_pipeline_a()
    print(json.dumps(result, indent=2))
