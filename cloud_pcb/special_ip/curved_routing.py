"""Any-Angle Chaikin / Circular-Arc Trace Smoother & Clearance-Checked Serpentine Meander Generator.

Emits native arc + straight segment primitives that serialize cleanly to:
  - KiCad board tracks (`Segment`, `Arc`)
  - KiCad graphics (`GrLine`, `GrArc`)
  - Gerbonara RS-274X true `Line` & `Arc` objects
and verifies self-clearance and obstacle avoidance via Shapely.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class StraightSegment:
    start: tuple[float, float]
    end: tuple[float, float]
    width_mm: float
    layer: str
    net_name: str = ""

    @property
    def length_mm(self) -> float:
        return math.hypot(self.end[0] - self.start[0], self.end[1] - self.start[1])

    def sample_points(self, max_step_mm: float = 0.25) -> list[tuple[float, float]]:
        n = max(int(math.ceil(self.length_mm / max(max_step_mm, 1e-3))), 1)
        return [
            (
                self.start[0] + (self.end[0] - self.start[0]) * (i / n),
                self.start[1] + (self.end[1] - self.start[1]) * (i / n),
            )
            for i in range(n + 1)
        ]


@dataclass(frozen=True)
class ArcSegment:
    start: tuple[float, float]
    mid: tuple[float, float]
    end: tuple[float, float]
    center: tuple[float, float]
    radius_mm: float
    clockwise: bool
    sweep_rad: float
    width_mm: float
    layer: str
    net_name: str = ""

    @property
    def length_mm(self) -> float:
        return self.radius_mm * self.sweep_rad

    def sample_points(self, n_pts: int = 16) -> list[tuple[float, float]]:
        cx, cy = self.center
        a0 = math.atan2(self.start[1] - cy, self.start[0] - cx)
        sign = -1.0 if self.clockwise else 1.0
        pts = []
        for i in range(n_pts + 1):
            ang = a0 + sign * self.sweep_rad * (i / n_pts)
            pts.append((cx + self.radius_mm * math.cos(ang), cy + self.radius_mm * math.sin(ang)))
        pts[0] = self.start
        pts[-1] = self.end
        return pts


@dataclass
class TracePath:
    layer: str
    width_mm: float
    net_name: str
    primitives: list[StraightSegment | ArcSegment] = field(default_factory=list)

    @property
    def total_length_mm(self) -> float:
        return sum(p.length_mm for p in self.primitives)

    @property
    def arc_count(self) -> int:
        return sum(1 for p in self.primitives if isinstance(p, ArcSegment))

    @property
    def segment_count(self) -> int:
        return sum(1 for p in self.primitives if isinstance(p, StraightSegment))

    def to_polyline(self, arc_samples: int = 16) -> list[tuple[float, float]]:
        pts: list[tuple[float, float]] = []
        for prim in self.primitives:
            seg_pts = prim.sample_points(arc_samples) if isinstance(prim, ArcSegment) else [prim.start, prim.end]
            for pt in seg_pts:
                if not pts or math.hypot(pt[0] - pts[-1][0], pt[1] - pts[-1][1]) > 1e-6:
                    pts.append(pt)
        return pts

    def to_shapely(self, arc_samples: int = 16) -> LineString:
        return LineString(self.to_polyline(arc_samples=arc_samples))


@dataclass
class MeanderResult:
    trace_path: TracePath
    target_length_mm: float
    actual_length_mm: float
    length_error_mm: float
    meander_cycles: int
    amplitude_mm: float
    min_obstacle_clearance_mm: float
    min_self_clearance_mm: float
    is_simple: bool
    clearance_ok: bool


def chaikin_smooth(
    points: Sequence[tuple[float, float]],
    iterations: int = 3,
    ratio: float = 0.25,
) -> list[tuple[float, float]]:
    """Apply endpoint-preserving Chaikin corner-cutting smoothing to an any-angle polyline."""
    if len(points) < 3 or iterations <= 0:
        return [(float(p[0]), float(p[1])) for p in points]

    pts = [(float(p[0]), float(p[1])) for p in points]
    r = float(ratio)
    for _ in range(iterations):
        new_pts: list[tuple[float, float]] = [pts[0]]
        for i in range(len(pts) - 1):
            p0 = pts[i]
            p1 = pts[i + 1]
            q = ((1.0 - r) * p0[0] + r * p1[0], (1.0 - r) * p0[1] + r * p1[1])
            s = (r * p0[0] + (1.0 - r) * p1[0], r * p0[1] + (1.0 - r) * p1[1])
            if i > 0:
                new_pts.append(q)
            if i < len(pts) - 2:
                new_pts.append(s)
        new_pts.append(pts[-1])
        pts = new_pts
    return pts


def chaikin_to_trace_path(
    points: Sequence[tuple[float, float]],
    width_mm: float,
    layer: str = "F.Cu",
    net_name: str = "",
    iterations: int = 3,
) -> TracePath:
    """Smooth polyline via Chaikin corner-cutting and emit a TracePath of StraightSegments."""
    smoothed = chaikin_smooth(points, iterations=iterations)
    prims: list[StraightSegment | ArcSegment] = []
    for i in range(len(smoothed) - 1):
        p0, p1 = smoothed[i], smoothed[i + 1]
        if math.hypot(p1[0] - p0[0], p1[1] - p0[1]) > 1e-5:
            prims.append(StraightSegment(start=p0, end=p1, width_mm=width_mm, layer=layer, net_name=net_name))
    return TracePath(layer=layer, width_mm=width_mm, net_name=net_name, primitives=prims)


def fillet_polyline_with_arcs(
    points: Sequence[tuple[float, float]],
    fillet_radius_mm: float = 0.80,
    width_mm: float = 0.25,
    layer: str = "F.Cu",
    net_name: str = "",
) -> TracePath:
    """Replace polyline corners with exact tangent circular ArcSegments + StraightSegments."""
    pts = [(float(p[0]), float(p[1])) for p in points]
    # Remove duplicate consecutive points
    dedup: list[tuple[float, float]] = []
    for p in pts:
        if not dedup or math.hypot(p[0] - dedup[-1][0], p[1] - dedup[-1][1]) > 1e-6:
            dedup.append(p)
    pts = dedup

    if len(pts) < 2:
        return TracePath(layer=layer, width_mm=width_mm, net_name=net_name)
    if len(pts) == 2 or fillet_radius_mm <= 1e-5:
        prims: list[StraightSegment | ArcSegment] = [
            StraightSegment(pts[i], pts[i + 1], width_mm, layer, net_name)
            for i in range(len(pts) - 1)
        ]
        return TracePath(layer=layer, width_mm=width_mm, net_name=net_name, primitives=prims)

    # First pass: compute setback distance t_i at each interior corner i = 1..n-2
    n = len(pts)
    corner_data: list[dict[str, Any] | None] = [None] * n
    setbacks = [0.0] * n

    for i in range(1, n - 1):
        p_prev, p_curr, p_next = pts[i - 1], pts[i], pts[i + 1]
        v1 = np.array([p_curr[0] - p_prev[0], p_curr[1] - p_prev[1]], dtype=float)
        v2 = np.array([p_next[0] - p_curr[0], p_next[1] - p_curr[1]], dtype=float)
        l1 = float(np.linalg.norm(v1))
        l2 = float(np.linalg.norm(v2))
        if l1 < 1e-6 or l2 < 1e-6:
            continue
        u1 = v1 / l1
        u2 = v2 / l2
        dot = float(np.clip(np.dot(u1, u2), -1.0, 1.0))
        alpha = math.acos(dot)  # deflection angle in [0, pi]
        cross = float(u1[0] * u2[1] - u1[1] * u2[0])
        if alpha < 1e-3 or abs(cross) < 1e-6:
            continue

        tan_half = math.tan(0.5 * alpha)
        t_req = fillet_radius_mm * tan_half
        max_leg = 0.45 * min(l1, l2)
        t_eff = min(t_req, max_leg)
        r_eff = t_eff / tan_half
        if r_eff < 0.05:
            continue

        setbacks[i] = t_eff
        corner_data[i] = {
            "u1": u1,
            "u2": u2,
            "alpha": alpha,
            "cross": cross,
            "t": t_eff,
            "r": r_eff,
        }

    # Ensure adjacent corners on the same segment don't overlap setbacks
    for i in range(1, n - 2):
        seg_len = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        if setbacks[i] + setbacks[i + 1] > 0.92 * seg_len and (setbacks[i] + setbacks[i + 1]) > 0:
            scale = 0.92 * seg_len / (setbacks[i] + setbacks[i + 1])
            for idx in (i, i + 1):
                if corner_data[idx] is not None:
                    corner_data[idx]["t"] *= scale
                    corner_data[idx]["r"] *= scale
                    setbacks[idx] *= scale

    prims = []
    cursor = pts[0]
    for i in range(1, n - 1):
        cd = corner_data[i]
        p_curr = np.array(pts[i], dtype=float)
        if cd is None:
            if math.hypot(p_curr[0] - cursor[0], p_curr[1] - cursor[1]) > 1e-5:
                prims.append(StraightSegment(cursor, (float(p_curr[0]), float(p_curr[1])), width_mm, layer, net_name))
            cursor = (float(p_curr[0]), float(p_curr[1]))
            continue

        u1 = cd["u1"]
        u2 = cd["u2"]
        t = cd["t"]
        r = cd["r"]
        alpha = cd["alpha"]
        cross = cd["cross"]

        t1 = p_curr - t * u1
        t2 = p_curr + t * u2
        bis = u2 - u1
        bis /= float(np.linalg.norm(bis))
        dc = r / max(math.cos(0.5 * alpha), 1e-6)
        center = p_curr + dc * bis
        mid = center - r * bis

        t1_tup = (float(t1[0]), float(t1[1]))
        t2_tup = (float(t2[0]), float(t2[1]))
        mid_tup = (float(mid[0]), float(mid[1]))
        ctr_tup = (float(center[0]), float(center[1]))

        if math.hypot(t1_tup[0] - cursor[0], t1_tup[1] - cursor[1]) > 1e-5:
            prims.append(StraightSegment(cursor, t1_tup, width_mm, layer, net_name))

        prims.append(
            ArcSegment(
                start=t1_tup,
                mid=mid_tup,
                end=t2_tup,
                center=ctr_tup,
                radius_mm=float(r),
                clockwise=(cross < 0.0),
                sweep_rad=float(alpha),
                width_mm=width_mm,
                layer=layer,
                net_name=net_name,
            )
        )
        cursor = t2_tup

    if math.hypot(pts[-1][0] - cursor[0], pts[-1][1] - cursor[1]) > 1e-5:
        prims.append(StraightSegment(cursor, pts[-1], width_mm, layer, net_name))

    return TracePath(layer=layer, width_mm=width_mm, net_name=net_name, primitives=prims)


def _build_meander_polyline(
    start: tuple[float, float],
    end: tuple[float, float],
    cycles: int,
    amplitude_mm: float,
    leg_pitch_mm: float,
    side: int = 1,
    symmetric: bool = True,
) -> list[tuple[float, float]]:
    """Build centerline polyline for rounded serpentine meander between start and end."""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    span = math.hypot(dx, dy)
    if span < 1e-6 or cycles <= 0 or amplitude_mm <= 1e-4:
        return [start, end]

    u = np.array([dx / span, dy / span], dtype=float)
    n = np.array([-u[1], u[0]], dtype=float) * float(side)
    p0 = np.array(start, dtype=float)

    meander_span = cycles * leg_pitch_mm
    lead = 0.5 * (span - meander_span)
    if lead < 0.2:
        return [start, end]

    half_lobe = 0.5 * leg_pitch_mm
    q_lobe = 0.25 * leg_pitch_mm
    pts: list[tuple[float, float]] = [start]

    for c in range(cycles):
        x0 = lead + c * leg_pitch_mm
        sign = (1.0 if c % 2 == 0 else -1.0) if symmetric else 1.0
        amp = sign * amplitude_mm
        if symmetric:
            # Classic square-wave serpentine corner vertices ready for arc filleting
            p_a = p0 + (x0 + q_lobe) * u
            p_b = p0 + (x0 + q_lobe) * u + amp * n
            p_c = p0 + (x0 + 3.0 * q_lobe) * u + amp * n
            p_d = p0 + (x0 + 3.0 * q_lobe) * u
            pts.extend([(float(p_a[0]), float(p_a[1])), (float(p_b[0]), float(p_b[1])), (float(p_c[0]), float(p_c[1])), (float(p_d[0]), float(p_d[1]))])
        else:
            p_a = p0 + (x0 + 0.25 * leg_pitch_mm) * u
            p_b = p0 + (x0 + 0.25 * leg_pitch_mm) * u + amp * n
            p_c = p0 + (x0 + 0.75 * leg_pitch_mm) * u + amp * n
            p_d = p0 + (x0 + 0.75 * leg_pitch_mm) * u
            pts.extend([(float(p_a[0]), float(p_a[1])), (float(p_b[0]), float(p_b[1])), (float(p_c[0]), float(p_c[1])), (float(p_d[0]), float(p_d[1]))])

    pts.append(end)
    return pts


def _measure_min_self_clearance(trace_path: TracePath, width_mm: float) -> float:
    """Measure edge-to-edge self-clearance between non-local trace sections.

    Samples points densely along the trace and measures minimum Euclidean distance
    minus trace width across point pairs separated by at least max(3.5 * width, 1.0mm)
    along the curve arc-length (avoiding trivial adjacent-point proximity around corners).
    """
    pts = trace_path.to_polyline(arc_samples=10)
    if len(pts) < 4:
        return 999.0
    arr = np.asarray(pts, dtype=float)
    diffs = np.diff(arr, axis=0)
    seg_lens = np.linalg.norm(diffs, axis=1)
    cum_s = np.concatenate([[0.0], np.cumsum(seg_lens)])
    # Resample uniformly at ~0.08mm spacing for exact clearance scan
    total_s = float(cum_s[-1])
    if total_s < 1e-4:
        return 999.0
    n_u = max(int(math.ceil(total_s / 0.08)), 20)
    s_u = np.linspace(0.0, total_s, n_u)
    xu = np.interp(s_u, cum_s, arr[:, 0])
    yu = np.interp(s_u, cum_s, arr[:, 1])
    pts_u = np.column_stack([xu, yu])

    # Geodesic separation threshold: half a U-bend turn (~ pi * r_bend + width)
    min_arc_sep = max(3.5 * width_mm, 1.05)
    # Pairwise Euclidean & arc-length matrices (n_u typically ~ 150-400)
    d_euclid = np.linalg.norm(pts_u[:, None, :] - pts_u[None, :, :], axis=2)
    d_arc = np.abs(s_u[:, None] - s_u[None, :])
    mask = d_arc >= min_arc_sep
    if not np.any(mask):
        return 999.0
    min_center_dist = float(np.min(d_euclid[mask]))
    return min_center_dist - width_mm


def generate_serpentine_meander(
    start: tuple[float, float],
    end: tuple[float, float],
    target_length_mm: float,
    width_mm: float = 0.25,
    min_clearance_mm: float = 0.20,
    max_amplitude_mm: float = 2.0,
    layer: str = "F.Cu",
    net_name: str = "",
    obstacles: Sequence[BaseGeometry] = (),
    symmetric: bool = True,
    preferred_side: int = 1,
) -> MeanderResult:
    """Generate a clearance-checked circular-arc serpentine meander matching target_length_mm.

    Automatically selects cycle count, fillet radius, side (+1/-1), and trombone amplitude
    via numerical root-finding while enforcing Shapely clearance against all obstacle
    geometries and self-clearance between meander legs.
    """
    direct_len = math.hypot(end[0] - start[0], end[1] - start[1])
    if target_length_mm <= direct_len + 0.03:
        tp = TracePath(
            layer=layer,
            width_mm=width_mm,
            net_name=net_name,
            primitives=[StraightSegment(start, end, width_mm, layer, net_name)],
        )
        line = tp.to_shapely()
        obs_clr = min((line.distance(o) - 0.5 * width_mm for o in obstacles), default=999.0)
        return MeanderResult(
            trace_path=tp,
            target_length_mm=target_length_mm,
            actual_length_mm=tp.total_length_mm,
            length_error_mm=abs(tp.total_length_mm - target_length_mm),
            meander_cycles=0,
            amplitude_mm=0.0,
            min_obstacle_clearance_mm=float(obs_clr),
            min_self_clearance_mm=999.0,
            is_simple=bool(line.is_simple),
            clearance_ok=obs_clr >= min_clearance_mm,
        )

    # Minimum centerline pitch between adjacent meander legs:
    # Each symmetric cycle contains two vertical legs spaced by 0.5 * leg_pitch.
    min_leg_gap = width_mm + max(min_clearance_mm, 0.22)
    leg_pitch = 2.15 * min_leg_gap if symmetric else 2.25 * min_leg_gap
    fillet_r = max(0.20 * leg_pitch, 0.16)

    usable_span = max(direct_len - 0.8, 0.5)
    max_cycles = max(int(usable_span // leg_pitch), 1)

    best_result: MeanderResult | None = None

    sides_to_try = [preferred_side, -preferred_side]
    for side in sides_to_try:
        for cycles in range(1, max_cycles + 1):
            def eval_amp(amp: float) -> tuple[TracePath, float, float, bool]:
                raw = _build_meander_polyline(
                    start, end, cycles=cycles, amplitude_mm=amp,
                    leg_pitch_mm=leg_pitch, side=side, symmetric=symmetric,
                )
                tp = fillet_polyline_with_arcs(
                    raw, fillet_radius_mm=fillet_r, width_mm=width_mm,
                    layer=layer, net_name=net_name,
                )
                line = tp.to_shapely()
                obs_clr = min((line.distance(o) - 0.5 * width_mm for o in obstacles), default=999.0)
                self_clr = _measure_min_self_clearance(tp, width_mm)
                # Topological check: buffering centerline by half of (width + clearance) must not create pinch holes
                r_pinch = 0.5 * (width_mm + min_clearance_mm) - 1e-3
                buf = line.buffer(r_pinch)
                no_pinch = (buf.geom_type == "Polygon") and (len(buf.interiors) == 0)
                ok = bool(line.is_simple) and no_pinch and obs_clr >= min_clearance_mm and self_clr >= min_clearance_mm
                return tp, obs_clr, self_clr, ok

            # Check maximum reachable length at max_amplitude_mm
            tp_max, _, _, _ = eval_amp(max_amplitude_mm)
            if tp_max.total_length_mm < target_length_mm - 0.05:
                continue

            # Binary search amplitude in [0.15, max_amplitude_mm]
            lo, hi = 0.15, max_amplitude_mm
            for _ in range(28):
                mid = 0.5 * (lo + hi)
                tp_mid, _, _, _ = eval_amp(mid)
                if tp_mid.total_length_mm < target_length_mm:
                    lo = mid
                else:
                    hi = mid

            amp_sol = 0.5 * (lo + hi)
            tp_sol, obs_clr, self_clr, ok = eval_amp(amp_sol)
            err = abs(tp_sol.total_length_mm - target_length_mm)
            cand = MeanderResult(
                trace_path=tp_sol,
                target_length_mm=target_length_mm,
                actual_length_mm=tp_sol.total_length_mm,
                length_error_mm=err,
                meander_cycles=cycles,
                amplitude_mm=amp_sol,
                min_obstacle_clearance_mm=obs_clr,
                min_self_clearance_mm=self_clr,
                is_simple=bool(tp_sol.to_shapely().is_simple),
                clearance_ok=ok,
            )
            if ok and err < 0.05:
                return cand
            if best_result is None or (cand.clearance_ok and not best_result.clearance_ok) or (
                cand.clearance_ok == best_result.clearance_ok
                and (
                    (not cand.clearance_ok and cand.min_obstacle_clearance_mm > best_result.min_obstacle_clearance_mm + 1e-4)
                    or (
                        (cand.clearance_ok or abs(cand.min_obstacle_clearance_mm - best_result.min_obstacle_clearance_mm) <= 1e-4)
                        and cand.length_error_mm < best_result.length_error_mm
                    )
                )
            ):
                best_result = cand

    assert best_result is not None, "Failed to generate meander path within max_cycles/max_amplitude"
    return best_result
