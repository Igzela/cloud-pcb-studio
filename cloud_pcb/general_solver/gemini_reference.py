"""Isolated external placement reference protocol; never imports solver answers.

A generated placement is not a routed/verified reference PCB. API credentials
are read at runtime and never included in request artifacts or provenance.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.request

from cloud_pcb.general_solver.electrical import GROUND_NAMES, voltage_label

API_ROOT = 'https://generativelanguage.googleapis.com/v1beta/models/'
DEFAULT_MODEL = 'gemini-4-hydrogen'
PAD_FIELDS = ('num','x_rel_mm','y_rel_mm','w_mm','h_mm','shape','type','layers','angle_rel_deg','roundrect_ratio','drill_mm','drill_width_mm','drill_offset_x_mm','drill_offset_y_mm','pin_type','pin_function')


def _opaque_ids(values, prefix):
    ordered=sorted(values,key=lambda value:hashlib.sha256(str(value).encode()).hexdigest())
    return {value:f'{prefix}{i:04d}' for i,value in enumerate(ordered,1)}


def anonymous_spec(spec):
    refs=_opaque_ids((c['refdes'] for c in spec['components']),'C')
    if len(refs)!=len(spec['components']):raise ValueError('duplicate component identity')
    names=set(spec.get('nets',{}))
    names.update(p['net'] for c in spec['components'] for p in c['pads'] if p.get('net'))
    nets=_opaque_ids(names,'N')
    outline=spec['board_outline_mm'];ox=float(outline.get('origin_x',0));oy=float(outline.get('origin_y',0))
    board={k:copy.deepcopy(outline[k]) for k in ('width','height','corner_radius_mm','vertices_mm') if k in outline}
    components=[]
    for comp in spec['components']:
        entry={k:copy.deepcopy(comp[k]) for k in ('value','body_bbox_mm','height_mm','antenna_keepout_mm') if k in comp}
        entry['id']=refs[comp['refdes']]
        package=comp.get('footprint_name','').lower()
        entry['package_family']=next((word for word in ('qfn','lqfp','tqfp','soic','sot','pinheader','pinsocket','usb','capacitor','resistor','inductor','module') if word in package),'unspecified')
        entry['pads']=[]
        for pad in comp['pads']:
            data={k:copy.deepcopy(pad[k]) for k in PAD_FIELDS if k in pad}
            data['net_id']=nets[pad['net']] if pad.get('net') else None
            entry['pads'].append(data)
        components.append(entry)
    network={}
    for net,pins in spec.get('nets',{}).items():
        network[nets[net]]=[[refs[ref],str(pin)] for ref,pin in pins]
    electrical=spec.get('design_requirements',{})
    rails=[]
    for rail in electrical.get('power_rails',[]):
        item={k:rail[k] for k in ('nominal_v','max_current_a','current_a','min_trace_width_mm','temperature_rise_c','copper_oz') if k in rail}
        item['net_id']=nets[rail['net']];rails.append(item)
    # Preserve conventional electrical meaning without revealing original labels.
    net_semantics=[]
    for net,identity in nets.items():
        item=dict(net_id=identity);voltage=voltage_label(net)
        if net.upper() in GROUND_NAMES:item['role']='ground'
        elif voltage is not None:item['nominal_v']=voltage
        if len(item)>1:net_semantics.append(item)
    pairs=[]
    for pair in electrical.get('differential_pairs',[]):
        item={k:pair[k] for k in ('target_diff_impedance_ohm','max_skew_mm') if k in pair}
        item.update(pos_net_id=nets[pair['pos_net']],neg_net_id=nets[pair['neg_net']]);pairs.append(item)
    holes=[dict(x_mm=float(h['x_mm'])-ox,y_mm=float(h['y_mm'])-oy,**{k:h[k] for k in ('drill_mm','fastener_radius_mm') if k in h}) for h in spec.get('mounting_holes',[])]
    interfaces=[dict(id=refs[c['refdes']],x_mm=float(c['x_mm'])-ox,y_mm=float(c['y_mm'])-oy,angle_deg=float(c.get('angle_deg',0)),**{k:c[k] for k in ('allow_body_overhang',) if k in c}) for c in spec.get('edge_connectors',[])]
    result=dict(protocol='pcb-independent-placement-v1',layer_count=spec.get('layer_count',4),board_outline_mm=board,mounting_holes=holes,fixed_interfaces=interfaces,components=sorted(components,key=lambda c:c['id']),nets=network,electrical_requirements=dict(power_rails=rails,differential_pairs=pairs,net_semantics=net_semantics,high_speed_net_ids=[nets[n] for n in electrical.get('high_speed_or_clock_nets',[])]))
    mapping=dict(components={v:k for k,v in refs.items()},nets={v:k for k,v in nets.items()})
    return result,mapping


def reference_request(anonymous, *, max_output_tokens=32768, thinking_level='low'):
    if isinstance(max_output_tokens,bool) or not isinstance(max_output_tokens,int) or not 1<=max_output_tokens<=65536:
        raise ValueError('Hydrogen output budget must be 1..65536 combined thinking/output tokens')
    if thinking_level not in ('low','medium','high'):
        raise ValueError('unsupported thinking level')
    prompt='''You are an independent PCB placement engineer. Work ONLY from the supplied anonymous blind specification. No solver output, human board or source repository is provided. Return JSON only: {"placements":[{"id":"C0001","x_mm":1.0,"y_mm":1.0,"angle_deg":0,"side":"top"}],"design_notes":["brief final design decisions"]}. Include every component exactly once. Do not add/delete components or change the outline, mounting holes or fixed mating interfaces. Coordinates use the provided local board frame. Rotation uses dx=x*cos(a)+y*sin(a), dy=-x*sin(a)+y*cos(a). Keep package bodies/fasteners clear and copper at least 0.50 mm from the board edge; report impossible input constraints in design_notes instead of changing them. Organize repeated topology coherently, place shunt capacitors near their actual IC supply pins and preserve signal escape channels. Bottom placement is allowed only for two-terminal SMD passives. Fixed interfaces must retain their supplied positions and angles. Never invent missing current ratings or body heights. This response will be validated independently; do not claim DRC/LVS/Gerber or engineering acceptance. Provide concise final decisions, not internal reasoning.\nBLIND_INPUT_JSON:\n'''
    return dict(contents=[dict(parts=[dict(text=prompt+json.dumps(anonymous,separators=(',',':'),ensure_ascii=False))])],generationConfig=dict(responseMimeType='application/json',maxOutputTokens=max_output_tokens,thinkingConfig=dict(thinkingLevel=thinking_level.upper(),includeThoughts=False)))


def _redact(value,key):
    if isinstance(value,str):return value.replace(key,'[REDACTED]')
    if isinstance(value,list):return [_redact(v,key) for v in value]
    if isinstance(value,dict):return {k:_redact(v,key) for k,v in value.items()}
    return value


def call_gemini(request_body,key,*,model=DEFAULT_MODEL,timeout=120,attempts=3,transport=None,pause=time.sleep):
    if not re.fullmatch(r'[A-Za-z0-9._-]+',model):raise ValueError('model must be an API model ID, not a URL')
    if not key or not key.strip():raise ValueError('API key is required')
    if not 1<=attempts<=3:raise ValueError('transport retry budget must be 1..3')
    opener=transport or urllib.request.urlopen;events=[]
    for attempt in range(1,attempts+1):
        started=time.monotonic()
        req=urllib.request.Request(API_ROOT+model+':generateContent',data=json.dumps(request_body).encode(),headers={'Content-Type':'application/json','X-goog-api-key':key})
        try:
            with opener(req,timeout=timeout) as response:
                body=json.load(response);status=response.status
            events.append(dict(attempt=attempt,http_status=status,runtime_s=round(time.monotonic()-started,3)))
            # Do not store model thought parts or credential-bearing headers.
            candidates=[]
            for candidate in body.get('candidates',[]):
                text=''.join(p.get('text','') for p in candidate.get('content',{}).get('parts',[]) if not p.get('thought'))
                candidates.append(dict(text=text,finish_reason=candidate.get('finishReason'),continuation_token=candidate.get('continuationToken')))
            return _redact(dict(status='generated',requested_model=model,reported_model_version=body.get('modelVersion'),response_id=body.get('responseId'),usage=body.get('usageMetadata'),attempts=events,candidates=candidates),key)
        except urllib.error.HTTPError as exc:
            message=exc.read().decode(errors='replace')[:3000]
            events.append(dict(attempt=attempt,http_status=exc.code,error=message.replace(key,'[REDACTED]'),runtime_s=round(time.monotonic()-started,3)))
            retry=exc.code in (429,500,502,503,504)
        except (urllib.error.URLError,TimeoutError,OSError,ValueError) as exc:
            events.append(dict(attempt=attempt,error=str(exc).replace(key,'[REDACTED]'),runtime_s=round(time.monotonic()-started,3)));retry=True
        if not retry or attempt==attempts:break
        pause(min(2**attempt,8))
    return dict(status='unavailable',requested_model=model,reported_model_version=None,attempts=events,candidates=[])


def decode_placement(reply,anonymous,mapping):
    if reply.get('status')!='generated':raise ValueError('no model output exists')
    candidates=reply.get('candidates',[])
    if len(candidates)!=1 or candidates[0].get('finish_reason')!='STOP':raise ValueError('model response is not a complete single final answer')
    def reject_constant(value):raise ValueError('nonfinite JSON constant: '+value)
    data=json.loads(candidates[0]['text'],parse_constant=reject_constant)
    if not isinstance(data,dict) or not isinstance(data.get('placements'),list):raise ValueError('placements array required')
    expected={c['id'] for c in anonymous['components']};components={c['id']:c for c in anonymous['components']};poses={}
    for entry in data['placements']:
        identity=entry.get('id')
        if identity not in expected or identity in poses:raise ValueError('unknown or duplicate component ID')
        coords=[entry.get(k) for k in ('x_mm','y_mm','angle_deg')]
        if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in coords):raise ValueError('finite numeric pose required')
        side=entry.get('side')
        if side not in ('top','bottom'):raise ValueError('side must be top or bottom')
        pads=components[identity]['pads']
        if side=='bottom' and (len(pads)!=2 or any(p.get('type')!='smd' for p in pads)):raise ValueError('bottom placement restricted to two-terminal SMD passives')
        poses[identity]=dict(x_mm=coords[0],y_mm=coords[1],angle_deg=coords[2]%360,side=side)
    if set(poses)!=expected:raise ValueError('component coverage is incomplete')
    for interface in anonymous['fixed_interfaces']:
        pose=poses[interface['id']]
        delta=(pose['angle_deg']-interface['angle_deg']+180)%360-180
        if abs(pose['x_mm']-interface['x_mm'])>1e-6 or abs(pose['y_mm']-interface['y_mm'])>1e-6 or abs(delta)>1e-6 or pose['side']!='top':raise ValueError('fixed mating interface moved')
    return dict(status='placement_generated_unverified',placements={mapping['components'][identity]:pose for identity,pose in poses.items()},design_notes=data.get('design_notes',[]),routing_status='not_run',geometry_status='not_run',engineering_pass=None)
