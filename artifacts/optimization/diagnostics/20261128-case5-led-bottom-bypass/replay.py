from hashlib import sha256
from math import dist
from pathlib import Path
import json

import networkx as nx
from kiutils.board import Board
from kiutils.items.brditems import Segment
from shapely.geometry import LineString

import screen

out = Path(__file__).resolve().parent
net = '/LED0'
layer = 'B.Cu'
width = .16
start = (3.802, 2.769)
retained_via = (30.4372, 9.151)
removed_via = (18.0625, 5.2706)
board = screen.load()
number = next(n.number for n in board.nets if n.name == net)
graph = nx.Graph()
for item in board.traceItems:
    if item.net == number and isinstance(item, Segment):
        graph.add_edge((item.start.X, item.start.Y), (item.end.X, item.end.Y), trace=item)
previous = nx.shortest_path(graph, start, retained_via)
old_segments = []
for first, last in zip(previous, previous[1:]):
    track = graph[first][last]['trace']
    old_segments.append(dict(layer=track.layer, width_mm=track.width, start_xy_mm=first, end_xy_mm=last))
assert [row['layer'] for row in old_segments] == ['In2.Cu'] * 8 + ['B.Cu'] * 19
source = screen.corrected / 'reconstructed-whole-case5.kicad_pcb'

def tagged_audit(test_board, scope, case):
    test_board.zones = []
    islands = screen.power_islands(test_board, screen.outline, screen.spec, screen.keepouts)
    _, stats = screen._build_4layer_pours_and_stitching(test_board, screen.outline, keepouts=screen.keepouts,
                                                      islands=islands, inner_antipad_clearance=.16)
    destination = out / f'{case}.kicad_pcb'
    test_board.to_file(str(destination))
    snapshot = Board.from_file(str(destination))
    results = screen.audit_general_board(snapshot, screen.spec, screen.report['placed_positions'], screen.keepouts)
    diff = screen.measure_differential_paths(snapshot, screen.spec)
    before = screen.measure_differential_paths(screen.reference, screen.spec)
    def routed_names(saved_board):
        used = {item.net for item in saved_board.traceItems}
        return {item.name for item in saved_board.nets if item.number in used and item.name != net}
    all_unchanged = routed_names(screen.reference) | routed_names(snapshot)
    comparison = screen.compare_protected_routing(screen.reference, snapshot, all_unchanged)
    result = {'scope': scope, 'source': str(source), 'source_sha256': sha256(source.read_bytes()).hexdigest(),
              'board_sha256': sha256(destination.read_bytes()).hexdigest(), 'candidate_raw_pours': stats,
              'independent_lvs_opens': results['lvs_opens'], 'independent_lvs_shorts': results['lvs_shorts'],
              'nonthermal_via_in_pad': results['via_in_pad_violations'],
              'all_non_target_copper': comparison, 'differential_paths_unchanged': diff == before}
    assert comparison['passed']
    assert diff == before
    return result

screen.cut(board, net, previous, {removed_via})
negative = tagged_audit(board, 'Removal-only control; intentional /LED0 open.', 'cut-control')
assert [row['net'] for row in negative['independent_lvs_opens']] == [net]
assert not negative['independent_lvs_shorts']
(out / 'cut-control-audit.json').write_text(json.dumps(negative, indent=2, allow_nan=False) + '\n')
board.zones = []
path = screen.route(board, net, layer, start, retained_via, width,
                    pad_exclusions=[('LAN1', '11')], via_exclusions={retained_via})
assert path is not None
pads, obstacles = screen._board_obstacles(board, screen.keepouts)
owner = {id(pad) for _, pad, name, _ in pads[('LAN1', '11')] if name == net}
path_geom = LineString(path)
minimum = min((path_geom.distance(o.geometry) - width/2 - o.extra_radius_mm, str(o.net), o.geometry.wkt)
              for o in obstacles[layer] if o.net != net and o.drill_owner not in owner
              and not (o.geometry.geom_type == 'Point' and (o.geometry.x, o.geometry.y) == retained_via))
assert minimum[0] >= .125
instruction = dict(net=net, removed_segments=old_segments, removed_via_xy_mm=removed_via,
                   retained_via_xy_mm=retained_via, layer=layer, width_mm=width, vertices_xy_mm=path,
                   original_length_mm=sum(dist(a,b) for a,b in zip(previous,previous[1:])),
                   candidate_length_mm=path_geom.length, required_clearance_mm=.125,
                   minimum_clearance_mm=minimum[0], closest_foreign_net=minimum[1],
                   closest_foreign_primitive=minimum[2],
                   minimum_board_edge_gap_mm=path_geom.distance(screen.outline.boundary) - width/2)
(out / 'route-replacement.json').write_text(json.dumps(instruction, indent=2, allow_nan=False) + '\n')
screen.add(board, net, layer, width, path)
positive = tagged_audit(board, 'Analytical exact copper reconstruction, no router execution.', 'reconstructed-whole-case5')
assert not positive['independent_lvs_opens']
assert not positive['independent_lvs_shorts']
assert not positive['nonthermal_via_in_pad']
positive['route_replacement'] = instruction
(out / 'independent-audit.json').write_text(json.dumps(positive, indent=2, allow_nan=False) + '\n')
print(json.dumps(positive['candidate_raw_pours'], indent=2))
