"""Parametric Chevron Capacitive Touch Slider Generator & Shapely Clearance Verifier.

Generates a 4-element interlocking chevron capacitive touch slider on F.Cu,
isolated by >= 0.20mm inter-electrode spacing, encircled by a perimeter GND
guard ring on F.Cu, and broken out via plated through-hole vias and B.Cu
jumper bridges that cross cleanly beneath the guard ring.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union


@dataclass(frozen=True)
class CapTouchSpec:
    """Parameters for the 4-element chevron capacitive touch slider."""

    center_x_mm: float = 25.0
    center_y_mm: float = 40.5
    num_elements: int = 4
    element_pitch_mm: float = 7.2
    height_mm: float = 7.0
    chevron_depth_mm: float = 2.6
    inter_electrode_gap_mm: float = 0.30
    guard_clearance_mm: float = 1.15
    guard_width_mm: float = 0.50
    bridge_width_mm: float = 0.25
    via_drill_mm: float = 0.30
    via_pad_mm: float = 0.65
    breakout_y_offset_mm: float = -6.5
    board_width_mm: float = 50.0
    board_height_mm: float = 50.0
    min_edge_clearance_mm: float = 0.50


@dataclass(frozen=True)
class ChevronElectrode:
    index: int
    net_name: str
    polygon_local_mm: list[tuple[float, float]]
    area_mm2: float
    via_in_local_mm: tuple[float, float]
    via_out_local_mm: tuple[float, float]
    bridge_points_local_mm: list[tuple[float, float]]


@dataclass
class CapTouchDesign:
    spec: CapTouchSpec
    electrodes: list[ChevronElectrode]
    guard_ring_points_local_mm: list[tuple[float, float]]
    guard_vias_local_mm: list[tuple[float, float]]
    guard_feed_local_mm: tuple[float, float]
    min_electrode_clearance_mm: float
    min_guard_clearance_mm: float
    min_via_other_net_clearance_mm: float
    min_board_edge_clearance_mm: float
    all_polygons_valid: bool
    drc_passed: bool
    notes: list[str] = field(default_factory=list)

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "spec": asdict(self.spec),
            "electrodes": [
                {
                    "index": e.index,
                    "net_name": e.net_name,
                    "area_mm2": round(e.area_mm2, 3),
                    "via_in_local_mm": [round(v, 4) for v in e.via_in_local_mm],
                    "via_out_local_mm": [round(v, 4) for v in e.via_out_local_mm],
                    "vertex_count": len(e.polygon_local_mm),
                }
                for e in self.electrodes
            ],
            "guard_vias_local_mm": [[round(c, 4) for c in pt] for pt in self.guard_vias_local_mm],
            "min_electrode_clearance_mm": round(self.min_electrode_clearance_mm, 4),
            "min_guard_clearance_mm": round(self.min_guard_clearance_mm, 4),
            "min_via_other_net_clearance_mm": round(self.min_via_other_net_clearance_mm, 4),
            "min_board_edge_clearance_mm": round(self.min_board_edge_clearance_mm, 4),
            "all_polygons_valid": self.all_polygons_valid,
            "drc_passed": self.drc_passed,
            "notes": self.notes,
        }


def build_captouch_slider(spec: CapTouchSpec | None = None) -> CapTouchDesign:
    """Construct the 4-element chevron capacitive touch slider and run Shapely DRC."""
    if spec is None:
        spec = CapTouchSpec()

    assert spec.num_elements >= 2
    assert spec.inter_electrode_gap_mm >= 0.20, "Inter-electrode isolation must be >= 0.20 mm"

    h = spec.height_mm
    half_h = h / 2.0
    d = spec.chevron_depth_mm
    pitch = spec.element_pitch_mm
    n = spec.num_elements

    # Center overall chevron bounding box horizontally around local X = 0
    total_pitch = n * pitch
    x_shift = -0.5 * (total_pitch + d)
    boundaries = [x_shift + k * pitch for k in range(n + 1)]

    # Angle of chevron bevel from vertical: dx/dy = ± d / half_h
    cos_alpha = half_h / math.hypot(d, half_h)
    dx_half = 0.5 * spec.inter_electrode_gap_mm / cos_alpha

    electrodes: list[ChevronElectrode] = []
    poly_shapes: list[Polygon] = []

    for k in range(n):
        x_l = boundaries[k] + dx_half
        x_r = boundaries[k + 1] - dx_half

        # Chevron polygon: top-left -> tip-right -> bot-right -> tip-left (classic '>' chevron)
        pts = [
            (x_l, -half_h),
            (x_r, -half_h),
            (x_r + d, 0.0),
            (x_r, +half_h),
            (x_l, +half_h),
            (x_l + d, 0.0),
        ]
        poly = Polygon(pts)
        assert poly.is_valid, f"Chevron polygon {k} invalid"
        poly_shapes.append(poly)

        # Place internal via at chevron spine center (x_mid + d, 0.0)
        via_x = 0.5 * (x_l + x_r) + d
        via_in = (via_x, 0.0)
        via_out = (via_x, spec.breakout_y_offset_mm)
        bridge_pts = [via_in, via_out]

        electrodes.append(
            ChevronElectrode(
                index=k,
                net_name=f"TOUCH_{k}",
                polygon_local_mm=pts,
                area_mm2=float(poly.area),
                via_in_local_mm=via_in,
                via_out_local_mm=via_out,
                bridge_points_local_mm=bridge_pts,
            )
        )

    # Perimeter GND guard ring enclosing all chevron pads on F.Cu
    chev_union = unary_union(poly_shapes)
    minx, miny, maxx, maxy = chev_union.bounds
    g_off = spec.guard_clearance_mm + 0.5 * spec.guard_width_mm
    gx0, gy0 = minx - g_off, miny - g_off
    gx1, gy1 = maxx + g_off, maxy + g_off
    guard_ring_pts = [
        (gx0, gy0),
        (gx1, gy0),
        (gx1, gy1),
        (gx0, gy1),
        (gx0, gy0),
    ]
    guard_line = LineString(guard_ring_pts).buffer(0.5 * spec.guard_width_mm)

    # Two GND stitching vias on far left & right edges (well away from B.Cu bridges)
    guard_vias = [(gx0, 0.0), (gx1, 0.0)]
    guard_feed = (gx0, gy0)

    # --- Shapely DRC Verification ---
    # 1. Inter-electrode isolation (must be >= 0.18 mm, target >= 0.20 mm)
    inter_dists = [
        poly_shapes[i].distance(poly_shapes[j])
        for i in range(n)
        for j in range(i + 1, n)
    ]
    min_elec_clr = float(min(inter_dists)) if inter_dists else 999.0

    # 2. Electrode-to-guard clearance on F.Cu
    min_guard_clr = float(min(p.distance(guard_line) for p in poly_shapes))

    # 3. Via and B.Cu bridge clearances against all OTHER nets on F.Cu and B.Cu
    via_rad = 0.5 * spec.via_pad_mm
    f_cu_by_net: dict[str, list[Any]] = {
        "GND": [guard_line] + [Point(gv).buffer(via_rad) for gv in guard_vias]
    }
    b_cu_by_net: dict[str, list[Any]] = {
        "GND": [Point(gv).buffer(via_rad) for gv in guard_vias]
    }
    for idx, elec in enumerate(electrodes):
        v_in_geom = Point(elec.via_in_local_mm).buffer(via_rad)
        v_out_geom = Point(elec.via_out_local_mm).buffer(via_rad)
        bridge_geom = LineString(elec.bridge_points_local_mm).buffer(0.5 * spec.bridge_width_mm)

        f_cu_by_net[elec.net_name] = [poly_shapes[idx], v_in_geom, v_out_geom]
        b_cu_by_net[elec.net_name] = [v_in_geom, v_out_geom, bridge_geom]

    other_net_dists: list[float] = []
    for layer_dict in (f_cu_by_net, b_cu_by_net):
        nets = list(layer_dict.keys())
        for i in range(len(nets)):
            gi = unary_union(layer_dict[nets[i]])
            for j in range(i + 1, len(nets)):
                gj = unary_union(layer_dict[nets[j]])
                other_net_dists.append(float(gi.distance(gj)))
    min_via_other_clr = min(other_net_dists) if other_net_dists else 999.0

    # 4. Copper-to-board-edge clearance (>= 0.50 mm on 50 x 50 mm board)
    all_local_geoms = []
    for lst in f_cu_by_net.values():
        all_local_geoms.extend(lst)
    for lst in b_cu_by_net.values():
        all_local_geoms.extend(lst)
    local_union = unary_union(all_local_geoms)
    lminx, lminy, lmaxx, lmaxy = local_union.bounds
    bminx = spec.center_x_mm + lminx
    bmaxx = spec.center_x_mm + lmaxx
    bminy = spec.center_y_mm + lminy
    bmaxy = spec.center_y_mm + lmaxy
    min_edge_clr = float(
        min(
            bminx,
            spec.board_width_mm - bmaxx,
            bminy,
            spec.board_height_mm - bmaxy,
        )
    )

    all_valid = all(p.is_valid for p in poly_shapes)
    drc_ok = (
        all_valid
        and min_elec_clr >= 0.18
        and min_guard_clr >= 0.50
        and min_via_other_clr >= 0.18
        and min_edge_clr >= spec.min_edge_clearance_mm
    )

    return CapTouchDesign(
        spec=spec,
        electrodes=electrodes,
        guard_ring_points_local_mm=guard_ring_pts,
        guard_vias_local_mm=guard_vias,
        guard_feed_local_mm=guard_feed,
        min_electrode_clearance_mm=min_elec_clr,
        min_guard_clearance_mm=min_guard_clr,
        min_via_other_net_clearance_mm=min_via_other_clr,
        min_board_edge_clearance_mm=min_edge_clr,
        all_polygons_valid=all_valid,
        drc_passed=drc_ok,
        notes=[
            "4 interlocking chevron electrodes on F.Cu (solder-mask covered for capacitive dielectric).",
            "B.Cu jumper bridges carry TOUCH_0..3 beneath the F.Cu GND guard ring to external breakout vias.",
        ],
    )
