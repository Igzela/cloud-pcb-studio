"""Independently check the two native Excellon exports of a KiCad board."""

import argparse
import json
from pathlib import Path

from kiutils.board import Board

from cloud_pcb.verify_gate.fabrication_drills import audit_fabrication_drills, read_fabrication_drill_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board', type=Path)
    parser.add_argument('pth', type=Path, help='Plated drill file')
    parser.add_argument('npth', type=Path, help='Non-plated drill file')
    parser.add_argument('--y-flip-mm', type=float, required=True, help='Value of H in the native export transform (x, H - y)')
    args = parser.parse_args()
    try:
        report = audit_fabrication_drills(
            Board.from_file(str(args.board)), read_fabrication_drill_file(args.pth), read_fabrication_drill_file(args.npth),
            y_flip_mm=args.y_flip_mm,
        )
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({'passed': False, 'error': f'{type(exc).__name__}: {exc}'}, indent=2))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
