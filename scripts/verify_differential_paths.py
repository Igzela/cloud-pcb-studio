"""Measure two-terminal differential path length and geometric skew in KiCad."""

import argparse
import json
from pathlib import Path

from kiutils.board import Board

from cloud_pcb.verify_gate.differential_paths import measure_differential_paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board', type=Path)
    parser.add_argument('--spec', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = measure_differential_paths(Board.from_file(str(args.board)), json.loads(args.spec.read_text()))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({'passed': False, 'error': str(exc)}, indent=2))
        return 2
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report['passed'] else 1 if any(pair['status'] == 'FAIL' for pair in report['pairs']) else 2


if __name__ == '__main__':
    raise SystemExit(main())
