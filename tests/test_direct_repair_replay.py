"""Require new engine evidence before accepting a physical repair."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from cloud_pcb.general_solver.direct_repair_replay import _fixed_wiring, _unchanged_replay


def test_add_fixed_wire_with_dsn_coordinates_without_editing_rules():
    source = "(pcb board\n  (rule (width 200))\n  (wiring\n  )\n)"
    link = SimpleNamespace(net="local branch", layer="F.Cu", width_mm=.221,
                           start_xy=(2.6, 3.1), end_xy=(4.6, 3.1))
    output = _fixed_wiring(source, [link])
    assert "(rule (width 200))" in output
    assert '(wire (path F.Cu 221 2600 -3100 4600 -3100) (net "local branch") (type fix))' in output
    with pytest.raises(ValueError, match="single empty DSN"):
        _fixed_wiring(output, [link])


def reports():
    control = {"stats": dict(nets=1, pins=3, connections=2, vias=0, unrouted=1, violations=0),
               "clearance_violations": []}
    trial = deepcopy(control)
    trial["stats"]["unrouted"] = 0
    return control, trial


@pytest.mark.parametrize("mutation,reason", [
    ({"unrouted": 1}, "connectivity gain"),
    ({"violations": 1}, "clearance violation"),
    ({"pins": 2}, "pin, connection"),
])
def test_replay_rejects_bad_connectivity_or_changed_input(mutation, reason):
    original, repaired = reports()
    _unchanged_replay(original, original, repaired, 1)
    repaired["stats"].update(mutation)
    with pytest.raises(ValueError, match=reason):
        _unchanged_replay(original, original, repaired, 1)


def test_unchanged_count_cannot_hide_different_violation():
    original, repaired = reports()
    original["stats"]["violations"] = repaired["stats"]["violations"] = 1
    original["clearance_violations"] = [{"layer": "B.Cu", "xy": [5, 4]}]
    repaired["clearance_violations"] = [{"layer": "F.Cu", "xy": [2, 3]}]
    with pytest.raises(ValueError, match="clearance violation"):
        _unchanged_replay(original, original, repaired, 1)
