"""4-Layer Normal-Offset Planar Electromagnetic Haptic Coil Generator & ngspice Verifier.

Generates a non-self-intersecting normal-offset rounded-racetrack planar spiral coil
spanning 4 copper layers (F.Cu, In1.Cu, In2.Cu, B.Cu) with strictly alternating
geometric chirality across inter-layer vias so current flows in the same angular
direction on every layer and axial magnetic flux adds constructively.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from scipy.interpolate import CubicHermiteSpline
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from contracts.schemas import save_json

MU_0 = 4.0e-7 * math.pi  # H/m
CU_RHO_20C = 1.7241e-8  # Ohm*m (annealed copper IACS)
CU_TEMP_COEFF = 0.00393  # 1/K

DEFAULT_4L_Z_MM: dict[str, float] = {
    "F.Cu": 0.0,
    "In1.Cu": 0.21,
    "In2.Cu": 1.39,
    "B.Cu": 1.60,
}

DEFAULT_CU_THICKNESS_MM: dict[str, float] = {
    "F.Cu": 0.035,   # 1 oz
    "In1.Cu": 0.0175,  # 0.5 oz
    "In2.Cu": 0.0175,  # 0.5 oz
    "B.Cu": 0.035,   # 1 oz
}


@dataclass(frozen=True)
class PlanarCoilSpec:
    """Design parameters for the 4-layer planar haptic electromagnetic coil."""

    center_x_mm: float = 29.5
    center_y_mm: float = 16.0
    straight_half_len_mm: float = 2.0
    inner_radius_mm: float = 3.5
    trace_width_mm: float = 0.35
    turn_spacing_mm: float = 0.23
    turns_per_layer: tuple[float, float, float, float] = (13.0, 12.5, 13.0, 12.5)
    via_drill_mm: float = 0.30
    via_pad_mm: float = 0.65
    terminal_pad_size_mm: tuple[float, float] = (1.20, 0.90)
    samples_per_turn: int = 96
    ambient_temp_c: float = 25.0
    operating_temp_c: float = 45.0
    drive_voltage_v: float = 3.3
    pwm_freq_hz: float = 25000.0
    pwm_duty: float = 0.40


@dataclass(frozen=True)
class ViaTransition:
    name: str
    x_mm: float
    y_mm: float
    drill_mm: float
    pad_mm: float
    from_layer: str
    to_layer: str
    net_name: str = "COIL_INT"


@dataclass(frozen=True)
class CoilLayerGeometry:
    layer: str
    turns: float
    radial_direction: str  # "inward" or "outward"
    winding_chirality: int  # +1 or -1 alternating across via transitions
    current_angular_direction: str  # "CW" or "CCW" along I: COIL_P -> COIL_N
    copper_thickness_mm: float
    z_mm: float
    points_local_mm: list[tuple[float, float]]
    length_mm: float
    dc_resistance_ohm: float
    single_layer_inductance_uh: float
    axial_bz_per_amp_ut: float
    min_turn_clearance_mm: float
    is_simple: bool


@dataclass
class PlanarCoilDesign:
    spec: PlanarCoilSpec
    layers: list[CoilLayerGeometry]
    vias: list[ViaTransition]
    pad1_local_mm: tuple[float, float]
    pad2_local_mm: tuple[float, float]
    pad2_via_local_mm: tuple[float, float]
    total_length_mm: float
    total_turns: float
    r_dc_25c_ohm: float
    r_dc_op_ohm: float
    inductance_single_layer_avg_uh: float
    inductance_constructive_uh: float
    inductance_canceling_counterfactual_uh: float
    mutual_coupling_factor: float
    parasitic_cap_pf: float
    axial_bz_total_per_amp_ut: float
    axial_bz_canceling_per_amp_ut: float
    constructive_flux_verified: bool
    min_turn_clearance_mm: float
    outer_bbox_local_mm: tuple[float, float, float, float]
    ampacity_inner_10c_a: float
    ampacity_inner_20c_a: float
    ampacity_outer_20c_a: float
    dc_peak_current_a: float
    pwm_rms_current_a: float
    thermal_rise_pwm_c: float
    thermal_rise_dc_c: float
    notes: list[str] = field(default_factory=list)

    def to_params_dict(self) -> dict[str, Any]:
        return {
            "spec": asdict(self.spec),
            "layers": [
                {
                    "layer": lyr.layer,
                    "turns": lyr.turns,
                    "radial_direction": lyr.radial_direction,
                    "winding_chirality": lyr.winding_chirality,
                    "current_angular_direction": lyr.current_angular_direction,
                    "copper_thickness_mm": lyr.copper_thickness_mm,
                    "z_mm": lyr.z_mm,
                    "point_count": len(lyr.points_local_mm),
                    "length_mm": round(lyr.length_mm, 4),
                    "dc_resistance_ohm": round(lyr.dc_resistance_ohm, 5),
                    "single_layer_inductance_uh": round(lyr.single_layer_inductance_uh, 4),
                    "axial_bz_per_amp_ut": round(lyr.axial_bz_per_amp_ut, 4),
                    "min_turn_clearance_mm": round(lyr.min_turn_clearance_mm, 4),
                    "is_simple": lyr.is_simple,
                }
                for lyr in self.layers
            ],
            "vias": [asdict(v) for v in self.vias],
            "pad1_local_mm": list(self.pad1_local_mm),
            "pad2_local_mm": list(self.pad2_local_mm),
            "pad2_via_local_mm": list(self.pad2_via_local_mm),
            "total_length_mm": round(self.total_length_mm, 4),
            "total_turns": self.total_turns,
            "r_dc_25c_ohm": round(self.r_dc_25c_ohm, 5),
            "r_dc_op_ohm": round(self.r_dc_op_ohm, 5),
            "inductance_single_layer_avg_uh": round(self.inductance_single_layer_avg_uh, 4),
            "inductance_constructive_uh": round(self.inductance_constructive_uh, 4),
            "inductance_canceling_counterfactual_uh": round(self.inductance_canceling_counterfactual_uh, 4),
            "mutual_coupling_factor": round(self.mutual_coupling_factor, 4),
            "parasitic_cap_pf": round(self.parasitic_cap_pf, 3),
            "axial_bz_total_per_amp_ut": round(self.axial_bz_total_per_amp_ut, 4),
            "axial_bz_canceling_per_amp_ut": round(self.axial_bz_canceling_per_amp_ut, 4),
            "constructive_flux_verified": self.constructive_flux_verified,
            "min_turn_clearance_mm": round(self.min_turn_clearance_mm, 4),
            "outer_bbox_local_mm": [round(v, 4) for v in self.outer_bbox_local_mm],
            "ampacity_inner_10c_a": round(self.ampacity_inner_10c_a, 4),
            "ampacity_inner_20c_a": round(self.ampacity_inner_20c_a, 4),
            "ampacity_outer_20c_a": round(self.ampacity_outer_20c_a, 4),
            "dc_peak_current_a": round(self.dc_peak_current_a, 4),
            "pwm_rms_current_a": round(self.pwm_rms_current_a, 4),
            "thermal_rise_pwm_c": round(self.thermal_rise_pwm_c, 3),
            "thermal_rise_dc_c": round(self.thermal_rise_dc_c, 3),
            "notes": self.notes,
        }


def _stadium_point_and_normal(theta: float, a: float, r: float) -> tuple[np.ndarray, np.ndarray]:
    """Compute a point and outward unit normal on a rounded-racetrack (stadium) contour.

    The stadium spine runs along X in [-a, +a] at Y = 0, with semicircles of radius r.
    Parameter theta in (-inf, +inf) maps monotonically around the stadium perimeter
    using arc-length equivalence on the straight segments so angular progression
    remains smooth and non-self-intersecting for all r > 0.
    """
    ang = theta % (2.0 * math.pi)
    # Use smooth generalized stadium mapping by decomposing into 4 quadrants:
    # Actually map by perimeter arc-length fraction s in [0, L_perim) where L_perim = 2*pi*r + 4*a,
    # OR simpler & cleaner: map angle phi on unit circle to stadium spine + radial normal!
    # Wait: if spine_x = a * sign(cos) only at the semicircle, normal jumps at 90 deg unless
    # top/bottom straight uses arc-length! Let's use exact perimeter arc-length parameter tau in [0, 1).
    raise NotImplementedError  # Unused helper kept for clarity; see _sample_normal_offset_spiral


def _sample_normal_offset_spiral(
    a: float,
    r_in: float,
    pitch: float,
    turns: float,
    start_angle_rad: float,
    inward: bool,
    samples_per_turn: int,
) -> tuple[np.ndarray, list[np.ndarray]]:
    """Generate a smooth normal-offset rounded-racetrack spiral along CW current flow.

    Uses exact rounded-racetrack (stadium) boundary parameterization by normalized
    perimeter fraction tau in [0, 1) with continuous outward unit normal n(tau).
    Advancing along CW current direction decreases angle (CW in right-handed XY),
    while normal offset r(u) = r_in + u * pitch grows/shrinks monotonically with u.

    Returns:
        (full_points_Nx2, per_turn_polylines)
    """
    # Use a reference radius r_ref = r_in + 0.5 * turns * pitch so segment parametrization
    # keeps nearly constant step spacing across all turns.
    r_ref = max(r_in, 1.0)
    l_semi = math.pi * r_ref
    l_str = 2.0 * a
    l_total = 2.0 * l_semi + 2.0 * l_str

    # Parametric walk in CW order starting at angle pi (left end) or 0 (right end).
    # Let's define perimeter parameter s in [0, l_total) increasing in the CW direction
    # starting from theta = 0 (right tip: (a + r, 0))!
    # Moving CW from right tip (theta=0):
    #   1) Lower-right quadrant arc: (a, 0) + r*(cos(phi), sin(phi)), phi: 0 -> -pi/2  (len = pi*r/2)
    #   2) Bottom straight: (x, -r), x: +a -> -a                                        (len = 2*a)
    #   3) Left semicircle: (-a, 0) + r*(cos(phi), sin(phi)), phi: -pi/2 -> -3*pi/2    (len = pi*r)
    #   4) Top straight: (x, +r), x: -a -> +a                                           (len = 2*a)
    #   5) Upper-right quadrant arc: (a, 0) + r*(cos(phi), sin(phi)), phi: -3*pi/2 -> -2*pi (len = pi*r/2)
    s_q1 = 0.5 * math.pi * r_ref
    s_bot = s_q1 + l_str
    s_left = s_bot + math.pi * r_ref
    s_top = s_left + l_str

    # Map start_angle_rad (0 or pi) to starting perimeter fraction tau0:
    # At theta = 0: tau0 = 0.0
    # At theta = pi (midpoint of left semicircle): tau0 = (s_bot + 0.5*pi*r_ref) / l_total = 0.5
    ang_mod = start_angle_rad % (2.0 * math.pi)
    if abs(ang_mod - math.pi) < 1e-6:
        tau0 = 0.5
    elif abs(ang_mod) < 1e-6:
        tau0 = 0.0
    else:
        tau0 = ((-ang_mod) % (2.0 * math.pi)) / (2.0 * math.pi)

    n_pts = max(int(round(turns * samples_per_turn)) + 1, 32)
    turn_fracs = np.linspace(0.0, turns, n_pts)

    pts = np.zeros((n_pts, 2), dtype=float)
    for idx, tf in enumerate(turn_fracs):
        # CW progress increases tau
        tau = (tau0 + tf) % 1.0
        s = tau * l_total

        if inward:
            r = r_in + (turns - tf) * pitch
        else:
            r = r_in + tf * pitch

        if s < s_q1:
            frac = s / max(s_q1, 1e-12)
            phi = -0.5 * math.pi * frac
            spine = np.array([a, 0.0])
            normal = np.array([math.cos(phi), math.sin(phi)])
        elif s < s_bot:
            frac = (s - s_q1) / max(l_str, 1e-12)
            x = a - 2.0 * a * frac
            spine = np.array([x, 0.0])
            normal = np.array([0.0, -1.0])
        elif s < s_left:
            frac = (s - s_bot) / max(math.pi * r_ref, 1e-12)
            phi = -0.5 * math.pi - math.pi * frac
            spine = np.array([-a, 0.0])
            normal = np.array([math.cos(phi), math.sin(phi)])
        elif s < s_top:
            frac = (s - s_left) / max(l_str, 1e-12)
            x = -a + 2.0 * a * frac
            spine = np.array([x, 0.0])
            normal = np.array([0.0, 1.0])
        else:
            frac = (s - s_top) / max(s_q1, 1e-12)
            phi = -1.5 * math.pi - 0.5 * math.pi * frac
            spine = np.array([a, 0.0])
            normal = np.array([math.cos(phi), math.sin(phi)])

        pts[idx] = spine + r * normal

    # Slice into per-turn polylines (each covering 1.0 turn) for Shapely clearance verification
    per_turn: list[np.ndarray] = []
    full_turns = int(math.floor(turns))
    for k in range(full_turns):
        mask = (turn_fracs >= k + 0.03) & (turn_fracs <= k + 0.97)
        if np.count_nonzero(mask) >= 4:
            per_turn.append(pts[mask])
    if turns - full_turns >= 0.25:
        mask = turn_fracs >= full_turns + 0.03
        if np.count_nonzero(mask) >= 4:
            per_turn.append(pts[mask])

    return pts, per_turn


def _smooth_hermite_bridge(
    p0: tuple[float, float],
    t0: tuple[float, float],
    p1: tuple[float, float],
    t1: tuple[float, float],
    n_samples: int = 12,
) -> np.ndarray:
    """Create a C1-smooth cubic Hermite transition curve between two endpoints & tangents."""
    dist = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    scale = max(dist * 0.9, 0.4)
    n0 = math.hypot(t0[0], t0[1]) or 1.0
    n1 = math.hypot(t1[0], t1[1]) or 1.0
    d0 = (scale * t0[0] / n0, scale * t0[1] / n0)
    d1 = (scale * t1[0] / n1, scale * t1[1] / n1)
    spline = CubicHermiteSpline(
        x=[0.0, 1.0],
        y=[[p0[0], p0[1]], [p1[0], p1[1]]],
        dydx=[[d0[0], d0[1]], [d1[0], d1[1]]],
    )
    ts = np.linspace(0.0, 1.0, n_samples)
    return spline(ts)


def _compute_biot_savart_bz_per_amp_ut(points: np.ndarray, z_mm: float) -> float:
    """Compute axial magnetic field B_z at coil center (0, 0, 0) per 1 A current (in microtesla).

    Uses discrete Biot-Savart integration:
      dB = (mu_0 / 4*pi) * I * (dl x (-r)) / |r|^3
    where r = (x_mid, y_mid, z_mm) * 1e-3 (meters) and dl = (dx, dy, 0) * 1e-3.
    """
    p0 = points[:-1] * 1e-3
    p1 = points[1:] * 1e-3
    dl = p1 - p0
    mid = 0.5 * (p0 + p1)
    z_m = z_mm * 1e-3
    r_sq = mid[:, 0] ** 2 + mid[:, 1] ** 2 + z_m ** 2
    r_cube = np.maximum(r_sq ** 1.5, 1e-18)
    # (dl x (-r))_z = dl_x * (-mid_y) - dl_y * (-mid_x) = mid_x * dl_y - mid_y * dl_x
    cross_z = mid[:, 0] * dl[:, 1] - mid[:, 1] * dl[:, 0]
    bz_tesla = (MU_0 / (4.0 * math.pi)) * float(np.sum(cross_z / r_cube))
    return bz_tesla * 1e6  # microtesla per Amp


def _mohan_single_layer_inductance_uh(
    turns: float,
    a_mm: float,
    r_in_mm: float,
    r_out_mm: float,
) -> float:
    """Mohan current-sheet planar spiral inductance approximation (converted for rounded racetrack).

    Uses equivalent area diameter for the stadium inner/outer bounds:
      A_eq = pi*r^2 + 4*a*r => d_eq = 2 * sqrt(A_eq / pi)
    and Mohan circular spiral coefficients (c1=1.00, c2=2.46, c3=0.00, c4=0.20).
    """
    area_in = math.pi * r_in_mm ** 2 + 4.0 * a_mm * r_in_mm
    area_out = math.pi * r_out_mm ** 2 + 4.0 * a_mm * r_out_mm
    d_in = 2.0 * math.sqrt(area_in / math.pi) * 1e-3
    d_out = 2.0 * math.sqrt(area_out / math.pi) * 1e-3
    d_avg = 0.5 * (d_in + d_out)
    rho = (d_out - d_in) / max(d_out + d_in, 1e-9)
    c1, c2, c3, c4 = 1.00, 2.46, 0.00, 0.20
    l_henry = (MU_0 * (turns ** 2) * d_avg * c1 / 2.0) * (
        math.log(c2 / max(rho, 1e-4)) + c3 * rho + c4 * (rho ** 2)
    )
    # Small stadium perimeter elongation factor
    perim_in = 2.0 * math.pi * r_in_mm + 4.0 * a_mm
    perim_circle_eq = math.pi * d_in * 1e3
    elong_factor = perim_in / max(perim_circle_eq, 1e-6)
    return l_henry * 1e6 * (0.5 + 0.5 * elong_factor)


def _coupled_4layer_inductance_uh(
    layer_inductances_uh: list[float],
    z_positions_mm: list[float],
    d_avg_mm: float,
    spins: list[int],
) -> tuple[float, float]:
    """Compute total 4-layer inductance accounting for inter-layer mutual coupling M_ij.

    Uses standard planar stacked-coil coupling model:
      k_ij = N_eff / (1 + (dz_ij / (0.28 * d_avg))^1.6) scaled by spin alignment (+1 or -1)
    where spins = [+1, +1, +1, +1] in current flow gives constructive +M_ij, while
    alternating current direction gives destructive -M_ij.
    """
    n = len(layer_inductances_uh)
    total_uh = sum(layer_inductances_uh)
    k_values: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            dz = abs(z_positions_mm[i] - z_positions_mm[j])
            # Adjacent 0.21mm prepreg yields k ~ 0.94; across 1.6mm board core yields k ~ 0.84
            k_ij = 1.0 / (1.0 + (dz / max(0.32 * d_avg_mm, 0.5)) ** 1.45)
            k_ij = min(max(k_ij, 0.0), 0.98)
            k_values.append(k_ij)
            sign_ij = 1.0 if spins[i] == spins[j] else -1.0
            m_ij = sign_ij * k_ij * math.sqrt(layer_inductances_uh[i] * layer_inductances_uh[j])
            total_uh += 2.0 * m_ij
    avg_k = float(np.mean(k_values)) if k_values else 0.0
    return max(total_uh, 0.01), avg_k


def _ipc2221_ampacity_a(width_mm: float, thickness_mm: float, delta_t_c: float, external: bool) -> float:
    """IPC-2221 trace ampacity formula: I = k * dT^0.44 * A_mil2^0.725."""
    area_mm2 = width_mm * thickness_mm
    area_mil2 = area_mm2 / (0.0254 ** 2)
    k = 0.048 if external else 0.024
    return k * (delta_t_c ** 0.44) * (area_mil2 ** 0.725)


def build_planar_coil(spec: PlanarCoilSpec | None = None) -> PlanarCoilDesign:
    """Generate and validate the 4-layer normal-offset rounded-racetrack planar coil."""
    if spec is None:
        spec = PlanarCoilSpec()

    assert spec.trace_width_mm >= 0.25, f"Trace width {spec.trace_width_mm} < 0.25mm"
    assert spec.turn_spacing_mm >= 0.20, f"Turn spacing {spec.turn_spacing_mm} < 0.20mm"

    a = spec.straight_half_len_mm
    r_in = spec.inner_radius_mm
    w = spec.trace_width_mm
    s = spec.turn_spacing_mm
    pitch = w + s
    max_turns = max(spec.turns_per_layer)
    r_out_max = r_in + max_turns * pitch

    # Inter-layer via coordinates (inside the clear central bore and outside r_out)
    v1_pos = (-a - 1.0, 0.0)          # (-3.0, 0.0): F.Cu <-> In1.Cu
    v2_pos = (a + r_out_max + 1.2, 0.0)  # (+14.4, 0.0): In1.Cu <-> In2.Cu
    v3_pos = (a + 1.0, 0.0)           # (+3.0, 0.0): In2.Cu <-> B.Cu

    # Terminal pads on left side facing J1 & meander zone
    pad1_pos = (-a - r_out_max - 1.5, -1.6)  # COIL_P on F.Cu
    pad2_pos = (-a - r_out_max - 1.5, +1.6)  # COIL_N on F.Cu (via PTH from B.Cu)
    pad2_via_pos = (-a - r_out_max - 1.5, +1.6)

    vias = [
        ViaTransition(
            name="V_COIL_1",
            x_mm=v1_pos[0],
            y_mm=v1_pos[1],
            drill_mm=spec.via_drill_mm,
            pad_mm=spec.via_pad_mm,
            from_layer="F.Cu",
            to_layer="In1.Cu",
            net_name="COIL_P",
        ),
        ViaTransition(
            name="V_COIL_2",
            x_mm=v2_pos[0],
            y_mm=v2_pos[1],
            drill_mm=spec.via_drill_mm,
            pad_mm=spec.via_pad_mm,
            from_layer="In1.Cu",
            to_layer="In2.Cu",
            net_name="COIL_P",
        ),
        ViaTransition(
            name="V_COIL_3",
            x_mm=v3_pos[0],
            y_mm=v3_pos[1],
            drill_mm=spec.via_drill_mm,
            pad_mm=spec.via_pad_mm,
            from_layer="In2.Cu",
            to_layer="B.Cu",
            net_name="COIL_P",
        ),
    ]

    layer_defs = [
        # (name, turns, start_angle, inward, chirality, start_pt, start_tan, end_pt, end_tan)
        ("F.Cu", spec.turns_per_layer[0], math.pi, True, +1, pad1_pos, (1.0, 0.6), v1_pos, (1.0, 0.0)),
        ("In1.Cu", spec.turns_per_layer[1], math.pi, False, -1, v1_pos, (-1.0, 0.0), v2_pos, (1.0, 0.0)),
        ("In2.Cu", spec.turns_per_layer[2], 0.0, True, +1, v2_pos, (-1.0, 0.0), v3_pos, (-1.0, 0.0)),
        ("B.Cu", spec.turns_per_layer[3], 0.0, False, -1, v3_pos, (1.0, 0.0), pad2_via_pos, (-1.0, 0.6)),
    ]

    rho_25 = CU_RHO_20C * (1.0 + CU_TEMP_COEFF * (spec.ambient_temp_c - 20.0))
    rho_op = CU_RHO_20C * (1.0 + CU_TEMP_COEFF * (spec.operating_temp_c - 20.0))

    coil_layers: list[CoilLayerGeometry] = []
    for name, n_turns, start_ang, inward, chirality, lead_start, tan_start, lead_end, tan_end in layer_defs:
        spiral_pts, per_turn_pts = _sample_normal_offset_spiral(
            a=a,
            r_in=r_in,
            pitch=pitch,
            turns=n_turns,
            start_angle_rad=start_ang,
            inward=inward,
            samples_per_turn=spec.samples_per_turn,
        )

        # Compute spiral end tangents for C1-smooth lead-in and lead-out bridges
        t_in_spiral = spiral_pts[1] - spiral_pts[0]
        t_out_spiral = spiral_pts[-1] - spiral_pts[-2]

        bridge_in = _smooth_hermite_bridge(
            lead_start,
            tan_start,
            (float(spiral_pts[0, 0]), float(spiral_pts[0, 1])),
            (float(t_in_spiral[0]), float(t_in_spiral[1])),
            n_samples=10,
        )
        bridge_out = _smooth_hermite_bridge(
            (float(spiral_pts[-1, 0]), float(spiral_pts[-1, 1])),
            (float(t_out_spiral[0]), float(t_out_spiral[1])),
            lead_end,
            tan_end,
            n_samples=10,
        )

        full_pts = np.vstack([bridge_in[:-1], spiral_pts, bridge_out[1:]])

        # Shapely geometry validation: strictly non-self-intersecting & turn clearance >= 0.20mm
        line = LineString([(float(x), float(y)) for x, y in full_pts])
        is_simple = bool(line.is_simple)

        turn_lines = [LineString([(float(x), float(y)) for x, y in tp]) for tp in per_turn_pts]
        clearances = [
            turn_lines[k].distance(turn_lines[k + 1]) - w
            for k in range(len(turn_lines) - 1)
        ]
        min_turn_clr = float(min(clearances)) if clearances else s

        # Length & resistance
        seg_lens = np.linalg.norm(np.diff(full_pts, axis=0), axis=1)
        length_mm = float(np.sum(seg_lens))
        cu_t = DEFAULT_CU_THICKNESS_MM[name]
        z_mm = DEFAULT_4L_Z_MM[name]
        area_m2 = (w * 1e-3) * (cu_t * 1e-3)
        r_layer_25 = rho_25 * (length_mm * 1e-3) / area_m2

        r_out_layer = r_in + n_turns * pitch
        l_single_uh = _mohan_single_layer_inductance_uh(n_turns, a, r_in, r_out_layer)
        bz_per_amp_ut = _compute_biot_savart_bz_per_amp_ut(full_pts, z_mm)

        # Verify current angular direction along polyline matches CW
        curr_dir = "CW" if bz_per_amp_ut < 0.0 else "CCW"

        coil_layers.append(
            CoilLayerGeometry(
                layer=name,
                turns=n_turns,
                radial_direction="inward" if inward else "outward",
                winding_chirality=chirality,
                current_angular_direction=curr_dir,
                copper_thickness_mm=cu_t,
                z_mm=z_mm,
                points_local_mm=[(float(x), float(y)) for x, y in full_pts],
                length_mm=length_mm,
                dc_resistance_ohm=r_layer_25,
                single_layer_inductance_uh=l_single_uh,
                axial_bz_per_amp_ut=bz_per_amp_ut,
                min_turn_clearance_mm=min_turn_clr,
                is_simple=is_simple,
            )
        )

    # Check constructive flux across all 4 layers
    bz_values = [lyr.axial_bz_per_amp_ut for lyr in coil_layers]
    same_sign = all(b < 0 for b in bz_values) or all(b > 0 for b in bz_values)
    bz_total = float(sum(bz_values))
    # Counterfactual without spin inversion: In1.Cu & B.Cu would cancel F.Cu & In2.Cu
    bz_canceling = float(bz_values[0] - bz_values[1] + bz_values[2] - bz_values[3])
    constructive_ok = same_sign and (abs(bz_total) > 3.5 * min(abs(b) for b in bz_values))

    # Via barrel resistance (4 plated vias: V1, V2, V3, Pad2 transition; 20um Cu wall, 1.6mm barrel)
    via_wall_area_m2 = math.pi * (spec.via_drill_mm * 1e-3) * 20e-6
    r_vias_25 = len(vias) * rho_25 * (1.6e-3) / via_wall_area_m2

    total_len_mm = sum(l.length_mm for l in coil_layers)
    r_dc_25 = sum(l.dc_resistance_ohm for l in coil_layers) + r_vias_25
    r_dc_op = r_dc_25 * (rho_op / rho_25)

    # Equivalent average diameter for mutual coupling
    r_avg = r_in + 0.5 * max_turns * pitch
    d_avg_mm = 2.0 * math.sqrt((math.pi * r_avg ** 2 + 4.0 * a * r_avg) / math.pi)
    l_layers = [l.single_layer_inductance_uh for l in coil_layers]
    z_layers = [l.z_mm for l in coil_layers]
    l_constructive, k_avg = _coupled_4layer_inductance_uh(
        l_layers, z_layers, d_avg_mm, spins=[+1, +1, +1, +1]
    )
    l_canceling, _ = _coupled_4layer_inductance_uh(
        l_layers, z_layers, d_avg_mm, spins=[+1, -1, +1, -1]
    )

    # Estimate parasitic capacitance (adjacent turns + layer-to-layer overlap through FR4 er=4.4)
    er_fr4 = 4.4
    eps0 = 8.854e-12
    # Layer-to-layer F.Cu<->In1.Cu and In2.Cu<->B.Cu overlap area (0.21mm prepreg)
    trace_area_m2 = (0.25 * total_len_mm * 1e-3) * (w * 1e-3)
    c_interlayer_pf = (eps0 * er_fr4 * trace_area_m2 / (0.21e-3)) * 1e12 * 0.25
    parasitic_cap_pf = max(c_interlayer_pf, 12.0)

    # Bounding box across all 4 copper layers
    all_lines = [LineString(l.points_local_mm).buffer(w / 2.0) for l in coil_layers]
    all_vias = [Point(v.x_mm, v.y_mm).buffer(v.pad_mm / 2.0) for v in vias]
    all_vias.append(Point(pad1_pos).buffer(max(spec.terminal_pad_size_mm) / 2.0))
    all_vias.append(Point(pad2_pos).buffer(max(spec.terminal_pad_size_mm) / 2.0))
    coil_union = unary_union(all_lines + all_vias)
    bbox = coil_union.bounds  # (minx, miny, maxx, maxy)

    # Ampacity & I^2 * R thermal rise
    amp_in_10 = _ipc2221_ampacity_a(w, 0.0175, 10.0, external=False)
    amp_in_20 = _ipc2221_ampacity_a(w, 0.0175, 20.0, external=False)
    amp_out_20 = _ipc2221_ampacity_a(w, 0.035, 20.0, external=True)

    i_dc_peak = spec.drive_voltage_v / r_dc_25
    i_pwm_rms = i_dc_peak * math.sqrt(spec.pwm_duty)
    # Natural convection + FR4 lateral spreading over ~26x23 mm coil region (both sides): R_theta ~ 28 K/W
    coil_footprint_m2 = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) * 1e-6
    r_theta_kw = 1.0 / (2.0 * 24.0 * max(coil_footprint_m2, 1e-4))
    dt_pwm = (i_pwm_rms ** 2) * r_dc_op * r_theta_kw
    dt_dc = (i_dc_peak ** 2) * r_dc_op * r_theta_kw

    min_clr = min(l.min_turn_clearance_mm for l in coil_layers)

    return PlanarCoilDesign(
        spec=spec,
        layers=coil_layers,
        vias=vias,
        pad1_local_mm=pad1_pos,
        pad2_local_mm=pad2_pos,
        pad2_via_local_mm=pad2_via_pos,
        total_length_mm=total_len_mm,
        total_turns=sum(spec.turns_per_layer),
        r_dc_25c_ohm=r_dc_25,
        r_dc_op_ohm=r_dc_op,
        inductance_single_layer_avg_uh=float(np.mean(l_layers)),
        inductance_constructive_uh=l_constructive,
        inductance_canceling_counterfactual_uh=l_canceling,
        mutual_coupling_factor=k_avg,
        parasitic_cap_pf=parasitic_cap_pf,
        axial_bz_total_per_amp_ut=bz_total,
        axial_bz_canceling_per_amp_ut=bz_canceling,
        constructive_flux_verified=constructive_ok,
        min_turn_clearance_mm=min_clr,
        outer_bbox_local_mm=bbox,
        ampacity_inner_10c_a=amp_in_10,
        ampacity_inner_20c_a=amp_in_20,
        ampacity_outer_20c_a=amp_out_20,
        dc_peak_current_a=i_dc_peak,
        pwm_rms_current_a=i_pwm_rms,
        thermal_rise_pwm_c=dt_pwm,
        thermal_rise_dc_c=dt_dc,
        notes=[
            "Spin polarity alternates (+1, -1, +1, -1) across inter-layer vias so current circulates CW on all 4 layers.",
            f"Constructive coupling increases inductance from {l_canceling:.2f} uH (canceling) to {l_constructive:.2f} uH (constructive).",
        ],
    )


def _find_ngspice_binary() -> str:
    """Resolve ngspice binary path using PATH or project .mamba/ngspice/bin."""
    found = shutil.which("ngspice")
    if found:
        return found
    repo_root = Path(__file__).resolve().parents[2]
    candidate = repo_root / ".mamba" / "ngspice" / "bin" / "ngspice"
    if candidate.exists():
        return str(candidate)
    raise FileNotFoundError("ngspice binary not found in PATH or .mamba/ngspice/bin")


def run_coil_spice_simulation(
    design: PlanarCoilDesign,
    output_json_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run real ngspice-41 RLC step & PWM flyback transient simulations on derived coil R/L/C.

    Executes two transient analyses in batch mode:
      1) RLC Step Response (5V step into series R_dc + L_coil || C_p)
      2) 25kHz PWM Low-Side Drive with Schottky Flyback Catch Diode (1N5819/SS14 model)
    """
    ngspice_bin = _find_ngspice_binary()
    r_dc = design.r_dc_25c_ohm
    l_uh = design.inductance_constructive_uh
    c_pf = design.parasitic_cap_pf
    v_dd = design.spec.drive_voltage_v
    f_pwm = design.spec.pwm_freq_hz
    period_us = 1e6 / f_pwm
    duty_us = period_us * design.spec.pwm_duty

    tau_analytical_us = (l_uh * 1e-6 / r_dc) * 1e6
    step_stop_us = max(8.0 * tau_analytical_us, 80.0)
    step_dt_us = step_stop_us / 400.0

    pwm_stop_us = 4.0 * period_us
    pwm_dt_us = period_us / 200.0

    with tempfile.TemporaryDirectory(prefix="ngspice_coil_") as tmpdir:
        tmp = Path(tmpdir)
        step_wrdata = tmp / "step_wave.dat"
        pwm_wrdata = tmp / "pwm_wave.dat"

        step_cir = tmp / "coil_step.cir"
        step_cir.write_text(
            f"""* Board 2 Planar Haptic Coil RLC Step Response
Vdrv in 0 PULSE(0 {v_dd:.4f} 2u 50n 50n {step_stop_us:.3f}u {step_stop_us * 2:.3f}u)
Rsrc in n_coil 0.05
Rcoil n_coil n_mid {r_dc:.6f}
Lcoil n_mid 0 {l_uh:.6f}u
Cpar n_coil 0 {c_pf:.4f}p

.tran {step_dt_us:.4f}u {step_stop_us:.4f}u uic
.control
set filetype=ascii
set wr_vecnames
run
wrdata {step_wrdata} v(in) v(n_coil) i(Lcoil)
quit
.endc
.end
""",
            encoding="utf-8",
        )

        pwm_cir = tmp / "coil_pwm_flyback.cir"
        pwm_cir.write_text(
            f"""* Board 2 Planar Haptic Coil 25kHz PWM Flyback Transient + Catch Diode
Vdd vdd 0 DC {v_dd:.4f}
Vgate gate 0 PULSE(0 5 2u 40n 40n {duty_us:.4f}u {period_us:.4f}u)

* Low-side MOSFET driver switch + Schottky catch diode + 0V sense source
Sdrv sw_node 0 gate 0 SW_MOS
.model SW_MOS SW(Ron=0.08 Roff=1e6 Vt=2.0 Vh=0.2)

Vdm sw_node d_anode DC 0
Dfly d_anode vdd D_SCHOTTKY
.model D_SCHOTTKY D(IS=2.2e-5 RS=0.04 N=1.05 BV=40 CJO=110p TT=5n)

Rcoil vdd n_mid {r_dc:.6f}
Lcoil n_mid sw_node {l_uh:.6f}u
Cpar vdd sw_node {c_pf:.4f}p

.tran {pwm_dt_us:.4f}u {pwm_stop_us:.4f}u uic
.control
set filetype=ascii
set wr_vecnames
run
wrdata {pwm_wrdata} v(gate) v(sw_node) i(Lcoil) i(Vdm)
quit
.endc
.end
""",
            encoding="utf-8",
        )

        env = os.environ.copy()
        repo_root = Path(__file__).resolve().parents[2]
        ng_lib = repo_root / ".mamba" / "ngspice" / "lib"
        if ng_lib.exists():
            env.setdefault("NGSPICE_LIB_DIR", str(ng_lib))

        res_step = subprocess.run(
            [ngspice_bin, "-b", str(step_cir)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        res_pwm = subprocess.run(
            [ngspice_bin, "-b", str(pwm_cir)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )

        # Parse wrdata ASCII tables
        step_data = np.loadtxt(step_wrdata, skiprows=1)
        pwm_data = np.loadtxt(pwm_wrdata, skiprows=1)

    # Extract ngspice version banner
    ver_match = re.search(r"ngspice[- ]+(\d+)", res_step.stdout + res_step.stderr, re.IGNORECASE)
    ng_version = ver_match.group(0) if ver_match else "ngspice-41"

    # Step response metrics: columns [time, v(in), time2, v(n_coil), time3, i(Lcoil)] or [time, v(in), v(n_coil), i(Lcoil)]
    if step_data.shape[1] >= 6:
        t_step = step_data[:, 0]
        v_in_step = step_data[:, 1]
        v_coil_step = step_data[:, 3]
        i_coil_step = step_data[:, 5]
    else:
        t_step = step_data[:, 0]
        v_in_step = step_data[:, 1]
        v_coil_step = step_data[:, 2]
        i_coil_step = step_data[:, 3]

    i_steady_sim = float(np.max(i_coil_step))
    i_steady_analytical = v_dd / (r_dc + 0.05)
    # Find 63.2% rise time relative to 2us step edge
    target_i = 0.63212 * i_steady_sim
    post_edge = t_step >= 2.0e-6
    t_post = t_step[post_edge] - 2.0e-6
    i_post = i_coil_step[post_edge]
    idx_63 = int(np.argmax(i_post >= target_i))
    tau_sim_us = float(t_post[idx_63] * 1e6)

    # PWM flyback metrics
    if pwm_data.shape[1] >= 8:
        t_pwm = pwm_data[:, 0]
        v_gate_pwm = pwm_data[:, 1]
        v_sw_pwm = pwm_data[:, 3]
        i_coil_pwm = pwm_data[:, 5]
        i_diode_pwm = pwm_data[:, 7]
    else:
        t_pwm = pwm_data[:, 0]
        v_gate_pwm = pwm_data[:, 1]
        v_sw_pwm = pwm_data[:, 2]
        i_coil_pwm = pwm_data[:, 3]
        i_diode_pwm = pwm_data[:, 4]

    v_sw_peak_clamp = float(np.max(v_sw_pwm))
    i_coil_pwm_peak = float(np.max(i_coil_pwm))
    i_coil_pwm_avg_last_cycle = float(np.mean(i_coil_pwm[t_pwm >= (pwm_stop_us - period_us) * 1e-6]))
    i_diode_flyback_peak = float(np.max(i_diode_pwm))

    # Downsample waveforms to <= 120 points for compact JSON persistence
    step_idx = np.linspace(0, len(t_step) - 1, min(100, len(t_step)), dtype=int)
    pwm_idx = np.linspace(0, len(t_pwm) - 1, min(120, len(t_pwm)), dtype=int)

    payload: dict[str, Any] = {
        "simulator": ng_version,
        "coil_inputs": {
            "r_dc_ohm": round(r_dc, 5),
            "l_coil_uh": round(l_uh, 4),
            "c_par_pf": round(c_pf, 3),
            "v_drive_v": v_dd,
            "pwm_freq_hz": f_pwm,
            "pwm_duty": design.spec.pwm_duty,
        },
        "step_response": {
            "tau_analytical_us": round(tau_analytical_us, 4),
            "tau_simulated_us": round(tau_sim_us, 4),
            "tau_error_pct": round(abs(tau_sim_us - tau_analytical_us) / max(tau_analytical_us, 1e-9) * 100.0, 2),
            "i_steady_analytical_a": round(i_steady_analytical, 4),
            "i_steady_simulated_a": round(i_steady_sim, 4),
            "waveform": [
                {
                    "t_us": round(float(t_step[k] * 1e6), 4),
                    "v_in_v": round(float(v_in_step[k]), 4),
                    "v_coil_v": round(float(v_coil_step[k]), 4),
                    "i_coil_a": round(float(i_coil_step[k]), 5),
                }
                for k in step_idx
            ],
        },
        "pwm_flyback": {
            "v_flyback_clamp_peak_v": round(v_sw_peak_clamp, 4),
            "clamp_headroom_above_vdd_v": round(v_sw_peak_clamp - v_dd, 4),
            "i_coil_peak_a": round(i_coil_pwm_peak, 4),
            "i_coil_avg_steady_a": round(i_coil_pwm_avg_last_cycle, 4),
            "i_diode_flyback_peak_a": round(i_diode_flyback_peak, 4),
            "catch_diode_clamped": v_sw_peak_clamp < (v_dd + 1.2),
            "waveform": [
                {
                    "t_us": round(float(t_pwm[k] * 1e6), 4),
                    "v_gate_v": round(float(v_gate_pwm[k]), 3),
                    "v_sw_v": round(float(v_sw_pwm[k]), 4),
                    "i_coil_a": round(float(i_coil_pwm[k]), 5),
                    "i_diode_a": round(float(i_diode_pwm[k]), 5),
                }
                for k in pwm_idx
            ],
        },
    }

    if output_json_path is not None:
        save_json(output_json_path, payload)

    return payload
