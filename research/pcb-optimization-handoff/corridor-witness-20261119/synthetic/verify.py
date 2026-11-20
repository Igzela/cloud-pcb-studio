from pathlib import Path
from collections import defaultdict
import json,math
from shapely.geometry import LineString
from shapely.ops import unary_union
from kiutils.board import Board
from kiutils.items.brditems import Segment
from kiutils.items.common import Position
from cloud_pcb.layout_route.dsn_ses_bridge import merge_ses_into_board
from cloud_pcb.verify_gate.gerber_drc import audit_general_board
root=Path('.run/corridor-witness-20261119/synthetic')
evidence={}
for trial in ('design','seeded'):
    pcb=Board.from_file(str(root/'unrouted.kicad_pcb'))
    ids={n.name:n.number for n in pcb.nets}
    names={n.number:n.name for n in pcb.nets}
    if trial=='seeded':
        fixed=json.loads((root/'fixed-witness.json').read_text())
        for item in fixed:
            for p,q in zip(item['coords'],item['coords'][1:]):
                pcb.traceItems.append(Segment(start=Position(X=p[0],Y=p[1]),end=Position(X=q[0],Y=q[1]),
                                   width=item['width'],layer=item['layer'],net=ids[item['net']]))
    _,ses=merge_ses_into_board(pcb,root/(trial+'.ses'))
    check=audit_general_board(pcb,{'components':[]}, {}, [])
    conductors=defaultdict(list); by_width=defaultdict(lambda:defaultdict(float))
    for item in pcb.traceItems:
        n=names[item.net];pts=[(item.start.X,item.start.Y),(item.end.X,item.end.Y)]
        line=LineString(pts);conductors[n].append(line.buffer(item.width/2))
        by_width[n][f"{item.width:.3f}"]+=line.length
    p=unary_union(conductors['lane_p']);n=unary_union(conductors['lane_n'])
    delta={k:dict(w) for k,w in by_width.items()}
    length_skew_mm=abs(sum(delta['lane_n'].values())-sum(delta['lane_p'].values()))
    evidence[trial]={'lvs_open':len(check['lvs_opens']),'lvs_short':len(check['lvs_shorts']),
                    'min_track_pair_gap_mm':round(p.distance(n),6),'end_to_end_centerline_skew_mm':round(length_skew_mm,6),
                    'track_length_by_net_and_width_mm':delta,'retained_fixed_segments':len(pcb.traceItems)-len(ses['segments']),
                    'released_ses_segments':ses['segments'],'layers_used':ses['layers_used']}
assert all(v['lvs_open']==v['lvs_short']==0 for v in evidence.values())
assert list(evidence['seeded']['layers_used'])==['F.Cu']
assert evidence['seeded']['min_track_pair_gap_mm']>=.33
assert evidence['seeded']['retained_fixed_segments']==12
assert all(v['0.125']<2.0 and v['0.290']>18 for v in evidence['seeded']['track_length_by_net_and_width_mm'].values())
(root/'physical-audit.json').write_text(json.dumps(evidence,indent=2)+'\n')
print(json.dumps({k:{a:b for a,b in v.items() if a!='released_ses_segments'} for k,v in evidence.items()},indent=2))
