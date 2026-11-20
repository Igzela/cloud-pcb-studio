# Bounded joint corridor check

The prototype is read-only and scoped to two or three affected nets on the
same copper layer. It does not generate a production board or change any
calibrated width. Input anchors belong to an exact µm lattice; protected
copper and keepouts are supplied as physical polygons with class rules.
The low-level shortest-path search checks every grid edge against actual
immutable copper and the 0.50 mm edge rule. It uses an outward bound for
mitred corners and the larger of the two participating clearance rules.
When independently chosen paths conflict, the high-level search branches
on which conflicting edge to ban and replans that net. Branches and total
path expansions are capped. No infeasible or partial result is emitted.

The synthetic fixture keeps a ground-style floor obstruction fixed. The
incumbent's previously connected horizontal trace divides every available
escape corridor. Holding it in place makes the orphan's connection
infeasible. Joint search admits both the original electrical connections
by detouring only the first net. Two exact physical trace envelopes clear
one another, the fixed obstruction and the board edge. An independently
rebuilt layered PCB has zero opens and shorts. Numerical results are in
`evidence.json`. Four tests exercised this, forced impossibility, bounded
failure, and an even-count or lower-count replacement that moves an open.

The comparison checks physical pad-group partitions, not just counts.
A newly open net, splitting an originally connected terminal group, new
short/VIP, or incidental change outside the authorized affected-net set is
rejected. A disappearing original open counts only with known unaffected
netlist/source provenance. Actual integrations must also compare immutable
copper signatures and unchanged netlist/placement, exact occupancy and
raw engine spacing, then rerun all existing full-board hard gates. The
helper does not replace the optimization eligibility policy.

Read-only check of the promoted seven-case Case 3 confirmed its DSN and
selected SES are byte-identical to the earlier real-router corridor trial.
The new topology comparison correctly rejects that trial: the original
open is removed and one is introduced on its neighbor. No claim of a real
Case 3 improvement follows from the synthetic result. Production expansion
needs allowed multilayer graph transitions and class-aware via capture;
model both stub and via geometry plus preserve same-net attachment against
the selected incumbent. Continue to require class-preserving engine
replay and physical LVS before any changed path can be accepted.

```bash
source scripts/env.sh
scratch=.run/corridor-witness-20261119/coescape
PYTHONPATH="$scratch:." nice -n 15 python -m pytest -q "$scratch/test_joint_corridor.py"
```
