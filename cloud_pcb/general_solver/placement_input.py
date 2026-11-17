"""Explicit external placement input, independent of any model or board identity."""
from __future__ import annotations

import hashlib
import json
import math

from cloud_pcb.general_solver.topology import analyze_topology


def apply_external_placement(spec,document):
    components={c['refdes']:c for c in spec['components']}
    poses=document.get('placements')
    if not isinstance(poses,dict) or set(poses)!=set(components):
        raise ValueError('external placement must cover every component exactly once')
    positions={};sides={}
    for ref,data in poses.items():
        if not isinstance(data,dict):raise ValueError('external pose must be an object')
        values=[data.get(k) for k in ('x_mm','y_mm','angle_deg')]
        if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in values):raise ValueError('external pose must contain finite numeric coordinates')
        side=data.get('side','top')
        if side not in ('top','bottom'):raise ValueError('unknown component side')
        pads=components[ref]['pads']
        if side=='bottom' and (len(pads)!=2 or any(p.get('type')!='smd' for p in pads)):raise ValueError('bottom placement restricted to two-terminal SMD components')
        positions[ref]=(float(values[0]),float(values[1]),float(values[2])%360);sides[ref]=side
    outline=spec['board_outline_mm']
    for interface in spec.get('edge_connectors',[]):
        ref=interface['refdes'];x,y,a=positions[ref]
        target=(float(interface['x_mm'])-float(outline.get('origin_x',0)),float(interface['y_mm'])-float(outline.get('origin_y',0)))
        if abs(x-target[0])>1e-6 or abs(y-target[1])>1e-6 or abs((a-float(interface.get('angle_deg',0))+180)%360-180)>1e-6 or sides[ref]!='top':raise ValueError('external placement moved a fixed mating interface')
    # Apply only after the entire document validates. No legalizer, coordinate
    # clamping, missing-component fill or replacement by internal placement.
    for ref,side in sides.items():components[ref]['side']=side
    metadata=dict(total_placed=len(positions),groups=dict(external=list(positions)),component_sides=sides,hierarchy=analyze_topology(spec),placement_source='external_input',placement_document_sha256=hashlib.sha256(json.dumps(document,sort_keys=True,allow_nan=False).encode()).hexdigest(),coordinates_repaired=False)
    return positions,metadata
