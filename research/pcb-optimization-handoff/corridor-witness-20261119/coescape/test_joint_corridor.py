from shapely.geometry import LineString, box
from shapely.ops import unary_union
from kiutils.board import Board
from kiutils.footprint import Footprint,Pad
from kiutils.items.brditems import Segment
from kiutils.items.common import Net, Position
from cloud_pcb.verify_gate.gerber_drc import audit_general_board

from joint_corridor import (
    EndpointRoute, FixedCopper, solve_joint_corridors, accept_without_migrating_opens,
)


OUTLINE=box(0,0,9,6)
REQUESTS=(
    EndpointRoute('previously_good',(1000,3000),(8000,3000),.16,.15),
    EndpointRoute('isolated',(4000,2000),(4000,4000),.22,.25),
)
FLOOR_KEEP_OUT=FixedCopper(box(3,0,5,1.35),.25)

def mm(p):return (p[0]/1000,p[1]/1000)
def pos(p):return Position(X=p[0]/1000,Y=p[1]/1000)

def board_with(paths):
    b=Board.create_new()
    b.nets=[Net(0,'')]+[Net(i+1,r.net) for i,r in enumerate(REQUESTS)]
    for i,r in enumerate(REQUESTS,1):
        for j,p in enumerate((r.start,r.stop),1):
            footprint=Footprint(entryName='Synthetic SMD anchor',
                    position=pos(p),properties={'Reference':f'D{i}_{j}'})
            footprint.pads=[Pad(number='1',type='smd',shape='rect',
                                size=Position(X=.25,Y=.25),layers=['F.Cu','F.Mask'],
                                net=b.nets[i])]
            b.footprints.append(footprint)
    for i,path in enumerate(paths,1):
        width=REQUESTS[i-1].width_mm
        for p,q in zip(path,path[1:]):
            b.traceItems.append(Segment(start=pos(p),end=pos(q),width=width,layer='F.Cu',net=i))
    return b


def audit(paths):
    return audit_general_board(board_with(paths),{'components':[]},{},[])


def test_joint_search_unlocks_blocked_corridor_without_open_migration():
    direct_good = (REQUESTS[0].start, REQUESTS[0].stop)
    # Keeping this movable copper fixed divides the usable 1 mm grid.
    old = FixedCopper(LineString(list(map(mm,direct_good))).buffer(.16/2),.15,REQUESTS[0].net)
    blocked=solve_joint_corridors(OUTLINE,REQUESTS,(FLOOR_KEEP_OUT,old),step_um=1000)
    assert blocked.status=='infeasible' and not blocked.routes
    result=solve_joint_corridors(OUTLINE,REQUESTS,(FLOOR_KEEP_OUT,),step_um=1000)
    assert result.status=='feasible', result
    assert result.route_search_count>2  # The independently shortest paths crossed.
    assert result==solve_joint_corridors(OUTLINE,REQUESTS,(FLOOR_KEEP_OUT,),step_um=1000)
    before=audit((direct_good,()))
    final=audit(result.routes)
    assert [n['net'] for n in before['lvs_opens']]==[REQUESTS[1].net]
    assert not final['lvs_opens'] and not final['lvs_shorts']
    assert accept_without_migrating_opens(before,final,0,0,[r.net for r in REQUESTS])
    envelopes=[]
    for path,request in zip(result.routes,REQUESTS):
        trace=LineString(list(map(mm,path))).buffer(request.width_mm/2,join_style='mitre')
        assert trace.distance(FLOOR_KEEP_OUT.shape)>=.25
        assert OUTLINE.boundary.distance(trace)>=.50
        envelopes.append(trace)
    assert envelopes[0].distance(envelopes[1])>=.25

    # A premature repair of only the original failure would leave the older
    # net open. Same number of opens is not a closure gain.
    migrated=audit(((),(REQUESTS[1].start,REQUESTS[1].stop)))
    assert [n['net'] for n in migrated['lvs_opens']]==[REQUESTS[0].net]
    assert not accept_without_migrating_opens(before,migrated,0,0,[r.net for r in REQUESTS])


def test_conflict_budget_exhaustion_never_returns_shortest_conflicting_pair():
    result=solve_joint_corridors(OUTLINE,REQUESTS,(FLOOR_KEEP_OUT,),step_um=1000,
                                 max_conflict_branches=1)
    assert result.status=='budget_exhausted' and not result.routes


def test_physical_cut_no_partial_route():
    right_half_cut=FixedCopper(box(4.4,0,5.6,6),.15)
    result=solve_joint_corridors(OUTLINE,REQUESTS,(right_half_cut,),step_um=1000)
    assert result.status=='infeasible' and not result.routes


def test_open_partition_cannot_split_old_pad_group_even_when_counts_improve():
    def report(*sets):
        items=[] if not sets else [dict(net='x',components=len(sets),
                 pad_count=len(set().union(*sets)),pad_groups={str(i):tuple(s) for i,s in enumerate(sets)})]
        return dict(lvs_opens=items,lvs_shorts=[],via_in_pad_violations=[])
    before=report({'a','b'},{'c'},{'d'})
    genuine=report({'a','b','c'},{'d'})
    assert accept_without_migrating_opens(before,genuine,0,0,['x'])
    assert not accept_without_migrating_opens(before,genuine,0,0,['other'])
    assert not accept_without_migrating_opens(before,genuine,0,1,['x'])
    assert not accept_without_migrating_opens(before,report({'a','c'},{'b','d'}),0,0,['x'])
    assert not accept_without_migrating_opens(before,report({'a','b','c'},{'b','d'}),0,0,['x'])
