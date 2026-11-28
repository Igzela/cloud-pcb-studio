"""Independent measurement of serialized routed copper widths."""

import json
import sys

from kiutils.board import Board
from kiutils.items.brditems import Arc, Segment, Via
from kiutils.items.common import Net, Position
import pytest

from cloud_pcb.verify_gate.trace_widths import audit_trace_width_file, audit_trace_widths
from scripts.verify_trace_widths import main


@pytest.fixture
def board():
    fixture = Board.create_new()
    fixture.nets = [Net(0, ''), Net(1, '+3V3')]
    fixture.traceItems = [
        Segment(start=Position(X=33.8537, Y=8.9603),
                end=Position(X=33.6505, Y=8.8816), width=.100, net=1,
                tstamp='2f7309ba-e619-4790-af99-c230486f1b08'),
        Arc(start=Position(X=2, Y=1), mid=Position(X=3, Y=0), end=Position(X=4, Y=1),
            width=.16, net=1, layer='B.Cu'),
        Via(position=Position(X=2, Y=1), size=.42, drill=.2, layers=['F.Cu', 'B.Cu'], net=1),
    ]
    return fixture


@pytest.mark.parametrize(('width', 'status', 'exit_code'), [(.100, 'PASS', 0), (.0136, 'FAIL', 1)])
def test_cli_reports_exported_stub_against_exact_floor(board, tmp_path, monkeypatch, capsys, width, status, exit_code):
    board.traceItems[0].width = width
    path = tmp_path / 'test.kicad_pcb'
    board.to_file(str(path))
    monkeypatch.setattr(sys, 'argv', ['verify_trace_widths', str(path), '--min-width-mm', '.1'])

    assert main() == exit_code
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == status
    assert report['observed_min_width_mm'] == width
    assert report['measured_tracks'] == 2
    assert report['required_min_width_mm'] == .1
    if status == 'FAIL':
        assert report['violations'] == [
            dict(track_index=0, item_type='segment', layer='F.Cu', net='+3V3', net_number=1,
                 coordinates_mm=[[33.8537, 8.9603], [33.6505, 8.8816]], width_mm=.0136)
        ]
    else:
        assert report['passed'] and not report['violations']


def test_track_arc_width_and_midpoint_are_reported(board):
    board.traceItems[1].width = .09
    report = audit_trace_widths(board, .1)
    detail, = report['violations']
    assert report['status'] == 'FAIL'
    assert report['observed_min_width_mm'] == .09
    assert detail['item_type'] == 'arc'
    assert detail['layer'] == 'B.Cu'
    assert detail['coordinates_mm'] == [[2, 1], [3, 0], [4, 1]]


def test_empty_or_invalid_track_data_remains_unknown(board):
    via_only = Board.create_new()
    via_only.traceItems = [board.traceItems[-1]]
    assert audit_trace_widths(via_only, .1)['status'] == 'UNKNOWN'
    assert audit_trace_widths(board, float('inf'))['required_min_width_mm'] is None
    board.traceItems[1].width = float('nan')
    partial = audit_trace_widths(board, .1)
    assert partial['status'] == 'UNKNOWN'
    assert partial['issues']
    assert partial['measured_tracks'] == 1
    board.traceItems[0].width = .0136
    fails = audit_trace_widths(board, .1)
    assert fails['status'] == 'FAIL' and fails['issues']


def test_cli_does_not_report_pass_for_omitted_width(board, tmp_path, monkeypatch, capsys):
    path = tmp_path / 'missing-width.kicad_pcb'
    board.to_file(str(path))
    source = path.read_text()
    assert '(width 0.1)' in source
    path.write_text(source.replace('(width 0.1)', '', 1))
    monkeypatch.setattr(sys, 'argv', ['verify_trace_widths', str(path), '--min-width-mm', '.1'])

    assert main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'UNKNOWN'
    assert report['observed_min_width_mm'] is None
    assert any('width' in issue for issue in report['issues'])
    assert audit_trace_width_file(tmp_path / 'missing', .1)['status'] == 'UNKNOWN'
