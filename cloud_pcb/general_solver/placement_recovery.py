"""Deterministic whole-board packing for an exhausted initial placer."""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable, Iterable, Iterator, Mapping
from itertools import islice


Bounds = tuple[float, float, float, float]
Point = tuple[float, float]
Pose = tuple[float, float, float]
ANGLES = (0, 90, 180, 270)


class PackingExhausted(RuntimeError):
    """One bounded search order could not find a legal pose."""

    def __init__(self, ref: str, probes: int):
        self.ref = ref
        self.probes = probes
        super().__init__(f"No legal recovery pose for {ref} after {probes} probes")


def _turn(bounds: Bounds, angle: int) -> Bounds:
    x0, y0, x1, y1 = bounds
    if angle == 90:
        return y0, -x1, y1, -x0
    if angle == 180:
        return -x1, -y1, -x0, -y0
    if angle == 270:
        return -y1, x0, -y0, x1
    return bounds


def _axis_samples(low: float, high: float, target: float, supports: Iterable[float]) -> list[int]:
    """Sample millimetre coordinates on the same 1 µm grid used for output."""
    start = math.ceil(low * 1000 - 1e-7)
    end = math.floor(high * 1000 + 1e-7)
    if start > end:
        return []
    aim = round(target * 1000)
    anchor = max(start, min(end, aim))
    # Keep very large boards bounded, while ordinary compact boards use the
    # original placer's 0.50 mm fallback pitch.
    pitch = max(500, math.ceil((end - start) / 500))
    first = start + (anchor - start) % pitch
    samples = set(range(first, end + 1, pitch))
    samples.update((start, end, (start + end) // 2, anchor))
    for support in supports:
        rounded = round(1000 * support)
        for point in (rounded - 1, rounded, rounded + 1):
            if start <= point <= end:
                samples.add(point)
    return sorted(samples, key=lambda point: ((point - aim) ** 2, point))


def _angle_candidates(
    bbox: Bounds,
    target: Point,
    board_size: Point,
    angle: int,
    occupied: tuple[Bounds, ...],
    clearance: float,
    edge_inset: float,
) -> Iterator[tuple[int, int, int, int]]:
    """Generate an angle's centre candidates nearest to its target first."""
    x0, y0, x1, y1 = _turn(bbox, angle)
    x0, y0, x1, y1 = x0 - clearance, y0 - clearance, x1 + clearance, y1 + clearance
    width, height = board_size
    tx, ty = target
    xs = _axis_samples(
        edge_inset - x0, width - edge_inset - x1, tx,
        (x for left, _, right, _ in occupied for x in (left - x1, right - x0)),
    )
    ys = _axis_samples(
        edge_inset - y0, height - edge_inset - y1, ty,
        (y for _, top, _, bottom in occupied for y in (top - y1, bottom - y0)),
    )
    if not xs or not ys:
        return
    tx_um, ty_um = round(tx * 1000), round(ty * 1000)

    def record(i: int, j: int) -> tuple[int, int, int, int, int]:
        x, y = xs[i], ys[j]
        return (x - tx_um) ** 2 + (y - ty_um) ** 2, x, y, i, j

    queue = [record(0, 0)]
    while queue:
        cost, x, y, i, j = heapq.heappop(queue)
        yield cost, x, y, angle
        if j == 0 and i + 1 < len(xs):
            heapq.heappush(queue, record(i + 1, 0))
        if j + 1 < len(ys):
            heapq.heappush(queue, record(i, j + 1))


def pack_movable(
    refs: Iterable[str],
    bboxes: Mapping[str, Bounds],
    targets: Mapping[str, Point] | Callable[[str], Point],
    valid: Callable[[str, float, float, float], bool],
    commit: Callable[[str, float, float, float], None],
    board_size: Point,
    *,
    mode: str = "area",
    clearance: float = 0.28,
    edge_inset: float = 1.0,
    support_bounds: Callable[[str], Iterable[Bounds]] | None = None,
    probe_budget: int = 40000,
) -> None:
    """Pack one deterministic order using the caller's exact legality test.

    Each checked pose can be committed immediately. On exhaustion the caller
    restores its fixed-only state before trying a different order. Support
    boxes add adjacency probes; the legality callback remains authoritative
    for non-rectangular outlines, holes, and layer-specific obstacles.
    """
    if mode not in ("area", "long-side", "ordered"):
        raise ValueError(f"Unknown recovery packing order: {mode}")
    width, height = board_size
    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
        raise ValueError("Recovery board size must be finite and positive")
    if not all(math.isfinite(v) and v >= 0 for v in (clearance, edge_inset)) or probe_budget < 1:
        raise ValueError("Recovery margins and probe budget are invalid")
    order = list(refs)

    def size_key(ref: str) -> tuple[float, float, str]:
        x0, y0, x1, y1 = bboxes[ref]
        w, h = x1 - x0 + 2 * clearance, y1 - y0 + 2 * clearance
        area, long_side = w * h, max(w, h)
        if mode == "long-side":
            return -long_side, -area, ref
        return -area, -long_side, ref

    if mode != "ordered":
        order.sort(key=size_key)
    for ref in order:
        target = targets(ref) if callable(targets) else targets[ref]
        if not all(math.isfinite(value) for value in target):
            raise ValueError("Recovery target must be finite")
        occupied = tuple(support_bounds(ref)) if support_bounds else ()
        ranked = heapq.merge(*(
            _angle_candidates(bboxes[ref], target, board_size, angle, occupied, clearance, edge_inset)
            for angle in ANGLES
        ))
        probes = 0
        for _, x_um, y_um, angle in islice(ranked, probe_budget):
            probes += 1
            pose = x_um / 1000, y_um / 1000, float(angle)
            if valid(ref, *pose):
                commit(ref, *pose)
                break
        else:
            raise PackingExhausted(ref, probes)
