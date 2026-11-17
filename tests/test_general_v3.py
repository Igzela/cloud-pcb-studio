"""Independent negative controls for blind topology, electrical intent and assembly."""
from copy import deepcopy
from dataclasses import replace

from kiutils.board import Board
from kiutils.footprint import Footprint,Pad
from kiutils.items.brditems import Segment,Via
from kiutils.items.common import Position,Net
from shapely.geometry import box

from cloud_pcb.general_solver.topology import analyze_topology
from cloud_pcb.general_solver.semantics import infer_rf,mechanical_check,rf_keepouts
from cloud_pcb.general_solver.power import specialize_power_rules,power_islands
from cloud_pcb.general_solver.assembly import export_assembly
from cloud_pcb.general_solver.run_blind import _build_blind_kicad_board
from cloud_pcb.verify_gate.gerber_drc import audit_general_board
from cloud_pcb.layout_route.dsn_ses_bridge import board_to_dsn
from contracts.schemas import NetClassRule


def component(ref,fp='Package_QFN:QFN-8_3x3mm',pads=8):
    return dict(refdes=ref,value='same',footprint_name=fp,height_mm=1,body_bbox_mm=[-1,-1,1,1],pads=[dict(num=str(i),net='',x_rel_mm=float(i%4),y_rel_mm=float(i//4)*3,w_mm=.3,h_mm=.6,type='smd',shape='rect',layers=['F.Cu']) for i in range(pads)])


def spec(comps,nets=None):
    return dict(project_name='synthetic',repo_url='',commit_sha='',board_outline_mm=dict(width=40,height=30),components=comps,nets=nets or {},design_requirements=dict(power_rails=[dict(net='rail',current_a=1.0)],differential_pairs=[]),mounting_holes=[],edge_connectors=[])


def test_topology_detects_exact_repeated_channels_without_ground_merge():
    comps=[component('a'),component('b'),component('x','Resistor',2),component('y','Resistor',2)]
    s=spec(comps,{'one':[['a','1'],['x','1']],'two':[['b','1'],['y','1']],'rail':[[r,'2'] for r in ('a','b','x','y')]})
    h=analyze_topology(s)
    assert len(h['communities'])==2
    assert any(set(g['anchors'])=={'a','b'} for g in h['repeated_channels'])
    s['components'][1]['value']='different'
    assert not analyze_topology(s)['repeated_channels']


def test_topology_rename_invariance():
    s=spec([component('a'),component('b')],{'sig':[['a','1'],['b','1']]})
    a=analyze_topology(s)
    s['components'][0]['refdes']='foo';s['components'][1]['refdes']='bar';s['nets']={'random':[['foo','1'],['bar','1']]}
    b=analyze_topology(s)
    assert [len(g) for g in a['communities']]==[len(g) for g in b['communities']]
    assert len(a['repeated_channels'])==len(b['repeated_channels'])


def test_one_fine_pitch_part_does_not_collapse_power_trunks():
    s=spec([component('fine')]);s['components'][0]['pads'][0]['net']='rail'
    rule=NetClassRule('POWER',.25,.15,.3,.55,2)
    cs,mp,intent=specialize_power_rules(s,{'POWER':rule,'DEFAULT':replace(rule,name='DEFAULT')},{'rail':'POWER'})
    assert cs[mp['rail']].trace_width_mm>=.5
    assert intent['rail']['local_neckdown']
    assert rule.trace_width_mm==.25


def test_dsn_overrides_compile_to_exclusive_classes(tmp_path):
    s=spec([component('a'),component('b')],{'rail':[['a','0'],['b','0']]})
    for c in s['components']: c['pads'][0]['net']='rail'
    board,classes,mapping,*_=_build_blind_kicad_board(s,{'a':(5,5,0),'b':(20,5,0)})
    p=board_to_dsn(board,tmp_path/'x.dsn',classes,mapping,net_rules={'rail':replace(classes['POWER'],trace_width_mm=.7)})
    text=p.read_text(); assert '(class NET_RULE_0000 rail' in text and '(width 700)' in text
    assert '(rule (width 700)' in text


def test_rf_requires_geometry_evidence_and_rotates_four_layers():
    c=component('radio','RF_Module:wireless_antenna',2)
    del c['body_bbox_mm']
    assert infer_rf(c)['confidence']=='unknown'
    c['antenna_keepout_mm']=[-2,-6,2,-3]
    ko,unknown=rf_keepouts(spec([c]),{'radio':(10,10,90)})
    assert not unknown and set(ko[0]['layers'])=={'F.Cu','In1.Cu','In2.Cu','B.Cu'}
    assert abs((ko[0]['x_max']-ko[0]['x_min'])-3)<1e-6


def test_mechanical_detects_rotated_bodies_holes_and_unknown_heights():
    s=spec([component('a'),component('b')]);p={'a':(10,10,37),'b':(10,10,0)}
    assert mechanical_check(s,p)['collision_count']==1
    p['b']=(20,20,0);assert mechanical_check(s,p)['passed']
    s['mounting_holes']=[dict(x_mm=10,y_mm=10,drill_mm=2)]
    assert mechanical_check(s,p)['collision_count']==1
    s['mounting_holes']=[];del s['components'][0]['height_mm']
    assert not mechanical_check(s,p)['passed']


def crossing_board():
    b=Board.create_new();b.nets=[Net(0,''),Net(1,'signal')]
    for i,(x,y) in enumerate(((0,0),(4,0),(2,-2),(2,2))):
        f=Footprint();f.entryName='test';f.position=Position(X=x,Y=y);f.properties={'Reference':f'p{i}'}
        p=Pad();p.number='1';p.type='smd';p.shape='circle';p.position=Position(X=0,Y=0);p.size=Position(X=.5,Y=.5);p.net=Net(1,'signal');p.layers=['F.Cu' if i<2 else 'B.Cu'];f.pads=[p];b.footprints.append(f)
    b.traceItems=[Segment(start=Position(X=0,Y=0),end=Position(X=4,Y=0),width=.2,layer='F.Cu',net=1),Segment(start=Position(X=2,Y=-2),end=Position(X=2,Y=2),width=.2,layer='B.Cu',net=1)]
    return b


def test_layered_lvs_rejects_projected_crossing_and_requires_plating():
    b=crossing_board();s=spec([])
    a=audit_general_board(b,s,{},[]);assert len(a['lvs_opens'])==1
    b.traceItems.append(Via(position=Position(X=2,Y=0),size=.6,drill=.3,layers=['F.Cu','B.Cu'],net=1))
    assert not audit_general_board(b,s,{},[])['lvs_opens']


def test_rf_audit_catches_inner_layer_copper():
    b=crossing_board();b.traceItems[0].layer='In2.Cu'
    a=audit_general_board(b,spec([]),{},[dict(kind='rf',x_min=1,y_min=-1,x_max=3,y_max=1)])
    assert len(a['rf_keepout'])==4
    assert not next(x for x in a['rf_keepout'] if x['layer']=='In2.Cu')['passed']


def test_power_plane_rejects_unplated_projection():
    b=crossing_board();s=spec([]);s['design_requirements']['power_rails']=[dict(net='signal',current_a=2)]
    assert power_islands(b,box(-5,-5,10,10),s,[])==[]
    b.traceItems.extend([Via(position=Position(X=x,Y=0),size=.6,drill=.3,layers=['F.Cu','B.Cu'],net=1) for x in (0,4)])
    assert power_islands(b,box(-5,-5,10,10),s,[])==[] # reference corridor divides the island
    b.traceItems[1].layer='F.Cu'
    assert power_islands(b,box(-5,-5,10,10),s,[])


def test_assembly_is_self_contained_and_has_real_height(tmp_path):
    s=spec([component('a')]);p={'a':(10,10,90)}
    b,*_=_build_blind_kicad_board(s,p)
    result=export_assembly(b,s,p,tmp_path)
    b.to_file(str(tmp_path/'with_envelopes.kicad_pcb'))
    loaded=Board.from_file(str(tmp_path/'with_envelopes.kicad_pcb'))
    assert 'height_mm=1.0000' in loaded.footprints[0].properties['MCAD_Envelope']
    import json
    scene=json.loads((tmp_path/'assembly_scene.json').read_text())
    assert scene['components'][0]['height_mm']==1
    assert result['component_models']==1
    text=(tmp_path/'assembly.html').read_text()
    assert '<script src=' not in text and 'scene-data' in text


def test_zone_partition_preserves_connected_copper_without_micron_slots():
    from shapely.geometry import Point
    from shapely.ops import unary_union
    from cloud_pcb.general_solver.run_blind import _slit_polygon_no_holes
    original=box(0,0,40,20).difference(unary_union([Point(x,10).buffer(.5) for x in range(2,39,2)]))
    pieces=_slit_polygon_no_holes(original)
    assert pieces and all(not p.interiors for p in pieces)
    rebuilt=unary_union(pieces)
    assert rebuilt.geom_type=='Polygon'
    assert rebuilt.symmetric_difference(original).area<1e-8


def test_import_omitted_angle_is_absolute_zero_not_footprint_angle():
    from scripts.seal_holdout import extract
    raw=b'''(kicad_pcb (layers (0 "F.Cu" signal)(1 "In1.Cu" power)(2 "In2.Cu" power)(31 "B.Cu" signal))
    (net 1 "sig")(gr_rect (start 0 0)(end 40 30)(layer "Edge.Cuts"))
    (footprint "Package_QFN:QFN" (at 10 10 90)(property "Reference" "part")(property "Value" "chip")
    (pad "1" smd rect (at 2 0)(size 0.2 0.9)(layers "F.Cu")(net 1 "sig"))))'''
    result=extract(raw,9)
    assert result['components'][0]['pads'][0]['angle_rel_deg']==270


def test_same_net_overlap_filter_keeps_foreign_net_and_missing_records():
    from cloud_pcb.verify_gate.gerber_drc import classify_router_violations
    c=component('connector','connector',2)
    for p in c['pads']: p['net']='ground';p['x_rel_mm']=0
    v=dict(first=dict(kind='pin',component='connector',pin='0'),second=dict(kind='pin',component='connector',pin='1'))
    report=dict(stats=dict(violations=2),clearance_violations=[v])
    assert classify_router_violations(report,spec([c]))['real_count']==1 # unexplained record stays real
    c['pads'][1]['net']='supply'
    assert classify_router_violations(report,spec([c]))['real_count']==2


def test_power_island_is_not_pierced_by_added_ground_stitching():
    from cloud_pcb.general_solver.run_blind import _build_4layer_pours_and_stitching
    from shapely.geometry import Point
    b=Board.create_new();b.nets=[Net(0,''),Net(1,'GND'),Net(2,'power')]
    island=dict(net='power',layer='In2.Cu',geometry=box(10,10,24,24),plated_anchor_count=2)
    _build_4layer_pours_and_stitching(b,box(0,0,40,40),islands=[island])
    assert all(not island['geometry'].intersects(Point(v.position.X,v.position.Y).buffer(v.size/2)) for v in b.traceItems if isinstance(v,Via))


def test_inner_signal_is_cut_out_of_inner_ground_fill():
    from cloud_pcb.general_solver.run_blind import _build_4layer_pours_and_stitching
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    b=Board.create_new();b.nets=[Net(0,''),Net(1,'GND'),Net(2,'signal')]
    t=Segment(start=Position(X=5,Y=5),end=Position(X=20,Y=5),width=.3,layer='In2.Cu',net=2);b.traceItems=[t]
    _build_4layer_pours_and_stitching(b,box(0,0,30,30))
    ground=unary_union([Polygon([(p.X,p.Y) for p in f.coordinates]) for z in b.zones if z.net==1 for f in z.filledPolygons if f.layer=='In2.Cu'])
    from shapely.geometry import LineString
    assert ground.distance(LineString([(5,5),(20,5)]))>=.3/2+.22-1e-6


def test_annular_geometry_rejects_missing_copper_and_checks_slots():
    from gerbonara.apertures import ExcellonTool
    from gerbonara.excellon import ExcellonFile
    from gerbonara.graphic_objects import Line
    from gerbonara.utils import MM
    from cloud_pcb.verify_gate.gerber_drc import audit_annular_geometry
    from shapely.geometry import LineString,Polygon
    drill=Line(5,5,6,5,aperture=ExcellonTool(.3,plated=True,unit=MM),unit=MM)
    pth=ExcellonFile(objects=[drill]);copper=LineString([(5,5),(6,5)]).buffer(.30)
    ring,hits=audit_annular_geometry(pth,copper,copper)
    assert ring>=.10 and not hits
    _,hits=audit_annular_geometry(pth,copper,Polygon())
    assert len(hits)==1 and hits[0]['layer']=='B.Cu'


def test_gerber_polarity_order_preserves_late_dark_flash(tmp_path):
    from gerbonara.apertures import CircleAperture
    from gerbonara.graphic_objects import Region,Flash
    from gerbonara.rs274x import GerberFile
    from gerbonara.utils import MM
    from cloud_pcb.verify_gate.gerber_drc import _gerber_layer_union
    from shapely.geometry import Point
    objects=[Region([(0,0),(10,0),(10,10),(0,10)],unit=MM),Region([(4,4),(6,4),(6,6),(4,6)],polarity_dark=False,unit=MM),Flash(5,5,CircleAperture(.6,unit=MM),unit=MM)]
    path=tmp_path/'ordered.gbr';path.write_bytes(GerberFile(objects=objects).write_to_bytes())
    g,_,_=_gerber_layer_union(path)
    assert g.covers(Point(5,5))
    assert not g.covers(Point(4.1,4.1))


def test_preexisting_ground_stubs_are_visible_to_both_router_obstacle_types(tmp_path):
    from cloud_pcb.general_solver.run_blind import _add_gnd_fanout_vias
    c=component('chip');c['pads'][0]['net']='GND'
    s=spec([c],{'GND':[['chip','0']]})
    b,classes,mapping,keepouts,outline=_build_blind_kicad_board(s,{'chip':(15,15,0)})
    _add_gnd_fanout_vias(b,outline,keepouts)
    if not any(isinstance(t,Segment) for t in b.traceItems):
        # A large exposed pad can use a direct thermal via: add an independent
        # ground stub fixture and repeat the ground fanout obstacle extraction.
        b.traceItems.append(Segment(start=Position(X=15,Y=15),end=Position(X=20,Y=15),width=.2,layer='F.Cu',net=1))
        _add_gnd_fanout_vias(b,outline,keepouts)
    fixed=[k for k in keepouts if k.get('kind')=='fixed_ground_stub']
    assert {'wire_keepout','via_keepout'}<=set(k['dsn_kind'] for k in fixed)
    text=board_to_dsn(b,tmp_path/'fixed.dsn',classes,mapping,keepouts_mm=keepouts).read_text()
    assert '(wire_keepout "" (polygon F.Cu' in text
    assert '(via_keepout "" (polygon F.Cu' in text


def test_electrolytic_identity_uses_diameter_and_height_not_flat_mlcc():
    from cloud_pcb.general_solver.semantics import component_envelope
    c=component('bulk','Capacitor_SMD:CP_Elec_12x16',2)
    del c['height_mm'];del c['body_bbox_mm']
    env=component_envelope(c)
    assert env['family']=='electrolytic' and env['height_mm']==16
    assert abs(env['bbox_mm'][2]-env['bbox_mm'][0]-12)<1e-8
    assert abs(env['bbox_mm'][3]-env['bbox_mm'][1]-12)<1e-8
