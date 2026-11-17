# Cloud PCB Studio

Headless Cloud Multi-Agent EDA Workbench & Board-Agnostic Blind PCB Solver:
- **Parts & DFM (`cloud_pcb/parts_dfm`)**: BOM verification, JLC04161H-7628 4-layer stackup, masked impedance calibration, `ngspice` power/ESD/Monte Carlo simulation.
- **Layout & Route (`cloud_pcb/layout_route`)**: Pipeline A (`@tscircuit/core` -> KiCad/Gerbers) & Pipeline B (KiCad + Specctra DSN/SES bake-off: `fastroute v0.1.14` vs `freerouting-cli v2.5.0`).
- **General Blind Solver (`cloud_pcb/general_solver`)**: Board-agnostic placement, decoupling assignment, SMD GND fanout vias, 4-layer GND pours & stitching vias, Gerber/Excellon export, and `compare_reference.py`.
- **Special IP (`cloud_pcb/special_ip`)**: Planar stacked coils (`In1.Cu` / `In2.Cu` constructive flux), chevron CapTouch slider, curved Chaikin routing.
- **Verify Gate (`cloud_pcb/verify_gate`)**: Strict `pygerber` RS-274X parse, `gerbonara` Excellon check, Shapely topological LVS/DRC, headless Chromium visual audit.

## Quick Setup (Linux / Codex Cloud)

```bash
# Always load the shared environment BEFORE running Python or either router.
source scripts/env.sh

# 1. Python venv + dependencies
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Node dependencies (@tscircuit/core, esbuild, etc.)
npm install
npx playwright install chromium

# 3. Ensure ngspice & chromium/playwright are available if running full verify gate
#    (fastroute & freerouting-cli binaries are included in ./bin/)
source scripts/env.sh
python -m pytest -v
```

Use the optimization workflow for new work. It preserves the frozen v3
artifacts and records each experiment in its own directory:

```bash
source scripts/env.sh
python -m scripts.optimize_pcb status
source scripts/env.sh
python -m scripts.optimize_pcb run --label my-change --hypothesis "Describe the generic change and expected improvement"
source scripts/env.sh
python -m scripts.optimize_pcb history
```

Open `artifacts/blind_v3_caseN/assembly.html` directly in a browser; it contains
its own Three.js code and model geometry. Parametric package envelopes and
estimated heights are identified in `assembly_scene.json`; zero envelope
collisions alone does not certify unknown real mechanical dimensions.
See [the research and implementation limits](docs/v3-design-decisions.zh-CN.md).

The workflow runs pytest, all regression boards, strict audits and offline
browser checks before comparing each board against the baseline. A regression
on one board cannot be hidden by gains on another. An eligible experiment only
updates an artifact baseline pointer; it does not certify engineering quality.
Unsealed cases 4 and 5 are regression data. Sealed holdouts are excluded from
optimization. See [the Chinese optimization guide](docs/optimization-workflow.zh-CN.md).
The [first measured round](docs/optimization-first-round.zh-CN.md) records both
the connectivity improvement and the regression that prevented promotion.

The experimental `--variant supply-semantics` enables supply inference from
hierarchical voltage labels and typed power pins. It keeps evidence in
`effective_spec.json` and never invents missing current ratings. The default
solver remains on the stable behavior while the candidate is evaluated.

The `--variant scoped-supply` experiment distinguishes a single IC supply
domain, evidenced by typed supply pins, shunt return capacitors and passive
R/L/C endpoints, from board-level distribution. Unknown-current local domains
have an independent routing intent; backbone and explicit current/width
requirements retain priority. Scope and width provenance remain in the output.

For the user-selected independent Gemini reference, use
`scripts.run_gemini_reference` with model `gemini-4-hydrogen` and a runtime
credential. It sends anonymous blind input and records API provenance; it does
not treat a generated placement proposal as a routed or verified reference.
See [the external-reference protocol](docs/gemini-reference.zh-CN.md).

`scripts/run_blind_v3.py` is retained as the historical experiment entry point;
it writes the fixed v3 directories. Use `scripts.optimize_pcb` for daily work
so existing frozen results remain available for comparison.
