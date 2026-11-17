# Cloud PCB Studio

Headless Cloud Multi-Agent EDA Workbench & Board-Agnostic Blind PCB Solver:
- **Parts & DFM (`cloud_pcb/parts_dfm`)**: BOM verification, JLC04161H-7628 4-layer stackup, masked impedance calibration, `ngspice` power/ESD/Monte Carlo simulation.
- **Layout & Route (`cloud_pcb/layout_route`)**: Pipeline A (`@tscircuit/core` -> KiCad/Gerbers) & Pipeline B (KiCad + Specctra DSN/SES bake-off: `fastroute v0.1.14` vs `freerouting-cli v2.5.0`).
- **General Blind Solver (`cloud_pcb/general_solver`)**: Board-agnostic placement, decoupling assignment, SMD GND fanout vias, 4-layer GND pours & stitching vias, Gerber/Excellon export, and `compare_reference.py`.
- **Special IP (`cloud_pcb/special_ip`)**: Planar stacked coils (`In1.Cu` / `In2.Cu` constructive flux), chevron CapTouch slider, curved Chaikin routing.
- **Verify Gate (`cloud_pcb/verify_gate`)**: Strict `pygerber` RS-274X parse, `gerbonara` Excellon check, Shapely topological LVS/DRC, headless Chromium visual audit.

## Quick Setup (Linux / Codex Cloud)

```bash
# 1. Python venv + dependencies
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Node dependencies (@tscircuit/core, esbuild, etc.)
npm install

# 3. Ensure ngspice & chromium/playwright are available if running full verify gate
#    (fastroute & freerouting-cli binaries are included in ./bin/)
source scripts/env.sh
python -m pytest -v
```
