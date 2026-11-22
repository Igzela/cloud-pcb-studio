# Exposed-board escape diagnosis and synthetic engine check

Only frozen solver artifacts for exposed Case 3 and Case 11 were used. No reference PCB or sealed intake was opened. No tracked file was edited. The engine ran only the isolated synthetic pair fixture below.

## Exposed geometry

Case 3 final board.dsn and selected SES are byte-identical to the prior per-net fixed-wiring control and one-victim trials. See /tmp/pcb-case3-rescue.4myv7h/findings.md. The clean single-ended local trial closed the intended high-speed net while opening its immediate neighbor instead. Retain the final board; test joint neighboring escapes in a bounded local experimental variant.

Case 11 solver artifacts show one open member of a required differential pair. The frozen pair class uses .290 mm width, .248 mm clearance, .330 mm target coupled gap, .20 mm maximum skew, F.Cu/B.Cu and no In2.Cu routing. The configured individual fallback already uses .125 mm necking. Admission at those dimensions is not interchangeable with independent full board SI closure.

On the .50 mm pitch / .25 mm pad minor width transmitter row, the exact centered pad launch at .290 has approximately -.018 mm class clearance margin. A .125 mm launch fits before existing routing. A 50 µm octilinear search of a 5 mm by 5 mm window around each transmitter pad falls from 3,478 top-reachable cells and 2,223 legal through-via landing samples to 56 cells and no legal landing samples for the affected member after loading selected copper. Its mate falls from 4,006 cells and 2,710 via samples to 63 cells and no local via sample. These are bounded witnesses, not global impossibility proofs.

The frozen pre-route geometry admits jointly feasible symmetric 1.22294 mm local launches with .125 mm necks and either .290 mm planar continuations or two .55/.30 mm plated vias with .336 mm pad-edge gap. Selected copper blocks those specific corridors. These two via witnesses intersect or approach selected copper from seven distinct nets, including a protected supply, the other differential pair and the clock. An early pair-atomic launch reservation has a smaller affected scope than cutting those settled routes. A complete coupled route and SI remain unverified for Case 11.

Inputs to these observations are captured in `phy-escape-probe.json`, `joint-probe.json` and `blockers.json`. The conservative probe checks both physical copper pads and fastroute's quantized DSN pad envelopes, actual solder pad/annulus geometry, board edge, foreign copper in each permitted layer and through-via obstructions in all four layers.

## Small real fastroute fixture

The fixture contains two mirrored four-pad synthetic rows, two netlisted differential lanes, two single-endpoint fixed flank pads on each row, 0.50 mm center pitch and 0.25 mm minor pad width. The pair uses the same synthetic .290 / .248 / .330 / .20 intent, with no footprint or board-scale dependencies. The control and treatment DSNs differ only by the treatment's eight fixed wiring records. Board outline, pads and routing class are identical and hashed by source comparison. Independent geometric admission includes mitred copper corners and rejects an inserted obstacle, an artificially compressed pair and a .290 initial center launch.

Exact engine invocation for the two scratch designs is recorded in the turn. Both primary `--pairs` runs with .125 neck/floor returned 0 airwires and 0 spacing violations. Both independently have zero copper LVS opens/shorts. Unseeded, each nominal .290 lane routes entirely at .250 for 20 mm, with a .2502 mm inter-track gap. Seeded, the actual merged PCB has 18.076 mm at .290 and 1.975362 mm of .125 neck per polarity, a .334 mm minimum track-pair gap, and zero centerline length skew. The engine reports 20.05 mm of geometric coupling on the seeded pair versus 0.00 mm unseeded. Both logs separately report “follower could not be connected” from attempted re-coupling, so this does not establish automatic re-coupling success. It proves a legal geometric continuation in a simple case.

The fixed records become 12 physical trace segments. They are omitted from the final engine SES as expected for SystemFixed copper. The independent audit explicitly reinstates just those segments, then adds the emitted SES delta. No physical trace is assumed present solely because it appears in a raw router count.

## Generic next variant

- Detect a geometrically constrained paired pad group using intended rule data and actual footprint pad centers, not net naming or board identifiers. Pair endpoint matching must have unambiguous same-footprint source and corresponding downstream terminals. Match both polarities together.
- Reserve two bounded equal-length short necks only if the established local width allowance permits them, then spread to the class target center gap and restore full class width. Check all resulting geometry together under the actual physical copper, keepout, board edge, drill and effective class-pair clearance rules. If vias are considered, preserve the normal allowed layer set and require matched geometric role. Avoid fixed orphan vias and physical VIP.
- Put both launches, or neither, into DSN fixed wiring. Preserve class, footprint and outline scopes. Use the same primary coupled router on a new candidate; no uncoupled route should pass solely because its total copper lengths happen to match. Keep raw failure modes and checked geometry, then require both independent LVS and measured class-width/gap/skew results. Incorporate demonstrated end-to-end SI only when independently available.
- Gate candidate generation and attempts by small deterministic budgets. Clean boards and negative neighborhoods retain their selected geometry. Preserve the frozen baseline if any per-board metrics regress or the opponent pair member becomes open.
