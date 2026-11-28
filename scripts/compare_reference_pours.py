"""Compare the saved zone fill for one net across two KiCad PCBs."""

import argparse
import json
from pathlib import Path

from kiutils.board import Board

from cloud_pcb.verify_gate.reference_pours import compare_reference_pours


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--net', required=True, help='Exact name of the reference net')
    parser.add_argument('--layer', action='append', help='Only measure this layer. May be repeated.')
    args = parser.parse_args()
    try:
        boards = [Board.from_file(str(path)) for path in (args.baseline, args.candidate)]
    except Exception as exc:
        # KiUtils also raises bare Exception on malformed board files.
        report = {'status': 'UNKNOWN', 'net': args.net, 'layers': {},
                  'issues': [str(exc) or 'Could not read the board']}
    else:
        report = compare_reference_pours(*boards, args.net, layers=args.layer or ())
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report['status'] == 'MEASURED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
