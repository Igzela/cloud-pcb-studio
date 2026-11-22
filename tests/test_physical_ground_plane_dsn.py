"""Real fastroute integration and fail-closed tests for physical In1.Cu ground plane DSN mode."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from kiutils.board import Board
from kiutils.footprint import DrillDefinition, Footprint, Pad
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Net, Position
from shapely.geometry import box

from cloud_pcb.general_solver.direct_repair_replay import _fixed_wiring
from cloud_pcb.general_solver.run_blind import (
    _add_gnd_fanout_vias,
    _build_physical_ground_plane_dsn_spec,
)
from cloud_pcb.layout_route.dsn_ses_bridge import board_to_dsn, parse_ses
from contracts.schemas import NetClassRule


def _run_fastroute(dsn_path: Path, ses_path: Path, report_path: Path, *, route: bool = True) -> tuple[dict, dict]:
    cmd = [
        "fastroute",
        "-de",
        str(dsn_path),
        "-do",
        str(ses_path),
        f"--report={report_path}",
    ]
    if route:
        cmd.extend(["-mp", "20"])
    else:
        cmd.extend([
            "--router.autorouter.enabled=false",
            "--router.fanout.enabled=false",
            "--router.optimizer.enabled=false",
        ])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"fastroute failed:\n{proc.stderr}\n{proc.stdout}"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    parsed = parse_ses(ses_path) if ses_path.exists() else {}
    return report, parsed


def _touching_epad_and_signal_board() -> Board:
    """QFN with narrow interior EPAD touching perimeter ground tabs + external signal/ground passives."""
    board = Board.create_new()
    gnd = Net(1, "GND")
    sig1 = Net(2, "SIG1")
    sig2 = Net(3, "SIG2")
    board.nets = [Net(0, ""), gnd, sig1, sig2]

    u1 = Footprint(
        entryName="test:qfn_touching_epad",
        position=Position(X=15.0, Y=15.0),
        properties={"Reference": "U1"},
    )
    # Interior thermal EPAD (1.50 x 2.60 mm)
    u1.pads.append(
        Pad(
            number="15",
            type="smd",
            shape="rect",
            position=Position(X=0.0, Y=0.0),
            size=Position(X=1.50, Y=2.60),
            net=gnd,
            layers=["F.Cu", "F.Mask", "F.Paste"],
        )
    )
    # Perimeter ground tabs edge-touching EPAD at y = +/- 1.30 mm
    for num, y_pos in (("16", 1.55), ("17", -1.55)):
        u1.pads.append(
            Pad(
                number=num,
                type="smd",
                shape="rect",
                position=Position(X=0.0, Y=y_pos),
                size=Position(X=0.30, Y=0.50),
                net=gnd,
                layers=["F.Cu", "F.Mask", "F.Paste"],
            )
        )
    # Perimeter signal pins left & right
    for num, x_pos, y_pos, net in (
        ("1", -1.45, -0.9, sig1),
        ("2", -1.45, 0.0, None),
        ("3", -1.45, 0.9, None),
        ("4", 1.45, -0.9, sig2),
        ("5", 1.45, 0.0, None),
        ("6", 1.45, 0.9, None),
    ):
        u1.pads.append(
            Pad(
                number=num,
                type="smd",
                shape="rect",
                position=Position(X=x_pos, Y=y_pos),
                size=Position(X=0.50, Y=0.35),
                net=net,
                layers=["F.Cu", "F.Mask", "F.Paste"],
            )
        )
    board.footprints.append(u1)

    r1 = Footprint(
        entryName="test:r0603",
        position=Position(X=6.0, Y=10.0),
        properties={"Reference": "R1"},
    )
    r1.pads = [
        Pad(
            number="1",
            type="smd",
            shape="rect",
            position=Position(X=-0.8, Y=0.0),
            size=Position(X=0.8, Y=0.8),
            net=gnd,
            layers=["F.Cu", "F.Mask", "F.Paste"],
        ),
        Pad(
            number="2",
            type="smd",
            shape="rect",
            position=Position(X=0.8, Y=0.0),
            size=Position(X=0.8, Y=0.8),
            net=sig1,
            layers=["F.Cu", "F.Mask", "F.Paste"],
        ),
    ]
    board.footprints.append(r1)

    r2 = Footprint(
        entryName="test:r0603",
        position=Position(X=24.0, Y=10.0),
        properties={"Reference": "R2"},
    )
    r2.pads = [
        Pad(
            number="1",
            type="smd",
            shape="rect",
            position=Position(X=-0.8, Y=0.0),
            size=Position(X=0.8, Y=0.8),
            net=sig2,
            layers=["F.Cu", "F.Mask", "F.Paste"],
        ),
        Pad(
            number="2",
            type="smd",
            shape="rect",
            position=Position(X=0.8, Y=0.0),
            size=Position(X=0.8, Y=0.8),
            net=gnd,
            layers=["F.Cu", "F.Mask", "F.Paste"],
        ),
    ]
    board.footprints.append(r2)
    return board


def test_edge_touching_ground_pads_and_centered_thermal_epad_with_fastroute(tmp_path: Path) -> None:
    board = _touching_epad_and_signal_board()
    board_poly = box(0.0, 0.0, 30.0, 30.0)
    keepouts: list[dict] = []
    skip_nets, override_pins, fanout_stats = _add_gnd_fanout_vias(
        board,
        board_poly,
        keepouts,
        octagonal_keepouts=True,
        via_pad_d=0.55,
        via_drill_d=0.30,
        dense_escape_repair=True,
    )
    assert skip_nets == {"GND"}
    assert override_pins == {}
    assert fanout_stats["unvia_gnd_pins"] == []

    rules = {"DEFAULT": NetClassRule("DEFAULT", 0.20, 0.15, 0.30, 0.55, 0.5)}
    routing_layers = {"DEFAULT": ("F.Cu", "In2.Cu", "B.Cu")}

    # Baseline legacy skip_nets drops GND pad membership, causing touching pads to trigger spacing violations.
    legacy_dsn = board_to_dsn(
        board,
        tmp_path / "legacy.dsn",
        rules,
        {},
        board_bounds_mm=(0.0, 0.0, 30.0, 30.0),
        board_outline_mm=list(board_poly.exterior.coords),
        keepouts_mm=keepouts,
        skip_nets=skip_nets,
        override_net_pins=override_pins,
        routing_layers_by_class=routing_layers,
        safe_pin_references=True,
    )
    legacy_rep, _ = _run_fastroute(
        legacy_dsn, tmp_path / "legacy.ses", tmp_path / "legacy_report.json"
    )
    assert legacy_rep["stats"]["violations"] > 0

    # Physical ground plane mode keeps all GND pad membership and encodes the centered thermal EPAD
    # via as a multi-layer padstack (never as a separate via-at-SMD in (wiring)).
    phys_plane = _build_physical_ground_plane_dsn_spec(board, board_poly, keepouts)
    assert phys_plane is not None
    assert phys_plane["net_name"] == "GND"
    assert phys_plane["thermal_epad_vias"] == {("U1", "15"): 500}
    assert len(phys_plane["fixed_vias"]) == 2
    assert len(phys_plane["fixed_wires"]) == 2
    # Confirm thermal EPAD via center (15000, -15000) is NOT emitted as a standalone fixed via.
    assert all(not (v["x_um"] == 15000 and v["y_um"] == -15000) for v in phys_plane["fixed_vias"])

    filtered_keepouts = [
        k for k in keepouts if k.get("kind") not in ("fixed_ground_via", "fixed_ground_stub")
    ]
    phys_dsn = board_to_dsn(
        board,
        tmp_path / "phys.dsn",
        rules,
        {},
        board_bounds_mm=(0.0, 0.0, 30.0, 30.0),
        board_outline_mm=list(board_poly.exterior.coords),
        keepouts_mm=filtered_keepouts,
        skip_nets=None,
        override_net_pins=None,
        routing_layers_by_class=routing_layers,
        safe_pin_references=True,
        physical_ground_plane=phys_plane,
    )
    dsn_text = phys_dsn.read_text(encoding="utf-8")
    assert "(plane GND" in dsn_text
    assert "(polygon In1.Cu 0 " in dsn_text
    assert "(padstack Rect_EPAD_T_1500x2600_V500_um" in dsn_text
    assert "(shape (circle In1.Cu 500))" in dsn_text
    assert "(shape (circle B.Cu 500))" in dsn_text
    assert '"U1"-"15"' in dsn_text and '"U1"-"16"' in dsn_text and '"U1"-"17"' in dsn_text
    assert "(type fix))" in dsn_text

    # Add an explicit (via_at_smd off) rule in (structure) to prove thermal EPAD padstack is 100% clean.
    strict_dsn = tmp_path / "phys_via_at_smd_off.dsn"
    strict_dsn.write_text(
        dsn_text.replace(
            "    (rule\n",
            "    (via_at_smd off)\n    (rule\n",
            1,
        ),
        encoding="utf-8",
    )
    phys_rep, phys_ses = _run_fastroute(
        strict_dsn, tmp_path / "phys.ses", tmp_path / "phys_report.json"
    )
    assert phys_rep["stats"]["unrouted"] == 0
    assert phys_rep["stats"]["violations"] == 0
    assert set(phys_ses["routed_net_names"]) == {"SIG1", "SIG2"}


def test_plane_polygon_with_foreign_th_window_routes_cleanly_with_fastroute(tmp_path: Path) -> None:
    board = Board.create_new()
    gnd = Net(1, "PGND")
    sig = Net(2, "DATA")
    board.nets = [Net(0, ""), gnd, sig]

    # Left & right ground TH connectors + SMD ground pad with fanout via
    j1 = Footprint(
        entryName="test:hdr",
        position=Position(X=5.0, Y=10.0),
        properties={"Reference": "J1"},
        pads=[
            Pad(
                number="1",
                type="thru_hole",
                shape="circle",
                position=Position(X=0.0, Y=0.0),
                size=Position(X=1.4, Y=1.4),
                drill=DrillDefinition(diameter=0.8),
                net=gnd,
                layers=["*.Cu", "*.Mask"],
            ),
            Pad(
                number="2",
                type="thru_hole",
                shape="circle",
                position=Position(X=0.0, Y=3.0),
                size=Position(X=1.4, Y=1.4),
                drill=DrillDefinition(diameter=0.8),
                net=sig,
                layers=["*.Cu", "*.Mask"],
            ),
        ],
    )
    # Foreign signal TH pad in the middle of the board (creates an In1.Cu antipad window)
    j_mid = Footprint(
        entryName="test:mid_th",
        position=Position(X=15.0, Y=10.0),
        properties={"Reference": "J2"},
        pads=[
            Pad(
                number="1",
                type="thru_hole",
                shape="circle",
                position=Position(X=0.0, Y=0.0),
                size=Position(X=1.6, Y=1.6),
                drill=DrillDefinition(diameter=0.9),
                net=sig,
                layers=["*.Cu", "*.Mask"],
            ),
        ],
    )
    # NPTH mounting hole in the middle-bottom (creates a second In1.Cu window)
    h1 = Footprint(
        entryName="test:npth",
        position=Position(X=15.0, Y=15.0),
        properties={"Reference": "H1"},
        pads=[
            Pad(
                number="1",
                type="np_thru_hole",
                shape="circle",
                position=Position(X=0.0, Y=0.0),
                size=Position(X=2.2, Y=2.2),
                drill=DrillDefinition(diameter=2.2),
                layers=["*.Cu", "*.Mask"],
            ),
        ],
    )
    j3 = Footprint(
        entryName="test:right_gnd",
        position=Position(X=25.0, Y=10.0),
        properties={"Reference": "J3"},
        pads=[
            Pad(
                number="1",
                type="thru_hole",
                shape="circle",
                position=Position(X=0.0, Y=0.0),
                size=Position(X=1.4, Y=1.4),
                drill=DrillDefinition(diameter=0.8),
                net=gnd,
                layers=["*.Cu", "*.Mask"],
            ),
        ],
    )
    board.footprints.extend([j1, j_mid, h1, j3])

    board_poly = box(0.0, 0.0, 30.0, 20.0)
    phys_plane = _build_physical_ground_plane_dsn_spec(board, board_poly, [])
    assert phys_plane is not None
    assert phys_plane["net_name"] == "PGND"
    # Expect at least 2 interior cutout windows: foreign TH pads (J1-2 & J2-1) + NPTH H1
    assert len(phys_plane["plane_windows_um"]) >= 3

    rules = {"DEFAULT": NetClassRule("DEFAULT", 0.20, 0.15, 0.30, 0.55, 0.5)}
    dsn_path = board_to_dsn(
        board,
        tmp_path / "window_plane.dsn",
        rules,
        {},
        board_bounds_mm=(0.0, 0.0, 30.0, 20.0),
        board_outline_mm=list(board_poly.exterior.coords),
        routing_layers_by_class={"DEFAULT": ("F.Cu", "In2.Cu", "B.Cu")},
        safe_pin_references=True,
        physical_ground_plane=phys_plane,
    )
    dsn_text = dsn_path.read_text(encoding="utf-8")
    assert "(plane PGND" in dsn_text
    assert "(window (polygon In1.Cu 0 " in dsn_text

    rep, ses = _run_fastroute(
        dsn_path, tmp_path / "window_plane.ses", tmp_path / "window_plane_report.json"
    )
    assert rep["stats"]["unrouted"] == 0
    assert rep["stats"]["violations"] == 0
    assert set(ses["routed_net_names"]) == {"DATA"}


def test_disconnected_in1_plane_islands_fail_closed() -> None:
    board = Board.create_new()
    gnd = Net(1, "GND")
    board.nets = [Net(0, ""), gnd]

    for ref, x_pos in (("J1", 5.0), ("J2", 25.0)):
        board.footprints.append(
            Footprint(
                entryName="test:th",
                position=Position(X=x_pos, Y=10.0),
                properties={"Reference": ref},
                pads=[
                    Pad(
                        number="1",
                        type="thru_hole",
                        shape="circle",
                        position=Position(X=0.0, Y=0.0),
                        size=Position(X=1.4, Y=1.4),
                        drill=DrillDefinition(diameter=0.8),
                        net=gnd,
                        layers=["*.Cu", "*.Mask"],
                    ),
                ],
            )
        )

    board_poly = box(0.0, 0.0, 30.0, 20.0)
    # RF keepout band spanning full board height splits In1.Cu into two disconnected islands.
    split_keepouts = [
        {"kind": "rf", "x_min": 13.0, "y_min": 0.0, "x_max": 17.0, "y_max": 20.0, "layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]}
    ]
    assert _build_physical_ground_plane_dsn_spec(board, board_poly, split_keepouts) is None


def test_off_center_thermal_via_inside_smd_pad_fails_closed() -> None:
    board = Board.create_new()
    gnd = Net(1, "GND")
    board.nets = [Net(0, ""), gnd]
    board.footprints.append(
        Footprint(
            entryName="test:epad",
            position=Position(X=15.0, Y=15.0),
            properties={"Reference": "U1"},
            pads=[
                Pad(
                    number="1",
                    type="smd",
                    shape="rect",
                    position=Position(X=0.0, Y=0.0),
                    size=Position(X=2.0, Y=2.0),
                    net=gnd,
                    layers=["F.Cu", "F.Mask", "F.Paste"],
                ),
            ],
        )
    )
    # Off-center ground via inside SMD pad (0.35 mm > 0.01 mm centering threshold)
    board.traceItems.append(
        Via(
            position=Position(X=15.35, Y=15.0),
            size=0.50,
            drill=0.25,
            layers=["F.Cu", "B.Cu"],
            net=1,
        )
    )
    assert _build_physical_ground_plane_dsn_spec(board, box(0.0, 0.0, 30.0, 30.0), []) is None


def test_direct_repair_replay_fixed_wiring_appends_to_nonempty_physical_plane_dsn(tmp_path: Path) -> None:
    board = _touching_epad_and_signal_board()
    board_poly = box(0.0, 0.0, 30.0, 30.0)
    keepouts: list[dict] = []
    _add_gnd_fanout_vias(board, board_poly, keepouts, dense_escape_repair=True)
    phys_plane = _build_physical_ground_plane_dsn_spec(board, board_poly, keepouts)
    assert phys_plane is not None
    assert phys_plane["fixed_wires"] and phys_plane["fixed_vias"]

    rules = {"DEFAULT": NetClassRule("DEFAULT", 0.20, 0.15, 0.30, 0.55, 0.5)}
    dsn_path = board_to_dsn(
        board,
        tmp_path / "base_phys.dsn",
        rules,
        {},
        board_bounds_mm=(0.0, 0.0, 30.0, 30.0),
        board_outline_mm=list(board_poly.exterior.coords),
        keepouts_mm=[k for k in keepouts if k.get("kind") not in ("fixed_ground_via", "fixed_ground_stub")],
        routing_layers_by_class={"DEFAULT": ("F.Cu", "In2.Cu", "B.Cu")},
        safe_pin_references=True,
        physical_ground_plane=phys_plane,
    )
    base_text = dsn_path.read_text(encoding="utf-8")
    repair = SimpleNamespace(
        net="SIG1",
        layer="F.Cu",
        width_mm=0.20,
        start_xy=(6.8, 10.0),
        end_xy=(13.55, 14.1),
    )
    updated_text = _fixed_wiring(base_text, [repair])
    # All pre-existing fixed ground vias and wires remain intact, and the new repair wire is appended cleanly.
    assert "(via Via[0-3]_550:300_um " in updated_text
    assert "(net GND) (type fix))" in updated_text
    assert "(wire (path F.Cu 200 6800 -10000 13550 -14100) (net SIG1) (type fix))" in updated_text

    updated_dsn = tmp_path / "repaired_phys.dsn"
    updated_dsn.write_text(updated_text, encoding="utf-8")
    rep, _ = _run_fastroute(
        updated_dsn, tmp_path / "repaired_phys.ses", tmp_path / "repaired_phys_report.json", route=False
    )
    assert rep["stats"]["nets"] == 3


def test_smd_bbox_guard_prevents_fixed_via_clearance_violation_in_fastroute(tmp_path: Path) -> None:
    """Roundrect SMD pads are exported as full axis-aligned rectangles in DSN; guarding fixed
    ground vias against SMD bounding boxes prevents FastRoute clearance violations on the corner."""
    board = Board.create_new()
    gnd = Net(1, "PGND")
    sig = Net(2, "SIG")
    board.nets = [Net(0, ""), gnd, sig]

    # Ground SMD pad adjacent to a roundrect SMD pad whose chamfered corner would sit <0.13mm
    # from a diagonal fanout via if only the rounded polygon (not the DSN bbox) were guarded.
    r1 = Footprint(
        entryName="test:r_rr",
        position=Position(X=10.0, Y=10.0),
        properties={"Reference": "R1"},
        pads=[
            Pad(
                number="1",
                type="smd",
                shape="roundrect",
                position=Position(X=0.0, Y=0.0),
                size=Position(X=1.2, Y=1.2),
                roundrectRatio=0.25,
                net=gnd,
                layers=["F.Cu", "F.Mask", "F.Paste"],
            ),
            Pad(
                number="2",
                type="smd",
                shape="roundrect",
                position=Position(X=1.35, Y=0.0),
                size=Position(X=1.2, Y=1.2),
                roundrectRatio=0.25,
                net=sig,
                layers=["F.Cu", "F.Mask", "F.Paste"],
            ),
        ],
    )
    # Nearby signal pad to connect SIG
    r2 = Footprint(
        entryName="test:r_sig",
        position=Position(X=18.0, Y=10.0),
        properties={"Reference": "R2"},
        pads=[
            Pad(
                number="1",
                type="smd",
                shape="rect",
                position=Position(X=0.0, Y=0.0),
                size=Position(X=1.0, Y=1.0),
                net=sig,
                layers=["F.Cu", "F.Mask", "F.Paste"],
            ),
        ],
    )
    # Second ground pad so PGND is a multi-pin net
    j1 = Footprint(
        entryName="test:j_gnd",
        position=Position(X=5.0, Y=10.0),
        properties={"Reference": "J1"},
        pads=[
            Pad(
                number="1",
                type="thru_hole",
                shape="circle",
                position=Position(X=0.0, Y=0.0),
                size=Position(X=1.4, Y=1.4),
                drill=DrillDefinition(diameter=0.8),
                net=gnd,
                layers=["*.Cu", "*.Mask"],
            ),
        ],
    )
    board.footprints.extend([r1, r2, j1])

    board_poly = box(0.0, 0.0, 25.0, 20.0)
    keepouts: list[dict] = []
    _add_gnd_fanout_vias(
        board,
        board_poly,
        keepouts,
        via_pad_d=0.50,
        via_drill_d=0.25,
        smd_via_clearance=0.165,
        use_smd_bbox_guard=True,
        dense_escape_repair=True,
    )
    phys_plane = _build_physical_ground_plane_dsn_spec(board, board_poly, keepouts)
    assert phys_plane is not None
    assert len(phys_plane["fixed_vias"]) == 1

    rules = {"DEFAULT": NetClassRule("DEFAULT", 0.20, 0.13, 0.25, 0.50, 0.5)}
    dsn_path = board_to_dsn(
        board,
        tmp_path / "rr_guard.dsn",
        rules,
        {},
        board_bounds_mm=(0.0, 0.0, 25.0, 20.0),
        board_outline_mm=list(board_poly.exterior.coords),
        keepouts_mm=[k for k in keepouts if k.get("kind") not in ("fixed_ground_via", "fixed_ground_stub")],
        routing_layers_by_class={"DEFAULT": ("F.Cu", "In2.Cu", "B.Cu")},
        safe_pin_references=True,
        honor_rule_via_geometry=True,
        align_structure_clearance=True,
        physical_ground_plane=phys_plane,
    )
    rep, ses = _run_fastroute(
        dsn_path, tmp_path / "rr_guard.ses", tmp_path / "rr_guard_report.json"
    )
    assert rep["stats"]["unrouted"] == 0
    assert rep["stats"]["violations"] == 0
    assert set(ses["routed_net_names"]) == {"SIG"}

