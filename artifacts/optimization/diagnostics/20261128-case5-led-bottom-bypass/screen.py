from math import copysign, dist, hypot
from itertools import product
from heapq import heappush, heappop
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
import json
from kiutils.board import Board
from kiutils.items.brditems import Via, Segment
from kiutils.items.common import Position
from shapely import intersects_xy
from shapely.geometry import LineString, Polygon, Point
from shapely.ops import unary_union
from scipy.ndimage import label
import numpy as np
import networkx as nx
from cloud_pcb.general_solver.direct_repair import _board_obstacles
from cloud_pcb.general_solver.run_blind import _rounded_rect_points, _build_4layer_pours_and_stitching
from cloud_pcb.general_solver.power import power_islands
from cloud_pcb.verify_gate.gerber_drc import audit_general_board
from cloud_pcb.verify_gate.differential_paths import measure_differential_paths
from scripts.verify_protected_routing import compare_protected_routing

original = Path('artifacts/optimization/runs/20261122T134458373666Z-exact-geometry-and-micro-escape/case5')
corrected = Path('artifacts/optimization/diagnostics/20261127-case5-track-width-and-fill/width-corrected')
spec = json.loads((original / 'effective_spec.json').read_text())
report = json.loads((original / 'blind_run_report.json').read_text())
reference = Board.from_file(str(corrected / 'reconstructed-whole-case5.kicad_pcb'))
protected = json.loads((corrected / 'independent-audit.json').read_text())['protected_geometry']['protected_nets']
outline_data = spec['board_outline_mm']
outline = Polygon(outline_data.get('vertices_mm') or _rounded_rect_points(outline_data['width'], outline_data['height'], outline_data.get('corner_radius_mm', 0)))
keepouts = report['keepouts']

def load():
    b = Board.from_file(str(corrected / 'reconstructed-whole-case5.kicad_pcb'))
    b.zones = []
    return b

def route(board, net, layer, source, dest, width, pad_exclusions=(), via_exclusions=()):
    pads, obstacles = _board_obstacles(board, keepouts)
    ignore = {id(pad) for key in pad_exclusions for _, pad, name, layers in pads[key] if name == net}
    clearance, margin = .125, .002
    safe_area = outline.buffer(-.5 - width / 2 - margin)
    step = .025
    xs = np.arange(0., 51.5, step)
    ys = np.arange(0., 20.2, step)
    xx, yy = np.meshgrid(xs, ys, indexing='ij')
    territory = intersects_xy(safe_area, xx, yy)
    pos = lambda node: (round(float(xs[node[0]]), 4), round(float(ys[node[1]]), 4))
    neighbours = [(di, dj, step * hypot(di, dj)) for di, dj in product((-1, 0, 1), repeat=2) if di or dj]
    obstacles = [
        item for item in obstacles[layer]
        if item.net != net and item.drill_owner not in ignore
        and not (item.geometry.geom_type == 'Point' and (item.geometry.x, item.geometry.y) in via_exclusions)
    ]
    forbidden = unary_union([item.geometry.buffer(width/2 + clearance + margin + item.extra_radius_mm, quad_segs=32) for item in obstacles])
    mask = territory & ~intersects_xy(forbidden, xx, yy)

    def legs(first, last):
        a, b = first
        x, y = last
        dx, dy = x - a, y - b
        diagonal = min(abs(dx), abs(dy))
        if diagonal < 1e-8 or abs(abs(dx) - abs(dy)) < 1e-8:
            yield (last,)
            return
        p = (round(a + copysign(diagonal, dx), 4), round(b + copysign(diagonal, dy), 4))
        q = (round(x - copysign(diagonal, dx), 4), round(y - copysign(diagonal, dy), 4))
        if p != first and p != last:
            yield p, last
        if q != first and q != last and p != q:
            yield q, last

    def valid(first, tail):
        pts = (first,) + tuple(tail)
        seg = Point(first) if all(p == first for p in pts) else LineString(pts)
        return safe_area.covers(seg) and not forbidden.intersects(seg)

    def anchors(point):
        a = np.argmin(abs(xs - point[0]))
        b = np.argmin(abs(ys - point[1]))
        return {
            (x, y) for x, y in product(range(a - 4, a + 5), range(b - 4, b + 5))
            if mask[x, y] and any(valid(point, t) for t in legs(point, pos((x, y))))
        }

    starts, ends = anchors(source), anchors(dest)
    labels, _ = label(mask)
    shared = ({labels[i] for i in starts} & {labels[i] for i in ends}) - {0}
    print('anchors', net, layer, str(source), str(dest), len(starts), len(ends), 'shared four-neighbour regions', len(shared), flush=True)
    if not shared:
        return None
    work = sorted((dist(source, pos(n)) + dist(dest, pos(n)), dist(source, pos(n)), n) for n in starts)
    distances = {n: v for _, v, n in work}
    parents = {n: None for n in starts}
    while work:
        _, distance, node = heappop(work)
        if distance != distances[node]:
            continue
        if node in ends:
            break
        x, y = node
        for di, dj, delta in neighbours:
            nx, ny = x + di, y + dj
            if not (0 <= nx < xs.size and 0 <= ny < ys.size) or not (mask[nx, ny] and mask[x, ny] and mask[nx, y]):
                continue
            other = (nx, ny)
            cost = distance + delta
            if cost >= distances.get(other, 1e6):
                continue
            distances[other] = cost
            parents[other] = node
            heappush(work, (cost + dist(dest, pos(other)), cost, other))
    else:
        return None
    trace = [dest]
    while node is not None:
        trace.append(pos(node))
        node = parents[node]
    trace.append(source)
    trace.reverse()
    shortened = [trace[0]]
    i = 0
    while i + 1 < len(trace):
        j = len(trace) - 1
        while j > i:
            path = next((t for t in legs(trace[i], trace[j]) if valid(trace[i], t)), None)
            if path:
                break
            j -= 1
        if j == i:
            print('unsafe local edge', i, trace[i:i+2], flush=True)
            return None
        for p in path:
            if p != shortened[-1]:
                shortened.append(p)
        i = j

    def heading(a, b):
        sign = lambda x: (x > 1e-8) - (x < -1e-8)
        return sign(b[0] - a[0]), sign(b[1] - a[1])

    condensed = [shortened[0]]
    for point in shortened[1:]:
        if len(condensed) > 1 and heading(*condensed[-2:]) == heading(condensed[-1], point):
            condensed[-1] = point
        else:
            condensed.append(point)
    trace = LineString(condensed)
    assert safe_area.covers(trace)
    minimum = min((trace.distance(o.geometry) - width / 2 - o.extra_radius_mm, str(o.net), o.geometry.wkt) for o in obstacles)
    assert minimum[0] >= clearance
    print('candidate', net, layer, 'vertices', condensed, 'length', trace.length, 'minimum', minimum, 'edge', trace.distance(outline.boundary)-width/2, flush=True)
    return condensed

def cut(board, net, vertices, via_points):
    number = next(n.number for n in board.nets if n.name == net)
    g = nx.Graph()
    for item in board.traceItems:
        if item.net == number and isinstance(item, Segment):
            g.add_edge((item.start.X, item.start.Y), (item.end.X, item.end.Y), item=item)
    assert all(g.degree[p] == 2 for p in vertices[1:-1])
    deleted = [g[a][b]['item'] for a,b in zip(vertices,vertices[1:])]
    deleted += [i for i in board.traceItems if i.net == number and isinstance(i, Via) and (i.position.X, i.position.Y) in via_points]
    assert len(deleted) == len(vertices)-1+len(via_points)
    board.traceItems = [i for i in board.traceItems if not any(i is t for t in deleted)]
    print('cut', len(vertices)-1, 'traces', len(via_points), 'via(s), length', sum(dist(a,b) for a,b in zip(vertices,vertices[1:])), flush=True)

def add(board, net, layer, width, vertices):
    number = next(n.number for n in board.nets if n.name == net)
    for p, q in zip(vertices, vertices[1:]):
        board.traceItems.append(Segment(start=Position(X=p[0], Y=p[1]), end=Position(X=q[0], Y=q[1]), width=width, layer=layer, net=number, tstamp=str(uuid5(NAMESPACE_URL, f"{net}:{layer}:{p}:{q}"))))

def audit(board):
    board.zones = []
    islands = power_islands(board, outline, spec, keepouts)
    _, pours = _build_4layer_pours_and_stitching(board, outline, keepouts=keepouts, islands=islands, inner_antipad_clearance=.16)
    result = audit_general_board(board, spec, report['placed_positions'], keepouts)
    print('audit', pours, 'opens', result['lvs_opens'], 'shorts', result['lvs_shorts'], 'VIP', result['via_in_pad_violations'], 'protected', compare_protected_routing(reference, board, protected)['passed'], 'DIFF same', measure_differential_paths(reference,spec)==measure_differential_paths(board,spec), flush=True)
    board.zones = []

if __name__ == '__main__':
    net = '/LED0'
    left = [(3.802,2.769),(3.8423,2.8093),(11.9123,2.8093),(12.5428,2.1788),(13.652,3.288),(14.7202,3.288),(16.427,3.288),(18.0625,4.9235),(18.0625,5.2706),(20.4296,5.2706)]
    b = load()
    cut(b, net, left, {(18.0625,5.2706)})
    audit(b)
    path = route(b, net, 'B.Cu', left[0], left[-1], .16, pad_exclusions=[('LAN1','11')])
    if path:
        add(b, net, 'B.Cu', .16, path)
        audit(b)
