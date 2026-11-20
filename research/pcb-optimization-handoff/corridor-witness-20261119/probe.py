from pathlib import Path
from collections import defaultdict
import heapq,json,math
import numpy as np
import shapely
from shapely.geometry import Point,LineString,box
from shapely.ops import unary_union
from shapely.prepared import prep
from kiutils.board import Board
from kiutils.items.brditems import Segment,Via
from cloud_pcb.general_solver.direct_repair import (_board_obstacles,_copper_layers,
    _keepout_polygon,_Obstacle,_pad_dsn_center,COPPER_LAYERS)
from cloud_pcb.general_solver.run_blind import _build_blind_kicad_board, _pad_world_polygon_kicad
from cloud_pcb.layout_route.dsn_ses_bridge import merge_ses_into_board, _pad_world_dims

root=Path('artifacts/holdouts/power-and-industrial-02-retry-01/case11')
raw=json.loads((root/'blind_run_report.json').read_text())
spec=json.loads((root/'effective_spec.json').read_text())
report=json.loads((root/'fastroute_report.json').read_text())
gap_report=report['unrouted'][0]
pair=next(p for p in spec['design_requirements']['differential_pairs']
          if gap_report['net'] in (p['pos_net'],p['neg_net']))
_,rules,classes,_,outline=_build_blind_kicad_board(spec,raw['placed_positions'])
pair_nets=[pair['pos_net'],pair['neg_net']]
geo={}
for phase in ('pre_route','selected'):
    board=Board.from_file(str(root/'board_unrouted.kicad_pcb'))
    old_count=len(board.traceItems)
    if phase=='selected':
        merge_ses_into_board(board,root/'board_fastroute.ses')
    pads,obstacles=_board_obstacles(board,raw['keepouts'])
    # The router uses quantized boxes for noncircular pads. Both the physical
    # rounded pad and the DSN envelope must clear a proposed trace.
    for fp in board.footprints:
        for pad in fp.pads:
            if pad.type=='np_thru_hole':continue
            px,py=(q/1000 for q in _pad_dsn_center(fp,pad))
            w,h=_pad_world_dims(pad.size.X,pad.size.Y,pad.position.angle or 0.)
            w,h=max(100,round(w*1000)),max(100,round(h*1000))
            n=pad.net.name if pad.net else None
            if pad.shape=='circle':stack=Point(px,py);radius=w/2000
            else:
                hw,hh=w//2/1000,h//2/1000
                stack=box(px-hw,py-hh,px+hw,py+hh);radius=0.
            for l in COPPER_LAYERS if pad.type=='thru_hole' else _copper_layers(pad.layers):
                obstacles[l].append(_Obstacle(stack,n,radius))
    for net in pair_nets:
        source_labels={a['component'] for a in (gap_report['from'],gap_report['to'])}
        fp,pad0,_,_=next(p[0] for (ref,pin),p in pads.items()
             if len(p)==1 and ref in source_labels and p[0][2]==net and
             p[0][0].properties["Reference"]==gap_report['from']['component'])
        anchor=tuple(q/1000 for q in _pad_dsn_center(fp,pad0))
        local=box(anchor[0]-2.5,anchor[1]-2.5,anchor[0]+2.5,anchor[1]+2.5)
        clearance=lambda other:max(.15,rules[classes[net]].clearance_mm,
                             rules[classes.get(other,'DEFAULT')].clearance_mm)
        all_width=rules[classes[net]].trace_width_mm
        def no_contact_keepouts():
            return [_keepout_polygon(k) for k in raw['keepouts']
                    if k.get('dsn_kind')=='via_keepout']
        via_radius=.275
        via_obstacles=set(o for l in COPPER_LAYERS for o in obstacles[l]
                          if o.net!=net and o.geometry.distance(local)<.7)
        via_rects=[o.geometry.buffer(via_radius+clearance(o.net)+o.extra_radius_mm+.004,quad_segs=12)
                   for o in via_obstacles]
        via_rects.extend(g.buffer(via_radius+clearance(None)+.004,quad_segs=12)
                         for g in no_contact_keepouts() if g.distance(local)<.7)
        via_rects.extend(_pad_world_polygon_kicad(f,p).buffer(via_radius+clearance(p.net.name if p.net else None)+.004)
                         for f in board.footprints for p in f.pads if p.type=='smd' and
                         _pad_world_polygon_kicad(f,p).distance(local)<.7)
        no_via=unary_union(via_rects)
        side=range(-50,51); ix,iy=np.meshgrid(side,side,indexing='ij')
        x,y=anchor[0]+ix*.05,anchor[1]+iy*.05
        via_ok=~shapely.intersects_xy(no_via,x,y) & shapely.intersects_xy(outline.buffer(-.779),x,y)
        min_margin={}
        for w in (all_width,.125):
            gap_to=sorted((round(o.geometry.distance(Point(anchor))-o.extra_radius_mm-w/2-clearance(o.net),6),str(o.net))
                          for o in obstacles['F.Cu'] if o.net!=net and o.geometry.distance(Point(anchor))<.8)
            # A full-width takeoff must begin at the actual DSN center; a
            # >0 margin in this local probe is only a geometric witness.
            f_copper=[o.geometry.buffer(w/2+clearance(o.net)+o.extra_radius_mm+.005,quad_segs=12)
                      for o in obstacles['F.Cu'] if o.net!=net and o.geometry.distance(local)<.65]
            f_forbidden=unary_union(f_copper)
            clear=~shapely.intersects_xy(f_forbidden,x,y) & shapely.intersects_xy(outline.buffer(-(.505+w/2)),x,y)
            obs=prep(f_forbidden); points={}
            reach={}
            if clear[50,50]:
                heap=[(0.,0,0)];reach={(0,0):0.}
                while heap:
                    cost,i,j=heapq.heappop(heap)
                    if cost!=reach[i,j]:continue
                    p=(x[i+50,j+50],y[i+50,j+50])
                    if via_ok[i+50,j+50]:points[(i,j)]=(cost,[round(float(v),3) for v in p])
                    for a,b in ((1,0),(0,1),(-1,0),(0,-1),(1,-1),(1,1),(-1,1),(-1,-1)):
                        u,v=i+a,j+b;k=(u,v)
                        delta=.05*math.hypot(a,b)
                        if not (-50<=u<=50 and -50<=v<=50) or not clear[u+50,v+50] or cost+delta>=reach.get(k,math.inf)-1e-9:continue
                        edge=LineString([p,(x[u+50,v+50],y[u+50,v+50])])
                        if obs.intersects(edge):continue
                        if not outline.covers(edge) or outline.boundary.distance(edge)<.505+w/2:continue
                        reach[k]=cost+delta;heapq.heappush(heap,(cost+delta,u,v))
            min_margin[str(w)]={'pin_center_is_clear':bool(clear[50,50]),'min_class_margin_mm':gap_to[:3],
                                'reachable_cells':len(reach),'via_sites':len(points),
                                'nearest_via':min(points.values()) if points else None}
        geo[f'{phase}/{net}']={'pin':pad0.number,'position_mm':anchor,'width_probes':min_margin}
print(json.dumps(geo,ensure_ascii=False,indent=2))
