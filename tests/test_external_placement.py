"""External model placements are explicit inputs, never silently repaired."""
import copy
import json
from pathlib import Path

import pytest

from cloud_pcb.general_solver.placement_input import apply_external_placement
from cloud_pcb.general_solver.semantics import mechanical_check
from cloud_pcb.general_solver.optimization import digest
from scripts.route_gemini_reference import verify_proposal


def fixture():
    pad=lambda n:dict(num=str(n),net='',x_rel_mm=float(n),y_rel_mm=0,w_mm=.3,h_mm=.6,type='smd',shape='rect',layers=['F.Cu'])
    comps=[dict(refdes=r,value='same',footprint_name='Capacitor:C_0603',pads=[pad(1),pad(2)],body_bbox_mm=[-1,-1,1,1],height_mm=1) for r in ('one','two')]
    spec=dict(board_outline_mm=dict(width=30,height=20,origin_x=100,origin_y=50),components=comps,nets={},edge_connectors=[dict(refdes='one',x_mm=105,y_mm=55,angle_deg=-90)],design_requirements=dict(power_rails=[],differential_pairs=[]))
    doc=dict(placements={'one':dict(x_mm=5,y_mm=5,angle_deg=270,side='top'),'two':dict(x_mm=15,y_mm=5,angle_deg=0,side='top')})
    return spec,doc


def test_external_coordinates_are_applied_exactly_and_others_not_filled():
    spec,doc=fixture();positions,meta=apply_external_placement(spec,doc)
    assert positions=={'one':(5.,5.,270.),'two':(15.,5.,0.)}
    assert meta['placement_source']=='external_input' and not meta['coordinates_repaired']
    assert meta['total_placed']==2
    missing=copy.deepcopy(doc);del missing['placements']['two']
    with pytest.raises(ValueError,match='cover every'):apply_external_placement(spec,missing)


@pytest.mark.parametrize('field,value',(('x_mm',True),('y_mm',float('inf')),('angle_deg',float('nan')),('side','unknown')))
def test_invalid_external_documents_do_not_partially_mutate_spec(field,value):
    spec,doc=fixture();before=copy.deepcopy(spec);doc['placements']['two'][field]=value
    with pytest.raises(ValueError):apply_external_placement(spec,doc)
    assert spec==before


def test_fixed_mating_interface_is_not_corrected_or_moved():
    spec,doc=fixture();doc['placements']['one']['x_mm']=6;before=copy.deepcopy(spec)
    with pytest.raises(ValueError,match='fixed mating'):apply_external_placement(spec,doc)
    assert spec==before


def test_bottom_assignment_is_input_not_a_new_internal_placement():
    spec,doc=fixture();doc['placements']['two']['side']='bottom';positions,meta=apply_external_placement(spec,doc)
    assert spec['components'][1]['side']=='bottom' and positions['two']==(15.,5.,0.)
    assert meta['component_sides']['two']=='bottom'
    spec,doc=fixture();spec['components'][1]['pads'].append(copy.deepcopy(spec['components'][1]['pads'][0]));doc['placements']['two']['side']='bottom'
    with pytest.raises(ValueError,match='two-terminal'):apply_external_placement(spec,doc)


def test_overlapping_input_stays_overlapping_and_independent_mechanics_fails():
    spec,doc=fixture();doc['placements']['two'].update(x_mm=5,y_mm=5)
    positions,_=apply_external_placement(spec,doc)
    assert positions['two'][:2]==positions['one'][:2]
    assert mechanical_check(spec,positions)['collision_count']==1


def test_external_entry_bypasses_internal_placer_but_default_entry_keeps_it(tmp_path,monkeypatch):
    from cloud_pcb.general_solver import run_blind as solver
    spec,doc=fixture();path=tmp_path/'input.json';path.write_text(json.dumps(spec));calls=[]
    class Constructed(Exception):pass
    def build(s,positions):calls.append(('build',positions));raise Constructed()
    def place(s):calls.append(('internal',None));return {'one':(1,1,0),'two':(2,2,0)},{}
    monkeypatch.setattr(solver,'_build_blind_kicad_board',build);monkeypatch.setattr(solver,'_classify_components_and_place',place)
    with pytest.raises(Constructed):solver.run_blind_solver(path,tmp_path/'external',external_placement=doc)
    assert [c[0] for c in calls]==['build'] and calls[0][1]['two']==(15.,5.,0.)
    calls.clear()
    with pytest.raises(Constructed):solver.run_blind_solver(path,tmp_path/'default')
    assert [c[0] for c in calls]==['internal','build']


def test_reference_source_and_placement_hashes_cannot_be_changed(tmp_path):
    spec=tmp_path/'spec.json';spec.write_text('{}');ref=tmp_path/'ref';ref.mkdir();pose=ref/'placement.json';pose.write_text('{"placements":{}}')
    manifest=dict(status='placement_generated_unverified',requested_model='gemini-4-hydrogen',reported_model_version='gemini-4-hydrogen',spec_sha256=digest(spec),artifacts={'placement.json':digest(pose)})
    (ref/'reference_manifest.json').write_text(json.dumps(manifest));assert verify_proposal(ref,spec)
    pose.write_text('{"placements":{"changed":{}}}')
    with pytest.raises(ValueError,match='artifact changed'):verify_proposal(ref,spec)
