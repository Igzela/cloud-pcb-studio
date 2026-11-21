"""Real fastroute integration checks for DSN pin quoting, via rules, and structure clearance."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from kiutils.board import Board
from kiutils.footprint import DrillDefinition, Footprint, Pad
from kiutils.items.common import Net, Position

from cloud_pcb.layout_route.dsn_ses_bridge import board_to_dsn, parse_ses
from contracts.schemas import NetClassRule


def _run_fastroute(dsn_path: Path, ses_path: Path, report_path: Path) -> tuple[dict, dict]:
    proc = subprocess.run(
        [
            "fastroute",
            "-de",
            str(dsn_path),
            "-do",
            str(ses_path),
            f"--report={report_path}",
            "-mp",
            "20",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"fastroute failed:\n{proc.stderr}\n{proc.stdout}"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    parsed = parse_ses(ses_path) if ses_path.exists() else {}
    return report, parsed


def test_safe_pin_references_routes_punctuated_refdes_with_fastroute(tmp_path: Path) -> None:
    board = Board.create_new()
    sig = Net(1, "NTC_SIG")
    board.nets = [Net(0, ""), sig]

    board.footprints.append(
        Footprint(
            position=Position(X=10.0, Y=10.0),
            properties={"Reference": "THERMISTOR(103-AT)1"},
            pads=[
                Pad(
                    number="1",
                    type="smd",
                    shape="rect",
                    position=Position(X=-1.0, Y=0.0),
                    size=Position(X=0.8, Y=0.8),
                    layers=["F.Cu"],
                ),
                Pad(
                    number="2",
                    type="smd",
                    shape="rect",
                    position=Position(X=1.0, Y=0.0),
                    size=Position(X=0.8, Y=0.8),
                    layers=["F.Cu"],
                    net=sig,
                ),
            ],
        )
    )
    board.footprints.append(
        Footprint(
            position=Position(X=24.0, Y=10.0),
            properties={"Reference": "R(pull up)-1"},
            pads=[
                Pad(
                    number="1",
                    type="smd",
                    shape="rect",
                    position=Position(X=0.0, Y=0.0),
                    size=Position(X=0.8, Y=0.8),
                    layers=["F.Cu"],
                    net=sig,
                )
            ],
        )
    )
    board.footprints.append(
        Footprint(
            position=Position(X=24.0, Y=20.0),
            properties={"Reference": "U1"},
            pads=[
                Pad(
                    number="3",
                    type="smd",
                    shape="rect",
                    position=Position(X=0.0, Y=0.0),
                    size=Position(X=0.8, Y=0.8),
                    layers=["F.Cu"],
                    net=sig,
                )
            ],
        )
    )

    rules = {"DEFAULT": NetClassRule("DEFAULT", 0.20, 0.15, 0.30, 0.55, 0.5)}

    legacy_dsn = board_to_dsn(
        board,
        tmp_path / "legacy.dsn",
        rules,
        {},
        board_bounds_mm=(0.0, 0.0, 40.0, 30.0),
        safe_pin_references=False,
    )
    assert '"THERMISTOR(103-AT)1-2"' in legacy_dsn.read_text(encoding="utf-8")
    legacy_report, legacy_ses = _run_fastroute(
        legacy_dsn, tmp_path / "legacy.ses", tmp_path / "legacy_report.json"
    )
    # Without separate component/pin quoting, fastroute drops the punctuated pins.
    assert legacy_report["stats"]["connections"] == 0
    assert legacy_ses["routed_nets"] == 0

    safe_dsn = board_to_dsn(
        board,
        tmp_path / "safe.dsn",
        rules,
        {},
        board_bounds_mm=(0.0, 0.0, 40.0, 30.0),
        safe_pin_references=True,
    )
    safe_text = safe_dsn.read_text(encoding="utf-8")
    assert '"THERMISTOR(103-AT)1"-"2"' in safe_text
    assert '"R(pull up)-1"-"1"' in safe_text
    safe_report, safe_ses = _run_fastroute(
        safe_dsn, tmp_path / "safe.ses", tmp_path / "safe_report.json"
    )
    assert safe_report["stats"]["connections"] == 2
    assert safe_report["stats"]["unrouted"] == 0
    assert safe_report["stats"]["violations"] == 0
    assert safe_ses["routed_nets"] == 1
    assert safe_ses["total_wirelength_mm"] > 0.0


def test_honor_rule_via_geometry_emits_and_routes_rule_vias_with_fastroute(tmp_path: Path) -> None:
    board = Board.create_new()
    sig = Net(1, "LAYER_JUMP")
    board.nets = [Net(0, ""), sig]

    board.footprints.append(
        Footprint(
            position=Position(X=10.0, Y=10.0),
            properties={"Reference": "U1"},
            pads=[
                Pad(
                    number="1",
                    type="smd",
                    shape="rect",
                    position=Position(X=0.0, Y=0.0),
                    size=Position(X=0.8, Y=0.8),
                    layers=["F.Cu"],
                    net=sig,
                )
            ],
        )
    )
    board.footprints.append(
        Footprint(
            position=Position(X=25.0, Y=10.0),
            properties={"Reference": "U2"},
            pads=[
                Pad(
                    number="1",
                    type="smd",
                    shape="rect",
                    position=Position(X=0.0, Y=0.0),
                    size=Position(X=0.8, Y=0.8),
                    layers=["B.Cu"],
                    net=sig,
                )
            ],
        )
    )

    rules = {"DEFAULT": NetClassRule("DEFAULT", 0.16, 0.15, 0.25, 0.50, 0.5)}

    default_dsn = board_to_dsn(
        board,
        tmp_path / "default_via.dsn",
        rules,
        {},
        board_bounds_mm=(0.0, 0.0, 40.0, 20.0),
        honor_rule_via_geometry=False,
    )
    default_text = default_dsn.read_text(encoding="utf-8")
    assert "Via[0-3]_550:300_um" in default_text
    assert "Via[0-3]_500:250_um" not in default_text
    default_report, default_ses = _run_fastroute(
        default_dsn, tmp_path / "default_via.ses", tmp_path / "default_via_report.json"
    )
    assert default_report["stats"]["unrouted"] == 0
    assert default_ses["via_count"] >= 1
    assert all(v["pad_mm"] == 0.55 and v["drill_mm"] == 0.30 for v in default_ses["vias"])

    honored_dsn = board_to_dsn(
        board,
        tmp_path / "rule_via.dsn",
        rules,
        {},
        board_bounds_mm=(0.0, 0.0, 40.0, 20.0),
        honor_rule_via_geometry=True,
    )
    honored_text = honored_dsn.read_text(encoding="utf-8")
    assert "Via[0-3]_500:250_um" in honored_text
    assert "Via[0-3]_550:300_um" not in honored_text
    honored_report, honored_ses = _run_fastroute(
        honored_dsn, tmp_path / "rule_via.ses", tmp_path / "rule_via_report.json"
    )
    assert honored_report["stats"]["unrouted"] == 0
    assert honored_report["stats"]["violations"] == 0
    assert honored_ses["via_count"] >= 1
    assert all(v["pad_mm"] == 0.50 and v["drill_mm"] == 0.25 for v in honored_ses["vias"])

    # Invalid rule via dimensions fall back safely to the explicit via_pad_mm/via_drill_mm defaults.
    bad_rules = {"DEFAULT": NetClassRule("DEFAULT", 0.16, 0.15, 0.40, 0.30, 0.5)}
    fallback_dsn = board_to_dsn(
        board,
        tmp_path / "fallback_via.dsn",
        bad_rules,
        {},
        board_bounds_mm=(0.0, 0.0, 40.0, 20.0),
        honor_rule_via_geometry=True,
    )
    assert "Via[0-3]_550:300_um" in fallback_dsn.read_text(encoding="utf-8")


def test_align_structure_clearance_allows_085mm_pitch_th_escape_with_fastroute(
    tmp_path: Path,
) -> None:
    board = Board.create_new()
    sig = Net(1, "TIGHT_SIG")
    board.nets = [Net(0, ""), sig]

    # A 0.85 mm pitch TH pad (0.44 mm diameter) surrounded by unassigned TH pads
    # at 0.85 mm pitch leaves a 0.41 mm edge-to-edge gap between adjacent flanking pins:
    #   0.13 mm trace + 2 * 0.15 mm clearance = 0.43 mm > 0.41 mm (blocked at 150 um)
    #   0.13 mm trace + 2 * 0.13 mm clearance = 0.39 mm <= 0.41 mm (escapes at 130 um)
    pad_dia = 0.44
    th_pads = [
        Pad(
            number="C",
            type="thru_hole",
            shape="circle",
            position=Position(X=0.0, Y=0.0),
            size=Position(X=pad_dia, Y=pad_dia),
            layers=["*.Cu", "*.Mask"],
            drill=DrillDefinition(diameter=0.25),
            net=sig,
        )
    ]
    for idx in range(6):
        angle = math.radians(60 * idx)
        th_pads.append(
            Pad(
                number=f"U{idx + 1}",
                type="thru_hole",
                shape="circle",
                position=Position(
                    X=round(0.85 * math.cos(angle), 4),
                    Y=round(0.85 * math.sin(angle), 4),
                ),
                size=Position(X=pad_dia, Y=pad_dia),
                layers=["*.Cu", "*.Mask"],
                drill=DrillDefinition(diameter=0.25),
            )
        )

    board.footprints.append(
        Footprint(
            position=Position(X=10.0, Y=10.0),
            properties={"Reference": "J1"},
            pads=th_pads,
        )
    )
    board.footprints.append(
        Footprint(
            position=Position(X=25.0, Y=10.0),
            properties={"Reference": "R1"},
            pads=[
                Pad(
                    number="1",
                    type="smd",
                    shape="rect",
                    position=Position(X=0.0, Y=0.0),
                    size=Position(X=0.8, Y=0.8),
                    layers=["F.Cu"],
                    net=sig,
                )
            ],
        )
    )

    rules = {
        "DEFAULT": NetClassRule("DEFAULT", 0.13, 0.13, 0.25, 0.50, 0.5),
        "SIGNAL": NetClassRule("SIGNAL", 0.13, 0.13, 0.25, 0.50, 0.5),
    }
    net_to_class = {"TIGHT_SIG": "SIGNAL"}

    legacy_dsn = board_to_dsn(
        board,
        tmp_path / "legacy_clr.dsn",
        rules,
        net_to_class,
        board_bounds_mm=(0.0, 0.0, 40.0, 20.0),
        align_structure_clearance=False,
    )
    legacy_text = legacy_dsn.read_text(encoding="utf-8")
    assert "(clearance 150)" in legacy_text
    assert "(clearance 150 (type default_smd))" in legacy_text
    legacy_report, _ = _run_fastroute(
        legacy_dsn, tmp_path / "legacy_clr.ses", tmp_path / "legacy_clr_report.json"
    )
    assert legacy_report["stats"]["unrouted"] == 1

    aligned_dsn = board_to_dsn(
        board,
        tmp_path / "aligned_clr.dsn",
        rules,
        net_to_class,
        board_bounds_mm=(0.0, 0.0, 40.0, 20.0),
        align_structure_clearance=True,
    )
    aligned_text = aligned_dsn.read_text(encoding="utf-8")
    assert "(clearance 130)" in aligned_text
    assert "(clearance 130 (type default_smd))" in aligned_text
    aligned_report, aligned_ses = _run_fastroute(
        aligned_dsn, tmp_path / "aligned_clr.ses", tmp_path / "aligned_clr_report.json"
    )
    assert aligned_report["stats"]["unrouted"] == 0
    assert aligned_report["stats"]["violations"] == 0
    assert aligned_ses["routed_nets"] == 1

    # Structure clearance never drops below the 125 um manufacturing floor.
    clamped_dsn = board_to_dsn(
        board,
        tmp_path / "clamped_clr.dsn",
        {
            "DEFAULT": NetClassRule("DEFAULT", 0.10, 0.10, 0.25, 0.50, 0.5),
            "SIGNAL": NetClassRule("SIGNAL", 0.13, 0.13, 0.25, 0.50, 0.5),
        },
        net_to_class,
        board_bounds_mm=(0.0, 0.0, 40.0, 20.0),
        align_structure_clearance=True,
    )
    clamped_text = clamped_dsn.read_text(encoding="utf-8")
    assert "(clearance 125)" in clamped_text
    assert "(clearance 125 (type default_smd))" in clamped_text
