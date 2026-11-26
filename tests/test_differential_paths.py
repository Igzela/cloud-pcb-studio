"""Independent endpoint length controls for a small, fully named pair."""

from kiutils.board import Board
from kiutils.footprint import Footprint, Pad
from kiutils.items.brditems import Segment, Stackup, StackupLayer, Via
from kiutils.items.common import Net, Position
import pytest

from scripts.verify_differential_paths import measure_differential_paths


def point(xy):
    return Position(X=xy[0], Y=xy[1])


def segment(start, end, *, net=1, layer='F.Cu'):
    return Segment(start=point(start), end=point(end), layer=layer, width=.2, net=net)


@pytest.fixture
def pair_board():
    board = Board.create_new()
    board.nets = [Net(0, ''), Net(1, 'signal+'), Net(2, 'signal-')]
    for ref, xy, net in (('ap', (0, 1), 1), ('bp', (10, 1), 1), ('an', (0, 4), 2), ('bn', (10.1, 4), 2)):
        fp = Footprint(position=point(xy), properties={'Reference': ref})
        fp.pads.append(Pad(number='1', type='smd', shape='rect', position=point((0, 0)), size=point((.5, .5)),
                           net=board.nets[net], layers=['F.Cu']))
        board.footprints.append(fp)
    board.traceItems = [segment((0, 1), (10, 1)), segment((0, 4), (10.1, 4), net=2)]
    spec = {'nets': {'signal+': [['ap', '1'], ['bp', '1']], 'signal-': [['an', '1'], ['bn', '1']]},
            'design_requirements': {'differential_pairs': [dict(pos_net='signal+', neg_net='signal-', max_skew_mm=.2)]}}
    return board, spec


def test_endpoint_distance_excludes_a_joined_same_net_stub(pair_board):
    board, spec = pair_board
    board.traceItems.append(segment((5, 1), (5, 3)))
    report = measure_differential_paths(board, spec)
    pair = report['pairs'][0]
    assert report['passed']
    assert pair['paths']['signal+']['length_mm'] == 10.
    assert pair['skew_mm'] == pytest.approx(.1)


def test_stackup_adds_via_length_only_when_layers_connect(pair_board):
    board, spec = pair_board
    board.footprints[1].pads[0].layers = ['B.Cu']
    board.traceItems = [segment((0, 1), (5, 1)), segment((5, 1), (10, 1), layer='B.Cu'), board.traceItems[1]]
    disconnected = measure_differential_paths(board, spec)['pairs'][0]
    assert disconnected['status'] == 'UNKNOWN'
    assert 'connected path' in disconnected['paths']['signal+']['reason']

    board.traceItems.append(Via(position=point((5, 1)), size=.5, drill=.25, layers=['F.Cu', 'B.Cu'], net=1))
    assert measure_differential_paths(board, spec)['pairs'][0]['status'] == 'UNKNOWN'
    board.setup.stackup = Stackup(layers=[StackupLayer(name='F.Cu', type='copper', thickness=.035),
                                         StackupLayer(name='dielectric 1', type='core', thickness=1.),
                                         StackupLayer(name='B.Cu', type='copper', thickness=.035)])
    pair = measure_differential_paths(board, spec)['pairs'][0]
    assert pair['paths']['signal+']['length_mm'] == pytest.approx(11.035)
    assert pair['status'] == 'FAIL'


def test_loop_has_no_unique_electrical_path(pair_board):
    board, spec = pair_board
    board.traceItems.extend([segment((0, 1), (5, 3)), segment((5, 3), (10, 1))])
    pair = measure_differential_paths(board, spec)['pairs'][0]
    assert pair['status'] == 'UNKNOWN'
    assert 'multiple centerline paths' in pair['paths']['signal+']['reason']


def test_pad_alignment_uncertainty_spanning_threshold_stays_unknown(pair_board):
    board, spec = pair_board
    board.footprints[0].position.X = .0005
    spec['design_requirements']['differential_pairs'][0]['max_skew_mm'] = .1
    pair = measure_differential_paths(board, spec)['pairs'][0]
    assert pair['skew_interval_mm'] == pytest.approx([.0995, .1005])
    assert pair['status'] == 'UNKNOWN'


def test_missing_pad_geometry_cannot_satisfy_skew_limit(pair_board):
    board, spec = pair_board
    board.footprints[0].pads[0].size.X = 0
    pair = measure_differential_paths(board, spec)['pairs'][0]
    assert pair['status'] == 'UNKNOWN'
    assert 'lacks a supported copper pad shape' in pair['paths']['signal+']['reason']
