"""Route one immutable Gemini placement with the shared stable routing backend."""
from __future__ import annotations

import argparse
import json
import os
import tarfile
import traceback
from datetime import datetime,timezone
from pathlib import Path

from cloud_pcb.general_solver.optimization import atomic_json,digest,report_metrics
from scripts.optimize_pcb import ROOT,DEFAULT_SUITE,read_suite,baseline_measurements,inspect_browser
from scripts.run_blind_v3 import code_fingerprint


def verify_proposal(directory,spec):
    directory=Path(directory);manifest=json.loads((directory/'reference_manifest.json').read_text())
    if manifest['status']!='placement_generated_unverified':raise ValueError('reference does not contain a complete generated placement')
    requested=manifest.get('requested_model','');reported=manifest.get('reported_model_version','')
    if not requested or not reported or not (reported==requested or reported.startswith(requested+'-')):
        raise ValueError('requested and reported model identity are not verified')
    if digest(spec)!=manifest['spec_sha256']:raise ValueError('reference input hash differs')
    for name,sha in manifest['artifacts'].items():
        path=(directory/name).resolve()
        if not path.is_relative_to(directory.resolve()) or digest(path)!=sha:raise ValueError('reference artifact changed or escaped directory')
    placement=json.loads((directory/'placement.json').read_text())
    return manifest,placement


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--reference',type=Path,required=True);parser.add_argument('--spec',type=Path,required=True);args=parser.parse_args()
    if os.environ.get('PCB_STUDIO_ROOT')!=str(ROOT):parser.error('source scripts/env.sh before Python')
    manifest,placement=verify_proposal(args.reference,args.spec)
    destination=ROOT/'artifacts/gemini_routed_reference'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');destination.mkdir(parents=True)
    code=code_fingerprint();source=destination/'source.tar.gz'
    with tarfile.open(source,'w:gz') as archive:
        for name in sorted(code):archive.add(ROOT/name,arcname=name,recursive=False)
    record=dict(status='running',reference_kind='independent Gemini placement with shared stable routing backend',requested_model=manifest['requested_model'],reported_model_version=manifest.get('reported_model_version'),proposal_manifest=str(args.reference/'reference_manifest.json'),proposal_manifest_sha256=digest(args.reference/'reference_manifest.json'),spec_sha256=digest(args.spec),placement_sha256=digest(args.reference/'placement.json'),source=code,source_archive_sha256=digest(source),backend_variant='stable',coordinates_repaired=False,engineering_pass=None)
    atomic_json(destination/'reference_route_manifest.json',record)
    try:
        from cloud_pcb.general_solver.run_blind import run_blind_solver
        out=destination/'board';result=run_blind_solver(args.spec,out,external_placement=placement)
        # Save and verify the exact model coordinates, rather than fixing model
        # mistakes with internal placement or silently borrowing the baseline.
        for ref,data in placement['placements'].items():
            actual=result['placed_positions'][ref]
            if abs(actual[0]-data['x_mm'])>1e-9 or abs(actual[1]-data['y_mm'])>1e-9 or abs((actual[2]-data['angle_deg']+180)%360-180)>1e-9:raise RuntimeError('model coordinates changed during routing')
        browser=inspect_browser(out);spec=json.loads(args.spec.read_text());report=json.loads((out/'blind_run_report.json').read_text());metrics=report_metrics(report,out/'routed.kicad_pcb',spec)
        if not browser['passed']:metrics['evidence_errors'].append('reference browser verification failed')
        comparison=dict(model=metrics,model_name=record['reported_model_version'],reference_kind=record['reference_kind'],shared_backend=True,engineering_pass=None)
        suite=read_suite(DEFAULT_SUITE);case=next((c for c in suite['cases'] if c['spec_sha256']==record['spec_sha256'] and c['split']=='regression'),None)
        if case:
            baseline=baseline_measurements(suite,suite['baseline'])[0][case['id']]
            comparison.update(dataset=case['id'],dataset_split='exposed_regression',solver_baseline=baseline,solver_baseline_manifest=suite['baseline'])
        atomic_json(destination/'comparison.json',comparison)
        if code_fingerprint()!=code:raise RuntimeError('source changed during reference route')
        verify_proposal(args.reference,args.spec)
        if digest(args.spec)!=record['spec_sha256'] or digest(args.reference/'reference_manifest.json')!=record['proposal_manifest_sha256']:
            raise RuntimeError('reference provenance changed during routing')
        record.update(status='complete',hard_gates_passed=report['hard_gates_passed'],browser_passed=browser['passed'],unrouted=metrics['values']['unrouted'],lvs_opens=metrics['values']['opens'],lvs_shorts=metrics['values']['shorts'],metrics=metrics)
    except Exception as exc:record.update(status='failed',error=str(exc),traceback=traceback.format_exc())
    record['artifacts']={str(p.relative_to(destination)):digest(p) for p in sorted(destination.rglob('*')) if p.is_file() and p.name!='reference_route_manifest.json'}
    atomic_json(destination/'reference_route_manifest.json',record)
    print(json.dumps(dict(directory=str(destination.relative_to(ROOT)),status=record['status'],hard_gates_passed=record.get('hard_gates_passed'),unrouted=record.get('unrouted'),lvs_opens=record.get('lvs_opens'),lvs_shorts=record.get('lvs_shorts'),coordinates_repaired=False),ensure_ascii=False))
    return 0 if record['status']=='complete' else 2


if __name__=='__main__':raise SystemExit(main())
