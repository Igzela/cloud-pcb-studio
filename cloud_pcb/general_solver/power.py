"""Per-net routing intent; plane candidates must have real plated connections."""
from __future__ import annotations

import math
from dataclasses import replace

from shapely.geometry import MultiPoint, LineString, Point
from shapely.ops import unary_union

from cloud_pcb.parts_dfm.dfm_physics import calc_ipc2221_trace_width_mil


def specialize_power_rules(spec, classes, assignments):
    result=dict(classes); mapping=dict(assignments); intent={}
    rails={r['net']:r for r in spec.get('design_requirements',{}).get('power_rails',[])}
    for i,net in enumerate(sorted(n for n,c in mapping.items() if c=='POWER')):
        req=rails.get(net,{})
        current=req.get('max_current_a')
        if current is None:
            current=req.get('current_a')
        local_domain = (req.get('routing_scope') == 'single_ic_domain'
                        and req.get('routing_scope_evidence', {}).get('scope') == 'single_ic_domain'
                        and current is None)
        # Unknown local-domain current is not a rating. Use a local routing
        # intent instead of turning every supply escape into a board backbone.
        # Explicit current and width requirements always take precedence.
        default_width = max(0.25, classes.get('DEFAULT', classes['POWER']).trace_width_mm) if local_domain else 0.50
        width=max(default_width,float(req.get('min_trace_width_mm',default_width)))
        if current is not None:
            width=max(width,calc_ipc2221_trace_width_mil(float(current),temp_rise_c=float(req.get('temperature_rise_c',10)),cu_oz=float(req.get('copper_oz',1)),internal=False)*0.0254)
        # Clearance inferred independently for THIS net's pads, not an unrelated IC.
        gap=math.inf
        for comp in spec['components']:
            for p in comp['pads']:
                if p.get('net')!=net: continue
                for q in comp['pads']:
                    if q.get('net')==net: continue
                    dx=max(0,abs(float(p['x_rel_mm'])-float(q['x_rel_mm']))-(float(p['w_mm'])+float(q['w_mm']))/2)
                    dy=max(0,abs(float(p['y_rel_mm'])-float(q['y_rel_mm']))-(float(p['h_mm'])+float(q['h_mm']))/2)
                    gap=min(gap,math.hypot(dx,dy))
        clearance=min(0.15,max(0.125,gap-0.002))
        name=f'POWER_{i:03d}'
        result[name]=replace(classes['POWER'],name=name,trace_width_mm=round(width,3),clearance_mm=round(clearance,3),max_current_a=float(current) if current is not None else classes['POWER'].max_current_a)
        mapping[net]=name
        intent[net]=dict(class_name=name,trunk_width_mm=round(width,3),local_neckdown=True,current_a=current,current_verified=current is not None,routing_scope=req.get('routing_scope','board_distribution'))
    return result,mapping,intent


def power_islands(board, board_poly, spec, keepouts):
    from kiutils.items.brditems import Via, Segment
    names={n.number:n.name for n in board.nets}
    rails={r['net']:r for r in spec.get('design_requirements',{}).get('power_rails',[]) if r['net'].upper() not in {'GND','AGND','DGND','PGND','VSS'}}
    plated={n:[] for n in rails}
    obstacles=[]
    for item in board.traceItems:
        if isinstance(item,Via):
            n=names.get(item.net,''); pt=Point(item.position.X,item.position.Y)
            obstacles.append((n,pt.buffer(item.size/2+0.25)))
            if n in plated: plated[n].append((item.position.X,item.position.Y))
        elif isinstance(item,Segment) and item.layer=='B.Cu':
            # Preserve a continuous bottom-side reference corridor.
            obstacles.append(('*',LineString([(item.start.X,item.start.Y),(item.end.X,item.end.Y)]).buffer(1.0)))
        elif isinstance(item,Segment) and item.layer=='In2.Cu':
            obstacles.append((names.get(item.net,''),LineString([(item.start.X,item.start.Y),(item.end.X,item.end.Y)]).buffer(item.width/2+.25)))
    from cloud_pcb.general_solver.run_blind import _pad_world_polygon_kicad
    for fp in board.footprints:
        for p in fp.pads:
            if p.type in ('thru_hole','np_thru_hole'):
                g=_pad_world_polygon_kicad(fp,p); n=p.net.name if p.net else ''
                obstacles.append((n,g.buffer(0.25)))
                if p.type=='thru_hole' and n in plated: plated[n].append((g.centroid.x,g.centroid.y))
    from shapely.geometry import box
    for ko in keepouts:
        if ko.get('kind')=='rf': obstacles.append(('*',box(ko['x_min'],ko['y_min'],ko['x_max'],ko['y_max'])))
    result=[]; used=[]
    for net,pts in plated.items():
        req=rails[net]; current=req.get('max_current_a')
        if current is None:
            current=req.get('current_a',0) or 0
        if len(set(pts))<2 or (current<1 and len(spec.get('nets',{}).get(net,[]))<6): continue
        candidate=MultiPoint(pts).convex_hull.buffer(0.8).intersection(board_poly.buffer(-0.5))
        candidate=candidate.difference(unary_union([g for n,g in obstacles if n!=net]+used))
        parts=list(candidate.geoms) if candidate.geom_type=='MultiPolygon' else [candidate]
        kept=[g for g in parts if g.area>=2 and sum(g.covers(Point(p)) for p in pts)>=2]
        if kept:
            geom=unary_union(kept); used.append(geom.buffer(0.25))
            result.append(dict(net=net,layer='In2.Cu',geometry=geom,plated_anchor_count=len(pts)))
    return result


def width_distribution(board, power_nets):
    from kiutils.items.brditems import Segment
    names={n.number:n.name for n in board.nets}; result={}
    for item in board.traceItems:
        if isinstance(item,Segment) and names.get(item.net) in power_nets:
            n=names[item.net]; hist=result.setdefault(n,{})
            w=f'{float(item.width):.3f}'; hist[w]=hist.get(w,0)+math.hypot(item.end.X-item.start.X,item.end.Y-item.start.Y)
    return result
