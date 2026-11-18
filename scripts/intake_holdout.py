"""Project only approved KiCad data from a private reference into a blind input."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from kiutils.utils.sexpr import parse_sexp

from scripts.seal_holdout import child, children, extract, prop


ROOT = Path(__file__).resolve().parents[1]
CASE_ID = re.compile(r"case(?:[6-9]|[1-9][0-9]+)")
SHA256 = re.compile(r"[a-f0-9]{64}")
GIT_SHA = re.compile(r"[a-f0-9]{40}")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sexpr(item):
    if isinstance(item, list):
        return "(" + " ".join(map(_sexpr, item)) + ")"
    return json.dumps(item, ensure_ascii=False, allow_nan=False)


def prepare_spec(raw: bytes, case: int) -> dict:
    """Check the projection by replacing every non-interface placement and route."""
    if not CASE_ID.fullmatch(f"case{case}"):
        raise ValueError("Existing regression IDs are reserved")
    spec = extract(raw, case, strict_intake=True)
    outline = spec["board_outline_mm"]
    if not all(math.isfinite(outline[k]) and outline[k] > 0 for k in ("width", "height")):
        raise ValueError("Board outline size must be positive")
    refs = [component["refdes"] for component in spec["components"]]
    if not refs or "" in refs or len(refs) != len(set(refs)):
        raise ValueError("Blind input requires unique nonempty component identities")
    endpoints = Counter(
        (p["net"], c["refdes"], p["num"])
        for c in spec["components"] for p in c["pads"] if p["net"]
    )
    if not endpoints or endpoints != Counter(
        (net, ref, pin) for net, pins in spec["nets"].items() for ref, pin in pins
    ):
        raise ValueError("Projected pad assignments do not match netlist endpoints")

    fixed = {part["refdes"] for part in spec["edge_connectors"]}
    ast = [
        element for element in parse_sexp(raw.decode("utf-8"))
        if not (isinstance(element, list) and element and element[0] in ("segment", "via", "arc", "zone"))
    ]
    for index, footprint in enumerate(children(ast, "footprint")):
        pads = children(footprint, "pad")
        if not pads or all(p[2] == "np_thru_hole" for p in pads) or prop(footprint, "Reference") in fixed:
            continue
        pose = child(footprint, "at")
        if pose is None:
            raise ValueError("Placed footprint lacks an instance transform")
        if len(pose) < 4:
            pose.append(0)
        pose[1:3] = (outline["origin_x"] + outline["width"] + 1000 + index,
                     outline["origin_y"] + outline["height"] + 1000 + index)
        pose[3] += 90
        for pad in pads:
            local = child(pad, "at")
            if local is None:
                pad.append(["at", 0, 0, 90])
            else:
                if len(local) < 4:
                    local.append(0)
                local[3] += 90
    if extract(_sexpr(ast).encode(), case, strict_intake=True) != spec:
        raise ValueError("Blind projection is sensitive to reference placement or routing")

    dx, dy = outline["origin_x"], outline["origin_y"]
    for item in [*spec["mounting_holes"], *spec["edge_connectors"]]:
        item["x_mm"] = round(item["x_mm"] - dx, 6)
        item["y_mm"] = round(item["y_mm"] - dy, 6)
    outline.update(origin_x=0, origin_y=0)
    spec.update(repo_url="", commit_sha="")
    return spec


def intake(case_id: str, theme: str, vault: Path, root: Path = ROOT) -> dict:
    if not CASE_ID.fullmatch(case_id):
        raise ValueError("Choose an unused holdout ID starting at case6")
    case_dir = vault / case_id
    reference = case_dir / "reference.kicad_pcb"
    provenance = json.loads((case_dir / "provenance.json").read_text())
    raw = reference.read_bytes()
    reference_digest = sha256(raw)
    if provenance.get("reference_sha256") != reference_digest:
        raise ValueError("Private reference digest does not match provenance")
    if not GIT_SHA.fullmatch(provenance.get("source_commit", "")) or not all(
        provenance.get(field) for field in ("repo_url", "source_path", "license")
    ):
        raise ValueError("Private provenance needs exact source commit, path, repository and license")
    path = root / "benchmark_case" / f"blind_spec_{case_id}.json"
    if path.exists() or path.is_symlink():
        raise ValueError("This blind spec already exists")
    spec = prepare_spec(raw, int(case_id[4:]))
    content = (json.dumps(spec, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(content)
    return {
        "id": case_id, "spec": str(path.relative_to(root)), "spec_sha256": sha256(content),
        "reference_sha256": reference_digest, "theme": theme,
        "counts": {"components": len(spec["components"]), "nets": len(spec["nets"]),
                   "differential_pairs": len(spec["design_requirements"]["differential_pairs"])},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--theme", required=True)
    parser.add_argument("--vault", type=Path, default=ROOT / ".run/holdout-vault")
    args = parser.parse_args()
    print(json.dumps(intake(args.case, args.theme, args.vault)))


if __name__ == "__main__":
    main()
