"""Independent optimization guardrails, not snapshots of solver implementation."""
import copy
import fcntl
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloud_pcb.general_solver.electrical import derive_electrical_requirements,voltage_label
from cloud_pcb.general_solver.optimization import GERBER_LAYERS,REQUIRED_GATES,compare_candidate,input_conflicts,report_metrics,digest
from scripts import optimize_pcb as workflow


@pytest.mark.parametrize("recorded_status", ["complete", "incomplete", "invalidated"])
def test_latest_round_reports_missing_evidence_without_changing_baseline(tmp_path, monkeypatch, recorded_status):
    monkeypatch.setattr(workflow, "ROOT", tmp_path)
    root = tmp_path / "artifacts/optimization"
    run = root / "round"
    run.mkdir(parents=True)
    pytest_log = run / "pytest.log"
    pytest_log.write_text("example recorded evidence")
    manifest = run / "manifest.json"
    manifest.write_text(json.dumps({
        "status": recorded_status, "cases": [],
        "evidence": {str(pytest_log.relative_to(tmp_path)): digest(pytest_log)},
    }))
    pointer = {"directory": str(run.relative_to(tmp_path)), "manifest_sha256": digest(manifest), "promoted": False}
    (root / "last_run.json").write_text(json.dumps(pointer))
    assert workflow.latest_round_status()["integrity"] == "verified"
    if recorded_status != "complete":
        with pytest.raises(ValueError, match="finalized eligible record"):
            workflow.verify_manifest({"manifest": str(manifest.relative_to(tmp_path)), "sha256": pointer["manifest_sha256"]})
    pytest_log.unlink()
    status = workflow.latest_round_status()
    assert status["recorded_status"] == recorded_status
    assert status["integrity"] == "unverified"
    assert "pytest.log" in status["error"]


@pytest.mark.parametrize(('lock_state', 'activity'), [
    ('idle', 'abandoned'), ('held', 'possibly_active'), ('missing', 'unknown'),
])
def test_unfinalized_attempts_observe_writer_lock_and_recorded_cases(tmp_path, monkeypatch, lock_state, activity):
    monkeypatch.setattr(workflow, 'ROOT', tmp_path)
    root = tmp_path / 'artifacts/optimization'
    run = root / 'runs/interrupted'
    partial = run / 'case3'
    partial.mkdir(parents=True)
    (partial / 'routed.kicad_pcb').write_text('partial board')
    manifest = run / 'manifest.json'
    manifest.write_text(json.dumps({
        'status': 'running', 'started_at': '2026-11-28T04:35:53+00:00',
        'variant': 'dense-escape-repair', 'cases': [
            {'id': 'case1', 'status': 'complete'}, {'id': 'case2', 'status': 'failed'},
        ],
    }))
    finalized = root / 'runs/finalized'
    finalized.mkdir()
    (finalized / 'manifest.json').write_text(json.dumps({'status': 'incomplete', 'cases': []}))

    lock = root / '.run.lock'
    if lock_state == 'missing':
        attempts = workflow.unfinalized_attempts()
        assert not lock.exists()
    else:
        lock.write_text('lock file contents')
        with lock.open('rb') as handle:
            if lock_state == 'held':
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            attempts = workflow.unfinalized_attempts()
        assert lock.read_text() == 'lock file contents'

    assert attempts == [{
        'directory': str(run.relative_to(tmp_path)), 'recorded_status': 'running',
        'activity': activity, 'started_at': '2026-11-28T04:35:53+00:00',
        'variant': 'dense-escape-repair', 'complete_cases': ['case1'], 'failed_cases': ['case2'],
    }]
    assert json.loads(manifest.read_text())['status'] == 'running'
    assert (partial / 'routed.kicad_pcb').read_text() == 'partial board'


def test_status_reports_unfinalized_attempts_separately_from_last_round(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(workflow, 'ROOT', tmp_path)
    monkeypatch.setattr(workflow, 'STATE', tmp_path / 'state.json')
    monkeypatch.setenv('PCB_STUDIO_ROOT', str(tmp_path))
    monkeypatch.setattr(workflow.sys, 'argv', ['optimize_pcb', 'status'])
    monkeypatch.setattr(workflow, 'read_suite', lambda _: {'baseline': {}})
    monkeypatch.setattr(workflow, 'diagnose', lambda *_: {
        'status': 'baseline_diagnosed', 'measurements': {}, 'tasks': [],
    })
    root = tmp_path / 'artifacts/optimization'
    finalized = root / 'runs/finalized'
    finalized.mkdir(parents=True)
    manifest = finalized / 'manifest.json'
    manifest.write_text(json.dumps({'status': 'incomplete', 'cases': []}))
    pointer = root / 'last_run.json'
    previous = {'directory': str(finalized.relative_to(tmp_path)),
                'manifest_sha256': digest(manifest), 'promoted': False}
    pointer.write_text(json.dumps(previous))
    unreadable = root / 'runs/unreadable'
    unreadable.mkdir()
    (unreadable / 'manifest.json').write_text('{')

    assert workflow.main() == 0
    result = json.loads((root / 'status/result.json').read_text())
    summary = json.loads(capsys.readouterr().out)
    assert result['last_round'] == summary['last_round']
    assert result['last_round']['recorded_status'] == 'incomplete'
    assert result['unfinalized_attempts'] == summary['unfinalized_attempts']
    attempt, = result['unfinalized_attempts']
    assert attempt['directory'] == str(unreadable.relative_to(tmp_path))
    assert attempt['activity'] == 'unknown' and attempt['error']
    assert json.loads(pointer.read_text()) == previous


def supply_spec():
    return dict(board_outline_mm=dict(width=20,height=20,origin_x=100,origin_y=50),components=[dict(refdes='arbitrary',pads=[dict(num='1',net='opaque',pin_type='power_in',pin_function='AVDD10',x_rel_mm=0,y_rel_mm=0,w_mm=.6,h_mm=.2,shape='rect',type='smd',layers=['F.Cu'])])],nets={'opaque':[['arbitrary','1']],'/domain/1V2_aux':[]},design_requirements=dict(power_rails=[],differential_pairs=[]),edge_connectors=[])


@pytest.mark.parametrize('label,voltage',[('/domain/1V2_aux',1.2),('+2.5V',2.5),('-12V',-12),('clock_12V',None),('3V3CLK',None),('VREF',None)])
def test_voltage_labels_are_lexical_not_substring_matches(label,voltage):
    assert voltage_label(label)==voltage


def test_supply_derivation_preserves_explicit_constraints_and_input():
    original=supply_spec();original['design_requirements']['power_rails']=[dict(net='opaque',current_a=.24,min_trace_width_mm=.7)]
    before=copy.deepcopy(original);derived,decisions=derive_electrical_requirements(original)
    assert original==before
    assert derived['design_requirements']['power_rails'][0]==before['design_requirements']['power_rails'][0]
    assert [r['net'] for r in decisions]==['/domain/1V2_aux']
    assert decisions[0]['nominal_v']==1.2 and not decisions[0]['current_verified']
    assert 'current_a' not in decisions[0]


def test_supply_inference_survives_arbitrary_net_and_reference_renaming():
    a=supply_spec();a['nets'].pop('/domain/1V2_aux');b=copy.deepcopy(a)
    b['components'][0]['refdes']='new_id';b['components'][0]['pads'][0]['net']='random_label';b['nets']={'random_label':[['new_id','1']]}
    ra=derive_electrical_requirements(a)[1];rb=derive_electrical_requirements(b)[1]
    assert len(ra)==len(rb)==1
    assert ra[0]['evidence'][0]['kind']==rb[0]['evidence'][0]['kind']=='typed_supply_pin'


def test_reference_ground_switch_and_explicit_bus_are_not_supply_trunks():
    s=supply_spec();s['nets'].pop('/domain/1V2_aux')
    for function in ('GND','VSS','VREF','VSW','BIAS','123'):
        s['components'][0]['pads'][0]['pin_function']=function
        assert not derive_electrical_requirements(s)[1]
    s['components'][0]['pads'][0]['pin_function']='AVDD10'
    s['design_requirements']['differential_pairs']=[dict(pos_net='opaque',neg_net='other')]
    assert not derive_electrical_requirements(s)[1]


def report(unrouted=0,opens=0,unknown=0):
    independent=dict(lvs_opens=[dict(net=f'n{i}',components=2) for i in range(opens)],lvs_shorts=[],via_in_pad_violations=[],rf_keepout=[],mechanical=dict(collision_count=0,collisions=[],unverified_envelopes=[str(i) for i in range(unknown)],all_envelopes_verified=unknown==0,passed=unknown==0))
    gates={name:True for name in REQUIRED_GATES};gates['unrouted_zero']=unrouted==0;gates['independent_lvs_opens_zero']=opens==0;gates['mechanical_zero_with_verified_envelopes']=unknown==0
    return dict(audit=dict(independent_4layer=independent,pygerber_by_layer={k:True for k in GERBER_LAYERS},min_annular_ring_mm=.125,annular_ring_violations=0,silk_to_mask_overlap_mm2=0,copper_edge_margins_mm={k:.5 for k in ('F_Cu.gbr','In1_Cu.gbr','In2_Cu.gbr','B_Cu.gbr')}),bakeoff=[dict(unrouted_nets=unrouted)],router_spacing=dict(real_count=0),rf_unresolved=[],gnd_pour_stats=dict(fill_ratio=.9),hard_gates=gates,hard_gates_passed=all(gates.values()),keepouts=[])


def measurement(**kwargs):
    metrics=report_metrics(report(**kwargs));assert not metrics['evidence_errors']
    metrics['values'].update(cap_distance_mm=3.,wirelength_mm=100.)
    return metrics


def test_an_aggregate_gain_cannot_hide_a_new_open_on_another_board():
    before={'a':measurement(opens=5,unrouted=5),'b':measurement()}
    after={'a':measurement(),'b':measurement(opens=1,unrouted=1)}
    decision=compare_candidate(before,after,full_suite=True,tests_passed=True)
    assert decision['improvements'] and decision['regressions'] and not decision['eligible_for_baseline']
    assert any(r['case']=='b' and r['metric']=='opens' for r in decision['regressions'])


def test_shortening_an_incomplete_board_never_earns_promotion():
    a=measurement(opens=1,unrouted=1);b=copy.deepcopy(a);b['values']['wirelength_mm']=1.
    d=compare_candidate({'x':a},{'x':b},full_suite=True,tests_passed=True)
    assert not d['improvements'] and not d['eligible_for_baseline']


def test_complete_suite_tests_and_meaningful_gain_required_for_promotion():
    a=measurement(opens=1,unrouted=1);b=measurement();b['values']['wirelength_mm']=200
    for full,tests in ((False,True),(True,False)):
        assert not compare_candidate({'x':a},{'x':b},full_suite=full,tests_passed=tests)['eligible_for_baseline']
    d=compare_candidate({'x':a},{'x':b},full_suite=True,tests_passed=True)
    assert d['eligible_for_baseline'] and d['hard_gates_passed']
    assert d['engineering_pass'] is None # SI/PI and human comparison are not certified here.


def test_regressing_qor_or_omitting_a_case_blocks_promotion():
    a=measurement();b=copy.deepcopy(a);b['values']['cap_distance_mm']=9.
    assert not compare_candidate({'x':a},{'x':b},full_suite=True,tests_passed=True)['eligible_for_baseline']
    assert compare_candidate({'x':a,'y':a},{'x':a},full_suite=False,tests_passed=True)['invalid']


def test_missing_evidence_and_fabricated_passes_fail_closed():
    missing=report_metrics({'hard_gates':{},'hard_gates_passed':True})
    assert missing['evidence_errors'] and not missing['hard_pass']
    r=report();del r['audit']['pygerber_by_layer']['B_Paste.gbr']
    m=report_metrics(r)
    assert m['values']['gerber_failures']==1 and m['evidence_errors'] and not m['hard_pass']
    r=report(unknown=2);r['hard_gates']['mechanical_zero_with_verified_envelopes']=True;r['hard_gates_passed']=True
    assert report_metrics(r)['evidence_errors']


def test_rf_audit_requires_all_four_layers_and_actual_zero_copper():
    r=report();r['keepouts']=[dict(kind='rf',refdes='radio')]
    r['audit']['independent_4layer']['rf_keepout']=[dict(refdes='radio',layer='F.Cu',copper_mm2=0,coverage_fraction=0,passed=True)]
    assert report_metrics(r)['evidence_errors']
    r['audit']['independent_4layer']['rf_keepout'][0]['copper_mm2']=1.
    assert any('contradicts' in e for e in report_metrics(r)['evidence_errors'])


def test_fixed_interface_conflict_accounts_for_rotation_and_board_origin():
    s=supply_spec();s['components'][0]['pads'][0].update(x_rel_mm=2)
    s['edge_connectors']=[dict(refdes='arbitrary',x_mm=100.8,y_mm=60,angle_deg=90)]
    assert input_conflicts(s)==[]
    s['edge_connectors'][0]['x_mm']=100.3
    conflicts=input_conflicts(s)
    assert len(conflicts)==1 and abs(conflicts[0]['actual_mm']-.2)<1e-6
    assert s['edge_connectors'][0]['x_mm']==100.3 # Never silently move a mating interface.


def test_exposed_holdout_and_input_hash_changes_are_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(workflow,'ROOT',tmp_path)
    spec=tmp_path/'input.json';spec.write_text('{}');exposure=tmp_path/'seal.json';exposure.write_text(json.dumps(dict(status='unsealed')))
    c=dict(id='sample',split='sealed_holdout',spec='input.json',spec_sha256=digest(spec),exposure_manifest='seal.json');suite=tmp_path/'suite.json'
    suite.write_text(json.dumps(dict(schema_version=1,cases=[c])))
    with pytest.raises(ValueError,match='exposed'):workflow.read_suite(suite)
    c['split']='regression';suite.write_text(json.dumps(dict(schema_version=1,cases=[c])));assert workflow.read_suite(suite)
    spec.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='input hash'):workflow.read_suite(suite)


def test_historical_artifact_changes_and_added_files_are_detected(tmp_path,monkeypatch):
    monkeypatch.setattr(workflow,'ROOT',tmp_path)
    out=tmp_path/'frozen';out.mkdir();artifact=out/'board';artifact.write_text('evidence')
    m=dict(status='frozen',cases=[dict(status='complete',outdir='frozen',artifacts={'frozen/board':digest(artifact)})]);path=tmp_path/'manifest.json';path.write_text(json.dumps(m));pointer=dict(manifest='manifest.json',sha256=digest(path))
    assert workflow.verify_manifest(pointer)
    extra=out/'unexpected';extra.write_text('new')
    with pytest.raises(ValueError,match='file set'):workflow.verify_manifest(pointer)
    extra.unlink();artifact.write_text('tampered')
    with pytest.raises(ValueError,match='artifact changed'):workflow.verify_manifest(pointer)


def test_source_change_invalidates_a_round(monkeypatch):
    monkeypatch.setattr(workflow,'source_snapshot',lambda _:dict(code='new'))
    with pytest.raises(RuntimeError,match='source or suite changed'):workflow.assert_unchanged(dict(code='old'),Path('unused'))


def test_timeout_terminates_the_managed_process_group(tmp_path):
    start=time.monotonic()
    code=workflow.sourced_process(['bash','-c','sleep 30'],tmp_path/'timeout.log',.2)
    assert code==124 and time.monotonic()-start<4


def test_failing_tests_prevent_solver_start_and_baseline_promotion(tmp_path,monkeypatch):
    monkeypatch.setattr(workflow,'ROOT',tmp_path);monkeypatch.setattr(workflow,'STATE',tmp_path/'state.json')
    source=tmp_path/'source.py';source.write_text('test fixture');spec=tmp_path/'input.json';spec.write_text('{}');suite_path=tmp_path/'suite.json';suite_path.write_text('{}')
    pointer=dict(format='test',manifest='old',sha256='fixed');suite=dict(baseline=pointer,cases=[dict(id='board',split='regression',spec='input.json',spec_sha256=digest(spec))])
    monkeypatch.setattr(workflow,'read_suite',lambda _:suite);monkeypatch.setattr(workflow,'source_snapshot',lambda _:dict({'source.py':digest(source)}));monkeypatch.setattr(workflow,'verify_manifest',lambda _:dict(status='frozen'))
    monkeypatch.setattr(workflow,'baseline_measurements',lambda *_:({'board':measurement()},{},{}))
    commands=[]
    def failed_tests(argv,log,timeout):commands.append(argv);Path(log).write_text('1 failed in 0.01s');return 1
    monkeypatch.setattr(workflow,'sourced_process',failed_tests)
    args=SimpleNamespace(suite=suite_path,cases='all',label='guard-test',hypothesis='test guard',case_timeout=1,variant='stable')
    assert workflow.run_round(args)==1 and len(commands)==1 and commands[0][2]=='pytest'
    assert not workflow.STATE.exists()
    manifests=list((tmp_path/'artifacts/optimization/runs').glob('*/manifest.json'));assert len(manifests)==1
    manifest=json.loads(manifests[0].read_text());assert manifest['status']=='invalidated' and not manifest['decision']['eligible_for_baseline']
