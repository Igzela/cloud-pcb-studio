"""Tests for assembly_scene side detection, camera framing/resize, and standoff lead Z."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from cloud_pcb.general_solver.assembly import assembly_scene, export_assembly
from cloud_pcb.general_solver.run_blind import _build_blind_kicad_board

ROOT = Path(__file__).resolve().parents[1]


def _make_component(
    refdes: str,
    *,
    side: str = "top",
    height_mm: float = 1.5,
    standoff_mm: float = 0.0,
    bbox_mm: list[float] | None = None,
    footprint_name: str = "Synthetic:SOIC",
) -> dict:
    bbox = list(bbox_mm) if bbox_mm is not None else [-1.5, -1.0, 1.5, 1.0]
    return {
        "refdes": refdes,
        "footprint_name": footprint_name,
        "value": refdes,
        "side": side,
        "body_bbox_mm": bbox,
        "height_mm": height_mm,
        "standoff_mm": standoff_mm,
        "pads": [
            {
                "num": "1",
                "type": "smd",
                "shape": "rect",
                "x_rel_mm": -1.2,
                "y_rel_mm": 0.0,
                "w_mm": 0.6,
                "h_mm": 0.8,
                "net": "SIG_A",
            },
            {
                "num": "2",
                "type": "smd",
                "shape": "rect",
                "x_rel_mm": 1.2,
                "y_rel_mm": 0.0,
                "w_mm": 0.6,
                "h_mm": 0.8,
                "net": "GND",
            },
        ],
    }


def _make_spec(components: list[dict], width: float = 24.0, height: float = 18.0) -> dict:
    return {
        "board_outline_mm": {"width": width, "height": height},
        "components": components,
        "nets": {"SIG_A": [], "GND": []},
        "edge_connectors": [],
        "mounting_holes": [],
        "design_requirements": {"power_rails": [], "differential_pairs": []},
        "mechanical_keepouts": [],
    }


def test_assembly_scene_picks_up_bottom_side_from_footprint_layer():
    asym_bbox = [-1.0, -0.5, 2.0, 0.5]
    spec = _make_spec([
        _make_component("U_TOP", side="top", height_mm=2.0, standoff_mm=0.0, bbox_mm=asym_bbox),
        _make_component("U_FLIP", side="top", height_mm=1.5, standoff_mm=0.5, bbox_mm=asym_bbox),
        _make_component("U_FALLBACK", side="bottom", height_mm=1.2, standoff_mm=0.0, bbox_mm=asym_bbox),
    ])
    placed = {
        "U_TOP": (6.0, 6.0, 0.0),
        "U_FLIP": (14.0, 10.0, 0.0),
        "U_FALLBACK": (18.0, 6.0, 0.0),
    }
    board, *_ = _build_blind_kicad_board(spec, placed)
    by_ref_fp = {fp.properties["Reference"]: fp for fp in board.footprints}
    # Simulate two-sided placement updating KiCad footprint layer without mutating spec.
    by_ref_fp["U_FLIP"].layer = "B.Cu"
    by_ref_fp["U_FALLBACK"].layer = None

    scene = assembly_scene(board, spec, placed)
    by_ref = {c["refdes"]: c for c in scene["components"]}

    assert spec["components"][1]["side"] == "top"

    top = by_ref["U_TOP"]
    assert top["side"] == "top"
    assert top["base_z_mm"] == pytest.approx(0.0)
    assert top["z_range_mm"] == pytest.approx((0.0, 2.0))
    assert top["bbox_mm"] == pytest.approx([-1.0, -0.5, 2.0, 0.5])

    flipped = by_ref["U_FLIP"]
    assert flipped["side"] == "bottom"
    assert flipped["base_z_mm"] == pytest.approx(-2.1)
    assert flipped["z_range_mm"] == pytest.approx((-3.6, -2.1))
    assert flipped["bbox_mm"] == pytest.approx([-2.0, -0.5, 1.0, 0.5])

    fallback = by_ref["U_FALLBACK"]
    assert fallback["side"] == "bottom"
    assert fallback["base_z_mm"] == pytest.approx(-1.6)
    assert fallback["z_range_mm"] == pytest.approx((-2.8, -1.6))


def test_assembly_viewer_inspect_camera_resize_and_standoff_leads(tmp_path):
    spec = _make_spec([
        _make_component("U1", side="top", height_mm=1.5, standoff_mm=0.0, bbox_mm=[-2.0, -1.5, 2.0, 1.5]),
        _make_component("U2", side="bottom", height_mm=4.0, standoff_mm=0.0, bbox_mm=[-2.0, -1.5, 2.0, 1.5]),
        _make_component("U3", side="top", height_mm=8.0, standoff_mm=2.5, bbox_mm=[-2.2, -1.6, 2.2, 1.6]),
        _make_component("U4", side="bottom", height_mm=2.0, standoff_mm=1.5, bbox_mm=[-1.8, -1.2, 1.8, 1.2]),
    ])
    placed = {
        "U1": (6.0, 6.0, 0.0),
        "U2": (18.0, 6.0, 0.0),
        "U3": (8.0, 13.0, 0.0),
        "U4": (17.0, 13.0, 0.0),
    }
    board, *_ = _build_blind_kicad_board(spec, placed)
    exported = export_assembly(board, spec, placed, tmp_path)
    html_path = Path(exported["html"])
    assert html_path.exists()

    # 1. Run standard inspect_assembly.mjs and check report + screenshot pixel_std.
    proc = subprocess.run(
        ["node", str(ROOT / "scripts/inspect_assembly.mjs"), str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"inspect_assembly.mjs failed: {proc.stdout}\n{proc.stderr}"
    report = json.loads((tmp_path / "assembly_browser_report.json").read_text())
    assert report["passed"] is True
    assert report["console_errors"] == []
    assert report["external_requests"] == []

    for mode in ("oblique", "top", "side"):
        img_path = tmp_path / f"assembly_{mode}.png"
        assert img_path.exists(), f"Missing screenshot {img_path}"
        arr = np.asarray(Image.open(img_path).convert("RGB"), dtype=np.float64)
        pixel_std = float(arr.std())
        assert pixel_std > 10.0, f"{mode} view pixel_std={pixel_std:.2f} <= 10"

    # 2. Verify preset fit, manual drag + resize clearing viewOffset, and standoff lead Z bounds.
    probe_script = r"""
import {chromium} from 'playwright';
import {pathToFileURL} from 'node:url';

const htmlPath = process.argv[1];
const errors = [];
const browser = await chromium.launch({
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}, deviceScaleFactor: 1});
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(pathToFileURL(htmlPath).href, {waitUntil: 'load'});
  await page.waitForFunction(() => window.pcbViewer?.ready, {timeout: 20000});

  const presetChecks = {};
  for (const mode of ['oblique', 'top', 'side']) {
    await page.evaluate(m => window.pcbViewer.view(m), mode);
    presetChecks[mode] = await page.evaluate(() => {
      const {scene, camera, groups} = window.pcbViewer;
      scene.updateMatrixWorld(true);
      camera.updateMatrixWorld(true);
      const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
      let meshes = 0;
      for (const g of Object.values(groups)) {
        g.traverseVisible(m => {
          if (!m.isMesh) return;
          meshes++;
          if (!m.geometry.boundingBox) m.geometry.computeBoundingBox();
          const b = m.geometry.boundingBox;
          for (const x of [b.min.x, b.max.x])
            for (const y of [b.min.y, b.max.y])
              for (const z of [b.min.z, b.max.z]) {
                const p = b.min.clone().set(x, y, z).applyMatrix4(m.matrixWorld).project(camera);
                min[0] = Math.min(min[0], p.x); max[0] = Math.max(max[0], p.x);
                min[1] = Math.min(min[1], p.y); max[1] = Math.max(max[1], p.y);
                min[2] = Math.min(min[2], p.z); max[2] = Math.max(max[2], p.z);
              }
        });
      }
      const fit = meshes > 0 && [...min, ...max].every(Number.isFinite) &&
        min.every(v => v >= -1) && max.every(v => v <= 1);
      return {fit, viewEnabled: !!(camera.view && camera.view.enabled)};
    });
  }

  // Inspect raised lead world Z spans per component refdes.
  const leadZByRef = await page.evaluate(() => {
    const {scene, groups} = window.pcbViewer;
    scene.updateMatrixWorld(true);
    const result = {};
    groups['元器件'].traverse(m => {
      if (!m.isMesh || !m.userData?.pad) return;
      if (!m.geometry.boundingBox) m.geometry.computeBoundingBox();
      const box = m.geometry.boundingBox.clone().applyMatrix4(m.matrixWorld);
      const ref = m.userData.refdes;
      if (!result[ref]) result[ref] = [box.min.z, box.max.z];
      else {
        result[ref][0] = Math.min(result[ref][0], box.min.z);
        result[ref][1] = Math.max(result[ref][1], box.max.z);
      }
    });
    return result;
  });

  // Manual drag to exit activePreset, then resize window.
  await page.evaluate(() => window.pcbViewer.view('oblique'));
  const beforeDragPos = await page.evaluate(() => window.pcbViewer.camera.position.toArray());
  await page.mouse.move(900, 500);
  await page.mouse.down();
  await page.mouse.move(980, 440, {steps: 5});
  await page.mouse.up();
  const afterDragPos = await page.evaluate(() => window.pcbViewer.camera.position.toArray());

  await page.setViewportSize({width: 960, height: 720});
  await page.waitForFunction(
    ratio => Math.abs(window.pcbViewer.camera.aspect - ratio) < 1e-6,
    960 / 720,
    {timeout: 10000},
  );

  const afterResize = await page.evaluate(() => {
    const c = window.pcbViewer.camera;
    return {
      view: c.view,
      viewDisabled: c.view === null || c.view.enabled === false,
      aspect: c.aspect,
      position: c.position.toArray(),
    };
  });

  console.log(JSON.stringify({
    presetChecks,
    leadZByRef,
    beforeDragPos,
    afterDragPos,
    afterResize,
    errors,
  }));
} finally {
  await browser.close();
}
"""
    probe = subprocess.run(
        ["node", "--input-type=module", "-e", probe_script, str(html_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, f"Playwright probe failed: {probe.stdout}\n{probe.stderr}"
    data = json.loads(probe.stdout.strip().splitlines()[-1])

    assert data["errors"] == []
    for mode in ("oblique", "top", "side"):
        assert data["presetChecks"][mode]["fit"] is True, f"{mode} did not fit frustum"
        assert data["presetChecks"][mode]["viewEnabled"] is True

    # Verify drag actually moved the camera and resize preserved it while clearing viewOffset.
    assert any(
        abs(a - b) > 1e-4 for a, b in zip(data["beforeDragPos"], data["afterDragPos"])
    ), "Manual drag did not move camera"
    assert data["afterResize"]["viewDisabled"] is True, (
        f"Stale camera.viewOffset remained active after drag+resize: {data['afterResize']['view']}"
    )
    assert data["afterResize"]["aspect"] == pytest.approx(960 / 720, abs=1e-6)
    assert data["afterResize"]["position"] == pytest.approx(data["afterDragPos"], abs=1e-6)

    # Verify standoff leads anchor cleanly between board surface and base_z_mm,
    # while default standoff_mm == 0 preserves legacy lead geometry.
    leads = data["leadZByRef"]
    assert leads["U1"] == pytest.approx([0.065, 0.195], abs=1e-4)
    assert leads["U2"] == pytest.approx([-1.795, -1.665], abs=1e-4)
    assert leads["U3"] == pytest.approx([0.0, 2.5], abs=1e-4)
    assert leads["U4"] == pytest.approx([-3.1, -1.6], abs=1e-4)
