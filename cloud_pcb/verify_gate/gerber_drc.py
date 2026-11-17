"""Independent Gerber, Excellon, KiCad LVS, Spec, and SPICE verification gate.

Strict zero-fake-pass policy:
- RS-274X Gerbers are parsed with `pygerber` (`pygerber.gerberx3.api.v2.GerberFile`)
  AND `gerbonara.rs274x.GerberFile` for Shapely geometry extraction.
- Excellon `.drl` drill files are parsed exclusively with
  `gerbonara.excellon.ExcellonFile` (NEVER `pygerber`).
- Every rule violation emits a structured `VerifyTicket` with exact measured vs
  required values and owning worker.
"""

from __future__ import annotations

import json
import math
import warnings
from pathlib import Path
from typing import Any

from gerbonara.apertures import CircleAperture, ObroundAperture, RectangleAperture
from gerbonara.excellon import ExcellonFile
from gerbonara.graphic_objects import Arc as GbArc
from gerbonara.graphic_objects import Flash as GbFlash
from gerbonara.graphic_objects import Line as GbLine
from gerbonara.graphic_objects import Region as GbRegion
from gerbonara.rs274x import GerberFile as GbGerberFile
from kiutils.board import Board
from kiutils.items.brditems import Arc as KiArc
from kiutils.items.brditems import Segment as KiSegment
from kiutils.items.brditems import Via as KiVia
from pygerber.gerberx3.api.v2 import GerberFile as PyGerberFile
from shapely.affinity import rotate as shp_rotate
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from contracts.schemas import GateReport, VerifyTicket

COPPER_LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")


def _fval(val: Any, default: float = 0.0) -> float:
    return float(val) if val is not None else default


class _TicketCollector:
    """Tracks check counts and builds structured VerifyTickets."""

    def __init__(self, board: str, default_owner: str) -> None:
        self.board = board
        self.default_owner = default_owner
        self.checks_run = 0
        self.checks_passed = 0
        self.tickets: list[VerifyTicket] = []
        self._seq = 0

    def check(
        self,
        passed: bool,
        gate: str,
        message: str,
        measured_value: Any,
        required_value: Any,
        *,
        severity: str = "CRITICAL",
        owner_worker: str | None = None,
        artifact_path: str = "",
    ) -> bool:
        self.checks_run += 1
        if passed:
            self.checks_passed += 1
            return True
        self._seq += 1
        prefix = self.board.upper().replace("-", "_").replace(" ", "_")
        self.tickets.append(
            VerifyTicket(
                ticket_id=f"{prefix}-{self._seq:03d}",
                severity=severity,  # type: ignore[arg-type]
                owner_worker=(owner_worker or self.default_owner),  # type: ignore[arg-type]
                board=self.board,
                gate=gate,
                message=message,
                measured_value=str(measured_value),
                required_value=str(required_value),
                artifact_path=artifact_path,
            )
        )
        return False

    def build_report(self, metrics: dict[str, Any]) -> GateReport:
        crit = sum(1 for t in self.tickets if t.severity == "CRITICAL")
        high = sum(1 for t in self.tickets if t.severity == "HIGH")
        return GateReport(
            board=self.board,
            passed=(crit == 0 and high == 0),
            checks_run=self.checks_run,
            checks_passed=self.checks_passed,
            critical_count=crit,
            high_count=high,
            metrics=metrics,
            tickets=self.tickets,
        )


def _gerbonara_obj_to_shapely(obj: Any) -> Any:
    """Convert a gerbonara graphic object into a Shapely geometry (in mm)."""
    if isinstance(obj, GbLine):
        ap = obj.aperture
        w = getattr(ap, "diameter", None)
        if w is None:
            w = min(float(getattr(ap, "w", 0.1)), float(getattr(ap, "h", 0.1)))
        r = max(float(w) / 2.0, 1e-4)
        return LineString([(float(obj.x1), float(obj.y1)), (float(obj.x2), float(obj.y2))]).buffer(r)
    if isinstance(obj, GbArc):
        segs = list(obj.approximate(0.05))
        if segs:
            pts = [(float(segs[0].x1), float(segs[0].y1))] + [
                (float(s.x2), float(s.y2)) for s in segs
            ]
        else:
            pts = [(float(obj.x1), float(obj.y1)), (float(obj.x2), float(obj.y2))]
        ap = obj.aperture
        w = getattr(ap, "diameter", None) or getattr(ap, "w", 0.1)
        r = max(float(w) / 2.0, 1e-4)
        return LineString(pts).buffer(r)
    if isinstance(obj, GbFlash):
        ap = obj.aperture
        x, y = float(obj.x), float(obj.y)
        if isinstance(ap, CircleAperture):
            return Point(x, y).buffer(max(float(ap.diameter) / 2.0, 1e-4))
        if isinstance(ap, RectangleAperture):
            return box(x - float(ap.w) / 2.0, y - float(ap.h) / 2.0, x + float(ap.w) / 2.0, y + float(ap.h) / 2.0)
        if isinstance(ap, ObroundAperture):
            w, h = float(ap.w), float(ap.h)
            r = min(w, h) / 2.0
            if w >= h:
                dx = w / 2.0 - r
                return LineString([(x - dx, y), (x + dx, y)]).buffer(max(r, 1e-4))
            dy = h / 2.0 - r
            return LineString([(x, y - dy), (x, y + dy)]).buffer(max(r, 1e-4))
        bb = obj.bounding_box()
        return box(float(bb[0][0]), float(bb[0][1]), float(bb[1][0]), float(bb[1][1]))
    if isinstance(obj, GbRegion):
        pts = [(float(x), float(y)) for x, y in obj.outline]
        if len(pts) >= 3:
            poly = Polygon(pts)
            return poly if poly.is_valid else poly.buffer(0)
    return None


def _gerber_layer_union(gbr_path: Path) -> tuple[Any, list[Any], list[float]]:
    """Load Gerber via gerbonara and return (net_dark_union, flashes, trace_widths_mm)."""
    gb = GbGerberFile.open(gbr_path)
    batch: list[Any] = []
    polarity: bool | None = None
    copper=Polygon()
    flashes: list[Any] = []
    widths: list[float] = []
    for obj in gb.objects:
        if isinstance(obj, GbFlash):
            flashes.append(obj)
        elif isinstance(obj, (GbLine, GbArc)):
            d = getattr(obj.aperture, "diameter", None)
            if d is not None and float(d) > 0:
                widths.append(float(d))
        geom = _gerbonara_obj_to_shapely(obj)
        if geom is None or geom.is_empty:
            continue
        if polarity is not None and obj.polarity_dark!=polarity:
            group=unary_union(batch)
            copper=copper.union(group) if polarity else copper.difference(group)
            batch=[]
        polarity=obj.polarity_dark
        batch.append(geom)
    if batch:
        group=unary_union(batch)
        copper=copper.union(group) if polarity else copper.difference(group)
    return copper, flashes, widths


def _strict_pygerber_parse(
    gbr_path: Path,
    tc: _TicketCollector,
    gate_prefix: str,
) -> tuple[bool, Any]:
    """Strictly parse a Gerber RS-274X file with pygerber (never silently patch raw errors)."""
    if not gbr_path.exists() or gbr_path.stat().st_size == 0:
        tc.check(
            False,
            f"{gate_prefix}_exists",
            f"Missing or empty RS-274X file: {gbr_path.name}",
            "missing_or_empty",
            "non-empty RS-274X file",
            severity="CRITICAL",
            artifact_path=str(gbr_path),
        )
        return False, None
    try:
        parsed = PyGerberFile.from_file(gbr_path).parse()
        info = parsed.get_info()
        tc.check(
            True,
            f"{gate_prefix}_pygerber_parse",
            f"pygerber parsed {gbr_path.name} cleanly",
            "parsed_ok",
            "parsed_ok",
            artifact_path=str(gbr_path),
        )
        return True, info
    except Exception as exc:
        err_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        tc.check(
            False,
            f"{gate_prefix}_pygerber_parse",
            f"pygerber strict RS-274X parse failed on {gbr_path.name}: {err_line}",
            f"{type(exc).__name__}: {err_line}",
            "strict RS-274X parseable by pygerber",
            severity="CRITICAL",
            artifact_path=str(gbr_path),
        )
        return False, None


def _edge_cuts_centerline_bounds(edge_path: Path) -> tuple[float, float, float, float] | None:
    """Compute board outline centerline bounds (min_x, min_y, max_x, max_y) in mm."""
    if not edge_path.exists() or edge_path.stat().st_size == 0:
        return None
    gb = GbGerberFile.open(edge_path)
    xs: list[float] = []
    ys: list[float] = []
    for obj in gb.objects:
        if isinstance(obj, (GbLine, GbArc)):
            xs.extend([float(obj.x1), float(obj.x2)])
            ys.extend([float(obj.y1), float(obj.y2)])
        elif isinstance(obj, GbRegion):
            for x, y in obj.outline:
                xs.append(float(x))
                ys.append(float(y))
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _open_excellon(drl_path: Path) -> ExcellonFile:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ExcellonFile.open(drl_path)


def _compute_min_annular_ring(
    pth: ExcellonFile, f_flashes: list[Any], b_flashes: list[Any]
) -> tuple[float | None, list[dict[str, Any]]]:
    """Verify annular ring = (pad_min_dim - drill_dia) / 2 on F_Cu and B_Cu."""
    min_ar: float | None = None
    violations: list[dict[str, Any]] = []
    for hole in pth.objects:
        hd = float(hole.aperture.diameter)
        hx, hy = float(hole.x), float(hole.y)
        for lname, flashes in (("F_Cu", f_flashes), ("B_Cu", b_flashes)):
            if not flashes:
                continue
            best = min(flashes, key=lambda f: (float(f.x) - hx) ** 2 + (float(f.y) - hy) ** 2)
            dist = math.hypot(float(best.x) - hx, float(best.y) - hy)
            if dist > 0.08:
                continue
            ap = best.aperture
            if isinstance(ap, CircleAperture):
                pad_d = float(ap.diameter)
            elif isinstance(ap, RectangleAperture):
                pad_d = min(float(ap.w), float(ap.h))
            elif isinstance(ap, ObroundAperture):
                pad_d = min(float(ap.w), float(ap.h))
            else:
                bb = best.bounding_box()
                pad_d = min(float(bb[1][0]) - float(bb[0][0]), float(bb[1][1]) - float(bb[0][1]))
            ar = (pad_d - hd) / 2.0
            if min_ar is None or ar < min_ar:
                min_ar = ar
            if ar < 0.10 - 1e-6:
                violations.append(
                    {
                        "layer": lname,
                        "x": round(hx, 3),
                        "y": round(hy, 3),
                        "drill_mm": round(hd, 3),
                        "pad_mm": round(pad_d, 3),
                        "annular_ring_mm": round(ar, 4),
                    }
                )
    return min_ar, violations


def audit_annular_geometry(pth: ExcellonFile,front: Any,back: Any) -> tuple[float | None,list[dict[str,Any]]]:
    """Measure actual minimum copper around every drilled circle or slot.

    Unlike nearest-flash matching, missing pads and drill offsets cannot be
    skipped, and region-based land shapes are covered too.
    """
    minimum=None;violations=[]
    for hole in pth.objects:
        radius=float(hole.aperture.diameter)/2
        if isinstance(hole,GbLine): centerline=LineString([(hole.x1,hole.y1),(hole.x2,hole.y2)])
        else: centerline=Point(hole.x,hole.y)
        for layer,copper in (('F.Cu',front),('B.Cu',back)):
            ring=float(centerline.distance(copper.boundary))-radius if not copper.is_empty and copper.covers(centerline) else -radius
            minimum=ring if minimum is None else min(minimum,ring)
            if ring<.10-1e-6: violations.append(dict(layer=layer,annular_ring_mm=ring,drill_diameter_mm=2*radius))
    return minimum,violations


def _check_kicad_drc_and_lvs(
    pcb_path: Path,
    *,
    allow_coil_bridge: bool = False,
    allow_electrode_vip: bool = False,
) -> dict[str, Any]:
    """Run independent Shapely topological LVS + via-in-pad + inter-net clearance on a KiCad PCB."""
    board = Board.from_file(str(pcb_path))
    net_names = {int(n.number): str(n.name) for n in board.nets}
    layer_net_geoms: dict[str, dict[int, list[Any]]] = {l: {} for l in COPPER_LAYERS}
    pad_nodes: dict[int, list[tuple[str, set[str], Any]]] = {}
    non_thermal_smt: list[tuple[str, str, str, Any]] = []

    for fidx, fp in enumerate(board.footprints):
        fx, fy = _fval(fp.position.X), _fval(fp.position.Y)
        ang = _fval(fp.position.angle)
        rad = math.radians(-ang)
        ca, sa = math.cos(rad), math.sin(rad)
        fp_name = str(fp.entryName or fp.libId or f"FP{fidx}")
        is_captouch_fp = "captouch" in fp_name.lower()

        fp_cu_pts = [
            (_fval(cp.position.X), _fval(cp.position.Y))
            for cp in fp.pads
            if any(l in COPPER_LAYERS or l == "*.Cu" for l in (cp.layers or []))
        ]
        if len(fp_cu_pts) >= 6:
            fp_min_x = min(pt[0] for pt in fp_cu_pts)
            fp_max_x = max(pt[0] for pt in fp_cu_pts)
            fp_min_y = min(pt[1] for pt in fp_cu_pts)
            fp_max_y = max(pt[1] for pt in fp_cu_pts)
        else:
            fp_min_x = fp_max_x = fp_min_y = fp_max_y = 0.0

        for p in fp.pads:
            layers = set(p.layers or [])
            if "*.Cu" in layers:
                layers.update(COPPER_LAYERS)
            cu_layers = layers & set(COPPER_LAYERS)
            if not cu_layers:
                continue
            px, py = _fval(p.position.X), _fval(p.position.Y)
            gx = fx + px * ca - py * sa
            gy = fy + px * sa + py * ca
            sx, sy = _fval(p.size.X), _fval(p.size.Y)
            pang = float(p.position.angle or 0.0)
            if p.shape == "circle":
                geom = Point(gx, gy).buffer(max(sx / 2.0, 1e-4))
            elif p.shape == "oval":
                r = max(min(sx, sy) / 2.0, 1e-4)
                if sx >= sy:
                    dx = max(sx / 2.0 - r, 0.0)
                    geom = LineString([(gx - dx, gy), (gx + dx, gy)]).buffer(r)
                else:
                    dy = max(sy / 2.0 - r, 0.0)
                    geom = LineString([(gx, gy - dy), (gx, gy + dy)]).buffer(r)
                if abs(pang) > 1e-6:
                    geom = shp_rotate(geom, -pang, origin=(gx, gy))
            elif p.shape == "roundrect":
                rr = min(sx, sy) * float(getattr(p, "roundrectRatio", None) or 0.25)
                rr = max(0.0, min(rr, min(sx, sy) / 2.0 - 1e-4))
                if rr > 1e-4:
                    geom = box(
                        gx - sx / 2.0 + rr,
                        gy - sy / 2.0 + rr,
                        gx + sx / 2.0 - rr,
                        gy + sy / 2.0 - rr,
                    ).buffer(rr)
                else:
                    geom = box(gx - sx / 2.0, gy - sy / 2.0, gx + sx / 2.0, gy + sy / 2.0)
                if abs(pang) > 1e-6:
                    geom = shp_rotate(geom, -pang, origin=(gx, gy))
            else:
                geom = box(gx - sx / 2.0, gy - sy / 2.0, gx + sx / 2.0, gy + sy / 2.0)
                if abs(pang) > 1e-6:
                    geom = shp_rotate(geom, -pang, origin=(gx, gy))
            net_num = int(p.net.number) if p.net else 0
            net_name = str(p.net.name) if p.net else ""

            is_interior_epad = (
                len(fp_cu_pts) >= 6
                and sx >= 1.6
                and sy >= 1.6
                and (fp_min_x + 0.35 <= px <= fp_max_x - 0.35)
                and (fp_min_y + 0.35 <= py <= fp_max_y - 0.35)
            )
            is_thermal = (
                is_interior_epad
                or (sx >= 2.2 and sy >= 2.2)
                or (str(p.number) in ("49", "EP", "EPAD", "0") and sx >= 2.0)
            )
            is_electrode = allow_electrode_vip and is_captouch_fp
            if (
                p.type == "smd"
                and not is_thermal
                and not is_electrode
                and ("F.Cu" in cu_layers or "B.Cu" in cu_layers)
            ):
                non_thermal_smt.append((fp_name, str(p.number), net_name, geom))

            if net_num > 0:
                for lyr in cu_layers:
                    layer_net_geoms[lyr].setdefault(net_num, []).append(geom)
                pad_nodes.setdefault(net_num, []).append((f"{fidx}:{p.number}", cu_layers, geom))

    vias = [t for t in board.traceItems if isinstance(t, KiVia)]
    vip_hits: list[dict[str, Any]] = []
    for v in vias:
        vp = Point(_fval(v.position.X), _fval(v.position.Y)).buffer(_fval(v.size) / 2.0)
        for fp_name, pnum, nname, pgeom in non_thermal_smt:
            if vp.intersects(pgeom):
                vip_hits.append(
                    {
                        "footprint": fp_name,
                        "pad": pnum,
                        "net": nname,
                        "x": round(_fval(v.position.X), 3),
                        "y": round(_fval(v.position.Y), 3),
                    }
                )

    for t in board.traceItems:
        if isinstance(t, KiSegment) and int(t.net) > 0 and t.layer in COPPER_LAYERS:
            g = LineString(
                [(_fval(t.start.X), _fval(t.start.Y)), (_fval(t.end.X), _fval(t.end.Y))]
            ).buffer(max(_fval(t.width) / 2.0, 1e-4))
            layer_net_geoms[t.layer].setdefault(int(t.net), []).append(g)
        elif isinstance(t, KiArc) and int(t.net) > 0 and t.layer in COPPER_LAYERS:
            g = LineString(
                [
                    (_fval(t.start.X), _fval(t.start.Y)),
                    (_fval(t.mid.X), _fval(t.mid.Y)),
                    (_fval(t.end.X), _fval(t.end.Y)),
                ]
            ).buffer(max(_fval(t.width) / 2.0, 1e-4))
            layer_net_geoms[t.layer].setdefault(int(t.net), []).append(g)
        elif isinstance(t, KiVia) and int(t.net) > 0:
            g = Point(_fval(t.position.X), _fval(t.position.Y)).buffer(max(_fval(t.size) / 2.0, 1e-4))
            for lyr in COPPER_LAYERS:
                layer_net_geoms[lyr].setdefault(int(t.net), []).append(g)

    for z in board.zones:
        if int(z.net) > 0 and z.layers:
            for lyr in z.layers:
                if lyr not in COPPER_LAYERS:
                    continue
                if getattr(z, "filledPolygons", None):
                    for fp in z.filledPolygons:
                        fp_lyr = getattr(fp, "layer", None)
                        if fp_lyr and fp_lyr != lyr:
                            continue
                        pts = [(_fval(pt.X), _fval(pt.Y)) for pt in fp.coordinates]
                        if len(pts) >= 3:
                            layer_net_geoms[lyr].setdefault(int(z.net), []).append(Polygon(pts).buffer(0))
                elif getattr(z, "polygons", None):
                    for poly in z.polygons:
                        pts = [(_fval(pt.X), _fval(pt.Y)) for pt in poly.coordinates]
                        if len(pts) >= 3:
                            layer_net_geoms[lyr].setdefault(int(z.net), []).append(Polygon(pts).buffer(0))

    shorts: list[dict[str, Any]] = []
    min_clearance_mm: float | None = None
    worst_pair: str = ""
    for lyr in ("F.Cu", "B.Cu"):
        net_unions = {n: unary_union(gs) for n, gs in layer_net_geoms[lyr].items() if gs}
        nlist = sorted(net_unions.keys())
        for i in range(len(nlist)):
            for j in range(i + 1, len(nlist)):
                n1, n2 = nlist[i], nlist[j]
                name1, name2 = net_names.get(n1, str(n1)), net_names.get(n2, str(n2))
                if allow_coil_bridge and {name1, name2} == {"COIL_P", "COIL_N"}:
                    continue
                g1, g2 = net_unions[n1], net_unions[n2]
                d = float(g1.distance(g2))
                if min_clearance_mm is None or d < min_clearance_mm:
                    min_clearance_mm = d
                    worst_pair = f"{lyr}:{name1}<->{name2}"
                if d == 0.0:
                    area = float(g1.intersection(g2).area)
                    if area > 1e-4:
                        shorts.append(
                            {
                                "layer": lyr,
                                "net1": name1,
                                "net2": name2,
                                "overlap_mm2": round(area, 5),
                            }
                        )

    opens: list[dict[str, Any]] = []
    for n, nodes in pad_nodes.items():
        if len(nodes) <= 1:
            continue
        items = [(lyr, g) for lyr in COPPER_LAYERS for g in layer_net_geoms[lyr].get(n, [])]
        parent = list(range(len(items)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        by_layer: dict[str, list[tuple[int, Any]]] = {}
        for idx, (lyr, g) in enumerate(items):
            by_layer.setdefault(lyr, []).append((idx, g))
        for lyr, litems in by_layer.items():
            for i in range(len(litems)):
                idx1, g1 = litems[i]
                b1 = g1.bounds
                for j in range(i + 1, len(litems)):
                    idx2, g2 = litems[j]
                    b2 = g2.bounds
                    if (
                        b1[2] < b2[0] - 0.01
                        or b2[2] < b1[0] - 0.01
                        or b1[3] < b2[1] - 0.01
                        or b2[3] < b1[1] - 0.01
                    ):
                        continue
                    if g1.distance(g2) <= 0.01:
                        union(idx1, idx2)

        for v in vias:
            if int(v.net) == n:
                vp = Point(_fval(v.position.X), _fval(v.position.Y))
                touching = [idx for idx, (_, g) in enumerate(items) if g.distance(vp) <= 0.05]
                for k in range(1, len(touching)):
                    union(touching[0], touching[k])

        for _, clayers, pgeom in nodes:
            if len(clayers) > 1:
                c = pgeom.centroid
                touching = [
                    idx for idx, (lyr, g) in enumerate(items) if lyr in clayers and g.distance(c) <= 0.05
                ]
                for k in range(1, len(touching)):
                    union(touching[0], touching[k])

        pad_roots: set[Any] = set()
        for pid, clayers, pgeom in nodes:
            c = pgeom.centroid
            l0 = next(iter(clayers))
            match = [idx for idx, (lyr, g) in enumerate(items) if lyr == l0 and g.distance(c) <= 0.01]
            pad_roots.add(find(match[0]) if match else ("missing", pid))
        if len(pad_roots) > 1:
            opens.append(
                {
                    "net": net_names.get(n, str(n)),
                    "pad_count": len(nodes),
                    "components": len(pad_roots),
                }
            )

    return {
        "footprints": len(board.footprints),
        "vias": len(vias),
        "via_in_pad_violations": vip_hits,
        "lvs_shorts": shorts,
        "lvs_opens": opens,
        "min_inter_net_clearance_mm": round(min_clearance_mm, 4) if min_clearance_mm is not None else None,
        "worst_clearance_pair": worst_pair,
    }


def classify_router_violations(report: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Only exclude evidenced same-footprint, same-net intersecting pads.

    Ground pads omitted from the router network can appear to be different
    unassigned nets there. Retain raw records and proof; never exempt a whole
    component, net class, or unevidenced missing violation record.
    """
    comps={c['refdes']:c for c in spec['components']}; real=[]; excluded=[]
    for violation in report.get('clearance_violations',[]):
        a,b=violation.get('first',{}),violation.get('second',{})
        proof=None
        if a.get('kind')==b.get('kind')=='pin' and a.get('component')==b.get('component'):
            c=comps.get(a.get('component'),{})
            p=next((p for p in c.get('pads',[]) if p['num']==a.get('pin')),None)
            q=next((p for p in c.get('pads',[]) if p['num']==b.get('pin')),None)
            if p and q and p.get('net') and p.get('net')==q.get('net'):
                def shape(p):
                    from cloud_pcb.general_solver.semantics import local_pad_polygon
                    return local_pad_polygon(p)
                overlap=shape(p).intersection(shape(q)).area
                if overlap>1e-8: proof=dict(net=p['net'],local_overlap_mm2=overlap,component=c['refdes'])
        if proof: excluded.append(dict(violation=violation,proof=proof))
        else: real.append(violation)
    raw=int(report.get('stats',{}).get('violations',len(real)+len(excluded)))
    # Missing detailed records cannot be silently counted as cleared violations.
    return dict(raw_count=raw,excluded_same_net_pad_overlaps=len(excluded),real_count=max(raw-len(excluded),len(real)),excluded=excluded,real=real)


def audit_general_board(board: Board, spec: dict[str, Any], placed: dict[str, Any], keepouts: list[dict[str, Any]]) -> dict[str, Any]:
    """Layer-resolved copper LVS. Cross-layer joins require actual plated features.

    Keep each disconnected filled polygon separate; do not project all layers
    onto a single plane, or assume every same-net zone island is connected.
    """
    from collections import defaultdict
    from kiutils.items.brditems import Segment, Via, Arc
    from shapely.geometry import MultiPoint
    from cloud_pcb.general_solver.run_blind import _pad_world_polygon_kicad
    from cloud_pcb.general_solver.semantics import mechanical_check
    from shapely.strtree import STRtree
    copper={l:defaultdict(list) for l in COPPER_LAYERS}; plated=defaultdict(list); terminals=defaultdict(list)
    names={n.number:n.name for n in board.nets}
    for fp in board.footprints:
        for p in fp.pads:
            if p.type=='np_thru_hole' or not p.net or not p.net.name: continue
            layers=set(COPPER_LAYERS) if '*.Cu' in p.layers else set(p.layers)&set(COPPER_LAYERS)
            if not layers: continue
            g=_pad_world_polygon_kicad(fp,p); n=p.net.name
            for l in layers: copper[l][n].append(g)
            terminals[n].append((layers,g.centroid,f"{fp.properties.get('Reference','?')}-{p.number}"))
            if p.type=='thru_hole': plated[n].append((layers,g))
    for t in board.traceItems:
        n=names.get(t.net,'')
        if not n: continue
        if isinstance(t,Segment):
            copper[t.layer][n].append(LineString([(t.start.X,t.start.Y),(t.end.X,t.end.Y)]).buffer(t.width/2))
        elif isinstance(t,Arc):
            from cloud_pcb.general_solver.geometry import copper_arc_points
            copper[t.layer][n].append(LineString(copper_arc_points(t)).buffer(t.width/2))
        elif isinstance(t,Via):
            g=Point(t.position.X,t.position.Y).buffer(t.size/2)
            start,end=[COPPER_LAYERS.index(l) for l in t.layers]
            layers=set(COPPER_LAYERS[min(start,end):max(start,end)+1])
            for l in layers: copper[l][n].append(g)
            plated[n].append((layers,g))
    for z in board.zones:
        if z.keepoutSettings: continue
        n=names.get(z.net,z.netName)
        for poly in z.filledPolygons:
            if poly.layer in copper and len(poly.coordinates)>=3:
                g=Polygon([(p.X,p.Y) for p in poly.coordinates]).buffer(0)
                copper[poly.layer][n].append(g)
    shorts=[]; unions={l:{n:unary_union(gs) for n,gs in m.items()} for l,m in copper.items()}
    for l,m in unions.items():
        entries=list(m.items())
        for i,(n,g) in enumerate(entries):
            for other,og in entries[i+1:]:
                area=g.intersection(og).area
                if area>1e-6: shorts.append(dict(layer=l,net_a=n,net_b=other,overlap_mm2=round(area,6)))
    opens=[]
    for n,pads in terminals.items():
        if len(pads)<2: continue
        nodes=[]
        for l,m in unions.items():
            g=m.get(n)
            if g is None or g.is_empty: continue
            pieces=list(g.geoms) if hasattr(g,'geoms') else [g]
            nodes.extend((l,p) for p in pieces if not p.is_empty)
        parent=list(range(len(nodes)))
        def find(i):
            while parent[i]!=i: parent[i]=parent[parent[i]]; i=parent[i]
            return i
        def join(ids):
            if ids:
                root=find(ids[0])
                for i in ids[1:]: parent[find(i)]=root
        for layers,g in plated[n]:
            join([i for i,(l,p) in enumerate(nodes) if l in layers and p.intersection(g).area>1e-7])
        roots=set(); pad_groups=defaultdict(list)
        for layers,pt,label in pads:
            ids=[i for i,(l,p) in enumerate(nodes) if l in layers and p.distance(pt)<=0.0002]
            roots.update(find(i) for i in ids)
            for i in ids: pad_groups[str(find(i))].append(label)
            if not ids: roots.add(('missing',len(roots)))
        if len(roots)>1: opens.append(dict(net=n,components=len(roots),pad_count=len(pads),pad_groups=dict(pad_groups)))
    rf=[]
    for ko in keepouts:
        if ko.get('kind')!='rf': continue
        region=box(ko['x_min'],ko['y_min'],ko['x_max'],ko['y_max'])
        for l in COPPER_LAYERS:
            covered=unary_union(list(unions[l].values())).intersection(region).area
            rf.append(dict(refdes=ko.get('refdes'),layer=l,copper_mm2=round(covered,8),coverage_fraction=covered/region.area if region.area else None,passed=covered<=1e-8))
    vip=[]
    # Use the existing gate's proven thermal-pad classification as additional evidence.
    for t in board.traceItems:
        if not isinstance(t,Via): continue
        vg=Point(t.position.X,t.position.Y).buffer(t.size/2)
        for fp in board.footprints:
            ps=[p for p in fp.pads if p.type=='smd' and any(l.endswith('.Cu') for l in p.layers)]
            for p in ps:
                local=Point(p.position.X,p.position.Y)
                centers=[Point(q.position.X,q.position.Y) for q in ps if q is not p]
                thermal=(len(ps)>=6 and centers and MultiPoint([(q.x,q.y) for q in centers]).convex_hull.contains(local) and min(p.size.X,p.size.Y)>=0.8 and p.net and p.net.number==t.net)
                if not thermal and vg.intersection(_pad_world_polygon_kicad(fp,p)).area>1e-6:
                    vip.append(dict(refdes=fp.properties.get('Reference',''),pad=p.number,x=t.position.X,y=t.position.Y))
    return dict(lvs_opens=opens,lvs_shorts=shorts,rf_keepout=rf,via_in_pad_violations=vip,mechanical=mechanical_check(spec,placed),passed=not opens and not shorts and not vip and all(x['passed'] for x in rf))


def audit_board1_spec(spec_dir: Path) -> GateReport:
    """Audit Board 1 Spec, BOM, DFM stackup/impedance/thermal, and ngspice Power/ESD/MC results."""
    tc = _TicketCollector("board1_spec", "parts_dfm")
    metrics: dict[str, Any] = {}

    bom_path = spec_dir / "bom_verified.json"
    dfm_path = spec_dir / "dfm_budget.json"
    pwr_path = spec_dir / "spice_power_results.json"
    esd_path = spec_dir / "spice_esd_results.json"

    for p in (bom_path, dfm_path, pwr_path, esd_path):
        if not tc.check(
            p.exists() and p.stat().st_size > 0,
            f"spec_file_{p.stem}",
            f"Missing or empty spec artifact: {p.name}",
            "missing",
            "existing non-empty JSON",
            artifact_path=str(p),
        ):
            return tc.build_report(metrics)

    bom = json.loads(bom_path.read_text(encoding="utf-8"))
    dfm = json.loads(dfm_path.read_text(encoding="utf-8"))
    pwr = json.loads(pwr_path.read_text(encoding="utf-8"))
    esd = json.loads(esd_path.read_text(encoding="utf-8"))

    # 1. BOM stock > 100 & CC1 != CC2 dual 5.1k resistors
    parts = bom.get("parts", [])
    low_stock = [
        {"refdes": p.get("refdes"), "mpn": p.get("mpn"), "stock": p.get("stock")}
        for p in parts
        if int(p.get("stock", 0)) <= 100
    ]
    tc.check(
        len(parts) >= 20 and len(low_stock) == 0,
        "bom_stock_gt_100",
        f"BOM parts with stock <= 100: {low_stock}",
        f"{len(low_stock)} low-stock parts (total={len(parts)})",
        "all parts stock > 100",
        artifact_path=str(bom_path),
    )

    cc_resistors = [
        p
        for p in parts
        if ("cc1" in str(p.get("role", "")).lower() or "cc2" in str(p.get("role", "")).lower())
        and "5.1k" in str(p.get("value", "")).lower()
    ]
    cc_refdes = {p.get("refdes") for p in cc_resistors}
    tc.check(
        len(cc_resistors) >= 2
        and len(cc_refdes) >= 2
        and bool(bom.get("independent_cc_resistors_verified", False)),
        "bom_dual_cc_5k1",
        "USB-C CC1 and CC2 must have two distinct 5.1k pulldown resistors",
        f"cc_refdes={sorted(cc_refdes)}",
        "2 distinct 5.1k resistors (CC1 != CC2)",
        artifact_path=str(bom_path),
    )
    metrics["bom_unique_parts"] = len(parts)
    metrics["bom_low_stock_count"] = len(low_stock)
    metrics["bom_cc_resistors"] = sorted(cc_refdes)

    # 2. DFM stackup & masked impedance
    stackup = dfm.get("stackup_selection", {}).get("stackup", {})
    imp = dfm.get("impedance_calibration", {})
    z50 = float(imp.get("single_ended_50ohm", {}).get("z0_masked_ohm", 0.0))
    z90 = float(imp.get("diff_pair_90ohm", {}).get("zdiff_masked_ohm", 0.0))
    tc.check(
        stackup.get("id") == "JLC04161H-7628" and int(stackup.get("layers", 0)) == 4,
        "stackup_jlc04161h_7628",
        "Expected 4-layer JLC04161H-7628 stackup",
        f"id={stackup.get('id')}, layers={stackup.get('layers')}",
        "id=JLC04161H-7628, layers=4",
        artifact_path=str(dfm_path),
    )
    tc.check(
        48.0 <= z50 <= 52.0,
        "masked_impedance_50ohm",
        f"50-ohm masked impedance out of tolerance: {z50} ohm",
        f"{z50} ohm",
        "50 +/- 2 ohm",
        artifact_path=str(dfm_path),
    )
    tc.check(
        85.0 <= z90 <= 95.0,
        "masked_impedance_90diff",
        f"90-ohm diff masked impedance out of tolerance: {z90} ohm",
        f"{z90} ohm",
        "90 +/- 5 ohm",
        artifact_path=str(dfm_path),
    )

    therm = dfm.get("electrical_safety_checks", {}).get("thermal_crossover", {})
    buck_th = therm.get("board1_3v3_sync_buck", {})
    ldo_th = therm.get("board1_2v5_post_ldo", {})
    tc.check(
        bool(buck_th.get("passed")) and float(buck_th.get("junction_temp_c", 999.0)) <= 125.0,
        "thermal_buck_3v3",
        "Buck 3.3V thermal limit check failed",
        f"Tj={buck_th.get('junction_temp_c')}C",
        "<= 125C",
        artifact_path=str(dfm_path),
    )
    tc.check(
        bool(ldo_th.get("passed")) and float(ldo_th.get("junction_temp_c", 999.0)) <= 125.0,
        "thermal_ldo_2v5",
        "LDO 2.5V thermal limit check failed",
        f"Tj={ldo_th.get('junction_temp_c')}C",
        "<= 125C",
        artifact_path=str(dfm_path),
    )
    metrics["z0_masked_ohm"] = z50
    metrics["zdiff_masked_ohm"] = z90
    metrics["buck_tj_c"] = buck_th.get("junction_temp_c")
    metrics["ldo_tj_c"] = ldo_th.get("junction_temp_c")

    # 3. SPICE power & Monte Carlo sanitization
    buck_m = pwr.get("buck_ldo_transient", {}).get("metrics", {})
    v33 = float(buck_m.get("v3v3_steady_mean_v", 0.0))
    r33 = float(buck_m.get("v3v3_steady_ripple_pp_mv", 999.0))
    v25 = float(buck_m.get("v2v5_steady_mean_v", 0.0))
    r25 = float(buck_m.get("v2v5_steady_ripple_pp_mv", 999.0))
    tc.check(
        bool(pwr.get("all_passed"))
        and 3.15 <= v33 <= 3.45
        and r33 < 50.0
        and 2.40 <= v25 <= 2.60
        and r25 < 5.0,
        "spice_buck_ldo_rails",
        "Buck 3.3V or LDO 2.5V SPICE regulation/ripple failed",
        f"3V3={v33}V (ripple={r33}mV), 2V5={v25}V (ripple={r25}mV)",
        "3V3=3.3+/-0.15V (<50mV), 2V5=2.5+/-0.1V (<5mV)",
        artifact_path=str(pwr_path),
    )

    mc = pwr.get("ac_filter_monte_carlo", {})
    mc_runs = mc.get("runs", [])
    bad_fc = [
        r.get("fc_hz")
        for r in mc_runs
        if r.get("fc_hz") is None
        or not isinstance(r.get("fc_hz"), (int, float))
        or math.isnan(float(r["fc_hz"]))
        or math.isinf(float(r["fc_hz"]))
        or float(r["fc_hz"]) <= 0.0
        or float(r["fc_hz"]) > 1e8
    ]
    fc_mean = float(mc.get("monte_carlo_summary", {}).get("fc_mean_hz", 0.0))
    tc.check(
        bool(mc.get("imag_frequency_column_sanitized"))
        and len(mc_runs) >= 20
        and len(bad_fc) == 0
        and 1200.0 <= fc_mean <= 2000.0,
        "spice_mc_fc_sanitized",
        f"Monte Carlo fc sanitization failed or anomalous values detected: {bad_fc[:5]}",
        f"sanitized={mc.get('imag_frequency_column_sanitized')}, fc_mean={fc_mean}Hz, bad={len(bad_fc)}",
        "sanitized=True, all runs finite positive around ~1591Hz",
        artifact_path=str(pwr_path),
    )
    metrics["v3v3_steady_v"] = v33
    metrics["v3v3_ripple_mv"] = r33
    metrics["v2v5_steady_v"] = v25
    metrics["v2v5_ripple_mv"] = r25
    metrics["mc_fc_mean_hz"] = fc_mean

    # 4. SPICE ESD limiter current < 20 mA
    esd_m = esd.get("esd_surge_clamping", {}).get("metrics", {})
    ic_ma = float(esd_m.get("protected_1k_ic_clamp_peak_ma", 999.0))
    tc.check(
        bool(esd.get("all_passed")) and 0.0 < ic_ma < 20.0,
        "spice_esd_limiter_lt_20ma",
        f"ESD 1k-limiter IC clamp current exceeds 20 mA: {ic_ma} mA",
        f"{ic_ma} mA",
        "< 20.0 mA",
        artifact_path=str(esd_path),
    )
    metrics["esd_protected_clamp_ma"] = ic_ma

    return tc.build_report(metrics)


def _audit_common_board1_gerbers(
    gerber_dir: Path,
    tc: _TicketCollector,
    metrics: dict[str, Any],
    *,
    min_drill_mm: float,
    min_copper_edge_mm: float = 0.25,
) -> None:
    """Shared strict Gerber + Excellon audit for Board 1 (60x40 mm, 4L)."""
    required_gbrs = [
        "Edge_Cuts.gbr",
        "F_Cu.gbr",
        "In1_Cu.gbr",
        "In2_Cu.gbr",
        "B_Cu.gbr",
        "F_Mask.gbr",
        "B_Mask.gbr",
        "F_SilkS.gbr",
        "B_SilkS.gbr",
    ]
    parse_ok: dict[str, bool] = {}
    for gname in required_gbrs:
        ok, _ = _strict_pygerber_parse(gerber_dir / gname, tc, f"gerber_{gname[:-4]}")
        parse_ok[gname] = ok

    edge_path = gerber_dir / "Edge_Cuts.gbr"
    bounds = _edge_cuts_centerline_bounds(edge_path)
    if bounds is None:
        tc.check(
            False,
            "edge_cuts_60x40",
            "Could not extract Edge_Cuts outline geometry",
            "missing",
            "60.0 x 40.0 mm",
            artifact_path=str(edge_path),
        )
        return

    ex0, ey0, ex1, ey1 = bounds
    bw, bh = ex1 - ex0, ey1 - ey0
    tc.check(
        abs(bw - 60.0) <= 0.5 and abs(bh - 40.0) <= 0.5,
        "edge_cuts_60x40",
        f"Board outline {bw:.3f}x{bh:.3f} mm deviates from 60x40 mm",
        f"{bw:.3f} x {bh:.3f} mm",
        "60.0 x 40.0 mm (+/-0.5)",
        artifact_path=str(edge_path),
    )
    metrics["board_size_mm"] = [round(bw, 3), round(bh, 3)]

    layer_areas: dict[str, float] = {}
    layer_margins: dict[str, float] = {}
    flashes_by_layer: dict[str, list[Any]] = {}
    trace_widths: list[float] = []

    for lyr in ("F_Cu.gbr", "In1_Cu.gbr", "In2_Cu.gbr", "B_Cu.gbr"):
        p = gerber_dir / lyr
        if not p.exists() or p.stat().st_size == 0:
            continue
        geom, flashes, widths = _gerber_layer_union(p)
        flashes_by_layer[lyr] = flashes
        if lyr in ("F_Cu.gbr", "B_Cu.gbr"):
            trace_widths.extend(widths)
        area = float(geom.area) if not geom.is_empty else 0.0
        layer_areas[lyr] = round(area, 2)
        if not geom.is_empty:
            minx, miny, maxx, maxy = geom.bounds
            margin = min(minx - ex0, miny - ey0, ex1 - maxx, ey1 - maxy)
        else:
            margin = -999.0
        layer_margins[lyr] = round(margin, 4)
        tc.check(
            margin >= min_copper_edge_mm - 1e-4,
            f"copper_edge_{lyr[:-4]}",
            f"{lyr} copper-to-edge margin {margin:.3f} mm < {min_copper_edge_mm} mm",
            f"{margin:.3f} mm",
            f">= {min_copper_edge_mm} mm",
            artifact_path=str(p),
        )
        if lyr in ("In1_Cu.gbr", "In2_Cu.gbr"):
            tc.check(
                area > 1500.0,
                f"gnd_pour_{lyr[:-4]}",
                f"{lyr} GND pour area {area:.1f} mm2 <= 1500 mm2",
                f"{area:.1f} mm2",
                "> 1500.0 mm2",
                artifact_path=str(p),
            )

    metrics["copper_areas_mm2"] = layer_areas
    metrics["copper_edge_margins_mm"] = layer_margins

    min_w = min(trace_widths) if trace_widths else 0.0
    tc.check(
        min_w >= 0.0889 - 1e-4,  # 3.5 mil = 0.0889 mm
        "min_trace_width",
        f"Minimum trace width {min_w:.4f} mm < 0.0889 mm (3.5 mil)",
        f"{min_w:.4f} mm",
        ">= 0.0889 mm (3.5 mil)",
        artifact_path=str(gerber_dir / "F_Cu.gbr"),
    )
    metrics["min_trace_width_mm"] = round(min_w, 4)

    # Silk-to-pad clearance (F_SilkS vs F_Mask)
    silk_p = gerber_dir / "F_SilkS.gbr"
    mask_p = gerber_dir / "F_Mask.gbr"
    if silk_p.exists() and mask_p.exists():
        silk_u, _, _ = _gerber_layer_union(silk_p)
        mask_u, _, _ = _gerber_layer_union(mask_p)
        overlap = float(silk_u.intersection(mask_u).area) if (not silk_u.is_empty and not mask_u.is_empty) else 0.0
        silk_dist = float(silk_u.distance(mask_u)) if (not silk_u.is_empty and not mask_u.is_empty) else 999.0
        tc.check(
            overlap <= 1e-4,
            "silk_to_pad_clearance",
            f"F_SilkS overlaps F_Mask solder openings by {overlap:.4f} mm2",
            f"overlap={overlap:.4f} mm2 (dist={silk_dist:.4f} mm)",
            "overlap == 0 mm2",
            severity="HIGH",
            artifact_path=str(silk_p),
        )
        metrics["silk_to_mask_overlap_mm2"] = round(overlap, 4)
        metrics["silk_to_mask_min_dist_mm"] = round(silk_dist, 4)

    # Excellon PTH.drl and NPTH.drl via gerbonara (NEVER pygerber!)
    pth_path = gerber_dir / "PTH.drl"
    npth_path = gerber_dir / "NPTH.drl"
    if tc.check(
        pth_path.exists() and pth_path.stat().st_size > 0,
        "excellon_pth_exists",
        "Missing or empty PTH.drl",
        "missing",
        "non-empty PTH.drl",
        artifact_path=str(pth_path),
    ):
        pth = _open_excellon(pth_path)
        pth_dias = [float(o.aperture.diameter) for o in pth.objects]
        min_pth = min(pth_dias) if pth_dias else 0.0
        tc.check(
            len(pth_dias) >= 10 and min_pth >= min_drill_mm - 1e-4,
            "excellon_pth_min_drill",
            f"PTH minimum drill {min_pth:.3f} mm < {min_drill_mm:.3f} mm",
            f"{min_pth:.3f} mm (count={len(pth_dias)})",
            f">= {min_drill_mm:.3f} mm",
            artifact_path=str(pth_path),
        )
        metrics["pth_hole_count"] = len(pth_dias)
        metrics["pth_min_drill_mm"] = round(min_pth, 4)

        min_ar, ar_viols = _compute_min_annular_ring(
            pth,
            flashes_by_layer.get("F_Cu.gbr", []),
            flashes_by_layer.get("B_Cu.gbr", []),
        )
        tc.check(
            min_ar is not None and min_ar >= 0.10 - 1e-4 and len(ar_viols) == 0,
            "annular_ring_min",
            f"Minimum annular ring {min_ar} mm < 0.10 mm ({len(ar_viols)} pads/vias failed)",
            f"min_ar={round(min_ar, 4) if min_ar is not None else None} mm, violations={len(ar_viols)}",
            ">= 0.10 mm",
            severity="HIGH",
            artifact_path=str(pth_path),
        )
        metrics["min_annular_ring_mm"] = round(min_ar, 4) if min_ar is not None else None
        metrics["annular_ring_violations_count"] = len(ar_viols)

    if tc.check(
        npth_path.exists() and npth_path.stat().st_size > 0,
        "excellon_npth_exists",
        "Missing or empty NPTH.drl",
        "missing",
        "non-empty NPTH.drl",
        artifact_path=str(npth_path),
    ):
        npth = _open_excellon(npth_path)
        tc.check(
            len(npth.objects) == 4,
            "npth_4_mounting_holes",
            f"Expected 4 NPTH mounting holes, found {len(npth.objects)}",
            str(len(npth.objects)),
            "4",
            artifact_path=str(npth_path),
        )
        metrics["npth_hole_count"] = len(npth.objects)


def audit_board1_pipeline_b(pb_dir: Path) -> GateReport:
    """Audit Board 1 Pipeline B (KiCad + FastRoute/FreeRouting/tscircuit bakeoff)."""
    tc = _TicketCollector("board1_pipeline_b", "layout_route")
    metrics: dict[str, Any] = {}
    gerber_dir = pb_dir / "gerbers"

    if not tc.check(
        gerber_dir.is_dir() and any(gerber_dir.glob("*.gbr")),
        "pipeline_b_gerbers_dir",
        "Pipeline B gerbers/ directory is missing or empty",
        "empty_or_missing",
        "RS-274X Gerbers + PTH.drl + NPTH.drl",
        artifact_path=str(gerber_dir),
    ):
        return tc.build_report(metrics)

    _audit_common_board1_gerbers(gerber_dir, tc, metrics, min_drill_mm=0.30, min_copper_edge_mm=0.25)

    # Check >= 4 QFN thermal vias (0.30 mm drills near QFN center or total >= 4 0.30mm drills)
    pth_path = gerber_dir / "PTH.drl"
    if pth_path.exists():
        pth = _open_excellon(pth_path)
        via_030 = [o for o in pth.objects if abs(float(o.aperture.diameter) - 0.30) <= 0.02]
        tc.check(
            len(via_030) >= 4,
            "qfn_thermal_vias_ge_4",
            f"Expected >= 4 thermal/signal 0.30mm drills, found {len(via_030)}",
            str(len(via_030)),
            ">= 4",
            artifact_path=str(pth_path),
        )

    # KiCad routed.kicad_pcb LVS + via-in-pad + USB/VBUS/CC physics
    pcb_path = pb_dir / "routed.kicad_pcb"
    if tc.check(
        pcb_path.exists() and pcb_path.stat().st_size > 0,
        "routed_kicad_pcb_exists",
        "Missing routed.kicad_pcb",
        "missing",
        "valid KiCad PCB",
        artifact_path=str(pcb_path),
    ):
        try:
            lvs = _check_kicad_drc_and_lvs(pcb_path)
            metrics["kicad_lvs"] = lvs
            tc.check(
                len(lvs["via_in_pad_violations"]) == 0,
                "via_in_non_thermal_smt_pad",
                f"Via-in-pad collision(s) on non-thermal SMT pads: {lvs['via_in_pad_violations'][:3]}",
                f"{len(lvs['via_in_pad_violations'])} collisions",
                "0 collisions",
                artifact_path=str(pcb_path),
            )
            tc.check(
                len(lvs["lvs_opens"]) == 0,
                "topological_lvs_opens",
                f"Topological LVS detected open nets: {lvs['lvs_opens']}",
                f"{len(lvs['lvs_opens'])} opens",
                "0 opens",
                artifact_path=str(pcb_path),
            )
            tc.check(
                len(lvs["lvs_shorts"]) == 0,
                "topological_lvs_shorts",
                f"Topological LVS detected copper short(s): {lvs['lvs_shorts']}",
                f"{len(lvs['lvs_shorts'])} shorts ({lvs['lvs_shorts'][:2]})",
                "0 shorts",
                artifact_path=str(pcb_path),
            )
            if lvs["min_inter_net_clearance_mm"] is not None:
                tc.check(
                    lvs["min_inter_net_clearance_mm"] >= 0.0889 - 1e-4,
                    "min_trace_space_clearance",
                    f"Min inter-net clearance {lvs['min_inter_net_clearance_mm']} mm < 3.5 mil (0.089 mm)",
                    f"{lvs['min_inter_net_clearance_mm']} mm ({lvs['worst_clearance_pair']})",
                    ">= 0.0889 mm",
                    artifact_path=str(pcb_path),
                )

            # Independent USB/VBUS/CC checks directly from routed.kicad_pcb
            board = Board.from_file(str(pcb_path))
            usb_nets = {int(n.number) for n in board.nets if n.name in ("USB_DP", "USB_DM")}
            usb_vias = [
                v for v in board.traceItems if isinstance(v, KiVia) and int(v.net) in usb_nets
            ]
            tc.check(
                len(usb_vias) == 0,
                "usb_dp_dm_zero_vias",
                f"USB_DP/USB_DM has {len(usb_vias)} via(s), expected 0",
                str(len(usb_vias)),
                "0",
                artifact_path=str(pcb_path),
            )

            vbus_nets = {int(n.number) for n in board.nets if n.name == "VBUS"}
            vbus_widths = [
                _fval(s.width)
                for s in board.traceItems
                if isinstance(s, KiSegment) and int(s.net) in vbus_nets
            ]
            vbus_max = max(vbus_widths) if vbus_widths else 0.0
            tc.check(
                vbus_max >= 2.0 - 1e-4,
                "vbus_width_ge_2mm",
                f"VBUS max trace width {vbus_max:.3f} mm < 2.0 mm",
                f"{vbus_max:.3f} mm",
                ">= 2.0 mm",
                artifact_path=str(pcb_path),
            )

            cc1_nets = {int(n.number) for n in board.nets if "CC1" in str(n.name)}
            cc2_nets = {int(n.number) for n in board.nets if "CC2" in str(n.name)}
            tc.check(
                bool(cc1_nets) and bool(cc2_nets) and cc1_nets.isdisjoint(cc2_nets),
                "usbc_cc1_ne_cc2",
                "USB-C CC1 and CC2 must be distinct nets",
                f"cc1={cc1_nets}, cc2={cc2_nets}",
                "distinct non-empty CC1 and CC2 nets",
                artifact_path=str(pcb_path),
            )
            metrics["usb_path_vias"] = len(usb_vias)
            metrics["vbus_max_width_mm"] = round(vbus_max, 3)
        except Exception as exc:
            tc.check(
                False,
                "routed_kicad_pcb_parse",
                f"Failed to parse routed.kicad_pcb: {exc}",
                f"{type(exc).__name__}: {exc}",
                "valid KiCad PCB",
                artifact_path=str(pcb_path),
            )

    # Pipeline B report (D_TVS distance & QFN thermal vias)
    rep_path = pb_dir / "pipeline_b_report.json"
    if tc.check(
        rep_path.exists() and rep_path.stat().st_size > 0,
        "pipeline_b_report_exists",
        "Missing pipeline_b_report.json",
        "missing",
        "non-empty JSON",
        artifact_path=str(rep_path),
    ):
        rep = json.loads(rep_path.read_text(encoding="utf-8"))
        phys = rep.get("physics", {})
        tvs_d = float(phys.get("usb_tvs_distance_mm", 999.0))
        qfn_v = int(phys.get("qfn_thermal_vias", 0))
        tc.check(
            0.0 < tvs_d <= 5.0,
            "usb_tvs_stub_le_5mm",
            f"D_TVS distance from USB-C receptacle {tvs_d} mm > 5.0 mm",
            f"{tvs_d} mm",
            "<= 5.0 mm",
            artifact_path=str(rep_path),
        )
        tc.check(
            qfn_v >= 4,
            "qfn_thermal_vias_report",
            f"QFN thermal via count {qfn_v} < 4",
            str(qfn_v),
            ">= 4",
            artifact_path=str(rep_path),
        )
        metrics["usb_tvs_distance_mm"] = tvs_d
        metrics["qfn_thermal_vias"] = qfn_v

    # 3-way Router Bakeoff audit
    bakeoff_path = pb_dir / "router_bakeoff.json"
    if tc.check(
        bakeoff_path.exists() and bakeoff_path.stat().st_size > 0,
        "router_bakeoff_exists",
        "Missing router_bakeoff.json",
        "missing",
        "non-empty JSON",
        artifact_path=str(bakeoff_path),
    ):
        bakeoff = json.loads(bakeoff_path.read_text(encoding="utf-8"))
        by_eng = {b.get("engine"): b for b in bakeoff.get("benchmarks", [])}
        for eng in ("fastroute_v0.1.14", "tscircuit_capacity_autorouter"):
            b = by_eng.get(eng)
            tc.check(
                b is not None and bool(b.get("success")) and int(b.get("unrouted_nets", 1)) == 0,
                f"bakeoff_{eng}",
                f"Autorouter {eng} did not achieve 0 unrouted nets",
                f"success={b.get('success') if b else None}, unrouted={b.get('unrouted_nets') if b else None}",
                "success=True, unrouted_nets=0",
                artifact_path=str(bakeoff_path),
            )
        fr_cli = by_eng.get("freerouting_cli_v2.5.0")
        tc.check(
            fr_cli is not None and float(fr_cli.get("runtime_ms", 0.0)) > 0.0,
            "bakeoff_freerouting_cli_benchmarked",
            "freerouting_cli_v2.5.0 benchmark missing or did not execute",
            f"present={fr_cli is not None}, runtime_ms={fr_cli.get('runtime_ms') if fr_cli else None}",
            "present with runtime_ms > 0 (max_passes=1 GraalVM workaround)",
            severity="HIGH",
            artifact_path=str(bakeoff_path),
        )
        metrics["bakeoff_winner"] = bakeoff.get("selected_winner")
        metrics["bakeoff_engines"] = list(by_eng.keys())

    return tc.build_report(metrics)


def audit_board1_pipeline_a(pa_dir: Path) -> GateReport:
    """Audit Board 1 Pipeline A (tscircuit + @tscircuit/capacity-autorouter)."""
    tc = _TicketCollector("board1_pipeline_a", "layout_route")
    metrics: dict[str, Any] = {}

    rep_path = pa_dir / "tscircuit_report.json"
    pcb_svg = pa_dir / "pcb.svg"
    sch_svg = pa_dir / "schematic.svg"

    if tc.check(
        rep_path.exists() and rep_path.stat().st_size > 0,
        "tscircuit_report_exists",
        "Missing tscircuit_report.json",
        "missing",
        "non-empty JSON",
        artifact_path=str(rep_path),
    ):
        rep = json.loads(rep_path.read_text(encoding="utf-8"))
        err_cnt = int(rep.get("runner_summary", {}).get("error_count", 1))
        tc.check(
            err_cnt == 0,
            "tscircuit_error_count_0",
            f"tscircuit_report.json reported {err_cnt} error(s)",
            str(err_cnt),
            "0",
            artifact_path=str(rep_path),
        )
        phys = rep.get("physics", {})
        metrics["tscircuit_error_count"] = err_cnt
        metrics["usb_tvs_distance_mm"] = phys.get("usb_tvs_distance_mm")
        metrics["usb_path_vias"] = phys.get("usb_path_vias")
        metrics["vbus_max_width_mm"] = phys.get("vbus_max_width_mm")
        metrics["cc_nets_distinct"] = phys.get("cc_nets_distinct")

    for svg_name, svg_p in (("pcb_svg", pcb_svg), ("schematic_svg", sch_svg)):
        ok_svg = svg_p.exists() and svg_p.stat().st_size > 1000 and "<svg" in svg_p.read_text(encoding="utf-8", errors="ignore")[:500]
        tc.check(
            ok_svg,
            f"{svg_name}_non_empty",
            f"{svg_p.name} missing or empty SVG",
            f"size={svg_p.stat().st_size if svg_p.exists() else 0}",
            "> 1000 bytes with <svg>",
            artifact_path=str(svg_p),
        )

    # Gerbers & Excellon (JLC04161H-7628 min_drill_mm=0.20)
    _audit_common_board1_gerbers(pa_dir, tc, metrics, min_drill_mm=0.20, min_copper_edge_mm=0.25)

    # KiCad PCB LVS + via-in-pad
    pcb_path = pa_dir / "board1.kicad_pcb"
    if pcb_path.exists() and pcb_path.stat().st_size > 0:
        lvs = _check_kicad_drc_and_lvs(pcb_path)
        metrics["kicad_lvs"] = lvs
        tc.check(
            len(lvs["via_in_pad_violations"]) == 0,
            "via_in_non_thermal_smt_pad",
            f"Via-in-pad collision(s) on non-thermal SMT pads: {lvs['via_in_pad_violations'][:3]}",
            f"{len(lvs['via_in_pad_violations'])} collisions",
            "0 collisions",
            artifact_path=str(pcb_path),
        )
        tc.check(
            len(lvs["lvs_opens"]) == 0,
            "topological_lvs_opens",
            f"Topological LVS detected open nets: {lvs['lvs_opens']}",
            f"{len(lvs['lvs_opens'])} opens",
            "0 opens",
            artifact_path=str(pcb_path),
        )
        tc.check(
            len(lvs["lvs_shorts"]) == 0,
            "topological_lvs_shorts",
            f"Topological LVS detected copper short(s): {lvs['lvs_shorts']}",
            f"{len(lvs['lvs_shorts'])} shorts",
            "0 shorts",
            artifact_path=str(pcb_path),
        )

    return tc.build_report(metrics)


def audit_board2_special(b2_dir: Path) -> GateReport:
    """Audit Board 2 (Special Planar Coil + Chevron CapTouch + Meanders + ngspice)."""
    tc = _TicketCollector("board2_special", "special_ip")
    metrics: dict[str, Any] = {}
    gerber_dir = b2_dir / "gerbers"

    coil_path = b2_dir / "coil_params.json"
    spice_path = b2_dir / "special_coil_spice.json"
    manifest_path = b2_dir / "board_manifest.json"

    for p in (coil_path, spice_path, manifest_path):
        tc.check(
            p.exists() and p.stat().st_size > 0,
            f"board2_file_{p.stem}",
            f"Missing or empty Board 2 artifact: {p.name}",
            "missing",
            "non-empty JSON",
            artifact_path=str(p),
        )

    if coil_path.exists():
        coil = json.loads(coil_path.read_text(encoding="utf-8"))
        bz_const = abs(float(coil.get("axial_bz_total_per_amp_ut", 0.0)))
        bz_cancel = abs(float(coil.get("axial_bz_canceling_per_amp_ut", 1e9)))
        l_const = float(coil.get("inductance_constructive_uh", 0.0))
        l_cancel = float(coil.get("inductance_canceling_counterfactual_uh", 1e9))
        flux_ratio = bz_const / max(bz_cancel, 1e-6)
        tc.check(
            bool(coil.get("constructive_flux_verified")) and flux_ratio > 10.0,
            "coil_constructive_flux_gt_10x",
            f"Constructive Bz flux must exceed canceling counterfactual by >10x (got {flux_ratio:.2f}x)",
            f"ratio={flux_ratio:.2f}x (Bz_const={bz_const:.1f}, Bz_cancel={bz_cancel:.1f} uT/A)",
            "> 10.0x",
            artifact_path=str(coil_path),
        )
        metrics["coil_bz_constructive_ut_per_a"] = round(bz_const, 2)
        metrics["coil_bz_canceling_ut_per_a"] = round(bz_cancel, 2)
        metrics["coil_flux_ratio"] = round(flux_ratio, 2)
        metrics["coil_l_constructive_uh"] = round(l_const, 3)
        metrics["coil_l_canceling_uh"] = round(l_cancel, 3)

    if spice_path.exists():
        spice = json.loads(spice_path.read_text(encoding="utf-8"))
        step = spice.get("step_response", {})
        fly = spice.get("pwm_flyback", {})
        tau_err = float(step.get("tau_error_pct", 999.0))
        clamped = bool(fly.get("catch_diode_clamped"))
        tc.check(
            tau_err < 2.0,
            "coil_spice_tau_error_lt_2pct",
            f"SPICE tau error {tau_err}% >= 2.0%",
            f"{tau_err}%",
            "< 2.0%",
            artifact_path=str(spice_path),
        )
        tc.check(
            clamped,
            "coil_spice_catch_diode_clamped",
            "Catch diode clamping verification failed",
            f"clamped={clamped}, v_peak={fly.get('v_flyback_clamp_peak_v')}V",
            "catch_diode_clamped=True",
            artifact_path=str(spice_path),
        )
        metrics["coil_tau_error_pct"] = tau_err
        metrics["coil_catch_diode_clamped"] = clamped
        metrics["coil_v_flyback_clamp_peak_v"] = fly.get("v_flyback_clamp_peak_v")

    # Gerbers & PTH.drl
    required_gbrs = [
        "Edge_Cuts.gbr",
        "F_Cu.gbr",
        "In1_Cu.gbr",
        "In2_Cu.gbr",
        "B_Cu.gbr",
        "F_Mask.gbr",
        "B_Mask.gbr",
        "F_SilkS.gbr",
        "B_SilkS.gbr",
    ]
    for gname in required_gbrs:
        _strict_pygerber_parse(gerber_dir / gname, tc, f"gerber_{gname[:-4]}")

    edge_path = gerber_dir / "Edge_Cuts.gbr"
    bounds = _edge_cuts_centerline_bounds(edge_path)
    if bounds is not None:
        ex0, ey0, ex1, ey1 = bounds
        bw, bh = ex1 - ex0, ey1 - ey0
        tc.check(
            abs(bw - 50.0) <= 0.5 and abs(bh - 50.0) <= 0.5,
            "edge_cuts_50x50",
            f"Board 2 outline {bw:.3f}x{bh:.3f} mm deviates from 50x50 mm",
            f"{bw:.3f} x {bh:.3f} mm",
            "50.0 x 50.0 mm (+/-0.5)",
            artifact_path=str(edge_path),
        )
        metrics["board_size_mm"] = [round(bw, 3), round(bh, 3)]

        margins: dict[str, float] = {}
        flashes_by_layer: dict[str, list[Any]] = {}
        widths: list[float] = []
        for lyr in ("F_Cu.gbr", "In1_Cu.gbr", "In2_Cu.gbr", "B_Cu.gbr"):
            p = gerber_dir / lyr
            if not p.exists():
                continue
            geom, flashes, w_list = _gerber_layer_union(p)
            flashes_by_layer[lyr] = flashes
            widths.extend(w_list)
            minx, miny, maxx, maxy = geom.bounds
            margin = min(minx - ex0, miny - ey0, ex1 - maxx, ey1 - maxy)
            margins[lyr] = round(margin, 4)
            tc.check(
                margin >= 0.50 - 1e-4,
                f"copper_edge_ge_050_{lyr[:-4]}",
                f"Board 2 {lyr} copper-to-edge margin {margin:.3f} mm < 0.50 mm",
                f"{margin:.3f} mm",
                ">= 0.50 mm",
                artifact_path=str(p),
            )
        metrics["copper_edge_margins_mm"] = margins

        min_w = min(widths) if widths else 0.0
        tc.check(
            min_w >= 0.20 - 1e-4,
            "board2_min_trace_width",
            f"Board 2 min trace width {min_w:.4f} mm < 0.20 mm",
            f"{min_w:.4f} mm",
            ">= 0.20 mm",
            artifact_path=str(gerber_dir / "F_Cu.gbr"),
        )

        # Silk-to-pad clearance
        silk_u, _, _ = _gerber_layer_union(gerber_dir / "F_SilkS.gbr")
        mask_u, _, _ = _gerber_layer_union(gerber_dir / "F_Mask.gbr")
        overlap = float(silk_u.intersection(mask_u).area) if (not silk_u.is_empty and not mask_u.is_empty) else 0.0
        tc.check(
            overlap <= 1e-4,
            "silk_to_pad_clearance",
            f"Board 2 F_SilkS overlaps F_Mask by {overlap:.4f} mm2",
            f"overlap={overlap:.4f} mm2",
            "overlap == 0 mm2",
            severity="HIGH",
            artifact_path=str(gerber_dir / "F_SilkS.gbr"),
        )
        metrics["silk_to_mask_overlap_mm2"] = round(overlap, 4)

        pth_path = gerber_dir / "PTH.drl"
        if tc.check(
            pth_path.exists() and pth_path.stat().st_size > 0,
            "excellon_pth_exists",
            "Missing or empty Board 2 PTH.drl",
            "missing",
            "non-empty PTH.drl",
            artifact_path=str(pth_path),
        ):
            pth = _open_excellon(pth_path)
            dias = [float(o.aperture.diameter) for o in pth.objects]
            min_pth = min(dias) if dias else 0.0
            tc.check(
                len(dias) >= 15 and min_pth >= 0.30 - 1e-4,
                "excellon_pth_min_drill",
                f"Board 2 min drill {min_pth:.3f} mm < 0.30 mm",
                f"{min_pth:.3f} mm (count={len(dias)})",
                ">= 0.30 mm",
                artifact_path=str(pth_path),
            )
            min_ar, ar_viols = _compute_min_annular_ring(
                pth, flashes_by_layer.get("F_Cu.gbr", []), flashes_by_layer.get("B_Cu.gbr", [])
            )
            tc.check(
                min_ar is not None and min_ar >= 0.10 - 1e-4 and len(ar_viols) == 0,
                "annular_ring_min",
                f"Board 2 min annular ring {min_ar} mm < 0.10 mm",
                f"{round(min_ar, 4) if min_ar is not None else None} mm",
                ">= 0.10 mm",
                severity="HIGH",
                artifact_path=str(pth_path),
            )
            metrics["pth_hole_count"] = len(dias)
            metrics["min_annular_ring_mm"] = round(min_ar, 4) if min_ar is not None else None

    if manifest_path.exists():
        man = json.loads(manifest_path.read_text(encoding="utf-8"))
        bdrc = man.get("board_drc", {})
        ct = man.get("captouch_metrics", {})
        tc.check(
            bool(bdrc.get("passed")) and float(bdrc.get("min_inter_net_clearance_mm", 0.0)) >= 0.20,
            "board2_manifest_drc",
            "Board 2 inter-net clearance DRC failed",
            f"passed={bdrc.get('passed')}, min_clr={bdrc.get('min_inter_net_clearance_mm')} mm",
            "passed=True, >= 0.20 mm",
            artifact_path=str(manifest_path),
        )
        tc.check(
            bool(ct.get("drc_passed")) and bool(ct.get("all_polygons_valid")),
            "captouch_drc",
            "CapTouch chevron slider DRC failed",
            f"drc={ct.get('drc_passed')}, valid={ct.get('all_polygons_valid')}",
            "True",
            artifact_path=str(manifest_path),
        )

    pcb_path = b2_dir / "special_board.kicad_pcb"
    if pcb_path.exists():
        lvs = _check_kicad_drc_and_lvs(pcb_path, allow_coil_bridge=True, allow_electrode_vip=True)
        metrics["kicad_lvs"] = lvs
        tc.check(
            len(lvs["via_in_pad_violations"]) == 0,
            "via_in_non_electrode_smt_pad",
            f"Via-in-pad collision on Board 2 non-electrode SMT pads: {lvs['via_in_pad_violations']}",
            f"{len(lvs['via_in_pad_violations'])} collisions",
            "0 collisions",
            artifact_path=str(pcb_path),
        )
        tc.check(
            len(lvs["lvs_shorts"]) == 0,
            "topological_lvs_shorts",
            f"Board 2 topological LVS detected unintended copper shorts: {lvs['lvs_shorts']}",
            f"{len(lvs['lvs_shorts'])} shorts",
            "0 unintended shorts",
            artifact_path=str(pcb_path),
        )

    return tc.build_report(metrics)
