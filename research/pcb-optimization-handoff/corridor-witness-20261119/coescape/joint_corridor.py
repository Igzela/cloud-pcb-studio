"""Scratch plan-only solver for bounded, single-layer joint escape problems.

Call with the affected nets together. Retained foreign copper is immutable.
The search proves route geometry within its graph, not a production edit.
"""
from collections import Counter
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import combinations, count
from math import isfinite, sqrt

from shapely.geometry import LineString, Point


@dataclass(frozen=True)
class EndpointRoute:
    net: str
    start: tuple[int, int]
    stop: tuple[int, int]
    width_mm: float
    clearance_mm: float
    layer: str = "F.Cu"


@dataclass(frozen=True)
class FixedCopper:
    shape: object
    clearance_mm: float
    owner_net: str | None = None


@dataclass(frozen=True)
class JointResult:
    status: str
    routes: tuple[tuple[tuple[int, int], ...], ...] = ()
    route_search_count: int = 0
    graph_expansions: int = 0


def _edge(a, b):
    return tuple(sorted((a, b)))


def _manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def solve_joint_corridors(
    outline, requests, fixed, *, step_um, max_board_edge_mm=.50,
    max_low_expansions=12_000, max_conflict_branches=96,
):
    """Search a finite Manhattan grid with geometry-aware conflict branching.

    At a collision between edges A and B, branch by forbidding A or B.
    Every accepted solution is valid for *all* requests. Reaching either
    budget produces no partial result. Anchors use exact integer µm input.
    To cover a 90° mitred bend conservatively, use a half width × √2 cap.
    """
    requests, fixed = tuple(requests), tuple(fixed)
    if (len(requests) < 2 or len(requests) > 3 or isinstance(step_um, bool)
        or not isinstance(step_um, int) or step_um < 1 or
        max_low_expansions < 1 or max_conflict_branches < 1):
        return JointResult('unsupported_input')
    if len({r.net for r in requests}) != len(requests) or not outline.is_valid:
        return JointResult('unsupported_input')
    if len({r.layer for r in requests}) != 1:
        return JointResult('unsupported_layer')
    origins={tuple(p % step_um for p in v) for r in requests for v in (r.start,r.stop)}
    if len(origins)!=1 or any(r.start==r.stop or not r.net for r in requests):
        return JointResult('unsupported_grid')
    if any(not isfinite(x) or x<0 for x in (max_board_edge_mm, *(f.clearance_mm for f in fixed))):
        return JointResult('unsupported_input')
    if any(not isfinite(x) or x<=0 for r in requests for x in (r.width_mm,r.clearance_mm)):
        return JointResult('unsupported_input')
    geom_by_edge={}
    legal_edge={}
    conflicts={}
    route_searches=0
    used_expansions=0
    exhausted=False
    # Stable numerical padding for µm grid and Shapely float comparisons.
    epsilon=.001
    radius=[r.width_mm*sqrt(2)/2 for r in requests]

    def shape(e):
        if e not in geom_by_edge:
            geom_by_edge[e]=LineString([(x/1000,y/1000) for x,y in e])
        return geom_by_edge[e]

    def allowed(i,e):
        k=(i,e)
        if k not in legal_edge:
            r,center,rad=requests[i],shape(e),radius[i]
            legal_edge[k]=(outline.covers(center)
                and center.distance(outline.boundary)>=max_board_edge_mm+rad+epsilon
                and all(f.owner_net==r.net or center.distance(f.shape)>=
                    rad+max(r.clearance_mm,f.clearance_mm)+epsilon for f in fixed))
        return legal_edge[k]

    def route(i, disallowed):
        nonlocal route_searches, used_expansions, exhausted
        route_searches+=1
        request=requests[i]
        p,q=request.start,request.stop
        heap=[(_manhattan(p,q),0,p)]
        cost={p:0}
        origin={}
        while heap:
            _,g,x=heappop(heap)
            if g!=cost[x]:
                continue
            if used_expansions>=max_low_expansions:
                exhausted=True
                return ()
            used_expansions+=1
            if x==q:
                nodes=[x]
                while nodes[-1]!=p:
                    nodes.append(origin[nodes[-1]])
                return tuple(reversed(nodes))
            for dx,dy in ((-step_um,0),(0,-step_um),(0,step_um),(step_um,0)):
                y=(x[0]+dx,x[1]+dy)
                new=g+step_um
                e=_edge(x,y)
                if (new>=cost.get(y,float('inf')) or e in disallowed or
                    not allowed(i,e)):
                    continue
                cost[y]=new
                origin[y]=x
                heappush(heap,(new+_manhattan(y,q),new,y))
        return ()

    def collides(paths):
        for i,j in combinations(range(len(paths)),2):
            required=radius[i]+radius[j]+max(requests[i].clearance_mm,requests[j].clearance_mm)+epsilon
            for a in map(lambda ab:_edge(*ab),zip(paths[i],paths[i][1:])):
                for b in map(lambda ab:_edge(*ab),zip(paths[j],paths[j][1:])):
                    key=(i,a,j,b)
                    if key not in conflicts:
                        conflicts[key]=shape(a).distance(shape(b))<required
                    if conflicts[key]:
                        return (i,a),(j,b)
        return None

    blocked=tuple(frozenset() for _ in requests)
    initial=tuple(route(i,stops) for i,stops in enumerate(blocked))
    if not all(initial):
        return JointResult('budget_exhausted' if exhausted else 'infeasible',(),route_searches,used_expansions)
    node_id=count()
    def state(paths, exclusions):
        return (sum(len(p) for p in paths),next(node_id),paths,exclusions)
    heap=[state(initial,blocked)]
    seen={blocked}
    for _ in range(max_conflict_branches):
        if not heap or exhausted:
            break
        _,_,paths,exclusions=heappop(heap)
        conflict=collides(paths)
        if conflict is None:
            return JointResult('feasible',paths,route_searches,used_expansions)
        for i,edge in conflict:
            revised=exclusions[:i]+(exclusions[i]|{edge},)+exclusions[i+1:]
            if revised in seen:
                continue
            seen.add(revised)
            p=route(i,revised[i])
            if p:
                heappush(heap,state(paths[:i]+(p,)+paths[i+1:],revised))
    return JointResult('budget_exhausted' if exhausted or heap else 'infeasible',(),route_searches,used_expansions)


def _open_groups(report):
    """Fail closed for ambiguous/not reported physical terminal partitions."""
    groups={}
    for item in report['lvs_opens']:
        net = item['net']
        parts=tuple(frozenset(part) for part in item['pad_groups'].values())
        counts=Counter(p for part in parts for p in part)
        if (net in groups or len(parts)<2 or len(parts)!=item['components'] or
            len(counts)!=item['pad_count'] or any(m!=1 for m in counts.values()) or
            any(not part for part in parts)):
            raise ValueError('unverifiable open partition')
        groups[net]=parts
    return groups


def accept_without_migrating_opens(
    before, after, before_real_violations, after_real_violations, planned_nets,
):
    """Require strict physical closure gain with no newly split old groups."""
    if any(type(n) is not int or n<0 for n in (before_real_violations,after_real_violations)):
        return False
    if after_real_violations>before_real_violations:
        return False
    try:
        if after['lvs_shorts'] or after['via_in_pad_violations']:
            return False
        original,latest=_open_groups(before),_open_groups(after)
    except (ValueError, KeyError, TypeError):
        return False
    if not set(latest).issubset(original):
        return False
    scope=set(planned_nets)
    if any(set(original[net])!=set(latest.get(net,())) for net in original if net not in scope):
        return False
    for n,new in latest.items():
        old=original[n]
        if not all(any(part.issubset(merged) for merged in new) for part in old):
            return False
        if frozenset().union(*old)!=frozenset().union(*new):
            return False
    return sum(len(p)-1 for p in latest.values()) < sum(len(p)-1 for p in original.values())
