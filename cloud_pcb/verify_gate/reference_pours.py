"""Compare saved KiCad zone fills in the same board coordinate frame."""

from collections import defaultdict
import math

from shapely.errors import GEOSException
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.validation import explain_validity


def _collect_fills(board, net_name):
    matching = [net for net in board.nets if net.name == net_name]
    if len(matching) != 1:
        return {}, {}, [f'Expected one declared net named {net_name!r}']
    number = matching[0].number
    if sum(net.number == number for net in board.nets) != 1:
        return {}, {}, [f'Ambiguous net number for {net_name!r}']
    pieces = defaultdict(list)
    issues = defaultdict(list)
    for zone in board.zones:
        if zone.keepoutSettings or (zone.net != number and zone.netName != net_name):
            continue
        declared = set(zone.layers)
        name = zone.name or '<unnamed zone>'
        if zone.net != number or zone.netName != net_name:
            return {}, {}, [f'Inconsistent net assignment for zone {name!r}']
        reported = set()
        for index, fill in enumerate(zone.filledPolygons):
            if (not isinstance(fill.layer, str) or not fill.layer.endswith('.Cu')
                    or (fill.layer not in declared and '*.Cu' not in declared)):
                return {}, {}, [f'Unexpected filled layer {fill.layer!r} for zone {name!r}']
            reported.add(fill.layer)
            coordinates = [(point.X, point.Y) for point in fill.coordinates]
            if (len(coordinates) < 3 or not all(
                    isinstance(value, (int, float)) and math.isfinite(value)
                    for xy in coordinates for value in xy)):
                issues[fill.layer].append(f'Invalid coordinates for zone {name!r}')
                continue
            polygon = Polygon(coordinates)
            if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
                issues[fill.layer].append(
                    f'Invalid filled polygon {index} for zone {name!r}: {explain_validity(polygon)}')
                continue
            pieces[fill.layer].append(polygon)
        for layer in declared - {'*.Cu'} - reported:
            issues[layer].append(f'Missing saved fill for zone {name!r}')
    return pieces, issues, []


def _regions(geometry):
    if geometry.is_empty:
        return []
    pieces = [geometry] if geometry.geom_type == 'Polygon' else list(geometry.geoms)
    sorted_pieces = sorted(pieces, key=lambda shape: (-shape.area, shape.bounds))
    return [
        {'area_mm2': round(shape.area, 6), 'bounds_xyxy_mm': list(shape.bounds)}
        for shape in sorted_pieces[:3]
    ]


def compare_reference_pours(baseline, candidate, net_name, layers=()):
    """Measure union area and local area changes without certifying return paths."""
    old, old_issues, invalid_old = _collect_fills(baseline, net_name)
    new, new_issues, invalid_new = _collect_fills(candidate, net_name)
    reported = {}
    general_issues = [f'Baseline: {issue}' for issue in invalid_old]
    general_issues += [f'Candidate: {issue}' for issue in invalid_new]
    selected = sorted(set(layers) or old.keys() | new.keys() | old_issues.keys() | new_issues.keys())
    if not selected and not general_issues:
        general_issues.append('No saved copper fills for the requested net')
    for layer in selected:
        issues = [f'Baseline: {issue}' for issue in old_issues.get(layer, [])]
        issues += [f'Candidate: {issue}' for issue in new_issues.get(layer, [])]
        if layer not in old or layer not in new:
            issues.append('Saved fills are required on both boards')
        if issues:
            reported[layer] = {'status': 'UNKNOWN', 'issues': issues}
            continue
        try:
            before, after = unary_union(old[layer]), unary_union(new[layer])
            added, removed = after.difference(before), before.difference(after)
            reported[layer] = dict(
                status='MEASURED', baseline_area_mm2=round(before.area, 6), candidate_area_mm2=round(after.area, 6),
                change_mm2=round(after.area - before.area, 6),
                added_area_mm2=round(added.area, 6), removed_area_mm2=round(removed.area, 6),
                largest_added_regions=_regions(added), largest_removed_regions=_regions(removed),
            )
        except GEOSException as exc:
            reported[layer] = {'status': 'UNKNOWN', 'issues': [str(exc)]}
    measured = not general_issues and all(item['status'] == 'MEASURED' for item in reported.values())
    return {'status': 'MEASURED' if measured else 'UNKNOWN', 'net': net_name,
            'layers': reported, 'issues': general_issues}
