# Bounded placement rollback

The prototype evaluates an unsuccessful typed placement against the existing body, copper, drilled-hole and keepout predicates. It considers target and board-edge anchors for the stalled part and identifies which already placed parts actually obstruct each feasible pose. It may move at most two unpinned parts per proposal and returns a complete placement mapping only after rechecking the displaced parts against the resulting occupancy. Input poses are immutable to the search.

The search uses 6,500 geometry probes by default, up to 80 candidate probes per anchor and 20 proposals across the stalled part, with at most two alternatives per displaced part per anchor. The probe limit and the proposal limit have distinct output statuses. A search can miss a legal packing even if it stays within these limits.

## Evidence

The 12 × 7 mm positive fixture is explicitly top-side. Both production greedy orders place the 6 × 5 mm part at the board center and exhaust when adding a 4 × 4 mm part. Rollback moves the 6 × 5 mm part, places the missing part and preserves the pinned component at its original pose. The tested impossible fixture has 55 mm² of same-side component bodies for 48 mm² of board area. A separate test forces probe exhaustion at the first geometry probe. The fixtures contain no reference PCB data.

```sh
source scripts/env.sh
PYTHONPATH="$PWD" nice -n 15 python -m pytest -q .run/typed-packing-rollback/test_rollback_probe.py --tb=short
```

The test run reported 4 passed in 1.88 s. See `measurements.json` for deterministic probe counts and transforms.

## Integration considerations

Invoke the search only when typed greedy packing stalls. Feed it the accepted prefix, fixed obstacles, typed bounds and the same side and orientation authority. Apply the returned positions as a batch before computing subsequent placement targets. Preserve the original prefix if the attempt fails. If the caller allows multiple rollbacks, cap their cumulative probes along with the per-stall limits and record moved references, statuses and probe counts. A budget shared fairly among proposal anchors would give later anchors a chance when an early proposal contains two blockers.

This prototype uses board-local coordinates and takes the already normalized board geometry. It verifies placement feasibility on its fixtures. Routing and fabrication gate effects require a fresh frozen evaluation.
