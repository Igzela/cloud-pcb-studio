"""Scratch prototype for admission of physical, pair-atomic launch reservations.

The proposed stubs are only candidates. A release integration must route from
them and independently accept the resulting final copper before replacing the
ordinary solver result.
"""
from collections import Counter
from dataclasses import dataclass
from math import ceil, hypot, isfinite

from shapely.errors import GEOSException
from shapely.geometry import LineString, Point, box

from cloud_pcb.general_solver.direct_repair import (
    COPPER_LAYERS, _Obstacle, _board_obstacles, _copper_layers, _pad_dsn_center,
)
from cloud_pcb.layout_route.dsn_ses_bridge import _pad_world_dims, _quote


@dataclass(frozen=True)
class Launch:
    net: str
    layer: str
    neck_um: int
    trunk_um: int
    pad_xy_um: tuple[int, int]
    knee_xy_um: tuple[int, int]
    exit_xy_um: tuple[int, int]
    anchor_xy_um: tuple[int, int]


@dataclass(frozen=True)
class PairedLaunch:
    pair: tuple[str, str]
    footprint: str
    pins: tuple[str, str]
    leads: tuple[Launch, Launch]
    local_neck_length_mm: float
    trace_gap_mm: float


def _coord(a, u, du):
    return (a[0]+u[0]*du, a[1]+u[1]*du)


def _dot(a,b):
    return a[0]*b[0]+a[1]*b[1]


def _add_dsn_pad_shapes(board, obstacles):
    """Include quantized router pad envelopes and actual copper geometry."""
    for fp in board.footprints:
        for p in fp.pads:
            if p.type == 'np_thru_hole':
                continue
            layers = COPPER_LAYERS if p.type == 'thru_hole' else _copper_layers(p.layers)
            if not layers:
                continue
            x, y = (n / 1000 for n in _pad_dsn_center(fp,p))
            w, h = _pad_world_dims(p.size.X,p.size.Y,p.position.angle or 0.)
            w, h = max(100, round(w*1000)), max(100, round(h*1000))
            net = p.net.name if p.net else None
            if p.shape == 'circle':
                obstacle = _Obstacle(Point(x,y), net, w/2000)
            else:
                hw, hh = w//2/1000, h//2/1000
                obstacle = _Obstacle(box(x-hw,y-hh,x+hw,y+hh), net)
            for layer in layers:
                obstacles[layer].append(obstacle)


def _mm(xy):
    return tuple(n/1000 for n in xy)


def _copper(launch):
    neck = LineString(list(map(_mm,(launch.pad_xy_um, launch.knee_xy_um, launch.exit_xy_um))))
    trunk = LineString(list(map(_mm,(launch.exit_xy_um, launch.anchor_xy_um))))
    return neck.buffer(launch.neck_um/2000,join_style='mitre').union(trunk.buffer(launch.trunk_um/2000))


def _legal(copper, net, layer, outline, obstacles, clearance):
    if not outline.covers(copper) or outline.boundary.distance(copper) < .501:
        return False
    return all(
        copper.distance(item.geometry) >= item.extra_radius_mm +
        max(clearance(net),clearance(item.net)) + .002
        for item in obstacles[layer] if item.net != net
    )


def _common_axis(a,b,body):
    dx,dy=b[0]-a[0],b[1]-a[1]
    if bool(dx)==bool(dy):
        return None
    pitch=abs(dx or dy)
    side=((dx>0)-(dx<0),(dy>0)-(dy<0))
    out=(side[1],-side[0])
    fwd = _dot((a[0]+b[0]-2*body[0],a[1]+b[1]-2*body[1]), out)
    if fwd==0:
        return None
    return (pitch,side,out if fwd>0 else (-out[0],-out[1]))


def propose_pair_launches(
    board, outline, keepouts, netclasses, assignments, routing_layers,
    requirements, *, permitted_neck_mm, neck_floor_mm, max_neck_length_mm,
    no_neckdown_classes=(), max_groups=4, max_leads=10,
):
    """Return bounded paired plans for unique, adjacent terminal bundles.

    The caller supplies the existing neck allowance and its length limit. This
    prototype does not create vias, infer electrical rules, reserve general
    single-ended nets, or modify the board or its design requirements.
    """
    try:
        if max_groups<1 or max_leads<1 or not outline.is_valid:
            return ()
        if any(not isfinite(x) or x<=0 for x in (permitted_neck_mm,neck_floor_mm,max_neck_length_mm)):
            return ()
        neck = round(1000*permitted_neck_mm)
        if neck<ceil(1000*neck_floor_mm):
            return ()
        pads,obstacles=_board_obstacles(board,keepouts)
        _add_dsn_pad_shapes(board,obstacles)
        for rule in netclasses.values():
            if not isfinite(rule.clearance_mm) or rule.clearance_mm<=0:
                return ()
    except (AttributeError,GEOSException,TypeError,ValueError,KeyError):
        return ()

    def clearance(net):
        rule = netclasses.get(assignments.get(net,'DEFAULT'))
        return max(.15, rule.clearance_mm) if rule else .15

    result=[]
    requirements=tuple(req for req in requirements if isinstance(req,dict))
    pair_members=Counter(
        name for req in requirements for key in ('pos_net','neg_net')
        if isinstance(name:=req.get(key),str) and name
    )
    for req in requirements:
        pos,neg=req.get('pos_net'),req.get('neg_net')
        if any(
            not isinstance(name,str) or pair_members[name]!=1
            for name in (pos,neg)
        ):
            continue
        skew=req.get('max_skew_mm',.20)
        if not isinstance(skew,(int,float)) or isinstance(skew,bool) or not isfinite(skew) or skew<=0:
            continue
        cls=assignments.get(pos)
        rule=netclasses.get(cls)
        if (cls in no_neckdown_classes or assignments.get(neg)!=cls or not
            rule or rule.diff_pair_gap_mm is None or rule.impedance_ohm is None):
            continue
        if any(not isfinite(x) or x<=0
            for x in (rule.diff_pair_gap_mm,rule.trace_width_mm,rule.impedance_ohm)):
            continue
        trunk=round(rule.trace_width_mm*1000)
        target_gap=ceil(rule.diff_pair_gap_mm*1000)
        if not 0<neck<trunk:
            continue
        start_index=len(result)
        retained_sizes={layer:len(values) for layer,values in obstacles.items()}
        supported=True
        for ref in sorted({k[0] for k in pads}):
            if any(len(match)!=1 and any(p[2] in (pos,neg) for p in match)
                   for (name,_),match in pads.items() if name==ref):
                supported=False
                break
            bundle=[[ (label,match[0]) for (name,label),match in pads.items()
                      if name==ref and len(match)==1 and match[0][2]==net ]
                      for net in (pos,neg)]
            if not all(bundle):
                continue
            if any(len(choices)!=1 for choices in bundle):
                supported=False
                break
            (la,(fa,pa,_,layers_a)),(lb,(fb,pb,_,layers_b))=(p[0] for p in bundle)
            # A through-hole endpoint does not need a narrow SMD launch.
            if pa.type==pb.type=='thru_hole':
                continue
            if (fa is not fb or pa.type!='smd' or pb.type!='smd' or pa.drill or pb.drill or
                len(layers_a)!=1 or layers_a!=layers_b or
                layers_a[0] not in routing_layers.get(cls,()) or
                (not rule.zero_via_allowed and layers_a!=('F.Cu',))):
                supported=False
                break
            layer=layers_a[0]
            a,b=(_pad_dsn_center(fa,pa),_pad_dsn_center(fb,pb))
            body=(round(fa.position.X*1000),round(fa.position.Y*1000))
            axes=_common_axis(a,b,body)
            if not axes:
                supported=False
                break
            pitch,side,out=axes
            # Regions narrower than the present local neck and requested gap
            # are not repaired by changing the rule or opening a whole net.
            if pitch>=trunk+target_gap:
                continue
            if pitch<neck+target_gap or len(result)>=max_groups:
                supported=False
                break
            dims=[_pad_world_dims(p.size.X,p.size.Y,p.position.angle or 0.) for p in (pa,pb)]
            axis=0 if out[0] else 1
            if any(d[axis]<=d[1-axis] for d in dims):
                supported=False
                break
            if any(
                obstacle.geometry.distance(LineString([_mm(a),_mm(b)]))<.001 and
                obstacle.net not in (pos,neg)
                for obstacle in obstacles[layer]
            ):
                supported=False
                break
            # Diverge in equal dimensions on integer µm coordinates, providing
            # a small outward margin over the nominal pair gap.
            spread=ceil((trunk+target_gap+4-pitch)/2)
            start=ceil(max(d[axis] for d in dims)*500+max(clearance(pos),clearance(neg))*1000+neck/2)
            increment=max(50,round(pitch/8))
            admitted=False
            for k in range(max_leads):
                lead=start+increment*k
                neck_length=(lead+hypot(spread,spread))/1000
                if neck_length>max_neck_length_mm:
                    break
                launches=[]
                for polarity,net,point in zip((-1,1),(pos,neg),(a,b)):
                    knee=_coord(point,out,lead)
                    end=_coord(_coord(knee,out,spread),side,polarity*spread)
                    anchor=_coord(end,out,2*trunk)
                    launches.append(Launch(net,layer,neck,trunk,point,knee,end,anchor))
                shapes=tuple(map(_copper,launches))
                gap=shapes[0].distance(shapes[1])
                if gap<rule.diff_pair_gap_mm+.001 or not all(
                    _legal(cu,l.net,l.layer,outline,obstacles,clearance)
                    for l,cu in zip(launches,shapes)
                ):
                    continue
                result.append(PairedLaunch((pos,neg),ref,(la,lb),tuple(launches),neck_length,gap))
                for l,cu in zip(launches,shapes):
                    obstacles[layer].append(_Obstacle(cu,l.net))
                admitted=True
                break
            if not admitted:
                supported=False
                break
        if not supported:
            del result[start_index:]
            for layer,retained in retained_sizes.items():
                del obstacles[layer][retained:]
    return tuple(result)


def render_unchanged_design_with_fixed_launches(dsn_text, plans):
    """Encode atomic plans in the normal empty-wiring DSN from our exporter."""
    marker='  (wiring\n  )'
    if not plans or dsn_text.count(marker)!=1:
        raise ValueError('nonempty plans and a single empty DSN wiring scope required')
    rows=[]
    for plan in plans:
        if len(plan.leads)!=2 or tuple(l.net for l in plan.leads)!=plan.pair:
            raise ValueError('incomplete differential launch pair')
        for l in plan.leads:
            for width,vertices in (
                (l.neck_um,(l.pad_xy_um,l.knee_xy_um,l.exit_xy_um)),
                (l.trunk_um,(l.exit_xy_um,l.anchor_xy_um)),
            ):
                coords=' '.join(f'{x} {-y}' for x,y in vertices)
                rows.append(f'    (wire (path {l.layer} {width} {coords}) (net {_quote(l.net)}) (type fix))')
    return dsn_text.replace(marker,'  (wiring\n'+'\n'.join(rows)+'\n  )')
