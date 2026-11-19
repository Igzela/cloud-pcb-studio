"""Repeatable optimization rounds. Always invoke after source scripts/env.sh.

Each round evaluates a concrete source revision and hypothesis, not an automatic
self-modifying loop. Existing baselines and sealed references remain untouched.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import platform
import re
import shlex
import signal
import subprocess
import sys
import tarfile
import time
import traceback
from datetime import datetime,timezone
from pathlib import Path

from cloud_pcb.general_solver.optimization import atomic_json,compare_candidate,digest,input_conflicts,report_metrics,triage,write_review
from scripts.run_blind_v3 import code_fingerprint

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_SUITE=ROOT/'benchmark_case/optimization_suite.json'
STATE=ROOT/'artifacts/optimization/state.json'


def now():return datetime.now(timezone.utc).isoformat()


def local_path(name):
    path=(ROOT/name).resolve()
    if not path.is_relative_to(ROOT):raise ValueError('path outside project: '+str(name))
    return path


def read_suite(path):
    suite=json.loads(Path(path).read_text())
    if suite.get('schema_version')!=1:raise ValueError('unsupported optimization suite schema')
    ids=[c['id'] for c in suite['cases']]
    if len(ids)!=len(set(ids)) or not ids:raise ValueError('dataset IDs must be unique and nonempty')
    for case in suite['cases']:
        if not re.fullmatch(r'[a-zA-Z0-9_-]+',case['id']):raise ValueError('unsafe dataset ID')
        if case['split'] not in ('regression','sealed_holdout'):raise ValueError('unknown dataset split')
        if digest(local_path(case['spec']))!=case['spec_sha256']:raise ValueError('blind input hash changed: '+case['id'])
        if case.get('exposure_manifest'):
            exposure=json.loads(local_path(case['exposure_manifest']).read_text())
            if exposure['status']=='unsealed' and case['split']!='regression':raise ValueError('exposed case cannot be a holdout: '+case['id'])
            if exposure['status']=='sealed' and case['split']!='sealed_holdout':raise ValueError('sealed case cannot be used for optimization: '+case['id'])
        elif case['split']=='sealed_holdout':raise ValueError('holdout requires explicit sealing metadata')
    return suite


def verify_manifest(pointer):
    path=local_path(pointer['manifest'])
    if digest(path)!=pointer['sha256']:raise ValueError('baseline manifest hash changed')
    manifest=json.loads(path.read_text())
    if manifest.get('status') not in ('frozen','complete'):raise ValueError('baseline is not a completed immutable run')
    if pointer.get('source_archive') and digest(local_path(pointer['source_archive']))!=pointer['source_archive_sha256']:raise ValueError('baseline source archive changed')
    for entry in manifest['cases']:
        if entry['status']!='complete':raise ValueError('baseline has incomplete case')
        for name,sha in entry['artifacts'].items():
            if digest(local_path(name))!=sha:raise ValueError('baseline artifact changed: '+name)
        directory=local_path(entry['outdir'])
        if {str(p.relative_to(ROOT)) for p in directory.rglob('*') if p.is_file()}!=set(entry['artifacts']):raise ValueError('baseline artifact file set changed')
    for group in ('evidence','inputs'):
        for name,sha in manifest.get(group,{}).items():
            if digest(local_path(name))!=sha:raise ValueError('baseline evidence changed: '+name)
    return manifest


def active_pointer(suite):
    return json.loads(STATE.read_text())['active_baseline'] if STATE.exists() else suite['baseline']


def latest_round_status():
    """Check the published round separately from the active baseline."""
    pointer_path = ROOT / 'artifacts/optimization/last_run.json'
    if not pointer_path.is_file():
        return None
    details = {}
    try:
        latest = json.loads(pointer_path.read_text())
        path = local_path(latest['directory']) / 'manifest.json'
        pointer = {'manifest': str(path.relative_to(ROOT)), 'sha256': latest['manifest_sha256']}
        details = {'directory': latest['directory'], 'promoted': latest['promoted']}
        details['recorded_status'] = json.loads(path.read_text()).get('status')
        verify_manifest(pointer)
        return dict(details, integrity='verified')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return dict(details, integrity='unverified', error=str(exc))


def baseline_measurements(suite,pointer):
    manifest=verify_manifest(pointer)
    entries={str(c.get('id',c.get('case'))):c for c in manifest['cases']};result={};reports={};details={}
    for case in suite['cases']:
        if case['split']!='regression':continue
        key=str(case['legacy_case']) if pointer['format']=='v3_freeze' else case['id']
        if key not in entries:raise ValueError('baseline does not cover regression dataset: '+case['id'])
        entry=entries[key]
        if entry['spec_sha256']!=case['spec_sha256']:raise ValueError('baseline input differs from suite: '+case['id'])
        directory=local_path(entry['outdir']);report=json.loads((directory/'blind_run_report.json').read_text());spec=json.loads(local_path(case['spec']).read_text())
        result[case['id']]=report_metrics(report,directory/'routed.kicad_pcb',spec)
        reports[case['id']]=report
        details[case['id']]=dict(ground_fill_ratio=report['gnd_pour_stats']['fill_ratio'],artifact_directory=str(directory.relative_to(ROOT)))
    return result,reports,details


def source_snapshot(suite_path):
    result=code_fingerprint();result[str(Path(suite_path).resolve().relative_to(ROOT))]=digest(suite_path)
    return result


def assert_unchanged(snapshot,suite):
    if source_snapshot(suite)!=snapshot:raise RuntimeError('source or suite changed during round; outputs cannot be promoted')
    read_suite(suite)


def sourced_process(argv,log,timeout):
    """A sourced shell precedes every new Python/router toolchain process."""
    with Path(log).open('w') as stream:
        process=subprocess.Popen(['bash','-lc','source scripts/env.sh && exec '+shlex.join([str(v) for v in argv])],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait()
            return 124


def inspect_browser(directory):
    from PIL import Image
    import numpy as np
    rc=sourced_process(['node','scripts/inspect_assembly.mjs',directory],directory/'browser.log',180)
    path=directory/'assembly_browser_report.json'
    report=json.loads(path.read_text()) if path.exists() else dict(passed=False,error='browser report missing')
    for view in ('oblique','top','side'):
        image=directory/f'assembly_{view}.png';report[view+'_pixel_std']=float(np.asarray(Image.open(image).convert('RGB'),dtype=float).std()) if image.exists() else 0.
    report['passed']=rc==0 and report.get('passed') is True and all(report[v+'_pixel_std']>10 for v in ('oblique','top','side'))
    atomic_json(path,report)
    return report


def diagnose(suite,pointer):
    measurements,reports,details=baseline_measurements(suite,pointer);tasks=[]
    for case in suite['cases']:
        if case['id'] not in measurements:continue
        conflicts=input_conflicts(json.loads(local_path(case['spec']).read_text()))
        tasks.extend(triage(case['id'],reports[case['id']],measurements[case['id']],conflicts))
    return dict(status='baseline_diagnosed',created_at=now(),baseline=pointer,measurements=measurements,case_details=details,tasks=tasks,decision=dict(eligible_for_baseline=False,engineering_pass=None,reason='diagnosis only'))


def run_round(args):
    suite=read_suite(args.suite);pointer=active_pointer(suite)
    all_ids={c['id'] for c in suite['cases'] if c['split']=='regression'}
    selected=all_ids if args.cases=='all' else set(args.cases.split(','))
    if not selected or not selected.issubset(all_ids):raise ValueError('run accepts exposed regression IDs only; sealed holdouts cannot be optimization cases')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,47}',args.label):raise ValueError('label must be a short lowercase filename token')
    snapshot=source_snapshot(args.suite)
    experiment = dict(code=snapshot, variant=args.variant, inputs={c['id']:c['spec_sha256'] for c in suite['cases'] if c['id'] in selected}, case_timeout_s=args.case_timeout)
    jvm_runtime = None
    if args.variant in ('freerouting-jvm', 'direct-repair', 'placement-recovery'):
        from cloud_pcb.layout_route.freerouting_runtime import resolve_freerouting_jvm
        jvm_runtime = resolve_freerouting_jvm(ROOT).provenance
        experiment['freerouting_runtime'] = jvm_runtime
    experiment_key = digest_bytes(experiment)
    runs=ROOT/'artifacts/optimization/runs';runs.mkdir(parents=True,exist_ok=True)
    for previous in runs.glob('*/manifest.json'):
        record=json.loads(previous.read_text())
        if record.get('experiment_key')==experiment_key and record.get('status')=='complete':raise ValueError('identical experiment already completed: '+str(previous.parent.relative_to(ROOT))+'; edit a concrete hypothesis implementation before starting another round')
    directory=runs/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+args.label);directory.mkdir()
    (directory/'inputs').mkdir()
    source_archive=directory/'source.tar.gz'
    with tarfile.open(source_archive,'w:gz') as archive:
        for name in sorted(snapshot):archive.add(ROOT/name,arcname=name,recursive=False)
    manifest=dict(schema_version=1,status='running',started_at=now(),hypothesis=args.hypothesis,variant=args.variant,experiment_key=experiment_key,code=snapshot,source_archive_sha256=digest(source_archive),baseline=pointer,python=sys.version,platform=platform.platform(),cases=[])
    if jvm_runtime is not None:
        manifest['freerouting_runtime'] = jvm_runtime
    atomic_json(directory/'manifest.json',manifest)
    baseline,reports,details=baseline_measurements(suite,pointer)
    result=dict(status='running',hypothesis=args.hypothesis,baseline=pointer,measurements={},case_details={},tasks=[],failures=[])
    print(json.dumps(dict(event='round_start',directory=str(directory.relative_to(ROOT)),cases=sorted(selected))),flush=True)
    start=time.monotonic()
    tests_rc=sourced_process(['python','-m','pytest','-v'],directory/'pytest.log',600)
    tests_log=(directory/'pytest.log').read_text()
    manifest['tests']=dict(returncode=tests_rc,passed=tests_rc==0,summary=next((line for line in reversed(tests_log.splitlines()) if re.search(r'\d+ passed',line)),tests_log[-800:]),sha256=digest(directory/'pytest.log'))
    try:
        assert_unchanged(snapshot,args.suite)
        if tests_rc!=0:raise RuntimeError('pytest failed; no expensive board runs started')
        for case in suite['cases']:
            if case['id'] not in selected:continue
            assert_unchanged(snapshot,args.suite);case_id=case['id'];out=directory/case_id;out.mkdir();copied=directory/'inputs'/(case_id+'.json');copied.write_bytes(local_path(case['spec']).read_bytes())
            t0=time.monotonic();entry=dict(id=case_id,outdir=str(out.relative_to(ROOT)),spec_sha256=case['spec_sha256'],status='running')
            print(json.dumps(dict(event='case_start',case=case_id)),flush=True)
            try:
                command=['python','-m','cloud_pcb.general_solver.run_blind','--spec',copied,'--outdir',out]
                if args.variant in ('supply-semantics','scoped-supply'):command.append('--derive-power')
                if args.variant=='scoped-supply':command.append('--scoped-power')
                if args.variant=='octagonal-fanout':command.append('--octagonal-fanout')
                if args.variant=='value-normalize':command.append('--value-normalize')
                if jvm_runtime is not None:
                    command.append('--freerouting-jvm')
                if args.variant in ('direct-repair', 'placement-recovery'):
                    command.append('--direct-repair')
                if args.variant == 'placement-recovery':
                    command.append('--placement-recovery')
                rc=sourced_process(command,out/'solver.log',args.case_timeout)
                if rc!=0:raise RuntimeError('solver process exit '+str(rc))
                report=json.loads((out/'blind_run_report.json').read_text());spec=json.loads(copied.read_text());browser=inspect_browser(out)
                metrics=report_metrics(report,out/'routed.kicad_pcb',spec)
                if not browser['passed']:metrics['evidence_errors'].append('browser assembly verification failed')
                if jvm_runtime is not None:
                    comparison = report.get('freerouting_comparison') or {}
                    if comparison.get('runtime') != jvm_runtime:
                        metrics['evidence_errors'].append('Freerouting JVM runtime differs from the frozen provenance')
                    if comparison.get('status') != 'completed':
                        metrics['evidence_errors'].append('Freerouting comparison did not complete: '+comparison.get('error','missing comparison'))
                conflicts=input_conflicts(spec);result['measurements'][case_id]=metrics
                result['case_details'][case_id]=dict(ground_fill_ratio=report['gnd_pour_stats']['fill_ratio'],artifact_directory=str(out.relative_to(ROOT)),electrical_derivations=report.get('electrical_derivations',[]),input_conflicts=conflicts)
                result['tasks'].extend(triage(case_id,report,metrics,conflicts));atomic_json(out/'metrics.json',metrics)
                entry.update(status='complete',browser_passed=browser['passed'])
            except Exception as exc:
                entry.update(status='failed',error=str(exc));failure=dict(case=case_id,error=str(exc),traceback=traceback.format_exc());result['failures'].append(failure);atomic_json(out/'execution_failure.json',failure)
            assert_unchanged(snapshot,args.suite)
            entry['runtime_s']=round(time.monotonic()-t0,3);entry['artifacts']={str(p.relative_to(ROOT)):digest(p) for p in sorted(out.rglob('*')) if p.is_file()};manifest['cases'].append(entry);atomic_json(directory/'manifest.json',manifest)
            print(json.dumps(dict(event='case_finish',case=case_id,status=entry['status'],runtime_s=entry['runtime_s'])),flush=True)
        result['decision']=compare_candidate(baseline,result['measurements'],full_suite=selected==all_ids and not result['failures'],tests_passed=tests_rc==0)
        manifest['status']='complete' if not result['failures'] else 'incomplete'
    except Exception as exc:
        manifest['status']='invalidated';result['failures'].append(dict(error=str(exc),traceback=traceback.format_exc()));result['decision']=dict(eligible_for_baseline=False,engineering_pass=None,reason=str(exc))
    result['status']=manifest['status'];result['runtime_s']=round(time.monotonic()-start,3);manifest['finished_at']=now();manifest['runtime_s']=result['runtime_s'];manifest['decision']=result['decision']
    for case in suite['cases']:
        copied=directory/'inputs'/(case['id']+'.json')
        if copied.exists():manifest.setdefault('inputs',{})[str(copied.relative_to(ROOT))]=case['spec_sha256']
    try:
        verify_manifest(pointer)
        assert_unchanged(snapshot,args.suite)
    except Exception as exc:
        manifest['status']=result['status']='invalidated'
        result['decision']=manifest['decision']=dict(eligible_for_baseline=False,engineering_pass=None,reason=str(exc))
        result['failures'].append(dict(error=str(exc)))
    atomic_json(directory/'result.json',result);write_review(directory,result)
    manifest['evidence']={str(p.relative_to(ROOT)):digest(p) for p in (directory/'result.json',directory/'review.md',directory/'review.html',directory/'pytest.log',source_archive)}
    atomic_json(directory/'manifest.json',manifest)
    promoted=False
    if result['decision'].get('eligible_for_baseline'):
        state=json.loads(STATE.read_text()) if STATE.exists() else dict(history=[],active_baseline=pointer)
        if state['active_baseline']!=pointer:raise RuntimeError('baseline changed concurrently')
        new_pointer=dict(format='optimization_round',manifest=str((directory/'manifest.json').relative_to(ROOT)),sha256=digest(directory/'manifest.json'))
        state['history'].append(dict(at=now(),previous=pointer,new=new_pointer));state['active_baseline']=new_pointer;atomic_json(STATE,state);promoted=True
    atomic_json(ROOT/'artifacts/optimization/last_run.json',dict(directory=str(directory.relative_to(ROOT)),manifest_sha256=digest(directory/'manifest.json'),promoted=promoted))
    print(json.dumps(dict(event='round_finish',status=manifest['status'],promoted=promoted,regressions=len(result['decision'].get('regressions',[])),improvements=len(result['decision'].get('improvements',[])),directory=str(directory.relative_to(ROOT)))),flush=True)
    return 0 if manifest['status']=='complete' else 1


def digest_bytes(value):
    import hashlib
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--suite',type=Path,default=DEFAULT_SUITE)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('status');sub.add_parser('history')
    run=sub.add_parser('run');run.add_argument('--label',required=True);run.add_argument('--hypothesis',required=True);run.add_argument('--cases',default='all');run.add_argument('--case-timeout',type=int,default=900);run.add_argument('--variant',choices=('stable','supply-semantics','scoped-supply','octagonal-fanout','value-normalize','freerouting-jvm','direct-repair','placement-recovery'),default='stable')
    args=parser.parse_args()
    if os.environ.get('PCB_STUDIO_ROOT')!=str(ROOT):parser.error('run source scripts/env.sh before invoking Python')
    if args.command=='history':
        for path in sorted((ROOT/'artifacts/optimization/runs').glob('*/manifest.json')):
            m=json.loads(path.read_text());print(json.dumps(dict(path=str(path.relative_to(ROOT)),status=m['status'],hypothesis=m['hypothesis'],decision=m.get('decision')),ensure_ascii=False))
        return 0
    suite=read_suite(args.suite)
    if args.command=='status':
        result=diagnose(suite,active_pointer(suite));target=ROOT/'artifacts/optimization/status';target.mkdir(parents=True,exist_ok=True)
        result['last_round'] = latest_round_status()
        atomic_json(target/'result.json',result);write_review(target,result)
        print(json.dumps(dict(cases=len(result['measurements']),tasks=len(result['tasks']),report=str((target/'review.md').relative_to(ROOT)), last_round=result['last_round']),ensure_ascii=False));return 0
    lock=ROOT/'artifacts/optimization/.run.lock';lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open('w') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:parser.error('another optimization round is already running')
        if args.case_timeout<1:parser.error('case timeout must be positive')
        return run_round(args)


if __name__=='__main__':raise SystemExit(main())
