"""Evaluate a registered cohort once and compare only after a verified freeze."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
from pathlib import Path

from cloud_pcb.general_solver.optimization import GERBER_LAYERS, atomic_json, digest, report_metrics
from scripts.intake_holdout import CASE_ID, GIT_SHA, SHA256, prepare_spec, sha256
from scripts.optimize_pcb import inspect_browser, now, sourced_process
from scripts.run_blind_v3 import code_fingerprint


ROOT = Path(__file__).resolve().parents[1]
COHORT_NAME = re.compile(r"[a-z][a-z0-9-]{0,47}")
VARIANTS = ("stable", "freerouting-jvm", "direct-repair", "placement-recovery")
BOARD_FILES = {"routed.kicad_pcb", "blind_run_report.json", "metrics.json",
               "assembly.html", "assembly_oblique.png", "assembly_top.png",
               "assembly_side.png", "assembly_browser_report.json", "solver.log", "browser.log",
               "fastroute_primary.log", "freerouting.log", "router_processes.json"} | {
    "gerbers/" + layer for layer in GERBER_LAYERS
}


def _safe(root: Path, path: Path) -> Path:
    root = root.absolute()
    path = path if path.is_absolute() else root / path
    if not path.is_relative_to(root) or path.resolve() != path:
        raise ValueError(f"Non-canonical, escaping or symlink path: {path.name}")
    return path


def _public_payload(spec: dict, case: str) -> None:
    keys = {"project_name", "repo_url", "commit_sha", "board_outline_mm", "layer_count",
            "mounting_holes", "edge_connectors", "components", "nets",
            "design_requirements", "input_limitations"}
    if set(spec) != keys or spec["repo_url"] or spec["commit_sha"]:
        raise ValueError("Blind spec contains private provenance or unapproved top-level fields")
    if spec["project_name"] != f"holdout_{case}" or spec["layer_count"] != 4:
        raise ValueError("Blind spec identity or copper stack is invalid")
    if spec["board_outline_mm"].get("origin_x") != 0 or spec["board_outline_mm"].get("origin_y") != 0:
        raise ValueError("Blind board frame must be local")
    for component in spec["components"]:
        if set(component) - {"refdes", "value", "footprint_name", "pads", "body_bbox_mm",
                             "height_mm", "antenna_keepout_mm"}:
            raise ValueError("Blind component contains unapproved instance fields")
        for pad in component["pads"]:
            if set(pad) - {"num", "net", "x_rel_mm", "y_rel_mm", "w_mm", "h_mm", "shape", "type",
                           "drill_mm", "drill_shape", "drill_width_mm", "drill_height_mm",
                           "drill_offset_mm", "layers", "angle_rel_deg", "pin_type",
                           "pin_function", "roundrect_ratio"}:
                raise ValueError("Blind pad contains unapproved instance fields")


def read_cohort(path: Path) -> dict:
    data = json.loads(_safe(ROOT, path).read_text())
    if set(data) != {"schema_version", "cohort", "cases"} or data["schema_version"] != 1 or not (
        isinstance(data["cohort"], str) and COHORT_NAME.fullmatch(data["cohort"])
    ):
        raise ValueError("Invalid public cohort registry")
    if not isinstance(data["cases"], list) or len(data["cases"]) < 2:
        raise ValueError("A holdout cohort requires at least two distinct boards")
    ids = set()
    references = set()
    shapes = set()
    for row in data["cases"]:
        if set(row) != {"id", "spec", "spec_sha256", "reference_sha256", "theme"}:
            raise ValueError("Invalid public case fields")
        case = row["id"]
        if not CASE_ID.fullmatch(case) or case in ids or row["spec"] != f"benchmark_case/blind_spec_{case}.json":
            raise ValueError("Invalid or duplicate holdout ID or spec path")
        ids.add(case)
        if not all(SHA256.fullmatch(row[k]) for k in ("spec_sha256", "reference_sha256")):
            raise ValueError("Inputs and references require full SHA-256 digests")
        if row["reference_sha256"] in references:
            raise ValueError("Duplicate reference digest")
        references.add(row["reference_sha256"])
        if not isinstance(row["theme"], str) or not 1 <= len(row["theme"]) <= 100:
            raise ValueError("Case theme must be a short public label")
        raw = _safe(ROOT, Path(row["spec"])).read_bytes()
        if sha256(raw) != row["spec_sha256"]:
            raise ValueError(f"Blind spec hash changed for {case}")
        spec = json.loads(raw)
        _public_payload(spec, case)
        spec.pop("project_name")
        shape = sha256(json.dumps(spec, sort_keys=True).encode())
        if shape in shapes:
            raise ValueError("Duplicate blind input under another case ID")
        shapes.add(shape)
    return data


def _tree(path: Path) -> dict[str, str]:
    result = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"Unexpected symlink in frozen output: {item.name}")
        if item.is_file():
            result[item.relative_to(path).as_posix()] = digest(item)
    return result


def _evidence(directory: Path) -> dict[str, str]:
    evidence = _tree(directory)
    evidence.pop("freeze.json", None)
    return evidence


def _check_sources(code, path, registry_digest):
    if code_fingerprint() != code:
        raise ValueError("Solver source changed")
    if digest(path) != registry_digest:
        raise ValueError("Cohort registry changed")
    read_cohort(path)


def run_frozen(registry_path: Path, directory: Path, *, variant: str, case_timeout: int,
               retry_of: str | None = None) -> dict:
    lock_path = _safe(ROOT, Path("artifacts/holdouts/.run.lock"))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _run_frozen(registry_path, directory, variant=variant, case_timeout=case_timeout,
                           retry_of=retry_of)


def _run_frozen(registry_path: Path, directory: Path, *, variant: str, case_timeout: int,
                retry_of: str | None) -> dict:
    registry_path = _safe(ROOT, registry_path)
    directory = _safe(ROOT, directory)
    cohort = read_cohort(registry_path)
    if variant not in VARIANTS or not 1 <= case_timeout <= 7200:
        raise ValueError("Unknown uniform variant or unsupported solver time budget")
    runs_root = ROOT / "artifacts/holdouts"
    earlier = []
    for committed in sorted(runs_root.glob("*/freeze.json")):
        previous = json.loads(_safe(ROOT, committed).read_text())
        if any(
            old["id"] == new["id"] or old["reference_sha256"] == new["reference_sha256"]
            for old in previous["registered_cases"] for new in cohort["cases"]
        ):
            if previous["cohort"] != cohort["cohort"]:
                raise ValueError("A registered board already belongs to another attempted cohort")
            earlier.append((committed, previous))
    code = code_fingerprint()
    registry_digest = digest(registry_path)
    expected_name = cohort["cohort"]
    if earlier:
        committed, previous = earlier[-1]
        if (
            retry_of != digest(committed) or variant != "placement-recovery"
            or any(attempt["status"] == "frozen" for _, attempt in earlier)
            or previous["status"] != "incomplete" or previous["code"] == code
            or previous["registered_cases"] != cohort["cases"]
            or previous["cohort_sha256"] != registry_digest
            or previous["evidence"] != _evidence(committed.parent)
        ):
            raise ValueError("Retry requires the verified latest incomplete, still-sealed cohort and changed placement code")
        failures = [entry for entry in previous["cases"] if entry["status"] == "failed"]
        if not failures or any(
            not (committed.parent / entry["id"] / "solver.log").read_text().rstrip().splitlines()[-1]
            .startswith(("RuntimeError: Unable to place component ", "PlacementExhausted: "))
            for entry in failures
        ):
            raise ValueError("Retry is limited to cohorts interrupted by placement exhaustion")
        expected_name += f"-retry-{len(earlier):02d}"
    elif retry_of:
        raise ValueError("Retry digest has no recorded parent")
    if directory.parent != runs_root or directory.name != expected_name:
        raise ValueError(f"Expected fresh run directory artifacts/holdouts/{expected_name}")
    runtime = None
    if variant in ("freerouting-jvm", "direct-repair", "placement-recovery"):
        from cloud_pcb.layout_route.freerouting_runtime import resolve_freerouting_jvm
        runtime = resolve_freerouting_jvm(ROOT).provenance
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "inputs").mkdir()
    shutil.copyfile(registry_path, directory / "cohort.json")
    for row in cohort["cases"]:
        shutil.copyfile(ROOT / row["spec"], directory / "inputs" / f'{row["id"]}.json')
    archive_path = directory / "source.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name in sorted(code):
            archive.add(ROOT / name, arcname=name, recursive=False)
    manifest = {
        "schema_version": 1, "cohort": cohort["cohort"], "status": "running",
        "started_at": now(), "variant": variant, "case_timeout_s": case_timeout,
        "code": code, "cohort_sha256": registry_digest, "runtime": runtime,
        "registered_cases": cohort["cases"], "cases": [],
    }
    if earlier:
        manifest["retry"] = {"parent_freeze_sha256": retry_of, "reason": "placement_exhaustion",
                             "first_pass": False}
    freeze_path = directory / "freeze.json"
    atomic_json(freeze_path, manifest)
    print(json.dumps({"event": "holdout_start", "cohort": cohort["cohort"],
                      "cases": [c["id"] for c in cohort["cases"]]}), flush=True)
    try:
        rc = sourced_process(["python", "-m", "pytest", "-v"], directory / "pytest.log", 600)
        manifest["pytest_returncode"] = rc
        if rc != 0:
            raise ValueError("pytest preflight failed")
        for row in cohort["cases"]:
            _check_sources(code, registry_path, registry_digest)
            case = row["id"]
            copied = directory / "inputs" / f"{case}.json"
            if digest(copied) != row["spec_sha256"]:
                raise ValueError(f"Copied blind input changed for {case}")
            outdir = directory / case
            outdir.mkdir()
            print(json.dumps({"event": "case_start", "case": case}), flush=True)
            started = time.monotonic()
            entry = {"id": case, "status": "failed"}
            try:
                command = ["python", "-m", "cloud_pcb.general_solver.run_blind",
                           "--spec", copied, "--outdir", outdir]
                if runtime:
                    command.append("--freerouting-jvm")
                if variant in ("direct-repair", "placement-recovery"):
                    command.append("--direct-repair")
                if variant == "placement-recovery":
                    command.append("--placement-recovery")
                rc = sourced_process(command, outdir / "solver.log", case_timeout)
                if rc != 0:
                    raise ValueError(f"Solver exited with status {rc}")
                report = json.loads((outdir / "blind_run_report.json").read_text())
                browser = inspect_browser(outdir)
                metrics = report_metrics(report, outdir / "routed.kicad_pcb", json.loads(copied.read_text()))
                if runtime:
                    evidence = report.get("freerouting_comparison") or {}
                    if evidence.get("runtime") != runtime or evidence.get("status") != "completed":
                        metrics["evidence_errors"].append("Secondary engine evidence incomplete or different runtime")
                atomic_json(outdir / "metrics.json", metrics)
                if not BOARD_FILES <= _tree(outdir).keys() or metrics["evidence_errors"] or not browser["passed"]:
                    raise ValueError("Board output evidence or browser check incomplete")
                entry.update(status="complete", hard_pass=metrics["hard_pass"], values=metrics["values"],
                             failed_gates=metrics["failed_gates"])
            except Exception as exc:
                entry["error"] = str(exc)
            entry["runtime_s"] = round(time.monotonic() - started, 3)
            manifest["cases"].append(entry)
            atomic_json(freeze_path, manifest)
            print(json.dumps({"event": "case_finish", **entry}), flush=True)
        _check_sources(code, registry_path, registry_digest)
        manifest["status"] = "frozen" if all(c["status"] == "complete" for c in manifest["cases"]) else "incomplete"
    except Exception as exc:
        manifest.update(status="invalidated", error=str(exc))
    manifest["finished_at"] = now()
    manifest["evidence"] = _evidence(directory)
    atomic_json(freeze_path, manifest)
    print(json.dumps({"event": "holdout_finish", "status": manifest["status"], "manifest": str(freeze_path),
                      "freeze_sha256": digest(freeze_path)}), flush=True)
    return manifest


def verify_frozen(registry_path: Path, directory: Path) -> dict:
    registry_path = _safe(ROOT, registry_path)
    directory = _safe(ROOT, directory)
    cohort = read_cohort(registry_path)
    manifest = json.loads(_safe(ROOT, directory / "freeze.json").read_text())
    if (
        manifest.get("schema_version") != 1 or manifest.get("status") != "frozen"
        or manifest.get("pytest_returncode") != 0 or manifest.get("cohort") != cohort["cohort"]
        or manifest.get("registered_cases") != cohort["cases"]
        or [(c.get("id"), c.get("status")) for c in manifest.get("cases", [])]
        != [(c["id"], "complete") for c in cohort["cases"]]
    ):
        raise ValueError("Complete frozen holdout cohort is required")
    _check_sources(manifest["code"], registry_path, manifest["cohort_sha256"])
    if manifest.get("evidence") != _evidence(directory):
        raise ValueError("Frozen output file set or digest changed")
    if digest(directory / "cohort.json") != manifest["cohort_sha256"]:
        raise ValueError("Frozen public registry digest changed")
    for row in cohort["cases"]:
        case = row["id"]
        if digest(directory / "inputs" / f"{case}.json") != row["spec_sha256"]:
            raise ValueError(f"Frozen input hash changed for {case}")
        if not BOARD_FILES <= _tree(directory / case).keys():
            raise ValueError(f"Frozen board outputs incomplete for {case}")
    return manifest


def compare_frozen(registry_path: Path, directory: Path, vault: Path, output: Path) -> dict:
    # This verification precedes the first access to the private vault.
    manifest = verify_frozen(registry_path, directory)
    directory = _safe(ROOT, directory)
    output = _safe(ROOT, output)
    if output == directory or output.is_relative_to(directory) or output.exists():
        raise ValueError("Comparison requires a new directory outside the frozen tree")
    if not output.is_relative_to(ROOT / "artifacts"):
        raise ValueError("Comparison output belongs under artifacts")
    vault = vault.absolute()
    if vault.is_relative_to(ROOT) and not vault.is_relative_to(ROOT / ".run"):
        raise ValueError("Reference vault must be outside solver inputs and tracked directories")
    references = []
    for row in manifest["registered_cases"]:
        case = row["id"]
        private = _safe(vault, Path(case))
        raw = _safe(vault, private / "reference.kicad_pcb").read_bytes()
        source = json.loads(_safe(vault, private / "provenance.json").read_text())
        if sha256(raw) != row["reference_sha256"] or source.get("reference_sha256") != row["reference_sha256"]:
            raise ValueError(f"Reference digest does not match registration for {case}")
        if not GIT_SHA.fullmatch(source.get("source_commit", "")) or not all(
            source.get(k) for k in ("repo_url", "source_path", "license")
        ):
            raise ValueError(f"Private source provenance incomplete for {case}")
        spec = json.loads((directory / "inputs" / f"{case}.json").read_text())
        if prepare_spec(raw, int(case[4:])) != spec:
            raise ValueError(f"Reference projection differs from frozen input for {case}")
        public_source = {key: source[key] for key in ("repo_url", "source_path", "source_commit",
                                                      "license", "reference_sha256")}
        for key in ("build_evidence", "source_board_sha256", "conversion"):
            if key in source:
                public_source[key] = source[key]
        references.append((case, raw, public_source))
    from cloud_pcb.general_solver.compare_reference import compare_boards
    result = {"cohort": manifest["cohort"], "status": "compared", "compared_at": now(),
              "freeze_sha256": digest(directory / "freeze.json"), "cases": []}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as staging:
        exports = Path(staging) / output.name
        exports.mkdir()
        for case, raw, source in references:
            # The board itself remains in the vault; the temp file is removed.
            with tempfile.NamedTemporaryFile(suffix=".kicad_pcb") as board:
                board.write(raw)
                board.flush()
                comparison = compare_boards(
                    directory / case / "routed.kicad_pcb", Path(board.name),
                    exports / f"{case}.json", exports / f"{case}.png",
                    spec_path=directory / "inputs" / f"{case}.json",
                )
            result["cases"].append({"id": case, "source": source, "deltas": comparison["deltas"]})
        atomic_json(exports / "comparison.json", result)
        exports.rename(output)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    run = sub.add_parser("run")
    compare = sub.add_parser("compare")
    for command in (run, compare):
        command.add_argument("--cohort", type=Path, required=True)
        command.add_argument("--directory", type=Path, required=True)
    run.add_argument("--variant", choices=VARIANTS, default="direct-repair")
    run.add_argument("--retry-of", help="Freeze SHA-256 of an incomplete placement-failure attempt")
    run.add_argument("--case-timeout", type=int, default=1200)
    compare.add_argument("--freeze-sha256", required=True)
    compare.add_argument("--vault", type=Path, default=ROOT / ".run/holdout-vault")
    compare.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("PCB_STUDIO_ROOT") != str(ROOT):
        parser.error("Run source scripts/env.sh first")
    if args.mode == "run":
        result = run_frozen(args.cohort, args.directory, variant=args.variant,
                            case_timeout=args.case_timeout, retry_of=args.retry_of)
        return 0 if result["status"] == "frozen" else 1
    directory = _safe(ROOT, args.directory)
    if digest(_safe(ROOT, directory / "freeze.json")) != args.freeze_sha256:
        parser.error("Independent freeze digest does not match")
    result = compare_frozen(args.cohort, directory, args.vault, args.output)
    print(json.dumps({"status": result["status"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
