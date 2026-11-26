"""Rebuild both warm-start boards, then compare copper, LVS and In1 coverage."""
import argparse
import json
from collections import Counter, defaultdict
from hashlib import sha256
from itertools import combinations
from pathlib import Path

from kiutils.board import Board
from shapely.geometry import Polygon
from shapely.ops import unary_union

from cloud_pcb.general_solver.direct_repair import _board_obstacles
from cloud_pcb.general_solver.power import power_islands
from cloud_pcb.general_solver.run_blind import _build_4layer_pours_and_stitching, _rounded_rect_points
from cloud_pcb.layout_route.dsn_ses_bridge import merge_ses_into_board, parse_ses
from cloud_pcb.verify_gate.gerber_drc import audit_general_board, classify_router_violations

root = Path('artifacts/optimization/runs')
case = root / '20261126T012118417153Z-local-output-preroute' / 'case5'
evidence = case / 'dense_escape_candidate' / 'micro_escape'
active = root / '20261122T134458373666Z-exact-geometry-and-micro-escape' / 'case5'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('probe_directory', type=Path, help='Directory containing board.ses and report.json')
scratch = parser.parse_args().probe_directory
spec = json.loads((case / 'effective_spec.json').read_text())
board_report = json.loads((case / 'blind_run_report.json').read_text())
active_report = json.loads((active / 'blind_run_report.json').read_text())
outline = spec['board_outline_mm']
polygon = Polygon(outline.get('vertices_mm') or _rounded_rect_points(
    outline['width'], outline['height'], outline.get('corner_radius_mm', 0),
))
before = evidence / 'board_fastroute.ses'
after = scratch / 'board.ses'

def multiset(items):
    return Counter(tuple(sorted(item.items())) for item in items)

def measured_copper_spacing(board):
    _, obstacles = _board_obstacles(board, ())
    spacing = {}
    for layer, features in obstacles.items():
        by_net = defaultdict(list)
        for feature in features:
            if feature.net:
                geometry = feature.geometry
                if feature.extra_radius_mm:
                    geometry = geometry.buffer(feature.extra_radius_mm)
                by_net[feature.net].append(geometry)
        groups = {net: unary_union(parts) for net, parts in by_net.items()}
        distances = [
            (shape.distance(other_shape), name, other_name)
            for (name, shape), (other_name, other_shape) in combinations(groups.items(), 2)
        ]
        distance, first, second = min(distances)
        spacing[layer] = {'min_mm': round(distance, 4), 'net_pair': [first, second],
                          'pairs_below_0_1249_mm': sum(d < .1249 for d, _, _ in distances)}
    return spacing

def inspect(ses, router_report):
    board = Board.from_file(str(evidence / 'board_unrouted.kicad_pcb'))
    board, _ = merge_ses_into_board(board, ses)
    keepouts = board_report['keepouts']
    islands = power_islands(board, polygon, spec, keepouts)
    _, pours = _build_4layer_pours_and_stitching(
        board, polygon, keepouts=keepouts, islands=islands, inner_antipad_clearance=.16,
    )
    audit = audit_general_board(board, spec, board_report['placed_positions'], keepouts)
    traces = parse_ses(ses)
    result = {
        'ses_sha256': sha256(ses.read_bytes()).hexdigest(),
        'routing': router_report['stats'],
        'real_router_spacing': classify_router_violations(router_report, spec)['real_count'],
        'copper_spacing': measured_copper_spacing(board),
        'foreign_ses_vias': sum(item['net'] != 'GND' for item in traces['vias']),
        'in1_plane_area_mm2': pours['pour_area_mm2'],
        'in1_fill_ratio': pours['fill_ratio'],
        'antipad_cutouts': pours['antipad_cutouts'],
        'lvs_opens': audit['lvs_opens'],
        'lvs_shorts': audit['lvs_shorts'],
        'nonthermal_via_in_pad': audit['via_in_pad_violations'],
    }
    return result, traces

baseline, original = inspect(before, json.loads((evidence / 'fastroute_report.json').read_text()))
candidate, changed = inspect(after, json.loads((scratch / 'report.json').read_text()))
geometry_differences = {}
for kind in ('segments', 'vias'):
    prior = multiset(original[kind])
    next_ = multiset(changed[kind])
    geometry_differences[kind] = {
        'before': prior.total(), 'after': next_.total(),
        'removed': (prior - next_).total(), 'added': (next_ - prior).total(),
    }
payload = {
    'active_in1_fill_ratio': active_report['gnd_pour_stats']['fill_ratio'],
    'active_in1_plane_area_mm2': active_report['gnd_pour_stats']['pour_area_mm2'],
    'warm_start': baseline,
    'optimized': candidate,
    'ses_copper_delta': geometry_differences,
}
print(json.dumps(payload, indent=2))
