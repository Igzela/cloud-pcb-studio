"""Test the annular proof through native Gerber and Excellon bytes."""
import pytest

from gerbonara.apertures import CircleAperture, ExcellonTool
from gerbonara.excellon import ExcellonFile
from gerbonara.graphic_objects import Flash, Line
from gerbonara.rs274x import GerberFile
from gerbonara.utils import MM

from cloud_pcb.verify_gate.gerber_drc import _gerber_layer_union, audit_annular_geometry


def audit(tmp_path, objects, *, slot=False, trusted_drills=True):
    gerber = tmp_path / "F_Cu.gbr"
    gerber.write_bytes(GerberFile(objects=objects).write_to_bytes())
    tool = ExcellonTool(.3, plated=True, unit=MM)
    hole = Line(5, 5, 5.05, 5, aperture=tool, unit=MM) if slot else Flash(5, 5, tool, unit=MM)
    drill = tmp_path / "PTH.drl"
    ExcellonFile(objects=[hole]).save(drill)

    lands = []
    copper, _, _ = _gerber_layer_union(gerber, circle_lands=lands)
    pth = ExcellonFile.open(drill)
    witnesses = []
    ring, violations = audit_annular_geometry(
        pth, copper, copper, circle_lands={"F.Cu": lands, "B.Cu": lands},
        source_drills_are_plated=trusted_drills, witness_records=witnesses,
    )
    return ring, violations, witnesses


def solid(x=5., diameter=.5, **kwargs):
    return Flash(x, 5., CircleAperture(diameter, unit=MM), unit=MM, **kwargs)


@pytest.mark.parametrize("offset,diameter", [(0., .5), (.001, .502)])
def test_exact_minimum_even_when_inscribed_polygon_is_smaller(tmp_path, offset, diameter):
    ring, violations, witnesses = audit(tmp_path, [solid(5. + offset, diameter)])
    assert ring == .10
    assert not violations
    assert {row["layer"] for row in witnesses} == {"F.Cu", "B.Cu"}
    assert all(row["polygon_lower_bound_mm"] < .10 for row in witnesses)


@pytest.mark.parametrize("offset,diameter", [(0., .498), (.0011, .502)])
def test_subthreshold_or_offcenter_hole_still_fails(tmp_path, offset, diameter):
    ring, violations, witnesses = audit(tmp_path, [solid(5. + offset, diameter)])
    assert ring < .10
    assert violations
    assert not witnesses


def test_late_clear_disqualifies_earlier_flash_but_not_final_dark_flash(tmp_path):
    dark = solid()
    clear = solid(5.2, .16, polarity_dark=False)
    ring, violations, witnesses = audit(tmp_path, [dark, clear])
    assert ring < .10 and violations and not witnesses
    ring, violations, witnesses = audit(tmp_path, [clear, dark])
    assert ring == .1 and not violations and len(witnesses) == 2


def test_aperture_with_hole_cannot_provide_analytic_land(tmp_path):
    aperture = CircleAperture(.5, hole_dia=.1, unit=MM)
    ring, violations, witnesses = audit(tmp_path, [Flash(5., 5., aperture, unit=MM)])
    assert ring < .10 and violations and not witnesses


@pytest.mark.parametrize("kwargs", [dict(slot=True), dict(trusted_drills=False)])
def test_slot_or_unknown_plating_retains_existing_audit(tmp_path, kwargs):
    ring, violations, witnesses = audit(tmp_path, [solid()], **kwargs)
    assert ring < .10 and violations and not witnesses
