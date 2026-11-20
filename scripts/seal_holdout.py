"""One-way holdout import. Never print source placement/routing or full ASTs.

Run with scripts/env.sh sourced. Only this importer may read reference bytes
before the freeze. Output is an explicit geometry/netlist whitelist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from kiutils.utils.sexpr import parse_sexp
from shapely.geometry import LineString
from shapely.ops import polygonize, polygonize_full, unary_union
from shapely.geometry import box
from shapely.affinity import rotate, translate


def children(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]


def child(node, key, default=None):
    return next(iter(children(node, key)), default)


def prop(fp, key):
    for p in children(fp, 'property'):
        if p[1] == key:
            return str(p[2])
    for p in children(fp, 'fp_text'):
        if str(p[1]).lower() == key.lower():
            return str(p[2])
    return ''


def arc_points(a, m, b):
    ax, ay = a; mx, my = m; bx, by = b
    d = 2 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my))
    if abs(d) < 1e-9:
        return [a, m, b]
    u = ((ax*ax+ay*ay)*(my-by)+(mx*mx+my*my)*(by-ay)+(bx*bx+by*by)*(ay-my))/d
    v = ((ax*ax+ay*ay)*(bx-mx)+(mx*mx+my*my)*(ax-bx)+(bx*bx+by*by)*(mx-ax))/d
    aa, ma, ba = [math.atan2(y-v, x-u) for x,y in (a,m,b)]
    span = (ba-aa) % (2*math.pi)
    if (ma-aa) % (2*math.pi) > span:
        span -= 2*math.pi
    r = math.hypot(ax-u, ay-v)
    return [(u+r*math.cos(aa+span*i/32),v+r*math.sin(aa+span*i/32)) for i in range(33)]


def extract(raw: bytes, case: int, *, strict_intake: bool = False):
    ast = parse_sexp(raw.decode('utf-8'))
    nets_by_id = {int(n[1]): str(n[2]) for n in children(ast, 'net') if len(n)>2}
    layer_names = [str(x[1]) for x in child(ast, 'layers')[1:] if isinstance(x,list)]
    layer_count = sum(n.endswith('.Cu') for n in layer_names)
    if layer_count != 4:
        raise ValueError('candidate must have exactly four copper layers')
    edges=[]
    for item in ast:
        if not isinstance(item,list) or not item or child(item,'layer',[None,None])[1]!='Edge.Cuts':
            continue
        if item[0] in ('gr_line','gr_arc','gr_rect'):
            a=tuple(map(float,child(item,'start')[1:3])); b=tuple(map(float,child(item,'end')[1:3]))
            if item[0]=='gr_arc':
                pts=arc_points(a,tuple(map(float,child(item,'mid')[1:3])),b)
            elif item[0]=='gr_rect':
                pts=[a,(b[0],a[1]),b,(a[0],b[1]),a]
            else:
                pts=[a,b]
            pts[0]=a; pts[-1]=b if item[0]!='gr_rect' else a
            edges.append(LineString([(round(x,6),round(y,6)) for x,y in pts]))
        elif item[0]=='gr_poly':
            pts=[tuple(map(float,p[1:3])) for p in child(item,'pts')[1:]]
            edges.append(LineString(pts+[pts[0]]))
        elif strict_intake:
            raise ValueError("Unsupported board-edge primitive in blind intake")
    merged = unary_union(edges)
    if strict_intake:
        polygons, cuts, dangles, invalid = polygonize_full(merged)
        if not all(g.is_empty for g in (cuts, dangles, invalid)):
            raise ValueError("Blind intake rejects open, dangling or invalid board-edge lines")
        outlines = list(polygons.geoms)
    else:
        outlines = list(polygonize(merged))
    if not outlines:
        raise ValueError('no closed outline')
    outline=max(outlines,key=lambda p:p.area)
    if strict_intake and (
        len(outlines) != 1 or outline.interiors or
        any(child(g, 'layer', [None, None])[1] == 'Edge.Cuts'
            for fp in children(ast, 'footprint') for g in fp if isinstance(g, list))
    ):
        raise ValueError("Blind intake requires one closed outer outline without unsupported cutouts")
    ox,oy,x1,y1=outline.bounds
    components=[]; holes=[]; interfaces=[]; nets=defaultdict(list)
    for fp in children(ast,'footprint'):
        ref=prop(fp,'Reference'); value=prop(fp,'Value')
        pads=children(fp,'pad')
        if not pads:
            continue
        # All-hole mechanical footprints are allowed to retain their world hole locations.
        if all(str(p[2])=='np_thru_hole' for p in pads):
            at=child(fp,'at',[None,0,0,0]); angle=float(at[3]) if len(at)>3 else 0
            for p in pads:
                pos=child(p,'at',[None,0,0]); dr=child(p,'drill',[None,0])
                if str(dr[1])=='oval':
                    raise ValueError('slotted mounting hole needs explicit schema support')
                t=math.radians(angle); x,y=map(float,pos[1:3])
                holes.append(dict(x_mm=float(at[1])+x*math.cos(t)+y*math.sin(t),y_mm=float(at[2])-x*math.sin(t)+y*math.cos(t),drill_mm=float(dr[1])))
            continue
        body=[]
        for g in fp:
            if isinstance(g,list) and g and g[0] in ('fp_line','fp_rect','fp_poly'):
                layer=child(g,'layer',[None,''])[1]
                if str(layer).endswith('.Fab'):
                    for key in ('start','end'):
                        c=child(g,key)
                        if c: body.append([float(c[1]),float(c[2])])
                    for pt in child(g,'pts',[])[1:]:
                        body.append([float(pt[1]),float(pt[2])])
        outpads=[]
        for p in pads:
            raw_layers=child(p,'layers',[None,'F.Cu'])[1:]
            if not any(str(l).endswith('.Cu') or l=='*.Cu' for l in raw_layers) and str(p[2])!='np_thru_hole':
                continue
            at=child(p,'at',[None,0,0]); size=child(p,'size'); net=child(p,'net')
            # Drop instance pad angle: in KiCad it is board-absolute. Derive local angle.
            fp_at=child(fp,'at',[None,0,0,0]); fa=float(fp_at[3]) if len(fp_at)>3 else 0
            # In a placed PCB, an omitted pad angle means absolute ZERO,
            # not inheritance of footprint orientation.
            pa=float(at[3]) if len(at)>3 else 0
            netname=str(net[2]) if net and len(net)>2 else (nets_by_id.get(int(net[1]),'') if net and isinstance(net[1],(int,float)) else str(net[1]) if net else '')
            layers=child(p,'layers',[None,'F.Cu','F.Paste','F.Mask'])[1:]
            layers=[str(l).replace('B.','F.') for l in layers] # canonical top-side footprint
            drill=child(p,'drill'); drill_mm=None
            if drill:
                drill_mm=float(drill[2] if str(drill[1])=='oval' else drill[1])
            ps=dict(num=str(p[1]),net=netname,x_rel_mm=float(at[1]),y_rel_mm=float(at[2]),w_mm=float(size[1]),h_mm=float(size[2]),shape=str(p[3]),type=str(p[2]),drill_mm=drill_mm,layers=layers,angle_rel_deg=round((pa-fa)%360,6))
            if drill and str(drill[1])=='oval':
                ps.update(drill_shape='oval',drill_width_mm=float(drill[2]),drill_height_mm=float(drill[3]))
            if drill and child(drill,'offset'):
                ps['drill_offset_mm']=list(map(float,child(drill,'offset')[1:3]))
            for src,dst in (('pintype','pin_type'),('pinfunction','pin_function'),('roundrect_rratio','roundrect_ratio')):
                attr=child(p,src)
                if attr: ps[dst]=attr[1]
            if str(p[3])=='custom':
                raise ValueError('custom pad needs explicit geometry support')
            outpads.append(ps)
            if netname: nets[netname].append([ref,str(p[1])])
        comp=dict(refdes=ref,value=value,footprint_name=str(fp[1]),pads=outpads)
        if body:
            comp['body_bbox_mm']=[min(p[0] for p in body),min(p[1] for p in body),max(p[0] for p in body),max(p[1] for p in body)]
        # Only board-edge mating connectors may retain mechanical interface pose.
        label=(str(fp[1])+' '+value).lower()
        library = str(fp[1]).split(':', 1)[0].lower()
        likely_interface = (
            library.startswith(('connector_', 'connectors_', 'terminalblock_')) or library in
            ('connector', 'connectors', 'terminalblock')
        ) if strict_intake else any(t in label for t in ('usb','rj45','jack','barrel','terminalblock','connectors'))
        if likely_interface:
            at=child(fp,'at',[None,0,0,0]); angle=float(at[3]) if len(at)>3 else 0
            bb=comp.get('body_bbox_mm')
            if outpads:
                pb=[min(p['x_rel_mm']-p['w_mm']/2 for p in outpads),min(p['y_rel_mm']-p['h_mm']/2 for p in outpads),max(p['x_rel_mm']+p['w_mm']/2 for p in outpads),max(p['y_rel_mm']+p['h_mm']/2 for p in outpads)]
                bb=[min(bb[0],pb[0]),min(bb[1],pb[1]),max(bb[2],pb[2]),max(bb[3],pb[3])] if bb else pb
            if bb:
                g=translate(rotate(box(*bb),-angle,origin=(0,0)),xoff=float(at[1]),yoff=float(at[2]))
                if g.distance(outline.boundary)<=1.0:
                    interfaces.append(dict(refdes=ref,x_mm=float(at[1]),y_mm=float(at[2]),angle_deg=angle,constraint_source='board-edge mating interface only',allow_body_overhang=True))
        components.append(comp)
    # Generic conventions identify requirement candidates, never placement or routes.
    pairs=[]
    for name in sorted(nets):
        for pos,neg in (('_P','_N'),('+','-'),('DP','DM'),('TXP','TXN'),('RXP','RXN')):
            if name.upper().endswith(pos):
                other=name[:-len(pos)]+neg
                match=next((n for n in nets if n.upper()==other.upper()),None)
                if match and not any(name in (p['pos_net'],p['neg_net']) for p in pairs):
                    pairs.append(dict(name=f'pair_{len(pairs)+1}',pos_net=name,neg_net=match,target_diff_impedance_ohm=90 if 'USB' in name.upper() else 100,max_skew_mm=0.2,inference='generic net suffix; impedance requires electrical review'))
    rails=[]
    byref={c['refdes']:c for c in components}
    for pair in pairs:
        frontier=[pair['pos_net'],pair['neg_net']]; seen=set(frontier); usb=False
        while frontier:
            n=frontier.pop()
            for ref,_ in nets[n]:
                c=byref[ref]
                if 'usb' in (c['footprint_name']+' '+c['value']).lower() and 'connector' in c['footprint_name'].lower(): usb=True
                if len(c['pads'])<=2:
                    for p in c['pads']:
                        nn=p.get('net')
                        if nn and nn not in seen: frontier.append(nn);seen.add(nn)
        if usb: pair['target_diff_impedance_ohm']=90
    for n in sorted(nets):
        u=n.upper()
        if u in ('GND','AGND','DGND','PGND','VSS') or u.startswith('+') or u in ('VCC','VDD','VBUS','VIN','VBAT'):
            rails.append(dict(net=n,nominal_v=0 if 'GND' in u or u=='VSS' else None,requirements_source='net naming convention; current unspecified'))
    spec=dict(project_name=f'holdout_case{case}',board_outline_mm=dict(width=x1-ox,height=y1-oy,origin_x=ox,origin_y=oy,corner_radius_mm=0,vertices_mm=[[round(x-ox,6),round(y-oy,6)] for x,y in outline.exterior.coords]),layer_count=4,mounting_holes=holes,edge_connectors=interfaces,components=sorted(components,key=lambda c:c['refdes']),nets=dict(sorted(nets.items())),design_requirements=dict(power_rails=rails,differential_pairs=pairs,high_speed_or_clock_nets=[]),input_limitations=['No datasheet current limits or verified body heights supplied','Only authorized board-edge mating interface poses retained; no other instance placement, traces, planes, reference annotations, or schematic coordinates retained'])
    return spec


def seal(gitdir, boardpath, case, root):
    sha=subprocess.check_output(['git',f'--git-dir={gitdir}','rev-parse','HEAD'],text=True).strip()
    raw=subprocess.check_output(['git',f'--git-dir={gitdir}','show',f'{sha}:{boardpath}'])
    spec=extract(raw,case)
    # Metamorphic anti-leak test mutates instance positions and removes all copper routes.
    ast=parse_sexp(raw.decode()); clean=[x for x in ast if not(isinstance(x,list) and x and x[0] in ('segment','via','arc','zone'))]
    def build_sexp(item):
        if isinstance(item,list):
            return '('+' '.join(build_sexp(v) for v in item)+')'
        return json.dumps(item) if isinstance(item,str) else str(item)
    for fp in children(clean,'footprint'):
        at=child(fp,'at')
        if at and prop(fp,'Reference') not in {e['refdes'] for e in spec['edge_connectors']} and not all(str(p[2])=='np_thru_hole' for p in children(fp,'pad')):
            at[1]+=101; at[2]-=73
            if len(at)<4: at.append(0)
            at[3]+=37
            for pad in children(fp,'pad'):
                pat=child(pad,'at')
                if pat:
                    if len(pat)<4: pat.append(0)
                    pat[3]+=37
    assert extract(build_sexp(clean).encode(),case)==spec,'placement/routing leaked into spec'
    sealed=root/'benchmark_case'/f'sealed_case{case}'; sealed.mkdir(parents=True,exist_ok=True)
    reference=sealed/'reference.kicad_pcb'
    if reference.exists():
        old=json.loads((sealed/'manifest.json').read_text())
        assert old['reference_sha256']==hashlib.sha256(raw).hexdigest()
    else:
        reference.write_bytes(raw)
    reference.chmod(0)
    specpath=root/'benchmark_case'/f'blind_spec_case{case}.json'
    spec.update(repo_url=subprocess.check_output(['git',f'--git-dir={gitdir}','config','--get','remote.origin.url'],text=True).strip(),commit_sha=sha)
    specpath.write_text(json.dumps(spec,indent=2)+'\n')
    manifest=dict(case=case,source_commit=sha,source_path=boardpath,repo_url=spec['repo_url'],sealed_at=datetime.now(timezone.utc).isoformat(),reference_sha256=hashlib.sha256(raw).hexdigest(),spec_sha256=hashlib.sha256(specpath.read_bytes()).hexdigest(),component_count=len(spec['components']),net_count=len(spec['nets']),differential_pair_count=len(spec['design_requirements']['differential_pairs']),layer_count=4,reference_path=str(reference.relative_to(root)),status='sealed',metamorphic_placement_route_test='passed')
    (sealed/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest))


if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--git-dir',type=Path,required=True); ap.add_argument('--board-path',required=True); ap.add_argument('--case',type=int,required=True)
    args=ap.parse_args()
    seal(args.git_dir,args.board_path,args.case,Path(__file__).resolve().parents[1])
