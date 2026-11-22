"""Specctra DSN / SES bridge for kiutils KiCad PCB boards.

Supports:
- Exporting a kiutils Board + NetClassRule dictionary into a Specctra .dsn file
  compatible with both Rust `fastroute v0.1.14` and native `freerouting-cli v2.5.0`.
- Parsing Specctra .ses files (handling arbitrary resolution multipliers) and merging
  routed Segment + Via geometry back into a kiutils Board.
"""

from __future__ import annotations

import math
import re
import uuid
from pathlib import Path
from typing import Any

from kiutils.board import Board
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Position

from contracts.schemas import NetClassRule

DEFAULT_VIA_NAME = "Via[0-3]_550:300_um"
COPPER_LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")
NPTH_COPPER_CLEARANCE_MM = 0.50


def _quote(token: str) -> str:
    if not token or any(ch.isspace() or ch in '()"' for ch in token):
        escaped = token.replace('"', '\\"')
        return f'"{escaped}"'
    return token


def _quoted_pin_identifier(token: str) -> str:
    """Quote one identifier using Freerouting's network token grammar."""
    if not token or '"' in token or any(ord(ch) < 32 or ord(ch) == 127 for ch in token):
        raise ValueError("Freerouting pin identifiers cannot contain quotes or control characters")
    return f'"{token}"'


def _secondary_pin_reference(ref: str, number: str) -> str:
    # The JVM reader scans the component, a literal hyphen, then the pin.
    # Quoting a combined identifier hides the separator from that reader.
    return f'{_quoted_pin_identifier(ref)}-{_quoted_pin_identifier(number)}'


def _rotate_kicad(dx: float, dy: float, angle_deg: float) -> tuple[float, float]:
    """Rotate local KiCad footprint offset (dx, dy) by KiCad footprint angle (CCW in Y-up)."""
    if abs(angle_deg) < 1e-9:
        return dx, dy
    rad = math.radians(angle_deg)
    c = math.cos(rad)
    s = math.sin(rad)
    # In KiCad Y-down coordinates, positive angle rotates CCW on screen (Y-up equivalent)
    return dx * c + dy * s, -dx * s + dy * c


def _pad_world_dims(w: float, h: float, total_angle_deg: float) -> tuple[float, float]:
    norm = round(total_angle_deg) % 180
    if norm == 90:
        return h, w
    return w, h


def _footprint_ref(fp: Any, idx: int) -> str:
    for prop_key in ("Reference", "reference"):
        if prop_key in getattr(fp, "properties", {}):
            val = str(fp.properties[prop_key]).strip()
            if val:
                return val
    for gi in getattr(fp, "graphicItems", []):
        if getattr(gi, "type", None) == "reference" and getattr(gi, "text", None):
            return str(gi.text).strip()
    return f"FP{idx}"


def _npth_keepout_lines(fp: Any, pad: Any) -> list[str]:
    """Protect the non-plated round or oblong drill on all layers."""
    from shapely.geometry import MultiPoint

    drill = pad.drill
    if drill is None:
        raise ValueError("NPTH pad has no drill definition")
    width = float(drill.diameter)
    height = float(drill.width) if drill.oval and drill.width is not None else width
    if any(not math.isfinite(dimension) or dimension <= 0 for dimension in (width, height)):
        raise ValueError("NPTH drill dimensions must be finite and positive")

    dx, dy = _rotate_kicad(pad.position.X, pad.position.Y, fp.position.angle or 0.0)
    pad_angle = pad.position.angle or 0.0
    offset = drill.offset or Position(X=0.0, Y=0.0)
    ox, oy = _rotate_kicad(offset.X, offset.Y, pad_angle)
    x, y = fp.position.X + dx + ox, fp.position.Y + dy + oy
    sx, sy = _rotate_kicad(max(0.0, width - height) / 2,
                           max(0.0, height - width) / 2, pad_angle)

    # The slot is swept by a circular drill between the two endpoints.
    # Circumscribe each end disk with a 16-gon, then form their convex hull.
    # The extra micron covers the <1 µm displacement of quantized vertices.
    sides = 16
    radius = min(width, height) / 2 + NPTH_COPPER_CLEARANCE_MM
    vertex_radius = (radius + .001) / math.cos(math.pi / sides)
    vertices = []
    for cx, cy in ((x - sx, y - sy), (x + sx, y + sy)):
        for index in range(sides):
            angle = (index + .5) * math.tau / sides
            vx = cx + vertex_radius * math.cos(angle)
            vy = cy + vertex_radius * math.sin(angle)
            vertices.append((round(vx * 1000), round(-vy * 1000)))
    hull = MultiPoint(vertices).convex_hull
    coordinates = " ".join(f"{int(vx)} {int(vy)}" for vx, vy in hull.exterior.coords)
    return [f'    (keepout "" (polygon {layer} 0 {coordinates}))' for layer in COPPER_LAYERS]


def _circumscribed_pad_polygon_um(
    shape_kind: str,
    sx_mm: float,
    sy_mm: float,
    pad_angle_deg: float,
    roundrect_ratio: float = 0.25,
    n_quarter: int = 8,
) -> list[tuple[int, int]]:
    """Return a CCW integer-micron convex polygon strictly containing a pad in Specctra (Y-up) frame.

    The +1.0 um outward inflation guarantees 0.000000 mm^2 uncovered copper after
    integer-micron rounding (max 0.707 um quantization error at any rotation angle).
    """
    hx = max(float(sx_mm) * 500.0, 50.0)
    hy = max(float(sy_mm) * 500.0, 50.0)
    if shape_kind == "oval":
        r = min(hx, hy)
    elif shape_kind == "roundrect":
        ratio = max(0.0, min(0.5, float(roundrect_ratio)))
        r = 2.0 * min(hx, hy) * ratio
    else:
        r = 0.0

    eps = 1.0
    local_pts: list[tuple[float, float]] = []
    if r <= 1e-6:
        local_pts = [
            (hx + eps, -(hy + eps)),
            (hx + eps, hy + eps),
            (-(hx + eps), hy + eps),
            (-(hx + eps), -(hy + eps)),
        ]
    else:
        ax = max(0.0, hx - r)
        ay = max(0.0, hy - r)
        r_eff = r + eps
        d = (math.pi / 2.0) / n_quarter
        r_vert = r_eff / math.cos(d / 2.0)
        quarters = (
            (ax, ay, 0.0),
            (-ax, ay, math.pi / 2.0),
            (-ax, -ay, math.pi),
            (ax, -ay, 1.5 * math.pi),
        )
        for cx, cy, base_ang in quarters:
            for k in range(n_quarter):
                ang = base_ang + (k + 0.5) * d
                local_pts.append((cx + r_vert * math.cos(ang), cy + r_vert * math.sin(ang)))

    rad = math.radians(float(pad_angle_deg))
    c = math.cos(rad)
    s = math.sin(rad)
    out: list[tuple[int, int]] = []
    for lx, ly in local_pts:
        wx = round(lx * c - ly * s)
        wy = round(lx * s + ly * c)
        if not out or out[-1] != (wx, wy):
            out.append((wx, wy))
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    area2 = sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(out, out[1:] + out[:1])
    )
    if area2 < 0:
        out.reverse()
    return out


def board_to_dsn(
    board: Board,
    dsn_path: Path,
    netclasses: dict[str, NetClassRule],
    net_to_class: dict[str, str],
    board_bounds_mm: tuple[float, float, float, float] = (0.0, 0.0, 60.0, 40.0),
    keepouts_mm: list[dict[str, Any]] | None = None,
    via_pad_mm: float = 0.55,
    via_drill_mm: float = 0.30,
    skip_nets: set[str] | None = None,
    override_net_pins: dict[str, list[str]] | None = None,
    net_rules: dict[str, NetClassRule] | None = None,
    board_outline_mm: list[tuple[float, float]] | None = None,
    routing_layers_by_class: dict[str, tuple[str, ...]] | None = None,
    *,
    protect_mechanical_holes: bool = False,
    safe_pin_references: bool = False,
    honor_rule_via_geometry: bool = False,
    align_structure_clearance: bool = False,
    physical_ground_plane: dict[str, Any] | None = None,
    exact_pad_shapes: bool = False,
    snap_angle: str | None = None,
) -> Path:
    """Export a kiutils Board to a Specctra .dsn file.

    The secondary router can opt into four-layer NPTH obstacles and explicit
    images for footprints without electrical pins, and independent quoting of
    component and pad names in pin references. Callers can also opt into
    honoring the DEFAULT netclass via geometry, aligning the structure-level
    clearance with the DEFAULT netclass clearance, exporting a physical
    In1.Cu ground plane with fixed copper and multi-layer thermal EPAD padstacks,
    and emitting strictly-circumscribed polygon padstacks for curved/rotated pads
    with an explicit Specctra snap_angle.
    """
    x0, y0, x1, y1 = board_bounds_mm
    # fastroute assigns explicit classes after reading per-net rules. Compile
    # overrides into exclusive singleton classes to preserve rule precedence.
    netclasses=dict(netclasses); net_to_class=dict(net_to_class)
    if net_rules:
        from dataclasses import replace
        for i,(net,rule) in enumerate(sorted(net_rules.items())):
            name=f'NET_RULE_{i:04d}'
            netclasses[name]=replace(rule,name=name)
            net_to_class[net]=name
    # Specctra uses Y-up: KiCad y in [0, 40] -> Specctra y in [-40, 0]
    bx0 = round(x0 * 1000)
    by0 = round(-y1 * 1000)
    bx1 = round(x1 * 1000)
    by1 = round(-y0 * 1000)

    default_rule = netclasses.get("DEFAULT")
    if (
        honor_rule_via_geometry
        and default_rule is not None
        and math.isfinite(default_rule.via_pad_mm)
        and math.isfinite(default_rule.via_drill_mm)
        and default_rule.via_pad_mm > 0
        and default_rule.via_drill_mm > 0
        and default_rule.via_pad_mm > default_rule.via_drill_mm
    ):
        via_pad_mm = default_rule.via_pad_mm
        via_drill_mm = default_rule.via_drill_mm

    via_pad_um = round(via_pad_mm * 1000)
    via_drill_um = round(via_drill_mm * 1000)
    via_name = f"Via[0-3]_{via_pad_um}:{via_drill_um}_um"

    struct_clr_um = 150
    if align_structure_clearance and default_rule is not None:
        struct_clr_um = max(125, round(default_rule.clearance_mm * 1000))

    plane_net_name: str | None = None
    thermal_epad_vias: dict[tuple[str, str], int] = {}
    if physical_ground_plane is not None:
        plane_net_name = str(physical_ground_plane["net_name"])
        thermal_epad_vias = {
            (str(k[0]), str(k[1])): int(v)
            for k, v in (physical_ground_plane.get("thermal_epad_vias") or {}).items()
        }

    padstacks: dict[str, list[str]] = {}
    images: list[str] = []
    placements: list[str] = []
    net_pins: dict[str, list[str]] = {}
    mechanical_keepout_lines: list[str] = []
    override_references: dict[str, set[str]] = {}

    # Register via padstack across all 4 copper layers
    padstacks[via_name] = [
        f'    (padstack {_quote(via_name)}',
        f"      (shape (circle F.Cu {via_pad_um}))",
        f"      (shape (circle In1.Cu {via_pad_um}))",
        f"      (shape (circle In2.Cu {via_pad_um}))",
        f"      (shape (circle B.Cu {via_pad_um}))",
        "      (attach off)",
        "    )",
    ]

    for idx, fp in enumerate(board.footprints):
        ref = _footprint_ref(fp, idx)
        fx = float(fp.position.X)
        fy = float(fp.position.Y)
        fangle = float(fp.position.angle or 0.0)
        img_name = f"IMG_{ref}"

        # Place footprint at world position with angle 0 and pre-rotated local pins
        # to eliminate any KiCad vs Specctra rotation-sign ambiguity.
        fx_um = round(fx * 1000)
        fy_um = round(-fy * 1000)
        placements.append(f"    (component {_quote(img_name)}")
        placements.append(f"      (place {_quote(ref)} {fx_um} {fy_um} front 0)")
        placements.append("    )")

        pin_lines: list[str] = []
        for pad_idx, pad in enumerate(fp.pads):
            pad_num = str(pad.number or f"P{pad_idx+1}").strip()
            if pad.type == "np_thru_hole":
                if protect_mechanical_holes:
                    mechanical_keepout_lines.extend(_npth_keepout_lines(fp, pad))
                continue

            lx_kicad, ly_kicad = _rotate_kicad(
                float(pad.position.X), float(pad.position.Y), fangle
            )
            px_um = round(lx_kicad * 1000)
            py_um = round(-ly_kicad * 1000)

            pad_angle = float(pad.position.angle or 0.0)
            pw, ph = _pad_world_dims(float(pad.size.X), float(pad.size.Y), pad_angle)
            pw_um = max(100, round(pw * 1000))
            ph_um = max(100, round(ph * 1000))

            if pad.type == "thru_hole":
                layer_list = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
                layer_tag = "TH"
            else:
                raw_layers = pad.layers or ["F.Cu"]
                if "*.Cu" in raw_layers:
                    layer_list = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
                else:
                    layer_list = [l for l in raw_layers if l.endswith(".Cu")]
                if not layer_list:
                    continue
                layer_tag = "B" if layer_list == ["B.Cu"] else "T"

            shape_kind = pad.shape
            ep_um = thermal_epad_vias.get((ref, pad_num)) if pad.type != "thru_hole" else None
            use_exact_poly = exact_pad_shapes and (
                shape_kind in ("oval", "roundrect")
                or (shape_kind != "circle" and abs(pad_angle % 90.0) > 1e-6)
            )
            rr_ratio = float(getattr(pad, "roundrectRatio", None) or 0.25)
            if ep_um is not None:
                smd_layers = set(layer_list)
                if shape_kind == "circle":
                    ps_name = f"Circ_EPAD_{layer_tag}_{pw_um}_V{ep_um}_um"
                    if ps_name not in padstacks:
                        lines = [f"    (padstack {_quote(ps_name)}"]
                        for lyr in COPPER_LAYERS:
                            dia = pw_um if lyr in smd_layers else ep_um
                            lines.append(f"      (shape (circle {lyr} {dia}))")
                        lines.append("      (attach off)")
                        lines.append("    )")
                        padstacks[ps_name] = lines
                elif use_exact_poly:
                    sx_um = round(float(pad.size.X) * 1000)
                    sy_um = round(float(pad.size.Y) * 1000)
                    ang_key = round(pad_angle * 10)
                    rr_key = round(rr_ratio * 1000) if shape_kind == "roundrect" else 0
                    ps_name = f"Poly_EPAD_{layer_tag}_{shape_kind}_{sx_um}x{sy_um}_a{ang_key}_r{rr_key}_V{ep_um}_um"
                    if ps_name not in padstacks:
                        poly_pts = _circumscribed_pad_polygon_um(
                            shape_kind, float(pad.size.X), float(pad.size.Y), pad_angle, roundrect_ratio=rr_ratio
                        )
                        coords = " ".join(f"{x} {y}" for x, y in poly_pts)
                        lines = [f"    (padstack {_quote(ps_name)}"]
                        for lyr in COPPER_LAYERS:
                            if lyr in smd_layers:
                                lines.append(f"      (shape (polygon {lyr} 0 {coords}))")
                            else:
                                lines.append(f"      (shape (circle {lyr} {ep_um}))")
                        lines.append("      (attach off)")
                        lines.append("    )")
                        padstacks[ps_name] = lines
                else:
                    ps_name = f"Rect_EPAD_{layer_tag}_{pw_um}x{ph_um}_V{ep_um}_um"
                    if ps_name not in padstacks:
                        hx = pw_um // 2
                        hy = ph_um // 2
                        lines = [f"    (padstack {_quote(ps_name)}"]
                        for lyr in COPPER_LAYERS:
                            if lyr in smd_layers:
                                lines.append(f"      (shape (rect {lyr} {-hx} {-hy} {hx} {hy}))")
                            else:
                                lines.append(f"      (shape (circle {lyr} {ep_um}))")
                        lines.append("      (attach off)")
                        lines.append("    )")
                        padstacks[ps_name] = lines
            elif shape_kind == "circle":
                ps_name = f"Circ_{layer_tag}_{pw_um}_um"
                if ps_name not in padstacks:
                    lines = [f"    (padstack {_quote(ps_name)}"]
                    for lyr in layer_list:
                        lines.append(f"      (shape (circle {lyr} {pw_um}))")
                    lines.append("      (attach off)")
                    lines.append("    )")
                    padstacks[ps_name] = lines
            elif use_exact_poly:
                sx_um = round(float(pad.size.X) * 1000)
                sy_um = round(float(pad.size.Y) * 1000)
                ang_key = round(pad_angle * 10)
                rr_key = round(rr_ratio * 1000) if shape_kind == "roundrect" else 0
                ps_name = f"Poly_{layer_tag}_{shape_kind}_{sx_um}x{sy_um}_a{ang_key}_r{rr_key}_um"
                if ps_name not in padstacks:
                    poly_pts = _circumscribed_pad_polygon_um(
                        shape_kind, float(pad.size.X), float(pad.size.Y), pad_angle, roundrect_ratio=rr_ratio
                    )
                    coords = " ".join(f"{x} {y}" for x, y in poly_pts)
                    lines = [f"    (padstack {_quote(ps_name)}"]
                    for lyr in layer_list:
                        lines.append(f"      (shape (polygon {lyr} 0 {coords}))")
                    lines.append("      (attach off)")
                    lines.append("    )")
                    padstacks[ps_name] = lines
            else:
                ps_name = f"Rect_{layer_tag}_{pw_um}x{ph_um}_um"
                if ps_name not in padstacks:
                    hx = pw_um // 2
                    hy = ph_um // 2
                    lines = [f"    (padstack {_quote(ps_name)}"]
                    for lyr in layer_list:
                        lines.append(f"      (shape (rect {lyr} {-hx} {-hy} {hx} {hy}))")
                    lines.append("      (attach off)")
                    lines.append("    )")
                    padstacks[ps_name] = lines

            pin_lines.append(f"      (pin {_quote(ps_name)} {_quote(pad_num)} {px_um} {py_um})")

            logical_reference = f"{ref}-{pad_num}"
            pin_reference = logical_reference
            if safe_pin_references:
                pin_reference = _secondary_pin_reference(ref, pad_num)
                if override_net_pins:
                    override_references.setdefault(logical_reference, set()).add(pin_reference)
            if pad.net and pad.net.name:
                if skip_nets and pad.net.name in skip_nets and pad.net.name != plane_net_name:
                    continue
                net_pins.setdefault(pad.net.name, []).append(pin_reference)

        if pin_lines or protect_mechanical_holes:
            images.append(f"    (image {_quote(img_name)}")
            images.extend(pin_lines)
            images.append("    )")

    if override_net_pins:
        for nname, plist in override_net_pins.items():
            if plane_net_name is not None and nname == plane_net_name:
                continue
            if len(plist) < 2:
                net_pins.pop(nname, None)
            elif safe_pin_references:
                resolved = []
                for pin in plist:
                    matches = override_references.get(pin, set())
                    if len(matches) != 1:
                        raise ValueError(f"Unresolved or ambiguous pin override for network {nname!r}: {pin!r}")
                    resolved.append(next(iter(matches)))
                net_pins[nname] = resolved
            else:
                net_pins[nname] = list(plist)

    # Keepout rectangles (e.g. RF corner keepout, QFN thermal via B.Cu keepout)
    keepout_lines: list[str] = []
    for ko in keepouts_mm or []:
        kx0 = round(ko["x_min"] * 1000)
        ky0 = round(-ko["y_max"] * 1000)
        kx1 = round(ko["x_max"] * 1000)
        ky1 = round(-ko["y_min"] * 1000)
        for lyr in ko.get("layers", ["F.Cu", "B.Cu"]):
            kind=ko.get('dsn_kind','keepout')
            if kind not in ('keepout','wire_keepout','via_keepout'): raise ValueError('unsupported DSN keepout kind')
            if ko.get('polygon_mm'):
                coords=' '.join(f'{round(x*1000)} {round(-y*1000)}' for x,y in ko['polygon_mm'])
                keepout_lines.append(f'    ({kind} "" (polygon {lyr} 0 {coords}))')
            else: keepout_lines.append(f'    ({kind} "" (rect {lyr} {kx0} {ky0} {kx1} {ky1}))')

    plane_lines: list[str] = []
    wiring_lines: list[str] = []
    if physical_ground_plane is not None:
        assert plane_net_name is not None

        def _ring_coords_um(ring: list[tuple[int, int]]) -> str:
            pts = [(int(x), int(y)) for x, y in ring]
            if len(pts) >= 3 and pts[0] != pts[-1]:
                pts.append(pts[0])
            return " ".join(f"{x} {y}" for x, y in pts)

        ext_coords = _ring_coords_um(physical_ground_plane["plane_exterior_um"])
        plane_lines.append(f"    (plane {_quote(plane_net_name)}")
        plane_lines.append(f"      (polygon In1.Cu 0 {ext_coords})")
        for win in physical_ground_plane.get("plane_windows_um") or []:
            win_coords = _ring_coords_um(win)
            plane_lines.append(f"      (window (polygon In1.Cu 0 {win_coords}))")
        plane_lines.append("    )")

        for fw in physical_ground_plane.get("fixed_wires") or []:
            layer = str(fw["layer"])
            width_um = int(fw["width_um"])
            x1_um = int(fw["x1_um"])
            y1_um = int(fw["y1_um"])
            x2_um = int(fw["x2_um"])
            y2_um = int(fw["y2_um"])
            net = str(fw["net"])
            wiring_lines.append(
                f"    (wire (path {layer} {width_um} {x1_um} {y1_um} {x2_um} {y2_um}) (net {_quote(net)}) (type fix))"
            )

        for fv in physical_ground_plane.get("fixed_vias") or []:
            fv_pad_um = int(fv["pad_um"])
            fv_drill_um = int(fv["drill_um"])
            fv_ps_name = f"Via[0-3]_{fv_pad_um}:{fv_drill_um}_um"
            if fv_ps_name not in padstacks:
                padstacks[fv_ps_name] = [
                    f"    (padstack {_quote(fv_ps_name)}",
                    f"      (shape (circle F.Cu {fv_pad_um}))",
                    f"      (shape (circle In1.Cu {fv_pad_um}))",
                    f"      (shape (circle In2.Cu {fv_pad_um}))",
                    f"      (shape (circle B.Cu {fv_pad_um}))",
                    "      (attach off)",
                    "    )",
                ]
            x_um = int(fv["x_um"])
            y_um = int(fv["y_um"])
            net = str(fv["net"])
            wiring_lines.append(
                f"    (via {_quote(fv_ps_name)} {x_um} {y_um} (net {_quote(net)}) (type fix))"
            )

    # Group nets by netclass
    class_to_nets: dict[str, list[str]] = {cname: [] for cname in netclasses}
    for net_name, pins in net_pins.items():
        if len(pins) < 2 and net_name != plane_net_name:
            continue
        cname = net_to_class.get(net_name, "DEFAULT")
        if cname not in class_to_nets:
            cname = "DEFAULT"
        class_to_nets[cname].append(net_name)

    network_lines: list[str] = []
    for net_name, pins in sorted(net_pins.items()):
        if len(pins) < 2 and net_name != plane_net_name:
            continue
        pin_str = " ".join(pins if safe_pin_references else (_quote(p) for p in pins))
        network_lines.append(f"    (net {_quote(net_name)}")
        network_lines.append(f"      (pins {pin_str})")
        network_lines.append("    )")

    for cname, rule in netclasses.items():
        nets_in_cls = sorted(class_to_nets.get(cname, []))
        if not nets_in_cls:
            continue
        net_tokens = " ".join(_quote(n) for n in nets_in_cls)
        w_um = round(rule.trace_width_mm * 1000)
        clr_um = round(rule.clearance_mm * 1000)
        network_lines.append(f"    (class {_quote(cname)} {net_tokens}")
        if rule.zero_via_allowed:
            layers=' '.join((routing_layers_by_class or {}).get(cname,('F.Cu','B.Cu')))
            network_lines.append(
                f"      (circuit (use_via {_quote(via_name)}) (use_layer {layers}))"
            )
        else:
            network_lines.append("      (circuit (use_layer F.Cu))")
        network_lines.append(f"      (rule (width {w_um}) (clearance {clr_um}))")
        network_lines.append("    )")

    snap_angle_lines: list[str] = []
    if snap_angle is not None:
        if snap_angle not in ("fortyfive_degree", "ninety_degree", "none"):
            raise ValueError(f"Unsupported Specctra snap_angle: {snap_angle!r}")
        snap_angle_lines.append(f"    (snap_angle {snap_angle})")

    dsn_lines: list[str] = [
        f'(pcb {_quote(dsn_path.name)}',
        "  (parser",
        '    (string_quote ")',
        "    (space_in_quoted_tokens on)",
        '    (host_cad "KiCad\'s Pcbnew")',
        '    (host_version "8.0.0")',
        "  )",
        "  (resolution um 10)",
        "  (unit um)",
        "  (structure",
        "    (layer F.Cu",
        "      (type signal)",
        "      (property (index 0))",
        "    )",
        "    (layer In1.Cu",
        "      (type power)",
        "      (property (index 1))",
        "    )",
        "    (layer In2.Cu",
        "      (type signal)" if routing_layers_by_class and any('In2.Cu' in layers for layers in routing_layers_by_class.values()) else "      (type power)",
        "      (property (index 2))",
        "    )",
        "    (layer B.Cu",
        "      (type signal)",
        "      (property (index 3))",
        "    )",
        "    (boundary",
        ('      (path pcb 0 '+' '.join(f'{round(x*1000)} {round(-y*1000)}' for x,y in board_outline_mm)+')') if board_outline_mm else f"      (rect pcb {bx0} {by0} {bx1} {by1})",
        "    )",
        *keepout_lines,
        *mechanical_keepout_lines,
        f"    (via {_quote(via_name)})",
        *snap_angle_lines,
        *plane_lines,
        "    (rule",
        "      (width 200)",
        f"      (clearance {struct_clr_um})",
        f"      (clearance {struct_clr_um} (type default_smd))",
        "      (clearance 50 (type smd_smd))",
        "    )",
        "  )",
        "  (placement",
        *placements,
        "  )",
        "  (library",
        *images,
    ]
    for ps_lines in padstacks.values():
        dsn_lines.extend(ps_lines)
    dsn_lines.extend(
        [
            "  )",
            "  (network",
            *network_lines,
            "  )",
            "  (wiring",
            *wiring_lines,
            "  )",
            ")",
        ]
    )

    dsn_path.parent.mkdir(parents=True, exist_ok=True)
    dsn_path.write_text("\n".join(dsn_lines) + "\n", encoding="utf-8")
    return dsn_path


def _parse_sexpr(text: str) -> list[Any]:
    """Parse Specctra S-expression text into nested Python lists of strings."""
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch in ("(", ")"):
            tokens.append(ch)
            i += 1
        elif ch == '"':
            j = i + 1
            buf: list[str] = []
            while j < n:
                c = text[j]
                if c == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                elif c == '"':
                    j += 1
                    break
                else:
                    buf.append(c)
                    j += 1
            tokens.append("".join(buf))
            i = j
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in ("(", ")"):
                j += 1
            tokens.append(text[i:j])
            i = j

    stack: list[list[Any]] = [[]]
    for tok in tokens:
        if tok == "(":
            node: list[Any] = []
            stack[-1].append(node)
            stack.append(node)
        elif tok == ")":
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append(tok)
    return stack[0]


def _find_child(node: list[Any], key: str) -> list[Any] | None:
    for item in node:
        if isinstance(item, list) and item and item[0] == key:
            return item
    return None


def _parse_padstack_dims(name: str, lib_node: list[Any] | None) -> tuple[float, float]:
    """Extract (via_pad_mm, via_drill_mm) from padstack name and library_out."""
    drill_mm = 0.30
    m = re.search(r"(\d+):(\d+)_um", name)
    pad_mm = int(m.group(1)) / 1000.0 if m else 0.55
    if m:
        drill_mm = int(m.group(2)) / 1000.0
    return pad_mm, drill_mm


def parse_ses(ses_path: Path) -> dict[str, Any]:
    """Parse a Specctra .ses file into normalized KiCad-mm segments, vias, and metrics."""
    raw = ses_path.read_text(encoding="utf-8")
    tree = _parse_sexpr(raw)
    if not tree:
        raise ValueError(f"Empty Specctra session file: {ses_path}")
    session = tree[0]
    routes = _find_child(session, "routes")
    if not routes:
        raise ValueError(f"Missing (routes ...) block in {ses_path}")

    res_node = _find_child(routes, "resolution")
    mm_per_unit = 0.0001
    if res_node and len(res_node) >= 3:
        unit = str(res_node[1]).lower()
        mult = float(res_node[2])
        if unit == "um":
            mm_per_unit = 1.0 / (1000.0 * mult)
        elif unit == "mm":
            mm_per_unit = 1.0 / mult
        elif unit == "mil":
            mm_per_unit = 0.0254 / mult
        elif unit == "inch":
            mm_per_unit = 25.4 / mult

    lib_out = _find_child(routes, "library_out")
    net_out = _find_child(routes, "network_out")
    if not net_out:
        raise ValueError(f"Missing (network_out ...) block in {ses_path}")

    segments: list[dict[str, Any]] = []
    vias: list[dict[str, Any]] = []
    routed_net_names: set[str] = set()
    total_wirelength_mm = 0.0
    usb_vias = 0
    layers_used: set[str] = set()

    for item in net_out[1:]:
        if not isinstance(item, list) or not item or item[0] != "net":
            continue
        net_name = str(item[1])
        has_geometry = False
        for sub in item[2:]:
            if not isinstance(sub, list) or not sub:
                continue
            if sub[0] == "wire":
                path_node = _find_child(sub, "path")
                if not path_node or len(path_node) < 7:
                    continue
                layer = str(path_node[1])
                width_mm = round(float(path_node[2]) * mm_per_unit, 4)
                coords = [float(v) for v in path_node[3:]]
                pts = [
                    (round(coords[k] * mm_per_unit, 4), round(-coords[k + 1] * mm_per_unit, 4))
                    for k in range(0, len(coords) - 1, 2)
                ]
                for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                    seg_len = math.hypot(x2 - x1, y2 - y1)
                    if seg_len < 1e-6:
                        continue
                    total_wirelength_mm += seg_len
                    segments.append(
                        {
                            "net": net_name,
                            "layer": layer,
                            "width_mm": width_mm,
                            "x1": x1,
                            "y1": y1,
                            "x2": x2,
                            "y2": y2,
                        }
                    )
                    layers_used.add(layer)
                    has_geometry = True
            elif sub[0] == "via" and len(sub) >= 4:
                ps_name = str(sub[1])
                vx = round(float(sub[2]) * mm_per_unit, 4)
                vy = round(-float(sub[3]) * mm_per_unit, 4)
                pad_mm, drill_mm = _parse_padstack_dims(ps_name, lib_out)
                vias.append(
                    {
                        "net": net_name,
                        "x": vx,
                        "y": vy,
                        "pad_mm": pad_mm,
                        "drill_mm": drill_mm,
                    }
                )
                if net_name in ("USB_DP", "USB_DM"):
                    usb_vias += 1
                has_geometry = True
        if has_geometry:
            routed_net_names.add(net_name)

    return {
        "segments": segments,
        "vias": vias,
        "routed_nets": len(routed_net_names),
        "routed_net_names": sorted(routed_net_names),
        "via_count": len(vias),
        "usb_path_vias": usb_vias,
        "total_wirelength_mm": round(total_wirelength_mm, 3),
        "layers_used": sorted(layers_used),
    }


def merge_ses_into_board(board: Board, ses_path: Path) -> tuple[Board, dict[str, Any]]:
    """Merge parsed Specctra .ses segments & vias into a kiutils Board."""
    parsed = parse_ses(ses_path)
    net_map = {n.name: n.number for n in board.nets}

    for seg in parsed["segments"]:
        net_num = net_map.get(seg["net"], 0)
        board.traceItems.append(
            Segment(
                start=Position(X=seg["x1"], Y=seg["y1"]),
                end=Position(X=seg["x2"], Y=seg["y2"]),
                width=seg["width_mm"],
                layer=seg["layer"],
                net=net_num,
                tstamp=str(uuid.uuid4()),
            )
        )

    for v in parsed["vias"]:
        net_num = net_map.get(v["net"], 0)
        board.traceItems.append(
            Via(
                position=Position(X=v["x"], Y=v["y"]),
                size=v["pad_mm"],
                drill=v["drill_mm"],
                layers=["F.Cu", "B.Cu"],
                net=net_num,
                tstamp=str(uuid.uuid4()),
            )
        )

    return board, parsed
