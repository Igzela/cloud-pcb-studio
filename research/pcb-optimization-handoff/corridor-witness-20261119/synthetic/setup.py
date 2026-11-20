from pathlib import Path
from collections import Counter
from dataclasses import asdict
from copy import deepcopy
import json
from kiutils.board import Board
from kiutils.footprint import Footprint,Pad
from kiutils.items.common import Position,Net
from shapely.geometry import box,LineString
from cloud_pcb.layout_route.dsn_ses_bridge import board_to_dsn
from cloud_pcb.general_solver.run_blind import _pad_world_polygon_kicad
from contracts.schemas import NetClassRule

out=Path('.run/corridor-witness-20261119/synthetic')
board=Board.create_new()
board.nets=[Net(0,''),Net(1,'lane_p'),Net(2,'lane_n')]
protected={'DPAIR':NetClassRule('DPAIR',.29,.248,.3,.55,.3,.33,100.),
           'DEFAULT':NetClassRule('DEFAULT',.16,.15,.3,.55,.5)}
layers={'DPAIR':('F.Cu','B.Cu'),'DEFAULT':('F.Cu','In2.Cu','B.Cu')}
nets={'lane_p':'DPAIR','lane_n':'DPAIR'}
bounds=((0,0),(30,0),(30,12),(0,12),(0,0))
outline=box(0,0,30,12)
xpos=(5.,25.)
y_for_net={'lane_p':5.75,'lane_n':6.25}
for idx,x in enumerate(xpos):
    fp=Footprint(entryName='Synthetic fine pitch row',layer='F.Cu',
                 position=Position(X=x,Y=6.),properties={'Reference':f'Device{idx}'})
    for pin,y in enumerate((-.75,-.25,.25,.75)):
        fp.pads.append(Pad(number=str(pin+1),type='smd',shape='rect',
                           position=Position(X=0,Y=y),size=Position(X=.875,Y=.25),
                           layers=['F.Cu','F.Mask','F.Paste'],net={1:board.nets[1],2:board.nets[2]}.get(pin)))
    board.footprints.append(fp)
board.to_file(str(out/'unrouted.kicad_pcb'))
dsn=board_to_dsn(board,out/'design.dsn',protected,nets,
                  board_bounds_mm=(0,0,30,12),board_outline_mm=list(bounds),
                  routing_layers_by_class=layers).read_text()
# Both launches match exactly; .125 is the predeclared local neck allowance,
# .290 trunk has the original clearance/class and begins after the expanded pitch.
wires=[]
for xi,x in enumerate(xpos):
    toward_trunk=1 if xi==0 else -1
    for sign,net in zip((-1,1),y_for_net):
        y=y_for_net[net]; a=(x,y);b=(x+.90*toward_trunk,y)
        c=(x+.962*toward_trunk,y+sign*.062);d=(x+1.662*toward_trunk,c[1])
        wires.append({'net':net,'width':.125,'layer':'F.Cu','coords':[a,b,c]})
        wires.append({'net':net,'width':.29,'layer':'F.Cu','coords':[c,d]})
assert dsn.count('  (wiring\n  )')==1
lines=[]
for t in wires:
    coords=' '.join(f'{round(x*1000)} {round(-y*1000)}' for x,y in t['coords'])
    lines.append(f"    (wire (path F.Cu {round(t['width']*1000)} {coords}) (net {t['net']}) (type fix))")
fixed='  (wiring\n'+'\n'.join(lines)+'\n  )'
seeded=dsn.replace('  (wiring\n  )',fixed)
assert seeded.replace(fixed,'  (wiring\n  )')==dsn
(out/'seeded.dsn').write_text(seeded)
(out/'fixed-witness.json').write_text(json.dumps(wires,indent=2))
(out/'pairs.txt').write_text('pair lane_p lane_n gap=0.33 skew=0.20\n')
# Independent physical admission before invoking the engine at all.
def assess(proposal,obstacles):
    copper=[(w['net'],LineString(w['coords']).buffer(w['width']/2,join_style='mitre')) for w in proposal]
    for n,g in copper:
        assert outline.covers(g)
        if outline.boundary.distance(g)<.50:return False
        for obstacle in obstacles:
            if g.distance(obstacle)<.248:return False
        for fp in board.footprints:
            for p in fp.pads:
                if p.net and p.net.name==n: continue
                if g.distance(_pad_world_polygon_kicad(fp,p))<.248:return False
        for other,h in copper:
            if other!=n and g.distance(h)<.330:return False
    return True

assert assess(wires,[])
# No "repair" through a routing corridor keepout.
assert not assess(wires,[box(5.6,5.60,5.75,5.9)])
# No unilateral compression of the pair, even if the two nets still clear
# the basic .248 mm cross-net spacing.
compressed=deepcopy(wires)
right=next(w for w in compressed if w['net']=='lane_n' and w['width']==.29)
right['coords']=[(x,y-.065) for x,y in right['coords']]
assert not assess(compressed,[])
# Full .29 width in the unchanged .50-pitch row violates the rule at the origin.
unnecked=deepcopy(wires)
unnecked[0]['width']=.29
assert not assess(unnecked,[])
print(json.dumps({'fixed_trace_count':len(wires),'preserved_rules_and_pad_library':True,
                  'independent_geometric_checks':['clear mirrored launches','blocked corridor rejected',
                    'compressed pair rejected','un-necked centered pad rejected']}))
