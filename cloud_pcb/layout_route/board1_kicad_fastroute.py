"""Pipeline B: KiCad 4-layer JLC04161H-7628 PCB + Specctra DSN/SES Autorouter Bake-Off
(Rust fastroute v0.1.14 vs native freerouting-cli v2.5.0) + Shapely L2/L3 GND Pours
+ gerbonara RS-274X/Excellon Export + schemdraw Schematic SVG.
"""

from __future__ import annotations

import json
import math
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import schemdraw
import schemdraw.elements as elm
from gerbonara.apertures import CircleAperture, ExcellonTool, RectangleAperture
from gerbonara.excellon import ExcellonFile
from gerbonara.graphic_objects import Flash, Line, Region
from gerbonara.rs274x import GerberFile
from gerbonara.utils import MM
from kiutils.board import Board
from kiutils.footprint import DrillDefinition, Footprint, Pad
from kiutils.items.brditems import LayerToken, Stackup, StackupLayer, Via
from kiutils.items.common import Coordinate, Effects, Font, Net, Position, Stroke
from kiutils.items.fpitems import FpRect, FpText
from kiutils.items.gritems import GrRect
from kiutils.items.zones import FilledPolygon, FillSettings, KeepoutSettings, Zone, ZonePolygon
from shapely.geometry import MultiPolygon as ShpMultiPolygon
from shapely.geometry import Point as ShpPoint
from shapely.geometry import Polygon as ShpPolygon
from shapely.ops import unary_union

from cloud_pcb.layout_route.dsn_ses_bridge import board_to_dsn, merge_ses_into_board, parse_ses
from contracts.schemas import NetClassRule, RoutingBenchmark, StackupProfile, save_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTDIR = PROJECT_ROOT / "artifacts" / "board1_kicad_fastroute"

BOARD_W = 60.0
BOARD_H = 40.0

BOARD1_STACKUP = StackupProfile(
    id="JLC04161H-7628",
    layers=4,
    board_thickness_mm=1.6,
    outer_cu_oz=1.0,
    inner_cu_oz=0.5,
    prepreg_type="7628",
    prepreg_thickness_mm=0.2104,
    prepreg_er=4.4,
    core_thickness_mm=1.065,
    surface_finish="ENIG",
    substrate="FR4-MidTg",
    w_50ohm_mil=12.05,
    w_90diff_mil=8.31,
    s_90diff_mil=5.0,
    min_trace_mil=3.5,
    min_space_mil=3.5,
    min_drill_mm=0.30,
    min_annular_ring_mm=0.125,
    copper_to_edge_mm=0.50,
)

BOARD1_NETCLASSES: dict[str, NetClassRule] = {
    "POWER_VBUS": NetClassRule(
        name="POWER_VBUS",
        trace_width_mm=0.55,
        clearance_mm=0.15,
        via_drill_mm=0.30,
        via_pad_mm=0.55,
        max_current_a=3.0,
        zero_via_allowed=True,
    ),
    "DIFF_USB_90": NetClassRule(
        name="DIFF_USB_90",
        trace_width_mm=0.211,
        clearance_mm=0.127,
        via_drill_mm=0.30,
        via_pad_mm=0.55,
        max_current_a=0.2,
        diff_pair_gap_mm=0.127,
        impedance_ohm=90.0,
        zero_via_allowed=False,
    ),
    "RF_50": NetClassRule(
        name="RF_50",
        trace_width_mm=0.306,
        clearance_mm=0.20,
        via_drill_mm=0.30,
        via_pad_mm=0.55,
        max_current_a=0.2,
        impedance_ohm=50.0,
        zero_via_allowed=False,
    ),
    "POWER_3V3": NetClassRule(
        name="POWER_3V3",
        trace_width_mm=0.40,
        clearance_mm=0.15,
        via_drill_mm=0.30,
        via_pad_mm=0.55,
        max_current_a=1.5,
        zero_via_allowed=True,
    ),
    "DEFAULT": NetClassRule(
        name="DEFAULT",
        trace_width_mm=0.20,
        clearance_mm=0.15,
        via_drill_mm=0.30,
        via_pad_mm=0.55,
        max_current_a=0.5,
        zero_via_allowed=True,
    ),
}

NET_TO_CLASS: dict[str, str] = {
    "VBUS": "POWER_VBUS",
    "USB_DP": "DIFF_USB_90",
    "USB_DM": "DIFF_USB_90",
    "TELEM_RF": "RF_50",
    "3V3": "POWER_3V3",
    "BUCK_SW": "POWER_3V3",
    "VANA_2V5": "DEFAULT",
}

RF_KEEPOUT = {"x_min": 52.5, "y_min": 7.5, "x_max": 57.5, "y_max": 12.5}
MCU_CENTER = (33.0, 20.0)


def _make_footprint(
    ref: str,
    value: str,
    lib_name: str,
    x: float,
    y: float,
    pads_spec: list[dict[str, Any]],
    net_map: dict[str, Net],
    courtyard: tuple[float, float, float, float],
) -> Footprint:
    fp = Footprint()
    fp.libraryNickname = "cloud_pcb"
    fp.entryName = lib_name
    fp.layer = "F.Cu"
    fp.position = Position(X=x, Y=y, angle=0.0)
    fp.properties = {"Reference": ref, "Value": value}

    eff = Effects(font=Font(width=0.6, height=0.6, thickness=0.12))
    fp.graphicItems = [
        FpText(
            type="reference",
            text=ref,
            position=Position(X=0.0, Y=courtyard[1] - 0.8),
            layer="F.SilkS",
            effects=eff,
        ),
        FpText(
            type="value",
            text=value,
            position=Position(X=0.0, Y=courtyard[3] + 0.8),
            layer="F.Fab",
            effects=eff,
        ),
        FpRect(
            start=Position(X=courtyard[0], Y=courtyard[1]),
            end=Position(X=courtyard[2], Y=courtyard[3]),
            layer="F.CrtYd",
            stroke=Stroke(width=0.12),
        ),
    ]

    for pspec in pads_spec:
        pad = Pad()
        pad.number = str(pspec["num"])
        pad.type = pspec.get("type", "smd")
        pad.shape = pspec.get("shape", "rect")
        pad.position = Position(X=pspec["x"], Y=pspec["y"])
        pad.size = Position(X=pspec["w"], Y=pspec["h"])
        if pad.type == "thru_hole":
            pad.drill = DrillDefinition(diameter=float(pspec["drill"]))
            pad.layers = ["*.Cu", "*.Mask"]
        elif pad.type == "np_thru_hole":
            pad.drill = DrillDefinition(diameter=float(pspec["drill"]))
            pad.layers = ["*.Cu", "*.Mask"]
        else:
            pad.layers = ["F.Cu", "F.Paste", "F.Mask"]
        net_name = pspec.get("net")
        if net_name and net_name in net_map:
            pad.net = net_map[net_name]
        fp.pads.append(pad)

    return fp


def _two_pad_spec(
    pkg: str, net1: str, net2: str, vertical: bool = False
) -> tuple[list[dict[str, Any]], tuple[float, float, float, float]]:
    dims = {
        "0402": (0.50, 0.56, 0.54),
        "0603": (0.75, 0.80, 0.80),
        "0805": (0.95, 1.15, 1.30),
        "sod123": (1.65, 1.00, 1.20),
    }[pkg]
    dx, pw, ph = dims
    if not vertical:
        pads = [
            {"num": "1", "x": -dx, "y": 0.0, "w": pw, "h": ph, "net": net1},
            {"num": "2", "x": dx, "y": 0.0, "w": pw, "h": ph, "net": net2},
        ]
        cy = (-dx - pw / 2 - 0.15, -ph / 2 - 0.15, dx + pw / 2 + 0.15, ph / 2 + 0.15)
    else:
        pads = [
            {"num": "1", "x": 0.0, "y": -dx, "w": ph, "h": pw, "net": net1},
            {"num": "2", "x": 0.0, "y": dx, "w": ph, "h": pw, "net": net2},
        ]
        cy = (-ph / 2 - 0.15, -dx - pw / 2 - 0.15, ph / 2 + 0.15, dx + pw / 2 + 0.15)
    return pads, cy


def build_board1_kicad() -> Board:
    """Construct the unrouted 4-layer JLC04161H-7628 Board 1 kiutils Board."""
    board = Board.create_new()
    board.version = "20231120"
    board.generator = "cloud_pcb_layout_route"
    board.general.thickness = 1.6

    # 4-layer Sig-GND-GND-Sig stackup
    board.layers = [
        LayerToken(ordinal=0, name="F.Cu", type="signal"),
        LayerToken(ordinal=1, name="In1.Cu", type="power"),
        LayerToken(ordinal=2, name="In2.Cu", type="power"),
        LayerToken(ordinal=31, name="B.Cu", type="signal"),
        LayerToken(ordinal=32, name="B.Adhes", type="user"),
        LayerToken(ordinal=33, name="F.Adhes", type="user"),
        LayerToken(ordinal=34, name="B.Paste", type="user"),
        LayerToken(ordinal=35, name="F.Paste", type="user"),
        LayerToken(ordinal=36, name="B.SilkS", type="user"),
        LayerToken(ordinal=37, name="F.SilkS", type="user"),
        LayerToken(ordinal=38, name="B.Mask", type="user"),
        LayerToken(ordinal=39, name="F.Mask", type="user"),
        LayerToken(ordinal=44, name="Edge.Cuts", type="user"),
        LayerToken(ordinal=46, name="B.CrtYd", type="user"),
        LayerToken(ordinal=47, name="F.CrtYd", type="user"),
        LayerToken(ordinal=48, name="B.Fab", type="user"),
        LayerToken(ordinal=49, name="F.Fab", type="user"),
    ]

    board.setup.stackup = Stackup(
        layers=[
            StackupLayer(name="F.Cu", type="copper", thickness=0.035),
            StackupLayer(
                name="dielectric 1",
                type="prepreg",
                thickness=0.2104,
                material="FR4-7628",
                epsilonR=4.4,
            ),
            StackupLayer(name="In1.Cu", type="copper", thickness=0.0175),
            StackupLayer(
                name="dielectric 2",
                type="core",
                thickness=1.065,
                material="FR4",
                epsilonR=4.6,
            ),
            StackupLayer(name="In2.Cu", type="copper", thickness=0.0175),
            StackupLayer(
                name="dielectric 3",
                type="prepreg",
                thickness=0.2104,
                material="FR4-7628",
                epsilonR=4.4,
            ),
            StackupLayer(name="B.Cu", type="copper", thickness=0.035),
        ],
        copperFinish="ENIG",
        dielectricContraints="yes",
    )

    # Board outline 60 x 40 mm
    board.graphicItems.append(
        GrRect(
            start=Position(X=0.0, Y=0.0),
            end=Position(X=BOARD_W, Y=BOARD_H),
            layer="Edge.Cuts",
            width=0.1,
        )
    )

    net_names = [
        "",
        "GND",
        "VBUS",
        "VBUS_TVS",
        "BUCK_EN",
        "CC1",
        "CC2",
        "USB_DP",
        "USB_DM",
        "BUCK_SW",
        "BUCK_BST",
        "BUCK_FB",
        "3V3",
        "VANA_2V5",
        "MCU_NRST",
        "ADC_IN1",
        "ADC_IN2",
        "SENSE_1",
        "SENSE_2",
        "LED_STAT",
        "LED_ANODE",
        "PWM_ACT1",
        "PWM_ACT2",
        "TELEM_RF",
    ]
    board.nets = [Net(number=i, name=name) for i, name in enumerate(net_names)]
    net_map = {n.name: n for n in board.nets if n.name}

    # Corner NPTH mounting holes (2.2 mm, 3.5 / 3.0 mm inset)
    for idx, (hx, hy) in enumerate(
        [(3.5, 3.0), (3.5, 37.0), (56.5, 3.0), (56.5, 37.0)], start=1
    ):
        board.footprints.append(
            _make_footprint(
                f"H{idx}",
                "MountHole_2.2mm",
                "MountingHole_2.2mm_NPTH",
                hx,
                hy,
                [
                    {
                        "num": "1",
                        "type": "np_thru_hole",
                        "shape": "circle",
                        "x": 0.0,
                        "y": 0.0,
                        "w": 2.2,
                        "h": 2.2,
                        "drill": 2.2,
                    }
                ],
                net_map,
                (-1.2, -1.2, 1.2, 1.2),
            )
        )

    # J_USB: USB-C 2.0 16-pin receptacle at (4.5, 20.0)
    j_usb_pads = [
        {"num": "1", "x": 2.8, "y": -4.6, "w": 1.6, "h": 0.8, "net": "GND"},
        {"num": "2", "x": 2.8, "y": -2.8, "w": 1.6, "h": 1.6, "net": "VBUS"},
        {"num": "3", "x": 2.8, "y": -1.05, "w": 1.2, "h": 0.35, "net": "CC1"},
        {"num": "4", "x": 2.8, "y": -0.35, "w": 1.2, "h": 0.32, "net": "USB_DP"},
        {"num": "5", "x": 2.8, "y": 0.35, "w": 1.2, "h": 0.32, "net": "USB_DM"},
        {"num": "6", "x": 2.8, "y": 1.05, "w": 1.2, "h": 0.35, "net": "CC2"},
        {"num": "7", "x": 2.8, "y": 2.8, "w": 1.6, "h": 1.6, "net": "VBUS"},
        {"num": "8", "x": 2.8, "y": 4.6, "w": 1.6, "h": 0.8, "net": "GND"},
        {"num": "9", "type": "thru_hole", "shape": "circle", "x": -1.5, "y": -5.8, "w": 1.5, "h": 1.5, "drill": 0.9, "net": "GND"},
        {"num": "10", "type": "thru_hole", "shape": "circle", "x": -1.5, "y": 5.8, "w": 1.5, "h": 1.5, "drill": 0.9, "net": "GND"},
        {"num": "11", "type": "thru_hole", "shape": "circle", "x": 2.0, "y": -5.8, "w": 1.5, "h": 1.5, "drill": 0.9, "net": "GND"},
        {"num": "12", "type": "thru_hole", "shape": "circle", "x": 2.0, "y": 5.8, "w": 1.5, "h": 1.5, "drill": 0.9, "net": "GND"},
    ]
    board.footprints.append(
        _make_footprint("J_USB", "USB-C_16P", "USB_C_Receptacle", 4.5, 20.0, j_usb_pads, net_map, (-3.6, -5.2, 3.6, 5.2))
    )

    # Dual independent 5.1k Rd resistors on CC1 & CC2
    p, cy = _two_pad_spec("0402", "CC1", "GND")
    board.footprints.append(_make_footprint("R_CC1", "5.1k", "R_0402", 7.5, 9.0, p, net_map, cy))
    p, cy = _two_pad_spec("0402", "CC2", "GND")
    board.footprints.append(_make_footprint("R_CC2", "5.1k", "R_0402", 10.5, 26.0, p, net_map, cy))

    # Inline ESD TVS: USBLC6-2SC6 SOT23-6L at (11.0, 20.0) -> 2.47 mm from USB-C D+/D- pads
    sot23_6_tvs = [
        {"num": "1", "x": -1.3, "y": -0.95, "w": 1.0, "h": 0.6, "net": "USB_DP"},
        {"num": "2", "x": -1.3, "y": 0.0, "w": 1.0, "h": 0.6, "net": "GND"},
        {"num": "3", "x": -1.3, "y": 0.95, "w": 1.0, "h": 0.6, "net": "USB_DM"},
        {"num": "4", "x": 1.3, "y": 0.95, "w": 1.0, "h": 0.6, "net": "USB_DM"},
        {"num": "5", "x": 1.3, "y": 0.0, "w": 1.0, "h": 0.6, "net": "VBUS_TVS"},
        {"num": "6", "x": 1.3, "y": -0.95, "w": 1.0, "h": 0.6, "net": "USB_DP"},
    ]
    board.footprints.append(
        _make_footprint("D_TVS", "USBLC6-2SC6", "SOT-23-6L", 11.0, 20.0, sot23_6_tvs, net_map, (-1.8, -1.5, 1.8, 1.5))
    )
    p, cy = _two_pad_spec("0603", "VBUS", "VBUS_TVS", vertical=False)
    board.footprints.append(_make_footprint("R_TVS_PWR", "0R_TVS", "R_0603", 14.5, 16.0, p, net_map, cy))

    # Buck power stage (VBUS -> 3.3V @ 1.2A)
    p, cy = _two_pad_spec("0805", "VBUS", "GND", vertical=True)
    board.footprints.append(_make_footprint("C_IN1", "10uF_25V", "C_0805", 15.5, 11.5, p, net_map, cy))
    p, cy = _two_pad_spec("0805", "VBUS", "GND", vertical=True)
    board.footprints.append(_make_footprint("C_IN2", "100nF", "C_0805", 18.8, 11.5, p, net_map, cy))
    p, cy = _two_pad_spec("0603", "VBUS", "BUCK_EN", vertical=False)
    board.footprints.append(_make_footprint("R_EN", "100k", "R_0603", 20.5, 14.8, p, net_map, cy))

    u_buck_pads = [
        {"num": "1", "x": -1.3, "y": -0.95, "w": 1.0, "h": 0.6, "net": "GND"},
        {"num": "2", "x": -1.3, "y": 0.0, "w": 1.0, "h": 0.6, "net": "BUCK_SW"},
        {"num": "3", "x": -1.3, "y": 0.95, "w": 1.0, "h": 0.6, "net": "VBUS"},
        {"num": "4", "x": 1.3, "y": 0.95, "w": 1.0, "h": 0.6, "net": "BUCK_FB"},
        {"num": "5", "x": 1.3, "y": 0.0, "w": 1.0, "h": 0.6, "net": "BUCK_EN"},
        {"num": "6", "x": 1.3, "y": -0.95, "w": 1.0, "h": 0.6, "net": "BUCK_BST"},
    ]
    board.footprints.append(
        _make_footprint("U_BUCK", "TPS562201_3V3", "SOT-23-6", 23.0, 11.0, u_buck_pads, net_map, (-1.8, -1.5, 1.8, 1.5))
    )
    p, cy = _two_pad_spec("0402", "BUCK_BST", "BUCK_SW")
    board.footprints.append(_make_footprint("C_BST", "100nF", "C_0402", 23.0, 7.0, p, net_map, cy))
    p, cy = _two_pad_spec("sod123", "GND", "BUCK_SW")
    board.footprints.append(_make_footprint("D_BUCK", "B5819W", "SOD-123", 26.5, 14.0, p, net_map, cy))
    p, cy = _two_pad_spec("0805", "BUCK_SW", "3V3")
    board.footprints.append(_make_footprint("L_BUCK", "4.7uH", "L_0805", 28.5, 10.0, p, net_map, cy))
    p, cy = _two_pad_spec("0402", "3V3", "BUCK_FB")
    board.footprints.append(_make_footprint("R_FB1", "31.6k", "R_0402", 26.5, 6.5, p, net_map, cy))
    p, cy = _two_pad_spec("0402", "BUCK_FB", "GND")
    board.footprints.append(_make_footprint("R_FB2", "10k", "R_0402", 26.5, 4.2, p, net_map, cy))
    p, cy = _two_pad_spec("0805", "3V3", "GND", vertical=True)
    board.footprints.append(_make_footprint("C_OUT1", "22uF", "C_0805", 33.0, 9.5, p, net_map, cy))
    p, cy = _two_pad_spec("0805", "3V3", "GND", vertical=True)
    board.footprints.append(_make_footprint("C_OUT2", "22uF", "C_0805", 36.2, 9.5, p, net_map, cy))

    # Post-LDO 2.5V analog rail + C0G decoupling
    p, cy = _two_pad_spec("0402", "3V3", "GND", vertical=True)
    board.footprints.append(_make_footprint("C_LDO_IN", "1uF", "C_0402", 39.2, 9.5, p, net_map, cy))
    u_ldo_pads = [
        {"num": "1", "x": -1.15, "y": -0.95, "w": 1.0, "h": 0.6, "net": "3V3"},
        {"num": "2", "x": -1.15, "y": 0.0, "w": 1.0, "h": 0.6, "net": "GND"},
        {"num": "3", "x": -1.15, "y": 0.95, "w": 1.0, "h": 0.6, "net": "3V3"},
        {"num": "4", "x": 1.15, "y": 0.95, "w": 1.0, "h": 0.6, "net": None},
        {"num": "5", "x": 1.15, "y": -0.95, "w": 1.0, "h": 0.6, "net": "VANA_2V5"},
    ]
    board.footprints.append(
        _make_footprint("U_LDO", "TLV75525P", "SOT-23-5", 42.2, 9.5, u_ldo_pads, net_map, (-1.7, -1.5, 1.7, 1.5))
    )
    p, cy = _two_pad_spec("0402", "VANA_2V5", "GND", vertical=True)
    board.footprints.append(_make_footprint("C_ANA1", "100nF_C0G", "C_0402", 45.2, 8.8, p, net_map, cy))
    p, cy = _two_pad_spec("0402", "VANA_2V5", "GND", vertical=True)
    board.footprints.append(_make_footprint("C_ANA2", "10nF_C0G", "C_0402", 45.2, 11.8, p, net_map, cy))

    # QFN-24 Telemetry MCU at (33.0, 20.0) + 2.4x2.4mm DAP + 2x2 thermal via grid
    mcu_pin_nets = {
        1: "3V3", 2: "GND", 3: "USB_DP", 4: "USB_DM", 5: "GND", 6: "VANA_2V5",
        7: "VANA_2V5", 8: "ADC_IN1", 9: "ADC_IN2", 10: "LED_STAT", 11: "PWM_ACT1", 12: "PWM_ACT2",
        13: None, 14: None, 15: "GND", 16: "3V3", 17: "TELEM_RF", 18: None,
        19: None, 20: "3V3", 21: "GND", 22: "GND", 23: "MCU_NRST", 24: "3V3",
        25: "GND",
    }
    mcu_pads: list[dict[str, Any]] = []
    ys_left = [-1.25, -0.75, -0.25, 0.25, 0.75, 1.25]
    for idx, py in enumerate(ys_left, start=1):
        mcu_pads.append({"num": str(idx), "x": -1.9, "y": py, "w": 0.8, "h": 0.26, "net": mcu_pin_nets[idx]})
    xs_bot = [-1.25, -0.75, -0.25, 0.25, 0.75, 1.25]
    for idx, px in enumerate(xs_bot, start=7):
        mcu_pads.append({"num": str(idx), "x": px, "y": 1.9, "w": 0.26, "h": 0.8, "net": mcu_pin_nets[idx]})
    ys_right = [1.25, 0.75, 0.25, -0.25, -0.75, -1.25]
    for idx, py in enumerate(ys_right, start=13):
        mcu_pads.append({"num": str(idx), "x": 1.9, "y": py, "w": 0.8, "h": 0.26, "net": mcu_pin_nets[idx]})
    xs_top = [1.25, 0.75, 0.25, -0.25, -0.75, -1.25]
    for idx, px in enumerate(xs_top, start=19):
        mcu_pads.append({"num": str(idx), "x": px, "y": -1.9, "w": 0.26, "h": 0.8, "net": mcu_pin_nets[idx]})
    mcu_pads.append({"num": "25", "x": 0.0, "y": 0.0, "w": 2.4, "h": 2.4, "net": "GND"})

    board.footprints.append(
        _make_footprint("U_MCU", "MCU_QFN24", "QFN-24_4x4mm_P0.5mm", MCU_CENTER[0], MCU_CENTER[1], mcu_pads, net_map, (-2.5, -2.5, 2.5, 2.5))
    )

    # 2x2 QFN DAP thermal via array (0.30 mm drill, 0.55 mm pad, 1.10 mm grid pitch strictly inside 2.4x2.4 DAP)
    gnd_net_num = net_map["GND"].number
    for dx, dy in [(-0.55, -0.55), (0.55, -0.55), (-0.55, 0.55), (0.55, 0.55)]:
        board.traceItems.append(
            Via(
                position=Position(X=MCU_CENTER[0] + dx, Y=MCU_CENTER[1] + dy),
                size=0.55,
                drill=0.30,
                layers=["F.Cu", "B.Cu"],
                net=gnd_net_num,
                locked=True,
            )
        )

    # MCU decoupling, pullup, ESD sense resistors, LED, RF connector, Actuator header
    p, cy = _two_pad_spec("0402", "3V3", "GND")
    board.footprints.append(_make_footprint("C_MCU1", "100nF", "C_0402", 29.0, 15.5, p, net_map, cy))
    p, cy = _two_pad_spec("0402", "3V3", "GND")
    board.footprints.append(_make_footprint("C_MCU2", "100nF", "C_0402", 36.5, 15.8, p, net_map, cy))
    p, cy = _two_pad_spec("0402", "VANA_2V5", "GND")
    board.footprints.append(_make_footprint("C_MCU_ANA", "100nF_C0G", "C_0402", 31.0, 25.0, p, net_map, cy))
    p, cy = _two_pad_spec("0402", "3V3", "MCU_NRST")
    board.footprints.append(_make_footprint("R_NRST", "10k", "R_0402", 32.5, 15.2, p, net_map, cy))

    p, cy = _two_pad_spec("0402", "ADC_IN1", "SENSE_1")
    board.footprints.append(_make_footprint("R_ADC1", "1k_ESD", "R_0402", 37.5, 27.0, p, net_map, cy))
    p, cy = _two_pad_spec("0402", "ADC_IN2", "SENSE_2")
    board.footprints.append(_make_footprint("R_ADC2", "1k_ESD", "R_0402", 37.5, 30.0, p, net_map, cy))

    p, cy = _two_pad_spec("0402", "LED_STAT", "LED_ANODE")
    board.footprints.append(_make_footprint("R_LED", "1k", "R_0402", 42.5, 24.5, p, net_map, cy))
    p, cy = _two_pad_spec("0603", "LED_ANODE", "GND")
    board.footprints.append(_make_footprint("D_LED", "LED_GREEN", "D_0603", 46.0, 24.5, p, net_map, cy))

    j_rf_pads = [
        {"num": "1", "x": -1.2, "y": 0.0, "w": 1.0, "h": 0.8, "net": "TELEM_RF"},
        {"num": "2", "x": 0.8, "y": -1.4, "w": 1.0, "h": 1.2, "net": "GND"},
        {"num": "3", "x": 0.8, "y": 1.4, "w": 1.0, "h": 1.2, "net": "GND"},
    ]
    board.footprints.append(
        _make_footprint("J_RF", "UFL_RF_50", "Conn_Coax_UFL", 49.5, 17.0, j_rf_pads, net_map, (-1.8, -2.1, 1.8, 2.1))
    )

    act_nets = ["3V3", "GND", "PWM_ACT1", "PWM_ACT2", "SENSE_1", "SENSE_2"]
    act_pads = [
        {
            "num": str(i + 1),
            "type": "thru_hole",
            "shape": "rect" if i == 0 else "circle",
            "x": 0.0,
            "y": (i - 2.5) * 2.54,
            "w": 1.7,
            "h": 1.7,
            "drill": 1.0,
            "net": act_nets[i],
        }
        for i in range(6)
    ]
    board.footprints.append(
        _make_footprint("J_ACT", "Header_1x6_2.54mm", "PinHeader_1x06_P2.54mm", 52.5, 25.5, act_pads, net_map, (-1.27, -7.62, 1.27, 7.62))
    )

    # RF keepout zone (non-copper keepout in KiCad)
    ko_zone = Zone()
    ko_zone.net = 0
    ko_zone.netName = ""
    ko_zone.layers = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    ko_zone.name = "RF_KEEPOUT"
    ko_zone.keepoutSettings = KeepoutSettings(
        tracks="not_allowed",
        vias="not_allowed",
        pads="not_allowed",
        copperpour="not_allowed",
        footprints="not_allowed",
    )
    ko_zone.polygons = [
        ZonePolygon(
            coordinates=[
                Coordinate(X=RF_KEEPOUT["x_min"], Y=RF_KEEPOUT["y_min"]),
                Coordinate(X=RF_KEEPOUT["x_max"], Y=RF_KEEPOUT["y_min"]),
                Coordinate(X=RF_KEEPOUT["x_max"], Y=RF_KEEPOUT["y_max"]),
                Coordinate(X=RF_KEEPOUT["x_min"], Y=RF_KEEPOUT["y_max"]),
            ]
        )
    ]
    board.zones.append(ko_zone)

    return board


def _build_shapely_gnd_pours(board: Board) -> tuple[ShpPolygon | Any, dict[str, Any]]:
    """Compute Shapely-trimmed solid GND copper pours for L2 (In1.Cu) and L3 (In2.Cu)."""
    edge = BOARD1_STACKUP.copper_to_edge_mm  # 0.50 mm
    base_poly = ShpPolygon(
        [
            (edge, edge),
            (BOARD_W - edge, edge),
            (BOARD_W - edge, BOARD_H - edge),
            (edge, BOARD_H - edge),
        ]
    )

    cutouts: list[Any] = []

    # Subtract RF keepout zone + 0.25 mm margin
    cutouts.append(
        ShpPolygon(
            [
                (RF_KEEPOUT["x_min"] - 0.25, RF_KEEPOUT["y_min"] - 0.25),
                (RF_KEEPOUT["x_max"] + 0.25, RF_KEEPOUT["y_min"] - 0.25),
                (RF_KEEPOUT["x_max"] + 0.25, RF_KEEPOUT["y_max"] + 0.25),
                (RF_KEEPOUT["x_min"] - 0.25, RF_KEEPOUT["y_max"] + 0.25),
            ]
        )
    )

    # Subtract NPTH mounting holes and non-GND PTH pads
    gnd_net = next((n.number for n in board.nets if n.name == "GND"), 1)
    for fp in board.footprints:
        fx, fy = float(fp.position.X), float(fp.position.Y)
        for pad in fp.pads:
            px, py = fx + float(pad.position.X), fy + float(pad.position.Y)
            if pad.type == "np_thru_hole":
                drill_d = float(pad.drill.diameter) if pad.drill and pad.drill.diameter else 2.2
                r = drill_d / 2.0 + 0.50
                cutouts.append(ShpPoint(px, py).buffer(r, resolution=32))
            elif pad.type == "thru_hole":
                pnet = pad.net.number if pad.net else 0
                if pnet != gnd_net:
                    r = max(float(pad.size.X), float(pad.size.Y)) / 2.0 + 0.25
                    cutouts.append(ShpPoint(px, py).buffer(r, resolution=24))

    # Subtract non-GND routing vias (antipads)
    for item in board.traceItems:
        if isinstance(item, Via) and int(item.net) != gnd_net:
            r = float(item.size) / 2.0 + 0.25
            cutouts.append(ShpPoint(float(item.position.X), float(item.position.Y)).buffer(r, resolution=24))

    poured = base_poly.difference(unary_union(cutouts))
    area_mm2 = float(poured.area)
    board_area = BOARD_W * BOARD_H
    stats = {
        "pour_area_mm2": round(area_mm2, 3),
        "board_area_mm2": board_area,
        "fill_ratio": round(area_mm2 / board_area, 4),
        "antipad_cutouts": len(cutouts),
        "is_empty": poured.is_empty,
    }
    return poured, stats


def _inject_gnd_zones(board: Board, poured_geom: Any) -> None:
    """Insert filled L2 (In1.Cu) and L3 (In2.Cu) GND zones into kiutils Board."""
    from cloud_pcb.general_solver.run_blind import _saved_ground_fill_rings

    gnd_net = next((n.number for n in board.nets if n.name == "GND"), 1)
    edge = BOARD1_STACKUP.copper_to_edge_mm

    parts = poured_geom.geoms if poured_geom.geom_type == "MultiPolygon" else (poured_geom,)
    retained = [part for part in parts if part.area >= 0.1]
    fill_rings = _saved_ground_fill_rings(ShpMultiPolygon(retained)) if retained else []

    for layer in ("In1.Cu", "In2.Cu"):
        z = Zone()
        z.net = gnd_net
        z.netName = "GND"
        z.layers = [layer]
        z.name = f"GND_{layer.replace('.', '_')}"
        z.clearance = 0.25
        z.minThickness = 0.20
        z.connectPads = "yes"
        z.filledAreasThickness = "no"
        z.fillSettings = FillSettings(yes=True, thermalGap=0.30, thermalBridgeWidth=0.35)
        z.polygons = [
            ZonePolygon(
                coordinates=[
                    Coordinate(X=edge, Y=edge),
                    Coordinate(X=BOARD_W - edge, Y=edge),
                    Coordinate(X=BOARD_W - edge, Y=BOARD_H - edge),
                    Coordinate(X=edge, Y=BOARD_H - edge),
                ]
            )
        ]
        z.filledPolygons = [
            FilledPolygon(layer=layer, coordinates=[Position(X=x, Y=y) for x, y in ring])
            for ring in fill_rings
        ]
        board.zones.append(z)


def _k2g_y(y_kicad: float) -> float:
    return BOARD_H - y_kicad


def export_gerbers_gerbonara(board: Board, poured_geom: Any, gerber_dir: Path) -> list[str]:
    """Export complete RS-274X Gerbers + PTH.drl + NPTH.drl using gerbonara (MM)."""
    gerber_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    layer_objs: dict[str, list[Any]] = {
        "F_Cu": [],
        "In1_Cu": [],
        "In2_Cu": [],
        "B_Cu": [],
        "F_Mask": [],
        "B_Mask": [],
        "F_Paste": [],
        "B_Paste": [],
        "F_SilkS": [],
        "B_SilkS": [],
        "Edge_Cuts": [],
    }
    pth_objs: list[Flash] = []
    npth_objs: list[Flash] = []

    # 1. Board outline (Edge_Cuts)
    edge_ap = CircleAperture(0.10, unit=MM)
    corners = [(0.0, 0.0), (BOARD_W, 0.0), (BOARD_W, BOARD_H), (0.0, BOARD_H)]
    for (x1, y1), (x2, y2) in zip(corners, corners[1:] + corners[:1]):
        layer_objs["Edge_Cuts"].append(Line(x1, y1, x2, y2, aperture=edge_ap, unit=MM))

    # 2. Inner GND copper pours (L2 & L3) via shapely exterior + clear-polarity holes
    polys = list(poured_geom.geoms) if poured_geom.geom_type == "MultiPolygon" else [poured_geom]
    for gnd_layer in ("In1_Cu", "In2_Cu"):
        for poly in polys:
            if poly.area < 0.05:
                continue
            ext = [(float(x), _k2g_y(float(y))) for x, y in poly.exterior.coords]
            layer_objs[gnd_layer].append(Region(ext, unit=MM, polarity_dark=True))
            for ring in poly.interiors:
                hole_pts = [(float(x), _k2g_y(float(y))) for x, y in ring.coords]
                layer_objs[gnd_layer].append(Region(hole_pts, unit=MM, polarity_dark=False))

    # 3. Footprints: pads, mask, paste, silkscreen, drills
    silk_ap = CircleAperture(0.15, unit=MM)
    mask_openings: list[Any] = []
    candidate_silk_segs: list[tuple[tuple[float, float], tuple[float, float]]] = []

    for fp in board.footprints:
        fx, fy = float(fp.position.X), float(fp.position.Y)
        for gi in fp.graphicItems:
            if isinstance(gi, FpRect) and gi.layer in ("F.SilkS", "F.CrtYd"):
                x1, y1 = fx + float(gi.start.X), _k2g_y(fy + float(gi.start.Y))
                x2, y2 = fx + float(gi.end.X), _k2g_y(fy + float(gi.end.Y))
                pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                for (a, b) in zip(pts, pts[1:] + pts[:1]):
                    candidate_silk_segs.append((a, b))

        for pad in fp.pads:
            px, py = fx + float(pad.position.X), _k2g_y(fy + float(pad.position.Y))
            pw, ph = float(pad.size.X), float(pad.size.Y)
            if pad.type == "np_thru_hole":
                drill_d = float(pad.drill.diameter) if pad.drill and pad.drill.diameter else 2.2
                tool = ExcellonTool(drill_d, plated=False, unit=MM)
                npth_objs.append(Flash(px, py, tool, unit=MM))
                mask_openings.append(ShpPoint(px, py).buffer(drill_d / 2.0 + 0.25))
                continue

            if pad.shape == "circle":
                ap_cu = CircleAperture(pw, unit=MM)
                ap_mask = CircleAperture(pw + 0.10, unit=MM)
                mask_openings.append(ShpPoint(px, py).buffer((pw + 0.10) / 2.0 + 0.20))
            else:
                ap_cu = RectangleAperture(pw, ph, unit=MM)
                ap_mask = RectangleAperture(pw + 0.10, ph + 0.10, unit=MM)
                hw_m, hh_m = (pw + 0.10) / 2.0 + 0.20, (ph + 0.10) / 2.0 + 0.20
                mask_openings.append(
                    ShpPolygon(
                        [
                            (px - hw_m, py - hh_m),
                            (px + hw_m, py - hh_m),
                            (px + hw_m, py + hh_m),
                            (px - hw_m, py + hh_m),
                        ]
                    )
                )

            if pad.type == "thru_hole":
                layer_objs["F_Cu"].append(Flash(px, py, ap_cu, unit=MM))
                layer_objs["B_Cu"].append(Flash(px, py, ap_cu, unit=MM))
                layer_objs["F_Mask"].append(Flash(px, py, ap_mask, unit=MM))
                layer_objs["B_Mask"].append(Flash(px, py, ap_mask, unit=MM))
                if pad.net and pad.net.name == "GND":
                    layer_objs["In1_Cu"].append(Flash(px, py, ap_cu, unit=MM))
                    layer_objs["In2_Cu"].append(Flash(px, py, ap_cu, unit=MM))
                drill_d = float(pad.drill.diameter) if pad.drill and pad.drill.diameter else 1.0
                tool = ExcellonTool(drill_d, plated=True, unit=MM)
                pth_objs.append(Flash(px, py, tool, unit=MM))
            else:
                layer_objs["F_Cu"].append(Flash(px, py, ap_cu, unit=MM))
                layer_objs["F_Mask"].append(Flash(px, py, ap_mask, unit=MM))
                layer_objs["F_Paste"].append(Flash(px, py, ap_cu, unit=MM))

    # Clip silkscreen segments cleanly against expanded mask openings (zero silk-on-pad)
    from shapely.geometry import LineString as ShpLineString

    mask_guard = unary_union(mask_openings) if mask_openings else None
    for a, b in candidate_silk_segs:
        seg_geom = ShpLineString([a, b])
        clipped = seg_geom.difference(mask_guard) if mask_guard is not None else seg_geom
        if clipped.is_empty:
            continue
        geoms = list(clipped.geoms) if hasattr(clipped, "geoms") else [clipped]
        for g in geoms:
            if g.geom_type == "LineString" and g.length >= 0.25:
                coords = list(g.coords)
                for p1, p2 in zip(coords, coords[1:]):
                    layer_objs["F_SilkS"].append(
                        Line(float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]), aperture=silk_ap, unit=MM)
                    )

    # 4. Traces & Vias
    gnd_net = next((n.number for n in board.nets if n.name == "GND"), 1)
    for item in board.traceItems:
        if isinstance(item, Via):
            vx, vy = float(item.position.X), _k2g_y(float(item.position.Y))
            ap_via = CircleAperture(float(item.size), unit=MM)
            layer_objs["F_Cu"].append(Flash(vx, vy, ap_via, unit=MM))
            layer_objs["B_Cu"].append(Flash(vx, vy, ap_via, unit=MM))
            if int(item.net) == gnd_net:
                layer_objs["In1_Cu"].append(Flash(vx, vy, ap_via, unit=MM))
                layer_objs["In2_Cu"].append(Flash(vx, vy, ap_via, unit=MM))
            tool = ExcellonTool(float(item.drill), plated=True, unit=MM)
            pth_objs.append(Flash(vx, vy, tool, unit=MM))
        else:
            # Segment
            lname = item.layer.replace(".", "_")
            if lname not in layer_objs:
                continue
            ap_tr = CircleAperture(float(item.width), unit=MM)
            layer_objs[lname].append(
                Line(
                    float(item.start.X),
                    _k2g_y(float(item.start.Y)),
                    float(item.end.X),
                    _k2g_y(float(item.end.Y)),
                    aperture=ap_tr,
                    unit=MM,
                )
            )

    for lname, objs in layer_objs.items():
        gf = GerberFile(objects=objs)
        out_path = gerber_dir / f"{lname}.gbr"
        raw_gbr = gf.write_to_bytes().decode("utf-8")
        cleaned_lines = [
            "G75*" if ln == "G75" else (ln.replace("**", "*") if "**" in ln else ln)
            for ln in raw_gbr.splitlines()
        ]
        out_path.write_text("\n".join(cleaned_lines) + "\n", encoding="utf-8")
        written.append(out_path.name)

    pth_file = ExcellonFile(objects=pth_objs)
    pth_file.save(gerber_dir / "PTH.drl")
    written.append("PTH.drl")

    npth_file = ExcellonFile(objects=npth_objs)
    npth_file.save(gerber_dir / "NPTH.drl")
    written.append("NPTH.drl")

    return sorted(written)


def generate_schematic_svg(svg_path: Path) -> Path:
    """Render clean Board 1 engineering schematic SVG via schemdraw."""
    d = schemdraw.Drawing(show=False)
    d.config(unit=2.8, fontsize=9)

    # USB-C input block
    j_usb = elm.Ic(
        pins=[
            elm.IcPin(name="VBUS", side="right", pin="A4/B9"),
            elm.IcPin(name="CC1", side="right", pin="A5"),
            elm.IcPin(name="D+", side="right", pin="A6"),
            elm.IcPin(name="D-", side="right", pin="A7"),
            elm.IcPin(name="CC2", side="right", pin="B5"),
            elm.IcPin(name="GND", side="bot", pin="A1/B12"),
        ],
        pinspacing=0.65,
        edgepadW=0.8,
        edgepadH=0.8,
        label="J_USB\nUSB-C 2.0",
    ).at((0, 0))
    d += j_usb
    d += elm.Ground().at(j_usb.GND)

    # Dual independent 5.1k Rd pulldowns
    d += elm.Line().right(1.0).at(j_usb.CC1)
    d += (r_cc1 := elm.Resistor().down().label("R_CC1\n5.1k"))
    d += elm.Ground().at(r_cc1.end)

    d += elm.Line().right(2.2).at(j_usb.CC2)
    d += (r_cc2 := elm.Resistor().down().label("R_CC2\n5.1k"))
    d += elm.Ground().at(r_cc2.end)

    # Inline TVS USBLC6-2SC6
    tvs = elm.Ic(
        pins=[
            elm.IcPin(name="IO1_IN", side="left", pin="1"),
            elm.IcPin(name="GND", side="bot", pin="2"),
            elm.IcPin(name="IO2_IN", side="left", pin="3"),
            elm.IcPin(name="IO1_OUT", side="right", pin="6"),
            elm.IcPin(name="VBUS", side="top", pin="5"),
            elm.IcPin(name="IO2_OUT", side="right", pin="4"),
        ],
        pinspacing=0.8,
        label="D_TVS\nUSBLC6-2SC6",
    ).at((4.5, -0.3))
    d += tvs
    d += elm.Line().at(j_usb.D_p if hasattr(j_usb, "D_p") else j_usb.D_plus if hasattr(j_usb, "D_plus") else (j_usb.absanchors["D+"])).to(tvs.IO1_IN)
    d += elm.Line().at(j_usb.absanchors["D-"]).to(tvs.IO2_IN)
    d += elm.Ground().at(tvs.GND)
    d += elm.Vdd().at(tvs.VBUS).label("VBUS (5V)")

    # VBUS -> Sync Buck 3.3V -> Post-LDO 2.5V
    d += elm.Line().at(j_usb.VBUS).up(2.8)
    d += elm.Line().right(2.5)
    d.push()
    d += (c_in1 := elm.Capacitor().down().label("C_IN1\n10uF"))
    d += elm.Ground().at(c_in1.end)
    d.pop()
    d += elm.Line().right(1.5)
    u_buck = elm.Ic(
        pins=[
            elm.IcPin(name="VIN", side="left", pin="3"),
            elm.IcPin(name="EN", side="left", pin="5"),
            elm.IcPin(name="GND", side="bot", pin="1"),
            elm.IcPin(name="SW", side="right", pin="2"),
            elm.IcPin(name="FB", side="right", pin="4"),
        ],
        pinspacing=0.7,
        label="U_BUCK\n3.3V 1.2A",
    ).anchor("VIN")
    d += u_buck
    d += elm.Ground().at(u_buck.GND)
    d += elm.Inductor().right().at(u_buck.SW).label("L_BUCK\n4.7uH")
    d.push()
    d += (c_out := elm.Capacitor().down().label("C_OUT1/2\n2x22uF"))
    d += elm.Ground().at(c_out.end)
    d.pop()
    d += elm.Line().right(1.5).label("3V3", loc="top")
    u_ldo = elm.Ic(
        pins=[
            elm.IcPin(name="IN", side="left", pin="1"),
            elm.IcPin(name="GND", side="bot", pin="2"),
            elm.IcPin(name="OUT", side="right", pin="5"),
        ],
        pinspacing=0.8,
        label="U_LDO\n2.5V ANA",
    ).anchor("IN")
    d += u_ldo
    d += elm.Ground().at(u_ldo.GND)
    d += elm.Line().right(1.2).at(u_ldo.OUT).label("VANA_2V5", loc="top")
    d += (c_ana := elm.Capacitor().down().label("C_ANA1/2\n100n/10n C0G"))
    d += elm.Ground().at(c_ana.end)

    # MCU QFN-24 + Analog ESD + Actuator + RF
    u_mcu = elm.Ic(
        pins=[
            elm.IcPin(name="USB_DP", side="left", pin="3"),
            elm.IcPin(name="USB_DM", side="left", pin="4"),
            elm.IcPin(name="VDD3V3", side="top", pin="1,16,20,24"),
            elm.IcPin(name="VDDA2V5", side="top", pin="6,7"),
            elm.IcPin(name="DAP_GND", side="bot", pin="25"),
            elm.IcPin(name="RF_TX", side="right", pin="17"),
            elm.IcPin(name="PWM1", side="right", pin="11"),
            elm.IcPin(name="ADC1", side="right", pin="8"),
            elm.IcPin(name="ADC2", side="right", pin="9"),
            elm.IcPin(name="LED", side="right", pin="10"),
        ],
        pinspacing=0.65,
        label="U_MCU\nQFN-24",
    ).at((10.5, -0.5))
    d += u_mcu
    d += elm.Line().at(tvs.IO1_OUT).to(u_mcu.USB_DP).label("90Ω DIFF", loc="top")
    d += elm.Line().at(tvs.IO2_OUT).to(u_mcu.USB_DM)
    d += elm.Ground().at(u_mcu.DAP_GND)
    d += elm.Vdd().at(u_mcu.VDD3V3).label("3V3")
    d += elm.Vdd().at(u_mcu.VDDA2V5).label("2.5V")
    d += elm.Line().right(1.5).at(u_mcu.RF_TX).label("RF_50 (50Ω)", loc="top")
    d += elm.CoaxConnect().label("J_RF")
    d += elm.Resistor().right().at(u_mcu.ADC1).label("R_ADC1 1k")
    d += elm.Line().right(0.6).label("SENSE1", loc="right")
    d += elm.Resistor().right().at(u_mcu.ADC2).label("R_ADC2 1k")
    d += elm.Line().right(0.6).label("SENSE2", loc="right")
    d += elm.Resistor().right().at(u_mcu.LED).label("R_LED 1k")
    d += (led := elm.LED().down().label("D_LED"))
    d += elm.Ground().at(led.end)

    svg_path.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(svg_path))
    return svg_path


def _check_physics_board1(board: Board, ses_info: dict[str, Any]) -> dict[str, Any]:
    """Verify Board 1 physics rules directly on the routed kiutils Board."""
    fp_by_ref = {
        fp.properties.get("Reference", ""): fp for fp in board.footprints
    }

    # 1. TVS distance to USB-C D+/D- pads
    j_usb = fp_by_ref["J_USB"]
    d_tvs = fp_by_ref["D_TVS"]
    usb_data = [p for p in j_usb.pads if p.number in ("4", "5")]
    tvs_io = [p for p in d_tvs.pads if p.number in ("1", "3")]
    dists = [
        math.hypot(
            (j_usb.position.X + a.position.X) - (d_tvs.position.X + b.position.X),
            (j_usb.position.Y + a.position.Y) - (d_tvs.position.Y + b.position.Y),
        )
        for a in usb_data
        for b in tvs_io
    ]
    tvs_dist_mm = round(min(dists), 4)

    # 2. CC1 & CC2 separation
    r_cc1_nets = {p.net.name for p in fp_by_ref["R_CC1"].pads if p.net}
    r_cc2_nets = {p.net.name for p in fp_by_ref["R_CC2"].pads if p.net}
    cc_distinct = ("CC1" in r_cc1_nets) and ("CC2" in r_cc2_nets) and ("CC2" not in r_cc1_nets)

    # 3. Trace widths for VBUS, DIFF_USB_90, RF_50
    net_by_num = {n.number: n.name for n in board.nets}
    vbus_widths: list[float] = []
    rf_widths: list[float] = []
    usb_widths: list[float] = []
    for item in board.traceItems:
        if isinstance(item, Via):
            continue
        nname = net_by_num.get(item.net, "")
        if nname == "VBUS":
            vbus_widths.append(float(item.width))
        elif nname == "TELEM_RF":
            rf_widths.append(float(item.width))
        elif nname in ("USB_DP", "USB_DM"):
            usb_widths.append(float(item.width))

    # 4. Via-in-SMT-pad check (all vias except QFN DAP thermal vias)
    u_mcu = fp_by_ref["U_MCU"]
    dap_pad = next(p for p in u_mcu.pads if p.number == "25")
    dap_x = u_mcu.position.X + dap_pad.position.X
    dap_y = u_mcu.position.Y + dap_pad.position.Y
    dap_hw, dap_hh = dap_pad.size.X / 2.0, dap_pad.size.Y / 2.0

    smt_pads: list[tuple[str, float, float, float, float]] = []
    for ref, fp in fp_by_ref.items():
        for p in fp.pads:
            if p.type == "smd" and not (ref == "U_MCU" and p.number == "25"):
                smt_pads.append(
                    (
                        f"{ref}.{p.number}",
                        fp.position.X + p.position.X,
                        fp.position.Y + p.position.Y,
                        p.size.X / 2.0,
                        p.size.Y / 2.0,
                    )
                )

    via_in_pad_violations: list[str] = []
    qfn_thermal_vias = 0
    for item in board.traceItems:
        if isinstance(item, Via):
            vx, vy = float(item.position.X), float(item.position.Y)
            vr = float(item.drill) / 2.0
            if (dap_x - dap_hw <= vx <= dap_x + dap_hw) and (dap_y - dap_hh <= vy <= dap_y + dap_hh):
                qfn_thermal_vias += 1
                continue
            for label, px, py, hw, hh in smt_pads:
                if (px - hw - vr < vx < px + hw + vr) and (py - hh - vr < vy < py + hh + vr):
                    via_in_pad_violations.append(label)

    return {
        "usb_tvs_distance_mm": tvs_dist_mm,
        "usb_path_vias": ses_info["usb_path_vias"],
        "cc_nets_distinct": cc_distinct,
        "vbus_max_width_mm": round(max(vbus_widths, default=0.0), 3),
        "rf_max_width_mm": round(max(rf_widths, default=0.0), 3),
        "usb_widths_mm": sorted({round(w, 3) for w in usb_widths}),
        "qfn_thermal_vias": qfn_thermal_vias,
        "via_in_smt_pad_violations": via_in_pad_violations,
    }


def run_pipeline_b(outdir: Path = DEFAULT_OUTDIR) -> dict[str, Any]:
    """Build Board 1 in kiutils, run fastroute vs freerouting-cli bake-off, pour L2/L3 GND, and export Gerbers + schematic."""
    outdir.mkdir(parents=True, exist_ok=True)
    gerber_dir = outdir / "gerbers"
    gerber_dir.mkdir(parents=True, exist_ok=True)

    # 1. Build unrouted KiCad board
    board = build_board1_kicad()
    unrouted_pcb = outdir / "board_unrouted.kicad_pcb"
    board.to_file(str(unrouted_pcb))

    # 2. Export Specctra DSN (include B.Cu keepout under QFN thermal via grid
    #    and F.Cu keepout above C_IN1/C_IN2 for the 2.1mm VBUS bulk trunk)
    keepouts = [
        {**RF_KEEPOUT, "layers": ["F.Cu", "B.Cu"]},
        {
            "x_min": MCU_CENTER[0] - 1.15,
            "y_min": MCU_CENTER[1] - 1.15,
            "x_max": MCU_CENTER[0] + 1.15,
            "y_max": MCU_CENTER[1] + 1.15,
            "layers": ["B.Cu"],
        },
        {
            "x_min": 14.2,
            "y_min": 8.0,
            "x_max": 20.1,
            "y_max": 9.85,
            "layers": ["F.Cu"],
        },
    ]
    dsn_path = outdir / "board.dsn"
    board_to_dsn(
        board,
        dsn_path,
        netclasses=BOARD1_NETCLASSES,
        net_to_class=NET_TO_CLASS,
        board_bounds_mm=(0.0, 0.0, BOARD_W, BOARD_H),
        keepouts_mm=keepouts,
    )

    pairs_path = outdir / "usb_pairs.txt"
    pairs_path.write_text("pair USB_DP USB_DM gap=0.127 skew=0.10\n", encoding="utf-8")

    # 3A. Run Rust fastroute v0.1.14
    fr_ses = outdir / "board_fastroute.ses"
    fr_report_path = outdir / "fastroute_report.json"
    t0 = time.perf_counter()
    fr_proc = subprocess.run(
        [
            "fastroute",
            "-de",
            str(dsn_path),
            "-do",
            str(fr_ses),
            f"--report={fr_report_path}",
            "--no-neckdown-classes=DIFF_USB_90,RF_50",
            f"--pairs={pairs_path}",
            "--layer-heights=0.035,0.245,1.328,1.591",
            "--router.copper_to_edge_clearance_um=500",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    fr_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    if fr_proc.returncode != 0:
        raise RuntimeError(f"fastroute failed ({fr_proc.returncode}):\n{fr_proc.stderr}\n{fr_proc.stdout}")

    fr_report = json.loads(fr_report_path.read_text(encoding="utf-8"))
    fr_ses_info = parse_ses(fr_ses)
    fr_stats = fr_report.get("stats", {})
    fr_bench = RoutingBenchmark(
        engine="fastroute_v0.1.14",
        board_name="Board1_USB_PD_Telemetry",
        success=fr_stats.get("unrouted", 1) == 0 and fr_stats.get("violations", 1) == 0,
        runtime_ms=fr_ms,
        total_nets=int(fr_stats.get("nets", fr_ses_info["routed_nets"])),
        routed_nets=int(fr_stats.get("nets", 0)) - int(fr_stats.get("unrouted", 0)),
        unrouted_nets=int(fr_stats.get("unrouted", 0)),
        via_count=int(fr_stats.get("vias", fr_ses_info["via_count"])),
        total_wirelength_mm=float(fr_stats.get("trace_length_mm", fr_ses_info["total_wirelength_mm"])),
        notes=(
            f"violations={fr_stats.get('violations', 0)}, "
            f"usb_path_vias={fr_ses_info['usb_path_vias']}, "
            f"layers={fr_ses_info['layers_used']}"
        ),
    )

    # 3B. Run native freerouting-cli v2.5.0 on equivalent .dsn without ObstacleArea keepouts
    # (freerouting-cli v2.5.0 GraalVM binary lacks ObstacleArea/java.lang.Number serialization
    # config, so we strip (keepout ...) and run single-pass --router.autorouter.max_passes=1)
    free_dsn = outdir / "board_freerouting.dsn"
    board_to_dsn(
        board,
        free_dsn,
        netclasses=BOARD1_NETCLASSES,
        net_to_class=NET_TO_CLASS,
        board_bounds_mm=(0.0, 0.0, BOARD_W, BOARD_H),
        keepouts_mm=[],
    )
    free_ses = outdir / "board_freerouting.ses"
    t1 = time.perf_counter()
    free_proc = subprocess.run(
        [
            "freerouting-cli",
            "-de",
            str(free_dsn),
            "-do",
            str(free_ses),
            "--gui.enabled=false",
            "--router.copperToEdgeClearanceUm=500",
            "--router.autorouter.max_passes=1",
            "--router.optimizer.enabled=false",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    free_ms = round((time.perf_counter() - t1) * 1000.0, 2)
    if free_proc.returncode != 0 or not free_ses.exists():
        raise RuntimeError(
            f"freerouting-cli failed ({free_proc.returncode}):\n{free_proc.stderr}\n{free_proc.stdout}"
        )

    free_ses_info = parse_ses(free_ses)
    total_dsn_nets = int(fr_stats.get("nets", free_ses_info["routed_nets"]))
    free_unrouted = max(0, total_dsn_nets - free_ses_info["routed_nets"])
    for line in (free_proc.stdout + "\n" + free_proc.stderr).splitlines():
        if "final score:" in line and "unrouted" in line:
            import re as _re
            m_unr = _re.search(r"\((\d+)\s+unrouted", line)
            if m_unr:
                free_unrouted = max(free_unr if False else free_unrouted, int(m_unr.group(1)))
    free_bench = RoutingBenchmark(
        engine="freerouting_cli_v2.5.0",
        board_name="Board1_USB_PD_Telemetry",
        success=free_unrouted == 0,
        runtime_ms=free_ms,
        total_nets=total_dsn_nets,
        routed_nets=free_ses_info["routed_nets"],
        unrouted_nets=free_unrouted,
        via_count=free_ses_info["via_count"],
        total_wirelength_mm=free_ses_info["total_wirelength_mm"],
        notes=(
            f"usb_path_vias={free_ses_info['usb_path_vias']}, "
            f"layers={free_ses_info['layers_used']}, max_passes=1 "
            "(v2.5.0 GraalVM BoardSnapshotManager crashes if max_passes>1)"
        ),
    )

    # Include Pipeline A (@tscircuit/capacity-autorouter) if already built
    bakeoff_entries: list[dict[str, Any]] = [
        asdict(fr_bench),
        asdict(free_bench),
    ]
    tsc_report = PROJECT_ROOT / "artifacts" / "board1_tscircuit" / "tscircuit_report.json"
    if tsc_report.exists():
        tsc_data = json.loads(tsc_report.read_text(encoding="utf-8"))
        tphys = tsc_data.get("physics", {})
        trun = tsc_data.get("runner_summary", {})
        tsc_bench = RoutingBenchmark(
            engine="tscircuit_capacity_autorouter",
            board_name="Board1_USB_PD_Telemetry",
            success=trun.get("error_count", 1) == 0,
            runtime_ms=0.0,
            total_nets=int(tsc_data.get("kiutils_validation", {}).get("nets", 22)),
            routed_nets=int(tsc_data.get("kiutils_validation", {}).get("nets", 22)),
            unrouted_nets=0 if trun.get("error_count", 1) == 0 else 1,
            via_count=int(tphys.get("pcb_vias", 0)),
            total_wirelength_mm=float(tphys.get("total_wirelength_mm", 0.0)),
            notes=f"errors={trun.get('error_count', 0)}, usb_path_vias={tphys.get('usb_path_vias', 0)}",
        )
        bakeoff_entries.append(asdict(tsc_bench))

    bakeoff_payload = {
        "board": "Board1_USB_PD_Telemetry",
        "stackup": asdict(BOARD1_STACKUP),
        "selected_winner": fr_bench.engine if fr_bench.success else free_bench.engine,
        "benchmarks": bakeoff_entries,
    }
    save_json(outdir / "router_bakeoff.json", bakeoff_payload)

    # 4. Merge fastroute .ses back into kiutils Board + 2.1mm VBUS bulk trunk + Shapely L2/L3 GND Pours
    routed_board, merged_ses_info = merge_ses_into_board(board, fr_ses)
    import uuid as _uuid
    from kiutils.items.brditems import Segment as _Segment
    vbus_net_num = next((n.number for n in routed_board.nets if n.name == "VBUS"), 2)
    # Trunk sits in reserved F.Cu keepout (y=9.35 -> y_bot=10.40, >1.45mm clear of GND pads at y=12.45)
    for (x1, y1), (x2, y2), w in [
        ((15.5, 10.55), (15.5, 9.35), 1.2),
        ((15.5, 9.35), (18.8, 9.35), 2.1),
        ((18.8, 9.35), (18.8, 10.55), 1.2),
    ]:
        routed_board.traceItems.append(
            _Segment(
                start=Position(X=x1, Y=y1),
                end=Position(X=x2, Y=y2),
                width=w,
                layer="F.Cu",
                net=vbus_net_num,
                locked=True,
                tstamp=str(_uuid.uuid4()),
            )
        )
    poured_geom, pour_stats = _build_shapely_gnd_pours(routed_board)
    _inject_gnd_zones(routed_board, poured_geom)

    routed_pcb_path = outdir / "routed.kicad_pcb"
    routed_board.to_file(str(routed_pcb_path))

    # Re-parse with kiutils to verify round-trip integrity
    verified_board = Board.from_file(str(routed_pcb_path))
    physics = _check_physics_board1(verified_board, merged_ses_info)

    # 5. Export Gerbers & Excellon drills via gerbonara
    gerber_files = export_gerbers_gerbonara(verified_board, poured_geom, gerber_dir)

    # 6. Export schematic SVG via schemdraw
    sch_svg = generate_schematic_svg(outdir / "schematic.svg")

    summary = {
        "pipeline": "kicad_fastroute",
        "board": "Board1_USB_PD_Telemetry",
        "outdir": str(outdir),
        "stackup": BOARD1_STACKUP.id,
        "bakeoff": bakeoff_payload,
        "gnd_pour_stats": pour_stats,
        "physics": physics,
        "gerber_files": gerber_files,
        "schematic_svg": sch_svg.name,
        "routed_kicad_pcb": routed_pcb_path.name,
    }
    save_json(outdir / "pipeline_b_report.json", summary)
    return summary


if __name__ == "__main__":
    res = run_pipeline_b()
    print(json.dumps(res, indent=2))
