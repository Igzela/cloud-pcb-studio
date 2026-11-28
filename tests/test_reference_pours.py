from kiutils.board import Board
from kiutils.items.common import Net, Position
from kiutils.items.zones import FilledPolygon, Zone

from cloud_pcb.verify_gate.reference_pours import compare_reference_pours
from scripts.optimize_pcb import round_reference_pours


def rectangle(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def zone(layer, *polygons, net=1, name='GND'):
    return Zone(
        net=net, netName=name, layers=[layer],
        filledPolygons=[
            FilledPolygon(layer=layer, coordinates=[Position(X=x, Y=y) for x, y in points])
            for points in polygons
        ],
    )


def board(*zones):
    return Board(nets=[Net(number=1, name='GND'), Net(number=2, name='VCC')], zones=list(zones))


def test_equal_area_can_hide_a_local_loss():
    before = board(zone('F.Cu', rectangle(0, 0, 4, 4)))
    after = board(zone('F.Cu', rectangle(1, 0, 5, 4)))
    result = compare_reference_pours(before, after, 'GND')
    assert result['status'] == 'MEASURED'
    front = result['layers']['F.Cu']
    assert front['change_mm2'] == 0
    assert front['added_area_mm2'] == front['removed_area_mm2'] == 4
    assert front['largest_removed_regions'] == [{'area_mm2': 4, 'bounds_xyxy_mm': [0, 0, 1, 4]}]


def test_same_net_overlap_is_unioned_and_other_nets_are_excluded():
    primary = rectangle(0, 0, 2, 2)
    before = board(zone('In1.Cu', primary))
    after = board(zone('In1.Cu', primary, rectangle(1, 0, 3, 2)),
                  zone('In1.Cu', rectangle(10, 0, 12, 2), net=2, name='VCC'))
    layer = compare_reference_pours(before, after, 'GND')['layers']['In1.Cu']
    assert layer['baseline_area_mm2'] == 4
    assert layer['candidate_area_mm2'] == 6
    assert layer['added_area_mm2'] == 2


def test_unfilled_or_invalid_geometry_is_unknown():
    before = board(zone('In1.Cu', rectangle(0, 0, 4, 4)), zone('F.Cu', rectangle(0, 0, 4, 4)))
    outside = [(0, 0), (4, 4), (0, 4), (4, 0)]
    after = board(zone('In1.Cu'), zone('F.Cu', outside))
    report = compare_reference_pours(before, after, 'GND')
    assert report['status'] == 'UNKNOWN'
    assert all(content['status'] == 'UNKNOWN' for content in report['layers'].values())
    assert 'Invalid filled polygon' in report['layers']['F.Cu']['issues'][0]


def test_selecting_a_layer_preserves_measurements_of_otherwise_partial_boards():
    piece = rectangle(0, 0, 4, 4)
    before = board(zone('F.Cu', piece), zone('In1.Cu', piece))
    after = board(zone('F.Cu', piece))
    assert compare_reference_pours(before, after, 'GND')['status'] == 'UNKNOWN'
    selected = compare_reference_pours(before, after, 'GND', layers=['F.Cu'])
    assert selected['status'] == 'MEASURED' and list(selected['layers']) == ['F.Cu']


def test_round_compares_the_baselines_saved_ground_domain(tmp_path):
    paths = [tmp_path / name for name in ('before.kicad_pcb', 'after.kicad_pcb')]
    for path, right in zip(paths, (3, 4), strict=True):
        pcb = Board(version='20240108', generator='pcbnew', nets=[Net(number=1, name='PGND')],
                    zones=[zone('F.Cu', rectangle(0, 0, right, 2), name='PGND')])
        pcb.to_file(str(path))
    report = round_reference_pours(*paths)
    assert report['status'] == 'MEASURED' and report['net'] == 'PGND'
    assert report['layers']['F.Cu']['added_area_mm2'] == 2
    assert round_reference_pours(paths[0], tmp_path / 'missing.kicad_pcb')['status'] == 'UNKNOWN'
