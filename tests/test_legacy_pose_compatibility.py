"""Scratch candidate compatibility witnesses and genuine fallback exhaustion."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from shapely.geometry import box

from cloud_pcb.general_solver.run_blind import PlacementExhausted, _classify_components_and_place
from cloud_pcb.general_solver.two_side_packing import validate_legacy_placement


def pad(num,x,y=0,net=""):
    return dict(num=str(num),type="smd",x_rel_mm=x,y_rel_mm=y,w_mm=.2,h_mm=.2,
                shape="rect",layers=["F.Cu"],net=net)


def passive():
    return {"refdes":"R","value":"100","body_bbox_mm":[-.2,-.3,.2,.3],
            "pads":[pad(1,0,-.2,"A"),pad(2,0,.2,"B")]}


def blocked_both_sides():
    top={"refdes":"J","value":"fixed top","side":"top","body_bbox_mm":[0,-3.2,6.4,3.2],
         "pads":[pad(1,3.3,-1.2,"A"),pad(2,3.3,1.2,"B")]}
    bottom={"refdes":"K","value":"fixed bottom","side":"bottom","body_bbox_mm":[-5.55,-3.2,0,3.2],
            "pads":[pad(1,-1.5,.5)]}
    def lock(ref):
        return {"refdes":ref,"x_mm":.8,"y_mm":4.,"angle_deg":0.,"allow_body_overhang":False}
    return {"project_name":"Synthetic both-side blockage","repo_url":"","commit_sha":"",
            "board_outline_mm":{"width":8.,"height":8.},"components":[top,bottom,passive()],
            "edge_connectors":[lock("J"),lock("K")],
            "nets":{"A":[["J","1"],["R","1"]],"B":[["J","2"],["R","2"]]},
            "design_requirements":{}}


def proof(spec, positions, sides, fixed=(), size=8):
    return validate_legacy_placement(spec,positions,sides,set(fixed),box(0,0,size,size))


def test_accepts_input_authorized_legacy_flip_without_mutating_result():
    spec=blocked_both_sides()
    spec['components'].pop(1);spec['edge_connectors'].pop(1)
    positions={"J":(.8,4.,0.),"R":(6.,5.,90.)}; sides={"J":"top","R":"bottom"}
    original=deepcopy(spec)
    assert proof(spec,positions,sides,("J",))["status"]=="accepted"
    assert positions["R"]==(6.,5.,90.) and sides["R"]=="bottom" and spec==original


def test_rejects_explicit_flip_and_moved_fixed_pose():
    spec=blocked_both_sides()
    spec['components'].pop(1);spec['edge_connectors'].pop(1)
    spec['components'][1]['side']='top'
    positions={"J":(.801,4.,0.),"R":(6.,5.,90.)}; sides={"J":"top","R":"bottom"}
    result=proof(spec,positions,sides,("J",))
    assert result['status']=='rejected'
    assert {i['kind'] for i in result['issues']}=={'source_side_constraint','fixed_pose_changed'}


def test_rejects_movable_copper_edge_and_opposite_side_offset_slot():
    p=passive()
    drill=dict(num="M",type="np_thru_hole",x_rel_mm=.7,y_rel_mm=0,
               w_mm=2.4,h_mm=1.4,shape="oval",angle_rel_deg=30,drill_shape="oval",
               drill_width_mm=1.6,drill_height_mm=.8,drill_offset_mm=[.4,0],layers=["*.Cu"])
    top={"refdes":"J","side":"top","body_bbox_mm":[-1,-2,4,2],"pads":[drill]}
    spec={"board_outline_mm":{"width":14,"height":14},"components":[top,p]}
    initial=proof(spec,{"J":(7,7,90),"R":(7.,5.8,90)},{"J":"top","R":"bottom"},size=14)
    assert initial['status']=='rejected'
    assert any(i['kind']=='opposite_side_drill_guard' for i in initial['issues'])
    near_edge=proof(spec,{"J":(7,7,90),"R":(.590,3,0)},{"J":"top","R":"bottom"},size=14)
    assert near_edge['status']=='rejected'
    assert any(i['kind']=='movable_outline' and i['refdes']=='R' for i in near_edge['issues'])


def test_real_legacy_auto_flip_exhausts_then_typed_geometry_finds_edge_slot():
    spec=blocked_both_sides()
    trial=deepcopy(spec)
    with pytest.raises(PlacementExhausted):
        _classify_components_and_place(trial)
    assert trial['components'][2]['side']=='bottom'
    assert 'side' not in spec['components'][2]
    positions,metadata=_classify_components_and_place(
        spec, packing_order='area',packing_compact=True,packing_two_sided=True,protect_sides=True,
    )
    assert positions['J']==positions['K']==(.8,4.,0.)
    assert metadata['placement_recovery']['auto_bottom']==['R']
    assert metadata['component_sides']=={'J':'top','K':'bottom','R':'bottom'}
    assert 7.10 < positions['R'][0] < 7.40


if __name__ == '__main__':
    import sys
    Path(sys.argv[1]).write_text(json.dumps(blocked_both_sides(),indent=2)+'\n')
