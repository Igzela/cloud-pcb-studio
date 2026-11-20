# FastRoute 0.1.14 reproducibility probe

Only generated synthetic DSN files were used. No board was fetched, opened or rerun. No project code, validation gate, reports, inputs or router binaries were changed. Evidence occupies less than 1 MiB in ignored scratch.

## Reproduce

From repository root with the tested pinned binary and dependencies:

```bash
source scripts/env.sh && PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" python .run/fast-repro-probe/probe.py
source scripts/env.sh && PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" python .run/fast-repro-probe/probe.py extras
```

Each nested router and workload shell also sources env. The observed fixture has four signal layers, 14 two-terminal nets / 28 SMD pads, a 32 × 32 mm board, three barriers on every copper layer, 0.16 mm traces, 0.15 mm clearance and 0.50 mm copper-to-edge constraint. Fixtures maintain same path and immutable content across conditions. The first synthetic DSN SHA-256 is `26a84138f51512f3ffee6051c914c7fcfbeef436479f80783ac95585ad263814`. A six-net negative control has an all-copper-layer center divider and three strictly blocked nets. The process and optional CPU worker are pinned to the same available CPU and both run nice +19. Conditions are two observed quiet starts, a 1.3 s process suspension following the CLI mode line, or a 0.6 s co-pinned CPU worker. Quiet describes absence of a synthetic worker, the shared machine can be active. Extra controls suspend at actual autorouter/optimizer start to avoid the small race in whether the timer thread has entered its sleep after the CLI mode line. The probe has a 12 s process watchdog.

The raw router JSON, SES and logs are included. `runs.json` and `extras.json` have exact SES SHA-256 plus a canonical copper digest of sorted segments and vias, with segment orientation normalized and coordinates retained at the bridge's 0.0001 mm parse resolution. Router metrics are the observed FastRoute counts, independent product gates were beyond this synthetic reproducibility task.

Binary SHA-256 `a4efebd6bfbb6614c9bac0535441dd65983d1a8e2cb3e00ebcf3ce90f2757d9f`. Vendor source tag `v0.1.14` at `19c3b75322da6a0dc2f87a616fc0afe34a98f07e`.

## Observed behavior

| Configuration | Control | Unrouted / raw spacing | Length mm | Result |
| --- | --- | ---: | ---: | --- |
| Wall per-search + 1 s global wall timer | Two quiet and co-pinned CPU load | 0 / 0 | 468.6172 | Same SES and copper digests, all global timer stops |
| Count per-search + 1 s global wall timer | Two quiet and co-pinned CPU load | 0 / 0 | 468.6172 | Same as wall quiet, all global timer stops |
| Wall or count per-search + 1 s global timer | 1.3 s suspension at autorouter start | 14 / 0 | 0 | Both exit 0 and write final report + SES after global stop |
| Wall per-search + 4.5 s global timer | Quiet / 4.7 s suspension at optimizer start | Both 0 / 0 | 463.8211 / 468.6172 | Both exit 0 and have 26 vias. SES and canonical copper digests differ |
| Default enhanced pipeline + `--no-time-limits` and no global timer, 2 threads | Four conditions | 0 / 0 | 463.8211 | Exact SES/copper repeated 4/4, two optimizer passes |
| Enhanced pipeline, no clock stage timer, 2 threads, optimizer off, multi-start 1 | Four conditions | 0 / 0 | 468.6172 | Exact SES/copper repeated 4/4 |
| Enhanced pipeline, no clock stage timer, sequential, optimizer off, multi-start 1 | Four conditions | 0 / 0 | 457.8289 | Exact SES/copper repeated 4/4 |
| Java parity mode, explicit sequential routing and optimizer | Four conditions | 0 / 0 | 457.8289 | Exact SES/copper repeated 4/4, two optimizer passes |
| Count factor 1 with 1 ms fanout pin setup | Quiet / suspended | 0 / 0 | 475.1068 | Actual per-search count limit fired in both runs, identical SES/copper |
| Six-net isolated negative control, 2-pass cap, multi-start 2 | Quiet / suspended at router start | Both 3 / 0 | 92.5852 | One actual variant, 2 initial + 2 variant passes, identical SES/copper |

The initial suspension triggered at the CLI mode message for count hit the global timer immediately while the wall-mode timer began after resume and produced the usual route. The deliberate stage-trigger controls remove this ambiguity. The small fixture reaches two optimizer passes without exercising the local greedy wall budget cutoff and finishes too quickly for slow-pass and multi-start eligibility thresholds. Equal fixture digests do not prove universal determinism.

## Source behavior and implications

- `crates/fastroute/src/main.rs:486–503` and `crates/fr-engine/src/datastructures/time_limit.rs:115–125` distinguish per-operation board time limits, stage deadlines and optimizer processing mode. Count is a budget of calls to `limit_exceeded` using configured milliseconds × factor, not CPU instructions, an exact wall bound or a whole-job budget. Default factor is 10. `--time-limit-mode=count` leaves stage deadlines and the default job monitor enabled. `--no-time-limits` switches the default to count and disables these selected wall clocks. Explicit `--time-limit-mode` can override the count default; `disabled` removes per-operation bounds.
- `crates/fastroute/src/main.rs:576–599,635–719` independently runs the user wall timer for `--max-time` (minimum 1 s). A graceful early stop can exit 0 with final output and bypass post-route pair coupling, pair length tuning or group length tuning. The JSON report from this release has no explicit timeout or search-completed bit. A subprocess wrapper timeout is separate. A complete-looking checkpoint, report, zero unrouted count, and zero spacing count can all coincide with wall-truncated optimization.
- Three wall-based choices in enhanced mode are unaffected by `--no-time-limits`. `crates/fr-engine/src/pipeline/autorouter.rs:1079` can stop unfinished routing on slow-pass stagnation after 20 s/pass. `crates/fr-engine/src/pipeline/mod.rs:197–209` skips multi-start when observed first-run time ×3 for a parallel pass or ×1 for a sequential pass crosses 600 s. `crates/fr-engine/src/pipeline/optimizer.rs:792–817,1011–1018` budgets greedy optimizer follow-up by elapsed time even in no-stage-wall mode. The optimizer's Java-compat setting by itself does not disable this enhanced greedy logic. The 600 s threshold in particular would not explain an ordinary ~110 s run by itself.
- `crates/fr-engine/src/pipeline/autorouter.rs:977–986` applies a positive `max_passes` per routing batch; zero is unlimited. This is separate from optimizer/fanout limits. `pipeline/mod.rs:248–288` fixes each variant seed, uses sequential passes inside variants, collects variants in index order and scores them against the same number and type of gates. It launches additional variants only while connections remain unrouted. Coupled-pair retry can invoke a second routing pipeline with its own pass budget. Pass caps alone do not cap copies, board sizes, individual post-processing costs or whole-job elapsed time.
- `crates/fr-engine/src/pipeline/autorouter.rs:550–681` dispatches speculative parallel work with fixed window and commits the oldest ticket. `pipeline/optimizer.rs:935–960` consumes scored optimizer candidates in predetermined order, within stopped-prefix boundaries. That is evidence against assuming first-worker-wins. The selected parallel and sequential algorithms can still produce different valid solutions. Pin both autorouter and optimizer thread counts explicitly. Defaults derive both from machine-visible processors.
- `--parity` enables count/no stage walls and defaults the optimizer mode to Java compat, but also disables enhancements in `crates/fastroute/src/main.rs:409,451–470,502–531,715`. This changes class-pair clearance propagation, overlap contact handling, fixed stitching vias and other geometry/routing behavior. It is a meaningful algorithm change, so the synthetic parity result does not authorize adoption for PCB production. Passing explicit pair controls is distinct from enhancement selection.

## Recommendation

Keep observed current candidate artifacts and classify their actual termination transparently. In the wrapper, timeout evidence can distinguish a subprocess watchdog exit, an internal wall-limit stop, a delivered stop signal, stage deadlines, and count-limit events. Report both canonical copper and exact SES hashes, retain the input DSN and execution configuration digests, and preserve optimizer or final pair-processing stage evidence. Never use repeated real-case runs to select the best accidental wall checkpoint.

A flag-only deterministic experiment on a never-opened fresh cohort can set `--no-time-limits`, explicit positive count factor, a positive autorouter cap `M`, `min_passes=M+1` to mask the wall-based stagnation decision, `multi-start=1`, optimizer off, explicit fixed worker counts and fixed fanout caps. Preserve all layer, net, geometry and independent physical-gate rules. An external hard watchdog bounds system resource use and marks a killed attempt as incomplete, so its checkpoint cannot masquerade as the scheduled result. This sacrifices variants and optimization and should not be chosen without independently verifying all board gates and quality. The synthetic fixture is evidence to test an experiment, not a production default.

For a router build that retains enhanced optimization and multi-start, guard slow-pass and multi-start wall decisions under the deterministic mode, replace greedy's local elapsed budget with a fixed candidate-attempt budget, keep deterministic callback/commit order and count-limited operations, and add a truthful structured stop reason. Pin separate routing/fanout/optimizer pass counts and retain the independent external watchdog as a resource safeguard. Test exact/canonical repeatability on synthetic congestion, pair tuning, plane/keepout and mixed geometry before adopting it for a uniformly frozen board cohort. A source change and broad acceptance run are outside this probe.
