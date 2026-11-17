"""Geometric clearance invariants, physical fanout parity and router evidence."""
from copy import deepcopy
import math
import re

import pytest
from kiutils.board import Board
from kiutils.footprint import Footprint, Pad
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Net, Position
from shapely.geometry import Point, Polygon, box

from cloud_pcb.general_solver.routing_guards import conservative_via_guard
from cloud_pcb.general_solver.router_logs import save_router_log
from cloud_pcb.general_solver.run_blind import _add_gnd_fanout_vias
from cloud_pcb.layout_route.dsn_ses_bridge import board_to_dsn
from contracts.schemas import NetClassRule


@pytest.mark.parametrize('x,y,radius', [(0, 0, .420), (-3.127, 7.859, .395),
                                      (19.361, -22.613, .333)])
def test_guard_contains_full_circle_and_recovers_only_square_corners(x, y, radius):
    guard = conservative_via_guard(x, y, radius, ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'])
    polygon = Polygon(guard['polygon_mm'])
    center = Point(x, y)
    # Minimum distance to EVERY boundary edge certifies the continuous disk,
    # rather than relying on an inscribed polygon approximation of a circle.
    assert polygon.contains(center)
    assert center.distance(polygon.boundary) >= radius - 1e-12
    assert polygon.equals(polygon.convex_hull)
    assert polygon.difference(box(x-radius, y-radius, x+radius, y+radius)).area < 1e-12
    assert polygon.area < 4 * radius ** 2 * .84
    assert len(polygon.exterior.coords) == 9
    assert all(abs(v * 1000 - round(v * 1000)) < 1e-8
               for point in guard['polygon_mm'] for v in point)
    for i in range(720):
        angle = math.tau * i / 720
        assert polygon.buffer(1e-12).covers(Point(x+radius*math.cos(angle), y+radius*math.sin(angle)))


@pytest.mark.parametrize('x,y,radius', [(.00049, -.00049, .42001),
                                      (-9.1236, 5.2346, .3952), (2, 3, .0001)])
def test_quantization_never_undercuts_non_grid_protection(x, y, radius):
    polygon = Polygon(conservative_via_guard(x, y, radius, ['B.Cu'])['polygon_mm'])
    assert polygon.contains(Point(x, y))
    assert polygon.boundary.distance(Point(x, y)) >= radius - 1e-12


@pytest.mark.parametrize('x,y,radius', [(float('nan'), 0, .4), (0, float('inf'), .4),
                                      (0, 0, -.1), (0, 0, 0), (True, 0, .4)])
def test_invalid_guard_input_is_rejected(x, y, radius):
    with pytest.raises(ValueError):
        conservative_via_guard(x, y, radius, ['F.Cu'])


def fanout_board():
    board = Board.create_new()
    board.nets = [Net(0, ''), Net(1, 'GND'), Net(2, 'signal')]
    # Ordinary SMD ground and an interior thermal pad exercise both layer sets.
    for name, x, pad_data in [
        ('passive', 8, [(-.8, 0, .6, .8, 1), (.8, 0, .6, .8, 2)]),
        ('chip', 22, [(-2, -2, .4, .6, 2), (-2, 0, .4, .6, 2),
                      (-2, 2, .4, .6, 2), (2, -2, .4, .6, 2),
                      (2, 0, .4, .6, 2), (2, 2, .4, .6, 2), (0, 0, 2, 2, 1)])]:
        fp = Footprint()
        fp.entryName = 'synthetic'
        fp.position = Position(X=x, Y=15)
        fp.properties = {'Reference': name}
        for i, (px, py, w, h, net) in enumerate(pad_data):
            pad = Pad()
            pad.number = str(i+1)
            pad.type, pad.shape = 'smd', 'rect'
            pad.position, pad.size = Position(X=px, Y=py), Position(X=w, Y=h)
            pad.net, pad.layers = board.nets[net], ['F.Cu']
            fp.pads.append(pad)
        board.footprints.append(fp)
    return board


def copper_signature(board):
    result = []
    for item in board.traceItems:
        if isinstance(item, Via):
            result.append(('via', item.position.X, item.position.Y, item.size,
                           item.drill, tuple(item.layers), item.net))
        elif isinstance(item, Segment):
            result.append(('segment', item.start.X, item.start.Y, item.end.X,
                           item.end.Y, item.width, item.layer, item.net))
    return result


def test_fanout_preserves_copper_connectivity_and_thermal_layer_exception():
    legacy, candidate = fanout_board(), fanout_board()
    old_guards, new_guards = [], []
    old = _add_gnd_fanout_vias(legacy, box(0, 0, 40, 30), old_guards)
    new = _add_gnd_fanout_vias(candidate, box(0, 0, 40, 30), new_guards, octagonal_keepouts=True)
    assert copper_signature(legacy) == copper_signature(candidate)
    assert old[:2] == new[:2]
    for key in ('fanout_vias_added', 'gnd_smd_pads_total', 'unvia_gnd_pins'):
        assert old[2][key] == new[2][key]
    assert new[2]['fanout_vias_added'] == 2
    guards = [g for g in new_guards if g.get('kind') == 'fixed_ground_via']
    assert {tuple(g['layers']) for g in guards} == {
        ('F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'), ('B.Cu', 'In1.Cu', 'In2.Cu')}
    assert all(g['dsn_kind'] == 'keepout' for g in guards)
    assert new[2]['via_guard_area_reclaimed_sum_mm2'] > 0
    # The pre-routed stub guards must remain byte-for-byte identical.
    assert [g for g in old_guards if g.get('kind') == 'fixed_ground_stub'] == [
        g for g in new_guards if g.get('kind') == 'fixed_ground_stub']


def test_exported_dsn_polygons_protect_disk_after_coordinate_quantization(tmp_path):
    board = fanout_board()
    guard = conservative_via_guard(12.345, 9.876, .420, ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'])
    original = deepcopy(guard)
    rule = NetClassRule('DEFAULT', .25, .15, .3, .55, 2)
    path = board_to_dsn(board, tmp_path/'guard.dsn', {'DEFAULT': rule},
                        {'GND': 'DEFAULT', 'signal': 'DEFAULT'}, keepouts_mm=[guard])
    matches = re.findall(r'\(keepout "" \(polygon (\S+) 0 ([^()]+)\)\)', path.read_text())
    assert {layer for layer, _ in matches} == set(guard['layers'])
    assert len(matches) == 4
    for _, coordinates in matches:
        values = [int(n) for n in coordinates.split()]
        polygon = Polygon([(values[i]/1000, -values[i+1]/1000) for i in range(0, len(values), 2)])
        center = Point(12.345, 9.876)
        assert polygon.contains(center)
        assert center.distance(polygon.boundary) >= .420 - 1e-12
    assert guard == original


def test_router_log_preserves_actual_failure_and_timeout_evidence(tmp_path):
    path = tmp_path/'router.log'
    result = save_router_log(path, ['router', '--route'], 7, 'partial routing', 'blocked by pins')
    assert result['returncode'] == 7 and not result['timed_out']
    assert 'blocked by pins' in path.read_text()
    result = save_router_log(path, ['router'], -1, b'partial\xff', b'timeout stderr', timed_out=True)
    assert result['returncode'] is None and result['timed_out']
    assert 'partial\ufffd' in path.read_text() and 'timeout stderr' in path.read_text()
    assert 'partial output preserved' in path.read_text()
    assert save_router_log(path, ['router'], 0)['returncode'] == 0
