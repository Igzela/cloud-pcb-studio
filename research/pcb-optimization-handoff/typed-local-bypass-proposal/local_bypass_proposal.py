"""Geometry-only proposal for shunts on demonstrable local supply domains."""
from __future__ import annotations

import math
import re

from cloud_pcb.general_solver.electrical import _typed_supply_pad, supply_routing_scope

SCALES = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3}


def explicit_farads(value: str) -> float | None:
    """A bare number or fallback footprint estimate cannot prove a local shunt."""
    value = value.strip().lower().replace("µ", "u").replace("μ", "u")
    inline = re.match(r"^(\d+)([pnum])(\d+)(?:f)?(?:$|[ /])", value)
    standard = re.match(r"^(\d+(?:\.\d+)?)\s*([pnum])(?:f)?(?:$|[ /])", value)
    if inline:
        whole, unit, fraction = inline.groups()
        return float(f"{whole}.{fraction}") * SCALES[unit]
    if standard:
        whole, unit = standard.groups()
        return float(whole) * SCALES[unit]
    return None


def typed_local_shunts(spec: dict) -> dict:
    """Return targets without reclassifying any supply or modifying the input."""
    parts = {c["refdes"]: c for c in spec["components"]}
    typed_nets = {
        p["net"] for c in parts.values() for p in c.get("pads", ())
        if p.get("net") and p.get("pin_type") == "power_in" and _typed_supply_pad(p)
    }
    decisions = {}
    for net in sorted(typed_nets):
        evidence = supply_routing_scope(spec, net)
        if evidence["scope"] != "single_ic_domain":
            continue
        owner = parts[evidence["owner_ref"]]
        input_pins = sorted({
            str(p["num"]) for p in owner["pads"]
            if p.get("net") == net and p.get("pin_type") == "power_in" and _typed_supply_pad(p)
        })
        if not input_pins:
            continue
        for ref in evidence["shunt_caps"]:
            cap = parts[ref]
            value = explicit_farads(cap.get("value", ""))
            if value is None or not 0 < value <= 1e-6 or len(cap["pads"]) != 2:
                continue
            if any(p.get("type") != "smd" or not any(
                layer.endswith(".Cu") for layer in p.get("layers", ())
            ) for p in cap["pads"]):
                continue
            if sum(p.get("net") == net for p in cap["pads"]) != 1:
                continue
            decisions[ref] = (net, owner["refdes"], tuple(input_pins))
    return decisions


def move_local_shunt(original, rail_offset, target_pads, legal, *, radius_limit=8.):
    """Try a bounded same-side move around actual pads, never relax legality.

    rail_offset is in the capacitor's existing side-corrected local frame.
    legal must be the caller's authoritative geometry check with the current
    courtyard margin and own footprint ignored.
    """
    target_pads = tuple(sorted(set(target_pads)))
    if not target_pads:
        return original

    def rotated_offset(angle):
        phi = math.radians(angle)
        x, y = rail_offset
        return x * math.cos(phi) + y * math.sin(phi), -x * math.sin(phi) + y * math.cos(phi)

    def distance(pose):
        x, y, angle = pose
        dx, dy = rotated_offset(angle)
        return min(math.hypot(x + dx - px, y + dy - py) for px, py in target_pads)

    original_distance = distance(original)
    directions = tuple((math.cos(i * math.tau / 16), math.sin(i * math.tau / 16)) for i in range(16))
    best = (original_distance, 0., original)
    checked = set()
    max_step = math.floor(2 * min(radius_limit, original_distance))
    for step in range(max_step + 1):
        radius = step / 2
        for px, py in target_pads:
            for ux, uy in directions:
                for rotation in (0., 90., 180., 270.):
                    dx, dy = rotated_offset(rotation)
                    pose = (round(px + ux * radius - dx, 3),
                            round(py + uy * radius - dy, 3), rotation)
                    if pose in checked:
                        continue
                    checked.add(pose)
                    gap = distance(pose)
                    if gap >= original_distance - 1e-6:
                        continue
                    key = (gap, math.hypot(pose[0] - original[0], pose[1] - original[1]), pose)
                    if key < best and legal(*pose):
                        best = key
    return best[2]
