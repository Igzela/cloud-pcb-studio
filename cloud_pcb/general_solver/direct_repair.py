"""Propose full-width straight repairs using the final routed copper geometry."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass

from kiutils.items.brditems import Arc, Segment, Via
from shapely.errors import GEOSException
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from cloud_pcb.general_solver.geometry import arc_parameters, copper_arc_points, drill_geometry
from cloud_pcb.layout_route.dsn_ses_bridge import _footprint_ref, _rotate_kicad


COPPER_LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")
BOARD_EDGE_CLEARANCE_MM = .50
BOARD_COPPER_CLEARANCE_MM = .15
# Leave room for DSN coordinate rounding. Shapely's pad circles use 16
# segments per quadrant, so their chords also need an outward error bound.
GRID_MARGIN_MM = .001
CIRCLE_CHORD_FRACTION = 1 - math.cos(math.pi / 64)
SUPPORTED_PAD_SHAPES = {"circle", "oval", "rect", "roundrect"}


@dataclass(frozen=True)
class DirectRepair:
    net: str
    layer: str
    width_mm: float
    start_xy: tuple[float, float]
    end_xy: tuple[float, float]


@dataclass(frozen=True)
class _Obstacle:
    geometry: BaseGeometry
    net: str | None = None
    # Positive omitted radius for centerlines, or conservative arc error for
    # polygonized copper. Distances are tested against it as well as clearance.
    extra_radius_mm: float = 0.
    drill_owner: int | None = None


def _point(position) -> tuple[float, float]:
    return _finite(position.X), _finite(position.Y)


def _finite(value) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("non-finite or non-numeric geometry")
    return float(value)


def _positive(value) -> float:
    result = _finite(value)
    if result <= 0:
        raise ValueError("non-positive geometry")
    return result


def _micrometres(value) -> int:
    return round(_finite(value) * 1000)


def _pad_dsn_center(fp, pad) -> tuple[int, int]:
    px, py = _point(fp.position)
    dx, dy = _rotate_kicad(*_point(pad.position), _finite(fp.position.angle or 0.))
    # This is the sum of two quantized coordinates in the DSN, rather than
    # one rounded world coordinate. Reverse the bridge's Y flip here.
    return _micrometres(px) + _micrometres(dx), _micrometres(py) + _micrometres(dy)


def _copper_layers(layers) -> tuple[str, ...]:
    if "*.Cu" in layers:
        return COPPER_LAYERS
    if any(layer.endswith(".Cu") and layer not in COPPER_LAYERS for layer in layers):
        raise ValueError("unsupported copper layer")
    return tuple(layer for layer in COPPER_LAYERS if layer in layers)


def _polygon(points) -> Polygon:
    geometry = Polygon(points)
    if geometry.is_empty or not geometry.is_valid or not math.isfinite(geometry.area):
        raise ValueError("invalid obstacle polygon")
    return geometry


def _keepout_polygon(keepout) -> Polygon:
    if "polygon_mm" in keepout and keepout["polygon_mm"]:
        return _polygon([(_finite(x), _finite(y)) for x, y in keepout["polygon_mm"]])
    x0, y0, x1, y1 = [
        _finite(keepout[key]) for key in ("x_min", "y_min", "x_max", "y_max")
    ]
    if not (x0 < x1 and y0 < y1):
        raise ValueError("invalid obstacle rectangle")
    return box(x0, y0, x1, y1)


def _board_obstacles(board, keepouts):
    from cloud_pcb.general_solver.run_blind import _pad_world_polygon_kicad

    obstacles = {layer: [] for layer in COPPER_LAYERS}
    pads = defaultdict(list)
    references = Counter(_footprint_ref(fp, i) for i, fp in enumerate(board.footprints))
    names = {net.number: net.name for net in board.nets}
    if len(names) != len(board.nets) or len(set(names.values())) != len(names):
        raise ValueError("duplicate net data")

    def add(layers, geometry, *, net=None, extra_radius_mm=0., drill_owner=None):
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError("invalid copper or hole geometry")
        item = _Obstacle(geometry, net, extra_radius_mm, drill_owner)
        for layer in layers:
            obstacles[layer].append(item)

    for i, fp in enumerate(board.footprints):
        ref = _footprint_ref(fp, i)
        if fp.zones or any(getattr(g, "layer", "") in COPPER_LAYERS for g in fp.graphicItems):
            raise ValueError("unsupported footprint copper graphic or zone")
        _point(fp.position)
        _finite(fp.position.angle or 0.)
        for pad in fp.pads:
            _point(pad.position)
            _finite(pad.position.angle or 0.)
            if pad.type in ("np_thru_hole", "thru_hole") and pad.drill is None:
                raise ValueError("missing hole geometry")
            if pad.drill is not None:
                drill_mm = _positive(pad.drill.diameter)
                drill_span_mm = _positive(pad.drill.width) if pad.drill.width is not None else drill_mm
                add(COPPER_LAYERS, drill_geometry(fp, pad), drill_owner=id(pad),
                    extra_radius_mm=max(drill_mm, drill_span_mm) / 2 * CIRCLE_CHORD_FRACTION)
            layers = _copper_layers(pad.layers)
            if pad.type == "np_thru_hole" or not layers:
                continue
            if pad.shape not in SUPPORTED_PAD_SHAPES or pad.customPadPrimitives:
                raise ValueError("unsupported pad copper geometry")
            sx, sy = _positive(pad.size.X), _positive(pad.size.Y)
            if pad.shape == "circle" and not math.isclose(sx, sy, abs_tol=1e-9):
                raise ValueError("invalid circular pad dimensions")
            if pad.shape == "roundrect" and pad.roundrectRatio is not None:
                ratio = _finite(pad.roundrectRatio)
                if not 0 < ratio <= .5:
                    raise ValueError("invalid roundrect ratio")
            net = names.get(pad.net.number) if pad.net else None
            if pad.net and net != pad.net.name:
                raise ValueError("contradictory pad net assignment")
            shape = _pad_world_polygon_kicad(fp, pad)
            pad_error = (max(sx, sy) / 2 * CIRCLE_CHORD_FRACTION
                         if pad.shape != "rect" else 0.)
            add(layers, shape, net=net, extra_radius_mm=pad_error)
            if references[ref] == 1 and pad.number:
                pads[(ref, str(pad.number).strip())].append((fp, pad, net, layers))

    for track in board.traceItems:
        net = names.get(track.net)
        if isinstance(track, Segment):
            geometry = LineString([_point(track.start), _point(track.end)])
            add(_copper_layers([track.layer]), geometry, net=net,
                extra_radius_mm=_positive(track.width) / 2)
        elif isinstance(track, Arc):
            if arc_parameters(track) is None:
                raise ValueError("invalid routed arc")
            geometry = LineString(copper_arc_points(track))
            add(_copper_layers([track.layer]), geometry, net=net,
                extra_radius_mm=_positive(track.width) / 2 + .0005)
        elif isinstance(track, Via):
            first, last = sorted(COPPER_LAYERS.index(layer) for layer in track.layers)
            layers = COPPER_LAYERS[first:last + 1]
            geometry = Point(_point(track.position))
            size, drill = _positive(track.size), _positive(track.drill)
            if size <= drill:
                raise ValueError("invalid via annulus")
            add(layers, geometry, net=net, extra_radius_mm=size / 2)
            add(layers, geometry, extra_radius_mm=drill / 2)
        else:
            raise ValueError("unsupported routed copper geometry")

    if any(getattr(item, "layer", "") in COPPER_LAYERS for item in board.graphicItems):
        raise ValueError("unsupported board copper graphic")
    for zone in board.zones:
        if zone.keepoutSettings:
            if zone.keepoutSettings.tracks == "not_allowed":
                for polygon in zone.polygons:
                    add(_copper_layers(zone.layers),
                        _polygon([_point(p) for p in polygon.coordinates]))
        else:
            for polygon in zone.filledPolygons:
                add(_copper_layers([polygon.layer]),
                    _polygon([_point(p) for p in polygon.coordinates]),
                    net=names.get(zone.net))

    for keepout in keepouts:
        kind = keepout.get("dsn_kind", "keepout")
        if kind == "via_keepout":
            continue
        if kind not in ("keepout", "wire_keepout"):
            raise ValueError("unsupported DSN keepout")
        add(_copper_layers(keepout.get("layers", ("F.Cu", "B.Cu"))),
            _keepout_polygon(keepout))
    return pads, obstacles


def _endpoint(connection, key, pads, net, width_mm):
    item = connection.get(key)
    xy = connection.get(f"{key}_xy")
    if not isinstance(item, dict) or item.get("kind") != "pin":
        return None
    if not isinstance(xy, (list, tuple)) or len(xy) != 2:
        return None
    ref, label = item.get("component"), item.get("pin")
    if not isinstance(ref, str) or not isinstance(label, str):
        return None
    matching = pads.get((ref, label), ())
    if len(matching) != 1:
        return None
    fp, pad, pad_net, layers = matching[0]
    if pad_net != net or pad.type != "thru_hole" or len(layers) != len(COPPER_LAYERS):
        return None
    if pad.drill.oval or pad.drill.offset:
        return None
    if min(pad.size.X, pad.size.Y) < max(width_mm, pad.drill.diameter + GRID_MARGIN_MM):
        return None
    position = _pad_dsn_center(fp, pad)
    if any(abs(_finite(axis) * 1000 - expected) > .05 for axis, expected in zip(xy, position)):
        return None
    return fp, pad, position


def _can_fit(centerline, layer, net, width, drill_owners, board_poly, obstacles, clearances):
    radius = width / 2
    if not board_poly.covers(centerline):
        return False
    if board_poly.boundary.distance(centerline) < radius + BOARD_EDGE_CLEARANCE_MM + GRID_MARGIN_MM:
        return False
    for obstacle in obstacles[layer]:
        if obstacle.net == net or obstacle.drill_owner in drill_owners:
            continue
        clearance = max(clearances[net], clearances[obstacle.net])
        if centerline.distance(obstacle.geometry) < radius + clearance + obstacle.extra_radius_mm + GRID_MARGIN_MM:
            return False
    return True


def propose_local_output_links(board, board_poly, keepouts, netclasses, net_to_class, routing_layers, targets):
    """Connect proven local decouplers before neighboring nets claim the pad exit."""
    from cloud_pcb.general_solver.run_blind import _pad_world_polygon_kicad

    if not targets:
        return []
    try:
        rule = netclasses['DEFAULT']
        if rule.impedance_ohm is not None or rule.diff_pair_gap_mm is not None:
            return []
        width = math.ceil(_positive(rule.trace_width_mm) * 1000 - 1e-9) / 1000
        base_clearance = max(BOARD_COPPER_CLEARANCE_MM, _finite(rule.clearance_mm))
        clearances = defaultdict(lambda: base_clearance)
        for net, name in net_to_class.items():
            clearances[net] = max(base_clearance, _finite(netclasses[name].clearance_mm))
        pads, obstacles = _board_obstacles(board, keepouts)
    except (AttributeError, GEOSException, KeyError, TypeError, ValueError):
        return []

    proposals = []
    allowed = routing_layers.get('DEFAULT', ('F.Cu', 'B.Cu'))
    for cap_ref, target in sorted(targets.items()):
        net = target['net']
        if net_to_class.get(net, 'DEFAULT') != 'DEFAULT':
            continue
        owner_pads = pads.get((target['owner_ref'], target['owner_pin']), ())
        cap_pads = pads.get((cap_ref, target['cap_pin']), ())
        if len(owner_pads) != 1 or len(cap_pads) != 1:
            continue
        owner, source, source_net, layers = owner_pads[0]
        cap, sink, sink_net, sink_layers = cap_pads[0]
        if source_net != net or sink_net != net or layers != sink_layers or len(layers) != 1:
            continue
        layer, = layers
        if layer not in allowed or (not rule.zero_via_allowed and layer != 'F.Cu'):
            continue
        if any(pad.type != 'smd' or pad.drill or min(pad.size.X, pad.size.Y) < width for pad in (source, sink)):
            continue

        start = _pad_dsn_center(owner, source)
        end = _pad_dsn_center(cap, sink)
        bounds = _pad_world_polygon_kicad(owner, source).bounds
        spans = (bounds[2] - bounds[0], bounds[3] - bounds[1])
        along_axis = 0 if spans[0] >= spans[1] else 1
        across_axis = 1 - along_axis
        if spans[along_axis] < 1.25 * spans[across_axis] or math.dist(start, end) > 8000:
            continue
        center = _point(owner.position)[along_axis]
        outward = 1 if start[along_axis] > 1000 * center else -1
        along = outward * (end[along_axis] - start[along_axis])
        across = end[across_axis] - start[across_axis]
        lead = math.ceil(1000 * (spans[along_axis] / 2 + width / 2 + GRID_MARGIN_MM))
        if along < lead:
            continue

        max_diagonal = min(along - lead, abs(across))
        for diagonal in range(max_diagonal, -50, -50):
            # Sample monotone axis/diagonal/axis paths starting beyond the IC pad.
            diagonal = max(diagonal, 0)
            corner = list(start)
            corner[along_axis] = end[along_axis] - outward * diagonal
            landing = list(end)
            landing[across_axis] = start[across_axis] + (diagonal if across >= 0 else -diagonal)
            points = [tuple(axis / 1000 for axis in point) for point in (start, corner, landing, end)]
            segments = [(a, b, LineString([a, b])) for a, b in zip(points, points[1:]) if a != b]
            if all(_can_fit(line, layer, net, width, (), board_poly, obstacles, clearances)
                   for _, _, line in segments):
                for a, b, line in segments:
                    proposals.append(DirectRepair(net, layer, width, a, b))
                    obstacles[layer].append(_Obstacle(line, net, width / 2))
                break
    return proposals


def propose_direct_repairs(
    routed_board,
    board_poly,
    keepouts,
    netclasses,
    net_to_class,
    routing_layers,
    router_report,
    *,
    allow_power_classes: bool = False,
    plane_ground_net: str | None = None,
) -> list[DirectRepair]:
    """Return safe candidates for a later engine check and independent board audit.

    Only line segments between distinct, uniquely identified plated pads of a
    single footprint qualify. The board should contain selected routed copper
    before filling fresh pours. No input is modified.
    """
    from cloud_pcb.general_solver.run_blind import _is_gnd_net

    unresolved = router_report.get("unrouted", [])
    default = netclasses.get("DEFAULT")
    if not isinstance(unresolved, list) or not unresolved or default is None:
        return []
    try:
        if not board_poly.is_valid or board_poly.is_empty or board_poly.area <= 0:
            return []
        base_clearance = max(BOARD_COPPER_CLEARANCE_MM, _finite(default.clearance_mm))
        clearances = defaultdict(lambda: base_clearance)
        for net, class_name in net_to_class.items():
            clearances[net] = max(base_clearance, _finite(netclasses[class_name].clearance_mm))
        pads, obstacles = _board_obstacles(routed_board, keepouts)
    except (AttributeError, GEOSException, KeyError, TypeError, ValueError):
        # Unknown geometry is an obstacle, not permission to add copper.
        return []

    candidates = []
    for connection in unresolved:
        if not isinstance(connection, dict):
            continue
        net = connection.get("net")
        if not isinstance(net, str) or not net:
            continue
        if plane_ground_net is not None:
            if net == plane_ground_net:
                continue
        elif _is_gnd_net(net):
            continue
        class_name = net_to_class.get(net, "DEFAULT")
        if allow_power_classes:
            if class_name != "DEFAULT" and not (
                isinstance(class_name, str) and class_name.startswith("POWER")
            ):
                continue
        elif class_name != "DEFAULT":
            continue
        rule = netclasses.get(class_name)
        if rule is None or rule.impedance_ohm is not None or rule.diff_pair_gap_mm is not None:
            continue
        try:
            width = math.ceil(_positive(rule.trace_width_mm) * 1000 - 1e-9) / 1000
            allowed = routing_layers.get(
                class_name, routing_layers.get("DEFAULT", ("F.Cu", "B.Cu"))
            )
            if not rule.zero_via_allowed:
                allowed = tuple(layer for layer in allowed if layer == "F.Cu")
            if not allowed or any(layer not in COPPER_LAYERS for layer in allowed):
                continue
            start = _endpoint(connection, "from", pads, net, width)
            end = _endpoint(connection, "to", pads, net, width)
        except (AttributeError, TypeError, ValueError):
            continue
        if start is None or end is None or start[0] is not end[0] or start[1] is end[1]:
            continue
        p, q = sorted((start[2], end[2]))
        dx, dy = q[0] - p[0], q[1] - p[1]
        if p == q or (dx and dy and abs(dx) != abs(dy)):
            continue
        candidates.append((math.hypot(dx, dy), net, p, q, {id(start[1]), id(end[1])}, width, allowed))

    proposals = []
    seen = set()
    for _, net, start_um, end_um, drill_owners, width, allowed in sorted(candidates, key=lambda c: c[:4]):
        key = (net, start_um, end_um)
        if key in seen:
            continue
        seen.add(key)
        start = tuple(axis / 1000 for axis in start_um)
        end = tuple(axis / 1000 for axis in end_um)
        centerline = LineString([start, end])
        placed_direct = False
        for layer in allowed:
            if _can_fit(centerline, layer, net, width, drill_owners, board_poly, obstacles, clearances):
                proposals.append(DirectRepair(net, layer, width, start, end))
                obstacles[layer].append(_Obstacle(centerline, net, width / 2))
                placed_direct = True
                break
        if not placed_direct and allow_power_classes and (start_um[0] == end_um[0] or start_um[1] == end_um[1]):
            for offset_um in (650, 750, 800, 900, 1000, 1150, 1300, 1500):
                if placed_direct:
                    break
                for sign in (-1, 1):
                    if start_um[0] == end_um[0]:
                        mid_x = (start_um[0] + sign * offset_um) / 1000
                        p1, p2 = (mid_x, start[1]), (mid_x, end[1])
                    else:
                        mid_y = (start_um[1] + sign * offset_um) / 1000
                        p1, p2 = (start[0], mid_y), (end[0], mid_y)
                    segs = (
                        (start, p1, LineString([start, p1])),
                        (p1, p2, LineString([p1, p2])),
                        (p2, end, LineString([p2, end])),
                    )
                    for layer in allowed:
                        if all(
                            _can_fit(seg, layer, net, width, drill_owners, board_poly, obstacles, clearances)
                            for _, _, seg in segs
                        ):
                            for s_pt, e_pt, seg in segs:
                                proposals.append(DirectRepair(net, layer, width, s_pt, e_pt))
                                obstacles[layer].append(_Obstacle(seg, net, width / 2))
                            placed_direct = True
                            break
                    if placed_direct:
                        break
    return proposals

