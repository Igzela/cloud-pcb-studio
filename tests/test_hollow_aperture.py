"""Raw standard hollow-flash and ordered polarity checks."""
import math

import pytest
from gerbonara.apertures import CircleAperture
from gerbonara.excellon import ExcellonFile
from gerbonara.graphic_objects import Flash
from shapely.geometry import Point

from cloud_pcb.verify_gate.gerber_drc import (
    _gerber_layer_union,
    _gerbonara_obj_to_shapely,
    audit_annular_geometry,
)


HOLLOW = ["C,0.60X0.35", "R,0.80X0.60X0.35", "O,0.95X0.60X0.35", "O,0.60X0.95X0.35"]
FLASH_HOLLOW = "D10*\nX500000Y500000D03*"
POUR_REGION = """G36*
X400000Y400000D02*
X600000Y400000D01*
X600000Y600000D01*
X400000Y600000D01*
X400000Y400000D01*
G37*"""
FLASH_RESTORE = "D12*\nX525000Y500000D03*"
PLATED_DRILL = """M48
METRIC
;TYPE=PLATED
T01C0.30000
%
T01
G05
X5.00000Y5.00000
M30
"""


def artwork(tmp_path, hollow, *commands):
    gerber = tmp_path / "copper.gbr"
    gerber.write_text("\n".join([
        "%FSLAX45Y45*%", "%MOMM*%", "%IPPOS*%", "%LPD*%", "G75*",
        f"%ADD10{hollow}*%", "%ADD12C,0.08*%",
        *commands, "M02*", "",
    ]))
    return _gerber_layer_union(gerber)[0]


@pytest.mark.parametrize("hollow", HOLLOW)
def test_hollow_dark_land_does_not_supply_copper_to_smaller_plated_drill(tmp_path, hollow):
    copper = artwork(tmp_path, hollow, FLASH_HOLLOW)
    assert not copper.covers(Point(5, 5))
    assert not copper.covers(Point(5.16, 5))
    assert copper.covers(Point(5.25, 5))
    # A .35 mm transparent hole around a .30 mm plated drill leaves a
    # physical radial gap. The old solid-disk conversion could pass it.
    drill = ExcellonFile.from_string(PLATED_DRILL)
    ring, violations = audit_annular_geometry(drill, copper, copper)
    assert ring < .1
    assert {item["layer"] for item in violations} == {"F.Cu", "B.Cu"}


@pytest.mark.parametrize("hollow", HOLLOW)
def test_hole_is_transparent_to_prior_dark_copper_at_both_polarities(tmp_path, hollow):
    dark = artwork(tmp_path, hollow, POUR_REGION, FLASH_HOLLOW)
    assert dark.covers(Point(5, 5))
    assert dark.covers(Point(5.25, 5))
    clear = artwork(tmp_path, hollow, POUR_REGION, "%LPC*%", FLASH_HOLLOW)
    assert clear.covers(Point(5, 5))
    assert not clear.covers(Point(5.25, 5))
    assert clear.covers(Point(5.75, 5))
    restored = artwork(
        tmp_path, hollow, POUR_REGION, "%LPC*%", FLASH_HOLLOW,
        "%LPD*%", FLASH_RESTORE,
    )
    assert restored.covers(Point(5.25, 5))
    assert not restored.covers(Point(4.75, 5))


def test_clear_curve_cap_does_not_leave_optimistic_copper_at_chord_midpoint(tmp_path):
    # At this angle the uninflated 64-gon falls short of the true arc by
    # more than 0.0001 mm. The clear aperture must remove that copper.
    angle = math.pi / 64
    probe = Point(5 + .2999 * math.cos(angle), 5 + .2999 * math.sin(angle))
    clear = artwork(tmp_path, HOLLOW[0], POUR_REGION, "%LPC*%", FLASH_HOLLOW)
    assert not clear.covers(probe)


def test_earlier_clear_hollow_flash_creates_no_dark_center_without_prior_copper(tmp_path):
    clear = artwork(tmp_path, HOLLOW[0], "%LPC*%", FLASH_HOLLOW)
    assert clear.is_empty


def test_synthetic_hollow_flash_handles_none_aperture_unit():
    aperture = CircleAperture(0.60, hole_dia=0.35, unit=None)
    flash = Flash(5.0, 5.0, aperture, unit=None)
    geom = _gerbonara_obj_to_shapely(flash)
    assert not geom.covers(Point(5.0, 5.0))
    assert not geom.covers(Point(5.16, 5.0))
    assert geom.covers(Point(5.25, 5.0))
