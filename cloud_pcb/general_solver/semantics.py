"""Physical envelopes and RF evidence, from blind footprint-local geometry only."""
from __future__ import annotations

import math
import re

from shapely.affinity import rotate, translate
from shapely.geometry import Polygon, Point, LineString, box

COPPER_LAYERS=('F.Cu','In1.Cu','In2.Cu','B.Cu')


def pad_bounds(comp):
    pads=comp.get('pads',[])
    if not pads: return [-1.,-1.,1.,1.]
    bounds=[local_pad_polygon(p).bounds for p in pads]
    return [min(b[0] for b in bounds),min(b[1] for b in bounds),max(b[2] for b in bounds),max(b[3] for b in bounds)]


def local_pad_polygon(p):
    w,h=float(p['w_mm']),float(p['h_mm']);kind=p.get('shape','rect')
    if kind=='circle': g=Point(0,0).buffer(w/2)
    elif kind=='oval':
        r=min(w,h)/2
        g=LineString([(-(w-h)/2,0),((w-h)/2,0)]).buffer(r) if w>=h else LineString([(0,-(h-w)/2),(0,(h-w)/2)]).buffer(r)
    elif kind=='roundrect':
        r=min(w,h)*float(p.get('roundrect_ratio',.25));r=min(r,min(w,h)/2-1e-6)
        g=box(-w/2+r,-h/2+r,w/2-r,h/2-r).buffer(r)
    else: g=box(-w/2,-h/2,w/2,h/2)
    return translate(rotate(g,-float(p.get('angle_rel_deg',0)),origin=(0,0)),xoff=float(p['x_rel_mm']),yoff=float(p['y_rel_mm']))


def component_envelope(comp):
    label=(comp.get('footprint_name','')+' '+comp.get('value','')).lower()
    bbox=comp.get('body_bbox_mm')
    source='blind footprint fabrication geometry' if bbox else 'pad envelope fallback'
    electrolytic=re.search(r'(?:cp|c)_elec_([0-9.]+)x([0-9.]+)',label)
    nominal_height=None
    if electrolytic:
        diameter,nominal_height=map(float,electrolytic.groups())
        if not bbox:
            pads=comp.get('pads',[]);cx=sum(float(p['x_rel_mm']) for p in pads)/max(len(pads),1);cy=sum(float(p['y_rel_mm']) for p in pads)/max(len(pads),1)
            bbox=[cx-diameter/2,cy-diameter/2,cx+diameter/2,cy+diameter/2];source='electrolytic diameter from footprint identity'
    if not bbox:
        m=re.search(r'(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)mm',label)
        if m:
            w,h=map(float,m.groups()); bbox=[-w/2,-h/2,w/2,h/2]; source='footprint dimension token'
        else: bbox=pad_bounds(comp)
    family=next((f for f in ('usb','qfn','lqfp','tqfp','soic','sot','pinheader','pinsocket','inductor','electrolytic','capacitor','resistor','module') if f in label),'generic')
    if electrolytic or ('capacitor' in label and 'radial' in label): family='electrolytic'
    heights={'usb':3.2,'qfn':1.0,'lqfp':1.6,'tqfp':1.6,'soic':1.75,'sot':1.2,'pinheader':8.5,'pinsocket':8.5,'inductor':4.0,'electrolytic':8.0,'capacitor':1.0,'resistor':0.6,'module':3.5,'generic':2.0}
    explicit_h=re.search(r'(?:^|_)h([0-9.]+)mm',label)
    if explicit_h:nominal_height=float(explicit_h.group(1))
    height=float(comp.get('height_mm',nominal_height if nominal_height is not None else heights[family]))
    if comp.get('side')=='bottom': bbox=[-bbox[2],bbox[1],-bbox[0],bbox[3]]
    return dict(bbox_mm=list(map(float,bbox)),height_mm=height,family=family,geometry_source=source,height_source='explicit blind requirement' if 'height_mm' in comp else 'nominal footprint height token' if nominal_height is not None else 'family estimate',verified=(('height_mm' in comp or nominal_height is not None) and source!='pad envelope fallback'))


def infer_rf(comp):
    """An antenna-like name alone is not sufficient to infer its orientation."""
    explicit=comp.get('antenna_keepout_mm')
    if explicit:
        return dict(local_bbox_mm=list(explicit),source='explicit footprint antenna constraint',confidence='explicit')
    label=(comp.get('footprint_name','')+' '+comp.get('value','')).lower()
    if not any(t in label for t in ('antenna','wroom','wireless','wifi','bluetooth')):
        return None
    body=comp.get('body_bbox_mm')
    if not body: return dict(confidence='unknown',reason='antenna candidate lacks body geometry or explicit keepout')
    p=pad_bounds(comp); b=list(map(float,body))
    gaps=[p[0]-b[0],p[1]-b[1],b[2]-p[2],b[3]-p[3]]
    side=max(range(4),key=lambda i:gaps[i])
    if gaps[side]<2.0: return dict(confidence='unknown',reason='no unique substantial unpadded overhang')
    # Only infer the physical unpadded lobe, not an invented universal RF margin.
    regions=[[b[0],b[1],p[0],b[3]],[b[0],b[1],b[2],p[1]],[p[2],b[1],b[2],b[3]],[b[0],p[3],b[2],b[3]]]
    return dict(local_bbox_mm=regions[side],source='unpadded fabrication overhang + antenna identity',confidence='inferred',side=side,clearance_verified=False)


def world_box(local, position):
    x,y,a=position
    return translate(rotate(box(*local),-a,origin=(0,0)),xoff=x,yoff=y)


def rf_keepouts(spec, placed):
    result=[]; unresolved=[]
    for c in spec['components']:
        rf=infer_rf(c)
        if not rf: continue
        if 'local_bbox_mm' not in rf:
            unresolved.append(dict(refdes=c['refdes'],**rf)); continue
        geom=world_box(rf['local_bbox_mm'],placed[c['refdes']])
        x0,y0,x1,y1=geom.bounds
        result.append(dict(refdes=c['refdes'],kind='rf',x_min=x0,y_min=y0,x_max=x1,y_max=y1,layers=list(COPPER_LAYERS),polygon_mm=list(geom.exterior.coords),evidence=rf))
    return result,unresolved


def mechanical_check(spec, placed):
    bodies=[]; hits=[]; unverified=[]
    for c in spec['components']:
        ref=c['refdes']; env=component_envelope(c)
        if ref not in placed:
            unverified.append(ref); continue
        if not env['verified']: unverified.append(ref)
        geom=world_box(env['bbox_mm'],placed[ref]); z0=float(c.get('standoff_mm',0))
        side=c.get('side','top'); z=(z0,z0+env['height_mm']) if side=='top' else (-1.6-z0-env['height_mm'],-1.6-z0)
        bodies.append((ref,geom,z))
    for i,(ref,geom,z) in enumerate(bodies):
        for other,og,oz in bodies[i+1:]:
            if min(z[1],oz[1])-max(z[0],oz[0])>1e-6 and geom.intersection(og).area>1e-6:
                hits.append(dict(kind='body_body',refdes=ref,other=other,overlap_mm2=geom.intersection(og).area))
        for j,mh in enumerate(spec.get('mounting_holes',[])):
            from shapely.geometry import Point
            o=spec['board_outline_mm']; x=float(mh['x_mm'])-float(o.get('origin_x',0)); y=float(mh['y_mm'])-float(o.get('origin_y',0))
            r=float(mh.get('fastener_radius_mm',float(mh['drill_mm'])/2+1))
            if geom.intersection(Point(x,y).buffer(r)).area>1e-6:
                hits.append(dict(kind='mounting_hole',refdes=ref,hole_index=j))
        for j,ko in enumerate(spec.get('mechanical_keepouts',[])):
            kz=ko.get('z_range_mm',[-100,100])
            if min(z[1],kz[1])>max(z[0],kz[0]) and geom.intersects(Polygon(ko['polygon_mm'])):
                hits.append(dict(kind='mechanical_keepout',refdes=ref,keepout_index=j))
    return dict(collision_count=len(hits),collisions=hits,unverified_envelopes=unverified,all_envelopes_verified=not unverified,passed=not hits and not unverified)
