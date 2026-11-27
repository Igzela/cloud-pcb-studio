"""Measure routed KiCad segment and arc widths against an explicit floor."""

import math
from pathlib import Path

from kiutils.board import Board
from kiutils.items.brditems import Arc, Segment, Via
from kiutils.utils.sexpr import parse_sexp


def _is_finite_number(value):
    return type(value) in (float, int) and math.isfinite(value)


def _unknown_report(min_width_mm):
    minimum = min_width_mm if _is_finite_number(min_width_mm) and min_width_mm > 0 else None
    return dict(status='UNKNOWN', passed=False, required_min_width_mm=minimum,
                observed_min_width_mm=None, measured_tracks=0, violations=[], issues=[])


def audit_trace_widths(board, min_width_mm):
    """Report a width failure when measured and retain incomplete evidence."""
    report = _unknown_report(min_width_mm)
    if report['required_min_width_mm'] is None:
        report['issues'].append('Minimum width must be a finite positive number in millimeters')
        return report

    net_names = {net.number: net.name for net in board.nets}
    widths = []
    for index, track in enumerate(board.traceItems):
        if isinstance(track, Via):
            continue
        if not isinstance(track, (Segment, Arc)):
            report['issues'].append(f'Track {index} has unsupported type {type(track).__name__}')
            continue

        arc = isinstance(track, Arc)
        positions = (track.start, track.mid, track.end) if arc else (track.start, track.end)
        coordinates = [(point.X, point.Y) for point in positions]
        if (not isinstance(track.layer, str) or not track.layer.endswith('.Cu')
                or not _is_finite_number(track.width) or track.width <= 0
                or not all(_is_finite_number(value) for pair in coordinates for value in pair)
                or len(set(coordinates)) != len(coordinates)):
            report['issues'].append(f'Track {index} has invalid width, layer, or coordinates')
            continue

        widths.append(track.width)
        if track.width < min_width_mm:
            net_number = track.net if isinstance(track.net, int) else None
            net_name = track.net if isinstance(track.net, str) else net_names.get(net_number)
            report['violations'].append(dict(track_index=index, item_type='arc' if arc else 'segment',
                                             net=net_name, net_number=net_number, layer=track.layer,
                                             coordinates_mm=coordinates, width_mm=track.width))

    if not widths:
        report['issues'].append('No measurable routed segments or arcs')
    report['measured_tracks'] = len(widths)
    report['observed_min_width_mm'] = min(widths, default=None)
    if report['violations']:
        report['status'] = 'FAIL'
    elif not report['issues']:
        report.update(status='PASS', passed=True)
    return report


def _load_board(path):
    root = parse_sexp(path.read_text(encoding='utf-8'))
    if not isinstance(root, list) or not root or root[0] != 'kicad_pcb':
        raise ValueError('Expected a KiCad PCB S-expression')
    for item in (node for node in root[1:] if isinstance(node, list) and node and node[0] in ('segment', 'arc')):
        required = {'start': 3, 'end': 3, 'width': 2, 'layer': 2}
        if item[0] == 'arc':
            required['mid'] = 3
        fields = [field for field in item[1:] if isinstance(field, list) and field]
        # KiUtils supplies defaults for absent track fields, including width.
        for name, size in required.items():
            matches = [field for field in fields if field[0] == name]
            if len(matches) != 1 or len(matches[0]) != size:
                raise ValueError(f'Expected one explicit {name} field on each routed {item[0]}')
    return Board.from_sexpr(root)


def audit_trace_width_file(path, min_width_mm):
    """Audit serialized track widths without allowing KiUtils field defaults."""
    try:
        board = _load_board(Path(path))
    except Exception as exc:
        # KiUtils raises bare Exception for some malformed file nodes.
        report = _unknown_report(min_width_mm)
        report['issues'].append(str(exc) or 'Invalid KiCad PCB')
        return report
    return audit_trace_widths(board, min_width_mm)
