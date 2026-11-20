import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from kiutils.board import Board
from kiutils.footprint import Footprint, Pad
from kiutils.items.brditems import Segment
from kiutils.items.common import Net, Position
from shapely.geometry import LineString, box
from shapely.ops import unary_union

from cloud_pcb.layout_route.dsn_ses_bridge import board_to_dsn, merge_ses_into_board
from cloud_pcb.verify_gate.gerber_drc import audit_general_board
from contracts.schemas import NetClassRule
from cloud_pcb.general_solver.pair_launch import propose_pair_launches, render_unchanged_design_with_fixed_launches


RULES={
    'DPAIR':NetClassRule('DPAIR',.29,.248,.3,.55,.3,.33,100.),
    'DEFAULT':NetClassRule('DEFAULT',.16,.15,.3,.55,.5),
}
LAYERS={'DPAIR':('F.Cu','B.Cu'),'DEFAULT':('F.Cu','In2.Cu','B.Cu')}
ASSIGN={'line_P':'DPAIR','line_N':'DPAIR'}
REQ=[{'pos_net':'line_P','neg_net':'line_N','max_skew_mm':.2}]


def fixture(transform=lambda x,y:(x,y)):
    b=Board.create_new()
    b.nets=[Net(0,''),Net(1,'line_P'),Net(2,'line_N')]
    for idx,(origin,local_x) in enumerate(((4.,1.),(26.,-1.))):
        x,y=transform(origin,6.)
        fp=Footprint(entryName='synthetic row',layer='F.Cu',position=Position(X=x,Y=y),
                     properties={'Reference':f'Part{idx}'})
        ox,oy=transform(0,0)
        xa,ya=transform(.875,.25)
        w,h=abs(xa-ox),abs(ya-oy)
        for pin,py in enumerate((-.75,-.25,.25,.75)):
            a,b0=transform(local_x,py)
            fp.pads.append(Pad(number=str(pin+1),type='smd',shape='rect',
                               position=Position(X=a-ox,Y=b0-oy),size=Position(X=w,Y=h),
                               layers=['F.Cu','F.Mask','F.Paste'],net={1:b.nets[1],2:b.nets[2]}.get(pin)))
        b.footprints.append(fp)
    x,y=transform(0,0)
    x1,y1=transform(30,12)
    return b,box(min(x,x1),min(y,y1),max(x,x1),max(y,y1))


def propose(board,outline,**kwargs):
    arguments=dict(keepouts=[], netclasses=RULES, assignments=ASSIGN, routing_layers=LAYERS,
            requirements=REQ, permitted_neck_mm=.125,neck_floor_mm=.125,max_neck_length_mm=1.2)
    return propose_pair_launches(board,outline,**(arguments|kwargs))


@pytest.mark.parametrize('transform', [
    lambda x,y:(x,y),
    lambda x,y:(30-x,y),
    lambda x,y:(x,12-y),
    lambda x,y:(y,x),
    lambda x,y:(12-y,30-x),
])
def test_rotated_mirrored_pairs_use_same_physical_admission(transform):
    board,outline=fixture(transform)
    actual=propose(board,outline)
    before=propose(*fixture())
    assert len(actual)==len(before)==2
    assert all(p.trace_gap_mm>=.33 and p.local_neck_length_mm<=1.2 for p in actual)
    for r,l in zip(before,actual):
        assert [t.net for t in l.leads]==['line_P','line_N']
        for a,b in zip(r.leads,l.leads):
            for field in ('pad_xy_um','knee_xy_um','exit_xy_um','anchor_xy_um'):
                wanted=tuple(round(v*1000) for v in transform(*(i/1000 for i in getattr(a,field))))
                assert getattr(b,field)==wanted
            assert a.neck_um==b.neck_um==125 and a.trunk_um==b.trunk_um==290
    assert not board.traceItems


def test_blocking_one_polarity_at_second_end_rolls_back_both_ends():
    b,o=fixture()
    corridor={'layers':['F.Cu'],'x_min':24.1,'x_max':24.45,'y_min':5.6,'y_max':5.85}
    assert not propose(b,o,keepouts=[corridor])


@pytest.mark.parametrize('decline', ['corner', 'mismatched_layers', 'under_gap_pitch',
                                     'no_neck', 'unknown_rule', 'budget', 'length_budget',
                                     'repeated_pad', 'repeated_pair_member'])
def test_unsupported_pair_declines_atomically(decline):
    b,o=fixture()
    args={}
    if decline=='corner':
        b.footprints[1].pads[2].position.X+=.1
    elif decline=='mismatched_layers':
        b.footprints[1].pads[2].layers=['B.Cu','B.Mask']
    elif decline=='under_gap_pitch':
        b.footprints[1].pads[2].position.Y=.15
    elif decline=='no_neck':
        args['no_neckdown_classes']={'DPAIR'}
    elif decline=='unknown_rule':
        args['netclasses']=RULES|{'DPAIR':replace(RULES['DPAIR'],impedance_ohm=None)}
    elif decline=='budget':
        args['max_groups']=1
    elif decline=='length_budget':
        args['max_neck_length_mm']=.3
    elif decline=='repeated_pad':
        b.footprints[1].pads.append(replace(b.footprints[1].pads[2]))
    elif decline=='repeated_pair_member':
        args['requirements']=REQ+[{'pos_net':'line_P','neg_net':'other'}]
    assert not propose(b,o,**args)
    assert not b.traceItems


def materialize_pair_copper(board,plans):
    names={n.name:n.number for n in board.nets}
    def point(p):return Position(X=p[0]/1000,Y=p[1]/1000)
    for plan in plans:
        for l in plan.leads:
            for a,b,width in (
                (l.pad_xy_um,l.knee_xy_um,l.neck_um),
                (l.knee_xy_um,l.exit_xy_um,l.neck_um),
                (l.exit_xy_um,l.anchor_xy_um,l.trunk_um),
            ):
                board.traceItems.append(Segment(start=point(a),end=point(b),
                    width=width/1000,layer=l.layer,net=names[l.net]))


def physical_metrics(board):
    audit=audit_general_board(board,{'components':[]},{},[])
    conductors={1:[],2:[]}
    lengths={1:{},2:{}}
    for t in board.traceItems:
        assert t.layer=='F.Cu' and isinstance(t,Segment)
        g=LineString([(t.start.X,t.start.Y),(t.end.X,t.end.Y)])
        conductors[t.net].append(g.buffer(t.width/2))
        lengths[t.net][t.width]=lengths[t.net].get(t.width,0)+g.length
    gap=unary_union(conductors[1]).distance(unary_union(conductors[2]))
    skew=abs(sum(lengths[1].values())-sum(lengths[2].values()))
    return len(audit['lvs_opens']),len(audit['lvs_shorts']),gap,skew,lengths


def test_real_primary_keeps_nominal_pair_rules_and_continues_from_both_seeds(tmp_path):
    b,o=fixture()
    original=board_to_dsn(b,tmp_path/'control.dsn',RULES,ASSIGN,
        board_bounds_mm=o.bounds,board_outline_mm=list(o.exterior.coords),
        routing_layers_by_class=LAYERS).read_text()
    assert '(rule (width 290) (clearance 248))' in original
    plans=propose(b,o)
    dsn=render_unchanged_design_with_fixed_launches(original,plans)
    start=dsn.index('  (wiring')
    end=dsn.index('  )', start)+3
    assert dsn[:start]+'  (wiring\n  )'+dsn[end:]==original
    assert dsn.count('(type fix)')==8
    (tmp_path/'seeded.dsn').write_text(dsn)
    pairs=tmp_path/'pairs.txt'
    pairs.write_text('pair line_P line_N gap=0.33 skew=0.20\n')
    actual={}
    for trial in ('control','seeded'):
        inp=tmp_path/f'{trial}.dsn';out=tmp_path/f'{trial}.ses';stats=tmp_path/f'{trial}.json'
        proc=subprocess.run(
            ['bash','-c','source scripts/env.sh && exec "$@"','router',
             'fastroute','-de',str(inp),'-do',str(out),f'--report={stats}',
             f'--pairs={pairs}','--max-time=8','--multi-start=1',
             '--router.autorouter.max_threads=1','--router.autorouter.max_passes=4',
             '--router.optimizer.enabled=false','--router.fanout.enabled=false',
             '--router.neck_width_um=125','--router.min_trace_width_um=125',
             '--router.copper_to_edge_clearance_um=500'],
            capture_output=True,text=True,check=True,timeout=15)
        raw=json.loads(stats.read_text())
        assert raw['stats']['unrouted']==raw['stats']['violations']==0
        result,_=fixture()
        if trial=='seeded':
            materialize_pair_copper(result,plans)
        merge_ses_into_board(result,out)
        actual[trial]=physical_metrics(result)
        (tmp_path/f'{trial}.log').write_text(proc.stdout+proc.stderr)
    a,c,gap_cd,skew_cd,_=actual['control']
    assert (a,c)==(0,0) and gap_cd<.33 and skew_cd<=.2
    a,c,gap,skew,lengths=actual['seeded']
    assert (a,c)==(0,0) and gap>=.33 and skew<=.2
    assert set(lengths[1])==set(lengths[2])=={.125,.29}
    assert all(w[.29]>18 and w[.125] <=2.4 for w in lengths.values())
