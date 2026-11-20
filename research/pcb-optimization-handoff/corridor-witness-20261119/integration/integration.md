# Pair-atomic launch opt-in integration

This is a proposed next experiment. The tracked solver and frozen regression
suite are untouched. The scratch prototype admits and renders launch copper;
the independent test reconstructs copper after a real fastroute run. It does
not select a candidate in production and does not certify impedance or SI.

## Before routing

Use the existing placed board after its fixed ground fanout and before
pouring. Preserve canonical blind spec, netlist, footprint poses, pair file,
allowed routing layers, all per-net classes and clearances, and envelope
rules. Reuse the actual fastroute command's permitted neck width and
manufacturing floor. Require an explicit experimental neck length cap.
If the incumbent command permits .160 mm, this opt-in may not authorize
.125 mm instead. Respect the existing no-neckdown class list.

The prototype handles unique co-located paired SMD terminals with orthogonal
outward pad axes. It does not infer pair identity from refdes or net text.
It computes starts from the exact DSN quantized pad centers and offsets
each polarity symmetrically on the same layer until the nominal full-width
corridor can meet the specified gap. Both short necks have equal length. The
full-width anchors are at least two class widths long. All geometry is integer
µm, orthogonal or 45°, with a clearance margin around bends. The first
prototype does not add vias. Per group the planner checks at most ten lead
lengths and per board at most four groups. It declines repeated pad labels,
ambiguous shared pair membership, nonorthogonal or unprovable outward axes,
incompatible layers or class rules, unrestricted width changes, no clearance,
and exhausted budgets.

Each candidate is checked against physical copper, DSN pad envelopes,
board Edge.Cuts, holes, fixed traces, route-applicable keepouts and already
admitted candidates. The maximum applicable class clearance applies;
the pair trace-to-trace gap is also checked. If one constrained endpoint
cannot be seeded, roll back all reservations for both members of that pair.
The existing baseline remains eligible even for unsupported via or
multi-terminal topologies. The prototype only changes the empty `wiring`
scope in a candidate copy of the emitted DSN. No other bytes change.

## Routing and actual fallback

Integrate as a new uniform opt-in variant, not as a case-specific patch or a
change to the stable router. The ordinary solver completes its normal
generation and audits and is the incumbent. Build candidate DSN from the
same initial board and keepouts. Try the same primary fastroute command with
`--pairs` in a separate candidate outdir. Do not make extra width or layer
changes, independent member retries or repeated seeds on identical input.
Allow one proposal and trial per board. On timeout, parse error, failure to
reserve, or any validation disagreement, keep the incumbent physical board
and all its unchanged measurements. Record rejected raw trial evidence.

Wire seeds are loaded as SystemFixed with `(type fix)` and not returned in
SES. Explicitly merge those exact admitted segments with only the returned
SES copper into a fresh copy of the pre-route board. Confirm both polarities
use their actual seed anchors and separately meet the topology rule. Re-run
engine verification on resolved copper with per-net class-preserving DSN
wiring. Avoid legacy raw SES reimport as it changes Trace/Via classes.
Confirm no engine reroute touched seed widths or omitted copper. Follow
with ordinary pours, Gerbers and independent physical audits.

Require sufficient pair observability before candidate selection. The first
real integration should limit acceptance to declared pairs with a unique
two-terminal correspondence and unbranched physical path per polarity.
Check both endpoint paths, full trunk widths on actual emitted geometry,
local neck width and length budgets, same permitted layers, edge gaps and
co-routed trunk tolerance with a declared tolerance, and end-to-end skew
including real via lengths if ever supported. Do not infer constituent path
lengths by summing a branched net. A multi-terminal pair needs explicit role
mapping and independent path-by-path checks before expanding the scope.
Do not count either fastroute's `coupled` token or an unbranched net-length
sum alone as evidence of electrical success. Finally use the existing
whole-board hard checks and strict per-case fallback comparison. A route
that shifts an open to the mate or another net is rejected. Keep engineering
acceptance unresolved unless the stack-up and independently verified SI
support it.

## Reproduction

From the repository root:

```bash
source scripts/env.sh
scratch=.run/corridor-witness-20261119/integration
PYTHONPATH="$scratch:." python -m pytest -q "$scratch/test_pair_launch.py" \
  --basetemp="$PWD/$scratch/artifacts"
```

16 tests passed. Five x/y orientation transforms, blockers and atomic
declines validate the planner. The final test runs real isolated fastroute
primary control and treatment, preserves outline/pad/class bytes, explicitly
restores fixed wiring, verifies independent copper LVS/DRC topology plus
actual widths, actual trace pair gap and centerline skew. The control has
0 unroutes and 0 raw spacing violations, yet its actual gap is below .330.
The treatment stays within its established neck budget and has over 18 mm
at the full .290 trunk width on both polarities, at least .330 actual gap,
and no new physical opens or shorts. This verifies only the synthetic planar
case. No inference of Case11 end-to-end success is made.
