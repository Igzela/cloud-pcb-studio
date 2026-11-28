"""Measure routed KiCad segment and arc widths against an explicit floor."""

import argparse
import json
from pathlib import Path

from cloud_pcb.verify_gate.trace_widths import audit_trace_width_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('board', type=Path)
    parser.add_argument('--min-width-mm', type=float, required=True)
    args = parser.parse_args()
    report = audit_trace_width_file(args.board, args.min_width_mm)
    print(json.dumps(report, indent=2, allow_nan=False))
    return {'PASS': 0, 'FAIL': 1, 'UNKNOWN': 2}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
