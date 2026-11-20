"""Independent tests for dense-escape thermal EPAD detection, touching-pad ground propagation,
and partial-island direct-repair LVS acceptance."""

from kiutils.board import Board
from kiutils.footprint import Footprint, Pad
from kiutils.items.brditems import Via
from kiutils.items.common import Net, Position
from shapely.geometry import box

from cloud_pcb.general_solver.run_blind import (
    _add_gnd_fanout_vias,
    _direct_repair_lvs_improved,
)


def _touching_epad_board():
    """6-pin+ footprint with a 1.50 x 2.60 mm interior ground pad touching perimeter ground tabs."""
    board = Board.create_new()
    board.nets = [Net(0, ""), Net(1, "GND"), Net(2, "SIG1"), Net(3, "SIG2")]
    fp = Footprint(
        entryName="test:qfn_narrow_epad",
        position=Position(X=15.0, Y=15.0),
        properties={"Reference": "U1"},
    )
    # Interior pad 1.50 x 2.60 mm (area 3.9 mm^2 >= 2.0, min side 1.50 >= 1.2 but < 1.6)
    fp.pads.append(
        Pad(
            number="15", type="smd", shape="rect",
            position=Position(X=0.0, Y=0.0), size=Position(X=1.50, Y=2.60),
            net=board.nets[1], layers=["F.Cu", "F.Mask", "F.Paste"],
        )
    )
    # Two perimeter ground tabs touching top/bottom edge of pad 15 (y = +/-1.30)
    for num, y_pos in (("16", 1.55), ("17", -1.55)):
        fp.pads.append(
            Pad(
                number=num, type="smd", shape="rect",
                position=Position(X=0.0, Y=y_pos), size=Position(X=0.30, Y=0.50),
                net=board.nets[1], layers=["F.Cu", "F.Mask", "F.Paste"],
            )
        )
    # Six perimeter signal pins left and right so outward fanout from U1-15 is blocked (real QFN layout)
    for num, x_pos, y_pos, net_idx in (
        ("1", -1.45, -0.9, 2), ("2", -1.45, 0.0, 3), ("3", -1.45, 0.9, 2),
        ("4", 1.45, -0.9, 3), ("5", 1.45, 0.0, 2), ("6", 1.45, 0.9, 3),
    ):
        fp.pads.append(
            Pad(
                number=num, type="smd", shape="rect",
                position=Position(X=x_pos, Y=y_pos), size=Position(X=0.50, Y=0.35),
                net=board.nets[net_idx], layers=["F.Cu", "F.Mask", "F.Paste"],
            )
        )
    board.footprints.append(fp)
    return board


def test_dense_escape_grounds_narrow_interior_epad_and_touching_tabs():
    legacy = _touching_epad_board()
    _, legacy_override, legacy_stats = _add_gnd_fanout_vias(legacy, box(0, 0, 30, 30), [], dense_escape_repair=False)
    assert "U1-15" in legacy_stats["unvia_gnd_pins"]
    assert "GND" in legacy_override

    repaired = _touching_epad_board()
    skipped, overrides, stats = _add_gnd_fanout_vias(repaired, box(0, 0, 30, 30), [], dense_escape_repair=True)
    assert skipped == {"GND"}
    assert overrides == {}
    assert stats["unvia_gnd_pins"] == []
    vias = [item for item in repaired.traceItems if isinstance(item, Via)]
    assert len(vias) == 1
    assert vias[0].size == 0.50 and vias[0].drill == 0.25


def test_touching_pad_propagation_grounds_unvia_pad_touching_later_grounded_tab():
    """Even when a pad fails interior EPAD via placement (e.g., too small for a thermal via),
    touching a later-grounded same-footprint pad removes it from unvia_gnd_pins."""
    board = Board.create_new()
    board.nets = [Net(0, ""), Net(1, "GND"), Net(2, "SIG")]
    fp = Footprint(
        entryName="test:touching_chain",
        position=Position(X=15.0, Y=15.0),
        properties={"Reference": "U2"},
    )
    # Pad 1 sits inside ic_interior with 1.0 x 1.0 (below EPAD threshold) so it fails outward fanout
    fp.pads.append(Pad(number="1", type="smd", shape="rect", position=Position(X=0.0, Y=0.0), size=Position(X=1.0, Y=1.0), net=board.nets[1], layers=["F.Cu"]))
    # Pad 2 touches Pad 1 at y=0.50 and extends outside ic_interior so it gets an outward fanout via
    fp.pads.append(Pad(number="2", type="smd", shape="rect", position=Position(X=0.0, Y=0.75), size=Position(X=0.30, Y=0.50), net=board.nets[1], layers=["F.Cu"]))
    for idx, (x, y) in enumerate(((-1.2, -0.8), (-1.2, 0.8), (1.2, -0.8), (1.2, 0.8)), start=3):
        fp.pads.append(Pad(number=str(idx), type="smd", shape="rect", position=Position(X=x, Y=y), size=Position(X=0.4, Y=0.25), net=board.nets[2], layers=["F.Cu"]))
    board.footprints.append(fp)

    skipped, overrides, stats = _add_gnd_fanout_vias(board, box(0, 0, 30, 30), [], dense_escape_repair=True)
    assert stats["unvia_gnd_pins"] == []
    assert skipped == {"GND"}
    assert overrides == {}


def test_direct_repair_lvs_improved_allows_partial_island_merge_and_blocks_migration():
    before = {
        "lvs_opens": [{"net": "VBUS", "components": 4}, {"net": "SIG", "components": 2}],
        "lvs_shorts": [],
        "via_in_pad_violations": [],
        "rf_keepout": [{"passed": True}],
    }
    partial = {
        "lvs_opens": [{"net": "VBUS", "components": 2}, {"net": "SIG", "components": 2}],
        "lvs_shorts": [],
        "via_in_pad_violations": [],
        "rf_keepout": [{"passed": True}],
    }
    migrated = {
        "lvs_opens": [{"net": "OTHER", "components": 2}],
        "lvs_shorts": [],
        "via_in_pad_violations": [],
        "rf_keepout": [{"passed": True}],
    }
    shorted = {
        "lvs_opens": [{"net": "SIG", "components": 2}],
        "lvs_shorts": [{"layer": "F.Cu", "net_a": "VBUS", "net_b": "GND"}],
        "via_in_pad_violations": [],
        "rf_keepout": [{"passed": True}],
    }
    assert not _direct_repair_lvs_improved(before, partial, allow_partial_islands=False)
    assert _direct_repair_lvs_improved(before, partial, allow_partial_islands=True)
    assert not _direct_repair_lvs_improved(before, migrated, allow_partial_islands=True)
    assert not _direct_repair_lvs_improved(before, shorted, allow_partial_islands=True)
