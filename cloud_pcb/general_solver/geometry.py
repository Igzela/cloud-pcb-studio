"""Shared physical length conventions for copper arcs and plated barrels."""
from __future__ import annotations
import math


def arc_parameters(arc):
    a=(float(arc.start.X),float(arc.start.Y));m=(float(arc.mid.X),float(arc.mid.Y));b=(float(arc.end.X),float(arc.end.Y))
    ax,ay=a;mx,my=m;bx,by=b
    d=2*(ax*(my-by)+mx*(by-ay)+bx*(ay-my))
    if abs(d)<1e-12: return None
    u=((ax*ax+ay*ay)*(my-by)+(mx*mx+my*my)*(by-ay)+(bx*bx+by*by)*(ay-my))/d
    v=((ax*ax+ay*ay)*(bx-mx)+(mx*mx+my*my)*(ax-bx)+(bx*bx+by*by)*(mx-ax))/d
    aa,ma,ba=[math.atan2(y-v,x-u) for x,y in (a,m,b)]
    sweep=(ba-aa)%(2*math.pi)
    if (ma-aa)%(2*math.pi)>sweep: sweep-=2*math.pi
    return u,v,math.hypot(ax-u,ay-v),aa,sweep


def arc_length_mm(arc):
    p=arc_parameters(arc)
    if p: return p[2]*abs(p[4])
    return math.hypot(arc.start.X-arc.mid.X,arc.start.Y-arc.mid.Y)+math.hypot(arc.end.X-arc.mid.X,arc.end.Y-arc.mid.Y)


def copper_arc_points(arc,tolerance_mm=.0005):
    p=arc_parameters(arc)
    if not p: return [(arc.start.X,arc.start.Y),(arc.mid.X,arc.mid.Y),(arc.end.X,arc.end.Y)]
    u,v,r,start,sweep=p
    step=2*math.acos(max(-1,min(1,1-tolerance_mm/max(r,1e-12))))
    n=max(2,math.ceil(abs(sweep)/max(step,1e-5)))
    pts=[(u+r*math.cos(start+sweep*i/n),v+r*math.sin(start+sweep*i/n)) for i in range(n+1)]
    pts[0]=(arc.start.X,arc.start.Y);pts[-1]=(arc.end.X,arc.end.Y)
    return pts


def via_barrel_length_mm(board,via):
    centers={};depth=0
    for layer in getattr(board.setup.stackup,'layers',[]) if board.setup.stackup else []:
        thickness=float(layer.thickness or 0)
        if layer.name.endswith('.Cu'): centers[layer.name]=depth+thickness/2
        depth+=thickness
    if not all(l in centers for l in via.layers):
        layers=[l.name for l in board.layers if l.name.endswith('.Cu')]
        centers={l:float(board.general.thickness)*i/max(len(layers)-1,1) for i,l in enumerate(layers)}
    return abs(centers.get(via.layers[-1],float(board.general.thickness))-centers.get(via.layers[0],0))


def drill_geometry(fp,pad):
    from shapely.geometry import Point,LineString
    def rot(x,y,angle):
        t=math.radians(angle);return x*math.cos(t)+y*math.sin(t),-x*math.sin(t)+y*math.cos(t)
    dx,dy=rot(float(pad.position.X),float(pad.position.Y),float(fp.position.angle or 0))
    x,y=float(fp.position.X)+dx,float(fp.position.Y)+dy
    d=pad.drill;angle=float(pad.position.angle or 0)
    if d.offset:
        dx,dy=rot(float(d.offset.X),float(d.offset.Y),angle);x+=dx;y+=dy
    w=float(d.diameter);h=float(d.width or w) if d.oval else w
    if abs(w-h)<1e-10: return Point(x,y).buffer(w/2,quad_segs=32)
    dx,dy=rot((w-h)/2 if w>h else 0,(h-w)/2 if h>w else 0,angle)
    return LineString([(x-dx,y-dy),(x+dx,y+dy)]).buffer(min(w,h)/2,quad_segs=32)
