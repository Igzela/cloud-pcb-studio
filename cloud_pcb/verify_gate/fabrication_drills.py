"""Cross-check native solver Excellon holes and slots against the KiCad board."""

import math
import warnings
from collections import Counter, defaultdict

from gerbonara.excellon import ExcellonFile
from gerbonara.graphic_objects import Flash, Line
from gerbonara.utils import MM
from kiutils.items.brditems import Via

from cloud_pcb.general_solver.geometry import drill_centerline


def read_fabrication_drill_file(path):
    """Require complete native XNC framing, including on a zero-hole layer."""
    data = path.read_text()
    commands = [line.partition(';')[0].strip() for line in data.splitlines()]
    commands = [command for command in commands if command]
    if (len(commands) < 4 or commands[0] != 'M48' or '%' not in commands
            or commands[-1] != 'M30' or not commands[1].startswith(('METRIC', 'INCH'))):
        raise ValueError(f'{path.name}: missing native XNC header or end-of-program command')
    with warnings.catch_warnings():
        warnings.simplefilter('error', SyntaxWarning)
        try:
            return ExcellonFile.from_string(data, filename=path)
        except (SyntaxError, SyntaxWarning) as exc:
            raise ValueError(f'{path.name}: {exc}') from exc


def _drill_key(diameter, start, end):
    values = (diameter, *start, *end)
    if not all(math.isfinite(value) for value in values) or diameter <= 0:
        raise ValueError('non-finite drill geometry or non-positive diameter')
    # Native XNC stores 5 decimal places in mm. Compare physical tool and
    # coordinates at that resolution, ignoring slot direction and file order.
    width, x1, y1, x2, y2 = (round(float(f'{value:.5f}') * 100000) for value in values)
    if width == 0:
        raise ValueError('drill diameter below the XNC grid')
    return width, *sorted(((x1, y1), (x2, y2)))


def _describe(key):
    diameter, start, end = key
    return {'diameter_mm': diameter / 100000,
            'start_mm': [value / 100000 for value in start],
            'end_mm': [value / 100000 for value in end]}


def audit_fabrication_drills(board, plated, unplated, *, y_flip_mm):
    """Measure both drill files separately and reconcile their exact geometry."""
    if not math.isfinite(y_flip_mm):
        raise ValueError('non-finite board coordinate transform')
    expected = {True: defaultdict(list), False: defaultdict(list)}
    issues = []

    def add_expected(is_plated, source, geometry):
        diameter, start, end = geometry
        try:
            key = _drill_key(diameter, (start[0], y_flip_mm - start[1]), (end[0], y_flip_mm - end[1]))
        except ValueError as exc:
            issues.append({'source': source, 'reason': str(exc)})
        else:
            expected[is_plated][key].append(source)

    for footprint in board.footprints:
        for pad in footprint.pads:
            if pad.type not in ('thru_hole', 'np_thru_hole') and pad.drill is None:
                continue
            source = {'refdes': footprint.properties.get('Reference'), 'pad': pad.number}
            if pad.type not in ('thru_hole', 'np_thru_hole') or pad.drill is None:
                issues.append({'source': source, 'reason': 'missing or unsupported pad drill'})
                continue
            add_expected(pad.type == 'thru_hole', source, drill_centerline(footprint, pad))
    for item in board.traceItems:
        if isinstance(item, Via):
            point = (float(item.position.X), float(item.position.Y))
            source = {'via_xy_mm': list(point), 'net_number': item.net}
            if set(item.layers) != {'F.Cu', 'B.Cu'}:
                issues.append({'source': source, 'reason': 'non-through via requires a separate drill group'})
                continue
            add_expected(True, source, (float(item.drill), point, point))

    groups = {}
    for label, drill_file, is_plated in (('PTH', plated, True), ('NPTH', unplated, False)):
        actual = Counter()
        for index, obj in enumerate(drill_file.objects):
            try:
                if isinstance(obj, Flash):
                    start = end = (MM(obj.x, obj.unit), MM(obj.y, obj.unit))
                elif isinstance(obj, Line):
                    start = (MM(obj.x1, obj.unit), MM(obj.y1, obj.unit))
                    end = (MM(obj.x2, obj.unit), MM(obj.y2, obj.unit))
                else:
                    raise ValueError('unsupported Excellon drill object')
                tool = obj.aperture
                if tool.plated is not None and tool.plated != is_plated:
                    raise ValueError('explicit plating disagrees with drill file group')
                actual[_drill_key(MM(tool.diameter, tool.unit), start, end)] += 1
            except (AttributeError, TypeError, ValueError) as exc:
                issues.append({'source': {'file': label, 'object': index}, 'reason': str(exc)})

        expected_counts = Counter({key: len(sources) for key, sources in expected[is_plated].items()})
        missing = [{'drill': _describe(key), 'count': count, 'sources': expected[is_plated][key]}
                   for key, count in sorted((expected_counts - actual).items())]
        unexpected = [{'drill': _describe(key), 'count': count}
                      for key, count in sorted((actual - expected_counts).items())]
        groups[label] = {'expected_count': expected_counts.total(), 'observed_count': len(drill_file.objects),
                         'missing': missing, 'unexpected': unexpected}
    passed = not issues and all(not group['missing'] and not group['unexpected'] for group in groups.values())
    return {'passed': passed, 'groups': groups, 'issues': issues}
