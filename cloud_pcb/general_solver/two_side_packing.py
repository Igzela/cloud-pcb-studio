"""Typed geometric checks and bottom-side recovery for failed placement."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from kiutils.footprint import DrillDefinition, Footprint, Pad
from kiutils.items.common import Position
from shapely.affinity import rotate, scale, translate
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from cloud_pcb.general_solver.geometry import drill_geometry
from cloud_pcb.general_solver.placement_recovery import PackingExhausted, pack_movable
from cloud_pcb.general_solver.semantics import component_envelope, infer_rf, local_pad_polygon


COPPER_EDGE_MM = 0.50
COMPACT_COURTYARD_MM = 0.28
DRILL_SIDE_GUARD_MM = 0.30
MICRON_MM = 0.001
PCB_THICKNESS_MM = 1.60


class PlacementGeometryUnsupported(ValueError):
    """The typed geometry cannot be used safely for this recovery attempt."""


def mounting_guard_radius(hole: dict) -> float:
    drill = float(hole["drill_mm"])
    radius = drill / 2 + 1.
    requested = float(hole.get("fastener_radius_mm", radius))
    if not all(math.isfinite(v) and v > 0 for v in (drill, requested)):
        raise PlacementGeometryUnsupported("Mounting guard radii must be finite and positive")
    return max(radius, requested)


def mechanical_keepouts(spec: dict) -> tuple:
    regions = []
    for region in spec.get("mechanical_keepouts", []):
        polygon = Polygon(region["polygon_mm"])
        z_min, z_max = (float(z) for z in region.get("z_range_mm", [-100, 100]))
        if (polygon.is_empty or not polygon.is_valid or not all(
                math.isfinite(v) for v in (*polygon.bounds, z_min, z_max)) or z_min > z_max):
            raise PlacementGeometryUnsupported("Mechanical keepout has invalid geometry")
        regions.append((polygon, z_min, z_max))
    return tuple(regions)


def component_z_range(part: dict, side: str) -> tuple[float, float]:
    standoff = float(part.get("standoff_mm", 0))
    height = component_envelope(part)["height_mm"]
    if not (math.isfinite(standoff) and math.isfinite(height) and standoff >= 0 and height > 0):
        raise PlacementGeometryUnsupported("Height and standoff must describe a positive body")
    if side == "bottom":
        return -PCB_THICKNESS_MM - standoff - height, -PCB_THICKNESS_MM - standoff
    return standoff, standoff + height


def hits_mechanical_keepout(body, z_range, regions) -> bool:
    z_min, z_max = z_range
    return any(min(z_max, upper) > max(z_min, lower) and body.intersects(polygon)
               for polygon, lower, upper in regions)


def _copper_layers(pad: dict) -> set[str]:
    kind = pad.get("type", "smd")
    if kind == "np_thru_hole":
        return set()
    if kind == "thru_hole":
        return {"*.Cu"}
    return {layer for layer in (pad.get("layers") or ["F.Cu"]) if layer.endswith(".Cu")}


def side_options(part: dict, *, protected_nets: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """Respect explicit sides and allow automatic bottom only for simple SMDs."""
    if "side" in part:
        side = part["side"]
        if side not in ("top", "bottom"):
            raise PlacementGeometryUnsupported("Unknown explicit component side")
        return (side,)
    pads = part.get("pads", [])
    numbers = {str(pad["num"]).strip() if pad.get("num") is not None else "" for pad in pads}
    if (len(pads) == 2 and len(numbers) == 2 and all(numbers)
            and not infer_rf(part)
            and all(pad.get("type") == "smd" and not pad.get("drill_mm")
                    and _copper_layers(pad) == {"F.Cu"}
                    and pad.get("net") not in protected_nets for pad in pads)):
        return "top", "bottom"
    return ("top",)


@dataclass(frozen=True)
class Occupancy:
    body: BaseGeometry
    copper: BaseGeometry
    holes: BaseGeometry
    hole_guard: BaseGeometry
    combined: BaseGeometry

    def _map(self, transform):
        return Occupancy(*(transform(value) for value in (
            self.body, self.copper, self.holes, self.hole_guard, self.combined,
        )))

    def rotate(self, angle: float):
        return self._map(lambda geom: rotate(geom, -angle, origin=(0, 0)))

    def translate(self, x: float, y: float):
        return self._map(lambda geom: translate(geom, xoff=x, yoff=y))

    def search_bounds(self) -> tuple[float, float, float, float]:
        # Expand only the copper part by its edge clearance. The generator's
        # bbox bounds are optional scaffolding for the true PCB outline check.
        return unary_union((self.body, self.copper.buffer(COPPER_EDGE_MM), self.holes)).bounds


def _local_hole(pad: dict) -> BaseGeometry:
    local = Pad(position=Position(X=pad["x_rel_mm"], Y=pad["y_rel_mm"],
                                  angle=pad.get("angle_rel_deg", 0)))
    local.drill = DrillDefinition(diameter=float(pad.get("drill_mm") or .9))
    if pad.get("drill_shape") == "oval":
        local.drill.oval = True
        local.drill.diameter = float(pad["drill_width_mm"])
        local.drill.width = float(pad["drill_height_mm"])
    if "drill_offset_mm" in pad:
        x, y = pad["drill_offset_mm"]
        local.drill.offset = Position(X=x, Y=y)
    origin = Footprint(position=Position(X=0, Y=0, angle=0))
    shape = drill_geometry(origin, local)
    if shape.is_empty:
        raise PlacementGeometryUnsupported("Missing physical drill or slot geometry")
    # drill_geometry uses 32 segments/quadrant. Keep the rounded slot inside
    # our occupancy witness even after aperture and location quantization.
    radius = max(local.drill.diameter, local.drill.width or 0) / 2
    return shape.buffer(radius * (1 - math.cos(math.pi / 128)) + MICRON_MM)


def local_occupancy(part: dict, side: str) -> Occupancy:
    """Build local body and CAD pad geometry before the KiCad pose."""
    nominal = dict(part, side="top")
    body = box(*component_envelope(nominal)["bbox_mm"])
    copper, holes, guards = [], [], []
    for pad in part.get("pads", []):
        kind = pad.get("type", "smd")
        if kind in ("thru_hole", "np_thru_hole"):
            hole = _local_hole(pad)
            holes.append(hole)
            guards.append(hole)
        layers = _copper_layers(pad)
        if not layers:
            continue
        shape = pad.get("shape", "rect")
        if shape not in ("rect", "circle", "oval", "roundrect"):
            raise PlacementGeometryUnsupported(f"Pad shape {shape!r} has no conservative placement projection")
        w, h = (float(pad[key]) for key in ("w_mm", "h_mm"))
        if not all(math.isfinite(value) and value > 0 for value in (w, h)):
            raise PlacementGeometryUnsupported("Copper dimensions must be finite and positive")
        # Match emitter treatment of omitted/zero roundrect ratio. The buffer
        # compensates chord sag for its default 16 segments/quadrant and the
        # µm export grid, so the geometry cannot overstate copper clearance.
        model = dict(pad, roundrect_ratio=pad.get("roundrect_ratio") or .25)
        sag = max(w, h) / 2 * (1 - math.cos(math.pi / 64)) if shape != "rect" else 0
        copper_shape = local_pad_polygon(model).buffer(sag + MICRON_MM)
        copper.append(copper_shape)
        if kind == "thru_hole" or layers != {"F.Cu" if side == "top" else "B.Cu"}:
            # Copper declared on another layer is conservatively reserved on both faces.
            if not (side == "bottom" and layers == {"F.Cu"}):
                guards.append(copper_shape)
    through = unary_union(guards).buffer(DRILL_SIDE_GUARD_MM)
    copper_union, hole_union = unary_union(copper), unary_union(holes)
    result = Occupancy(body, copper_union, hole_union, through,
                       unary_union((body, copper_union, hole_union)))
    if side == "bottom":
        return result._map(lambda geom: scale(geom, xfact=-1, origin=(0, 0)))
    return result


def noninterfering(first_side: str, first: Occupancy, other_side: str, other: Occupancy) -> bool:
    if (first_side == other_side and
            first.combined.distance(other.combined) < 2 * COMPACT_COURTYARD_MM - 1e-9):
        return False
    return not (first.combined.intersects(other.hole_guard) or other.combined.intersects(first.hole_guard))


def in_outline(board: Polygon, boundary, shape: Occupancy) -> bool:
    """Check body footprint, plated copper and bare hole geometry separately."""
    return (board.covers(shape.body)
            and (shape.holes.is_empty or board.covers(shape.holes))
            and (shape.copper.is_empty or (board.covers(shape.copper)
                 and boundary.distance(shape.copper) >= COPPER_EDGE_MM - 1e-9)))


def _support_for_box(obstacle, projection, search, spacing):
    """Position the projected XY bounds, compensating extra search edge space."""
    return (obstacle[0] - spacing + search[2] - projection[2],
            obstacle[1] - spacing + search[3] - projection[3],
            obstacle[2] + spacing + search[0] - projection[0],
            obstacle[3] + spacing + search[1] - projection[1])


def pack_two_sided(parts: dict, reserved: dict, board: Polygon, fixed: list, target, commit, *,
                   board_size, mode: str, protected_nets: frozenset[str],
                   mechanical_regions=()) -> list[str]:
    """One attempt, with fixed and single-sided parts before flexible passives."""
    @lru_cache(maxsize=None)
    def local(ref, side, angle=0):
        return local_occupancy(parts[ref], side).rotate(angle)

    placed_shapes = {}
    for ref, (x, y, angle) in reserved.items():
        side = parts[ref].get("side", "top")
        placed_shapes[ref] = side, local(ref, side, angle).translate(x, y)
    boundary = board.boundary
    options = {ref: side_options(part, protected_nets=protected_nets)
               for ref, part in parts.items() if ref not in reserved}
    moved = []

    def order_key(ref):
        x0, y0, x1, y1 = local(ref, options[ref][0]).search_bounds()
        w, h = x1-x0, y1-y0
        first, second = (max(w, h), w*h) if mode == "long-side" else (w*h, max(w, h))
        return len(options[ref]), -first, -second, ref

    for ref in sorted(options, key=order_key):
        for side in options[ref]:
            check_shapes = list(placed_shapes.values())
            height_range = component_z_range(parts[ref], side)
            active_regions = [region for region in mechanical_regions
                              if min(height_range[1], region[2]) > max(height_range[0], region[1])]

            def valid(_, x, y, angle):
                shape = local(ref, side, angle).translate(x, y)
                return (in_outline(board, boundary, shape)
                        and all(not shape.combined.intersects(keepout) for keepout in fixed)
                        and all(not shape.body.intersects(region[0]) for region in active_regions)
                        and all(noninterfering(side, shape, existing_side, existing)
                                for existing_side, existing in check_shapes))

            def accepted(_, x, y, angle):
                placed_shapes[ref] = side, local(ref, side, angle).translate(x, y)
                if side == "bottom" and len(options[ref]) > 1:
                    moved.append(ref)
                commit(ref, (x, y, angle), side)

            def local_supports(_, angle):
                form = local(ref, side, angle)
                search = form.search_bounds()
                for existing_side, shape in check_shapes:
                    if existing_side == side:
                        yield _support_for_box(shape.combined.bounds, form.combined.bounds,
                                               search, 2 * COMPACT_COURTYARD_MM)
                    if not shape.hole_guard.is_empty:
                        yield _support_for_box(shape.hole_guard.bounds, form.combined.bounds, search, 0)
                    if not form.hole_guard.is_empty:
                        yield _support_for_box(shape.combined.bounds, form.hole_guard.bounds, search, 0)
                for keepout in fixed:
                    yield _support_for_box(keepout.bounds, form.combined.bounds, search, 0)
                for region in active_regions:
                    yield _support_for_box(region[0].bounds, form.body.bounds, search, 0)

            try:
                pack_movable([ref], {ref: local(ref, side).search_bounds()}, target, valid, accepted, board_size,
                             clearance=0, edge_inset=0, angle_support_bounds=local_supports)
            except PackingExhausted:
                if side == options[ref][-1]:
                    raise
            else:
                break
    return moved
