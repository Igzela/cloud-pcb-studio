# Exposed Case 10 polygon-pad diagnostic

A single solve completed in 394.84 seconds at nice +15 while the production queue was active. It used an executable-only scratch copy of commit `59fd274` with the draft polygon pad export enabled for both engines. The only input was the previously exposed Case 10 profile. No tracked file, sealed input, reference PCB, frozen output or production job was changed for this diagnostic.

The baseline was the Case 10 result in `20261119T165010331810Z-annular-exact-seven`. The baseline report and input were checked against that completed manifest. The manifest digest is `beacfb3d2941f025dd21269099f47a797e7fa03983cd2760c71cf7bb163a669f` and the identical input digest is `1eb7577a45172208c0b2365ba109639d2da8e536fad991820d589b5eebe39ca6`. The legacy-compatible placement path was retained with all 84 poses, physical sides, and ground fanout matching. Both routers' DSN structure, network, placement and wiring sections matched the baseline. The primary DSN used 26 polygon padstacks for 190 pins. `--freerouting-jvm --direct-repair --placement-recovery` matches the baseline solver flags. This is an isolated single-board quality observation, not a comparable timing experiment or promotion run.

| Measurement | Annular baseline | One polygon-pad solve |
| --- | ---: | ---: |
| Primary raw unrouted connections | 4 | 4 |
| Primary independent LVS open networks | 2 | 2, same nets and split counts |
| Primary independent shorts and nonthermal via-in-pad | 0 / 0 | 0 / 0 |
| Primary raw / classified spacing findings | 12 / 12 | 12 / 12 |
| Primary vias | 82 | 82 |
| Primary raw route length on incomplete board | 1679.9014 mm | 1592.3864 mm |
| Measured physical minimum inter-net clearance | 0.1317 mm | 0.1317 mm |
| Gerber layers independently parsed | 11/11 | 11/11 |
| Minimum annular ring and violations | 0.1000 mm / 0 | 0.1000 mm / 0 |
| Copper edge margins by layer | 0.5000 mm on four | 0.5000 mm on four |
| Silk overlap area | 0 mm² | 0 mm² |
| Inner ground fraction | 86.84% | 86.84% |
| RF findings and all hard-gate states | — | unchanged |
| Freerouting reported unrouted connections | 31 | 26 |
| Freerouting independent LVS open networks | 21 | 20 |
| Freerouting reported spacing findings / illegal-layer tracks | 12 / 0 | 12 / 0 |

Primary routing continued to select individual routing with length tuning. All reported primary failures, including unresolved mechanical verification, remain recorded. The shorter primary raw length receives no quality credit because both boards still have opens. The changes in the secondary snapshot did not remove either engine's spacing issues. There is no observed worse metric in this single screen, but no adoption or promotion recommendation.

## Why the four plated-pad records remain

The physical pads that generated the repeated 0.130 mm records had a foil-to-foil gap of 0.147641 mm. The legacy rectangular DSN representation had roughly 0.0730 mm gap. The new DSN polygons give 0.137660 mm and the five newly polygonized copper objects implicated in the listed pair each conservatively enclose the corresponding measured pad shape. FastRoute's default search/DRC tree replaces each polygon with a 45° envelope, whose predicted gap is 0.069000 mm. That agrees with the actual 0.0691 mm engine value and explains why the four records continue to flag. The other eight pin records also remain. All are left unfiltered.

## Unrestricted-angle test boundary

A separate future variant can explicitly test `(snap_angle none)` with the same pin geometry, copper clearance, widths, via geometry, fixed constraints, edge distances and independent audits. Both engine implementations then use the polygon's convex tile instead of the 45° octagon for default search-tree drill shapes. The prior **synthetic** off-axis capsule fixture showed that switching only this constraint removed its two unrouted items and one false-positive spacing record. Each engine's resulting synthetic tracks included two 56.5925° segments, so this option demonstrably changes the permitted routed angles. This Case 10 diagnostic remained at 45° and has no unrestricted-angle run.

Any future variant must record the angle policy, use the same settings across the preregistered regression cases, and retain the unfiltered engine report and existing acceptance gates. Check whether the board or contract explicitly requires 45° geometry before considering it eligible. Independent copper verification should cover rule-specific spacing, manufacturing minima, completeness, and trace geometry close to free-angle bends. Compare non-45° segment count and bearings, acute bends, narrow local necks or slivers, branch length and differential skew, via count, layer restrictions, plane clearances and reference continuity, as well as the ordinary tests. These are measurable tradeoffs from a change in routing search and allowed path topology, even when nominal electrical spacing is identical. Staying at strict 45° requires an engine that can use more faithful copper obstacles while preserving allowed trace directions, or a placement where the default envelopes have adequate clearance. Neither can be inferred from a change to the report filter.

`comparison.json` includes both complete metric sets and verification flags. `pin-geometry.json` records the source of retained pin findings. `pre_route_compare.json` records DSN parity. Raw reports, SES files, Gerbers and the routed PCB are in `result/`. The new scratch evidence occupies about 13 MB and has not been staged. The production queue and historical working files remain separate.
