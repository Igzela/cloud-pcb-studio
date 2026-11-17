"""External-model isolation and provenance checks; no network in unit tests."""
import copy
import io
import json
import urllib.error

import pytest

from cloud_pcb.general_solver.gemini_reference import anonymous_spec,reference_request,call_gemini,decode_placement


def blind_spec():
    return dict(project_name='hidden_project',repo_url='https://example.test/private',commit_sha='hidden_commit',placed_positions={'secret_part':[999,888,0]},board_outline_mm=dict(width=30,height=20,origin_x=100,origin_y=50),mounting_holes=[dict(x_mm=103,y_mm=54,drill_mm=2)],edge_connectors=[dict(refdes='secret_part',x_mm=110,y_mm=55,angle_deg=90,allow_body_overhang=True)],components=[dict(refdes='secret_part',value='chip',footprint_name='PrivateLibrary:QFN-4',position=[777,666],body_bbox_mm=[-2,-2,2,2],pads=[dict(num='1',net='/hidden_rail/1V2',x_rel_mm=-1,y_rel_mm=0,w_mm=.3,h_mm=.6,type='smd',layers=['F.Cu'],pin_type='power_in',pin_function='VDD')])],nets={'/hidden_rail/1V2':[['secret_part','1']]},design_requirements=dict(power_rails=[dict(net='/hidden_rail/1V2',nominal_v=1.2)],differential_pairs=[]))


def output(anonymous,**changes):
    pose=dict(id=anonymous['components'][0]['id'],x_mm=10,y_mm=5,angle_deg=90,side='top');pose.update(changes)
    return dict(status='generated',candidates=[dict(finish_reason='STOP',text=json.dumps(dict(placements=[pose],design_notes=['final design decision'])))])


def test_anonymous_request_excludes_project_and_solver_answers():
    spec=blind_spec();before=copy.deepcopy(spec);anonymous,mapping=anonymous_spec(spec);request=reference_request(anonymous);text=json.dumps(request)
    for forbidden in ('hidden_project','hidden_commit','example.test','secret_part','hidden_rail','PrivateLibrary','999','888','777','666'):
        assert forbidden not in text
    assert anonymous['fixed_interfaces'][0]['x_mm']==10 and anonymous['mounting_holes'][0]['x_mm']==3
    assert anonymous['components'][0]['pads'][0]['x_rel_mm']==-1
    assert any(n.get('nominal_v')==1.2 for n in anonymous['electrical_requirements']['net_semantics'])
    assert mapping['components'][anonymous['components'][0]['id']]=='secret_part' and spec==before


def test_decoded_proposal_never_claims_routing_or_engineering_pass():
    anonymous,mapping=anonymous_spec(blind_spec());decoded=decode_placement(output(anonymous),anonymous,mapping)
    assert decoded['placements']['secret_part']['x_mm']==10
    assert decoded['status']=='placement_generated_unverified' and decoded['routing_status']=='not_run' and decoded['geometry_status']=='not_run' and decoded['engineering_pass'] is None


def test_thinking_and_final_output_share_a_recorded_bounded_budget():
    anonymous,_=anonymous_spec(blind_spec());request=reference_request(anonymous)
    assert request['generationConfig']['maxOutputTokens']==32768
    assert request['generationConfig']['thinkingConfig']==dict(thinkingLevel='LOW',includeThoughts=False)
    for invalid in (0,65537,True):
        with pytest.raises(ValueError):reference_request(anonymous,max_output_tokens=invalid)
    with pytest.raises(ValueError):reference_request(anonymous,thinking_level='unbounded')


def test_retry_keeps_task_and_identity_and_cannot_resample_a_valid_proposal(tmp_path):
    from scripts.run_gemini_reference import retry_lineage
    anonymous,_=anonymous_spec(blind_spec());request=reference_request(anonymous)
    old=copy.deepcopy(request);old['generationConfig']['maxOutputTokens']=12000
    (tmp_path/'request.json').write_text(json.dumps(old))
    manifest=dict(status='invalid_model_output',anonymous_input_sha256='same-input',requested_model='gemini-4-hydrogen')
    (tmp_path/'reference_manifest.json').write_text(json.dumps(manifest))
    assert retry_lineage(tmp_path,'same-input','gemini-4-hydrogen',request)['prompt_unchanged']
    with pytest.raises(ValueError):retry_lineage(tmp_path,'changed-input','gemini-4-hydrogen',request)
    changed=copy.deepcopy(request);changed['contents']=[]
    with pytest.raises(ValueError):retry_lineage(tmp_path,'same-input','gemini-4-hydrogen',changed)
    manifest['status']='placement_generated_unverified';(tmp_path/'reference_manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):retry_lineage(tmp_path,'same-input','gemini-4-hydrogen',request)


@pytest.mark.parametrize('changes',({'x_mm':11},{'x_mm':True},{'x_mm':float('nan')},{'id':'unknown'},{'side':'bottom'}))
def test_invalid_poses_and_moved_fixed_interfaces_are_rejected(changes):
    anonymous,mapping=anonymous_spec(blind_spec())
    with pytest.raises(ValueError):decode_placement(output(anonymous,**changes),anonymous,mapping)


def test_incomplete_or_truncated_model_output_is_not_a_reference():
    anonymous,mapping=anonymous_spec(blind_spec());reply=output(anonymous);reply['candidates'][0]['finish_reason']='MAX_TOKENS'
    with pytest.raises(ValueError,match='complete'):decode_placement(reply,anonymous,mapping)
    reply=output(anonymous);reply['candidates'][0]['text']='{"placements":[]}'
    with pytest.raises(ValueError,match='coverage'):decode_placement(reply,anonymous,mapping)
    with pytest.raises(ValueError,match='no model output'):decode_placement(dict(status='unavailable'),anonymous,mapping)


def test_bounded_service_retries_do_not_change_model_or_leak_key():
    key='private-unit-test-key';requests=[];pauses=[]
    def busy(request,timeout):
        requests.append(request)
        raise urllib.error.HTTPError(request.full_url,503,'busy',{},io.BytesIO(('busy '+key).encode()))
    reply=call_gemini({'contents':[]},key,transport=busy,pause=pauses.append)
    assert reply['status']=='unavailable' and len(requests)==3 and pauses==[2,4]
    assert all(r.full_url.endswith('/gemini-4-hydrogen:generateContent') for r in requests)
    assert key not in json.dumps(reply)
    assert all(key not in r.full_url for r in requests)


def test_authorization_error_is_not_retried_and_no_model_url_injection():
    calls=[]
    def forbidden(request,timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url,403,'forbidden',{},io.BytesIO(b'denied'))
    reply=call_gemini({},'unit-key',transport=forbidden,pause=lambda _:None)
    assert len(calls)==1 and reply['status']=='unavailable'
    with pytest.raises(ValueError,match='model ID'):call_gemini({},'unit-key',model='../other-server')


def test_reported_model_version_and_usage_are_preserved_and_thoughts_filtered():
    class Response(io.BytesIO):status=200
    body=dict(modelVersion='gemini-4-hydrogen-actual',responseId='response-id',usageMetadata=dict(promptTokenCount=4),candidates=[dict(finishReason='STOP',continuationToken='continuation-id',content=dict(parts=[dict(text='private reasoning',thought=True),dict(text='{"placements":[]}')]))])
    reply=call_gemini({},'unit-key',transport=lambda *_args,**_kwargs:Response(json.dumps(body).encode()))
    assert reply['reported_model_version']=='gemini-4-hydrogen-actual' and reply['usage']['promptTokenCount']==4
    assert reply['candidates'][0]['text']=='{"placements":[]}' and 'private reasoning' not in json.dumps(reply)
    assert reply['response_id']=='response-id' and reply['candidates'][0]['continuation_token']=='continuation-id'
