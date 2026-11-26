"""Check that explicitly protected KiCad traces and vias survive an edit."""

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from kiutils.board import Board
from kiutils.items.brditems import Segment, Via
from shapely.errors import GEOSException
from shapely.geometry import LineString
from shapely.ops import unary_union


def _routes_by_net(board, protected):
    net_names = {net.number: net.name for net in board.nets}
    if len(net_names) != len(board.nets) or len(set(net_names.values())) != len(net_names):
        raise ValueError('ambiguous PCB net assignments')
    traces = defaultdict(lambda: defaultdict(list))
    vias = defaultdict(Counter)
    issues = defaultdict(list)
    for item in board.traceItems:
        net = net_names.get(item.net)
        if net not in protected:
            continue
        if isinstance(item, Segment):
            start, end = (item.start.X, item.start.Y), (item.end.X, item.end.Y)
            if (not item.layer.endswith('.Cu') or item.width <= 0 or start == end
                    or any(not math.isfinite(n) for n in (*start, *end, item.width))):
                issues[net].append('invalid segment geometry')
                continue
            traces[net][item.layer, item.width].append(LineString([start, end]))
        elif isinstance(item, Via):
            xy = (item.position.X, item.position.Y)
            if (len(set(item.layers)) != 2 or not all(layer.endswith('.Cu') for layer in item.layers)
                    or not 0 < item.drill < item.size or any(not math.isfinite(n) for n in (*xy, item.size, item.drill))):
                issues[net].append('invalid via geometry')
                continue
            key = (*xy, item.size, item.drill, tuple(sorted(item.layers)), item.type or '',
                   bool(item.removeUnusedLayers), bool(item.keepEndLayers))
            vias[net][key] += 1
        else:
            issues[net].append(f'unsupported routing item: {type(item).__name__}')
    return traces, vias, issues


def _via_changes(changes):
    return [dict(xy_mm=[x, y], diameter_mm=size, drill_mm=drill, layers=list(layers),
                 via_type=kind or 'through', remove_unused_layers=remove_unused, keep_end_layers=keep_ends, count=count)
            for (x, y, size, drill, layers, kind, remove_unused, keep_ends), count in sorted(changes.items())]


def compare_protected_routing(before, after, protected):
    """Compare route paths by layer and width, independent of segment splits."""
    protected = set(protected)
    before_traces, before_vias, before_issues = _routes_by_net(before, protected)
    after_traces, after_vias, after_issues = _routes_by_net(after, protected)
    details = {}
    for net in sorted(protected):
        issues = [f'baseline: {issue}' for issue in before_issues[net]]
        issues += [f'candidate: {issue}' for issue in after_issues[net]]
        if not before_traces[net] and not before_vias[net]:
            issues.append('baseline has no modeled route for the protected net')
        if not after_traces[net] and not after_vias[net]:
            issues.append('candidate has no modeled route for the protected net')

        changed = []
        for layer, width in sorted(before_traces[net].keys() | after_traces[net].keys()):
            keys = (layer, width)
            a = unary_union(before_traces[net].get(keys, []))
            b = unary_union(after_traces[net].get(keys, []))
            if not a.equals(b):
                changed.append(dict(layer=layer, width_mm=width, removed_length_mm=a.difference(b).length,
                                    added_length_mm=b.difference(a).length))
        removed = _via_changes(before_vias[net] - after_vias[net])
        added = _via_changes(after_vias[net] - before_vias[net])
        details[net] = dict(passed=not (changed or removed or added or issues), changed_trace_groups=changed,
                            removed_vias=removed, added_vias=added, issues=issues)
    return dict(passed=all(item['passed'] for item in details.values()), protected_nets=details)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--spec', type=Path, required=True, help='Blind specification that declares protected nets')
    args = parser.parse_args()
    try:
        requirements = json.loads(args.spec.read_text())['design_requirements']
        protected = set(requirements.get('high_speed_or_clock_nets', []))
        protected.update(pair[end] for pair in requirements.get('differential_pairs', [])
                         for end in ('pos_net', 'neg_net'))
        report = compare_protected_routing(Board.from_file(str(args.baseline)),
                                           Board.from_file(str(args.candidate)), protected)
    except (GEOSException, KeyError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({'passed': False, 'error': str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
