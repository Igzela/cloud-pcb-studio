"""Protected copper is compared independently of routing object serialization."""

from copy import deepcopy

from kiutils.board import Board
from kiutils.items.brditems import Arc, Segment, Via
from kiutils.items.common import Net, Position
import pytest

from scripts.verify_protected_routing import compare_protected_routing


def segment(start, end, *, net=1, layer='F.Cu', width=.2):
    return Segment(start=Position(X=start[0], Y=start[1]), end=Position(X=end[0], Y=end[1]),
                   width=width, net=net, layer=layer)


@pytest.fixture
def protected_board():
    board = Board.create_new()
    board.nets = [Net(0, ''), Net(1, 'clock'), Net(2, 'other')]
    board.traceItems = [segment((0, 0), (4, 4)),
                        Via(position=Position(X=4, Y=4), size=.5, drill=.25, layers=['F.Cu', 'B.Cu'], net=1)]
    return board


def test_collinear_splits_reversal_and_other_net_edits_preserve_protected_geometry(protected_board):
    candidate = deepcopy(protected_board)
    candidate.traceItems = [candidate.traceItems[1], segment((4, 4), (2, 2)), segment((0, 0), (2, 2)),
                            segment((0, 6), (4, 6), net=2)]
    assert compare_protected_routing(protected_board, candidate, {'clock'})['passed']


@pytest.mark.parametrize('change', ('trace_width', 'trace_layer', 'trace_move', 'via_move', 'via_drill', 'via_layer'))
def test_protected_path_width_layer_and_via_edits_are_measured(protected_board, change):
    candidate = deepcopy(protected_board)
    trace, via = candidate.traceItems
    if change == 'trace_width':
        trace.width -= .01
    elif change == 'trace_layer':
        trace.layer = 'B.Cu'
    elif change == 'trace_move':
        trace.end.X += .01
    elif change == 'via_move':
        via.position.X += .01
    elif change == 'via_drill':
        via.drill += .01
    elif change == 'via_layer':
        via.layers[-1] = 'In2.Cu'
    report = compare_protected_routing(protected_board, candidate, {'clock'})
    detail = report['protected_nets']['clock']
    assert not report['passed'] and not detail['issues']
    if change.startswith('via'):
        assert detail['removed_vias'] and detail['added_vias']
    else:
        assert detail['changed_trace_groups']


def test_missing_and_unsupported_route_data_fail_closed(protected_board):
    candidate = deepcopy(protected_board)
    candidate.traceItems.append(Arc(net=1, width=.2))
    report = compare_protected_routing(protected_board, candidate, {'clock', 'other'})
    assert not report['passed']
    assert report['protected_nets']['clock']['issues'] == ['candidate: unsupported routing item: Arc']
    assert len(report['protected_nets']['other']['issues']) == 2
