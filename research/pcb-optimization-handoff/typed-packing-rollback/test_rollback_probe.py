from copy import deepcopy
import pytest
from shapely.geometry import box

from cloud_pcb.general_solver.placement_recovery import PackingExhausted
from cloud_pcb.general_solver.two_side_packing import in_outline, local_occupancy, noninterfering, pack_two_sided
from rollback_probe import repack_blockers


def device(w,h):
    return {'side':'top', 'body_bbox_mm':[-w/2,-h/2,w/2,h/2], 'pads':[]}


def crowded():
    return dict(big=device(6,5), small=device(4,4), pin=device(.4,.4)), {'pin':(11.3,6.7,0.,'top')}


def failed_greedy(parts, fixed, board, mode):
    # Exact production strategy; both size orderings begin with the same large
    # package competing for the same connected-neighbor target in the centre.
    poses=deepcopy(fixed)
    def remember(ref, where, side):
        poses[ref]=(*where,side)
    with pytest.raises(PackingExhausted,match='small'):
        pack_two_sided(parts, {ref:pose[:3] for ref,pose in fixed.items()},board,[],lambda _:(6,3.5),remember,
                       board_size=board.bounds[2:],mode=mode,protected_nets=frozenset())
    return poses


@pytest.mark.parametrize('mode',['area','long-side'])
def test_both_production_greedy_orders_fail_but_rollback_finds_valid_pose(mode):
    parts,fixed=crowded();board=box(0,0,12,7)
    incumbent=failed_greedy(parts,fixed,board,mode)
    untouched=deepcopy(incumbent)
    assert incumbent['big']==(6,3.5,0.,'top')
    result=repack_blockers(parts,incumbent,'small',{'pin'},board,(6,3.5),probe_budget=5000)
    assert result.status=='repacked' and result.moved==('big',)
    assert result.poses['pin']==fixed['pin'] and incumbent==untouched
    assert result.probes <=5000 and set(result.poses)=={'pin','big','small'}
    checks=[]
    for ref,(x,y,a,side) in result.poses.items():
        occupancy=local_occupancy(parts[ref],side).rotate(a).translate(x,y)
        assert in_outline(board,board.boundary,occupancy)
        assert all(noninterfering(side,occupancy,*q) for q in checks)
        checks.append((side,occupancy))
    assert result==repack_blockers(parts,incumbent,'small',{'pin'},board,(6,3.5),probe_budget=5000)


def test_impossible_same_side_body_area_does_not_commit_partial_choice():
    parts={'big':device(6,5),'small':device(5,5)}
    board=box(0,0,8,6)
    # The two body areas sum to 55 mm² in 48 mm²; both sides are fixed top.
    assert 6*5+5*5 > board.area
    incumbent={'big':(4.,3.,0.,'top')}
    original=deepcopy(incumbent)
    result=repack_blockers(parts,incumbent,'small',set(),board,(4,3),probe_budget=6500)
    assert result.status=='search_exhausted' and result.poses is None
    assert incumbent==original and result.probes<=6500


def test_probe_limit_is_distinct_from_search_exhaustion_and_pins_are_never_repacked():
    parts,fixed=crowded();board=box(0,0,12,7)
    fixed['big']=(6.,3.5,0.,'top');original=deepcopy(fixed)
    tiny=repack_blockers(parts,fixed,'small',{'pin'},board,(6,3.5),probe_budget=1)
    assert tiny.status=='probe_budget_exhausted' and tiny.probes==1 and tiny.poses is None
    pinned=repack_blockers(parts,fixed,'small',{'pin','big'},board,(6,3.5),probe_budget=5000)
    assert pinned.status=='search_exhausted' and pinned.poses is None and pinned.branches==0
    assert fixed==original
