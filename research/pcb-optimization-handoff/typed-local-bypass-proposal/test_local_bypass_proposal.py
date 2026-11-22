from copy import deepcopy
import math

from shapely.affinity import rotate, translate
from shapely.geometry import box

from local_bypass_proposal import move_local_shunt, typed_local_shunts


def synthetic():
    def pad(number, net, *, typ="passive", function=""):
        return {"num": number, "net": net, "pin_type": typ, "pin_function": function,
                "type": "smd", "layers": ["F.Cu"]}

    def capacitor(ref, rail, value):
        return {"refdes": ref, "footprint_name": "Capacitor_SMD:C_0603",
                "value": value, "pads": [pad("1", rail), pad("2", "GND")]}

    owner = {"refdes": "device", "footprint_name": "Package_QFN:generic", "pads": [
        pad("1", "opaque", typ="power_in", function="AVDD_1"),
        pad("2", "noise", typ="input", function="RESET"),
        pad("3", "GND", typ="power_in", function="GND"),
    ]}
    parts = [owner, capacitor("local", "opaque", "0u1"),
             capacitor("bulk", "opaque", "10uF"), capacitor("noise_filter", "noise", "100n"),
             capacitor("ambiguous", "opaque", "100")]
    nets = {}
    for c in parts:
        for p in c["pads"]:
            nets.setdefault(p["net"], []).append([c["refdes"], p["num"]])
    return {"components": parts, "nets": nets}


def test_local_topology_preserves_rails_and_excludes_bulk_and_signal_shunts():
    spec = synthetic()
    original = deepcopy(spec)
    assert typed_local_shunts(spec) == {"local": ("opaque", "device", ("1",))}
    assert spec == original


def test_multiple_active_owners_or_inconsistent_endpoint_evidence_rejects_move():
    spec = synthetic()
    duplicate = deepcopy(spec["components"][0])
    duplicate["refdes"] = "second"
    spec["components"].append(duplicate)
    for p in duplicate["pads"]:
        spec["nets"][p["net"]].append(["second", p["num"]])
    assert not typed_local_shunts(spec)
    original = synthetic()
    original["nets"]["opaque"].remove(["local", "1"])
    assert not typed_local_shunts(original)


def test_legal_move_improves_actual_power_pad_distance_not_just_center_distance():
    original = (22., 16., 0.)
    owner_pad = (10., 10.)
    offset = (-.8, 0.)
    footprint = box(-1., -.65, 1., .65)
    allowed = box(.5, .5, 26.5, 21.5)
    obstacles = (box(8, 8, 11, 12), box(11, 10, 14, 11), box(6, 8, 8, 12))

    def geometry(x, y, angle):
        return translate(rotate(footprint, -angle, origin=(0, 0)), xoff=x, yoff=y)

    def legal(x, y, angle):
        body = geometry(x, y, angle)
        return allowed.contains(body) and all(not body.intersects(o) for o in obstacles)

    assert legal(*original)
    x, y, angle = move_local_shunt(original, offset, [owner_pad], legal)
    assert legal(x, y, angle)
    theta = math.radians(angle)
    actual_distance = math.hypot(x - .8 * math.cos(theta) - 10, y + .8 * math.sin(theta) - 10)
    assert actual_distance < 3.0
    assert actual_distance < math.hypot(22. - .8 - 10, 16. - 10)


def test_no_legal_move_leaves_original_pose():
    original = (22., 16., 90.)
    assert move_local_shunt(original, (-.8, 0.), [(10., 10.)], lambda *_: False) == original
