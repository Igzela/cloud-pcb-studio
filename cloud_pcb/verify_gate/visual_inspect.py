"""Composite Gerber PNG rendering (pygerber + gerbonara + resvg_py) and Chromium headless inspection."""

from __future__ import annotations

import io
import re
import subprocess
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import resvg_py
from gerbonara.excellon import ExcellonFile
from PIL import Image
from pygerber.common.rgba import RGBA
from pygerber.gerberx3.api.v2 import ColorScheme
from pygerber.gerberx3.api.v2 import GerberFile as PyGerberFile

from cloud_pcb.verify_gate.gerber_drc import _edge_cuts_centerline_bounds


def _normalize_rs274x_for_render(raw: str) -> str:
    """Normalize gerbonara 1.5.0 missing G75* / G01** terminators in memory for visual rendering."""
    text = re.sub(r"(?m)^G75\s*$", "G75*", raw)
    text = text.replace("G01**", "G01*")
    return text


def _extract_svg_body(svg_str: str, prefix: str) -> tuple[str, float, float]:
    """Extract inner SVG group & dimensions, prefixing defs/id references to avoid collisions."""
    w_match = re.search(r'width="([0-9.]+)"', svg_str)
    h_match = re.search(r'height="([0-9.]+)"', svg_str)
    w = float(w_match.group(1)) if w_match else 0.0
    h = float(h_match.group(1)) if h_match else 0.0

    start = svg_str.find(">", svg_str.find("<svg")) + 1
    end = svg_str.rfind("</svg>")
    body = svg_str[start:end] if start > 0 and end > start else ""

    # Prefix IDs (id="d0", mask="url(#d0)", xlink:href="#d1", href="#d1")
    body = re.sub(r'\bid="([^"]+)"', rf'id="{prefix}_\1"', body)
    body = re.sub(r'url\(#([^)]+)\)', rf'url(#{prefix}_\1)', body)
    body = re.sub(r'xlink:href="#([^"]+)"', rf'xlink:href="#{prefix}_\1"', body)
    body = re.sub(r'(?<!xlink:)href="#([^"]+)"', rf'href="#{prefix}_\1"', body)
    return body, w, h


def _render_layer_svg_fragment(
    gbr_path: Path,
    solid_rgba: RGBA,
    clear_rgba: RGBA,
    prefix: str,
    board_bounds: tuple[float, float, float, float],
) -> str:
    """Parse one Gerber layer via pygerber and wrap its SVG positioned in board space."""
    if not gbr_path.exists() or gbr_path.stat().st_size == 0:
        return ""
    raw = gbr_path.read_text(encoding="utf-8", errors="ignore")
    try:
        parsed = PyGerberFile.from_str(raw).parse()
    except Exception:
        parsed = PyGerberFile.from_str(_normalize_rs274x_for_render(raw)).parse()

    info = parsed.get_info()
    w_mm = float(info.width_mm)
    h_mm = float(info.height_mm)
    if w_mm <= 0.0 or h_mm <= 0.0:
        return ""

    buf = io.BytesIO()
    cs = ColorScheme(
        background_color=RGBA(r=0, g=0, b=0, a=0),
        solid_color=solid_rgba,
        clear_color=clear_rgba,
        solid_region_color=solid_rgba,
        clear_region_color=clear_rgba,
    )
    parsed.render_svg(buf, color_scheme=cs)
    svg_str = buf.getvalue().decode("utf-8")
    body, frag_w, frag_h = _extract_svg_body(svg_str, prefix)
    if not body.strip():
        return ""

    ex0, _, _, ey1 = board_bounds
    dx = float(info.min_x_mm) - ex0
    dy = ey1 - float(info.max_y_mm)
    vw = frag_w if frag_w > 0 else w_mm
    vh = frag_h if frag_h > 0 else h_mm
    return (
        f'<svg x="{dx:.5f}" y="{dy:.5f}" width="{vw:.5f}" height="{vh:.5f}" '
        f'viewBox="0 0 {vw:.5f} {vh:.5f}" overflow="visible">{body}</svg>'
    )


def render_composite_board_png(
    gerber_dir: Path,
    out_png: Path,
    *,
    copper_layer: str = "F_Cu.gbr",
    mask_layer: str | None = "F_Mask.gbr",
    silk_layer: str | None = "F_SilkS.gbr",
    substrate_hex: str = "#0a3622",
    copper_rgba: RGBA = RGBA(r=214, g=168, b=62, a=240),
    mask_rgba: RGBA = RGBA(r=240, g=200, b=90, a=110),
    silk_rgba: RGBA = RGBA(r=248, g=250, b=252, a=235),
    width_px: int = 1200,
) -> dict[str, Any]:
    """Render a multi-layer composite Gerber+Excellon PNG via pygerber SVG + resvg_py."""
    edge_path = gerber_dir / "Edge_Cuts.gbr"
    bounds = _edge_cuts_centerline_bounds(edge_path)
    if bounds is None:
        raise FileNotFoundError(f"Cannot render {out_png.name}: missing Edge_Cuts.gbr in {gerber_dir}")
    ex0, ey0, ex1, ey1 = bounds
    bw, bh = ex1 - ex0, ey1 - ey0
    pad = 1.8

    fragments: list[str] = []
    # Substrate board fill
    fragments.append(
        f'<rect x="0" y="0" width="{bw:.4f}" height="{bh:.4f}" rx="0.6" '
        f'fill="{substrate_hex}" stroke="#38bdf8" stroke-width="0.18"/>'
    )

    layer_specs: list[tuple[str, RGBA, RGBA, str]] = [
        (copper_layer, copper_rgba, RGBA(r=10, g=54, b=34, a=255), "cu"),
    ]
    if mask_layer:
        layer_specs.append((mask_layer, mask_rgba, RGBA(r=0, g=0, b=0, a=0), "mask"))
    if silk_layer:
        layer_specs.append((silk_layer, silk_rgba, RGBA(r=0, g=0, b=0, a=0), "silk"))

    for fname, s_rgba, c_rgba, pfx in layer_specs:
        frag = _render_layer_svg_fragment(gerber_dir / fname, s_rgba, c_rgba, pfx, bounds)
        if frag:
            fragments.append(frag)

    # Overlay Excellon drill holes via gerbonara (NEVER pygerber)
    drill_circles: list[str] = []
    for drl_name, fill_hex, ring_hex in (
        ("PTH.drl", "#020617", "#eab308"),
        ("NPTH.drl", "#020617", "#94a3b8"),
    ):
        drl_path = gerber_dir / drl_name
        if drl_path.exists() and drl_path.stat().st_size > 0:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                ex = ExcellonFile.open(drl_path)
            for hole in ex.objects:
                cx = float(hole.x) - ex0
                cy = ey1 - float(hole.y)
                r = max(float(hole.aperture.diameter) / 2.0, 0.08)
                drill_circles.append(
                    f'<circle cx="{cx:.4f}" cy="{cy:.4f}" r="{r:.4f}" '
                    f'fill="{fill_hex}" stroke="{ring_hex}" stroke-width="0.06"/>'
                )

    edge_frag = _render_layer_svg_fragment(
        edge_path, RGBA(r=56, g=189, b=248, a=255), RGBA(r=0, g=0, b=0, a=0), "edge", bounds
    )
    if edge_frag:
        fragments.append(edge_frag)

    svg_doc = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="{-pad:.3f} {-pad:.3f} {bw + 2 * pad:.3f} {bh + 2 * pad:.3f}">\n'
        f'<rect x="{-pad:.3f}" y="{-pad:.3f}" width="{bw + 2 * pad:.3f}" height="{bh + 2 * pad:.3f}" fill="#090d16"/>\n'
        + "\n".join(fragments)
        + "\n"
        + "\n".join(drill_circles)
        + "\n</svg>"
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    png_bytes = resvg_py.svg_to_bytes(svg_string=svg_doc, width=width_px)
    out_png.write_bytes(png_bytes)

    # Verify non-blank pixel variance via PIL
    img = Image.open(out_png).convert("RGB")
    arr = np.asarray(img, dtype=np.float32)
    std_val = float(arr.std())
    return {
        "path": str(out_png),
        "width_px": img.width,
        "height_px": img.height,
        "bytes": len(png_bytes),
        "pixel_std": round(std_val, 2),
        "non_blank": std_val > 5.0 and img.width >= 400 and img.height >= 200,
    }


def render_all_board_previews(artifacts_root: Path, renders_dir: Path) -> dict[str, Any]:
    """Render composite PNG previews for Board 1 (Pipeline B & A) and Board 2."""
    renders_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}

    b1_b_dir = artifacts_root / "board1_kicad_fastroute" / "gerbers"
    b1_a_dir = artifacts_root / "board1_tscircuit"
    b2_dir = artifacts_root / "board2_special" / "gerbers"

    b1_primary = b1_b_dir if (b1_b_dir / "Edge_Cuts.gbr").exists() else b1_a_dir
    results["board1_top"] = render_composite_board_png(
        b1_primary,
        renders_dir / "board1_top.png",
        copper_layer="F_Cu.gbr",
        mask_layer="F_Mask.gbr",
        silk_layer="F_SilkS.gbr",
        substrate_hex="#0a3622",
    )
    results["board1_bottom"] = render_composite_board_png(
        b1_primary,
        renders_dir / "board1_bottom.png",
        copper_layer="B_Cu.gbr",
        mask_layer="B_Mask.gbr",
        silk_layer="B_SilkS.gbr",
        substrate_hex="#082c1c",
    )

    if (b1_a_dir / "Edge_Cuts.gbr").exists():
        results["board1_tscircuit_top"] = render_composite_board_png(
            b1_a_dir,
            renders_dir / "board1_tscircuit_top.png",
            copper_layer="F_Cu.gbr",
            mask_layer="F_Mask.gbr",
            silk_layer="F_SilkS.gbr",
            substrate_hex="#0b3a2c",
        )

    results["board2_top"] = render_composite_board_png(
        b2_dir,
        renders_dir / "board2_top.png",
        copper_layer="F_Cu.gbr",
        mask_layer="F_Mask.gbr",
        silk_layer="F_SilkS.gbr",
        substrate_hex="#0f2942",
        copper_rgba=RGBA(r=234, g=179, b=8, a=240),
    )
    results["board2_inner1"] = render_composite_board_png(
        b2_dir,
        renders_dir / "board2_inner1.png",
        copper_layer="In1_Cu.gbr",
        mask_layer=None,
        silk_layer=None,
        substrate_hex="#111827",
        copper_rgba=RGBA(r=45, g=212, b=191, a=235),
    )
    return results


def inspect_workbench_chromium(
    workbench_html: Path,
    screenshot_png: Path,
) -> dict[str, Any]:
    """Inspect generated workbench.html in headless Chromium (DOM, console errors, pixel variance)."""
    url = f"file://{workbench_html.resolve()}"
    screenshot_png.parent.mkdir(parents=True, exist_ok=True)

    common_flags = [
        "chromium",
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--window-size=1440,1080",
        "--virtual-time-budget=2500",
    ]

    dom_proc = subprocess.run(
        [*common_flags, "--dump-dom", url],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    shot_proc = subprocess.run(
        [*common_flags, "--enable-logging=stderr", f"--screenshot={screenshot_png}", url],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )

    dom_text = dom_proc.stdout or ""
    console_errors: list[str] = []
    for line in (shot_proc.stderr or "").splitlines():
        if "CONSOLE" in line and any(
            kw in line for kw in ("ERROR", "Uncaught", "TypeError", "ReferenceError", "SyntaxError")
        ):
            console_errors.append(line.strip())

    img = Image.open(screenshot_png).convert("RGB") if screenshot_png.exists() else None
    pixel_std = float(np.asarray(img, dtype=np.float32).std()) if img is not None else 0.0
    dom_non_blank = len(dom_text) > 2000 and "Cloud PCB Studio" in dom_text

    return {
        "url": url,
        "dom_returncode": dom_proc.returncode,
        "shot_returncode": shot_proc.returncode,
        "dom_length": len(dom_text),
        "dom_non_blank": dom_non_blank,
        "console_errors": console_errors,
        "screenshot_path": str(screenshot_png),
        "screenshot_width": img.width if img else 0,
        "screenshot_height": img.height if img else 0,
        "screenshot_pixel_std": round(pixel_std, 2),
        "passed": (
            dom_proc.returncode == 0
            and shot_proc.returncode == 0
            and dom_non_blank
            and len(console_errors) == 0
            and pixel_std > 10.0
        ),
    }
