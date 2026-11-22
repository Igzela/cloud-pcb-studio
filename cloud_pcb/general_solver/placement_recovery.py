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


def _cluster_supports(
    points: set[int],
    aim: int,
    span: int,
    max_points: int,
    priority: frozenset[int] = frozenset(),
) -> set[int]:
    """Deterministically thin dense support axes while preserving full-axis coverage."""
    if len(points) <= max_points:
        return points
    ordered = sorted(points)
    min_gap = max(1, span // max_points)
    while True:
        clustered: list[int] = []
        bucket_start = ordered[0]
        best = ordered[0]
        best_key = (best not in priority, abs(best - aim), best)
        for pt in ordered[1:]:
            if pt - bucket_start < min_gap:
                key = (pt not in priority, abs(pt - aim), pt)
                if key < best_key:
                    best = pt
                    best_key = key
            else:
                clustered.append(best)
                bucket_start = pt
                best = pt
                best_key = (pt not in priority, abs(pt - aim), pt)
        clustered.append(best)
        if len(clustered) <= max_points or min_gap >= span:
            return set(clustered)
        min_gap *= 2


def _axis_tiers(
    low: float,
    high: float,
    target: float,
    low_supports: Iterable[float],
    high_supports: Iterable[float],
) -> tuple[list[int], list[int], list[int]]:
    """Build 3 ordered 1 µm axis sample tiers so Tier 0 sweeps the whole board.

    - Tier 0: full-board coarse grid + clustered outward-clear supports (and
      exact pinch midpoints where opposite supports meet within 2 µm). Bounded
      so all 4 angles cover 100% of board width/height well inside the default
      probe budget.
    - Tier 1: full 0.50 mm fine grid + all unclustered exact & outward supports.
    - Tier 2: ±1 µm micron nudges around every support.
    """
    start = math.ceil(low * 1000 - 1e-7)
    end = math.floor(high * 1000 + 1e-7)
    if start > end:
        return [], [], []
    aim = round(target * 1000)
    anchor = max(start, min(end, aim))
    span = end - start

    # Coarse grid guarantees full-board coverage across [start, end].
    coarse_pitch = max(500, math.ceil(span / 28))
    first_coarse = start + (anchor - start) % coarse_pitch
    coarse_grid = set(range(first_coarse, end + 1, coarse_pitch))
    coarse_grid.update((start, end, (start + end) // 2, anchor))

    exact_low: list[int] = []
    exact_high: list[int] = []
    exact_all: set[int] = set()
    outward: set[int] = set()

    for support in low_supports:
        rounded = round(1000 * support)
        if start <= rounded <= end:
            exact_low.append(rounded)
            exact_all.add(rounded)
        # Moving part sits to the left/top of the obstacle: clear side is -1 µm.
        clear = math.ceil(1000 * support - 1e-7) - 1
        if start <= clear <= end:
            outward.add(clear)

    for support in high_supports:
        rounded = round(1000 * support)
        if start <= rounded <= end:
            exact_high.append(rounded)
            exact_all.add(rounded)
        # Moving part sits to the right/bottom of the obstacle: clear side is +1 µm.
        clear = math.floor(1000 * support + 1e-7) + 1
        if start <= clear <= end:
            outward.add(clear)

    # Detect exact-fit pinch gaps where a right/bottom support meets a left/top
    # support within 2 µm so zero-slack gaps are still hit directly in Tier 0.
    pinch: set[int] = set()
    if exact_low and exact_high:
        exact_low.sort()
        exact_high.sort()
        idx_l = 0
        n_l = len(exact_low)
        for r_pt in exact_high:
            while idx_l < n_l and exact_low[idx_l] < r_pt - 2:
                idx_l += 1
            j = idx_l
            while j < n_l and exact_low[j] <= r_pt + 2:
                mid = (r_pt + exact_low[j]) // 2
                if start <= mid <= end:
                    pinch.add(mid)
                j += 1

    primary_supports = _cluster_supports(
        outward | pinch, aim, max(span, 1), max_points=40, priority=frozenset(pinch),
    )
    tier0_set = coarse_grid | primary_supports

    # Tier 1: original 0.50 mm fallback pitch (bounded at 500 steps) + all exact/outward supports.
    fine_pitch = max(500, math.ceil(span / 500))
    first_fine = start + (anchor - start) % fine_pitch
    tier1_set = tier0_set | set(range(first_fine, end + 1, fine_pitch)) | exact_all | outward

    # Tier 2: ±1 µm micron nudges around every support edge.
    tier2_set = set(tier1_set)
    for rounded in exact_all:
        for point in (rounded - 1, rounded + 1):
            if start <= point <= end:
                tier2_set.add(point)

    def sort_axis(values: set[int]) -> list[int]:
        return sorted(values, key=lambda point: ((point - aim) ** 2, point))

    return sort_axis(tier0_set), sort_axis(tier1_set), sort_axis(tier2_set)


def _axis_samples(low: float, high: float, target: float, supports: Iterable[float]) -> list[int]:
    """Sample millimetre coordinates on the same 1 µm grid used for output."""
    support_list = tuple(supports)
    tier0, _, _ = _axis_tiers(low, high, target, support_list, support_list)
    return tier0


def _tier_candidates(
    xs: list[int],
    ys: list[int],
    tx_um: int,
    ty_um: int,
    angle: int,
    tier: int,
    skip_xs: frozenset[int] = frozenset(),
    skip_ys: frozenset[int] = frozenset(),
) -> Iterator[tuple[tuple[int, int], int, int, int]]:
    def record(i: int, j: int) -> tuple[int, int, int, int, int]:
        x, y = xs[i], ys[j]
        return (x - tx_um) ** 2 + (y - ty_um) ** 2, x, y, i, j

    queue = [record(0, 0)]
    while queue:
        cost, x, y, i, j = heapq.heappop(queue)
        if j == 0 and i + 1 < len(xs):
            heapq.heappush(queue, record(i + 1, 0))
        if j + 1 < len(ys):
            heapq.heappush(queue, record(i, j + 1))
        if skip_xs and x in skip_xs and y in skip_ys:
            continue
        yield (tier, cost), x, y, angle


def _angle_candidates(
    bbox: Bounds,
    target: Point,
    board_size: Point,
    angle: int,
    occupied: tuple[Bounds, ...],
    clearance: float,
    edge_inset: float,
) -> Iterator[tuple[tuple[int, int], int, int, int]]:
    """Generate an angle's centre candidates tiered for full-board coverage first."""
    x0, y0, x1, y1 = _turn(bbox, angle)
    x0, y0, x1, y1 = x0 - clearance, y0 - clearance, x1 + clearance, y1 + clearance
    width, height = board_size
    tx, ty = target
    xs0, xs1, xs2 = _axis_tiers(
        edge_inset - x0, width - edge_inset - x1, tx,
        (left - x1 for left, _, _, _ in occupied),
        (right - x0 for _, _, right, _ in occupied),
    )
    ys0, ys1, ys2 = _axis_tiers(
        edge_inset - y0, height - edge_inset - y1, ty,
        (top - y1 for _, top, _, _ in occupied),
        (bottom - y0 for _, _, _, bottom in occupied),
    )
    if not xs0 or not ys0:
        return
    tx_um, ty_um = round(tx * 1000), round(ty * 1000)

    yield from _tier_candidates(xs0, ys0, tx_um, ty_um, angle, 0)

    if len(xs1) > len(xs0) or len(ys1) > len(ys0):
        yield from _tier_candidates(
            xs1, ys1, tx_um, ty_um, angle, 1, frozenset(xs0), frozenset(ys0),
        )

    if len(xs2) > len(xs1) or len(ys2) > len(ys1):
        yield from _tier_candidates(
            xs2, ys2, tx_um, ty_um, angle, 2, frozenset(xs1), frozenset(ys1),
        )


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
    angle_support_bounds: Callable[[str, int], Iterable[Bounds]] | None = None,
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
            _angle_candidates(
                bboxes[ref], target, board_size, angle,
                occupied + tuple(angle_support_bounds(ref, angle)) if angle_support_bounds else occupied,
                clearance, edge_inset,
            )
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
