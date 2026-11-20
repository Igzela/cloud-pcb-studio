from pathlib import Path
from collections import defaultdict
import json
from shapely.geometry import LineString,Point
from kiutils.board import Board
from kiutils.items.brditems import Segment, Via
from cloud_pcb.layout_route.dsn_ses_bridge import merge_ses_into_board
from cloud_pcb.general_solver.run_blind import _build_blind_kicad_board
r=Path('artifacts/holdouts/power-and-industrial-02-retry-01/case11')
local=Path('.run/corridor-witness-20261119')
report=json.loads((r/'blind_run_report.json').read_text())
spec=json.loads((r/'effective_spec.json').read_text())
_,classes,assign,_,_= _build_blind_kicad_board(spec,report['placed_positions'])
raw=json.loads((local/'joint-probe.json').read_text())['pre_route/joint']['paired_via_landing']
board=Board.from_file(str(r/'board_unrouted.kicad_pcb'))
first_routed=len(board.traceItems)
merge_ses_into_board(board,r/'board_fastroute.ses')
numbers={n.number:n.name for n in board.nets}
evidence=[]
for rec in raw['traces']:
    net=rec['net'];trace=LineString(rec['vertices']).buffer(.125/2,join_style='mitre')
    via=Point(rec['vertices'][-1]).buffer(.55/2)
    required=lambda other:max(classes[assign[net]].clearance_mm,classes[assign.get(other,'DEFAULT')].clearance_mm,.15)
    blockers=[]
    for item in board.traceItems[first_routed:]:
        n=numbers[item.net]
        if n==net:continue
        if isinstance(item,Segment):
            shape=LineString([(item.start.X,item.start.Y),(item.end.X,item.end.Y)]).buffer(item.width/2)
            layer=item.layer
            vdist=via.distance(shape)
            sdist=trace.distance(shape) if item.layer=='F.Cu' else float('inf')
        elif isinstance(item,Via):
            shape=Point(item.position.X,item.position.Y).buffer(item.size/2)
            layer='through'
            vdist=via.distance(shape)
            sdist=trace.distance(shape)
        else:continue
        for kind,dist in [('neck',sdist),('via',vdist)]:
            if dist<required(n)+.005:
                blockers.append({'blocks':kind,'net':n,'layer':layer,'gap_mm':round(dist,5),
                                 'required_mm':required(n)})
    counts=defaultdict(list)
    for item in blockers:counts[(item['net'],item['layer'],item['blocks'])].append(item['gap_mm'])
    evidence.append({'net':net,'blockers':[{'net':key[0],'layer':key[1],'part':key[2],
                                          'count':len(v),'min_gap_mm':min(v)}
                                         for key,v in sorted(counts.items())]})
print(json.dumps(evidence,ensure_ascii=False,indent=2))
