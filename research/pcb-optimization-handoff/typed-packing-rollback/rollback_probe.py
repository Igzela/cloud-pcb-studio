"""Scratch prototype for typed, bounded failed-part blocker repacking."""

from dataclasses import dataclass
from functools import lru_cache
from heapq import merge
from itertools import islice

from cloud_pcb.general_solver.placement_recovery import ANGLES, _angle_candidates
from cloud_pcb.general_solver.two_side_packing import (
    COMPACT_COURTYARD_MM, _support_for_box, component_z_range,
    hits_mechanical_keepout, in_outline, local_occupancy, noninterfering, side_options,
)


class _ProbeBudget(Exception):
    pass


@dataclass(frozen=True)
class Result:
    status: str
    poses: dict | None
    probes: int
    branches: int
    moved: tuple[str, ...] = ()


def repack_blockers(parts, poses, blocked, pinned, board, target, *, fixed=(),
                     mechanical_regions=(), protected_nets=frozenset(), probe_budget=6500,
                     anchor_probes=80, branch_cap=20, max_blockers=2):
    """Try outright placements for a single stalled part with 0–2 relocations.

    Placement is transactional: caller's mapping is unchanged on every path.
    All conflicts use the typed geometry used by the ordinary two-side packer.
    """
    if blocked in poses or not set(pinned) <= poses.keys() or blocked not in parts:
        raise ValueError('Invalid rollback state')
    if min(probe_budget, anchor_probes, branch_cap, max_blockers) < 1:
        raise ValueError('Invalid rollback budget')
    x0, y0, width, height = board.bounds
    if x0 != 0 or y0 != 0:
        raise ValueError('Expected board-local frame')
    boundary = board.boundary
    count = 0
    branches = 0

    @lru_cache(maxsize=None)
    def local(ref, side, angle=0):
        return local_occupancy(parts[ref], side).rotate(angle)

    def shape_at(ref, pose):
        x, y, angle, side = pose
        return side, local(ref, side, angle).translate(x, y)

    initial = {ref: shape_at(ref, pose) for ref, pose in poses.items()}

    def constrained_support(ref, side, angle, others):
        form = local(ref, side, angle)
        search = form.search_bounds()
        for other_side, other in others.values():
            if other_side == side:
                yield _support_for_box(other.combined.bounds, form.combined.bounds, search,
                                       2 * COMPACT_COURTYARD_MM)
            if not other.hole_guard.is_empty:
                yield _support_for_box(other.hole_guard.bounds, form.combined.bounds, search, 0)
            if not form.hole_guard.is_empty:
                yield _support_for_box(other.combined.bounds, form.hole_guard.bounds, search, 0)
        for keepout in fixed:
            yield _support_for_box(keepout.bounds, form.combined.bounds, search, 0)
        z_min, z_max = component_z_range(parts[ref], side)
        for keepout, low, high in mechanical_regions:
            if min(z_max, high) > max(z_min, low):
                yield _support_for_box(keepout.bounds, form.body.bounds, search, 0)

    def candidates(ref, side, anchor, others, limit):
        nonlocal count
        form = local(ref, side)
        ranked = merge(*(
            _angle_candidates(form.search_bounds(), anchor, (width, height), angle,
                              tuple(constrained_support(ref, side, angle, others)), 0., 0.)
            for angle in ANGLES
        ))
        for _, x, y, angle in islice(ranked, limit):
            if count == probe_budget:
                raise _ProbeBudget
            count += 1
            pose = (x / 1000., y / 1000., float(angle), side)
            _, shape = shape_at(ref, pose)
            if (in_outline(board, boundary, shape)
                    and all(not shape.combined.intersects(keepout) for keepout in fixed)
                    and not hits_mechanical_keepout(shape.body, component_z_range(parts[ref], side), mechanical_regions)):
                yield pose, shape

    def conflicts(side, shape, other_shapes):
        return tuple(ref for ref, (other_side, other) in other_shapes.items()
                     if not noninterfering(side, shape, other_side, other))

    def anchors(goal):
        x, y = goal
        # Geometric anchors, rather than four equivalent orientations at the
        # centre, give a distinct chance to preserve a large open region.
        return tuple(dict.fromkeys((goal,(0,y),(width,y),(x,0),(x,height),
                                   (0,0),(width,0),(0,height),(width,height))))

    def alternatives(ref, other_shapes):
        old = poses[ref]
        options = side_options(parts[ref], protected_nets=protected_nets)
        if old[3] not in options:
            return
        sides = (old[3], *(side for side in options if side != old[3]))
        seen = set()
        for side in sides:
            for anchor in anchors(old[:2]):
                found = 0
                for pose, shape in candidates(ref, side, anchor, other_shapes, anchor_probes):
                    if pose in seen or conflicts(side, shape, other_shapes):
                        continue
                    seen.add(pose)
                    yield pose, shape
                    found += 1
                    if found == 2:
                        break

    def refill(refs, state, output):
        if not refs:
            return output
        ref, *rest = refs
        for pose, shape in alternatives(ref, state):
            solved = refill(rest, dict(state, **{ref: (pose[3], shape)}), dict(output, **{ref: pose}))
            if solved is not None:
                return solved
        return None

    proposals = []
    try:
        seen = set()
        for side in side_options(parts[blocked], protected_nets=protected_nets):
            for anchor in anchors(target):
                for pose, shape in candidates(blocked, side, anchor, initial, anchor_probes):
                    touched = conflicts(side, shape, initial)
                    if pose in seen or pinned.intersection(touched) or len(touched) > max_blockers:
                        continue
                    seen.add(pose)
                    proposals.append((len(touched), (pose[0]-target[0])**2+(pose[1]-target[1])**2,
                                      side != 'top', pose, shape, touched))
                    # One witness per remote anchor prevents local target
                    # neighborhoods from taking the entire branch budget.
                    break
        proposals.sort(key=lambda record: record[:4])
        for _, _, _, pose, shape, touched in islice(proposals, branch_cap):
            branches += 1
            rest = {ref: info for ref, info in initial.items() if ref not in touched}
            rest[blocked] = pose[3], shape
            recovered = refill(sorted(touched, key=lambda r: (-initial[r][1].combined.area, r)),
                               rest, {blocked: pose})
            if recovered is not None:
                updated = dict(poses, **recovered)
                moved = tuple(sorted(ref for ref in touched if updated[ref] != poses[ref]))
                return Result('repacked', updated, count, branches, moved)
    except _ProbeBudget:
        return Result('probe_budget_exhausted', None, count, branches)
    status = 'branch_budget_exhausted' if len(proposals) > branch_cap else 'search_exhausted'
    return Result(status, None, count, branches)
