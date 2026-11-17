"""Board-agnostic blind PCB solver consuming an arbitrary `blind_spec.json`.

Design constraints (strict zero-overfitting):
- Strictly reads ONLY `blind_spec.json` (never accesses any sealed reference files).
- Uses general EDA algorithms applicable to any PCB:
  1. Netlist-topology classification & perimeter-rail header/testpad alignment
  2. Value- and pin-capacity-aware IC decoupling capacitor assignment (no round-robin)
  3. Functional micro-clustering (coupled 2-pin chains + multi-pass barycenter refinement)
  4. Pitch-aware NetClass clearance clamping & microstrip impedance calibration
  5. Pre-route SMD GND pad fanout vias directly to inner planes (eliminating outer GND chains)
  6. Dual Specctra DSN/SES autorouter bake-off (fastroute v0.1.14 vs freerouting v2.5.0)
  7. True 4-layer GND copper pours (F.Cu, In1.Cu, In2.Cu, B.Cu) + GND stitching via fence
  8. Hole-free KiCad Zone export, rotated oval/roundrect RS-274X + Excellon export,
     and independent PyGerber + Shapely LVS/DRC + GND-plane continuity verification.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import time
import uuid
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any

from gerbonara.apertures import CircleAperture, ExcellonTool, ObroundAperture, RectangleAperture
from gerbonara.excellon import ExcellonFile
from gerbonara.graphic_objects import Flash, Line, Region
from gerbonara.rs274x import GerberFile
from gerbonara.utils import MM
from kiutils.board import Board
from kiutils.footprint import DrillDefinition, Footprint, Pad
from kiutils.items.brditems import LayerToken, Segment, Stackup, StackupLayer, Via
from kiutils.items.common import Coordinate, Effects, Font, Net, Position, Stroke
from kiutils.items.fpitems import FpRect, FpText
from kiutils.items.gritems import GrLine
from kiutils.items.zones import FilledPolygon, FillSettings, Zone, ZonePolygon
from pygerber.gerberx3.api.v2 import GerberFile as PyGerberFile
from shapely.affinity import rotate as shp_rotate
from shapely.affinity import scale as shp_scale
from shapely.affinity import translate as shp_translate
from shapely.geometry import LineString as ShpLineString
from shapely.geometry import Point as ShpPoint
from shapely.geometry import Polygon as ShpPolygon
from shapely.geometry import box as shp_box
from shapely.ops import split as shp_split
from shapely.ops import unary_union

from cloud_pcb.layout_route.dsn_ses_bridge import (
    _rotate_kicad,
    board_to_dsn,
    merge_ses_into_board,
    parse_ses,
)
from cloud_pcb.verify_gate.gerber_drc import (
    _compute_min_annular_ring,
    _gerber_layer_union,
)
from contracts.schemas import NetClassRule, RoutingBenchmark, save_json
from cloud_pcb.general_solver.topology import analyze_topology, macro_targets
from cloud_pcb.general_solver.semantics import component_envelope, infer_rf, rf_keepouts, world_box, pad_bounds
from cloud_pcb.general_solver.power import specialize_power_rules, power_islands, width_distribution

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BLIND_SPEC = PROJECT_ROOT / "benchmark_case" / "blind_spec.json"
DEFAULT_OUTDIR = PROJECT_ROOT / "artifacts" / "blind_benchmark"


def _rounded_rect_points(
    w: float, h: float, radius: float, segs_per_corner: int = 8
) -> list[tuple[float, float]]:
    """Return closed polygon vertices (CCW) for [0..w] x [0..h] with corner radius."""
    r = max(0.0, min(radius, min(w, h) / 2.0 - 0.1))
    if r <= 1e-3:
        return [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]
    corners = [
        (w - r, r, -math.pi / 2.0, 0.0),
        (w - r, h - r, 0.0, math.pi / 2.0),
        (r, h - r, math.pi / 2.0, math.pi),
        (r, r, math.pi, 1.5 * math.pi),
    ]
    pts: list[tuple[float, float]] = []
    for cx, cy, a0, a1 in corners:
        for k in range(segs_per_corner + 1):
            ang = a0 + (a1 - a0) * (k / segs_per_corner)
            pts.append((round(cx + r * math.cos(ang), 4), round(cy + r * math.sin(ang), 4)))
    return pts


def _comp_local_bbox(comp: dict[str, Any]) -> tuple[float, float, float, float]:
    """Compute local-frame (min_x, min_y, max_x, max_y) around all component pads."""
    pads = comp.get("pads", [])
    if not pads:
        return (-1.0, -1.0, 1.0, 1.0)
    min_x,min_y,max_x,max_y=pad_bounds(comp)
    body=component_envelope(comp)['bbox_mm']
    if comp.get('side')=='bottom': min_x,max_x=-max_x,-min_x
    return (min(min_x,body[0]),min(min_y,body[1]),max(max_x,body[2]),max(max_y,body[3]))


def _rotated_bbox(
    bbox: tuple[float, float, float, float], angle_deg: float
) -> tuple[float, float, float, float]:
    """Axis-aligned bounding box of a local bbox rotated by KiCad angle_deg."""
    x0, y0, x1, y1 = bbox
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    rot = [_rotate_kicad(cx, cy, angle_deg) for cx, cy in corners]
    xs = [p[0] for p in rot]
    ys = [p[1] for p in rot]
    return (min(xs), min(ys), max(xs), max(ys))


def _is_power_net(name: str) -> bool:
    u = name.upper()
    if u in {"GND", "AGND", "DGND", "PGND", "VSS", "VBUS", "VCC", "VDD", "VBAT", "VIN", "VOUT", "5V", "3V3", "3.3V", "1V8", "12V"}:
        return True
    return u.startswith("+") or u.startswith("VDD_") or u.startswith("VCC_")


def _is_gnd_net(name: str) -> bool:
    u = name.upper()
    return u in {"GND", "AGND", "DGND", "PGND", "VSS"}


def _parse_capacitance_farads(val: str, fp_name: str = "") -> float:
    """Parse capacitor value like '100nF', '10uF', '22pF', '0.1u' -> Farads."""
    s = (val or "").strip().lower().replace("μ", "u")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(p|n|u|m)?\s*f?", s)
    if m:
        num = float(m.group(1))
        unit = m.group(2) or ""
        scale = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3}.get(unit, 1e-6)
        return num * scale
    fp = fp_name.lower()
    if "1206" in fp or "1210" in fp or "elec" in fp:
        return 22e-6
    if "0805" in fp:
        return 4.7e-6
    return 100e-9


def _calibrate_diff_pair_geometry(target_ohm: float, h_mm: float = 0.2104, er: float = 4.4, t_mm: float = 0.035) -> tuple[float, float]:
    """Microstrip differential pair (trace_width_mm, gap_mm) for target Z_diff on 4L prepreg."""
    # Single-ended Z0 ~ (87 / sqrt(er + 1.41)) * ln(5.98*h / (0.8*w + t))
    # Z_diff ~ 2 * Z0 * (1 - 0.48 * exp(-0.96 * gap / h))
    # Search manufacturable widths [0.18..0.35] and gaps [0.13..0.35]
    best = (0.22, 0.18)
    best_err = float("inf")
    for iw in range(18, 36):
        w = iw * 0.01
        z0 = (87.0 / math.sqrt(er + 1.41)) * math.log((5.98 * h_mm) / (0.8 * w + t_mm))
        for ig in range(13, 36):
            g = ig * 0.01
            zd = 2.0 * z0 * (1.0 - 0.48 * math.exp(-0.96 * g / h_mm))
            err = abs(zd - target_ohm) + 0.02 * abs(w - 0.25)
            if err < best_err:
                best_err = err
                best = (round(w, 3), round(g, 3))
    return best


def _is_copper_pad(pad: Pad) -> bool:
    """Return True if a kiutils Pad has copper on any layer (excludes F.Paste/F.Mask-only apertures)."""
    if pad.type in ("thru_hole", "np_thru_hole"):
        return True
    return any(l.endswith(".Cu") or l == "*.Cu" for l in (pad.layers or []))


def _classify_components_and_place(
    spec: dict[str, Any],
) -> tuple[dict[str, tuple[float, float, float]], dict[str, Any]]:
    """General topology-aware placement + perimeter rail alignment + IC-specific cap matching."""
    outline = spec["board_outline_mm"]
    W = float(outline["width"])
    H = float(outline["height"])
    ox = float(outline.get("origin_x", 0.0))
    oy = float(outline.get("origin_y", 0.0))
    cr = float(outline.get("corner_radius_mm", 0.0))

    board_poly = ShpPolygon(outline.get('vertices_mm') or _rounded_rect_points(W, H, cr))
    safe_inner_poly = board_poly.buffer(-1.0)

    placed: dict[str, tuple[float, float, float]] = {}
    occupied_by_ref: dict[str, Any] = {}
    fixed_obstacles: list[Any] = []

    # Reserve mounting hole keepout circles
    for mh in spec.get("mounting_holes", []):
        mx = float(mh["x_mm"]) - ox
        my = float(mh["y_mm"]) - oy
        r = float(mh["drill_mm"]) / 2.0 + 1.0
        fixed_obstacles.append(ShpPoint(mx, my).buffer(r, resolution=24))

    comp_by_ref: dict[str, dict[str, Any]] = {c["refdes"]: c for c in spec["components"]}
    local_bboxes = {ref: _comp_local_bbox(c) for ref, c in comp_by_ref.items()}
    hierarchy=analyze_topology(spec)
    targets=macro_targets(spec,hierarchy)

    def _world_pad_pos(ref: str, pad: dict[str, Any], x: float, y: float, ang: float) -> tuple[float, float]:
        flip=-1 if comp_by_ref[ref].get('side')=='bottom' else 1
        dx, dy = _rotate_kicad(flip*float(pad["x_rel_mm"]), float(pad["y_rel_mm"]), ang)
        return x + dx, y + dy

    def _courtyard_geom(ref: str, x: float, y: float, ang: float, pad_margin: float) -> Any:
        bx0, by0, bx1, by1 = _rotated_bbox(local_bboxes[ref], ang)
        return shp_box(x + bx0 - pad_margin, y + by0 - pad_margin, x + bx1 + pad_margin, y + by1 + pad_margin)

    def _is_valid_slot(ref: str, x: float, y: float, ang: float, margin: float = 0.65, ignore_ref: str | None = None) -> bool:
        g = _courtyard_geom(ref, x, y, ang, margin)
        if not safe_inner_poly.contains(g):
            return False
        for obs in fixed_obstacles:
            if g.intersects(obs):
                return False
        for oref, occ in occupied_by_ref.items():
            if oref == ignore_ref:
                continue
            if comp_by_ref[oref].get('side','top')!=comp_by_ref[ref].get('side','top'):
                # Bodies may overlap across sides, but plated pins occupy both sides.
                thru=[p for p in comp_by_ref[oref]['pads'] if p.get('type') in ('thru_hole','np_thru_hole')]
                for p in thru:
                    px,py=_world_pad_pos(oref,p,*placed[oref])
                    if g.intersects(ShpPoint(px,py).buffer(max(p['w_mm'],p['h_mm'])/2+0.3)): return False
                continue
            if g.intersects(occ):
                return False
        return True

    # 1. Pin mechanical edge connectors first
    edge_refs: set[str] = set()
    edge_sides: set[str] = set()
    for ec in spec.get("edge_connectors", []):
        ref = ec["refdes"]
        if ref not in comp_by_ref:
            continue
        ex = float(ec["x_mm"]) - ox
        ey = float(ec["y_mm"]) - oy
        ang = float(ec.get("angle_deg", 0.0))
        placed[ref] = (ex, ey, ang)
        occupied_by_ref[ref] = _courtyard_geom(ref, ex, ey, ang, 0.55)
        edge_refs.add(ref)
        if ec.get("edge_side"):
            edge_sides.add(str(ec["edge_side"]).lower())

    # Antenna lobes inferred in footprint coordinates must face a board edge.
    for ref,comp in comp_by_ref.items():
        rf=infer_rf(comp)
        if ref in placed or not rf or 'local_bbox_mm' not in rf: continue
        best=None
        for angle in (0.,90.,180.,270.):
            b=_rotated_bbox(tuple(pad_bounds(comp)),angle)
            lobe=world_box(rf['local_bbox_mm'],(0,0,angle)).centroid
            candidates=[(-b[0]+0.51,H/2),(W-b[2]-0.51,H/2),(W/2,-b[1]+0.51),(W/2,H-b[3]-0.51)]
            for x,y in candidates:
                physical=world_box(local_bboxes[ref],(x,y,angle))
                pads_geom=world_box(pad_bounds(comp),(x,y,angle))
                if not board_poly.buffer(-.50).covers(pads_geom): continue
                if any(physical.intersects(g) for g in fixed_obstacles+list(occupied_by_ref.values())): continue
                antenna=world_box(rf['local_bbox_mm'],(x,y,angle))
                score=antenna.intersection(board_poly).area
                if best is None or score<best[0]: best=(score,(x,y,angle))
        if best:
            placed[ref]=best[1]; occupied_by_ref[ref]=_courtyard_geom(ref,*best[1],0.65); edge_refs.add(ref)
            kg=world_box(rf['local_bbox_mm'],best[1]); fixed_obstacles.append(kg)

    # 2. Build netlist topology purely from spec["nets"]
    nets: dict[str, list[list[str]]] = spec.get("nets", {})
    power_rails = {r["net"] for r in spec.get("design_requirements", {}).get("power_rails", [])}
    power_rails |= {n for n in nets if _is_power_net(n)}
    gnd_names = {n for n in power_rails if _is_gnd_net(n)} or {"GND"}
    non_gnd_rails = power_rails - gnd_names

    comp_signal_neighbors: dict[str, set[str]] = defaultdict(set)
    comp_signal_nets: dict[str, set[str]] = defaultdict(set)
    for nname, pins in nets.items():
        if not nname or nname in gnd_names:
            continue
        refs_on_net = [p[0] for p in pins if p[0] in comp_by_ref]
        if nname not in power_rails:
            for r in refs_on_net:
                comp_signal_nets[r].add(nname)
                for r2 in refs_on_net:
                    if r != r2:
                        comp_signal_neighbors[r].add(r2)

    ics: list[str] = []
    regulators: list[str] = []
    multi_pin_headers: list[str] = []
    single_pad_tp: list[str] = []
    signal_passives: list[str] = []
    bypass_caps: list[str] = []

    for ref, comp in comp_by_ref.items():
        if ref in edge_refs:
            continue
        pads = comp.get("pads", [])
        npads = len(pads)
        fp_name = comp.get("footprint_name", "").lower()
        pad_nets = {p.get("net") for p in pads if p.get("net")}
        non_pwr = {n for n in pad_nets if n not in power_rails}
        cu_pads = [
            p for p in pads
            if p.get("type") == "thru_hole"
            or any(str(l).endswith(".Cu") or l == "*.Cu" for l in (p.get("layers") or ["F.Cu"]))
        ]
        th_count = sum(1 for p in cu_pads if p.get("type") == "thru_hole")
        is_th_conn = th_count >= max(1, len(cu_pads) // 2) if cu_pads else False

        if npads == 1:
            single_pad_tp.append(ref)
        elif (
            is_th_conn
            or "pinheader" in fp_name
            or "pinsocket" in fp_name
            or "header" in fp_name
            or "jst" in fp_name
            or "terminal" in fp_name
        ):
            multi_pin_headers.append(ref)
        elif npads >= 3:
            pwr_touch = pad_nets & non_gnd_rails
            if len(pwr_touch) >= 2 and npads <= 8 and len(non_pwr) == 0:
                regulators.append(ref)
            else:
                ics.append(ref)
        else:
            has_gnd = bool(pad_nets & gnd_names)
            has_rail = bool(pad_nets & non_gnd_rails)
            if not non_pwr and has_gnd and has_rail:
                bypass_caps.append(ref)
            else:
                signal_passives.append(ref)

    ics.sort(key=lambda r: (-len(comp_signal_nets[r]), -len(comp_by_ref[r]["pads"]), r))

    def _hpwl_cost(ref: str, cx: float, cy: float, ang: float, tx: float, ty: float, anchor_weight: float = 0.15) -> float:
        cost = 0.0
        for pad in comp_by_ref[ref]["pads"]:
            nn = pad.get("net")
            if not nn or nn in gnd_names:
                continue
            w = 0.20 if nn in power_rails else 1.0
            px, py = _world_pad_pos(ref, pad, cx, cy, ang)
            for other_ref, other_pin in nets.get(nn, []):
                if other_ref == ref or other_ref not in placed:
                    continue
                oxp, oyp, oang = placed[other_ref]
                opad = next((op for op in comp_by_ref[other_ref]["pads"] if op["num"] == other_pin), None)
                if opad is None:
                    continue
                wx, wy = _world_pad_pos(other_ref, opad, oxp, oyp, oang)
                cost += w * (abs(px - wx) + abs(py - wy))
        return cost + anchor_weight * math.hypot(cx - tx, cy - ty)

    def _find_nearest_legal(
        ref: str,
        tx: float,
        ty: float,
        prefer_angles: tuple[float, ...] = (0.0, 90.0, 180.0, 270.0),
        margin: float = 0.65,
        step: float = 0.6,
        max_radius: float = 38.0,
        ignore_self: bool = False,
        custom_cost: Any = None,
    ) -> tuple[float, float, float]:
        best_cost = float("inf")
        best_pos: tuple[float, float, float] | None = None
        tx_c = max(1.8, min(W - 1.8, tx))
        ty_c = max(1.8, min(H - 1.8, ty))
        ig = ref if ignore_self else None

        for r_ring in [k * step for k in range(int(max_radius / step) + 1)]:
            if r_ring == 0.0:
                pts = [(tx_c, ty_c)]
            else:
                n_ang = max(8, int(2 * math.pi * r_ring / max(step, 0.8)))
                pts = [
                    (
                        tx_c + r_ring * math.cos(2 * math.pi * k / n_ang),
                        ty_c + r_ring * math.sin(2 * math.pi * k / n_ang),
                    )
                    for k in range(n_ang)
                ]
            found_ring = False
            for cx, cy in pts:
                for ang in prefer_angles:
                    if _is_valid_slot(ref, cx, cy, ang, margin=margin, ignore_ref=ig):
                        cval = custom_cost(cx, cy, ang) if custom_cost else _hpwl_cost(ref, cx, cy, ang, tx, ty)
                        if cval < best_cost:
                            best_cost = cval
                            best_pos = (round(cx, 3), round(cy, 3), ang)
                            found_ring = True
            if found_ring and best_pos is not None:
                return best_pos

        if margin > 0.30:
            return _find_nearest_legal(
                ref, tx, ty, prefer_angles, margin=max(0.28, margin - 0.25), step=0.5, max_radius=45.0,
                ignore_self=ignore_self, custom_cost=custom_cost
            )
        if comp_by_ref[ref].get('side','top')=='top' and len(comp_by_ref[ref]['pads'])<=2 and all(p.get('type')=='smd' for p in comp_by_ref[ref]['pads']):
            comp_by_ref[ref]['side']='bottom'
            local_bboxes[ref]=_comp_local_bbox(comp_by_ref[ref])
            return _find_nearest_legal(ref,tx,ty,prefer_angles,margin=0.55,step=0.5,max_radius=45.0,ignore_self=ignore_self,custom_cost=custom_cost)
        raise RuntimeError(f"Unable to place component {ref} without overlap on {W}x{H} mm board")

    def _barycenter(ref: str, default_xy: tuple[float, float]) -> tuple[float, float]:
        xs: list[float] = []
        ys: list[float] = []
        for nname, pins in nets.items():
            if not nname or nname in gnd_names:
                continue
            if not any(p[0] == ref for p in pins):
                continue
            wt = 4 if nname not in power_rails else 1
            for other_ref, other_pin in pins:
                if other_ref != ref and other_ref in placed:
                    oxp, oyp, oang = placed[other_ref]
                    opad = next((op for op in comp_by_ref[other_ref]["pads"] if op["num"] == other_pin), None)
                    if opad:
                        wx, wy = _world_pad_pos(other_ref, opad, oxp, oyp, oang)
                        xs.extend([wx] * wt)
                        ys.extend([wy] * wt)
        if not xs:
            return default_xy
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    # 3. Place central MCU & Regulators & Secondary ICs
    if ics:
        mcu_ref = ics[0]
        # Start near center; slightly shift away from left edge connector if board is compact
        cx0, cy0 = W * 0.50, H * 0.45
        pos = _find_nearest_legal(mcu_ref, cx0, cy0, margin=1.5)
        placed[mcu_ref] = pos
        occupied_by_ref[mcu_ref] = _courtyard_geom(mcu_ref, *pos, 1.1)

    pwr_anchor = (W * 0.25, H * 0.55)
    if edge_refs:
        er = next(iter(edge_refs))
        ex, ey, _ = placed[er]
        pwr_anchor = (min(W - 8.0, ex + 10.0), min(H - 6.0, ey + 4.0))
    for reg in regulators:
        tx, ty = _barycenter(reg, pwr_anchor)
        pos = _find_nearest_legal(reg, tx, ty, margin=1.0)
        placed[reg] = pos
        occupied_by_ref[reg] = _courtyard_geom(reg, *pos, 0.85)

    rem_ics = ics[1:]
    for idx, ref in enumerate(rem_ics):
        ang0 = (idx + 0.5) * (2.0 * math.pi / max(len(rem_ics), 1))
        fallback = (W * 0.55 + 11.0 * math.cos(ang0), H * 0.50 + 9.0 * math.sin(ang0))
        target=targets.get(ref,fallback)
        bx,by=_barycenter(ref,target)
        tx,ty=(0.75*target[0]+0.25*bx,0.75*target[1]+0.25*by)
        pos = _find_nearest_legal(ref, tx, ty, margin=1.0)
        placed[ref] = pos
        occupied_by_ref[ref] = _courtyard_geom(ref, *pos, 0.85)

    # 4. Place multi-pin headers ALONG BOARD PERIMETER EDGES (parallel to edge)
    multi_pin_headers.sort(key=lambda r: (-len(comp_by_ref[r]["pads"]), r))
    # Build perimeter rail candidates on Top, Bottom, Right, Left (skip side occupied by edge connector if tight)
    for ref in multi_pin_headers:
        bx, by = _barycenter(ref, (W * 0.5, H * 0.2))
        lx0, ly0, lx1, ly1 = local_bboxes[ref]
        w0, h0 = lx1 - lx0, ly1 - ly0
        # Determine horizontal vs vertical natural footprint orientation
        # Long dimension should run parallel to the chosen edge
        horiz_angles = (0.0, 180.0) if w0 >= h0 else (90.0, 270.0)
        vert_angles = (90.0, 270.0) if w0 >= h0 else (0.0, 180.0)

        best_edge_cost = float("inf")
        best_edge_pos: tuple[float, float, float] | None = None

        # Sample perimeter bands: top, bottom, right, left
        edge_bands: list[tuple[list[tuple[float, float]], tuple[float, ...]]] = []
        # Top edge band (y = 2.4..5.5)
        top_pts = [(x, y) for y in (2.6, 3.6, 5.2, 6.8) for x in [4.0 + 1.5 * k for k in range(int((W - 8.0) / 1.5) + 1)]]
        edge_bands.append((top_pts, horiz_angles))
        # Bottom edge band
        bot_pts = [(x, y) for y in (H - 2.6, H - 3.6, H - 5.2, H - 6.8) for x in [4.0 + 1.5 * k for k in range(int((W - 8.0) / 1.5) + 1)]]
        edge_bands.append((bot_pts, horiz_angles))
        # Right edge band
        right_pts = [(x, y) for x in (W - 2.6, W - 3.8, W - 5.5, W - 7.5) for y in [4.0 + 1.5 * k for k in range(int((H - 8.0) / 1.5) + 1)]]
        edge_bands.append((right_pts, vert_angles))
        if "left" not in edge_sides:
            left_pts = [(x, y) for x in (2.6, 3.8, 5.5) for y in [4.0 + 1.5 * k for k in range(int((H - 8.0) / 1.5) + 1)]]
            edge_bands.append((left_pts, vert_angles))

        for pts, angs in edge_bands:
            for cx, cy in pts:
                for ang in angs:
                    if _is_valid_slot(ref, cx, cy, ang, margin=0.65):
                        edge_dist = min(cx, W - cx, cy, H - cy)
                        cval = _hpwl_cost(ref, cx, cy, ang, bx, by, anchor_weight=0.05) + 1.8 * edge_dist
                        if cval < best_edge_cost:
                            best_edge_cost = cval
                            best_edge_pos = (round(cx, 3), round(cy, 3), ang)

        if best_edge_pos is None:
            best_edge_pos = _find_nearest_legal(ref, bx, by, margin=0.65)
        placed[ref] = best_edge_pos
        occupied_by_ref[ref] = _courtyard_geom(ref, *best_edge_pos, 0.55)

    # 5. Place single-pad test/solder points neatly aligned along available perimeter edge
    # Sort by connected net neighbor coordinate for untangled fanout
    def _tp_sort_key(r: str) -> tuple[float, float]:
        bx, by = _barycenter(r, (W * 0.9, H * 0.5))
        return (round(by, 1), round(bx, 1))

    single_pad_tp.sort(key=_tp_sort_key)
    for ref in single_pad_tp:
        bx, by = _barycenter(ref, (W * 0.9, H * 0.5))
        # Prefer right edge or bottom/top perimeter rail
        cand_rails = (
            [(W - 3.2, 4.0 + 1.2 * k) for k in range(int((H - 8.0) / 1.2) + 1)]
            + [(4.0 + 1.5 * k, 2.8) for k in range(int((W - 8.0) / 1.5) + 1)]
            + [(4.0 + 1.5 * k, H - 2.8) for k in range(int((W - 8.0) / 1.5) + 1)]
        )
        best_tp_cost = float("inf")
        best_tp_pos: tuple[float, float, float] | None = None
        for cx, cy in cand_rails:
            for ang in (0.0, 90.0):
                if _is_valid_slot(ref, cx, cy, ang, margin=0.55):
                    edge_dist = min(cx, W - cx, cy, H - cy)
                    cval = _hpwl_cost(ref, cx, cy, ang, bx, by, anchor_weight=0.05) + 1.5 * edge_dist
                    if cval < best_tp_cost:
                        best_tp_cost = cval
                        best_tp_pos = (round(cx, 3), round(cy, 3), ang)
        if best_tp_pos is None:
            best_tp_pos = _find_nearest_legal(ref, bx, by, margin=0.55)
        placed[ref] = best_tp_pos
        occupied_by_ref[ref] = _courtyard_geom(ref, *best_tp_pos, 0.50)

    # 6. Refine secondary ICs (now that headers are placed!)
    for ref in rem_ics:
        tx, ty = _barycenter(ref, (placed[ref][0], placed[ref][1]))
        if any(ref in g['anchors'] for g in hierarchy['repeated_channels']):
            tx,ty=targets[ref]
        pos = _find_nearest_legal(ref, tx, ty, margin=0.85, ignore_self=True)
        placed[ref] = pos
        occupied_by_ref[ref] = _courtyard_geom(ref, *pos, 0.75)

    # 8. IC-Specific Value- and Pin-Capacity-Aware Decoupling Capacitor Assignment
    # Collect exact world coords of every non-GND power pin on ICs, regulators, edge connectors
    rail_pins: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ic_pin_counts: dict[str, int] = {r: len(comp_by_ref[r]["pads"]) for r in comp_by_ref}
    for ref in [*ics, *regulators, *edge_refs]:
        if ref not in placed:
            continue
        cx, cy, cang = placed[ref]
        role = "reg" if ref in regulators else ("edge" if ref in edge_refs else "ic")
        for pad in comp_by_ref[ref]["pads"]:
            pn = pad.get("net")
            if pn in non_gnd_rails:
                wx, wy = _world_pad_pos(ref, pad, cx, cy, cang)
                rail_pins[pn].append({
                    "ref": ref,
                    "pad": pad["num"],
                    "wx": wx,
                    "wy": wy,
                    "role": role,
                    "weight": ic_pin_counts[ref],
                    "assigned": 0,
                })

    # Sort bypass caps: bulk (>= 4.7uF) first (for regulators/edges), then small high-freq caps
    cap_records = []
    for ref in bypass_caps:
        comp = comp_by_ref[ref]
        cap_f = _parse_capacitance_farads(comp.get("value", ""), comp.get("footprint_name", ""))
        rail = next((p.get("net") for p in comp["pads"] if p.get("net") in non_gnd_rails), "+3V3")
        cap_records.append((ref, cap_f, rail))
    cap_records.sort(key=lambda t: (-t[1], t[0]))

    # Track total caps assigned per IC ref to ensure proportional distribution across ICs
    ic_cap_load: dict[str, float] = defaultdict(float)
    for ref, cap_f, rail in cap_records:
        candidates = rail_pins.get(rail) or []
        if not candidates:
            pos = _find_nearest_legal(ref, W * 0.5, H * 0.5, margin=0.50)
            placed[ref] = pos
            occupied_by_ref[ref] = _courtyard_geom(ref, *pos, 0.45)
            continue

        is_bulk = cap_f >= 4.7e-6
        if is_bulk:
            # Bulk caps strongly prefer regulators first, then power-entry edge, then largest IC
            def _bulk_score(t: dict[str, Any]) -> tuple[int, float, int]:
                role_pri = 0 if t["role"] == "reg" else (1 if t["role"] == "edge" else 2)
                return (role_pri, t["assigned"], -t["weight"])
            target = min(candidates, key=_bulk_score)
        else:
            # High-freq decoupling caps go to uncovered IC power pins first, balanced by sqrt(pin_count)
            def _hf_score(t: dict[str, Any]) -> tuple[int, int, float, float]:
                role_pri = 0 if t["role"] == "ic" else 1
                cap_norm = ic_cap_load[t["ref"]] / max(math.sqrt(t["weight"]), 1.0)
                return (role_pri, t["assigned"], cap_norm, -t["weight"])
            target = min(candidates, key=_hf_score)

        target["assigned"] += 1
        ic_cap_load[target["ref"]] += 1.0
        tx, ty = target["wx"], target["wy"]

        # Minimize distance from cap's power pad directly to the target IC power pin!
        pwr_pad = next((p for p in comp_by_ref[ref]["pads"] if p.get("net") == rail), comp_by_ref[ref]["pads"][0])

        def _cap_pin_cost(cx: float, cy: float, ang: float, _pp: dict[str, Any] = pwr_pad, _tx: float = tx, _ty: float = ty) -> float:
            px, py = _world_pad_pos(ref, _pp, cx, cy, ang)
            return math.hypot(px - _tx, py - _ty)

        pos = _find_nearest_legal(ref, tx, ty, margin=0.45, step=0.4, max_radius=25.0, custom_cost=_cap_pin_cost)
        placed[ref] = pos
        occupied_by_ref[ref] = _courtyard_geom(ref, *pos, 0.42)


    # 7. Place signal_passives using dynamic placed-neighbor degree + micro-clustering
    rem_passives = set(signal_passives)
    ordered_passives: list[str] = []
    while rem_passives:
        # Pick passive with highest number of already-placed neighbors (tie-break: smallest pad count / ref)
        nxt = max(
            rem_passives,
            key=lambda r: (
                sum(1 for nb in comp_signal_neighbors[r] if nb in placed),
                len(comp_signal_nets[r]),
                r,
            ),
        )
        rem_passives.remove(nxt)
        ordered_passives.append(nxt)
        tx, ty = _barycenter(nxt, (W * 0.5, H * 0.5))
        pos = _find_nearest_legal(nxt, tx, ty, margin=0.55)
        placed[nxt] = pos
        occupied_by_ref[nxt] = _courtyard_geom(nxt, *pos, 0.48)

    # 1 refinement pass on signal_passives so coupled chains (e.g., LED <-> R) pull tight
    for ref in ordered_passives:
        tx, ty = _barycenter(ref, (placed[ref][0], placed[ref][1]))
        pos = _find_nearest_legal(ref, tx, ty, margin=0.52, ignore_self=True)
        placed[ref] = pos
        occupied_by_ref[ref] = _courtyard_geom(ref, *pos, 0.46)

    meta = {
        "board_width_mm": W,
        "board_height_mm": H,
        "corner_radius_mm": cr,
        "groups": {
            "edge_connectors": sorted(edge_refs),
            "ics": ics,
            "regulators": regulators,
            "multi_pin_headers": multi_pin_headers,
            "single_pad_tp": single_pad_tp,
            "signal_passives": ordered_passives,
            "bypass_caps": bypass_caps,
        },
        "total_placed": len(placed),
        "hierarchy": hierarchy,
        "component_sides": {r:c.get('side','top') for r,c in comp_by_ref.items()},
    }
    return placed, meta


def _compute_min_pad_gap_by_class(
    spec: dict[str, Any],
    net_to_class: dict[str, str],
) -> dict[str, float]:
    """Measure physical edge-to-edge gap between distinct-net copper pads per NetClass."""
    min_gap: dict[str, float] = {}
    for comp in spec.get("components", []):
        pads = [
            p for p in comp.get("pads", [])
            if p.get("type") in ("thru_hole", "np_thru_hole")
            or any(str(l).endswith(".Cu") or l == "*.Cu" for l in (p.get("layers") or ["F.Cu"]))
        ]
        if len(pads) < 2:
            continue
        for i in range(len(pads)):
            p1 = pads[i]
            n1 = p1.get("net") or ""
            c1 = net_to_class.get(n1, "DEFAULT") if n1 else "DEFAULT"
            x1, y1, w1, h1 = float(p1["x_rel_mm"]), float(p1["y_rel_mm"]), float(p1["w_mm"]), float(p1["h_mm"])
            for j in range(i + 1, len(pads)):
                p2 = pads[j]
                n2 = p2.get("net") or ""
                if n1 and n1 == n2:
                    continue
                x2, y2, w2, h2 = float(p2["x_rel_mm"]), float(p2["y_rel_mm"]), float(p2["w_mm"]), float(p2["h_mm"])
                gx = abs(x1 - x2) - (w1 + w2) / 2.0
                gy = abs(y1 - y2) - (h1 + h2) / 2.0
                gap = max(gx, gy) if (gx > 0 or gy > 0) else 0.0
                if gap > 0.02:
                    c2 = net_to_class.get(n2, "DEFAULT") if n2 else "DEFAULT"
                    for cls in (c1, c2):
                        if cls not in min_gap or gap < min_gap[cls]:
                            min_gap[cls] = gap
    return min_gap


def _build_blind_kicad_board(
    spec: dict[str, Any],
    placed: dict[str, tuple[float, float, float]],
) -> tuple[Board, dict[str, NetClassRule], dict[str, str], list[dict[str, Any]], ShpPolygon]:
    """Construct unrouted 4-layer kiutils Board with pitch-safe rules & impedance diff pairs."""
    outline = spec["board_outline_mm"]
    W = float(outline["width"])
    H = float(outline["height"])
    ox = float(outline.get("origin_x", 0.0))
    oy = float(outline.get("origin_y", 0.0))
    cr = float(outline.get("corner_radius_mm", 0.0))

    board_poly = ShpPolygon(outline.get('vertices_mm') or _rounded_rect_points(W, H, cr))

    board = Board.create_new()
    board.version = "20231120"
    board.generator = "cloud_pcb_general_solver"
    board.general.thickness = 1.6
    board.layers = [
        LayerToken(ordinal=0, name="F.Cu", type="signal"),
        LayerToken(ordinal=1, name="In1.Cu", type="power"),
        LayerToken(ordinal=2, name="In2.Cu", type="power"),
        LayerToken(ordinal=31, name="B.Cu", type="signal"),
        LayerToken(ordinal=34, name="B.Paste", type="user"),
        LayerToken(ordinal=35, name="F.Paste", type="user"),
        LayerToken(ordinal=36, name="B.SilkS", type="user"),
        LayerToken(ordinal=37, name="F.SilkS", type="user"),
        LayerToken(ordinal=38, name="B.Mask", type="user"),
        LayerToken(ordinal=39, name="F.Mask", type="user"),
        LayerToken(ordinal=44, name="Edge.Cuts", type="user"),
        LayerToken(ordinal=46, name="B.CrtYd", type="user"),
        LayerToken(ordinal=47, name="F.CrtYd", type="user"),
        LayerToken(ordinal=48, name="B.Fab", type="user"),
        LayerToken(ordinal=49, name="F.Fab", type="user"),
    ]
    board.setup.stackup = Stackup(
        layers=[
            StackupLayer(name="F.Cu", type="copper", thickness=0.035),
            StackupLayer(name="dielectric 1", type="prepreg", thickness=0.2104, material="FR4-7628", epsilonR=4.4),
            StackupLayer(name="In1.Cu", type="copper", thickness=0.0175),
            StackupLayer(name="dielectric 2", type="core", thickness=1.065, material="FR4", epsilonR=4.5),
            StackupLayer(name="In2.Cu", type="copper", thickness=0.0175),
            StackupLayer(name="dielectric 3", type="prepreg", thickness=0.2104, material="FR4-7628", epsilonR=4.4),
            StackupLayer(name="B.Cu", type="copper", thickness=0.035),
        ]
    )

    edge_pts = list(board_poly.exterior.coords)[:-1]
    for p1, p2 in zip(edge_pts, edge_pts[1:] + edge_pts[:1]):
        board.graphicItems.append(
            GrLine(start=Position(X=p1[0], Y=p1[1]), end=Position(X=p2[0], Y=p2[1]), layer="Edge.Cuts", width=0.1)
        )

    net_names = sorted({n for n in spec.get("nets", {}).keys() if n})
    gnd_first = [n for n in net_names if _is_gnd_net(n)]
    others = [n for n in net_names if not _is_gnd_net(n)]
    if "GND" not in gnd_first:
        gnd_first.insert(0, "GND")
    ordered_nets = gnd_first + others

    board.nets = [Net(number=0, name="")]
    net_map: dict[str, Net] = {}
    for idx, nname in enumerate(ordered_nets, start=1):
        net_obj = Net(number=idx, name=nname)
        board.nets.append(net_obj)
        net_map[nname] = net_obj

    for comp in spec["components"]:
        ref = comp["refdes"]
        fx, fy, fang = placed[ref]
        fp = Footprint()
        fp.libraryNickname = "cloud_pcb_blind"
        fp.entryName = comp.get("footprint_name", ref)
        fp.layer = 'B.Cu' if comp.get('side')=='bottom' else 'F.Cu'
        fp.position = Position(X=fx, Y=fy, angle=fang)
        fp.properties = {"Reference": ref, "Value": comp.get("value", "")}
        envelope=component_envelope(comp)
        fp.properties['MCAD_Envelope']='family='+envelope['family']+';bbox_mm='+','.join(f'{v:.4f}' for v in envelope['bbox_mm'])+f";height_mm={envelope['height_mm']:.4f};verified={str(envelope['verified']).lower()};height_source="+envelope['height_source']
        bx0, by0, bx1, by1 = _comp_local_bbox(comp)
        eff = Effects(font=Font(width=0.6, height=0.6, thickness=0.12))
        fp.graphicItems = [
            FpText(type="reference", text=ref, position=Position(X=0.0, Y=by0 - 0.8), layer="F.SilkS", effects=eff),
            FpText(type="value", text=comp.get("value", ""), position=Position(X=0.0, Y=by1 + 0.8), layer="F.Fab", effects=eff),
            FpRect(start=Position(X=bx0 - 0.15, Y=by0 - 0.15), end=Position(X=bx1 + 0.15, Y=by1 + 0.15), layer="F.CrtYd", stroke=Stroke(width=0.12)),
        ]
        for pspec in comp["pads"]:
            pad = Pad()
            pad.number = str(pspec["num"])
            pad.type = pspec.get("type", "smd")
            pad.shape = pspec.get("shape", "rect")
            # KiCad stores pad angle as board-absolute orientation
            flip=-1 if fp.layer=='B.Cu' else 1
            pad.position = Position(X=flip*float(pspec["x_rel_mm"]), Y=float(pspec["y_rel_mm"]), angle=fang+flip*float(pspec.get('angle_rel_deg',0)))
            pad.size = Position(X=float(pspec["w_mm"]), Y=float(pspec["h_mm"]))
            pad.pinType=pspec.get('pin_type');pad.pinFunction=pspec.get('pin_function')
            if 'roundrect_ratio' in pspec: pad.roundrectRatio=float(pspec['roundrect_ratio'])
            if pad.type in ("thru_hole", "np_thru_hole"):
                pad.drill = DrillDefinition(diameter=float(pspec.get("drill_mm") or 0.9))
                if pspec.get('drill_shape')=='oval':
                    pad.drill.oval=True;pad.drill.diameter=float(pspec['drill_width_mm']);pad.drill.width=float(pspec['drill_height_mm'])
                if 'drill_offset_mm' in pspec:
                    dx,dy=pspec['drill_offset_mm'];pad.drill.offset=Position(X=flip*dx,Y=dy)
                pad.layers = ["*.Cu", "*.Mask"]
            else:
                pad.layers = list(pspec.get("layers") or ["F.Cu", "F.Paste", "F.Mask"])
                if fp.layer=='B.Cu': pad.layers=[l.replace('F.','B.') for l in pad.layers]
            nname = pspec.get("net")
            if nname and nname in net_map:
                pad.net = net_map[nname]
            fp.pads.append(pad)
        board.footprints.append(fp)

    keepouts: list[dict[str, Any]] = []
    for idx, mh in enumerate(spec.get("mounting_holes", []), start=1):
        mx = float(mh["x_mm"]) - ox
        my = float(mh["y_mm"]) - oy
        drill = float(mh["drill_mm"])
        fp = Footprint()
        fp.libraryNickname = "cloud_pcb_blind"
        fp.entryName = f"MountingHole_{drill}mm"
        fp.layer = "F.Cu"
        fp.position = Position(X=mx, Y=my, angle=0.0)
        fp.properties = {"Reference": f"H{idx}", "Value": f"M_{drill}mm"}
        pad = Pad()
        pad.number = "1"
        pad.type = "np_thru_hole"
        pad.shape = "circle"
        pad.position = Position(X=0.0, Y=0.0)
        pad.size = Position(X=drill, Y=drill)
        pad.drill = DrillDefinition(diameter=drill)
        pad.layers = ["*.Cu", "*.Mask"]
        fp.pads.append(pad)
        board.footprints.append(fp)

        kr = drill / 2.0 + 0.55
        keepouts.append({"x_min": mx - kr, "y_min": my - kr, "x_max": mx + kr, "y_max": my + kr, "layers": ["F.Cu", "B.Cu"]})

    # Net classes & impedance calibration
    net_to_class: dict[str, str] = {}
    dreq = spec.get("design_requirements", {})
    for pr in dreq.get("power_rails", []):
        if not _is_gnd_net(pr["net"]):
            net_to_class[pr["net"]] = "POWER"
    diff_targets: list[float] = []
    for dp in dreq.get("differential_pairs", []):
        net_to_class[dp["pos_net"]] = "DIFF_PAIR"
        net_to_class[dp["neg_net"]] = "DIFF_PAIR"
        if dp.get("target_diff_impedance_ohm"):
            diff_targets.append(float(dp["target_diff_impedance_ohm"]))
    for hsn in dreq.get("high_speed_or_clock_nets", []):
        net_to_class.setdefault(hsn, "HIGH_SPEED")
    for nname in ordered_nets:
        if not _is_gnd_net(nname) and _is_power_net(nname):
            net_to_class.setdefault(nname, "POWER")

    target_zdiff = diff_targets[0] if diff_targets else 100.0
    diff_w, diff_gap = _calibrate_diff_pair_geometry(target_zdiff)

    # Pitch-aware clearance clamping so fine-pitch pads (e.g. 0.5mm pitch) never cause unfixable violations
    min_gaps = _compute_min_pad_gap_by_class(spec, net_to_class)
    is_ultra_fine = min(min_gaps.values(), default=0.25) <= 0.22

    def _safe_clr(cls_name: str, desired: float) -> float:
        phys = min_gaps.get(cls_name, 0.25)
        return round(min(desired, max(0.125, phys - 0.002)), 3)

    des_clr = 0.130 if is_ultra_fine else 0.150
    sig_w = 0.16 if is_ultra_fine else 0.20
    pwr_width = 0.25 if is_ultra_fine else (0.35 if min_gaps.get("POWER", 0.30) < 0.28 else 0.42)
    via_pad = 0.50 if is_ultra_fine else 0.55
    via_drill = 0.25 if is_ultra_fine else 0.30

    netclasses = {
        "POWER": NetClassRule(name="POWER", trace_width_mm=pwr_width, clearance_mm=_safe_clr("POWER", des_clr), via_drill_mm=via_drill, via_pad_mm=via_pad, max_current_a=2.0, zero_via_allowed=True),
        "DIFF_PAIR": NetClassRule(name="DIFF_PAIR", trace_width_mm=diff_w, clearance_mm=_safe_clr("DIFF_PAIR", max(des_clr, diff_gap)), via_drill_mm=via_drill, via_pad_mm=via_pad, max_current_a=0.3, zero_via_allowed=True),
        "HIGH_SPEED": NetClassRule(name="HIGH_SPEED", trace_width_mm=sig_w, clearance_mm=_safe_clr("HIGH_SPEED", des_clr), via_drill_mm=via_drill, via_pad_mm=via_pad, max_current_a=0.3, zero_via_allowed=True),
        "DEFAULT": NetClassRule(name="DEFAULT", trace_width_mm=sig_w, clearance_mm=_safe_clr("DEFAULT", des_clr), via_drill_mm=via_drill, via_pad_mm=via_pad, max_current_a=0.5, zero_via_allowed=True),
    }

    netclasses,net_to_class,_=specialize_power_rules(spec,netclasses,net_to_class)
    from dataclasses import replace
    for i,pair in enumerate(dreq.get('differential_pairs',[])):
        width,gap=_calibrate_diff_pair_geometry(float(pair.get('target_diff_impedance_ohm',100)))
        name=f'DIFF_PAIR_{i:03d}'
        netclasses[name]=replace(netclasses['DIFF_PAIR'],name=name,trace_width_mm=width,diff_pair_gap_mm=gap,impedance_ohm=float(pair.get('target_diff_impedance_ohm',100)))
        net_to_class[pair['pos_net']]=name;net_to_class[pair['neg_net']]=name
    rf_regions,_=rf_keepouts(spec,placed)
    keepouts.extend(rf_regions)
    from kiutils.items.zones import KeepoutSettings
    for ko in rf_regions:
        zone=Zone(); zone.layers=list(ko['layers']); zone.name='RF_antenna'
        zone.keepoutSettings=KeepoutSettings(tracks='not_allowed',vias='not_allowed',pads='not_allowed',copperpour='not_allowed',footprints='allowed')
        zone.polygons=[ZonePolygon(coordinates=[Coordinate(X=x,Y=y) for x,y in ko['polygon_mm']])]
        board.zones.append(zone)

    return board, netclasses, net_to_class, keepouts, board_poly


def _pad_world_polygon_kicad(fp: Footprint, pad: Pad) -> Any:
    """Return Shapely polygon of pad in KiCad world frame (Y-down), supporting oval & roundrect."""
    fx, fy = float(fp.position.X), float(fp.position.Y)
    fang = float(fp.position.angle or 0.0)
    dx, dy = _rotate_kicad(float(pad.position.X), float(pad.position.Y), fang)
    gx, gy = fx + dx, fy + dy
    sx, sy = float(pad.size.X), float(pad.size.Y)
    pang = float(pad.position.angle or 0.0)
    if pad.shape == "circle":
        return ShpPoint(gx, gy).buffer(max(sx / 2.0, 1e-4))
    if pad.shape == "oval":
        r = max(min(sx, sy) / 2.0, 1e-4)
        if sx >= sy:
            ex = max(sx / 2.0 - r, 0.0)
            g = ShpLineString([(gx - ex, gy), (gx + ex, gy)]).buffer(r)
        else:
            ey = max(sy / 2.0 - r, 0.0)
            g = ShpLineString([(gx, gy - ey), (gx, gy + ey)]).buffer(r)
        return shp_rotate(g, -pang, origin=(gx, gy)) if abs(pang) > 1e-6 else g
    if pad.shape == "roundrect":
        rr = min(sx, sy) * float(getattr(pad, "roundrectRatio", None) or 0.25)
        rr = max(0.0, min(rr, min(sx, sy) / 2.0 - 1e-4))
        g = shp_box(gx - sx / 2.0 + rr, gy - sy / 2.0 + rr, gx + sx / 2.0 - rr, gy + sy / 2.0 - rr).buffer(rr) if rr > 1e-4 else shp_box(gx - sx / 2.0, gy - sy / 2.0, gx + sx / 2.0, gy + sy / 2.0)
        return shp_rotate(g, -pang, origin=(gx, gy)) if abs(pang) > 1e-6 else g
    g = shp_box(gx - sx / 2.0, gy - sy / 2.0, gx + sx / 2.0, gy + sy / 2.0)
    return shp_rotate(g, -pang, origin=(gx, gy)) if abs(pang) > 1e-6 else g


def _add_gnd_fanout_vias(
    board: Board,
    board_poly: ShpPolygon,
    keepouts: list[dict[str, Any]],
    channel_corridors: Any = None,
    *, octagonal_keepouts: bool = False,
) -> tuple[set[str], dict[str, list[str]], dict[str, Any]]:
    """Place verified outward SMD GND fanout vias (and interior thermal EPAD vias) to In1.Cu/In2.Cu."""
    gnd_net_obj = next((n for n in board.nets if _is_gnd_net(n.name)), None)
    if gnd_net_obj is None:
        return set(), {}, {"fanout_vias_added": 0, "unvia_gnd_pins": []}

    gnd_net_num = int(gnd_net_obj.number)
    gnd_net_name = str(gnd_net_obj.name)
    safe_zone = board_poly.buffer(-0.65)
    rf_obstacles=[shp_box(k['x_min'],k['y_min'],k['x_max'],k['y_max']) for k in keepouts if k.get('kind')=='rf']
    if rf_obstacles: safe_zone=safe_zone.difference(unary_union(rf_obstacles))

    non_gnd_pads: list[Any] = []
    all_smd_pads: list[Any] = []
    hole_obst: list[Any] = []
    gnd_smd_pads: list[dict[str, Any]] = []
    gnd_th_pins: list[str] = []
    fp_grounded_geoms: dict[int, list[Any]] = defaultdict(list)

    for idx, fp in enumerate(board.footprints):
        ref = str(fp.properties.get("Reference", f"FP{idx}")) if isinstance(fp.properties, dict) else f"FP{idx}"
        fx, fy = float(fp.position.X), float(fp.position.Y)
        fang = float(fp.position.angle or 0.0)

        cu_pads = [pad for pad in fp.pads if _is_copper_pad(pad)]
        fp_pad_polys: list[Any] = []
        fp_non_gnd_centers: list[tuple[float, float]] = []
        fp_cu_local_pts: list[tuple[float, float]] = []

        for pad in cu_pads:
            pg = _pad_world_polygon_kicad(fp, pad)
            fp_pad_polys.append(pg)
            if pad.type == "np_thru_hole":
                d = float(pad.drill.diameter) if pad.drill and pad.drill.diameter else 2.2
                from cloud_pcb.general_solver.geometry import drill_geometry
                hole_obst.append(drill_geometry(fp,pad).buffer(.50))
                continue
            fp_cu_local_pts.append((float(pad.position.X), float(pad.position.Y)))
            pnet = pad.net.name if pad.net else ""
            if pad.type == "thru_hole":
                if _is_gnd_net(pnet):
                    gnd_th_pins.append(f"{ref}-{pad.number}")
                    fp_grounded_geoms[idx].append(pg)
                else:
                    non_gnd_pads.append(pg)
                    hole_obst.append(pg)
                    dx, dy = _rotate_kicad(float(pad.position.X), float(pad.position.Y), fang)
                    fp_non_gnd_centers.append((fx + dx, fy + dy))
            else:
                all_smd_pads.append(pg)
                if not _is_gnd_net(pnet):
                    non_gnd_pads.append(pg)
                    dx, dy = _rotate_kicad(float(pad.position.X), float(pad.position.Y), fang)
                    fp_non_gnd_centers.append((fx + dx, fy + dy))

        ic_interior = None
        if len(cu_pads) >= 4 and fp_pad_polys:
            b = unary_union(fp_pad_polys).bounds
            if b[2] - b[0] > 1.0 and b[3] - b[1] > 1.0:
                ic_interior = shp_box(b[0] + 0.15, b[1] + 0.15, b[2] - 0.15, b[3] - 0.15)

        if len(fp_cu_local_pts) >= 6:
            fp_min_x = min(pt[0] for pt in fp_cu_local_pts)
            fp_max_x = max(pt[0] for pt in fp_cu_local_pts)
            fp_min_y = min(pt[1] for pt in fp_cu_local_pts)
            fp_max_y = max(pt[1] for pt in fp_cu_local_pts)
        else:
            fp_min_x = fp_max_x = fp_min_y = fp_max_y = 0.0

        for pad in cu_pads:
            if pad.type == "smd" and pad.net and _is_gnd_net(pad.net.name):
                lx, ly = float(pad.position.X), float(pad.position.Y)
                dx, dy = _rotate_kicad(lx, ly, fang)
                px, py = fx + dx, fy + dy
                sx, sy = float(pad.size.X), float(pad.size.Y)
                pg = _pad_world_polygon_kicad(fp, pad)
                is_interior_epad = (
                    len(fp_cu_local_pts) >= 6
                    and sx >= 1.6
                    and sy >= 1.6
                    and (fp_min_x + 0.35 <= lx <= fp_max_x - 0.35)
                    and (fp_min_y + 0.35 <= ly <= fp_max_y - 0.35)
                )
                min_ng_dist = (
                    min((math.hypot(px - nx, py - ny) for nx, ny in fp_non_gnd_centers), default=9.9)
                )
                gnd_smd_pads.append({
                    "fp_idx": idx,
                    "ref": ref,
                    "pnum": str(pad.number),
                    "px": px,
                    "py": py,
                    "fcx": fx,
                    "fcy": fy,
                    "pad_r": max(sx, sy) / 2.0,
                    "sx": sx,
                    "sy": sy,
                    "pg": pg,
                    "ic_interior": ic_interior,
                    "is_interior_epad": is_interior_epad,
                    "is_fine_pitch": (ic_interior is not None and min_ng_dist < 0.75),
                    "layer": 'B.Cu' if 'B.Cu' in pad.layers and 'F.Cu' not in pad.layers else 'F.Cu',
                })

    # Place largest/interior thermal pads first so overlapping sub-pads inherit connectivity
    gnd_smd_pads.sort(key=lambda d: (0 if d["is_interior_epad"] else 1, -(d["sx"] * d["sy"]), d["ref"], d["pnum"]))

    non_gnd_union = unary_union(non_gnd_pads) if non_gnd_pads else None
    all_smd_union = unary_union(all_smd_pads) if all_smd_pads else None
    hole_union = unary_union(hole_obst) if hole_obst else None
    placed_via_pts: list[tuple[float, float]] = []
    unvia_pins: list[str] = []

    via_pad_d = 0.55
    via_drill_d = 0.30
    via_r = via_pad_d / 2.0
    guard_area_reclaimed = 0.0

    def add_via_guard(vx,vy,radius,layers):
        nonlocal guard_area_reclaimed
        if octagonal_keepouts:
            from cloud_pcb.general_solver.routing_guards import conservative_via_guard
            guard=conservative_via_guard(vx,vy,radius,layers)
            guard_area_reclaimed += max(0.0,4*radius*radius-ShpPolygon(guard['polygon_mm']).area)
        else:
            guard=dict(x_min=round(vx-radius,3),y_min=round(vy-radius,3),x_max=round(vx+radius,3),y_max=round(vy+radius,3),layers=layers)
        keepouts.append(guard)

    for rec in gnd_smd_pads:
        fp_idx = rec["fp_idx"]
        ref = rec["ref"]
        pnum = rec["pnum"]
        px, py = rec["px"], rec["py"]
        fcx, fcy = rec["fcx"], rec["fcy"]
        pg = rec["pg"]
        ic_interior = rec["ic_interior"]

        # 0. Already connected via overlapping GND through-hole vias or overlapping GND SMD pad on same footprint
        if any(pg.intersects(gg) for gg in fp_grounded_geoms[fp_idx]):
            continue

        # 1. Center thermal EPAD on multi-pin IC: place thermal via directly inside EPAD
        if rec["is_interior_epad"]:
            ep_pad_d = 0.50
            ep_drill_d = 0.25
            ep_r = ep_pad_d / 2.0
            chosen_ep: tuple[float, float] | None = None
            for ox_off, oy_off in ((0.0, 0.0), (0.35, 0.0), (-0.35, 0.0), (0.0, 0.35), (0.0, -0.35)):
                vx, vy = round(px + ox_off, 3), round(py + oy_off, 3)
                vp_pt = ShpPoint(vx, vy)
                if not safe_zone.contains(vp_pt):
                    continue
                vp_geom = vp_pt.buffer(ep_r)
                if non_gnd_union is not None and vp_geom.distance(non_gnd_union) < 0.18:
                    continue
                if hole_union is not None and vp_geom.distance(hole_union) < 0.20:
                    continue
                if any(math.hypot(vx - oxv, vy - oyv) < 0.65 for oxv, oyv in placed_via_pts):
                    continue
                chosen_ep = (vx, vy)
                break
            if chosen_ep is not None:
                vx, vy = chosen_ep
                placed_via_pts.append((vx, vy))
                fp_grounded_geoms[fp_idx].append(pg)
                board.traceItems.append(
                    Via(
                        position=Position(X=vx, Y=vy),
                        size=ep_pad_d,
                        drill=ep_drill_d,
                        layers=["F.Cu", "B.Cu"],
                        net=gnd_net_num,
                        tstamp=str(uuid.uuid4()),
                    )
                )
                kr = ep_r + 0.145
                add_via_guard(vx,vy,kr,["B.Cu", "In1.Cu", "In2.Cu"])
                continue

        # 2. Check if an already-grounded pad/EPAD on the SAME footprint (e.g. QFN center EPAD) is within short bridge reach
        bridged_xy: tuple[float, float] | None = None
        for gg in fp_grounded_geoms[fp_idx]:
            d_gg = pg.distance(gg)
            if d_gg <= 0.85:
                # Find nearest point on gg to (px, py)
                from shapely.ops import nearest_points
                _, np_gg = nearest_points(ShpPoint(px, py), gg)
                bx, by = round(float(np_gg.x), 3), round(float(np_gg.y), 3)
                if math.hypot(px - bx, py - by) <= 1.25:
                    stub = ShpLineString([(px, py), (bx, by)]).buffer(0.095)
                    if non_gnd_union is None or stub.distance(non_gnd_union) >= 0.150:
                        bridged_xy = (bx, by)
                        break
        if bridged_xy is not None:
            fp_grounded_geoms[fp_idx].append(pg)
            board.traceItems.append(
                Segment(
                    start=Position(X=px, Y=py),
                    end=Position(X=bridged_xy[0], Y=bridged_xy[1]),
                    width=0.18,
                    layer=rec["layer"],
                    net=gnd_net_num,
                    tstamp=str(uuid.uuid4()),
                )
            )
            continue

        # 3. Check if an already-placed GND fanout via is right next to this pad (e.g. adjacent GND pins)
        shared_via = None
        for oxv, oyv in placed_via_pts:
            if math.hypot(px - oxv, py - oyv) <= 0.95:
                stub = ShpLineString([(px, py), (oxv, oyv)]).buffer(0.12)
                if non_gnd_union is None or stub.distance(non_gnd_union) >= 0.155:
                    shared_via = (oxv, oyv)
                    break
        if shared_via is not None:
            fp_grounded_geoms[fp_idx].append(pg)
            board.traceItems.append(
                Segment(
                    start=Position(X=px, Y=py),
                    end=Position(X=shared_via[0], Y=shared_via[1]),
                    width=0.22,
                    layer=rec["layer"],
                    net=gnd_net_num,
                    tstamp=str(uuid.uuid4()),
                )
            )
            continue

        # 3. Search outward fanout via position scaled by pad extent & fine-pitch clearance
        out_ang = math.atan2(py - fcy, px - fcx) if math.hypot(px - fcx, py - fcy) > 0.05 else 0.0
        dirs = [out_ang + k * (math.pi / 4.0) for k in ((0, 1, -1) if ic_interior is not None else (0, 1, -1, 2, -2))]

        pad_r = rec["pad_r"]
        if rec["is_fine_pitch"]:
            dists = [round(max(1.28, pad_r + 0.85) + off, 3) for off in (0.0, 0.22, 0.45, 0.70, 1.10)]
            req_ng_via = 0.28
        else:
            d0 = max(0.85, pad_r + 0.48)
            dists = [round(d0 + off, 3) for off in (0.0, 0.18, 0.40, 0.65)]
            req_ng_via = 0.165

        chosen_xy: tuple[float, float] | None = None
        best_channel_cost=float('inf')
        for dist in dists:
            for ang in dirs:
                vx = round(px + dist * math.cos(ang), 3)
                vy = round(py + dist * math.sin(ang), 3)
                vp_pt = ShpPoint(vx, vy)
                if not safe_zone.covers(vp_pt.buffer(via_r)):
                    continue
                if ic_interior is not None and ic_interior.contains(vp_pt):
                    continue
                vp_geom = vp_pt.buffer(via_r)
                # Ensure via pad NEVER overlaps any SMD solder pad (zero via-in-pad)
                if all_smd_union is not None and vp_geom.distance(all_smd_union) < 0.08:
                    continue
                stub_geom = ShpLineString([(px, py), (vx, vy)]).buffer(0.125)
                if non_gnd_union is not None:
                    if vp_geom.distance(non_gnd_union) < req_ng_via:
                        continue
                    if stub_geom.distance(non_gnd_union) < 0.16:
                        continue
                full_geom = unary_union([vp_geom, stub_geom])
                if hole_union is not None and full_geom.distance(hole_union) < 0.22:
                    continue
                if any(math.hypot(vx - oxv, vy - oyv) < 0.72 for oxv, oyv in placed_via_pts):
                    continue
                channel_cost=dist+(40*full_geom.intersection(channel_corridors).area if channel_corridors is not None else 0)
                if channel_cost<best_channel_cost:
                    best_channel_cost=channel_cost
                    chosen_xy = (vx, vy)

        if chosen_xy is not None:
            vx, vy = chosen_xy
            placed_via_pts.append((vx, vy))
            fp_grounded_geoms[fp_idx].append(pg)
            board.traceItems.append(
                Segment(
                    start=Position(X=px, Y=py),
                    end=Position(X=vx, Y=vy),
                    width=0.22,
                    layer=rec["layer"],
                    net=gnd_net_num,
                    tstamp=str(uuid.uuid4()),
                )
            )
            board.traceItems.append(
                Via(
                    position=Position(X=vx, Y=vy),
                    size=via_pad_d,
                    drill=via_drill_d,
                    layers=["F.Cu", "B.Cu"],
                    net=gnd_net_num,
                    tstamp=str(uuid.uuid4()),
                )
            )
            kr = via_r + 0.145
            add_via_guard(vx,vy,kr,["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"])
        else:
            unvia_pins.append(f"{ref}-{pnum}")

    skip_nets: set[str] = set()
    override_pins: dict[str, list[str]] = {}
    if not unvia_pins:
        skip_nets.add(gnd_net_name)
    else:
        grounded_pins=[f"{p['ref']}-{p['pnum']}" for p in gnd_smd_pads if f"{p['ref']}-{p['pnum']}" not in unvia_pins]
        anchor = gnd_th_pins[0] if gnd_th_pins else (grounded_pins[0] if grounded_pins else None)
        if anchor:
            override_pins[gnd_net_name] = unvia_pins + [anchor]
        elif len(unvia_pins) >= 2:
            override_pins[gnd_net_name] = unvia_pins
        else:
            skip_nets.add(gnd_net_name)

    # The router must see the pre-routed STUBS as well as their via pads.
    # Wire/via-only keepouts protect their physical copper without falsely
    # treating same-net footprint pads as foreign copper obstacles.
    ground_pads=unary_union([_pad_world_polygon_kicad(fp,p) for fp in board.footprints for p in fp.pads if p.net and p.net.number==gnd_net_num and _is_copper_pad(p)])
    for item in board.traceItems:
        if not isinstance(item,Segment) or item.net!=gnd_net_num: continue
        copper=ShpLineString([(item.start.X,item.start.Y),(item.end.X,item.end.Y)]).buffer(item.width/2+.001)
        for kind,geom in (('wire_keepout',copper.difference(ground_pads)),('via_keepout',copper)):
            pieces=list(geom.geoms) if hasattr(geom,'geoms') else [geom]
            for piece in pieces:
                if piece.is_empty or piece.geom_type!='Polygon':continue
                for polygon in _slit_polygon_no_holes(piece):
                    x0,y0,x1,y1=polygon.bounds
                    keepouts.append(dict(kind='fixed_ground_stub',dsn_kind=kind,x_min=x0,y_min=y0,x_max=x1,y_max=y1,layers=[item.layer],polygon_mm=list(polygon.exterior.coords)))
    return skip_nets, override_pins, {
        "fanout_vias_added": len(placed_via_pts),
        "gnd_smd_pads_total": len(gnd_smd_pads),
        "unvia_gnd_pins": unvia_pins,
        "via_guard_geometry": 'circumscribed_octagon' if octagonal_keepouts else 'legacy_square',
        "via_guard_area_reclaimed_sum_mm2": round(guard_area_reclaimed,6),
        "via_guard_area_statistic": 'sum over vias, not union area; original protection radii unchanged',
    }


def _slit_polygon_no_holes(poly: ShpPolygon, depth: int = 0) -> list[ShpPolygon]:
    """Partition holes with ZERO-width cuts; never etch artificial plane slots.

    The previous buffered cuts created 1 um gaps across entire planes, which
    projected LVS concealed. Adjacent pieces now share their exact boundary.
    """
    pending=[poly]; out=[]
    while pending:
        current=pending.pop()
        if current.is_empty: continue
        if not current.interiors:
            out.append(current); continue
        ring=ShpPolygon(current.interiors[0]); cx=ring.representative_point().x
        minx,miny,maxx,maxy=current.bounds
        pieces=list(shp_split(current,ShpLineString([(cx,miny-1),(cx,maxy+1)])).geoms)
        if len(pieces)<2: raise RuntimeError('unable to partition plane hole without loss of copper')
        pending.extend(p for p in pieces if p.geom_type=='Polygon' and p.area>1e-10)
    return out


def _build_4layer_pours_and_stitching(
    board: Board,
    board_poly: ShpPolygon,
    edge_margin: float = 0.50,
    keepouts: list[dict[str, Any]] | None = None,
    islands: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build In1.Cu/In2.Cu solid GND planes + F.Cu/B.Cu GND pours + stitching via fence."""
    gnd_net_obj = next((n for n in board.nets if _is_gnd_net(n.name)), None)
    gnd_net_num = int(gnd_net_obj.number) if gnd_net_obj else 1
    gnd_net_name = str(gnd_net_obj.name) if gnd_net_obj else "GND"
    base_poly = board_poly.buffer(-edge_margin)
    rf_geoms=[shp_box(k['x_min'],k['y_min'],k['x_max'],k['y_max']) for k in keepouts or [] if k.get('kind')=='rf']
    copper_base=base_poly.difference(unary_union(rf_geoms)) if rf_geoms else base_poly

    # 1. Inner planes (In1.Cu / In2.Cu): subtract NPTH + non-GND PTH/vias
    inner_cutouts: list[Any] = []
    inner_layer_cutouts: dict[str,list[Any]]={'In1.Cu':[],'In2.Cu':[]}
    outer_cutouts: dict[str, list[Any]] = {"F.Cu": [], "B.Cu": []}
    gnd_bridges: list[Any] = []
    existing_vias: list[tuple[float, float]] = []
    non_gnd_copper_all: list[Any] = []

    for fp in board.footprints:
        fx, fy = float(fp.position.X), float(fp.position.Y)
        fang = float(fp.position.angle or 0.0)
        for pad in fp.pads:
            if pad.type != "np_thru_hole" and not _is_copper_pad(pad):
                continue
            dx, dy = _rotate_kicad(float(pad.position.X), float(pad.position.Y), fang)
            px, py = fx + dx, fy + dy
            if pad.type == "np_thru_hole":
                drill_d = float(pad.drill.diameter) if pad.drill and pad.drill.diameter else 2.2
                from cloud_pcb.general_solver.geometry import drill_geometry
                c = drill_geometry(fp,pad).buffer(.50)
                inner_cutouts.append(c)
                outer_cutouts["F.Cu"].append(c)
                outer_cutouts["B.Cu"].append(c)
                non_gnd_copper_all.append(c)
                continue
            pg = _pad_world_polygon_kicad(fp, pad)
            pnet = int(pad.net.number) if pad.net else 0
            if pad.type == "thru_hole":
                if pnet != gnd_net_num:
                    inner_cutouts.append(pg.buffer(0.25))
                    c_out = pg.buffer(0.24)
                    outer_cutouts["F.Cu"].append(c_out)
                    outer_cutouts["B.Cu"].append(c_out)
                    non_gnd_copper_all.append(pg)
                else:
                    gnd_bridges.append(pg)
                    non_gnd_copper_all.append(pg)
            else:
                lyr = "B.Cu" if pad.layers and "B.Cu" in pad.layers and "F.Cu" not in pad.layers else "F.Cu"
                non_gnd_copper_all.append(pg)
                if pnet != gnd_net_num:
                    outer_cutouts[lyr].append(pg.buffer(0.24))

    for item in board.traceItems:
        if isinstance(item, Via):
            vx, vy = float(item.position.X), float(item.position.Y)
            existing_vias.append((vx, vy))
            if int(item.net) != gnd_net_num:
                r_in = float(item.size) / 2.0 + 0.25
                c_in = ShpPoint(vx, vy).buffer(r_in, resolution=24)
                inner_cutouts.append(c_in)
                outer_cutouts["F.Cu"].append(c_in)
                outer_cutouts["B.Cu"].append(c_in)
                non_gnd_copper_all.append(ShpPoint(vx, vy).buffer(float(item.size) / 2.0))
            else:
                gnd_bridges.append(ShpPoint(vx, vy).buffer(float(item.size) / 2.0))
        elif isinstance(item, Segment) and item.layer in ("F.Cu", "B.Cu"):
            if int(item.net) != gnd_net_num:
                ls = ShpLineString([(float(item.start.X), float(item.start.Y)), (float(item.end.X), float(item.end.Y))])
                w_half = max(float(item.width) / 2.0, 0.05)
                outer_cutouts[item.layer].append(ls.buffer(w_half + 0.22))
                non_gnd_copper_all.append(ls.buffer(w_half))
        elif isinstance(item,Segment) and item.layer in inner_layer_cutouts and int(item.net)!=gnd_net_num:
            ls=ShpLineString([(float(item.start.X),float(item.start.Y)),(float(item.end.X),float(item.end.Y))])
            # Polygonized circular caps must conservatively contain the intended
            # clearance even after 0.1 um coordinate serialization.
            inner_layer_cutouts[item.layer].append(ls.buffer(float(item.width)/2+0.221,resolution=32))

    inner_pour = copper_base.difference(unary_union(inner_cutouts)) if inner_cutouts else copper_base

    # 2. Outer layer pours (F.Cu / B.Cu) - keep only non-floating islands (>= 2.0 mm2 and touching GND via/TH)
    def _clean_outer_pour(lyr: str) -> Any:
        raw = copper_base.difference(unary_union(outer_cutouts[lyr])) if outer_cutouts[lyr] else copper_base
        if raw.is_empty:
            return ShpPolygon()
        br_u = unary_union(gnd_bridges) if gnd_bridges else None
        pieces = list(raw.geoms) if raw.geom_type == "MultiPolygon" else [raw]
        kept = []
        for p in pieces:
            if p.area < 2.0:
                continue
            if br_u is not None and not p.intersects(br_u):
                continue
            kept.append(p)
        return unary_union(kept) if kept else ShpPolygon()

    f_pour = _clean_outer_pour("F.Cu")
    b_pour = _clean_outer_pour("B.Cu")

    # 3. GND Stitching Via Fence / Grid in open poured zones
    stitch_added = 0
    non_gnd_u = unary_union(non_gnd_copper_all) if non_gnd_copper_all else None
    safe_inner_stitch = board_poly.buffer(-1.25)
    if islands:
        safe_inner_stitch=safe_inner_stitch.difference(unary_union([i['geometry'].buffer(.65) for i in islands]))
    if inner_layer_cutouts['In2.Cu']:
        safe_inner_stitch=safe_inner_stitch.difference(unary_union(inner_layer_cutouts['In2.Cu']).buffer(.4))
    minx, miny, maxx, maxy = safe_inner_stitch.bounds
    step = 6.5
    x = minx + 1.0
    while x <= maxx - 1.0:
        y = miny + 1.0
        while y <= maxy - 1.0:
            pt = ShpPoint(round(x, 2), round(y, 2))
            if safe_inner_stitch.contains(pt) and not f_pour.is_empty and not b_pour.is_empty:
                if f_pour.contains(pt) and b_pour.contains(pt):
                    ok_clear = (non_gnd_u is None) or (pt.distance(non_gnd_u) >= 0.65)
                    ok_via = not any(math.hypot(pt.x - oxv, pt.y - oyv) < 2.5 for oxv, oyv in existing_vias)
                    if ok_clear and ok_via:
                        board.traceItems.append(
                            Via(
                                position=Position(X=pt.x, Y=pt.y),
                                size=0.60,
                                drill=0.30,
                                layers=["F.Cu", "B.Cu"],
                                net=gnd_net_num,
                                tstamp=str(uuid.uuid4()),
                            )
                        )
                        existing_vias.append((pt.x, pt.y))
                        stitch_added += 1
            y += step
        x += step

    # Rebuild F.Cu/B.Cu clean pours now including newly added stitching vias so islands connect
    if stitch_added > 0:
        for vx, vy in existing_vias[-stitch_added:]:
            gnd_bridges.append(ShpPoint(vx, vy).buffer(0.30))
        f_pour = _clean_outer_pour("F.Cu")
        b_pour = _clean_outer_pour("B.Cu")

    pours = {
        "F.Cu": f_pour,
        "In1.Cu": inner_pour.difference(unary_union(inner_layer_cutouts['In1.Cu'])),
        "In2.Cu": inner_pour.difference(unary_union(inner_layer_cutouts['In2.Cu'])),
        "B.Cu": b_pour,
    }
    if islands:
        pours['In2.Cu']=pours['In2.Cu'].difference(unary_union([i['geometry'].buffer(0.25) for i in islands]))

    # Inject real KiCad Zone items with slit hole-free filledPolygons
    ext_pts = [Coordinate(X=round(px, 4), Y=round(py, 4)) for px, py in base_poly.exterior.coords]
    for lyr, geom in pours.items():
        if geom.is_empty:
            continue
        z = Zone()
        z.net = gnd_net_num
        z.netName = gnd_net_name
        z.layers = [lyr]
        z.name = f"GND_{lyr.replace('.', '_')}"
        z.clearance = 0.22
        z.minThickness = 0.20
        z.connectPads = "yes"
        z.fillSettings = FillSettings(yes=True, mode="solid", thermalGap=0.30, thermalBridgeWidth=0.35)
        z.polygons = [ZonePolygon(coordinates=ext_pts)]
        pieces = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        for piece in pieces:
            for slit_poly in _slit_polygon_no_holes(piece):
                coords = [Position(X=round(px, 4), Y=round(py, 4)) for px, py in slit_poly.exterior.coords]
                if len(coords) >= 3:
                    z.filledPolygons.append(FilledPolygon(layer=lyr, coordinates=coords))
        board.zones.append(z)

    for island in islands or []:
        z=Zone(); z.net=next(n.number for n in board.nets if n.name==island['net']); z.netName=island['net']; z.layers=['In2.Cu']; z.name='POWER_island'; z.connectPads='yes'; z.minThickness=0.20; z.clearance=0.25
        z.fillSettings=FillSettings(yes=True,mode='solid',thermalGap=0.30,thermalBridgeWidth=0.35)
        geom=island['geometry']; pieces=list(geom.geoms) if geom.geom_type=='MultiPolygon' else [geom]
        for piece in pieces:
            z.polygons.append(ZonePolygon(coordinates=[Coordinate(X=x,Y=y) for x,y in piece.exterior.coords]))
            for part in _slit_polygon_no_holes(piece): z.filledPolygons.append(FilledPolygon(layer='In2.Cu',coordinates=[Position(X=x,Y=y) for x,y in part.exterior.coords]))
        board.zones.append(z)

    board_area = float(board_poly.area)
    stats = {
        "board_area_mm2": round(board_area, 3),
        "pour_area_mm2": round(float(inner_pour.area), 3),
        "fill_ratio": round(float(inner_pour.area) / max(board_area, 1.0), 4),
        "f_cu_pour_mm2": round(float(f_pour.area), 3),
        "b_cu_pour_mm2": round(float(b_pour.area), 3),
        "stitching_vias_added": stitch_added,
        "antipad_cutouts": len(inner_cutouts),
        "is_empty": inner_pour.is_empty,
        "power_islands": [{k:v for k,v in i.items() if k!='geometry'}|{'area_mm2':i['geometry'].area} for i in islands or []],
    }
    # Export aggregate copper while KiCad zones retain individual net identity.
    if islands: pours['In2.Cu']=unary_union([pours['In2.Cu']]+[i['geometry'] for i in islands])
    return pours, stats


def _export_blind_gerbers(
    board: Board,
    board_poly: ShpPolygon,
    pours: dict[str, Any],
    H: float,
    gerber_dir: Path,
) -> list[str]:
    """Export RS-274X Gerbers + PTH/NPTH Excellon drills with rotated oval/roundrect pads."""
    gerber_dir.mkdir(parents=True, exist_ok=True)

    def _gy(yk: float) -> float:
        return H - yk

    layer_objs: dict[str, list[Any]] = {
        k: []
        for k in ("F_Cu", "In1_Cu", "In2_Cu", "B_Cu", "F_Mask", "B_Mask", "F_Paste", "B_Paste", "F_SilkS", "B_SilkS", "Edge_Cuts")
    }
    pth_objs: list[Flash] = []
    npth_objs: list[Flash] = []

    edge_ap = CircleAperture(0.10, unit=MM)
    epts = list(board_poly.exterior.coords)[:-1]
    for p1, p2 in zip(epts, epts[1:] + epts[:1]):
        layer_objs["Edge_Cuts"].append(Line(float(p1[0]), _gy(float(p1[1])), float(p2[0]), _gy(float(p2[1])), aperture=edge_ap, unit=MM))

    # 4-layer copper pours (F.Cu, In1.Cu, In2.Cu, B.Cu)
    for k_lyr, g_lyr in (("F.Cu", "F_Cu"), ("In1.Cu", "In1_Cu"), ("In2.Cu", "In2_Cu"), ("B.Cu", "B_Cu")):
        geom = pours.get(k_lyr)
        if geom is None or geom.is_empty:
            continue
        polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            if poly.area < 0.05:
                continue
            ext = [(float(x), _gy(float(y))) for x, y in poly.exterior.coords]
            layer_objs[g_lyr].append(Region(ext, unit=MM, polarity_dark=True))
            for ring in poly.interiors:
                layer_objs[g_lyr].append(Region([(float(x), _gy(float(y))) for x, y in ring.coords], unit=MM, polarity_dark=False))

    silk_ap = CircleAperture(0.15, unit=MM)
    mask_openings: list[Any] = []
    candidate_silk: list[tuple[tuple[float, float], tuple[float, float]]] = []

    for fp in board.footprints:
        fx, fy = float(fp.position.X), float(fp.position.Y)
        fang = float(fp.position.angle or 0.0)
        for gi in fp.graphicItems:
            if isinstance(gi, FpRect) and gi.layer in ("F.SilkS", "F.CrtYd"):
                corners_l = [
                    (float(gi.start.X), float(gi.start.Y)),
                    (float(gi.end.X), float(gi.start.Y)),
                    (float(gi.end.X), float(gi.end.Y)),
                    (float(gi.start.X), float(gi.end.Y)),
                ]
                wpts = []
                for lx, ly in corners_l:
                    rx, ry = _rotate_kicad(lx, ly, fang)
                    wpts.append((fx + rx, _gy(fy + ry)))
                for a, b in zip(wpts, wpts[1:] + wpts[:1]):
                    candidate_silk.append((a, b))

        for pad in fp.pads:
            rx, ry = _rotate_kicad(float(pad.position.X), float(pad.position.Y), fang)
            px, py = fx + rx, _gy(fy + ry)
            pw, ph = float(pad.size.X), float(pad.size.Y)
            pang = float(pad.position.angle or 0.0)
            def _drill_object(plated):
                dr=pad.drill;w=float(dr.diameter);h=float(dr.width or w) if dr.oval else w
                offset=dr.offset or Position(X=0,Y=0)
                oxp,oyp=_rotate_kicad(float(offset.X),float(offset.Y),pang)
                cx,cy=px+oxp,py-oyp
                if abs(w-h)<1e-8: return Flash(cx,cy,ExcellonTool(w,plated=plated,unit=MM),unit=MM)
                dx,dy=((abs(w-h)/2,0) if w>h else (0,abs(h-w)/2));dx,dy=_rotate_kicad(dx,dy,pang)
                return Line(cx-dx,cy+dy,cx+dx,cy-dy,aperture=ExcellonTool(min(w,h),plated=plated,unit=MM),unit=MM)
            if round(pang) % 180 == 90:
                pw, ph = ph, pw

            if pad.type == "np_thru_hole":
                drill_d = float(pad.drill.diameter) if pad.drill and pad.drill.diameter else 2.2
                npth_objs.append(_drill_object(False))
                mask_openings.append(ShpPoint(px, py).buffer(drill_d / 2.0 + 0.25))
                continue

            if pad.shape == "circle":
                ap_cu = CircleAperture(pw, unit=MM)
                ap_mask = CircleAperture(pw + 0.10, unit=MM)
                mask_openings.append(ShpPoint(px, py).buffer((pw + 0.10) / 2.0 + 0.20))
            elif pad.shape == "oval":
                ap_cu = ObroundAperture(pw, ph, unit=MM)
                ap_mask = ObroundAperture(pw + 0.10, ph + 0.10, unit=MM)
                mask_openings.append(shp_box(px - (pw + 0.5) / 2.0, py - (ph + 0.5) / 2.0, px + (pw + 0.5) / 2.0, py + (ph + 0.5) / 2.0))
            else:
                ap_cu = RectangleAperture(pw, ph, unit=MM)
                ap_mask = RectangleAperture(pw + 0.10, ph + 0.10, unit=MM)
                mask_openings.append(shp_box(px - (pw + 0.5) / 2.0, py - (ph + 0.5) / 2.0, px + (pw + 0.5) / 2.0, py + (ph + 0.5) / 2.0))

            def _pad_artwork(mask=False):
                if pad.shape=='roundrect' or abs(pang/90-round(pang/90))>1e-8:
                    geom=_pad_world_polygon_kicad(fp,pad)
                    if mask: geom=geom.buffer(.05)
                    return Region([(float(x),_gy(float(y))) for x,y in geom.exterior.coords],unit=MM)
                return Flash(px,py,ap_mask if mask else ap_cu,unit=MM)

            if pad.type == "thru_hole":
                layer_objs["F_Cu"].append(_pad_artwork())
                layer_objs["B_Cu"].append(_pad_artwork())
                layer_objs["F_Mask"].append(_pad_artwork(True))
                layer_objs["B_Mask"].append(_pad_artwork(True))
                if pad.net and _is_gnd_net(pad.net.name):
                    layer_objs["In1_Cu"].append(_pad_artwork())
                    layer_objs["In2_Cu"].append(_pad_artwork())
                drill_d = float(pad.drill.diameter) if pad.drill and pad.drill.diameter else 0.9
                pth_objs.append(_drill_object(True))
            elif not _is_copper_pad(pad):
                pad_lyrs = set(pad.layers or [])
                if "F.Paste" in pad_lyrs:
                    layer_objs["F_Paste"].append(_pad_artwork())
                if "B.Paste" in pad_lyrs:
                    layer_objs["B_Paste"].append(_pad_artwork())
            else:
                pad_lyrs = set(pad.layers or ["F.Cu"])
                if "B.Cu" in pad_lyrs and "F.Cu" not in pad_lyrs:
                    layer_objs["B_Cu"].append(_pad_artwork())
                    layer_objs["B_Mask"].append(_pad_artwork(True))
                    layer_objs["B_Paste"].append(_pad_artwork())
                else:
                    layer_objs["F_Cu"].append(_pad_artwork())
                    layer_objs["F_Mask"].append(_pad_artwork(True))
                    layer_objs["F_Paste"].append(_pad_artwork())

    mask_guard = unary_union(mask_openings) if mask_openings else None
    for a, b in candidate_silk:
        seg = ShpLineString([a, b])
        clipped = seg.difference(mask_guard) if mask_guard is not None else seg
        if clipped.is_empty:
            continue
        for g in (list(clipped.geoms) if hasattr(clipped, "geoms") else [clipped]):
            if g.geom_type == "LineString" and g.length >= 0.25:
                coords = list(g.coords)
                for p1, p2 in zip(coords, coords[1:]):
                    layer_objs["F_SilkS"].append(Line(float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]), aperture=silk_ap, unit=MM))

    gnd_net = next((n.number for n in board.nets if _is_gnd_net(n.name)), 1)
    for item in board.traceItems:
        if isinstance(item, Via):
            vx, vy = float(item.position.X), _gy(float(item.position.Y))
            ap_via = CircleAperture(float(item.size), unit=MM)
            layer_objs["F_Cu"].append(Flash(vx, vy, ap_via, unit=MM))
            layer_objs["B_Cu"].append(Flash(vx, vy, ap_via, unit=MM))
            if int(item.net) == gnd_net:
                layer_objs["In1_Cu"].append(Flash(vx, vy, ap_via, unit=MM))
                layer_objs["In2_Cu"].append(Flash(vx, vy, ap_via, unit=MM))
            pth_objs.append(Flash(vx, vy, ExcellonTool(float(item.drill), plated=True, unit=MM), unit=MM))
        elif isinstance(item, Segment):
            lname = item.layer.replace(".", "_")
            if lname in layer_objs:
                layer_objs[lname].append(
                    Line(float(item.start.X), _gy(float(item.start.Y)), float(item.end.X), _gy(float(item.end.Y)), aperture=CircleAperture(float(item.width), unit=MM), unit=MM)
                )

    written: list[str] = []
    for lname, objs in layer_objs.items():
        raw = GerberFile(objects=objs).write_to_bytes().decode("utf-8")
        cleaned = ["G75*" if ln == "G75" else (ln.replace("**", "*") if "**" in ln else ln) for ln in raw.splitlines()]
        (gerber_dir / f"{lname}.gbr").write_text("\n".join(cleaned) + "\n", encoding="utf-8")
        written.append(f"{lname}.gbr")

    ExcellonFile(objects=pth_objs).save(gerber_dir / "PTH.drl")
    ExcellonFile(objects=npth_objs).save(gerber_dir / "NPTH.drl")
    written.extend(["PTH.drl", "NPTH.drl"])
    return sorted(written)


def _pad_world_polygon(fp: Footprint, pad: Pad, H: float) -> Any:
    """Return Shapely polygon of pad in Gerber frame (Y-flipped)."""
    g_kicad = _pad_world_polygon_kicad(fp, pad)
    if g_kicad.geom_type == "Polygon":
        return ShpPolygon([(x, H - y) for x, y in g_kicad.exterior.coords])
    return g_kicad


def _audit_blind_output(
    board: Board,
    board_poly: ShpPolygon,
    pours: dict[str, Any],
    gerber_dir: Path,
    H: float,
) -> dict[str, Any]:
    """Run strict independent PyGerber + Gerbonara + Shapely LVS/DRC + GND continuity audit."""
    pygerber_ok: dict[str, bool] = {}
    cu_unions: dict[str, Any] = {}
    flashes: dict[str, list[Any]] = {}
    for gname in ("F_Cu.gbr", "In1_Cu.gbr", "In2_Cu.gbr", "B_Cu.gbr",
                  "F_Mask.gbr", "B_Mask.gbr", "F_Paste.gbr", "B_Paste.gbr",
                  "F_SilkS.gbr", "B_SilkS.gbr", "Edge_Cuts.gbr"):
        gp = gerber_dir / gname
        try:
            PyGerberFile.from_file(gp).parse().get_info()
            pygerber_ok[gname] = True
        except Exception:
            pygerber_ok[gname] = False
        u, fl, _ = _gerber_layer_union(gp)
        cu_unions[gname] = u
        flashes[gname] = fl

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        pth = ExcellonFile.open(gerber_dir / "PTH.drl")
        npth = ExcellonFile.open(gerber_dir / "NPTH.drl")

    from cloud_pcb.verify_gate.gerber_drc import audit_annular_geometry
    min_ar, ar_viols = audit_annular_geometry(pth,cu_unions['F_Cu.gbr'],cu_unions['B_Cu.gbr'])
    silk_mask_overlap_by_side = {
        side: float(cu_unions[f"{side}_SilkS.gbr"].intersection(cu_unions[f"{side}_Mask.gbr"]).area)
        for side in ("F", "B")
    }
    silk_mask_overlap = sum(silk_mask_overlap_by_side.values())

    g_poly = ShpPolygon([(x, H - y) for x, y in board_poly.exterior.coords])
    edge_margins: dict[str, float] = {}
    for lname in ("F_Cu.gbr", "In1_Cu.gbr", "In2_Cu.gbr", "B_Cu.gbr"):
        u = cu_unions[lname]
        if not u.is_empty:
            edge_margins[lname] = round(float(g_poly.exterior.distance(u)), 4)

    # Full 3D LVS connectivity including GND plane continuity across In1_Cu / In2_Cu / PTH / Vias
    net_geoms: dict[str, list[Any]] = defaultdict(list)
    net_by_num = {n.number: n.name for n in board.nets}
    gnd_names = {n.name for n in board.nets if _is_gnd_net(n.name)} or {"GND"}
    for fp in board.footprints:
        for pad in fp.pads:
            if not _is_copper_pad(pad) or not pad.net or not pad.net.name:
                continue
            pg = _pad_world_polygon(fp, pad, H)
            if pg is not None:
                net_geoms[pad.net.name].append(pg)
    for item in board.traceItems:
        if isinstance(item, Segment):
            nname = net_by_num.get(int(item.net), "")
            if nname:
                ls = ShpLineString([(float(item.start.X), H - float(item.start.Y)), (float(item.end.X), H - float(item.end.Y))])
                net_geoms[nname].append(ls.buffer(max(float(item.width) / 2.0, 0.02)))
        elif isinstance(item, Via):
            nname = net_by_num.get(int(item.net), "")
            if nname:
                net_geoms[nname].append(ShpPoint(float(item.position.X), H - float(item.position.Y)).buffer(float(item.size) / 2.0))

    for gn in gnd_names:
        if gn in net_geoms and not cu_unions["In1_Cu.gbr"].is_empty:
            net_geoms[gn].append(cu_unions["In1_Cu.gbr"])

    lvs_opens: list[str] = []
    merged_nets = {k: unary_union(v) for k, v in net_geoms.items() if v}
    for nname, g in merged_nets.items():
        n_comp = len(list(g.geoms)) if g.geom_type == "MultiPolygon" else 1
        if n_comp > 1:
            lvs_opens.append(f"{nname} ({n_comp} islands)")

    layer_net_geoms: dict[str, dict[str, list[Any]]] = {"F.Cu": defaultdict(list), "B.Cu": defaultdict(list)}
    for fp in board.footprints:
        for pad in fp.pads:
            if not _is_copper_pad(pad) or not pad.net or not pad.net.name:
                continue
            pg = _pad_world_polygon(fp, pad, H)
            if pad.type == "thru_hole":
                layer_net_geoms["F.Cu"][pad.net.name].append(pg)
                layer_net_geoms["B.Cu"][pad.net.name].append(pg)
            else:
                pad_lyrs = set(pad.layers or ["F.Cu"])
                if "B.Cu" in pad_lyrs and "F.Cu" not in pad_lyrs:
                    layer_net_geoms["B.Cu"][pad.net.name].append(pg)
                else:
                    layer_net_geoms["F.Cu"][pad.net.name].append(pg)
    for item in board.traceItems:
        if isinstance(item, Segment) and item.layer in layer_net_geoms:
            nname = net_by_num.get(int(item.net), "")
            if nname:
                ls = ShpLineString([(float(item.start.X), H - float(item.start.Y)), (float(item.end.X), H - float(item.end.Y))])
                layer_net_geoms[item.layer][nname].append(ls.buffer(max(float(item.width) / 2.0, 0.02)))
        elif isinstance(item, Via):
            nname = net_by_num.get(int(item.net), "")
            if nname:
                pt = ShpPoint(float(item.position.X), H - float(item.position.Y)).buffer(float(item.size) / 2.0)
                layer_net_geoms["F.Cu"][nname].append(pt)
                layer_net_geoms["B.Cu"][nname].append(pt)
    # Include F.Cu/B.Cu GND pours (NOT full Gerber unions!) in clearance/short check
    for k_lyr in ("F.Cu", "B.Cu"):
        pour_geom = pours.get(k_lyr)
        if pour_geom is not None and not pour_geom.is_empty:
            pour_gerb = shp_translate(shp_scale(pour_geom, xfact=1.0, yfact=-1.0, origin=(0.0, 0.0)), yoff=H)
            for gn in gnd_names:
                if gn in layer_net_geoms[k_lyr]:
                    layer_net_geoms[k_lyr][gn].append(pour_gerb)
                    break

    lvs_shorts: list[dict[str, Any]] = []
    min_clearance = float("inf")
    for lyr, nmap in layer_net_geoms.items():
        items = [(n, unary_union(gs)) for n, gs in nmap.items() if gs]
        for i in range(len(items)):
            n1, g1 = items[i]
            for j in range(i + 1, len(items)):
                n2, g2 = items[j]
                d = float(g1.distance(g2))
                if d < min_clearance:
                    min_clearance = d
                inter = g1.intersection(g2)
                if inter.area > 1e-4:
                    lvs_shorts.append({"layer": lyr, "net_a": n1, "net_b": n2, "overlap_mm2": round(float(inter.area), 5)})

    return {
        "pygerber_all_ok": all(pygerber_ok.values()),
        "pygerber_by_layer": pygerber_ok,
        "pth_hole_count": len(pth.objects),
        "npth_hole_count": len(npth.objects),
        "min_annular_ring_mm": round(min_ar, 4) if min_ar is not None else None,
        "annular_ring_violations": len(ar_viols),
        "silk_to_mask_overlap_mm2": round(silk_mask_overlap, 4),
        "silk_to_mask_overlap_by_side_mm2": silk_mask_overlap_by_side,
        "copper_edge_margins_mm": edge_margins,
        "lvs_opens": lvs_opens,
        "lvs_shorts": lvs_shorts,
        "min_inter_net_clearance_mm": round(min_clearance, 4) if math.isfinite(min_clearance) else None,
    }


def run_blind_solver(spec_path: Path = DEFAULT_BLIND_SPEC, outdir: Path = DEFAULT_OUTDIR, *, derive_power: bool = False, scoped_power: bool = False, external_placement: dict[str, Any] | None = None, octagonal_fanout: bool = False) -> dict[str, Any]:
    """Execute end-to-end blind PCB build on any blind_spec.json."""
    from cloud_pcb.general_solver.electrical import derive_electrical_requirements
    raw_spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec, electrical_derivations = derive_electrical_requirements(raw_spec, scoped=scoped_power) if derive_power or scoped_power else (raw_spec, [])
    outdir.mkdir(parents=True, exist_ok=True)
    gerber_dir = outdir / "gerbers"
    save_json(outdir / 'effective_spec.json', spec)
    for stale in ('board_fastroute.ses','fastroute_report.json','board_freerouting.ses','blind_run_report.json'):
        (outdir/stale).unlink(missing_ok=True)

    t_start = time.perf_counter()
    if external_placement is None:
        placed, place_meta = _classify_components_and_place(spec)
    else:
        from cloud_pcb.general_solver.placement_input import apply_external_placement
        placed, place_meta = apply_external_placement(spec, external_placement)
    board, netclasses, net_to_class, keepouts, board_poly = _build_blind_kicad_board(spec, placed)
    W = float(spec["board_outline_mm"]["width"])
    H = float(spec["board_outline_mm"]["height"])

    # Pre-route SMD GND fanout vias to In1.Cu/In2.Cu
    corridor_lines=[]
    by_ref={c['refdes']:c for c in spec['components']}
    protected={n for p in spec.get('design_requirements',{}).get('differential_pairs',[]) for n in (p['pos_net'],p['neg_net'])}
    for net in protected:
        points=[]
        for ref,pnum in spec.get('nets',{}).get(net,[]):
            pad=next((p for p in by_ref[ref]['pads'] if p['num']==pnum),None)
            if pad:
                x,y,a=placed[ref]; dx,dy=_rotate_kicad(pad['x_rel_mm'],pad['y_rel_mm'],a); points.append((x+dx,y+dy))
        if len(points)>1: corridor_lines.append(ShpLineString(points).buffer(0.6))
    # Reserve radial escape corridors for every dense IC's non-ground pins,
    # not just already identified differential buses.
    for fp in board.footprints:
        copper_pads=[p for p in fp.pads if _is_copper_pad(p)]
        if len(copper_pads)<6: continue
        for p in copper_pads:
            if not p.net or _is_gnd_net(p.net.name): continue
            g=_pad_world_polygon_kicad(fp,p);x,y=g.centroid.x,g.centroid.y
            dx,dy=x-float(fp.position.X),y-float(fp.position.Y);norm=math.hypot(dx,dy)
            if norm<.2:continue
            corridor_lines.append(ShpLineString([(x,y),(x+2.5*dx/norm,y+2.5*dy/norm)]).buffer(.20))
    corridors=unary_union(corridor_lines) if corridor_lines else None
    skip_nets, override_net_pins, fanout_stats = _add_gnd_fanout_vias(board, board_poly, keepouts,corridors,octagonal_keepouts=octagonal_fanout)

    unrouted_path = outdir / "board_unrouted.kicad_pcb"
    board.to_file(str(unrouted_path))

    dsn_path = outdir / "board.dsn"
    routing_layers={c:('F.Cu','B.Cu') if c.startswith('DIFF_PAIR') else ('F.Cu','In2.Cu','B.Cu') for c in netclasses}
    board_to_dsn(
        board,
        dsn_path,
        netclasses=netclasses,
        net_to_class=net_to_class,
        board_bounds_mm=(0.0, 0.0, W, H),
        board_outline_mm=list(board_poly.exterior.coords),
        keepouts_mm=keepouts,
        skip_nets=skip_nets,
        override_net_pins=override_net_pins,
        routing_layers_by_class=routing_layers,
    )

    pairs_lines = []
    for dp in spec.get("design_requirements", {}).get("differential_pairs", []):
        rule=netclasses[net_to_class[dp['pos_net']]]
        gap_mm=rule.diff_pair_gap_mm or rule.clearance_mm
        skew=float(dp.get('max_skew_mm',0.20))
        pairs_lines.append(f"pair {dp['pos_net']} {dp['neg_net']} gap={gap_mm} skew={skew}")
    pairs_path = outdir / "diff_pairs.txt"
    pairs_path.write_text("\n".join(pairs_lines) + ("\n" if pairs_lines else ""), encoding="utf-8")

    # 1. FastRoute v0.1.14
    fr_ses = outdir / "board_fastroute.ses"
    fr_rep_path = outdir / "fastroute_report.json"
    no_neckdown=[]
    for cname,rule in netclasses.items():
        if not cname.startswith('DIFF_PAIR'): continue
        endpoint_pads=[p for c in spec['components'] for p in c['pads'] if net_to_class.get(p.get('net'))==cname]
        if endpoint_pads and all(min(p['w_mm'],p['h_mm'])>=rule.trace_width_mm for p in endpoint_pads): no_neckdown.append(cname)
    fr_cmd = [
        "fastroute", "-de", str(dsn_path), "-do", str(fr_ses),
        f"--report={fr_rep_path}",
        "--max-time=110",
        "--multi-start=2",
        "--router.autorouter.max_threads=2",
        "--router.neck_width_um=160",
        "--router.min_trace_width_um=125",
        "--layer-heights=0.035,0.245,1.328,1.591",
        "--router.copper_to_edge_clearance_um=500",
    ]
    if no_neckdown: fr_cmd.append('--no-neckdown-classes='+','.join(no_neckdown))
    if pairs_lines:
        fr_cmd.append(f"--pairs={pairs_path}")
    t0 = time.perf_counter()
    fr_rc = -1
    from cloud_pcb.general_solver.router_logs import save_router_log
    router_processes=[]
    fr_stdout=fr_stderr=None
    fr_timed_out=False
    try:
        fr_proc = subprocess.run(fr_cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=150, check=False)
        fr_rc = fr_proc.returncode
        fr_stdout,fr_stderr=fr_proc.stdout,fr_proc.stderr
    except subprocess.TimeoutExpired as exc:
        fr_rc = -1
        fr_timed_out=True
        fr_stdout,fr_stderr=exc.stdout,exc.stderr
    fr_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    router_processes.append(save_router_log(outdir/'fastroute_primary.log',fr_cmd,fr_rc,fr_stdout,fr_stderr,timed_out=fr_timed_out))
    fr_ok = fr_rc == 0 and fr_ses.exists()
    fr_report=json.loads(fr_rep_path.read_text(encoding='utf-8')) if fr_rep_path.exists() else {}
    fr_stats = fr_report.get('stats',{})
    from cloud_pcb.verify_gate.gerber_drc import classify_router_violations
    spacing=classify_router_violations(fr_report,spec)
    route_mode='coupled_pairs' if pairs_lines else 'standard'
    route_candidates=[dict(mode=route_mode,unrouted=fr_stats.get('unrouted'),real_violations=spacing['real_count'])]
    if pairs_lines and fr_stats.get('unrouted',0)>0:
        # Dense endpoints may not admit the coupled router's full-width entry.
        # Try independently routed controlled-width nets with length tuning;
        # keep the candidate only if its actual routing/DRC objective improves.
        tune=outdir/'pair_tuning.txt';lines=[]
        for i,pair in enumerate(spec['design_requirements']['differential_pairs']):
            lines.extend([f"group pair_{i} tolerance={float(pair.get('max_skew_mm',.2))}",'  '+pair['pos_net'],'  '+pair['neg_net']])
        tune.write_text('\n'.join(lines)+'\n')
        alt_ses=outdir/'board_fastroute_individual.ses';alt_rep=outdir/'fastroute_individual_report.json'
        alt_ses.unlink(missing_ok=True);alt_rep.unlink(missing_ok=True)
        alt_cmd=[v for v in fr_cmd if not v.startswith('--pairs=') and not v.startswith('--report=')]
        alt_cmd[alt_cmd.index('-do')+1]=str(alt_ses)
        alt_cmd=[v.replace('--router.neck_width_um=160','--router.neck_width_um=125') for v in alt_cmd]
        alt_cmd.extend([f'--report={alt_rep}',f'--tune={tune}'])
        ta=time.perf_counter()
        alt_stdout=alt_stderr=None
        alt_timed_out=False
        try:
            alternate=subprocess.run(alt_cmd,cwd=PROJECT_ROOT,capture_output=True,text=True,timeout=150,check=False)
            alt_stdout,alt_stderr=alternate.stdout,alternate.stderr
            alt_rc=alternate.returncode
        except subprocess.TimeoutExpired as exc:
            alt_rc=-1;alt_timed_out=True
            alt_stdout,alt_stderr=exc.stdout,exc.stderr
        fr_ms+=round((time.perf_counter()-ta)*1000,2)
        router_processes.append(save_router_log(outdir/'fastroute_individual.log',alt_cmd,alt_rc,alt_stdout,alt_stderr,timed_out=alt_timed_out))
        if alt_rep.exists() and alt_ses.exists() and alt_rc==0:
            alt_report=json.loads(alt_rep.read_text());alt_spacing=classify_router_violations(alt_report,spec);alt_stats=alt_report.get('stats',{})
            route_candidates.append(dict(mode='individual_with_length_tuning',unrouted=alt_stats.get('unrouted'),real_violations=alt_spacing['real_count']))
            def objective(stats,spacing):return (spacing['real_count'],stats.get('unrouted',999),stats.get('trace_length_mm',float('inf')))
            if objective(alt_stats,alt_spacing)<objective(fr_stats,spacing):
                shutil.copyfile(fr_ses,outdir/'board_fastroute_coupled.ses');shutil.copyfile(fr_rep_path,outdir/'fastroute_coupled_report.json')
                shutil.copyfile(alt_ses,fr_ses);shutil.copyfile(alt_rep,fr_rep_path)
                fr_report,fr_stats,spacing,fr_rc,fr_ok=alt_report,alt_stats,alt_spacing,alt_rc,True
                route_mode='individual_with_length_tuning'
    fr_ses_info = parse_ses(fr_ses) if fr_ok else {"routed_nets": 0, "via_count": 0, "total_wirelength_mm": 0.0, "layers_used": []}
    fr_bench = RoutingBenchmark(
        engine="fastroute_v0.1.14",
        board_name=spec["project_name"],
        success=fr_ok and fr_stats.get("unrouted", 1) == 0 and spacing['real_count']==0,
        runtime_ms=fr_ms,
        total_nets=int(fr_stats.get("nets", fr_ses_info["routed_nets"])),
        routed_nets=int(fr_stats.get("nets", 0)) - int(fr_stats.get("unrouted", 0)) if fr_stats else fr_ses_info["routed_nets"],
        unrouted_nets=int(fr_stats.get("unrouted", 99)),
        via_count=int(fr_stats.get("vias", fr_ses_info["via_count"])),
        total_wirelength_mm=float(fr_stats.get("trace_length_mm", fr_ses_info["total_wirelength_mm"])),
        notes=f"rc={fr_rc}, raw_violations={fr_stats.get('violations', 'NA')}, real_violations={spacing['real_count']}, layers={fr_ses_info.get('layers_used')}",
    )

    # 2. Freerouting CLI v2.5.0
    free_dsn = outdir / "board_freerouting.dsn"
    board_to_dsn(
        board,
        free_dsn,
        netclasses=netclasses,
        net_to_class=net_to_class,
        board_bounds_mm=(0.0, 0.0, W, H),
        board_outline_mm=list(board_poly.exterior.coords),
        keepouts_mm=keepouts,
        skip_nets=skip_nets,
        override_net_pins=override_net_pins,
        routing_layers_by_class=routing_layers,
    )
    free_ses = outdir / "board_freerouting.ses"
    t1 = time.perf_counter()
    free_rc = -1
    free_out = ""
    free_stdout=free_stderr=None
    free_timed_out=False
    free_cmd=["freerouting-cli", "-de", str(free_dsn), "-do", str(free_ses),
              "--gui.enabled=false", "--router.copperToEdgeClearanceUm=500",
              "--router.autorouter.max_passes=1", "--router.optimizer.enabled=false"]
    try:
        free_proc = subprocess.run(
            free_cmd,
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60, check=False,
        )
        free_rc = free_proc.returncode
        free_out = (free_proc.stdout or "") + "\n" + (free_proc.stderr or "")
        free_stdout,free_stderr=free_proc.stdout,free_proc.stderr
    except subprocess.TimeoutExpired as exc:
        free_out = "timeout_60s"
        free_timed_out=True
        free_stdout,free_stderr=exc.stdout,exc.stderr
    free_ms = round((time.perf_counter() - t1) * 1000.0, 2)
    router_processes.append(save_router_log(outdir/'freerouting.log',free_cmd,free_rc,free_stdout,free_stderr,timed_out=free_timed_out))
    save_json(outdir/'router_processes.json',router_processes)
    free_ok = free_rc == 0 and free_ses.exists()
    free_ses_info = parse_ses(free_ses) if free_ok else {"routed_nets": 0, "via_count": 0, "total_wirelength_mm": 0.0, "layers_used": []}
    free_unr = max(0, fr_bench.total_nets - free_ses_info["routed_nets"])
    for ln in free_out.splitlines():
        if "final score:" in ln and "unrouted" in ln:
            m = re.search(r"\((\d+)\s+unrouted", ln)
            if m:
                free_unr = max(free_unr, int(m.group(1)))
    free_bench = RoutingBenchmark(
        engine="freerouting_cli_v2.5.0",
        board_name=spec["project_name"],
        success=free_ok and free_unr == 0,
        runtime_ms=free_ms,
        total_nets=fr_bench.total_nets,
        routed_nets=free_ses_info["routed_nets"],
        unrouted_nets=free_unr,
        via_count=free_ses_info["via_count"],
        total_wirelength_mm=free_ses_info["total_wirelength_mm"],
        notes=f"rc={free_rc}, max_passes=1" if free_rc >= 0 else "timeout_60s",
    )

    chosen_ses = fr_ses if fr_ok else free_ses
    routed_board = Board.from_file(str(unrouted_path))
    if chosen_ses.exists():
        routed_board, _ = merge_ses_into_board(routed_board, chosen_ses)

    islands=power_islands(routed_board,board_poly,spec,keepouts)
    pours, pour_stats = _build_4layer_pours_and_stitching(routed_board, board_poly,keepouts=keepouts,islands=islands)
    routed_pcb = outdir / "routed.kicad_pcb"
    routed_board.to_file(str(routed_pcb))

    gerbers = _export_blind_gerbers(routed_board, board_poly, pours, H, gerber_dir)
    audit = _audit_blind_output(routed_board, board_poly, pours, gerber_dir, H)
    from cloud_pcb.verify_gate.gerber_drc import audit_general_board
    from cloud_pcb.general_solver.assembly import export_assembly
    independent=audit_general_board(routed_board,spec,placed,keepouts)
    audit['legacy_projected_lvs_opens']=audit['lvs_opens']
    audit['lvs_opens']=independent['lvs_opens']; audit['lvs_shorts']=independent['lvs_shorts']
    audit['independent_4layer']=independent
    assembly=export_assembly(routed_board,spec,placed,outdir)
    rf_unknown=rf_keepouts(spec,placed)[1]
    hard_gates={
        'unrouted_zero':fr_ok and fr_stats.get('unrouted')==0,
        'router_spacing_zero':fr_ok and spacing['real_count']==0,
        'independent_lvs_opens_zero':not independent['lvs_opens'],
        'independent_lvs_shorts_zero':not independent['lvs_shorts'],
        'nonthermal_via_in_pad_zero':not independent['via_in_pad_violations'],
        'gerber_all_parsed':audit['pygerber_all_ok'],
        'annular_ring_ge_010':audit['min_annular_ring_mm'] is not None and audit['min_annular_ring_mm']>=.10 and audit['annular_ring_violations']==0,
        'silk_overlap_zero':audit['silk_to_mask_overlap_mm2']==0,
        'copper_edge_ge_050':all(d>=.50-1e-4 for d in audit['copper_edge_margins_mm'].values()) and len(audit['copper_edge_margins_mm'])==4,
        'rf_all_four_layers_clear':not rf_unknown and all(k['passed'] for k in independent['rf_keepout']),
        'mechanical_zero_with_verified_envelopes':independent['mechanical']['passed'],
    }

    summary = {
        "project_name": spec["project_name"],
        "electrical_derivations": electrical_derivations,
        "power_routing_intent": {
            net: dict(class_name=cls, target_width_mm=netclasses[cls].trace_width_mm,
                      routing_scope=next((r.get('routing_scope', 'board_distribution') for r in spec.get('design_requirements', {}).get('power_rails', []) if r['net'] == net), 'board_distribution'),
                      current_verified=any(r['net'] == net and (r.get('max_current_a') is not None or r.get('current_a') is not None) for r in spec.get('design_requirements', {}).get('power_rails', [])))
            for net, cls in net_to_class.items() if cls.startswith('POWER')
        },
        "repo_url": spec["repo_url"],
        "commit_sha": spec["commit_sha"],
        "total_runtime_ms": round((time.perf_counter() - t_start) * 1000.0, 2),
        "placement_meta": place_meta,
        "placed_positions": placed,
        "gnd_fanout_stats": fanout_stats,
        "bakeoff": [fr_bench, free_bench],
        "gnd_pour_stats": pour_stats,
        "gerber_files": gerbers,
        "audit": audit,
        "hard_gates": hard_gates,
        "hard_gates_passed": all(hard_gates.values()),
        "router_spacing": spacing,
        "routing_mode": route_mode,
        "router_processes": router_processes,
        "routing_candidates": route_candidates,
        "full_width_at_pin_diff_classes": no_neckdown,
        "keepouts": keepouts,
        "rf_unresolved": rf_unknown,
        "power_width_distribution_mm": width_distribution(routed_board,{n for n,c in net_to_class.items() if c.startswith('POWER')}),
        "assembly": assembly,
    }
    save_json(outdir / "blind_run_report.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run board-agnostic blind PCB solver")
    parser.add_argument("--spec", type=Path, default=DEFAULT_BLIND_SPEC)
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--derive-power", action="store_true", help="Evaluate evidence-based supply inference as an experimental variant")
    parser.add_argument("--scoped-power", action="store_true", help="Experimental single-IC supply scope; implies supply inference")
    parser.add_argument("--octagonal-fanout", action="store_true", help="Use circumscribed via guards with unchanged circular protection radius")
    args = parser.parse_args()
    res = run_blind_solver(args.spec, args.outdir, derive_power=args.derive_power, scoped_power=args.scoped_power,octagonal_fanout=args.octagonal_fanout)
    print(json.dumps({
        "project": res["project_name"],
        "total_placed": res["placement_meta"]["total_placed"],
        "gnd_fanout_stats": res["gnd_fanout_stats"],
        "bakeoff": [
            {k: getattr(b, k) for k in ("engine", "success", "routed_nets", "total_nets", "unrouted_nets", "via_count", "total_wirelength_mm", "runtime_ms", "notes")}
            for b in res["bakeoff"]
        ],
        "gnd_pour_stats": res["gnd_pour_stats"],
        "audit": res["audit"],
    }, indent=2))
