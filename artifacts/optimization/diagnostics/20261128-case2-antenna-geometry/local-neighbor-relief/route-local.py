import argparse
import json
from pathlib import Path

from cloud_pcb.general_solver.run_blind import run_blind_solver

parser = argparse.ArgumentParser(description='Replay the local antenna placement diagnostic.')
parser.add_argument('--spec', type=Path, required=True)
parser.add_argument('--placements', type=Path, required=True)
parser.add_argument('--outdir', type=Path, required=True)
args = parser.parse_args()
placement = json.loads(args.placements.read_text())
result = run_blind_solver(args.spec, args.outdir, external_placement=placement, dense_escape_repair=True)
print(json.dumps(result['hard_gates'],indent=2))
