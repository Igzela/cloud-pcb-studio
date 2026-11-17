"""KiCad Footprint/PCB Serializer & Gerbonara RS-274X + Excellon Exporter for Board 2.

Exports:
  - planar_coil.kicad_mod
  - captouch_slider.kicad_mod
  - special_board.kicad_pcb (50 x 50 mm 4-layer board)
  - coil_params.json & special_coil_spice.json & board_manifest.json
  - gerbers/{F_Cu,In1_Cu,In2_Cu,B_Cu,F_Mask,B_Mask,F_SilkS,B_SilkS,Edge_Cuts}.gbr & PTH.drl
"""

from __future__ import annotations

import argparse
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from gerbonara.apertures import CircleAperture, RectangleAperture
from gerbonara.excellon import ExcellonFile, ExcellonTool
from gerbonara.graphic_objects import Arc as GerbArc
from gerbonara.graphic_objects import Flash as GerbFlash
from gerbonara.graphic_objects import Line as GerbLine
from gerbonara.graphic_objects import Region as GerbRegion
from gerbonara.rs274x import GerberFile
from gerbonara.utils import MM
from kiutils.board import Board, LayerToken
from kiutils.footprint import DrillDefinition, Footprint, Pad, PadOptions
from kiutils.items.brditems import Arc as KiArc
from kiutils.items.brditems import Segment as KiSegment
from kiutils.items.brditems import Via as KiVia
from kiutils.items.common import Effects, Font, Net, Position
from kiutils.items.fpitems import FpLine, FpText
from kiutils.items.gritems import GrArc, GrLine, GrPoly, GrText
from pygerber.gerberx3.api.v2 import GerberFile as PyGerberFile
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from contracts.schemas import save_json

from .captouch import CapTouchDesign, CapTouchSpec, build_captouch_slider
from .curved_routing import (
    ArcSegment,
    MeanderResult,
    StraightSegment,
    TracePath,
    chaikin_to_trace_path,
    fillet_polyline_with_arcs,
    generate_serpentine_meander,
)
from .planar_coil import (
    PlanarCoilDesign,
    PlanarCoilSpec,
    build_planar_coil,
    run_coil_spice_simulation,
)

BOARD_W_MM = 50.0
BOARD_H_MM = 50.0

# Minimal crisp 5x7 uppercase/digit vector stroke font in normalized [0..1] x [0..1.4] cell
_STROKE_FONT: dict[str, list[list[tuple[float, float]]]] = {
    "A": [[(0, 0), (0.5, 1.4), (1, 0)], [(0.2, 0.55), (0.8, 0.55)]],
    "B": [[(0, 0), (0, 1.4), (0.7, 1.4), (0.9, 1.2), (0.9, 0.9), (0.7, 0.7), (0, 0.7)], [(0.7, 0.7), (0.95, 0.5), (0.95, 0.2), (0.7, 0), (0, 0)]],
    "C": [[(1, 1.2), (0.7, 1.4), (0.3, 1.4), (0, 1.1), (0, 0.3), (0.3, 0), (0.7, 0), (1, 0.2)]],
    "D": [[(0, 0), (0, 1.4), (0.65, 1.4), (1, 1.05), (1, 0.35), (0.65, 0), (0, 0)]],
    "E": [[(1, 1.4), (0, 1.4), (0, 0), (1, 0)], [(0, 0.7), (0.7, 0.7)]],
    "F": [[(1, 1.4), (0, 1.4), (0, 0)], [(0, 0.7), (0.7, 0.7)]],
    "G": [[(1, 1.2), (0.7, 1.4), (0.3, 1.4), (0, 1.1), (0, 0.3), (0.3, 0), (0.8, 0), (1, 0.25), (1, 0.65), (0.55, 0.65)]],
    "H": [[(0, 0), (0, 1.4)], [(1, 0), (1, 1.4)], [(0, 0.7), (1, 0.7)]],
    "I": [[(0.2, 1.4), (0.8, 1.4)], [(0.5, 1.4), (0.5, 0)], [(0.2, 0), (0.8, 0)]],
    "J": [[(0.8, 1.4), (0.8, 0.3), (0.55, 0), (0.2, 0), (0, 0.25)]],
    "K": [[(0, 0), (0, 1.4)], [(1, 1.4), (0, 0.65), (1, 0)]],
    "L": [[(0, 1.4), (0, 0), (1, 0)]],
    "M": [[(0, 0), (0, 1.4), (0.5, 0.6), (1, 1.4), (1, 0)]],
    "N": [[(0, 0), (0, 1.4), (1, 0), (1, 1.4)]],
    "O": [[(0.3, 1.4), (0.7, 1.4), (1, 1.1), (1, 0.3), (0.7, 0), (0.3, 0), (0, 0.3), (0, 1.1), (0.3, 1.4)]],
    "P": [[(0, 0), (0, 1.4), (0.75, 1.4), (1, 1.15), (1, 0.9), (0.75, 0.65), (0, 0.65)]],
    "R": [[(0, 0), (0, 1.4), (0.75, 1.4), (1, 1.15), (1, 0.9), (0.75, 0.65), (0, 0.65)], [(0.55, 0.65), (1, 0)]],
    "S": [[(0.95, 1.2), (0.7, 1.4), (0.25, 1.4), (0, 1.15), (0.25, 0.8), (0.75, 0.6), (1, 0.25), (0.7, 0), (0.25, 0), (0, 0.2)]],
    "T": [[(0, 1.4), (1, 1.4)], [(0.5, 1.4), (0.5, 0)]],
    "U": [[(0, 1.4), (0, 0.3), (0.3, 0), (0.7, 0), (1, 0.3), (1, 1.4)]],
    "V": [[(0, 1.4), (0.5, 0), (1, 1.4)]],
    "W": [[(0, 1.4), (0.25, 0), (0.5, 0.7), (0.75, 0), (1, 1.4)]],
    "X": [[(0, 1.4), (1, 0)], [(0, 0), (1, 1.4)]],
    "Y": [[(0, 1.4), (0.5, 0.7), (1, 1.4)], [(0.5, 0.7), (0.5, 0)]],
    "Z": [[(0, 1.4), (1, 1.4), (0, 0), (1, 0)]],
    "0": [[(0.3, 1.4), (0.7, 1.4), (1, 1.1), (1, 0.3), (0.7, 0), (0.3, 0), (0, 0.3), (0, 1.1), (0.3, 1.4)], [(0.15, 0.2), (0.85, 1.2)]],
    "1": [[(0.25, 1.1), (0.55, 1.4), (0.55, 0)], [(0.2, 0), (0.9, 0)]],
    "2": [[(0.1, 1.15), (0.35, 1.4), (0.75, 1.4), (1, 1.15), (1, 0.85), (0, 0), (1, 0)]],
    "3": [[(0.1, 1.2), (0.35, 1.4), (0.75, 1.4), (1, 1.15), (0.75, 0.75), (0.4, 0.75)], [(0.75, 0.75), (1, 0.35), (0.75, 0), (0.3, 0), (0.1, 0.2)]],
    "4": [[(0.8, 0), (0.8, 1.4), (0, 0.45), (1, 0.45)]],
    "5": [[(0.95, 1.4), (0.1, 1.4), (0.1, 0.8), (0.75, 0.8), (1, 0.5), (1, 0.25), (0.7, 0), (0.2, 0), (0, 0.2)]],
    "6": [[(0.85, 1.3), (0.6, 1.4), (0.25, 1.4), (0, 1.0), (0, 0.3), (0.3, 0), (0.75, 0), (1, 0.25), (1, 0.6), (0.7, 0.8), (0, 0.8)]],
    "7": [[(0, 1.4), (1, 1.4), (0.35, 0)]],
    "8": [[(0.3, 1.4), (0.7, 1.4), (0.95, 1.1), (0.7, 0.75), (0.3, 0.75), (0.05, 1.1), (0.3, 1.4)], [(0.3, 0.75), (0, 0.45), (0, 0.2), (0.3, 0), (0.7, 0), (1, 0.2), (1, 0.45), (0.7, 0.75)]],
    "9": [[(1, 0.65), (0.25, 0.65), (0, 0.9), (0, 1.2), (0.3, 1.4), (0.75, 1.4), (1, 1.1), (1, 0.3), (0.7, 0), (0.25, 0)]],
    "+": [[(0.5, 0.2), (0.5, 1.2)], [(0.05, 0.7), (0.95, 0.7)]],
    "-": [[(0.1, 0.7), (0.9, 0.7)]],
    ":": [[(0.45, 0.95), (0.55, 0.95), (0.55, 0.85), (0.45, 0.85), (0.45, 0.95)], [(0.45, 0.25), (0.55, 0.25), (0.55, 0.15), (0.45, 0.15), (0.45, 0.25)]],
    ".": [[(0.45, 0.12), (0.55, 0.12), (0.55, 0.02), (0.45, 0.02), (0.45, 0.12)]],
}


def _vector_text_segments(
    text: str,
    x: float,
    y: float,
    char_h: float = 1.1,
    flip_y: bool = False,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Generate line segments for crisp stroke-font silkscreen rendering."""
    scale_x = char_h / 1.4 * 0.75
    scale_y = char_h / 1.4
    adv = scale_x * 1.45
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    cx = x
    for ch in text.upper():
        strokes = _STROKE_FONT.get(ch)
        if strokes:
            for poly in strokes:
                for i in range(len(poly) - 1):
                    px0 = cx + poly[i][0] * scale_x
                    py0 = y + ((-poly[i][1] if flip_y else poly[i][1]) * scale_y)
                    px1 = cx + poly[i + 1][0] * scale_x
                    py1 = y + ((-poly[i + 1][1] if flip_y else poly[i + 1][1]) * scale_y)
                    segs.append(((px0, py0), (px1, py1)))
        cx += adv
    return segs


def _save_gerber_clean(gf: GerberFile, out_path: Path) -> None:
    """Serialize GerberFile with gerbonara and normalize G75* / G01* statement terminators.

    Workaround for gerbonara 1.5.0 emitting `G75` without `*` and `G01**` inside regions,
    ensuring strict RS-274X compatibility across both `gerbonara` and `pygerber`.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw = gf.write_to_bytes().decode("utf-8")
    fixed_lines = []
    for line in raw.splitlines():
        if line == "G75":
            fixed_lines.append("G75*")
        elif "**" in line:
            fixed_lines.append(line.replace("**", "*"))
        else:
            fixed_lines.append(line)
    out_path.write_text("\n".join(fixed_lines) + "\n", encoding="utf-8")


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _create_planar_coil_footprint(coil: PlanarCoilDesign, include_copper_traces: bool = True) -> Footprint:
    """Create standalone or board-embedded KiCad Footprint for the 4-layer planar coil."""
    fp = Footprint.create_new("special_ip:planar_coil", "HAPTIC_COIL_4L", type="smd", reference="L1")
    fp.description = "55-turn 4-layer normal-offset rounded-racetrack electromagnetic haptic coil"
    fp.tags = "coil inductor haptic 4-layer planar"
    fp.netTiePadGroups.append("1,2")

    # Adjust reference/value silkscreen positions outside coil bbox
    minx, miny, maxx, maxy = coil.outer_bbox_local_mm
    for gi in fp.graphicItems:
        if isinstance(gi, FpText) and gi.type == "reference":
            gi.position = Position(X=0.0, Y=miny - 1.0, unlocked=True)
        elif isinstance(gi, FpText) and gi.type == "value":
            gi.position = Position(X=0.0, Y=maxy + 1.1, unlocked=True)

    pw, ph = coil.spec.terminal_pad_size_mm
    fp.pads.append(
        Pad(
            number="1",
            type="smd",
            shape="roundrect",
            roundrectRatio=0.25,
            position=Position(X=coil.pad1_local_mm[0], Y=coil.pad1_local_mm[1]),
            size=Position(X=pw, Y=ph),
            layers=["F.Cu", "F.Mask", "F.Paste"],
            solderMaskMargin=0.05,
        )
    )
    # Pad 2 is a plated through-hole pad connecting B.Cu coil exit cleanly up to F.Cu
    fp.pads.append(
        Pad(
            number="2",
            type="thru_hole",
            shape="roundrect",
            roundrectRatio=0.25,
            position=Position(X=coil.pad2_local_mm[0], Y=coil.pad2_local_mm[1]),
            size=Position(X=pw, Y=ph),
            drill=DrillDefinition(diameter=coil.spec.via_drill_mm),
            layers=["*.Cu", "*.Mask"],
            solderMaskMargin=0.05,
        )
    )

    if include_copper_traces:
        for lyr in coil.layers:
            pts = lyr.points_local_mm
            for i in range(len(pts) - 1):
                fp.graphicItems.append(
                    FpLine(
                        start=Position(X=pts[i][0], Y=pts[i][1]),
                        end=Position(X=pts[i + 1][0], Y=pts[i + 1][1]),
                        layer=lyr.layer,
                        width=coil.spec.trace_width_mm,
                    )
                )
        for v in coil.vias:
            fp.pads.append(
                Pad(
                    number="",
                    type="thru_hole",
                    shape="circle",
                    position=Position(X=v.x_mm, Y=v.y_mm),
                    size=Position(X=v.pad_mm, Y=v.pad_mm),
                    drill=DrillDefinition(diameter=v.drill_mm),
                    layers=["*.Cu"],
                )
            )

    # Courtyard rectangle on F.CrtYd
    pad_m = 0.5
    c_pts = [
        (minx - pad_m, miny - pad_m),
        (maxx + pad_m, miny - pad_m),
        (maxx + pad_m, maxy + pad_m),
        (minx - pad_m, maxy + pad_m),
        (minx - pad_m, miny - pad_m),
    ]
    for i in range(4):
        fp.graphicItems.append(
            FpLine(
                start=Position(X=c_pts[i][0], Y=c_pts[i][1]),
                end=Position(X=c_pts[i + 1][0], Y=c_pts[i + 1][1]),
                layer="F.CrtYd",
                width=0.05,
            )
        )
    return fp


def _create_captouch_footprint(ct: CapTouchDesign, include_bridges_in_fp: bool = True) -> Footprint:
    """Create standalone or board-embedded KiCad Footprint for the Chevron CapTouch Slider."""
    fp = Footprint.create_new("special_ip:captouch_slider", "CAPTOUCH_CHEVRON_4CH", type="smd", reference="TOUCH1")
    fp.description = "4-element interlocking chevron capacitive touch slider with B.Cu bridges and GND guard ring"
    fp.tags = "captouch chevron slider sensor"

    gx0, gy0 = ct.guard_ring_points_local_mm[0]
    gx1, gy1 = ct.guard_ring_points_local_mm[2]
    for gi in fp.graphicItems:
        if isinstance(gi, FpText) and gi.type == "reference":
            gi.position = Position(X=0.0, Y=gy1 + 1.1, unlocked=True)
        elif isinstance(gi, FpText) and gi.type == "value":
            gi.position = Position(X=0.0, Y=gy1 + 2.4, unlocked=True)

    for elec in ct.electrodes:
        vx, vy = elec.via_in_local_mm
        rel_pts = [Position(X=px - vx, Y=py - vy) for px, py in elec.polygon_local_mm]
        # Custom polygonal F.Cu electrode pad (mask-covered -> F.Cu only)
        fp.pads.append(
            Pad(
                number=str(elec.index + 1),
                type="smd",
                shape="custom",
                position=Position(X=vx, Y=vy),
                size=Position(X=ct.spec.via_pad_mm, Y=ct.spec.via_pad_mm),
                layers=["F.Cu"],
                customPadOptions=PadOptions(clearance="outline", anchor="circle"),
                customPadPrimitives=[
                    GrPoly(layer="F.Cu", coordinates=rel_pts, width=0.0, fill="solid")
                ],
            )
        )
        if include_bridges_in_fp:
            fp.pads.append(
                Pad(
                    number=str(elec.index + 1),
                    type="thru_hole",
                    shape="circle",
                    position=Position(X=vx, Y=vy),
                    size=Position(X=ct.spec.via_pad_mm, Y=ct.spec.via_pad_mm),
                    drill=DrillDefinition(diameter=ct.spec.via_drill_mm),
                    layers=["*.Cu"],
                )
            )
            fp.pads.append(
                Pad(
                    number=str(elec.index + 1),
                    type="thru_hole",
                    shape="circle",
                    position=Position(X=elec.via_out_local_mm[0], Y=elec.via_out_local_mm[1]),
                    size=Position(X=ct.spec.via_pad_mm, Y=ct.spec.via_pad_mm),
                    drill=DrillDefinition(diameter=ct.spec.via_drill_mm),
                    layers=["*.Cu"],
                )
            )
            for i in range(len(elec.bridge_points_local_mm) - 1):
                p0, p1 = elec.bridge_points_local_mm[i], elec.bridge_points_local_mm[i + 1]
                fp.graphicItems.append(
                    FpLine(
                        start=Position(X=p0[0], Y=p0[1]),
                        end=Position(X=p1[0], Y=p1[1]),
                        layer="B.Cu",
                        width=ct.spec.bridge_width_mm,
                    )
                )

    if include_bridges_in_fp:
        g_pts = ct.guard_ring_points_local_mm
        for i in range(len(g_pts) - 1):
            fp.graphicItems.append(
                FpLine(
                    start=Position(X=g_pts[i][0], Y=g_pts[i][1]),
                    end=Position(X=g_pts[i + 1][0], Y=g_pts[i + 1][1]),
                    layer="F.Cu",
                    width=ct.spec.guard_width_mm,
                )
            )
        for gv in ct.guard_vias_local_mm:
            fp.pads.append(
                Pad(
                    number="5",
                    type="thru_hole",
                    shape="circle",
                    position=Position(X=gv[0], Y=gv[1]),
                    size=Position(X=ct.spec.via_pad_mm, Y=ct.spec.via_pad_mm),
                    drill=DrillDefinition(diameter=ct.spec.via_drill_mm),
                    layers=["*.Cu"],
                )
            )

    # Courtyard outline on F.CrtYd
    c_pts = [
        (gx0 - 0.5, gy0 - 0.5),
        (gx1 + 0.5, gy0 - 0.5),
        (gx1 + 0.5, gy1 + 0.5),
        (gx0 - 0.5, gy1 + 0.5),
        (gx0 - 0.5, gy0 - 0.5),
    ]
    for i in range(4):
        fp.graphicItems.append(
            FpLine(
                start=Position(X=c_pts[i][0], Y=c_pts[i][1]),
                end=Position(X=c_pts[i + 1][0], Y=c_pts[i + 1][1]),
                layer="F.CrtYd",
                width=0.05,
            )
        )
    return fp


def _create_header_j1(nets_by_name: dict[str, Net]) -> Footprint:
    """Create 1x7 2.54mm driver & CapTouch interface header J1."""
    fp = Footprint.create_new("Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm", "HDR_1x7", type="through_hole", reference="J1")
    fp.position = Position(X=3.5, Y=6.0)
    pin_nets = ["COIL_P", "COIL_N", "TOUCH_3", "TOUCH_2", "TOUCH_1", "TOUCH_0", "GND"]
    for gi in fp.graphicItems:
        if isinstance(gi, FpText) and gi.type == "reference":
            gi.position = Position(X=0.0, Y=-1.7, unlocked=True)
        elif isinstance(gi, FpText) and gi.type == "value":
            gi.position = Position(X=0.0, Y=6 * 2.54 + 1.7, unlocked=True)

    for idx, nname in enumerate(pin_nets):
        fp.pads.append(
            Pad(
                number=str(idx + 1),
                type="thru_hole",
                shape="roundrect" if idx == 0 else "circle",
                roundrectRatio=0.20 if idx == 0 else None,
                position=Position(X=0.0, Y=idx * 2.54),
                size=Position(X=1.70, Y=1.70),
                drill=DrillDefinition(diameter=1.00),
                layers=["*.Cu", "*.Mask"],
                solderMaskMargin=0.05,
                net=nets_by_name[nname],
            )
        )
    # Silkscreen outline around J1
    box = [(-1.25, -1.25), (1.25, -1.25), (1.25, 6 * 2.54 + 1.25), (-1.25, 6 * 2.54 + 1.25), (-1.25, -1.25)]
    for i in range(4):
        fp.graphicItems.append(
            FpLine(start=Position(X=box[i][0], Y=box[i][1]), end=Position(X=box[i + 1][0], Y=box[i + 1][1]), layer="F.SilkS", width=0.15)
        )
    return fp


def _create_diode_d1(nets_by_name: dict[str, Net]) -> Footprint:
    """Create SOD-123 flyback Schottky catch diode D1 across COIL_P / COIL_N."""
    fp = Footprint.create_new("Diode_SMD:D_SOD-123", "SS14", type="smd", reference="D1")
    fp.position = Position(X=8.5, Y=7.27)
    for gi in fp.graphicItems:
        if isinstance(gi, FpText) and gi.type == "reference":
            gi.position = Position(X=0.0, Y=-2.8, unlocked=True)
        elif isinstance(gi, FpText) and gi.type == "value":
            gi.position = Position(X=0.0, Y=2.8, unlocked=True)

    fp.pads.append(
        Pad(
            number="1",
            type="smd",
            shape="roundrect",
            roundrectRatio=0.20,
            position=Position(X=0.0, Y=-1.60),
            size=Position(X=1.20, Y=1.20),
            layers=["F.Cu", "F.Mask", "F.Paste"],
            solderMaskMargin=0.05,
            net=nets_by_name["COIL_P"],
        )
    )
    fp.pads.append(
        Pad(
            number="2",
            type="smd",
            shape="roundrect",
            roundrectRatio=0.20,
            position=Position(X=0.0, Y=+1.60),
            size=Position(X=1.20, Y=1.20),
            layers=["F.Cu", "F.Mask", "F.Paste"],
            solderMaskMargin=0.05,
            net=nets_by_name["COIL_N"],
        )
    )
    # Cathode polarity bar on F.SilkS
    fp.graphicItems.append(FpLine(start=Position(X=-0.8, Y=-0.7), end=Position(X=0.8, Y=-0.7), layer="F.SilkS", width=0.18))
    return fp


@dataclass
class Board2Assembly:
    coil: PlanarCoilDesign
    captouch: CapTouchDesign
    board: Board
    coil_footprint: Footprint
    captouch_footprint: Footprint
    routed_traces: list[TracePath]
    meander_results: dict[str, MeanderResult]
    board_vias: list[dict[str, Any]]
    board_drc: dict[str, Any]
    silk_segments: list[tuple[tuple[float, float], tuple[float, float]]]


def _append_trace_path_to_board(board: Board, trace: TracePath, net_num: int) -> None:
    """Append native KiCad Segment & Arc items from a TracePath into board.traceItems."""
    for prim in trace.primitives:
        if isinstance(prim, StraightSegment):
            board.traceItems.append(
                KiSegment(
                    start=Position(X=prim.start[0], Y=prim.start[1]),
                    end=Position(X=prim.end[0], Y=prim.end[1]),
                    width=prim.width_mm,
                    layer=prim.layer,
                    net=net_num,
                    tstamp=_new_uuid(),
                )
            )
        else:
            board.traceItems.append(
                KiArc(
                    start=Position(X=prim.start[0], Y=prim.start[1]),
                    mid=Position(X=prim.mid[0], Y=prim.mid[1]),
                    end=Position(X=prim.end[0], Y=prim.end[1]),
                    width=prim.width_mm,
                    layer=prim.layer,
                    net=net_num,
                    tstamp=_new_uuid(),
                )
            )


def _trace_path_to_gerber_objs(trace: TracePath) -> list[GerbLine | GerbArc]:
    """Convert a TracePath to Gerbonara Line and true circular Arc objects using MM."""
    ap = CircleAperture(trace.width_mm, unit=MM)
    objs: list[GerbLine | GerbArc] = []
    for prim in trace.primitives:
        if isinstance(prim, StraightSegment):
            objs.append(GerbLine(prim.start[0], prim.start[1], prim.end[0], prim.end[1], aperture=ap, unit=MM))
        else:
            cx_rel = prim.center[0] - prim.start[0]
            cy_rel = prim.center[1] - prim.start[1]
            objs.append(
                GerbArc(
                    prim.start[0],
                    prim.start[1],
                    prim.end[0],
                    prim.end[1],
                    cx=cx_rel,
                    cy=cy_rel,
                    clockwise=prim.clockwise,
                    aperture=ap,
                    unit=MM,
                )
            )
    return objs


def assemble_board2(
    coil_spec: PlanarCoilSpec | None = None,
    ct_spec: CapTouchSpec | None = None,
) -> Board2Assembly:
    """Assemble the complete 50 x 50 mm 4-layer Board 2 design and verify board-level DRC."""
    coil = build_planar_coil(coil_spec)
    ct = build_captouch_slider(ct_spec)

    board = Board.create_new()
    board.general.thickness = 1.6
    # Insert inner copper layers In1.Cu (ordinal 1) and In2.Cu (ordinal 2)
    board.layers.insert(1, LayerToken(ordinal=1, name="In1.Cu", type="signal"))
    board.layers.insert(2, LayerToken(ordinal=2, name="In2.Cu", type="signal"))

    net_names = ["COIL_P", "COIL_N", "TOUCH_0", "TOUCH_1", "TOUCH_2", "TOUCH_3", "GND"]
    nets_by_name: dict[str, Net] = {"": board.nets[0]}
    for idx, nname in enumerate(net_names, start=1):
        net_obj = Net(number=idx, name=nname)
        board.nets.append(net_obj)
        nets_by_name[nname] = net_obj

    # Board Edge.Cuts (50 x 50 mm)
    edge_pts = [(0.0, 0.0), (BOARD_W_MM, 0.0), (BOARD_W_MM, BOARD_H_MM), (0.0, BOARD_H_MM), (0.0, 0.0)]
    for i in range(4):
        board.graphicItems.append(
            GrLine(
                start=Position(X=edge_pts[i][0], Y=edge_pts[i][1]),
                end=Position(X=edge_pts[i + 1][0], Y=edge_pts[i + 1][1]),
                layer="Edge.Cuts",
                width=0.10,
                tstamp=_new_uuid(),
            )
        )

    # Standalone footprints (complete self-contained geometry for .kicad_mod exports)
    coil_fp_standalone = _create_planar_coil_footprint(coil, include_copper_traces=True)
    ct_fp_standalone = _create_captouch_footprint(ct, include_bridges_in_fp=True)

    # Board footprints (L1 & TOUCH1 embed pads cleanly while board.traceItems holds segments/vias)
    j1_fp = _create_header_j1(nets_by_name)
    d1_fp = _create_diode_d1(nets_by_name)

    l1_board_fp = _create_planar_coil_footprint(coil, include_copper_traces=False)
    l1_board_fp.position = Position(X=coil.spec.center_x_mm, Y=coil.spec.center_y_mm)
    for pad in l1_board_fp.pads:
        if pad.number == "1":
            pad.net = nets_by_name["COIL_P"]
        elif pad.number == "2":
            pad.net = nets_by_name["COIL_N"]

    touch_board_fp = _create_captouch_footprint(ct, include_bridges_in_fp=False)
    touch_board_fp.position = Position(X=ct.spec.center_x_mm, Y=ct.spec.center_y_mm)
    for pad in touch_board_fp.pads:
        idx = int(pad.number) - 1
        pad.net = nets_by_name[f"TOUCH_{idx}"]

    board.footprints.extend([j1_fp, d1_fp, l1_board_fp, touch_board_fp])

    # Place 4-layer coil segments + vias in board coordinates
    cx_coil, cy_coil = coil.spec.center_x_mm, coil.spec.center_y_mm
    for lyr in coil.layers:
        pts_abs = [(cx_coil + px, cy_coil + py) for px, py in lyr.points_local_mm]
        for i in range(len(pts_abs) - 1):
            board.traceItems.append(
                KiSegment(
                    start=Position(X=pts_abs[i][0], Y=pts_abs[i][1]),
                    end=Position(X=pts_abs[i + 1][0], Y=pts_abs[i + 1][1]),
                    width=coil.spec.trace_width_mm,
                    layer=lyr.layer,
                    net=nets_by_name["COIL_P"].number,
                    tstamp=_new_uuid(),
                )
            )

    board_vias: list[dict[str, Any]] = []
    for v in coil.vias:
        vx, vy = cx_coil + v.x_mm, cy_coil + v.y_mm
        board.traceItems.append(
            KiVia(
                position=Position(X=vx, Y=vy),
                size=v.pad_mm,
                drill=v.drill_mm,
                layers=["F.Cu", "B.Cu"],
                net=nets_by_name["COIL_P"].number,
                tstamp=_new_uuid(),
            )
        )
        board_vias.append({"name": v.name, "x": vx, "y": vy, "size": v.pad_mm, "drill": v.drill_mm, "net": "COIL_P"})

    # Place CapTouch B.Cu bridges, vias, and GND guard ring in board.traceItems
    cx_ct, cy_ct = ct.spec.center_x_mm, ct.spec.center_y_mm
    for elec in ct.electrodes:
        net_num = nets_by_name[elec.net_name].number
        v_in = (cx_ct + elec.via_in_local_mm[0], cy_ct + elec.via_in_local_mm[1])
        v_out = (cx_ct + elec.via_out_local_mm[0], cy_ct + elec.via_out_local_mm[1])
        for vname, (vx, vy) in ((f"V_CT_IN_{elec.index}", v_in), (f"V_CT_OUT_{elec.index}", v_out)):
            board.traceItems.append(
                KiVia(
                    position=Position(X=vx, Y=vy),
                    size=ct.spec.via_pad_mm,
                    drill=ct.spec.via_drill_mm,
                    layers=["F.Cu", "B.Cu"],
                    net=net_num,
                    tstamp=_new_uuid(),
                )
            )
            board_vias.append({"name": vname, "x": vx, "y": vy, "size": ct.spec.via_pad_mm, "drill": ct.spec.via_drill_mm, "net": elec.net_name})
        # B.Cu jumper bridge trace
        b_pts = [(cx_ct + px, cy_ct + py) for px, py in elec.bridge_points_local_mm]
        for i in range(len(b_pts) - 1):
            board.traceItems.append(
                KiSegment(
                    start=Position(X=b_pts[i][0], Y=b_pts[i][1]),
                    end=Position(X=b_pts[i + 1][0], Y=b_pts[i + 1][1]),
                    width=ct.spec.bridge_width_mm,
                    layer="B.Cu",
                    net=net_num,
                    tstamp=_new_uuid(),
                )
            )

    # GND guard ring on F.Cu + GND stitching vias
    gnd_num = nets_by_name["GND"].number
    g_pts_abs = [(cx_ct + px, cy_ct + py) for px, py in ct.guard_ring_points_local_mm]
    for i in range(len(g_pts_abs) - 1):
        board.traceItems.append(
            KiSegment(
                start=Position(X=g_pts_abs[i][0], Y=g_pts_abs[i][1]),
                end=Position(X=g_pts_abs[i + 1][0], Y=g_pts_abs[i + 1][1]),
                width=ct.spec.guard_width_mm,
                layer="F.Cu",
                net=gnd_num,
                tstamp=_new_uuid(),
            )
        )
    for idx, gv in enumerate(ct.guard_vias_local_mm):
        vx, vy = cx_ct + gv[0], cy_ct + gv[1]
        board.traceItems.append(
            KiVia(
                position=Position(X=vx, Y=vy),
                size=ct.spec.via_pad_mm,
                drill=ct.spec.via_drill_mm,
                layers=["F.Cu", "B.Cu"],
                net=gnd_num,
                tstamp=_new_uuid(),
            )
        )
        board_vias.append({"name": f"V_GND_{idx}", "x": vx, "y": vy, "size": ct.spec.via_pad_mm, "drill": ct.spec.via_drill_mm, "net": "GND"})

    # --- Curved-Track & Serpentine Meander Feed Routing (F.Cu) ---
    routed_traces: list[TracePath] = []
    meander_results: dict[str, MeanderResult] = {}

    pad1_abs = (cx_coil + coil.pad1_local_mm[0], cy_coil + coil.pad1_local_mm[1])  # (14.96, 14.40)
    pad2_abs = (cx_coil + coil.pad2_local_mm[0], cy_coil + coil.pad2_local_mm[1])  # (14.96, 17.60)
    d1_cathode = (8.5, 5.67)
    d1_anode = (8.5, 8.87)

    # Obstacles for meander clearance checking (nearby pads + coil outer envelope)
    coil_f_geom = LineString([(cx_coil + px, cy_coil + py) for px, py in coil.layers[0].points_local_mm]).buffer(
        0.5 * coil.spec.trace_width_mm
    )
    j1_geoms = [Point(3.5, 6.0 + k * 2.54).buffer(0.85) for k in range(7)]
    d1_geoms = [Point(8.5, 5.67).buffer(0.60), Point(8.5, 8.87).buffer(0.60)]

    # 1. COIL_P & COIL_N from J1 -> D1 (Chaikin + Arc smoothed)
    tp_j1_d1_p = fillet_polyline_with_arcs(
        [(3.5, 6.00), (6.0, 6.00), (7.2, 5.67), d1_cathode],
        fillet_radius_mm=0.90,
        width_mm=0.35,
        layer="F.Cu",
        net_name="COIL_P",
    )
    tp_j1_d1_n = fillet_polyline_with_arcs(
        [(3.5, 8.54), (6.0, 8.54), (7.2, 8.87), d1_anode],
        fillet_radius_mm=0.90,
        width_mm=0.35,
        layer="F.Cu",
        net_name="COIL_N",
    )

    # 2. COIL_P & COIL_N length-matched differential feed meanders (target total = 22.00 mm)
    coil_feed_target = 22.00
    p_m_start = (9.9, 5.67)
    tp_p_pre = fillet_polyline_with_arcs(
        [d1_cathode, p_m_start],
        fillet_radius_mm=0.50,
        width_mm=0.35,
        layer="F.Cu",
        net_name="COIL_P",
    )
    tp_p_tail = fillet_polyline_with_arcs(
        [(14.96, 5.67), pad1_abs],
        fillet_radius_mm=0.60,
        width_mm=0.35,
        layer="F.Cu",
        net_name="COIL_P",
    )
    m_coil_p = generate_serpentine_meander(
        start=p_m_start,
        end=(14.96, 5.67),
        target_length_mm=coil_feed_target - tp_j1_d1_p.total_length_mm - tp_p_pre.total_length_mm - tp_p_tail.total_length_mm,
        width_mm=0.35,
        min_clearance_mm=0.22,
        max_amplitude_mm=1.35,
        layer="F.Cu",
        net_name="COIL_P",
        obstacles=[coil_f_geom, *d1_geoms, Point(13.4, 8.87).buffer(0.4)],
        symmetric=False,
        preferred_side=-1,  # Meander upward (-Y in board coords -> Y >= 4.2)
    )

    n_m_start = (13.4, 9.80)
    n_m_end = (13.4, 16.60)
    tp_n_pre = fillet_polyline_with_arcs(
        [d1_anode, (13.4, 8.87), n_m_start],
        fillet_radius_mm=0.60,
        width_mm=0.35,
        layer="F.Cu",
        net_name="COIL_N",
    )
    tp_n_tail = fillet_polyline_with_arcs(
        [n_m_end, (13.4, 17.60), pad2_abs],
        fillet_radius_mm=0.60,
        width_mm=0.35,
        layer="F.Cu",
        net_name="COIL_N",
    )
    m_coil_n = generate_serpentine_meander(
        start=n_m_start,
        end=n_m_end,
        target_length_mm=coil_feed_target - tp_j1_d1_n.total_length_mm - tp_n_pre.total_length_mm - tp_n_tail.total_length_mm,
        width_mm=0.35,
        min_clearance_mm=0.22,
        max_amplitude_mm=0.65,
        layer="F.Cu",
        net_name="COIL_N",
        obstacles=[coil_f_geom, *d1_geoms, Point(14.96, 14.40).buffer(0.6), LineString([(14.96, 5.67), pad1_abs]).buffer(0.25)],
        symmetric=True,
        preferred_side=-1,
    )
    meander_results["COIL_P"] = m_coil_p
    meander_results["COIL_N"] = m_coil_n

    for net_n, tp in (
        ("COIL_P", tp_j1_d1_p),
        ("COIL_P", tp_p_pre),
        ("COIL_P", m_coil_p.trace_path),
        ("COIL_P", tp_p_tail),
        ("COIL_N", tp_j1_d1_n),
        ("COIL_N", tp_n_pre),
        ("COIL_N", m_coil_n.trace_path),
        ("COIL_N", tp_n_tail),
    ):
        routed_traces.append(tp)
        _append_trace_path_to_board(board, tp, nets_by_name[net_n].number)

    # 3. CapTouch channels TOUCH_3..TOUCH_0 + GND guard feed
    # Lane X positions descending left side: 11.8 (TOUCH_3), 10.2 (TOUCH_2), 8.6 (TOUCH_1), 7.0 (TOUCH_0), 5.4 (GND)
    # Horizontal bus Y positions: 28.6 (TOUCH_3), 29.8 (TOUCH_2), 31.0 (TOUCH_1), 32.2 (TOUCH_0)
    ct_specs = [
        ("TOUCH_3", (3.5, 11.08), 11.8, 28.6, (cx_ct + ct.electrodes[3].via_out_local_mm[0], cy_ct + ct.electrodes[3].via_out_local_mm[1])),
        ("TOUCH_2", (3.5, 13.62), 10.2, 29.8, (cx_ct + ct.electrodes[2].via_out_local_mm[0], cy_ct + ct.electrodes[2].via_out_local_mm[1])),
        ("TOUCH_1", (3.5, 16.16), 8.6, 31.0, (cx_ct + ct.electrodes[1].via_out_local_mm[0], cy_ct + ct.electrodes[1].via_out_local_mm[1])),
        ("TOUCH_0", (3.5, 18.70), 7.0, 32.2, (cx_ct + ct.electrodes[0].via_out_local_mm[0], cy_ct + ct.electrodes[0].via_out_local_mm[1])),
    ]
    # Add serpentine delay-line tuning on TOUCH_0..TOUCH_2 (within 1.2mm/1.6mm lane corridors)
    # and smooth circular-arc routing on TOUCH_3.
    bus_obs = [coil_f_geom, *j1_geoms]
    for net_n, j_pt, lane_x, bus_y, v_out_pt in ct_specs:
        if net_n == "TOUCH_3":
            lead_tp = fillet_polyline_with_arcs(
                [j_pt, (lane_x, j_pt[1]), (lane_x, bus_y), (v_out_pt[0], bus_y), v_out_pt],
                fillet_radius_mm=0.90,
                width_mm=0.25,
                layer="F.Cu",
                net_name=net_n,
            )
            routed_traces.append(lead_tp)
            _append_trace_path_to_board(board, lead_tp, nets_by_name[net_n].number)
        elif net_n == "TOUCH_2":
            m_start = (15.0, bus_y)
            m_end = (v_out_pt[0] - 0.9, bus_y)
            lead_tp = fillet_polyline_with_arcs(
                [j_pt, (lane_x, j_pt[1]), (lane_x, bus_y), m_start],
                fillet_radius_mm=0.90,
                width_mm=0.25,
                layer="F.Cu",
                net_name=net_n,
            )
            tail_tp = fillet_polyline_with_arcs(
                [m_end, (v_out_pt[0], bus_y), v_out_pt],
                fillet_radius_mm=0.60,
                width_mm=0.25,
                layer="F.Cu",
                net_name=net_n,
            )
            direct_m = m_end[0] - m_start[0]
            m_res = generate_serpentine_meander(
                start=m_start,
                end=m_end,
                target_length_mm=direct_m + 1.80,
                width_mm=0.25,
                min_clearance_mm=0.20,
                max_amplitude_mm=0.40,
                layer="F.Cu",
                net_name=net_n,
                obstacles=bus_obs,
                symmetric=True,
                preferred_side=1,
            )
            meander_results[net_n] = m_res
            for tp in (lead_tp, m_res.trace_path, tail_tp):
                routed_traces.append(tp)
                _append_trace_path_to_board(board, tp, nets_by_name[net_n].number)
        else:
            # Put vertical serpentine meander along vertical lane_x (X spacing 1.6mm -> +/-0.55mm safe!)
            m_start = (lane_x, j_pt[1] + 1.2)
            m_end = (lane_x, bus_y - 1.2)
            lead_tp = fillet_polyline_with_arcs(
                [j_pt, (lane_x, j_pt[1]), m_start],
                fillet_radius_mm=0.80,
                width_mm=0.25,
                layer="F.Cu",
                net_name=net_n,
            )
            tail_tp = fillet_polyline_with_arcs(
                [m_end, (lane_x, bus_y), (v_out_pt[0], bus_y), v_out_pt],
                fillet_radius_mm=0.80,
                width_mm=0.25,
                layer="F.Cu",
                net_name=net_n,
            )
            direct_m = m_end[1] - m_start[1]
            extra = 2.40 if net_n == "TOUCH_0" else 1.50
            m_res = generate_serpentine_meander(
                start=m_start,
                end=m_end,
                target_length_mm=direct_m + extra,
                width_mm=0.25,
                min_clearance_mm=0.20,
                max_amplitude_mm=0.50,
                layer="F.Cu",
                net_name=net_n,
                obstacles=bus_obs,
                symmetric=True,
                preferred_side=1,
            )
            meander_results[net_n] = m_res
            for tp in (lead_tp, m_res.trace_path, tail_tp):
                routed_traces.append(tp)
                _append_trace_path_to_board(board, tp, nets_by_name[net_n].number)

    # GND trace from J1.7 (3.5, 21.24) -> Chaikin-smoothed curve -> guard ring feed
    gnd_feed_abs = (cx_ct + ct.guard_feed_local_mm[0], cy_ct + ct.guard_feed_local_mm[1])
    tp_gnd = chaikin_to_trace_path(
        [(3.5, 21.24), (5.4, 21.24), (5.4, 35.6), gnd_feed_abs],
        width_mm=0.40,
        layer="F.Cu",
        net_name="GND",
        iterations=3,
    )
    routed_traces.append(tp_gnd)
    _append_trace_path_to_board(board, tp_gnd, gnd_num)

    # --- Board Silkscreen Labels (placed cleanly away from pads & mask openings) ---
    silk_texts: list[tuple[str, float, float, float]] = [
        ("BOARD2: 4L COIL+CAPTOUCH", 15.0, 2.2, 1.15),
        ("J1", 2.7, 3.6, 1.0),
        ("D1", 7.8, 3.6, 1.0),
        ("L1", 28.8, 3.3, 1.1),
        ("TOUCH1", 22.0, 46.8, 1.1),
        ("+", 1.7, 5.4, 1.0),
        ("1", 1.7, 6.2, 0.75),
        ("7", 1.7, 21.4, 0.75),
    ]
    silk_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for txt, tx, ty, ch in silk_texts:
        board.graphicItems.append(
            GrText(
                text=txt,
                position=Position(X=tx, Y=ty),
                layer="F.SilkS",
                effects=Effects(font=Font(height=ch, width=ch * 0.8, thickness=0.15)),
                tstamp=_new_uuid(),
            )
        )
        silk_segments.extend(_vector_text_segments(txt, tx, ty, char_h=ch, flip_y=False))

    # Native KiCad GrArc decorative alignment emblem on silkscreen (upper-right corner)
    board.graphicItems.append(
        GrArc(
            start=Position(X=45.0, Y=4.0),
            mid=Position(X=46.414, Y=5.414),
            end=Position(X=45.0, Y=6.828),
            layer="F.SilkS",
            width=0.15,
            tstamp=_new_uuid(),
        )
    )

    # --- Full Multi-Layer Board-Level Shapely DRC ---
    board_drc = _verify_board_shapely_drc(board, coil, ct, routed_traces, board_vias)

    return Board2Assembly(
        coil=coil,
        captouch=ct,
        board=board,
        coil_footprint=coil_fp_standalone,
        captouch_footprint=ct_fp_standalone,
        routed_traces=routed_traces,
        meander_results=meander_results,
        board_vias=board_vias,
        board_drc=board_drc,
        silk_segments=silk_segments,
    )


def _verify_board_shapely_drc(
    board: Board,
    coil: PlanarCoilDesign,
    ct: CapTouchDesign,
    routed_traces: list[TracePath],
    board_vias: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run comprehensive Shapely DRC across all 4 copper layers, drills, and 50x50mm edge."""
    copper_by_layer_net: dict[str, dict[str, list[Any]]] = {
        lyr: {} for lyr in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")
    }

    def add_geom(layer: str, net_name: str, geom: Any) -> None:
        copper_by_layer_net[layer].setdefault(net_name, []).append(geom)

    # 1. Coil traces (all treated as COIL_SERIES net; Pad 1 & Pad 2 tested separately too)
    cx_c, cy_c = coil.spec.center_x_mm, coil.spec.center_y_mm
    for lyr in coil.layers:
        pts_abs = [(cx_c + px, cy_c + py) for px, py in lyr.points_local_mm]
        add_geom(lyr.layer, "COIL_SERIES", LineString(pts_abs).buffer(0.5 * coil.spec.trace_width_mm))

    # 2. Vias span all 4 layers
    for v in board_vias:
        nname = "COIL_SERIES" if v["net"] in ("COIL_P", "COIL_N") and v["name"].startswith("V_COIL") else v["net"]
        v_geom = Point(v["x"], v["y"]).buffer(0.5 * v["size"])
        for lyr in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"):
            add_geom(lyr, nname, v_geom)

    # 3. Footprint pads
    # J1
    pin_nets = ["COIL_P", "COIL_N", "TOUCH_3", "TOUCH_2", "TOUCH_1", "TOUCH_0", "GND"]
    for idx, nname in enumerate(pin_nets):
        p_geom = Point(3.5, 6.0 + idx * 2.54).buffer(0.85)
        for lyr in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"):
            add_geom(lyr, nname, p_geom)

    # D1
    add_geom("F.Cu", "COIL_P", Polygon([(7.9, 5.07), (9.1, 5.07), (9.1, 6.27), (7.9, 6.27)]))
    add_geom("F.Cu", "COIL_N", Polygon([(7.9, 8.27), (9.1, 8.27), (9.1, 9.47), (7.9, 9.47)]))

    # L1 terminal pads
    p1x, p1y = cx_c + coil.pad1_local_mm[0], cy_c + coil.pad1_local_mm[1]
    p2x, p2y = cx_c + coil.pad2_local_mm[0], cy_c + coil.pad2_local_mm[1]
    pw, ph = coil.spec.terminal_pad_size_mm
    add_geom("F.Cu", "COIL_P", Polygon([(p1x - pw/2, p1y - ph/2), (p1x + pw/2, p1y - ph/2), (p1x + pw/2, p1y + ph/2), (p1x - pw/2, p1y + ph/2)]))
    p2_poly = Polygon([(p2x - pw/2, p2y - ph/2), (p2x + pw/2, p2y - ph/2), (p2x + pw/2, p2y + ph/2), (p2x - pw/2, p2y + ph/2)])
    for lyr in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"):
        add_geom(lyr, "COIL_N", p2_poly)

    # CapTouch chevrons & B.Cu bridges & GND guard ring
    cx_t, cy_t = ct.spec.center_x_mm, ct.spec.center_y_mm
    for elec in ct.electrodes:
        poly_abs = Polygon([(cx_t + px, cy_t + py) for px, py in elec.polygon_local_mm])
        add_geom("F.Cu", elec.net_name, poly_abs)
        b_abs = LineString([(cx_t + px, cy_t + py) for px, py in elec.bridge_points_local_mm]).buffer(0.5 * ct.spec.bridge_width_mm)
        add_geom("B.Cu", elec.net_name, b_abs)

    g_abs = LineString([(cx_t + px, cy_t + py) for px, py in ct.guard_ring_points_local_mm]).buffer(0.5 * ct.spec.guard_width_mm)
    add_geom("F.Cu", "GND", g_abs)

    # Routed traces
    for tp in routed_traces:
        add_geom(tp.layer, tp.net_name, tp.to_shapely().buffer(0.5 * tp.width_mm))

    # Measure inter-net minimum clearances on every copper layer
    min_inter_net_clr = 999.0
    worst_pair = ""
    all_copper_geoms: list[Any] = []
    for lyr, nets_dict in copper_by_layer_net.items():
        net_keys = list(nets_dict.keys())
        unions = {k: unary_union(v) for k, v in nets_dict.items()}
        all_copper_geoms.extend(unions.values())
        for i in range(len(net_keys)):
            for j in range(i + 1, len(net_keys)):
                k1, k2 = net_keys[i], net_keys[j]
                # Skip COIL_SERIES vs COIL_P/COIL_N net-tie intentional junction at L1 pads
                if "COIL_SERIES" in (k1, k2) and ({"COIL_P", "COIL_N"} & {k1, k2}):
                    continue
                d = float(unions[k1].distance(unions[k2]))
                if d < min_inter_net_clr:
                    min_inter_net_clr = d
                    worst_pair = f"{lyr}:{k1}<->{k2}"

    # Check copper-to-edge clearance across entire 50 x 50 mm board
    board_union = unary_union(all_copper_geoms)
    bminx, bminy, bmaxx, bmaxy = board_union.bounds
    min_edge_clr = float(min(bminx, BOARD_W_MM - bmaxx, bminy, BOARD_H_MM - bmaxy))

    # Check drill-to-drill edge clearance
    drills = [(3.5, 6.0 + idx * 2.54, 1.00) for idx in range(7)]
    drills.append((p2x, p2y, coil.spec.via_drill_mm))
    for v in board_vias:
        drills.append((v["x"], v["y"], v["drill"]))
    min_drill_hole_wall_clr = 999.0
    for i in range(len(drills)):
        for j in range(i + 1, len(drills)):
            d_wall = math.hypot(drills[i][0] - drills[j][0], drills[i][1] - drills[j][1]) - 0.5 * (drills[i][2] + drills[j][2])
            min_drill_hole_wall_clr = min(min_drill_hole_wall_clr, d_wall)

    passed = (
        min_inter_net_clr >= 0.15
        and min_edge_clr >= 0.50
        and coil.min_turn_clearance_mm >= 0.20
        and ct.min_electrode_clearance_mm >= 0.18
        and min_drill_hole_wall_clr >= 0.50
    )

    return {
        "passed": passed,
        "min_inter_net_clearance_mm": round(min_inter_net_clr, 4),
        "worst_inter_net_pair": worst_pair,
        "min_copper_to_edge_mm": round(min_edge_clr, 4),
        "min_drill_wall_clearance_mm": round(min_drill_hole_wall_clr, 4),
        "coil_min_turn_clearance_mm": round(coil.min_turn_clearance_mm, 4),
        "captouch_min_electrode_clearance_mm": round(ct.min_electrode_clearance_mm, 4),
        "copper_bounds_mm": [round(bminx, 4), round(bminy, 4), round(bmaxx, 4), round(bmaxy, 4)],
    }


def _build_gerber_files(asm: Board2Assembly) -> tuple[dict[str, GerberFile], ExcellonFile]:
    """Build RS-274X GerberFile objects (all using gerbonara.utils.MM) and Excellon PTH.drl."""
    coil = asm.coil
    ct = asm.captouch
    cx_c, cy_c = coil.spec.center_x_mm, coil.spec.center_y_mm
    cx_t, cy_t = ct.spec.center_x_mm, ct.spec.center_y_mm

    layers: dict[str, GerberFile] = {
        name: GerberFile()
        for name in ("F_Cu", "In1_Cu", "In2_Cu", "B_Cu", "F_Mask", "B_Mask", "F_SilkS", "B_SilkS", "Edge_Cuts")
    }

    # 1. Coil traces on F_Cu, In1_Cu, In2_Cu, B_Cu
    coil_ap = CircleAperture(coil.spec.trace_width_mm, unit=MM)
    for lyr in coil.layers:
        g_name = lyr.layer.replace(".", "_")
        pts_abs = [(cx_c + px, cy_c + py) for px, py in lyr.points_local_mm]
        for i in range(len(pts_abs) - 1):
            layers[g_name].objects.append(
                GerbLine(pts_abs[i][0], pts_abs[i][1], pts_abs[i + 1][0], pts_abs[i + 1][1], aperture=coil_ap, unit=MM)
            )

    # 2. Vias across all 4 copper layers
    for v in asm.board_vias:
        v_ap = CircleAperture(v["size"], unit=MM)
        for cu in ("F_Cu", "In1_Cu", "In2_Cu", "B_Cu"):
            layers[cu].objects.append(GerbFlash(v["x"], v["y"], aperture=v_ap, unit=MM))

    # 3. J1 7-pin PTH header pads (copper + mask openings on F_Mask & B_Mask)
    j1_cu_ap = CircleAperture(1.70, unit=MM)
    j1_sq_ap = RectangleAperture(1.70, 1.70, unit=MM)
    j1_mask_ap = CircleAperture(1.80, unit=MM)
    j1_sq_mask_ap = RectangleAperture(1.80, 1.80, unit=MM)
    for idx in range(7):
        px, py = 3.5, 6.0 + idx * 2.54
        cu_ap = j1_sq_ap if idx == 0 else j1_cu_ap
        mk_ap = j1_sq_mask_ap if idx == 0 else j1_mask_ap
        for cu in ("F_Cu", "In1_Cu", "In2_Cu", "B_Cu"):
            layers[cu].objects.append(GerbFlash(px, py, aperture=cu_ap, unit=MM))
        layers["F_Mask"].objects.append(GerbFlash(px, py, aperture=mk_ap, unit=MM))
        layers["B_Mask"].objects.append(GerbFlash(px, py, aperture=mk_ap, unit=MM))

    # 4. D1 SOD-123 SMD pads on F_Cu + F_Mask
    d1_cu_ap = RectangleAperture(1.20, 1.20, unit=MM)
    d1_mk_ap = RectangleAperture(1.30, 1.30, unit=MM)
    for py in (5.67, 8.87):
        layers["F_Cu"].objects.append(GerbFlash(8.5, py, aperture=d1_cu_ap, unit=MM))
        layers["F_Mask"].objects.append(GerbFlash(8.5, py, aperture=d1_mk_ap, unit=MM))

    # 5. L1 Terminal pads (Pad 1 SMD F_Cu/F_Mask, Pad 2 PTH *.Cu/*.Mask)
    pw, ph = coil.spec.terminal_pad_size_mm
    l1_cu_ap = RectangleAperture(pw, ph, unit=MM)
    l1_mk_ap = RectangleAperture(pw + 0.10, ph + 0.10, unit=MM)
    p1x, p1y = cx_c + coil.pad1_local_mm[0], cy_c + coil.pad1_local_mm[1]
    p2x, p2y = cx_c + coil.pad2_local_mm[0], cy_c + coil.pad2_local_mm[1]
    layers["F_Cu"].objects.append(GerbFlash(p1x, p1y, aperture=l1_cu_ap, unit=MM))
    layers["F_Mask"].objects.append(GerbFlash(p1x, p1y, aperture=l1_mk_ap, unit=MM))
    for cu in ("F_Cu", "In1_Cu", "In2_Cu", "B_Cu"):
        layers[cu].objects.append(GerbFlash(p2x, p2y, aperture=l1_cu_ap, unit=MM))
    layers["F_Mask"].objects.append(GerbFlash(p2x, p2y, aperture=l1_mk_ap, unit=MM))
    layers["B_Mask"].objects.append(GerbFlash(p2x, p2y, aperture=l1_mk_ap, unit=MM))

    # 6. CapTouch chevrons on F_Cu (Regions), B_Cu jumper bridges, F_Cu GND guard ring
    bridge_ap = CircleAperture(ct.spec.bridge_width_mm, unit=MM)
    for elec in ct.electrodes:
        poly_abs = [(cx_t + px, cy_t + py) for px, py in elec.polygon_local_mm]
        layers["F_Cu"].objects.append(GerbRegion(poly_abs, unit=MM))
        b_abs = [(cx_t + px, cy_t + py) for px, py in elec.bridge_points_local_mm]
        for i in range(len(b_abs) - 1):
            layers["B_Cu"].objects.append(
                GerbLine(b_abs[i][0], b_abs[i][1], b_abs[i + 1][0], b_abs[i + 1][1], aperture=bridge_ap, unit=MM)
            )

    guard_ap = CircleAperture(ct.spec.guard_width_mm, unit=MM)
    g_abs = [(cx_t + px, cy_t + py) for px, py in ct.guard_ring_points_local_mm]
    for i in range(len(g_abs) - 1):
        layers["F_Cu"].objects.append(
            GerbLine(g_abs[i][0], g_abs[i][1], g_abs[i + 1][0], g_abs[i + 1][1], aperture=guard_ap, unit=MM)
        )

    # 7. Routed curved & meandered feed traces (F_Cu)
    for tp in asm.routed_traces:
        g_name = tp.layer.replace(".", "_")
        layers[g_name].objects.extend(_trace_path_to_gerber_objs(tp))

    # 8. Silkscreen (F_SilkS + B_SilkS)
    silk_ap = CircleAperture(0.15, unit=MM)
    for p0, p1 in asm.silk_segments:
        layers["F_SilkS"].objects.append(GerbLine(p0[0], p0[1], p1[0], p1[1], aperture=silk_ap, unit=MM))
    # J1 & D1 silkscreen outlines
    j1_box = [(2.25, 4.75), (4.75, 4.75), (4.75, 22.49), (2.25, 22.49), (2.25, 4.75)]
    for i in range(4):
        layers["F_SilkS"].objects.append(GerbLine(j1_box[i][0], j1_box[i][1], j1_box[i + 1][0], j1_box[i + 1][1], aperture=silk_ap, unit=MM))
    layers["F_SilkS"].objects.append(GerbLine(7.7, 6.57, 9.3, 6.57, aperture=silk_ap, unit=MM))
    # Decorative silkscreen arc on F_SilkS matching GrArc
    layers["F_SilkS"].objects.append(GerbArc(45.0, 4.0, 45.0, 6.8284, cx=0.0, cy=1.4142, clockwise=False, aperture=silk_ap, unit=MM))
    # Back silkscreen board label
    for p0, p1 in _vector_text_segments("B.CU JUMPER+COIL", 15.0, 47.0, char_h=1.0):
        layers["B_SilkS"].objects.append(GerbLine(p0[0], p0[1], p1[0], p1[1], aperture=silk_ap, unit=MM))

    # 9. Edge_Cuts (50.0 x 50.0 mm closed outline)
    edge_ap = CircleAperture(0.10, unit=MM)
    edge_pts = [(0.0, 0.0), (BOARD_W_MM, 0.0), (BOARD_W_MM, BOARD_H_MM), (0.0, BOARD_H_MM), (0.0, 0.0)]
    for i in range(4):
        layers["Edge_Cuts"].objects.append(
            GerbLine(edge_pts[i][0], edge_pts[i][1], edge_pts[i + 1][0], edge_pts[i + 1][1], aperture=edge_ap, unit=MM)
        )

    # 10. Excellon PTH.drl
    excellon = ExcellonFile()
    t_via = ExcellonTool(coil.spec.via_drill_mm, plated=True, unit=MM)
    t_hdr = ExcellonTool(1.00, plated=True, unit=MM)
    for idx in range(7):
        excellon.objects.append(GerbFlash(3.5, 6.0 + idx * 2.54, aperture=t_hdr, unit=MM))
    excellon.objects.append(GerbFlash(p2x, p2y, aperture=t_via, unit=MM))
    for v in asm.board_vias:
        tool = t_via if abs(v["drill"] - coil.spec.via_drill_mm) < 1e-4 else ExcellonTool(v["drill"], plated=True, unit=MM)
        excellon.objects.append(GerbFlash(v["x"], v["y"], aperture=tool, unit=MM))

    return layers, excellon


def export_board2_artifacts(
    output_dir: str | Path = "artifacts/board2_special",
    run_spice: bool = True,
) -> dict[str, Any]:
    """Build and export all Board 2 artifacts, KiCad files, Gerbers, and ngspice verification."""
    out = Path(output_dir)
    gerb_dir = out / "gerbers"
    out.mkdir(parents=True, exist_ok=True)
    gerb_dir.mkdir(parents=True, exist_ok=True)

    asm = assemble_board2()

    # Save KiCad footprints and assembled PCB
    coil_mod_path = out / "planar_coil.kicad_mod"
    ct_mod_path = out / "captouch_slider.kicad_mod"
    pcb_path = out / "special_board.kicad_pcb"

    asm.coil_footprint.to_file(str(coil_mod_path))
    asm.captouch_footprint.to_file(str(ct_mod_path))
    asm.board.to_file(str(pcb_path))

    # Save Gerber & Excellon fabrication files
    gerber_files, excellon_file = _build_gerber_files(asm)
    gerber_paths: dict[str, str] = {}
    roundtrip_checks: dict[str, dict[str, Any]] = {}

    for layer_name, gf in gerber_files.items():
        gbr_path = gerb_dir / f"{layer_name}.gbr"
        _save_gerber_clean(gf, gbr_path)
        gerber_paths[layer_name] = str(gbr_path)

        # Roundtrip verification in both gerbonara and pygerber
        gb_parsed = GerberFile.open(str(gbr_path))
        pg_parsed = PyGerberFile.from_file(str(gbr_path)).parse()
        pg_info = pg_parsed.get_info()
        roundtrip_checks[layer_name] = {
            "gerbonara_objects": len(gb_parsed.objects),
            "pygerber_width_mm": float(pg_info.width_mm),
            "pygerber_height_mm": float(pg_info.height_mm),
        }

    drl_path = gerb_dir / "PTH.drl"
    excellon_file.save(str(drl_path))
    drl_parsed = ExcellonFile.open(str(drl_path))
    raw_hits = drl_parsed.hit_count()
    roundtrip_checks["PTH_drl"] = {
        "total_hits": int(sum(raw_hits.values())),
        "hits_by_drill_mm": {f"{float(t.diameter):.3f}": int(cnt) for t, cnt in raw_hits.items()},
        "drill_sizes_mm": sorted(round(float(d), 3) for d in drl_parsed.drill_sizes()),
    }

    # Save coil params + ngspice simulation JSON
    coil_params_path = out / "coil_params.json"
    save_json(coil_params_path, asm.coil.to_params_dict())

    spice_path = out / "special_coil_spice.json"
    spice_summary: dict[str, Any] = {}
    if run_spice:
        spice_summary = run_coil_spice_simulation(asm.coil, output_json_path=spice_path)

    manifest = {
        "board_name": "board2_special_coil_captouch",
        "dimensions_mm": [BOARD_W_MM, BOARD_H_MM],
        "layers": 4,
        "kicad_files": {
            "planar_coil_mod": str(coil_mod_path),
            "captouch_slider_mod": str(ct_mod_path),
            "special_board_pcb": str(pcb_path),
        },
        "gerber_files": gerber_paths,
        "excellon_file": str(drl_path),
        "coil_params_json": str(coil_params_path),
        "spice_json": str(spice_path) if run_spice else "",
        "coil_metrics": {
            "total_turns": asm.coil.total_turns,
            "total_length_mm": round(asm.coil.total_length_mm, 3),
            "r_dc_25c_ohm": round(asm.coil.r_dc_25c_ohm, 4),
            "inductance_constructive_uh": round(asm.coil.inductance_constructive_uh, 4),
            "inductance_canceling_uh": round(asm.coil.inductance_canceling_counterfactual_uh, 4),
            "constructive_flux_verified": asm.coil.constructive_flux_verified,
            "min_turn_clearance_mm": round(asm.coil.min_turn_clearance_mm, 4),
        },
        "captouch_metrics": asm.captouch.to_summary_dict(),
        "meanders": {
            name: {
                "target_length_mm": round(res.target_length_mm, 3),
                "actual_length_mm": round(res.actual_length_mm, 3),
                "length_error_mm": round(res.length_error_mm, 4),
                "cycles": res.meander_cycles,
                "amplitude_mm": round(res.amplitude_mm, 3),
                "min_obstacle_clearance_mm": round(res.min_obstacle_clearance_mm, 3),
                "min_self_clearance_mm": round(res.min_self_clearance_mm, 3),
                "clearance_ok": res.clearance_ok,
            }
            for name, res in asm.meander_results.items()
        },
        "board_drc": asm.board_drc,
        "gerber_roundtrip": roundtrip_checks,
    }
    manifest_path = out / "board_manifest.json"
    save_json(manifest_path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Board 2 Special IP PCB & Gerbers")
    parser.add_argument("--out", default="artifacts/board2_special", help="Output directory")
    parser.add_argument("--skip-spice", action="store_true", help="Skip ngspice run")
    args = parser.parse_args()
    manifest = export_board2_artifacts(args.out, run_spice=not args.skip_spice)
    print("Exported Board 2 artifacts to:", args.out)
    print("Board DRC:", manifest["board_drc"])
    print("Coil metrics:", manifest["coil_metrics"])


if __name__ == "__main__":
    main()
