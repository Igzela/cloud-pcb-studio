"""Synthetic geometry and router probes; invoked from repository root with env loaded."""
from __future__ import annotations
from dataclasses import dataclass
import json
import math
import re
import shlex
import subprocess
from pathlib import Path
from shapely.geometry import LineString, MultiPoint, Point, Polygon, box
from shapely.affinity import rotate, translate
from cloud_pcb.layout_route.dsn_ses_bridge import parse_ses

ROOT = Path(__file__).resolve().parent
CLEARANCE_MM = .130


@dataclass(frozen=True)
class Pad:
    width: float
    height: float
    radius: float
    angle: float = 0.

    def core_points(self):
        a, b = self.width / 2 - self.radius, self.height / 2 - self.radius
        c, s = math.cos(math.radians(self.angle)), math.sin(math.radians(self.angle))
        return [(x*c-y*s, x*s+y*c) for x in (-a, a) for y in (-b, b)]

    def emitted(self, bias=.002, sides=32):
        disk_radius = (self.radius + bias) / math.cos(math.pi / sides)
        points = [
            (round(1000*(x+disk_radius*math.cos(a))), round(1000*(y+disk_radius*math.sin(a))))
            for x,y in self.core_points()
            for a in ((k+.5)*math.tau/sides for k in range(sides))
        ]
        out = MultiPoint(points).convex_hull
        assert out.is_valid and out.equals(out.convex_hull)
        # The actual convex pad support at each emitted polygon face, computed
        # analytically rather than reconstructing the producer's vertices.
        coord=list(out.exterior.coords)
        for (x1,y1),(x2,y2) in zip(coord,coord[1:]):
            dx,dy=x2-x1,y2-y1; norm=math.hypot(dx,dy)
            u,v=(-dy/norm,dx/norm)
            extent=(x1*u+y1*v)/1000
            if extent<0:
                extent,u,v = -extent,-u,-v
            actual=max(x*u+y*v for x,y in self.core_points())+self.radius
            assert extent>=actual+math.sqrt(2)/2000 - 1e-10, (extent,actual)
        return out

    def actual(self, pos):
        core=MultiPoint(self.core_points()).convex_hull
        return translate(core.buffer(self.radius,quad_segs=256),*pos)

    def polygon_expr(self,layer):
        coordinates=' '.join(f'{int(x)} {int(y)}' for x,y in self.emitted().exterior.coords)
        return f'(polygon {layer} 0 {coordinates})'

    def rectangular_expr(self,layer):
        hx,hy=round(self.width*500),round(self.height*500)
        return f'(rect {layer} {-hx} {-hy} {hx} {hy})'


def um(value):
    return str(round(1000*value))


def as_mm(poly):
    return Polygon([(x/1000,y/1000) for x,y in poly.exterior.coords])


def octagon(poly):
    x0,y0,x1,y1=poly.bounds
    ps=list(poly.exterior.coords)
    s0,s1=min(x+y for x,y in ps),max(x+y for x,y in ps)
    d0,d1=min(x-y for x,y in ps),max(x-y for x,y in ps)
    # Clip the axis-aligned bound by both diagonal coordinate intervals.
    from shapely.geometry import Polygon as Poly
    to_original=lambda s,d: ((s+d)/2,(s-d)/2)
    dbox=Poly([to_original(s,d) for s,d in [(s0,d0),(s0,d1),(s1,d1),(s1,d0)]])
    return box(x0,y0,x1,y1).intersection(dbox)


def dsn_header(name):
    return f'''(pcb {name}.dsn
  (parser (string_quote ") (space_in_quoted_tokens on) (host_cad "KiCad's Pcbnew") (host_version "8.0.0"))
  (resolution um 10) (unit um)
  (structure
    (layer F.Cu (type signal) (property (index 0)))
    (layer In1.Cu (type signal) (property (index 1)))
    (layer In2.Cu (type signal) (property (index 2)))
    (layer B.Cu (type signal) (property (index 3)))
    (boundary (rect pcb 0 -20000 24000 0))
    (via Via[0-3]_550:300_um)
'''


def board_dsn(name,pad_a,pad_b,centers,remote,*,precise,slot=None):
    pa,pb=centers; da,db=remote
    shapes=(pad_a.polygon_expr('F.Cu'),pad_b.polygon_expr('F.Cu')) if precise else (pad_a.rectangular_expr('F.Cu'),pad_b.rectangular_expr('F.Cu'))
    keepouts=''
    if slot is not None:
        shape,c=slot
        # Use a true closed keepout polygon, not a DSN pin or copper area.
        q=translate(shape.emitted(),1000*c[0],1000*c[1]); coords=' '.join(f'{round(x)} {round(y)}' for x,y in q.exterior.coords)
        keepouts='\n'.join(f'(keepout "" (polygon {layer} 0 {coords}))' for layer in ('F.Cu','In1.Cu','In2.Cu','B.Cu'))
    return dsn_header(name)+f'''
    {keepouts}
    (rule (width 160) (clearance 150) (clearance 130 (type smd_smd))))
  (placement
    (component IMG_A (place A {um(pa[0])} {um(pa[1])} front 0))
    (component IMG_B (place B {um(pb[0])} {um(pb[1])} front 0))
    (component IMG_R (place U {um(da[0])} {um(da[1])} front 0) (place V {um(db[0])} {um(db[1])} front 0)))
  (library
    (image IMG_A (pin PAD_A 1 0 0))
    (image IMG_B (pin PAD_B 1 0 0))
    (image IMG_R (pin PAD_R 1 0 0))
    (padstack PAD_A (shape {shapes[0]}) (attach off))
    (padstack PAD_B (shape {shapes[1]}) (attach off))
    (padstack PAD_R (shape (rect F.Cu -200 -200 200 200)) (attach off))
    (padstack Via[0-3]_550:300_um
      (shape (circle F.Cu 550)) (shape (circle In1.Cu 550))
      (shape (circle In2.Cu 550)) (shape (circle B.Cu 550)) (attach off)))
  (network
    (net A_NET (pins A-1 U-1))
    (net B_NET (pins B-1 V-1))
    (class PLAIN A_NET B_NET (circuit (use_layer F.Cu)) (rule (width 160) (clearance 150))))
  (wiring))
'''


def quantize(coords):
    return tuple(tuple(round(t*1000)/1000 for t in p) for p in coords)


def fixture(name,a,b,centers,remote,precise=True,slot=None):
    centers=quantize(centers)
    p1,p2=(a.actual(centers[0]),b.actual(centers[1]))
    q1,q2=(translate(as_mm(a.emitted()),*centers[0]),translate(as_mm(b.emitted()),*centers[1]))
    info={
        'physical_copper_gap_mm':p1.distance(p2),
        'emitted_polygon_gap_mm':q1.distance(q2),
        'predicted_45deg_envelope_gap_mm':octagon(q1).distance(octagon(q2)),
        'legacy_rect_gap_mm':box(-a.width/2,-a.height/2,a.width/2,a.height/2).buffer(0).distance(box(centers[1][0]-centers[0][0]-b.width/2,centers[1][1]-centers[0][1]-b.height/2,centers[1][0]-centers[0][0]+b.width/2,centers[1][1]-centers[0][1]+b.height/2)),
        'tested_threshold_mm':CLEARANCE_MM,
    }
    assert q1.covers(p1) and q2.covers(p2)
    out=ROOT/f'{name}.dsn'; out.write_text(board_dsn(name,a,b,centers,remote,precise=precise,slot=slot))
    return info


def execute(name, engine):
    out=ROOT/f'{name}-{engine}'
    common=['-de',str(ROOT/f'{name}.dsn'),'-do',str(out.with_suffix('.ses')),
            '--router.autorouter.max_passes=1','--router.fanout.enabled=false','--router.optimizer.enabled=false']
    if engine=='fast':
        cmd=['fastroute',*common,f'--report={out.with_suffix(".json")}','--max-time=3','--multi-start=1']
    else:
        java=Path('.run/tools/freerouting/jdk-25.0.5+8-jre/bin/java')
        jar=Path('.run/tools/freerouting/freerouting-2.5.0.jar')
        cmd=[str(java),'-jar',str(jar),*common,'--gui.enabled=false']
    if not out.with_suffix('.ses').exists() or not out.with_suffix('.log').exists() or (engine=='fast' and not out.with_suffix('.json').exists()):
        with out.with_suffix('.log').open('w') as log:
            subprocess.run(['bash','-c','source scripts/env.sh && exec '+shlex.join(cmd)],stdout=log,stderr=subprocess.STDOUT,timeout=12,check=True)
    s=parse_ses(out.with_suffix('.ses'))
    log=out.with_suffix('.log').read_text()
    if engine=='fast':
        d=json.loads(out.with_suffix('.json').read_text()); stats=d['stats']; violations=d['clearance_violations']
        pin_pairs=[v for v in violations if all(v[k].get('kind')=='pin' for k in ('first','second'))]
        count={'unrouted':stats['unrouted'],'raw_violations':stats['violations'],'pin_pairs':pin_pairs}
    else:
        assert 'state: COMPLETED' in log, log
        m=re.search(r'final score:[^\r\n]*?\((\d+) unrouted and (\d+) violations?\)',log)
        assert m, log
        count={'unrouted':int(m.group(1)),'raw_violations':int(m.group(2))}
    count.update(routed_net_names=s['routed_net_names'],segments=len(s['segments']),vias=s['via_count'])
    return count,s


def main():
    rr=Pad(.72,.42,.105); oval=Pad(.72,.42,.21)
    position=((8.,-8.),(8.732,-7.568))
    remote=((3.,-8.),(14.,-7.568))
    bad=((8.,-8.),(8.7,-7.6))
    tilt=Pad(1.8,.35,.175,30.)
    centers=((8.,-8.),(7.75,-8.+.5*math.sin(math.pi/3)))
    far=tuple((x+sign*4*(-.5),y+sign*4*math.sin(math.pi/3)) for sign,(x,y) in zip((-1,1),centers))
    slot=Pad(6.0,2.0,1.0,29.)
    rows={
        'axis_box':fixture('axis_box',rr,oval,position,remote,False),
        'axis_polygon':fixture('axis_polygon',rr,oval,position,remote),
        'true_violation':fixture('true_violation',rr,oval,bad,remote),
        'rotated_polygon':fixture('rotated_polygon',tilt,tilt,centers,far),
        'milled_slot':fixture('milled_slot',rr,oval,((2,-8),(2,-14)),((20,-8),(20,-14)),slot=(slot,(11,-8))),
    }
    for name,row in rows.items():
        for engine in ('fast','jvm'):
            row[engine],routed=execute(name,engine)
            if name=='milled_slot':
                drill=slot.actual((11,-8))
                spaces=[]
                for s in routed['segments']:
                    l=LineString(((s['x1'],-s['y1']),(s['x2'],-s['y2'])))
                    spaces.append(l.buffer(s['width_mm']/2).distance(drill))
                assert spaces
                assert min(spaces)>.12, (name,engine,min(spaces))
                row[engine]['min_routed_trace_copper_to_milled_slot_mm']=min(spaces)
    (ROOT/'results.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps(rows,indent=2))

if __name__=='__main__': main()
