"""Supply-domain inference under renaming, incomplete data and explicit limits."""
import copy

import pytest

from contracts.schemas import NetClassRule
from cloud_pcb.general_solver.electrical import derive_electrical_requirements, supply_routing_scope
from cloud_pcb.general_solver.power import specialize_power_rules


def pin(num, net, kind='passive', function='', xy=(0,0)):
    return dict(num=str(num),net=net,pin_type=kind,pin_function=function,x_rel_mm=xy[0],y_rel_mm=xy[1],w_mm=.3,h_mm=.6,type='smd',shape='rect',layers=['F.Cu'])


def component(ref, footprint, pads):
    return dict(refdes=ref,value='unspecified',footprint_name=footprint,pads=pads,body_bbox_mm=[-1.5,-1.5,1.5,1.5],height_mm=1)


def network(extra=None):
    owner=component('owner','Package_QFN:QFN-4',[pin(1,'opaque','power_in','AVDD10',(-1,-1)),pin(2,'return','power_in','GND',(1,-1)),pin(3,'data','output','OUT',(1,1)),pin(4,'other','input','EN',(-1,1))])
    cap=component('tank','Capacitor_SMD:C_0603',[pin(1,'opaque',xy=(-.75,0)),pin(2,'return',xy=(.75,0))])
    s=dict(project_name='synthetic',board_outline_mm=dict(width=40,height=30),mounting_holes=[],edge_connectors=[],components=[owner,cap]+(extra or []),nets={},design_requirements=dict(power_rails=[dict(net='return',nominal_v=0)],differential_pairs=[]))
    rebuild_nets(s)
    return s


def rebuild_nets(spec):
    spec['nets']={}
    for c in spec['components']:
        for p in c['pads']:
            if p.get('net'):spec['nets'].setdefault(p['net'],[]).append([c['refdes'],p['num']])


def power_rule(spec):
    rule=NetClassRule('POWER',.5,.15,.25,.5,2)
    classes,mapping,intents=specialize_power_rules(spec,{'POWER':rule,'DEFAULT':NetClassRule('DEFAULT',.2,.15,.25,.5,.3)},{'opaque':'POWER'})
    return classes[mapping['opaque']],intents['opaque']


def test_one_ic_and_shunt_cap_require_typed_supply_and_return_evidence():
    s=network();before=copy.deepcopy(s)
    scope=supply_routing_scope(s,'opaque')
    assert scope['scope']=='single_ic_domain' and scope['owner_ref']=='owner'
    assert scope['shunt_caps']==['tank'] and scope['supply_pins']==['1']
    assert s==before
    derived,decisions=derive_electrical_requirements(s,scoped=True)
    rule,intent=power_rule(derived)
    assert rule.trace_width_mm==.25 and rule.clearance_mm==.15
    assert intent['current_a'] is None and not intent['current_verified']
    assert decisions[0]['routing_scope']=='single_ic_domain'


def test_filter_inductor_and_feedback_resistor_can_belong_to_one_ic_domain():
    extra=[component('filter','Inductor_SMD:L_0805',[pin(1,'switch'),pin(2,'opaque')]),component('feedback','Resistor_SMD:R_0402',[pin(1,'opaque'),pin(2,'feedback_node')])]
    scope=supply_routing_scope(network(extra),'opaque')
    assert scope['scope']=='single_ic_domain'
    assert set(scope['passive_members'])=={'filter','feedback','tank'}


@pytest.mark.parametrize('value',('3.3uH/1A','47nF/16V','4k7/1%'))
def test_opaque_two_terminal_packages_require_value_unit_evidence(value):
    extra=component('passive','Private:Opaque_2pin',[pin(1,'opaque'),pin(2,'filter_node')])
    extra['value']=value
    assert supply_routing_scope(network([extra]),'opaque')['scope']=='single_ic_domain'
    extra['value']='2'
    assert supply_routing_scope(network([extra]),'opaque')['scope']=='board_distribution'


@pytest.mark.parametrize('extra',[
    component('second','Package_SO:SOIC-4',[pin(1,'opaque','power_in','VDD'),pin(2,'return'),pin(3,'a'),pin(4,'b')]),
    component('external','Connector_PinHeader:PinHeader_1x02',[pin(1,'opaque'),pin(2,'return')]),
    component('unknown','Custom:Unknown_2pad',[pin(1,'opaque'),pin(2,'unclassified')]),
])
def test_other_ic_interface_or_unknown_endpoint_prevents_local_scope(extra):
    s=network([extra]);assert supply_routing_scope(s,'opaque')['scope']=='board_distribution'


def test_connector_cannot_masquerade_as_an_ic_even_without_edge_metadata():
    s=network();s['components'][0]['footprint_name']='Connector_USB:Receptacle'
    assert supply_routing_scope(s,'opaque')['scope']=='board_distribution'
    s=network();s['edge_connectors']=[dict(refdes='owner')]
    assert supply_routing_scope(s,'opaque')['scope']=='board_distribution'


def test_open_or_unconnected_cap_is_not_return_evidence():
    s=network();s['components'][1]['pads'][1]['net']='floating'
    rebuild_nets(s);assert supply_routing_scope(s,'opaque')['scope']=='board_distribution'
    s['components'][1]['pads'][1]['net']='';s['components'][0]['pads'][1]['net']=''
    rebuild_nets(s);assert supply_routing_scope(s,'opaque')['scope']=='board_distribution'


@pytest.mark.parametrize('function',('123','VREF','VDD_SW','VSS','GND'))
def test_numeric_reference_switch_and_ground_pin_labels_cannot_prove_supply(function):
    s=network();s['components'][0]['pads'][0]['pin_function']=function
    assert supply_routing_scope(s,'opaque')['scope']=='board_distribution'


def test_inconsistent_net_and_pad_tables_prevent_narrowing():
    s=network();s['nets']['opaque'].pop();assert supply_routing_scope(s,'opaque')['scope']=='board_distribution'
    s=network();s['nets']['opaque'].append(['missing','1']);assert supply_routing_scope(s,'opaque')['scope']=='board_distribution'
    s=network();s['components'][0]['pads'].append(pin(5,'opaque','power_in','DVDD'))
    assert supply_routing_scope(s,'opaque')['scope']=='board_distribution'


def test_scope_and_width_survive_arbitrary_ref_net_and_input_order_changes():
    s=network();renamed=copy.deepcopy(s)
    refs={'owner':'r9a','tank':'q6b'};nets={n:'signal_'+str(i) for i,n in enumerate(s['nets'])}
    for c in renamed['components']:
        c['refdes']=refs[c['refdes']]
        for p in c['pads']:p['net']=nets[p['net']]
        c['pads'].reverse()
    renamed['components'].reverse();rebuild_nets(renamed)
    old=supply_routing_scope(s,'opaque');new=supply_routing_scope(renamed,nets['opaque'])
    assert old['scope']==new['scope']=='single_ic_domain'
    assert len(old['shunt_caps'])==len(new['shunt_caps'])==1
    assert new['owner_ref']=='r9a' and not new['current_verified']


def test_explicit_current_and_width_override_local_domain_default():
    s=network();s=derive_electrical_requirements(s,scoped=True)[0]
    rail=next(r for r in s['design_requirements']['power_rails'] if r['net']=='opaque')
    rail['max_current_a']=1.
    assert power_rule(s)[0].trace_width_mm>=.5 and power_rule(s)[1]['current_verified']
    rail['max_current_a']=None;rail['current_a']=2.
    assert power_rule(s)[0].trace_width_mm>=.5 and power_rule(s)[1]['current_verified']
    del rail['current_a']
    del rail['max_current_a'];rail['min_trace_width_mm']=.8
    assert power_rule(s)[0].trace_width_mm>=.8
    raw=network();raw['design_requirements']['power_rails'].append(dict(net='opaque',max_current_a=2.,min_trace_width_mm=.7))
    original=copy.deepcopy(raw['design_requirements']['power_rails']);derived,decisions=derive_electrical_requirements(raw,scoped=True)
    assert not decisions and derived['design_requirements']['power_rails']==original
    assert power_rule(derived)[0].trace_width_mm>=.7


def test_backbone_width_is_not_collapsed_and_earlier_variant_remains_unchanged():
    s=network([component('port','Connector:Terminal_2',[pin(1,'opaque'),pin(2,'return')])])
    scoped=derive_electrical_requirements(s,scoped=True)[0]
    assert power_rule(scoped)[0].trace_width_mm>=.5
    legacy=derive_electrical_requirements(network())[0]
    assert all('routing_scope' not in r for r in legacy['design_requirements']['power_rails'])
    assert power_rule(legacy)[0].trace_width_mm>=.5


def test_local_rules_reach_dsn_as_exclusive_classes(tmp_path):
    from cloud_pcb.general_solver.run_blind import _build_blind_kicad_board
    from cloud_pcb.layout_route.dsn_ses_bridge import board_to_dsn
    s=derive_electrical_requirements(network(),scoped=True)[0]
    board,classes,mapping,*_=_build_blind_kicad_board(s,{'owner':(10,10,0),'tank':(20,10,0)})
    rule=classes[mapping['opaque']];assert rule.trace_width_mm==.25
    text=board_to_dsn(board,tmp_path/'scope.dsn',classes,mapping).read_text()
    assert '(class '+mapping['opaque']+' opaque' in text and '(width 250)' in text
