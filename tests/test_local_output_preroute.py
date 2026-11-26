"""Independent controls for pre-routing short local output decouplers."""

from copy import deepcopy
from math import ceil

import pytest

from cloud_pcb.general_solver.direct_repair import propose_local_output_links
from cloud_pcb.general_solver.electrical import local_output_decoupler_targets
from cloud_pcb.general_solver.run_blind import _build_blind_kicad_board
from test_supply_scope import component, network, pin, rebuild_nets


@pytest.fixture
def local_output_spec():
    bulk = component(
        "bulk", "Capacitor_SMD:C_0805",
        [pin(1, "+3V3", xy=(-.95, 0)), pin(2, "GND", xy=(.95, 0))],
    )
    bulk["value"] = "10uF"
    spec = network([bulk])
    owner, cap, _ = spec["components"]
    owner["pads"][0].update(pin_type="power_out", x_rel_mm=1, y_rel_mm=0, w_mm=.95, h_mm=.3)
    owner["pads"][1]["net"] = "GND"
    owner["pads"][2] = pin(3, "+3V3", "power_in", "VDD", (-1, -.2))
    cap["pads"][1]["net"] = "GND"
    cap["body_bbox_mm"] = [-.8, -.4, .8, .4]
    cap["value"] = "100nF"
    spec["design_requirements"]["power_rails"] = [{"net": "GND", "nominal_v": 0}]
    rebuild_nets(spec)
    return spec


def _targets(spec):
    return local_output_decoupler_targets(spec, {"GND", "+3V3"})


def test_exact_typed_output_and_shunt_cap_prove_one_target(local_output_spec):
    original = deepcopy(local_output_spec)
    expected = {"tank": {"net": "opaque", "owner_ref": "owner", "owner_pin": "1", "cap_pin": "1"}}
    assert _targets(local_output_spec) == expected
    assert local_output_spec == original


@pytest.mark.parametrize(
    ("pin_type", "pin_function", "cap_side"),
    [("power_in", "AVDD10", "top"), ("power_out", "VDD_SW", "top"), ("power_out", "AVDD10", "bottom")],
    ids=["input-pin", "switch-node", "opposite-side"],
)
def test_local_output_requires_correct_pin_semantics_and_side(local_output_spec, pin_type, pin_function, cap_side):
    owner, cap, _ = local_output_spec["components"]
    owner["pads"][0].update(pin_type=pin_type, pin_function=pin_function)
    cap["side"] = cap_side
    assert not _targets(local_output_spec)


@pytest.mark.parametrize(
    "requirements",
    [
        {"high_speed_or_clock_nets": ["opaque"]},
        {"differential_pairs": [{"pos_net": "other", "neg_net": "opaque"}]},
    ],
    ids=["clock", "differential-leg"],
)
def test_explicit_signal_rules_override_local_preroute(local_output_spec, requirements):
    local_output_spec["design_requirements"].update(requirements)
    assert not _targets(local_output_spec)


def test_ambiguous_netlist_has_no_target(local_output_spec):
    local_output_spec["components"][2]["pads"][0]["net"] = "opaque"
    assert not _targets(local_output_spec)


def test_duplicate_pad_labels_have_no_target(local_output_spec):
    local_output_spec["components"][1]["pads"][1]["num"] = "1"
    rebuild_nets(local_output_spec)
    assert not _targets(local_output_spec)


def test_paste_only_pad_has_no_target(local_output_spec):
    local_output_spec["components"][1]["pads"][0]["layers"] = ["F.Paste"]
    assert not _targets(local_output_spec)


def test_preroute_preserves_netclass_and_produces_a_complete_octilinear_link(local_output_spec):
    poses = {'owner': (10., 10., 0.), 'tank': (14., 13., 0.), 'bulk': (20., 20., 0.)}
    board, classes, mapping, keepouts, outline = _build_blind_kicad_board(local_output_spec, poses)
    links = propose_local_output_links(board, outline, keepouts, classes, mapping, {}, _targets(local_output_spec))
    assert 2 <= len(links) <= 3
    assert links[0].start_xy == (11., 10.)
    assert links[0].end_xy[0] > 11.5 and links[0].end_xy[1] == 10.
    assert links[-1].end_xy == (13.25, 13.)
    assert all(a.end_xy == b.start_xy for a, b in zip(links, links[1:]))
    for link in links:
        assert (link.net, link.layer) == ('opaque', 'F.Cu')
        assert link.width_mm == ceil(1000 * classes['DEFAULT'].trace_width_mm) / 1000
        dx = abs(link.end_xy[0] - link.start_xy[0])
        dy = abs(link.end_xy[1] - link.start_xy[1])
        assert not dx or not dy or dx == pytest.approx(dy)
    assert not board.traceItems


def test_preroute_rejects_a_link_whose_corridor_is_blocked(local_output_spec):
    poses = {'owner': (10., 10., 0.), 'tank': (14., 13., 0.), 'bulk': (20., 20., 0.)}
    board, classes, mapping, keepouts, outline = _build_blind_kicad_board(local_output_spec, poses)
    keepouts.append(dict(x_min=11.6, y_min=8., x_max=12., y_max=15., layers=['F.Cu']))
    assert not propose_local_output_links(board, outline, keepouts, classes, mapping, {}, _targets(local_output_spec))
