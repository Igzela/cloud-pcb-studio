"""Typed geometric checks and bottom-side recovery for failed placement."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from heapq import merge
from itertools import combinations, islice

from kiutils.footprint import DrillDefinition, Footprint, Pad
from kiutils.items.common import Position
from shapely.affinity import rotate, scale, translate
from shapely.geometry import Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from cloud_pcb.general_solver.geometry import drill_geometry
from cloud_pcb.general_solver.placement_recovery import (
    ANGLES, PackingExhausted, _angle_candidates, pack_movable,
)
from cloud_pcb.general_solver.semantics import component_elevation, component_envelope, infer_rf, local_pad_polygon


COPPER_EDGE_MM = 0.50
COMPACT_COURTYARD_MM = 0.28
SAME_SIDE_CLEARANCE_MM = 2 * COMPACT_COURTYARD_MM
DRILL_SIDE_GUARD_MM = 0.30
MICRON_MM = 0.001
PCB_THICKNESS_MM = 1.60
_AABB_EPS_MM = 1e-7
_SAME_SIDE_OUTER_MM = SAME_SIDE_CLEARANCE_MM + _AABB_EPS_MM
_SAME_SIDE_INNER_SQ = (SAME_SIDE_CLEARANCE_MM - _AABB_EPS_MM) ** 2

Bounds4 = tuple[float, float, float, float]
_UNSET = object()


class PlacementGeometryUnsupported(ValueError):
    """The typed geometry cannot be used safely for this recovery attempt."""


class _ProbeBudget(Exception):
    pass


class _BranchProbeBudget(Exception):
    pass


@dataclass(frozen=True)
class RollbackResult:
    status: str
    poses: dict | None
    probes: int
    branches: int
    moved: tuple[str, ...] = ()


Result = RollbackResult


def _geom_bounds(geom: BaseGeometry) -> Bounds4 | None:
    if geom.is_empty:
        return None
    b = geom.bounds
    if len(b) != 4:
        return None
    return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))


def _shift_bounds(bounds: Bounds4 | None, x: float, y: float) -> Bounds4 | None:
    if bounds is None:
        return None
    return (bounds[0] + x, bounds[1] + y, bounds[2] + x, bounds[3] + y)


def _aabb_separated(a: Bounds4 | None, b: Bounds4 | None, clearance: float = 0.0) -> bool:
    """Return True ONLY when AABBs are mathematically guaranteed separated by > clearance."""
    if a is None or b is None:
        return True
    margin = clearance + _AABB_EPS_MM
    return (
        a[2] + margin < b[0]
        or b[2] + margin < a[0]
        or a[3] + margin < b[1]
        or b[3] + margin < a[1]
    )


def _rect_dist_sq(a: Bounds4, b: Bounds4) -> float:
    """Exact squared Euclidean distance between two axis-aligned rectangles."""
    dx = b[0] - a[2] if b[0] > a[2] else (a[0] - b[2] if a[0] > b[2] else 0.0)
    dy = b[1] - a[3] if b[1] > a[3] else (a[1] - b[3] if a[1] > b[3] else 0.0)
    return dx * dx + dy * dy


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


def component_z_range(
    part: dict,
    side: str,
    board_thickness_mm: float = PCB_THICKNESS_MM,
) -> tuple[float, float]:
    try:
        elevation = component_elevation(
            side=side, board_thickness_mm=board_thickness_mm,
            height_mm=component_envelope(part)["height_mm"],
            standoff_mm=float(part.get("standoff_mm", 0)),
        )
    except (TypeError, ValueError) as exc:
        raise PlacementGeometryUnsupported(str(exc)) from exc
    return elevation["z_range_mm"]


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
    body_bounds: Bounds4 | None = field(default=_UNSET, compare=False)  # type: ignore[assignment]
    copper_bounds: Bounds4 | None = field(default=_UNSET, compare=False)  # type: ignore[assignment]
    holes_bounds: Bounds4 | None = field(default=_UNSET, compare=False)  # type: ignore[assignment]
    hole_guard_bounds: Bounds4 | None = field(default=_UNSET, compare=False)  # type: ignore[assignment]
    combined_bounds: Bounds4 | None = field(default=_UNSET, compare=False)  # type: ignore[assignment]
    body_is_rect: bool = field(default=_UNSET, repr=False, compare=False)  # type: ignore[assignment]
    _search_bounds_cache: Bounds4 | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.body_bounds is _UNSET:
            object.__setattr__(self, "body_bounds", _geom_bounds(self.body))
        if self.copper_bounds is _UNSET:
            object.__setattr__(self, "copper_bounds", _geom_bounds(self.copper))
        if self.holes_bounds is _UNSET:
            object.__setattr__(self, "holes_bounds", _geom_bounds(self.holes))
        if self.hole_guard_bounds is _UNSET:
            object.__setattr__(self, "hole_guard_bounds", _geom_bounds(self.hole_guard))
        if self.combined_bounds is _UNSET:
            object.__setattr__(self, "combined_bounds", _geom_bounds(self.combined))
        if self.body_is_rect is _UNSET:
            bb = self.body_bounds
            is_rect = (
                bb is not None
                and not self.body.is_empty
                and self.body.equals(box(*bb))
                and self.combined.covers(self.body)
            )
            object.__setattr__(self, "body_is_rect", is_rect)

    def _map(self, transform) -> Occupancy:
        return Occupancy(
            transform(self.body),
            self.copper if self.copper_bounds is None else transform(self.copper),
            self.holes if self.holes_bounds is None else transform(self.holes),
            self.hole_guard if self.hole_guard_bounds is None else transform(self.hole_guard),
            transform(self.combined),
        )

    def rotate(self, angle: float) -> Occupancy:
        if angle % 360 == 0:
            return self
        return self._map(lambda geom: rotate(geom, -angle, origin=(0, 0)))

    def translate(self, x: float, y: float) -> Occupancy:
        return Occupancy(
            translate(self.body, xoff=x, yoff=y),
            self.copper if self.copper_bounds is None else translate(self.copper, xoff=x, yoff=y),
            self.holes if self.holes_bounds is None else translate(self.holes, xoff=x, yoff=y),
            self.hole_guard if self.hole_guard_bounds is None else translate(self.hole_guard, xoff=x, yoff=y),
            translate(self.combined, xoff=x, yoff=y),
            body_bounds=_shift_bounds(self.body_bounds, x, y),
            copper_bounds=_shift_bounds(self.copper_bounds, x, y),
            holes_bounds=_shift_bounds(self.holes_bounds, x, y),
            hole_guard_bounds=_shift_bounds(self.hole_guard_bounds, x, y),
            combined_bounds=_shift_bounds(self.combined_bounds, x, y),
            body_is_rect=self.body_is_rect,
            _search_bounds_cache=_shift_bounds(self._search_bounds_cache, x, y),
        )

    def search_bounds(self) -> Bounds4:
        cached = self._search_bounds_cache
        if cached is not None:
            return cached
        # Expand only the copper part by its edge clearance. The generator's
        # bbox bounds are optional scaffolding for the true PCB outline check.
        b = unary_union((self.body, self.copper.buffer(COPPER_EDGE_MM), self.holes)).bounds
        result = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
        object.__setattr__(self, "_search_bounds_cache", result)
        return result


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
    if first_side == other_side:
        if not _aabb_separated(first.combined_bounds, other.combined_bounds, SAME_SIDE_CLEARANCE_MM):
            if (first.body_is_rect and other.body_is_rect
                    and first.body_bounds is not None and other.body_bounds is not None
                    and _rect_dist_sq(first.body_bounds, other.body_bounds) < _SAME_SIDE_INNER_SQ):
                return False
            if first.combined.distance(other.combined) < SAME_SIDE_CLEARANCE_MM - 1e-9:
                return False
    if (other.hole_guard_bounds is not None
            and not _aabb_separated(first.combined_bounds, other.hole_guard_bounds)
            and first.combined.intersects(other.hole_guard)):
        return False
    if (first.hole_guard_bounds is not None
            and not _aabb_separated(other.combined_bounds, first.hole_guard_bounds)
            and other.combined.intersects(first.hole_guard)):
        return False
    return True


def in_outline(board: Polygon, boundary, shape: Occupancy) -> bool:
    """Check body footprint, plated copper and bare hole geometry separately."""
    bx0, by0, bx1, by1 = board.bounds
    bb = shape.body_bounds
    if bb is not None and (
        bb[0] < bx0 - _AABB_EPS_MM or bb[2] > bx1 + _AABB_EPS_MM
        or bb[1] < by0 - _AABB_EPS_MM or bb[3] > by1 + _AABB_EPS_MM
    ):
        return False
    hb = shape.holes_bounds
    if hb is not None and (
        hb[0] < bx0 - _AABB_EPS_MM or hb[2] > bx1 + _AABB_EPS_MM
        or hb[1] < by0 - _AABB_EPS_MM or hb[3] > by1 + _AABB_EPS_MM
    ):
        return False
    cpb = shape.copper_bounds
    if cpb is not None and (
        cpb[0] < bx0 + COPPER_EDGE_MM - _AABB_EPS_MM
        or cpb[2] > bx1 - COPPER_EDGE_MM + _AABB_EPS_MM
        or cpb[1] < by0 + COPPER_EDGE_MM - _AABB_EPS_MM
        or cpb[3] > by1 - COPPER_EDGE_MM + _AABB_EPS_MM
    ):
        return False
    return (board.covers(shape.body)
            and (hb is None or board.covers(shape.holes))
            and (cpb is None or (board.covers(shape.copper)
                 and boundary.distance(shape.copper) >= COPPER_EDGE_MM - 1e-9)))


def _is_axis_aligned_rect(board: Polygon, board_bounds: Bounds4) -> bool:
    return (not board.interiors) and board.equals(box(*board_bounds))


def _candidate_valid(
    form: Occupancy,
    side: str,
    x: float,
    y: float,
    board: Polygon,
    boundary,
    board_bounds: Bounds4,
    is_rect_board: bool,
    fixed_entries: list[tuple[BaseGeometry, Bounds4 | None]],
    active_region_entries: list[tuple[BaseGeometry, Bounds4 | None]],
    check_shapes: list[tuple[str, Occupancy]],
) -> bool:
    """Conservative AABB + lazy-translation legality check matching exact Shapely."""
    bx0, by0, bx1, by1 = board_bounds
    bb = _shift_bounds(form.body_bounds, x, y)
    if bb is None:
        return False
    if (bb[0] < bx0 - _AABB_EPS_MM or bb[2] > bx1 + _AABB_EPS_MM
            or bb[1] < by0 - _AABB_EPS_MM or bb[3] > by1 + _AABB_EPS_MM):
        return False
    hb = _shift_bounds(form.holes_bounds, x, y)
    if hb is not None and (
        hb[0] < bx0 - _AABB_EPS_MM or hb[2] > bx1 + _AABB_EPS_MM
        or hb[1] < by0 - _AABB_EPS_MM or hb[3] > by1 + _AABB_EPS_MM
    ):
        return False
    cpb = _shift_bounds(form.copper_bounds, x, y)
    if cpb is not None and (
        cpb[0] < bx0 + COPPER_EDGE_MM - _AABB_EPS_MM
        or cpb[2] > bx1 - COPPER_EDGE_MM + _AABB_EPS_MM
        or cpb[1] < by0 + COPPER_EDGE_MM - _AABB_EPS_MM
        or cpb[3] > by1 - COPPER_EDGE_MM + _AABB_EPS_MM
    ):
        return False

    # Check body outline before translating copper/holes/hole_guard/combined.
    body_t = None
    body_strictly_inside = (
        is_rect_board
        and bb[0] >= bx0 + _AABB_EPS_MM and bb[2] <= bx1 - _AABB_EPS_MM
        and bb[1] >= by0 + _AABB_EPS_MM and bb[3] <= by1 - _AABB_EPS_MM
    )
    if not body_strictly_inside:
        body_t = translate(form.body, xoff=x, yoff=y)
        if not board.covers(body_t):
            return False

    for region_geom, region_bounds in active_region_entries:
        if not _aabb_separated(bb, region_bounds):
            if body_t is None:
                body_t = translate(form.body, xoff=x, yoff=y)
            if body_t.intersects(region_geom):
                return False

    cb = _shift_bounds(form.combined_bounds, x, y)
    bb0, bb1, bb2, bb3 = bb
    if form.body_is_rect:
        for existing_side, existing in check_shapes:
            if side == existing_side and existing.body_is_rect:
                eb = existing.body_bounds
                if eb is not None:
                    dx = eb[0] - bb2 if eb[0] > bb2 else (bb0 - eb[2] if bb0 > eb[2] else 0.0)
                    dy = eb[1] - bb3 if eb[1] > bb3 else (bb1 - eb[3] if bb1 > eb[3] else 0.0)
                    if dx * dx + dy * dy < _SAME_SIDE_INNER_SQ:
                        return False

    combined_t = None
    for keepout_geom, keepout_bounds in fixed_entries:
        if not _aabb_separated(cb, keepout_bounds):
            if combined_t is None:
                combined_t = translate(form.combined, xoff=x, yoff=y)
            if combined_t.intersects(keepout_geom):
                return False

    cb0, cb1, cb2, cb3 = cb if cb is not None else (0.0, 0.0, 0.0, 0.0)
    hgb = _shift_bounds(form.hole_guard_bounds, x, y)
    hole_guard_t = None
    for existing_side, existing in check_shapes:
        if side == existing_side and cb is not None:
            ecb = existing.combined_bounds
            if (ecb is not None
                    and not (cb2 + _SAME_SIDE_OUTER_MM < ecb[0]
                             or ecb[2] + _SAME_SIDE_OUTER_MM < cb0
                             or cb3 + _SAME_SIDE_OUTER_MM < ecb[1]
                             or ecb[3] + _SAME_SIDE_OUTER_MM < cb1)):
                if combined_t is None:
                    combined_t = translate(form.combined, xoff=x, yoff=y)
                if combined_t.distance(existing.combined) < SAME_SIDE_CLEARANCE_MM - 1e-9:
                    return False
        ehgb = existing.hole_guard_bounds
        if (ehgb is not None and cb is not None
                and not (cb2 + _AABB_EPS_MM < ehgb[0]
                         or ehgb[2] + _AABB_EPS_MM < cb0
                         or cb3 + _AABB_EPS_MM < ehgb[1]
                         or ehgb[3] + _AABB_EPS_MM < cb1)):
            if combined_t is None:
                combined_t = translate(form.combined, xoff=x, yoff=y)
            if combined_t.intersects(existing.hole_guard):
                return False
        if hgb is not None:
            ecb = existing.combined_bounds
            if (ecb is not None
                    and not (ecb[2] + _AABB_EPS_MM < hgb[0]
                             or hgb[2] + _AABB_EPS_MM < ecb[0]
                             or ecb[3] + _AABB_EPS_MM < hgb[1]
                             or hgb[3] + _AABB_EPS_MM < ecb[1])):
                if hole_guard_t is None:
                    hole_guard_t = translate(form.hole_guard, xoff=x, yoff=y)
                if existing.combined.intersects(hole_guard_t):
                    return False

    if hb is not None:
        holes_strictly_inside = (
            is_rect_board
            and hb[0] >= bx0 + _AABB_EPS_MM and hb[2] <= bx1 - _AABB_EPS_MM
            and hb[1] >= by0 + _AABB_EPS_MM and hb[3] <= by1 - _AABB_EPS_MM
        )
        if not holes_strictly_inside:
            if not board.covers(translate(form.holes, xoff=x, yoff=y)):
                return False

    if cpb is not None:
        copper_strictly_inside = (
            is_rect_board
            and cpb[0] >= bx0 + COPPER_EDGE_MM + _AABB_EPS_MM
            and cpb[2] <= bx1 - COPPER_EDGE_MM - _AABB_EPS_MM
            and cpb[1] >= by0 + COPPER_EDGE_MM + _AABB_EPS_MM
            and cpb[3] <= by1 - COPPER_EDGE_MM - _AABB_EPS_MM
        )
        if not copper_strictly_inside:
            copper_t = translate(form.copper, xoff=x, yoff=y)
            if not (board.covers(copper_t) and boundary.distance(copper_t) >= COPPER_EDGE_MM - 1e-9):
                return False

    return True


def validate_legacy_placement(spec: dict, placed: dict, sides: dict, fixed: set,
                              board: Polygon) -> dict:
    """Accept legacy placements only after checking the original side contract."""
    parts = {part["refdes"]: part for part in spec["components"]}
    if set(placed) != parts.keys() or set(sides) != parts.keys():
        return {"status": "rejected", "issues": [{"kind":"incomplete_placement"}]}
    requested = {entry["refdes"]: entry for entry in spec.get("edge_connectors", [])}
    fixed = fixed | requested.keys()
    protected_nets = frozenset(
        pair[key] for pair in spec.get("design_requirements", {}).get("differential_pairs", [])
        for key in ("pos_net", "neg_net") if key in pair
    )
    outline = spec["board_outline_mm"]
    ox, oy = outline.get("origin_x", 0), outline.get("origin_y", 0)
    mounting = [
        Point(hole["x_mm"] - ox, hole["y_mm"] - oy).buffer(mounting_guard_radius(hole), resolution=24)
        for hole in spec.get("mounting_holes", [])
    ]
    regions = mechanical_keepouts(spec)
    boundary = board.boundary
    issues, preserved_fixed_outline = [], []
    occupancy = {}
    for ref in sorted(parts):
        part, side = parts[ref], sides[ref]
        pose = placed[ref]
        if side not in side_options(part, protected_nets=protected_nets):
            issues.append({"kind": "source_side_constraint", "refdes": ref, "side": side})
            continue
        if not all(math.isfinite(value) for value in pose):
            issues.append({"kind": "nonfinite_pose", "refdes": ref})
            continue
        if ref in requested:
            entry = requested[ref]
            expected = entry["x_mm"] - ox, entry["y_mm"] - oy, entry.get("angle_deg", 0)
            if tuple(pose) != expected:
                issues.append({"kind": "fixed_pose_changed", "refdes": ref})
        x, y, angle = pose
        try:
            shape = local_occupancy(part, side).rotate(angle).translate(x, y)
        except PlacementGeometryUnsupported as exc:
            issues.append({"kind": "unsupported_geometry", "refdes": ref, "detail": str(exc)})
            continue
        occupancy[ref] = shape
        if not in_outline(board, boundary, shape):
            if ref in fixed:
                # A prescribed interface can have a pre-existing edge conflict.
                # Keep it pinned and report the exception for the final gate.
                preserved_fixed_outline.append(ref)
            else:
                issues.append({"kind": "movable_outline", "refdes": ref})
        if ref in fixed:
            continue
        if any(shape.combined.intersects(guard) for guard in mounting):
            issues.append({"kind": "mounting_guard", "refdes": ref})
        if hits_mechanical_keepout(shape.body, component_z_range(part, side), regions):
            issues.append({"kind": "mechanical_keepout", "refdes": ref})
    for ref, other in combinations(occupancy, 2):
        if sides[ref] == sides[other] and ref in fixed and other in fixed:
            continue
        if not noninterfering(sides[ref], occupancy[ref], sides[other], occupancy[other]):
            issues.append({"kind": "same_side_occupancy" if sides[ref] == sides[other]
                           else "opposite_side_drill_guard", "refdes": ref, "other_refdes": other})
    return {"status": "rejected" if issues else "accepted", "issues": issues,
            "preserved_fixed_outline": preserved_fixed_outline}


def _support_for_box(obstacle, projection, search, spacing):
    """Position the projected XY bounds, compensating extra search edge space."""
    return (obstacle[0] - spacing + search[2] - projection[2],
            obstacle[1] - spacing + search[3] - projection[3],
            obstacle[2] + spacing + search[0] - projection[0],
            obstacle[3] + spacing + search[1] - projection[1])


def repack_blockers(
    parts: dict,
    poses: dict,
    blocked: str,
    pinned,
    board: Polygon,
    target: tuple[float, float],
    *,
    fixed=(),
    mechanical_regions=(),
    protected_nets: frozenset[str] = frozenset(),
    probe_budget: int = 6500,
    anchor_probes: int = 80,
    branch_cap: int = 20,
    branch_probe_cap: int = 1200,
    max_blockers: int = 2,
    board_thickness_mm: float = PCB_THICKNESS_MM,
) -> RollbackResult:
    """Try outright placements for a single stalled part with 0–2 relocations.

    Placement is transactional: caller's mapping is unchanged on every path.
    All conflicts use the typed geometry used by the ordinary two-side packer.
    """
    pinned_set = frozenset(pinned)
    if blocked in poses or not pinned_set <= poses.keys() or blocked not in parts:
        raise ValueError("Invalid rollback state")
    if min(probe_budget, anchor_probes, branch_cap, branch_probe_cap, max_blockers) < 1:
        raise ValueError("Invalid rollback budget")
    x0, y0, width, height = board.bounds
    if abs(x0) > 1e-6 or abs(y0) > 1e-6:
        raise ValueError("Expected board-local frame")
    board_bounds: Bounds4 = (float(x0), float(y0), float(width), float(height))
    is_rect_board = _is_axis_aligned_rect(board, board_bounds)
    boundary = board.boundary
    fixed_entries = [(keepout, _geom_bounds(keepout)) for keepout in fixed]
    region_entries = [(geom, _geom_bounds(geom), low, high) for geom, low, high in mechanical_regions]
    count = 0
    branches = 0
    branch_limit: int | None = None

    @lru_cache(maxsize=None)
    def local(ref: str, side: str, angle: float = 0) -> Occupancy:
        return local_occupancy(parts[ref], side).rotate(angle)

    @lru_cache(maxsize=None)
    def active_regions_for(ref: str, side: str) -> list[tuple[BaseGeometry, Bounds4 | None]]:
        z_min, z_max = component_z_range(parts[ref], side, board_thickness_mm=board_thickness_mm)
        return [
            (geom, rbounds)
            for geom, rbounds, low, high in region_entries
            if min(z_max, high) > max(z_min, low)
        ]

    def shape_at(ref: str, pose: tuple[float, float, float, str]) -> tuple[str, Occupancy]:
        x, y, angle, side = pose
        return side, local(ref, side, angle).translate(x, y)

    initial = {ref: shape_at(ref, pose) for ref, pose in poses.items()}

    def constrained_support(ref: str, side: str, angle: int, others: dict):
        form = local(ref, side, angle)
        search = form.search_bounds()
        form_cb = form.combined_bounds
        form_hgb = form.hole_guard_bounds
        form_bb = form.body_bounds
        for other_side, other in others.values():
            other_cb = other.combined_bounds
            if other_side == side and other_cb is not None and form_cb is not None:
                yield _support_for_box(other_cb, form_cb, search, SAME_SIDE_CLEARANCE_MM)
            if other.hole_guard_bounds is not None and form_cb is not None:
                yield _support_for_box(other.hole_guard_bounds, form_cb, search, 0)
            if form_hgb is not None and other_cb is not None:
                yield _support_for_box(other_cb, form_hgb, search, 0)
        for _, keepout_bounds in fixed_entries:
            if keepout_bounds is not None and form_cb is not None:
                yield _support_for_box(keepout_bounds, form_cb, search, 0)
        if form_bb is not None:
            for _, rbounds in active_regions_for(ref, side):
                if rbounds is not None:
                    yield _support_for_box(rbounds, form_bb, search, 0)

    def candidates(ref: str, side: str, anchor: tuple[float, float], others: dict, limit: int,
                   *, must_clear: list[tuple[str, Occupancy]] | None = None,
                   skip_poses: set | None = None):
        nonlocal count
        form = local(ref, side)
        active_regs = active_regions_for(ref, side)
        check_list = must_clear if must_clear is not None else []
        ranked = merge(*(
            _angle_candidates(form.search_bounds(), anchor, (width, height), angle,
                              tuple(constrained_support(ref, side, angle, others)), 0., 0.)
            for angle in ANGLES
        ))
        for _, x_um, y_um, angle in islice(ranked, limit):
            if count >= probe_budget:
                raise _ProbeBudget
            if branch_limit is not None and count >= branch_limit:
                raise _BranchProbeBudget
            count += 1
            x, y = x_um / 1000., y_um / 1000.
            pose = (x, y, float(angle), side)
            if skip_poses is not None and pose in skip_poses:
                continue
            form_rot = local(ref, side, angle)
            if _candidate_valid(
                form_rot, side, x, y, board, boundary, board_bounds, is_rect_board,
                fixed_entries, active_regs, check_list,
            ):
                yield pose, form_rot.translate(x, y)

    def conflicts(side: str, shape: Occupancy, other_shapes: dict) -> tuple[str, ...]:
        return tuple(ref for ref, (other_side, other) in other_shapes.items()
                     if not noninterfering(side, shape, other_side, other))

    def anchors(goal: tuple[float, float]):
        x, y = goal
        # Geometric anchors, rather than four equivalent orientations at the
        # centre, give a distinct chance to preserve a large open region.
        return tuple(dict.fromkeys((goal, (0, y), (width, y), (x, 0), (x, height),
                                    (0, 0), (width, 0), (0, height), (width, height))))

    def alternatives(ref: str, other_shapes: dict):
        old = poses[ref]
        options = side_options(parts[ref], protected_nets=protected_nets)
        if old[3] not in options:
            return
        sides = (old[3], *(side for side in options if side != old[3]))
        seen: set = set()
        other_list = list(other_shapes.values())
        for side in sides:
            for anchor in anchors(old[:2]):
                found = 0
                for pose, shape in candidates(
                    ref, side, anchor, other_shapes, anchor_probes,
                    must_clear=other_list, skip_poses=seen,
                ):
                    seen.add(pose)
                    yield pose, shape
                    found += 1
                    if found == 2:
                        break

    def refill(refs: list[str], state: dict, output: dict):
        if not refs:
            return output
        ref, *rest = refs
        for pose, shape in alternatives(ref, state):
            solved = refill(rest, dict(state, **{ref: (pose[3], shape)}), dict(output, **{ref: pose}))
            if solved is not None:
                return solved
        return None

    proposals = []
    try:
        seen: set = set()
        for side in side_options(parts[blocked], protected_nets=protected_nets):
            for anchor in anchors(target):
                for pose, shape in candidates(blocked, side, anchor, initial, anchor_probes, skip_poses=seen):
                    touched = conflicts(side, shape, initial)
                    if pinned_set.intersection(touched) or len(touched) > max_blockers:
                        continue
                    seen.add(pose)
                    proposals.append((
                        len(touched),
                        (pose[0] - target[0]) ** 2 + (pose[1] - target[1]) ** 2,
                        side != "top",
                        pose,
                        shape,
                        touched,
                    ))
                    # One witness per remote anchor prevents local target
                    # neighborhoods from taking the entire branch budget.
                    break
        proposals.sort(key=lambda record: record[:4])
        for _, _, _, pose, shape, touched in islice(proposals, branch_cap):
            branches += 1
            branch_limit = count + branch_probe_cap
            rest = {ref: info for ref, info in initial.items() if ref not in touched}
            rest[blocked] = (pose[3], shape)
            try:
                recovered = refill(
                    sorted(touched, key=lambda r: (-initial[r][1].combined.area, r)),
                    rest,
                    {blocked: pose},
                )
            except _BranchProbeBudget:
                continue
            if recovered is not None:
                updated = dict(poses, **recovered)
                moved = tuple(sorted(ref for ref in touched if updated[ref] != poses[ref]))
                return RollbackResult("repacked", updated, count, branches, moved)
    except _ProbeBudget:
        return RollbackResult("probe_budget_exhausted", None, count, branches)
    status = "branch_budget_exhausted" if len(proposals) > branch_cap else "search_exhausted"
    return RollbackResult(status, None, count, branches)


def pack_two_sided(
    parts: dict,
    reserved: dict,
    board: Polygon,
    fixed: list,
    target,
    commit,
    *,
    board_size,
    mode: str,
    protected_nets: frozenset[str],
    mechanical_regions=(),
    board_thickness_mm: float = PCB_THICKNESS_MM,
    rollback_probe_budget: int = 12000,
    rollback_per_part_budget: int = 4000,
    rollback_branch_probe_cap: int = 1000,
    rollback_branch_cap: int = 20,
    rollback_max_blockers: int = 2,
) -> list[str]:
    """One transactional attempt: fixed and single-sided parts before flexible passives."""
    @lru_cache(maxsize=None)
    def local(ref: str, side: str, angle: float = 0) -> Occupancy:
        return local_occupancy(parts[ref], side).rotate(angle)

    placed_shapes: dict[str, tuple[str, Occupancy]] = {}
    staged_poses: dict[str, tuple[float, float, float, str]] = {}
    for ref, (x, y, angle) in reserved.items():
        side = parts[ref].get("side", "top")
        staged_poses[ref] = (x, y, angle, side)
        placed_shapes[ref] = (side, local(ref, side, angle).translate(x, y))

    pinned = frozenset(reserved)
    board_bounds: Bounds4 = tuple(float(v) for v in board.bounds)  # type: ignore[assignment]
    is_rect_board = _is_axis_aligned_rect(board, board_bounds)
    boundary = board.boundary
    fixed_entries = [(keepout, _geom_bounds(keepout)) for keepout in fixed]
    region_entries = [(geom, _geom_bounds(geom), low, high) for geom, low, high in mechanical_regions]
    options = {ref: side_options(part, protected_nets=protected_nets)
               for ref, part in parts.items() if ref not in reserved}
    can_rollback = (
        rollback_probe_budget > 0
        and abs(board_bounds[0]) <= 1e-6
        and abs(board_bounds[1]) <= 1e-6
    )
    remaining_rollback_probes = rollback_probe_budget

    def order_key(ref: str):
        x0, y0, x1, y1 = local(ref, options[ref][0]).search_bounds()
        w, h = x1 - x0, y1 - y0
        first, second = (max(w, h), w * h) if mode == "long-side" else (w * h, max(w, h))
        return len(options[ref]), -first, -second, ref

    order = sorted(options, key=order_key)

    for ref in order:
        last_exhausted: PackingExhausted | None = None
        for side in options[ref]:
            check_shapes = list(placed_shapes.values())
            height_range = component_z_range(parts[ref], side, board_thickness_mm=board_thickness_mm)
            active_region_entries = [
                (geom, rbounds)
                for geom, rbounds, low, high in region_entries
                if min(height_range[1], high) > max(height_range[0], low)
            ]

            def valid(_, x, y, angle):
                return _candidate_valid(
                    local(ref, side, angle), side, x, y, board, boundary, board_bounds,
                    is_rect_board, fixed_entries, active_region_entries, check_shapes,
                )

            def accepted(_, x, y, angle):
                placed_shapes[ref] = (side, local(ref, side, angle).translate(x, y))
                staged_poses[ref] = (x, y, angle, side)

            def local_supports(_, angle):
                form = local(ref, side, angle)
                search = form.search_bounds()
                form_cb = form.combined_bounds
                form_hgb = form.hole_guard_bounds
                form_bb = form.body_bounds
                for existing_side, shape in check_shapes:
                    existing_cb = shape.combined_bounds
                    if existing_side == side and existing_cb is not None and form_cb is not None:
                        yield _support_for_box(existing_cb, form_cb, search, SAME_SIDE_CLEARANCE_MM)
                    if shape.hole_guard_bounds is not None and form_cb is not None:
                        yield _support_for_box(shape.hole_guard_bounds, form_cb, search, 0)
                    if form_hgb is not None and existing_cb is not None:
                        yield _support_for_box(existing_cb, form_hgb, search, 0)
                for _, keepout_bounds in fixed_entries:
                    if keepout_bounds is not None and form_cb is not None:
                        yield _support_for_box(keepout_bounds, form_cb, search, 0)
                if form_bb is not None:
                    for _, rbounds in active_region_entries:
                        if rbounds is not None:
                            yield _support_for_box(rbounds, form_bb, search, 0)

            try:
                pack_movable([ref], {ref: local(ref, side).search_bounds()}, target, valid, accepted, board_size,
                             clearance=0, edge_inset=0, angle_support_bounds=local_supports)
            except PackingExhausted as exc:
                last_exhausted = exc
            else:
                last_exhausted = None
                break

        if last_exhausted is not None:
            recovered = False
            if can_rollback and remaining_rollback_probes > 0:
                part_budget = min(remaining_rollback_probes, rollback_per_part_budget)
                ref_target = target(ref) if callable(target) else target[ref]
                rb = repack_blockers(
                    parts,
                    staged_poses,
                    ref,
                    pinned,
                    board,
                    ref_target,
                    fixed=fixed,
                    mechanical_regions=mechanical_regions,
                    protected_nets=protected_nets,
                    probe_budget=part_budget,
                    branch_cap=rollback_branch_cap,
                    branch_probe_cap=min(rollback_branch_probe_cap, part_budget),
                    max_blockers=rollback_max_blockers,
                    board_thickness_mm=board_thickness_mm,
                )
                remaining_rollback_probes = max(0, remaining_rollback_probes - rb.probes)
                if rb.status == "repacked" and rb.poses is not None:
                    staged_poses = dict(rb.poses)
                    for changed_ref in (ref, *rb.moved):
                        cx, cy, cangle, cside = staged_poses[changed_ref]
                        placed_shapes[changed_ref] = (cside, local(changed_ref, cside, cangle).translate(cx, cy))
                    recovered = True
            if not recovered:
                raise last_exhausted

    # Atomic commit only after the entire two-sided packing succeeds.
    moved = []
    for ref in order:
        x, y, angle, side = staged_poses[ref]
        if side == "bottom" and len(options[ref]) > 1:
            moved.append(ref)
        commit(ref, (x, y, angle), side)
    return moved
