"""Measure two-terminal differential path length and geometric skew in KiCad."""

import math
from collections import defaultdict

import networkx as nx
from kiutils.items.brditems import Segment, Via
from shapely.errors import GEOSException
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from cloud_pcb.general_solver.geometry import via_barrel_length_mm
from cloud_pcb.layout_route.dsn_ses_bridge import _rotate_kicad

# 1 µm per axis covers native µm serialization and still requires a unique
# route vertex at each pad. This distance contributes to the reported error.
TERMINAL_TOLERANCE_MM = math.sqrt(2) / 1000


def _terminals(board, net, endpoints):
    if len(endpoints) != 2 or len({tuple(endpoint) for endpoint in endpoints}) != 2:
        raise ValueError('exactly two distinct declared terminals are required')
    pads = []
    for ref, number in endpoints:
        matches = [(fp, pad) for fp in board.footprints if fp.properties.get('Reference') == ref
                   for pad in fp.pads if str(pad.number) == str(number)]
        if len(matches) != 1:
            raise ValueError(f'{ref}-{number} does not identify exactly one pad')
        fp, pad = matches[0]
        if pad.net is None or pad.net.name != net or pad.type not in ('smd', 'thru_hole'):
            raise ValueError(f'{ref}-{number} lacks a supported same-net terminal pad')
        if (pad.shape not in ('circle', 'oval', 'rect', 'roundrect') or pad.customPadPrimitives
                or not all(math.isfinite(value) and value > 0 for value in (pad.size.X, pad.size.Y))):
            raise ValueError(f'{ref}-{number} lacks a supported copper pad shape')
        dx, dy = _rotate_kicad(pad.position.X, pad.position.Y, fp.position.angle or 0)
        xy = (fp.position.X + dx, fp.position.Y + dy)
        if any(not math.isfinite(value) for value in xy):
            raise ValueError('non-finite pad position')
        tolerance = min(TERMINAL_TOLERANCE_MM, pad.size.X / 2, pad.size.Y / 2)
        pads.append((f'{ref}-{number}', xy, set(pad.layers), tolerance))
    return pads


def _trace_graph(board, net_number):
    graph = nx.Graph()
    layer_traces = defaultdict(list)
    vias = []
    split_by_layer = defaultdict(set)
    for item in board.traceItems:
        if item.net != net_number:
            continue
        if isinstance(item, Segment):
            a, b = (item.start.X, item.start.Y), (item.end.X, item.end.Y)
            if (not item.layer.endswith('.Cu') or item.width <= 0 or a == b
                    or not all(math.isfinite(v) for v in (*a, *b, item.width))):
                raise ValueError('invalid route segment geometry')
            layer_traces[item.layer].append(LineString([a, b]))
        elif isinstance(item, Via):
            xy = (item.position.X, item.position.Y)
            if (len(item.layers) != 2 or len(set(item.layers)) != 2 or not 0 < item.drill < item.size
                    or not all(math.isfinite(v) for v in (*xy, item.drill, item.size))):
                raise ValueError('invalid plated via geometry')
            vias.append((item, xy))
            for layer in item.layers:
                split_by_layer[layer].add(xy)
        else:
            raise ValueError(f'unsupported route item: {type(item).__name__}')
    if any(zone.net == net_number and not zone.keepoutSettings for zone in board.zones):
        raise ValueError('the net contains zone copper, without a unique centerline')

    for layer, lines in layer_traces.items():
        combined = unary_union(lines)
        parts = combined.geoms if combined.geom_type == 'MultiLineString' else (combined,)
        for part in parts:
            points = list(part.coords)
            for a, b in zip(points, points[1:]):
                line = LineString([a, b])
                splits = [xy for xy in split_by_layer[layer] if xy not in (a, b) and line.covers(Point(xy))]
                splits.sort(key=lambda xy: math.dist(a, xy))
                splits = [a, *splits, b]
                for start, end in zip(splits, splits[1:]):
                    graph.add_edge((layer, *start), (layer, *end), length=math.dist(start, end))

    stackup = getattr(board.setup.stackup, 'layers', ())
    copper_order = [layer.name for layer in stackup if layer.name.endswith('.Cu')]
    for item, xy in vias:
        first, last = item.layers
        attached = {layer for layer in layer_traces if (layer, *xy) in graph}
        if len(attached) < 2:
            continue
        if (len(copper_order) != len(set(copper_order)) or first not in copper_order or last not in copper_order
                or any(
            layer.thickness is None or not math.isfinite(layer.thickness) or layer.thickness <= 0 for layer in stackup
        )):
            raise ValueError('declared stackup thickness is required to measure a via path')
        low, high = sorted((copper_order.index(first), copper_order.index(last)))
        if attached.intersection(copper_order[low:high + 1]) != {first, last}:
            raise ValueError('routing attaches to an unmodeled layer within the via span')
        graph.add_edge((first, *xy), (last, *xy), length=via_barrel_length_mm(board, item))
    return graph


def _net_length(board, spec, net):
    try:
        numbers = [entry.number for entry in board.nets if entry.name == net]
        if len(numbers) != 1 or sum(entry.number == numbers[0] for entry in board.nets) != 1:
            raise ValueError('net assignment is missing or ambiguous')
        terminals = _terminals(board, net, spec['nets'].get(net, []))
        graph = _trace_graph(board, numbers[0])
        nodes, errors = [], []
        for label, xy, layers, tolerance in terminals:
            matches = [(vertex, math.dist(xy, vertex[1:])) for vertex in graph
                       if (vertex[0] in layers or '*.Cu' in layers) and math.dist(xy, vertex[1:]) <= tolerance]
            if len(matches) != 1:
                raise ValueError(f'{label} has {len(matches)} possible route vertices at the pad center')
            vertex, error = matches[0]
            nodes.append(vertex)
            errors.append(error)
        if nodes[0] == nodes[1] or not nx.has_path(graph, *nodes):
            raise ValueError('terminal vertices do not have a distinct connected path')
        connected = graph.subgraph(nx.node_connected_component(graph, nodes[0]))
        if not nx.is_tree(connected):
            raise ValueError('the connected route contains multiple centerline paths')
        path = nx.shortest_path(connected, *nodes)
        length = sum(connected[a][b]['length'] for a, b in zip(path, path[1:]))
        return {'length_mm': length, 'pad_alignment_bound_mm': sum(errors),
                'terminals': [dict(pad=item[0], route_vertex=list(vertex)) for item, vertex in zip(terminals, nodes)]}
    except (GEOSException, KeyError, TypeError, ValueError) as exc:
        return {'length_mm': None, 'reason': str(exc)}


def measure_differential_paths(board, spec):
    """Check the independent geometric path against explicit length skew limits."""
    pairs = []
    for pair in spec['design_requirements'].get('differential_pairs', []):
        positive, negative = pair['pos_net'], pair['neg_net']
        measured = {net: _net_length(board, spec, net) for net in (positive, negative)}
        value = pair.get('max_skew_mm')
        limit = value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
        result = {'pos_net': positive, 'neg_net': negative, 'paths': measured,
                  'max_skew_mm': limit, 'status': 'UNKNOWN'}
        if positive == negative or limit is None:
            result['reason'] = 'distinct pair nets and a finite non-negative skew limit are required'
        elif all(path['length_mm'] is not None for path in measured.values()):
            skew = abs(measured[positive]['length_mm'] - measured[negative]['length_mm'])
            alignment = sum(path['pad_alignment_bound_mm'] for path in measured.values())
            minimum, maximum = max(0., skew - alignment), skew + alignment
            status = 'PASS' if maximum <= limit else 'FAIL' if minimum > limit else 'UNKNOWN'
            result.update(skew_mm=skew, skew_interval_mm=[minimum, maximum], status=status)
        pairs.append(result)
    return {'passed': bool(pairs) and all(pair['status'] == 'PASS' for pair in pairs), 'pairs': pairs}
