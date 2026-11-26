"""Fabrication evidence must match the physical drill plan of the board."""

from gerbonara.apertures import ExcellonTool
from gerbonara.excellon import ExcellonFile
from gerbonara.graphic_objects import Flash, Line
from gerbonara.utils import MM
from kiutils.board import Board
from kiutils.footprint import DrillDefinition, Footprint, Pad
from kiutils.items.brditems import Via
from kiutils.items.common import Position
import pytest

from cloud_pcb.verify_gate.fabrication_drills import audit_fabrication_drills, read_fabrication_drill_file


@pytest.fixture
def exported_drills(tmp_path):
    board = Board.create_new()
    footprint = Footprint(position=Position(X=10, Y=6, angle=90), properties={'Reference': 'mount'})
    footprint.pads = [Pad(
        number='1', type='np_thru_hole', position=Position(X=2, Y=1, angle=90),
        drill=DrillDefinition(oval=True, diameter=.4, width=1.4, offset=Position(X=.2, Y=.1)),
    )]
    board.footprints.append(footprint)
    board.traceItems.append(Via(position=Position(X=3, Y=4), drill=.3, layers=['F.Cu', 'B.Cu']))

    # The slot's board center is (11.1, 3.8). Reverse its Excellon path
    # to demonstrate that drill direction is immaterial.
    pth_path = tmp_path / 'PTH.drl'
    npth_path = tmp_path / 'NPTH.drl'
    ExcellonFile(objects=[Flash(3, 16, ExcellonTool(.3, plated=True, unit=MM), unit=MM)]).save(pth_path)
    ExcellonFile(objects=[Line(11.6, 16.2, 10.6, 16.2, ExcellonTool(.4, plated=False, unit=MM), unit=MM)]).save(npth_path)
    return board, read_fabrication_drill_file(pth_path), read_fabrication_drill_file(npth_path)


def test_round_via_and_rotated_offset_slot_match_physical_exports(exported_drills):
    report = audit_fabrication_drills(*exported_drills, y_flip_mm=20)
    assert report['passed'] and not report['issues']
    assert {name: group['observed_count'] for name, group in report['groups'].items()} == {'PTH': 1, 'NPTH': 1}


@pytest.mark.parametrize('invalid', ['', 'M48\nMETRIC\n%\n', 'M48\nMETRIC\n%\nINVALID\nM30\n'])
def test_empty_or_incomplete_file_does_not_substitute_for_valid_zero_hole_export(tmp_path, invalid):
    path = tmp_path / 'NPTH.drl'
    ExcellonFile(objects=[]).save(path)
    assert not read_fabrication_drill_file(path).objects
    path.write_text(invalid)
    with pytest.raises(ValueError, match='NPTH.drl'):
        read_fabrication_drill_file(path)


@pytest.mark.parametrize('change', ['omitted', 'diameter', 'position', 'wrong-group', 'duplicate'])
def test_drill_reconciliation_rejects_fabrication_errors(exported_drills, change):
    board, pth, npth = exported_drills
    if change == 'omitted':
        npth.objects.clear()
    elif change == 'diameter':
        pth.objects[0].aperture = ExcellonTool(.35, plated=True, unit=MM)
    elif change == 'position':
        pth.objects[0].x += .05
    elif change == 'wrong-group':
        pth.objects, npth.objects = npth.objects, pth.objects
    elif change == 'duplicate':
        pth.objects.append(pth.objects[0])

    report = audit_fabrication_drills(board, pth, npth, y_flip_mm=20)
    assert not report['passed']
    if change == 'omitted':
        assert report['groups']['NPTH']['missing'][0]['sources'] == [{'refdes': 'mount', 'pad': '1'}]
    if change in ('diameter', 'position'):
        group = report['groups']['PTH']
        assert group['expected_count'] == group['observed_count'] == 1
        assert len(group['missing']) == len(group['unexpected']) == 1
