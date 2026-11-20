"""Synthetic only. Run from repository root after sourcing scripts/env.sh."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import threading
import time

from cloud_pcb.layout_route.dsn_ses_bridge import parse_ses

ROOT = Path(__file__).resolve().parent
LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu")
CPU = max(os.sched_getaffinity(0))

def fixture(path, parallel_nets, barriers=True, partition=False):
    components, nets = [], []
    n = parallel_nets
    for i in range(2 * n):
        k = i % n
        j = (k * 5 + 7) % n
        t, s = 2800 + k * 1320, 2800 + j * 1320
        (ax, ay), (bx, by) = ((1100, -t), (30900, -s)) if i < n else ((t, -1100), (s, -30900))
        components.append(f"(place PA{i} {ax} {ay} front 0) (place PB{i} {bx} {by} front 0)")
        nets.append(f"(net NET{i} (pins PA{i}-1 PB{i}-1))")
    layers = ' '.join(f"(layer {layer} (type signal) (property (index {i})))" for i, layer in enumerate(LAYERS))
    vias = ' '.join(f"(shape (circle {layer} 550))" for layer in LAYERS)
    obstacles = ' '.join(f'(keepout "" (rect {layer} {x0} {y0} {x1} {y1}))'
        for layer in LAYERS for x0, y0, x1, y1 in [(9000,-14000,12000,-7000),(16500,-26000,18500,-17000),(21000,-14000,23800,-9000)]
        ) if barriers else ""
    if partition:
        obstacles += " " + " ".join(f'(keepout "" (rect {layer} 14500 -32000 15500 0))' for layer in LAYERS)
    path.write_text(f'''(pcb {path.stem}.dsn
  (parser (string_quote ") (space_in_quoted_tokens on) (host_cad "KiCad's Pcbnew") (host_version "8.0.0"))
  (resolution um 10) (unit um)
  (structure {layers} (boundary (rect pcb 0 -32000 32000 0)) (via Via[0-3]_550:300_um)
    {obstacles} (rule (width 160) (clearance 150)))
  (placement (component PADIMAGE {" ".join(components)}))
  (library (image PADIMAGE (pin PAD 1 0 0))
    (padstack PAD (shape (rect F.Cu -250 -250 250 250)) (attach off))
    (padstack Via[0-3]_550:300_um {vias} (attach off)))
  (network {" ".join(nets)}
    (class PLAIN {" ".join(f"NET{i}" for i in range(2*n))} (circuit (use_layer {" ".join(LAYERS)})) (rule (width 160) (clearance 150))))
  (wiring))
''')
    return path

def digest(data):
    return hashlib.sha256(data).hexdigest()

def canonical(path):
    model = parse_ses(path)
    segments = sorted((s["net"], s["layer"], s["width_mm"],
        *sorted([(s["x1"], s["y1"]), (s["x2"], s["y2"])])) for s in model["segments"])
    vias = sorted(json.dumps(v, sort_keys=True) for v in model["vias"])
    return digest(json.dumps([segments, vias], separators=(",", ":")).encode())

def run(path, mode, condition, flags):
    out = ROOT / f"{path.stem}-{mode}-{condition}"
    ses, report, log_path = (out.with_suffix(ext) for ext in (".ses", ".json", ".log"))
    for old in [ses, report, log_path]:
        old.unlink(missing_ok=True)
    cmd = ["bash", "-c", 'source scripts/env.sh && exec nice -n 19 taskset -c "$1" fastroute "${@:2}"',
        "probe", str(CPU), "-de", str(path), "-do", str(ses), f"--report={report}",
        "--router.copper_to_edge_clearance_um=500", "--router.neck_width_um=160",
        "--router.min_trace_width_um=125", "--router.autorouter.max_passes=5",
        "--router.optimizer.max_passes=2", *flags]
    started = time.monotonic()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, start_new_session=True)
    load = None
    resume = None
    chunks = []
    pending = b""
    applied = False
    marker, pause_s = {
        "pause-route": (b"Auto-routing stage started", 1.3),
        "pause-opt": (b"Optimization stage started", 4.7),
    }.get(condition, (b"stage wall clock limits:", 1.3))
    with contextlib.ExitStack() as stack:
        stack.callback(p.wait)
        stack.callback(lambda: p.kill() if p.poll() is None else None)
        sel = stack.enter_context(selectors.DefaultSelector())
        sel.register(p.stdout, selectors.EVENT_READ)
        while time.monotonic() - started < 12:
            if not sel.select(.04):
                if p.poll() is not None:
                    break
                continue
            raw = os.read(p.stdout.fileno(), 32768)
            if not raw:
                break
            chunks.append(raw)
            pending += raw
            if not applied and marker in pending:
                applied = True
                if condition.startswith("pause"):
                    # Force descheduling after the global timer is armed. Unlike
                    # busy work this consumes no extra CPU on the running queue.
                    try:
                        os.killpg(p.pid, signal.SIGSTOP)
                    except ProcessLookupError:
                        pass
                    else:
                        resume = threading.Timer(pause_s, lambda: os.killpg(p.pid, signal.SIGCONT))
                        resume.start()
                elif condition == "cpu":
                    # At most 0.6 wall seconds at nice +19 on the same allowed CPU.
                    low = "import time; end = time.monotonic() + .6\nwhile time.monotonic() < end: pass"
                    load = subprocess.Popen(["bash", "-c",
                        "source scripts/env.sh && exec nice -n 19 taskset -c \"$1\" python -c \"$2\"",
                        "load", str(CPU), low])
            pending = pending[-128:]
        if resume is not None:
            resume.join(2)
        if load is not None:
            load.wait(timeout=2)
    log = b"".join(chunks)
    log_path.write_bytes(log)
    txt = log.decode(errors="replace")
    result = dict(mode=mode, condition=condition, applied=applied, returncode=p.returncode,
        elapsed_s=round(time.monotonic() - started, 3), input_sha256=digest(path.read_bytes()),
        ses_sha256=digest(ses.read_bytes()) if ses.exists() else None,
        copper_sha256=canonical(ses) if ses.exists() else None,
        stopped="--max-time (" in txt,
        per_search_limit="note: a board time limit fired" in txt,
        pass_count=len(re.findall(r"Auto-routing pass #\d+ was completed", txt)),
        optimizer_pass_count=len(re.findall(r"Optimizer pass #\d+ was completed", txt)),
        variants=len(re.findall(r"Multi-start variant \d+:", txt)))

    if report.exists():
        stats = json.loads(report.read_bytes())["stats"]
        result.update({key: stats[key] for key in ("unrouted", "violations", "vias", "trace_length_mm")})
    return result

OPTIONS = {
    "wall": ["--max-time=1", "--multi-start=2", "--router.autorouter.max_threads=2", "--router.optimizer.max_threads=2"],
    "count": ["--max-time=1", "--time-limit-mode=count", "--multi-start=2", "--router.autorouter.max_threads=2", "--router.optimizer.max_threads=2"],
    "count-no-stage": ["--no-time-limits", "--multi-start=2", "--router.autorouter.max_threads=2", "--router.optimizer.max_threads=2"],
    "count-bounded": ["--no-time-limits", "--multi-start=1", "--router.autorouter.max_threads=2", "--router.autorouter.min_passes=6", "--router.optimizer.enabled=false"],
    "count-sequential": ["--no-time-limits", "--multi-start=1", "--router.autorouter.max_threads=1", "--router.autorouter.min_passes=6", "--router.optimizer.enabled=false"],
    "parity": ["--parity", "--router.autorouter.max_threads=1", "--router.optimizer.max_threads=1"],
}

if __name__ == "__main__":
    path = fixture(ROOT / "crossings.dsn", 7)
    if sys.argv[1:] == ["extras"]:
        wall_opt = [*OPTIONS["wall"][1:], "--max-time=4.5"]
        blocked = fixture(ROOT / "partition.dsn", 3, barriers=False, partition=True)
        variant = ["--no-time-limits", "--multi-start=2", "--router.autorouter.max_threads=2",
            "--router.autorouter.max_passes=2", "--router.autorouter.min_passes=3",
            "--router.optimizer.enabled=false", "--router.fanout.enabled=false"]
        count_stop = [*OPTIONS["count-bounded"], "--time-limit-factor=1",
            "--router.fanout.max_milliseconds_per_pin=1"]
        planned = [
            (path, "wall", "pause-route", OPTIONS["wall"]),
            (path, "count", "pause-route", OPTIONS["count"]),
            (path, "wall-opt", "quiet", wall_opt),
            (path, "wall-opt", "pause-opt", wall_opt),
            (blocked, "variant-cap", "quiet", variant),
            (blocked, "variant-cap", "pause-route", variant),
            (path, "count-search", "quiet", count_stop),
            (path, "count-search", "pause", count_stop),
        ]
        rows = []
        for row in planned:
            result = run(*row)
            rows.append(result)
            print(json.dumps(result), flush=True)
        (ROOT / "extras.json").write_text(json.dumps(rows, indent=2) + "\n")
    elif len(sys.argv) == 3:
        print(json.dumps(run(path, sys.argv[1], sys.argv[2], OPTIONS[sys.argv[1]]), indent=2))
    else:
        rows = []
        for mode, flags in OPTIONS.items():
            for condition in ("quiet1", "quiet2", "pause", "cpu"):
                row = run(path, mode, condition, flags)
                rows.append(row)
                print(json.dumps(row), flush=True)
        (ROOT / "runs.json").write_text(json.dumps(rows, indent=2) + "\n")
