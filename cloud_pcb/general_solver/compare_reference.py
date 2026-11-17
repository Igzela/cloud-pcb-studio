"""Quantitative & visual side-by-side comparison between Blind Output and Human Reference."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle
from kiutils.board import Board
from kiutils.items.brditems import Arc, Segment, Via

from cloud_pcb.layout_route.dsn_ses_bridge import _rotate_kicad
from cloud_pcb.verify_gate.gerber_drc import _check_kicad_drc_and_lvs

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BLIND_PCB = PROJECT_ROOT / "artifacts" / "blind_benchmark" / "routed.kicad_pcb"
REF_PCB = PROJECT_ROOT / "benchmark_case" / "sealed_reference" / "can_sensor_board_kicad" / "Sensor_Board.kicad_pcb"
SPEC_PATH = PROJECT_ROOT / "benchmark_case" / "blind_spec.json"
OUT_JSON = PROJECT_ROOT / "artifacts" / "blind_benchmark" / "comparison_report.json"
OUT_PNG = PROJECT_ROOT / "artifacts" / "blind_benchmark" / "comparison_side_by_side.png"


def _is_gnd(name: str) -> bool:
    return (name or "").upper() in {"GND", "AGND", "DGND", "PGND", "VSS"}


def _board_bounds(board: Board) -> tuple[float, float, float, float]:
    """Infer (min_x, min_y, W, H) from Edge.Cuts graphic items."""
    xs: list[float] = []
    ys: list[float] = []
    for gi in board.graphicItems:
        if getattr(gi, "layer", None) == "Edge.Cuts":
            for attr in ("start", "end", "mid"):
                p = getattr(gi, attr, None)
                if p is not None and p.X is not None and p.Y is not None:
                    xs.append(float(p.X))
                    ys.append(float(p.Y))
            pts = getattr(gi, "coordinates", None)
            if pts:
                for p in pts:
                    if p.X is not None and p.Y is not None:
                        xs.append(float(p.X))
                        ys.append(float(p.Y))
    if not xs or not ys:
        return (0.0, 0.0, 60.0, 50.0)
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    return (minx, miny, max(maxx - minx, 1.0), max(maxy - miny, 1.0))


def _pad_world_xy(fp: Any, pad: Any, ox: float, oy: float) -> tuple[float, float]:
    fx = float(fp.position.X) - ox
    fy = float(fp.position.Y) - oy
    ang = float(fp.position.angle or 0.0)
    dx, dy = _rotate_kicad(float(pad.position.X), float(pad.position.Y), ang)
    return (fx + dx, fy + dy)


def _extract_metrics(
    pcb_path: Path,
    name: str,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    board = Board.from_file(str(pcb_path))
    ox, oy, bw, bh = _board_bounds(board)
    net_names = {int(n.number): str(n.name) for n in board.nets}

    fp_by_ref: dict[str, Any] = {}
    for fp in board.footprints:
        ref = ""
        if isinstance(fp.properties, dict):
            ref = str(fp.properties.get("Reference", ""))
        if not ref:
            for gi in fp.graphicItems:
                if getattr(gi, "type", "") == "reference":
                    ref = str(getattr(gi, "text", ""))
                    break
        if ref:
            fp_by_ref[ref] = fp

    segs = [t for t in board.traceItems if isinstance(t, Segment)]
    arcs = [t for t in board.traceItems if isinstance(t, Arc)]
    vias = [t for t in board.traceItems if isinstance(t, Via)]

    seg_by_layer: dict[str, int] = Counter(s.layer for s in segs)
    len_by_layer: dict[str, float] = defaultdict(float)
    width_hist: dict[str, int] = Counter()
    total_len = 0.0
    net_len: dict[str, float] = defaultdict(float)

    for s in segs:
        dl = math.hypot(float(s.end.X) - float(s.start.X), float(s.end.Y) - float(s.start.Y))
        total_len += dl
        len_by_layer[s.layer] += dl
        width_hist[f"{float(s.width):.3f}"] += 1
        nn = net_names.get(int(s.net), f"NET_{s.net}")
        net_len[nn] += dl

    for a in arcs:
        p1 = (float(a.start.X), float(a.start.Y))
        pm = (float(a.mid.X), float(a.mid.Y))
        p2 = (float(a.end.X), float(a.end.Y))
        dl = math.hypot(pm[0] - p1[0], pm[1] - p1[1]) + math.hypot(p2[0] - pm[0], p2[1] - pm[1])
        total_len += dl
        len_by_layer[a.layer] += dl
        width_hist[f"{float(a.width):.3f}"] += 1
        nn = net_names.get(int(a.net), f"NET_{a.net}")
        net_len[nn] += dl

    gnd_vias = 0
    sig_vias = 0
    via_sizes: Counter[str] = Counter()
    for v in vias:
        nn = net_names.get(int(v.net), "")
        if _is_gnd(nn):
            gnd_vias += 1
        else:
            sig_vias += 1
        via_sizes[f"{float(v.size):.2f}/{float(v.drill):.2f}"] += 1

    zone_layers: list[str] = []
    zone_nets: list[str] = []
    for z in board.zones:
        nn = net_names.get(int(z.net), str(z.net))
        zone_nets.append(nn)
        zone_layers.extend(list(z.layers or []))

    def _cap_to_ic_supply_dist(cap_ref: str, ic_refs: list[str]) -> dict[str, Any]:
        if cap_ref not in fp_by_ref:
            return {}
        cfp = fp_by_ref[cap_ref]
        cap_rails = {p.net.name for p in cfp.pads if p.net and p.net.name and not _is_gnd(p.net.name)}
        best_d = float("inf")
        best_target = ""
        for p in cfp.pads:
            if not p.net or not p.net.name or _is_gnd(p.net.name):
                continue
            cx, cy = _pad_world_xy(cfp, p, ox, oy)
            for iref in ic_refs:
                if iref not in fp_by_ref:
                    continue
                ifp = fp_by_ref[iref]
                for ip in ifp.pads:
                    if ip.net and ip.net.name == p.net.name:
                        ix, iy = _pad_world_xy(ifp, ip, ox, oy)
                        d = math.hypot(cx - ix, cy - iy)
                        if d < best_d:
                            best_d = d
                            best_target = f"{iref}.{ip.number}({p.net.name})"
        return {
            "rails": sorted(cap_rails),
            "nearest_ic_pin": best_target,
            "dist_mm": round(best_d, 3) if math.isfinite(best_d) else None,
        }

    # Auto-detect bypass caps (2-pin caps touching GND + another rail) and ICs (>=3 pads)
    ic_refs = [
        r for r, fp in fp_by_ref.items()
        if (r.startswith(("U", "IC")) or (len(fp.pads) >= 4 and not r.startswith(("J", "H"))))
        and not r.startswith("H")
    ]
    cap_refs: list[str] = []
    for r, fp in sorted(fp_by_ref.items()):
        if r.startswith("C") and len(fp.pads) == 2:
            pnets = [p.net.name for p in fp.pads if p.net and p.net.name]
            if any(_is_gnd(n) for n in pnets) and any(not _is_gnd(n) for n in pnets):
                cap_refs.append(r)

    cap_dists = {cr: _cap_to_ic_supply_dist(cr, ic_refs) for cr in cap_refs}
    valid_cap_d = [v["dist_mm"] for v in cap_dists.values() if v.get("dist_mm") is not None]

    # Header/connector edge alignment using auto-detected board bounds
    edge_conn_refs = {ec["refdes"] for ec in (spec or {}).get("edge_connectors", [])}
    header_refs = [
        r for r, fp in fp_by_ref.items()
        if (r.startswith("J") or "header" in str(fp.entryName or "").lower() or "pinsocket" in str(fp.entryName or "").lower())
        and r not in edge_conn_refs
        and not r.startswith("H")
    ]
    header_edge_dists: dict[str, float] = {}
    for hr in sorted(header_refs):
        fp = fp_by_ref[hr]
        hx = float(fp.position.X) - ox
        hy = float(fp.position.Y) - oy
        ed = min(hx, bw - hx, hy, bh - hy)
        header_edge_dists[hr] = round(ed, 2)

    # Differential pair analysis (spec-driven or fallback CAN)
    diff_pairs = list((spec or {}).get("design_requirements", {}).get("differential_pairs", []))
    if not diff_pairs and ("CAN_H" in net_len or "CAN_L" in net_len):
        diff_pairs = [{"pos_net": "CAN_H", "neg_net": "CAN_L"}]
    diff_metrics: list[dict[str, Any]] = []
    for dp in diff_pairs:
        pn, nn = dp["pos_net"], dp["neg_net"]
        pl = round(net_len.get(pn, 0.0), 3)
        nl = round(net_len.get(nn, 0.0), 3)
        diff_metrics.append({
            "pos_net": pn,
            "neg_net": nn,
            "pos_len_mm": pl,
            "neg_len_mm": nl,
            "length_mismatch_mm": round(abs(pl - nl), 3),
        })

    # Footprint side breakdown (F.Cu vs B.Cu)
    fp_sides = Counter(str(fp.layer or "F.Cu") for fp in board.footprints)
    drc_lvs = _check_kicad_drc_and_lvs(pcb_path)

    return {
        "label": name,
        "pcb_path": str(pcb_path),
        "board_dims_mm": [round(bw, 3), round(bh, 3)],
        "origin_shift_mm": [round(ox, 3), round(oy, 3)],
        "footprint_sides": dict(fp_sides),
        "copper_layers_in_stackup": [l.name for l in board.layers if l.type in ("signal", "power", "mixed")],
        "zones_count": len(board.zones),
        "zone_layers": sorted(set(zone_layers)),
        "zone_nets": sorted(set(zone_nets)),
        "segments_count": len(segs),
        "arcs_count": len(arcs),
        "total_wirelength_mm": round(total_len, 3),
        "wirelength_by_layer_mm": {k: round(v, 3) for k, v in sorted(len_by_layer.items())},
        "segments_by_layer": dict(sorted(seg_by_layer.items())),
        "trace_width_histogram": dict(sorted(width_hist.items())),
        "vias_total": len(vias),
        "vias_signal": sig_vias,
        "vias_gnd": gnd_vias,
        "via_sizes": dict(via_sizes),
        "diff_pairs": diff_metrics,
        "decoupling_cap_distances": cap_dists,
        "avg_decoupling_dist_mm": round(sum(valid_cap_d) / len(valid_cap_d), 3) if valid_cap_d else None,
        "max_decoupling_dist_mm": round(max(valid_cap_d), 3) if valid_cap_d else None,
        "header_edge_distances_mm": header_edge_dists,
        "avg_header_edge_dist_mm": round(sum(header_edge_dists.values()) / len(header_edge_dists), 2) if header_edge_dists else None,
        "drc_lvs": drc_lvs,
    }


def _plot_board_ax(ax: Any, pcb_path: Path, title: str) -> None:
    board = Board.from_file(str(pcb_path))
    ox, oy, bw, bh = _board_bounds(board)

    ax.set_facecolor("#0f172a")
    for gi in board.graphicItems:
        if getattr(gi, "layer", None) == "Edge.Cuts":
            if hasattr(gi, "start") and hasattr(gi, "end"):
                if gi.__class__.__name__ == "GrRect":
                    x0, y0 = float(gi.start.X) - ox, float(gi.start.Y) - oy
                    x1, y1 = float(gi.end.X) - ox, float(gi.end.Y) - oy
                    ax.plot([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], color="#facc15", lw=1.4, zorder=5)
                else:
                    ax.plot(
                        [float(gi.start.X) - ox, float(gi.end.X) - ox],
                        [float(gi.start.Y) - oy, float(gi.end.Y) - oy],
                        color="#facc15",
                        lw=1.4,
                        zorder=5,
                    )

    layer_colors = {"F.Cu": "#ef4444", "B.Cu": "#3b82f6", "In1.Cu": "#22c55e", "In2.Cu": "#a855f7"}
    for t in board.traceItems:
        if isinstance(t, Segment):
            col = layer_colors.get(t.layer, "#94a3b8")
            ax.plot(
                [float(t.start.X) - ox, float(t.end.X) - ox],
                [float(t.start.Y) - oy, float(t.end.Y) - oy],
                color=col,
                lw=max(0.6, float(t.width) * 2.2),
                alpha=0.82,
                zorder=3,
            )
        elif isinstance(t, Arc):
            col = layer_colors.get(t.layer, "#94a3b8")
            ax.plot(
                [float(t.start.X) - ox, float(t.mid.X) - ox, float(t.end.X) - ox],
                [float(t.start.Y) - oy, float(t.mid.Y) - oy, float(t.end.Y) - oy],
                color=col,
                lw=max(0.6, float(t.width) * 2.2),
                alpha=0.82,
                zorder=3,
            )
        elif isinstance(t, Via):
            vx, vy = float(t.position.X) - ox, float(t.position.Y) - oy
            ax.add_patch(Circle((vx, vy), float(t.size) / 2.0, color="#eab308", zorder=4, alpha=0.9))

    for fp in board.footprints:
        fx, fy = float(fp.position.X) - ox, float(fp.position.Y) - oy
        ang = float(fp.position.angle or 0.0)
        ref = ""
        if isinstance(fp.properties, dict):
            ref = str(fp.properties.get("Reference", ""))
        for pad in fp.pads:
            dx, dy = _rotate_kicad(float(pad.position.X), float(pad.position.Y), ang)
            px, py = fx + dx, fy + dy
            pw, ph = float(pad.size.X), float(pad.size.Y)
            pang = float(pad.position.angle) if pad.position.angle is not None else ang
            if round(pang) % 180 == 90:
                pw, ph = ph, pw
            if pad.type == "np_thru_hole":
                ax.add_patch(Circle((px, py), pw / 2.0, facecolor="#1e293b", edgecolor="#cbd5e1", lw=0.8, zorder=4))
            else:
                lyrs = set(pad.layers or ["F.Cu"])
                if pad.type == "thru_hole":
                    col = "#22d3ee"
                elif "B.Cu" in lyrs and "F.Cu" not in lyrs:
                    col = "#60a5fa"
                else:
                    col = "#f87171"
                ax.add_patch(Rectangle((px - pw / 2.0, py - ph / 2.0), pw, ph, facecolor=col, edgecolor="none", alpha=0.85, zorder=4))
        if ref and not ref.startswith(("H", "LOGO", "REF", "G***", "TP")):
            ax.text(fx, fy, ref, color="#f8fafc", fontsize=5.8, ha="center", va="center", fontweight="bold", zorder=6)

    ax.set_xlim(-2, bw + 2)
    ax.set_ylim(bh + 2, -2)  # KiCad Y-down
    ax.set_aspect("equal")
    ax.set_title(title, color="#f8fafc", fontsize=11, pad=8)
    ax.set_xlabel("X (mm)", color="#94a3b8")
    ax.set_ylabel("Y (mm)", color="#94a3b8")
    ax.tick_params(colors="#94a3b8")


def compare_boards(
    blind_pcb: Path,
    ref_pcb: Path,
    out_json: Path,
    out_png: Path,
    spec_path: Path | None = None,
    title_blind: str = "Blind Solver (cloud-pcb-studio)",
    title_ref: str = "Human Reference",
) -> dict[str, Any]:
    spec = json.loads(spec_path.read_text(encoding="utf-8")) if spec_path and spec_path.exists() else None
    blind_m = _extract_metrics(blind_pcb, title_blind, spec=spec)
    ref_m = _extract_metrics(ref_pcb, title_ref, spec=spec)

    report = {
        "blind_solver": blind_m,
        "human_reference": ref_m,
        "deltas": {
            "wirelength_ratio_blind_vs_ref": round(blind_m["total_wirelength_mm"] / max(ref_m["total_wirelength_mm"], 1e-6), 3),
            "vias_total_blind_vs_ref": [blind_m["vias_total"], ref_m["vias_total"]],
            "vias_signal_blind_vs_ref": [blind_m["vias_signal"], ref_m["vias_signal"]],
            "vias_gnd_blind_vs_ref": [blind_m["vias_gnd"], ref_m["vias_gnd"]],
            "avg_decoupling_dist_mm_blind_vs_ref": [blind_m["avg_decoupling_dist_mm"], ref_m["avg_decoupling_dist_mm"]],
            "avg_header_edge_dist_mm_blind_vs_ref": [blind_m["avg_header_edge_dist_mm"], ref_m["avg_header_edge_dist_mm"]],
            "diff_pairs_blind_vs_ref": [blind_m["diff_pairs"], ref_m["diff_pairs"]],
        },
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7.2), facecolor="#020617")
    _plot_board_ax(
        ax1,
        blind_pcb,
        f"{title_blind}\nLen={blind_m['total_wirelength_mm']:.1f}mm | Vias={blind_m['vias_total']} (Sig {blind_m['vias_signal']}, GND {blind_m['vias_gnd']})",
    )
    _plot_board_ax(
        ax2,
        ref_pcb,
        f"{title_ref}\nLen={ref_m['total_wirelength_mm']:.1f}mm | Vias={ref_m['vias_total']} (Sig {ref_m['vias_signal']}, GND {ref_m['vias_gnd']})",
    )
    plt.tight_layout()
    fig.savefig(out_png, dpi=180, facecolor="#020617")
    plt.close(fig)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare blind PCB output against human reference.")
    parser.add_argument("--blind", type=Path, default=BLIND_PCB)
    parser.add_argument("--ref", type=Path, default=REF_PCB)
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-png", type=Path, default=OUT_PNG)
    parser.add_argument("--title-blind", type=str, default="Blind Solver (cloud-pcb-studio)")
    parser.add_argument("--title-ref", type=str, default="Human Reference")
    args = parser.parse_args()
    rep = compare_boards(
        args.blind,
        args.ref,
        args.out_json,
        args.out_png,
        spec_path=args.spec,
        title_blind=args.title_blind,
        title_ref=args.title_ref,
    )
    print(json.dumps(rep["deltas"], indent=2))


if __name__ == "__main__":
    main()
