"""Uniform sequential run, immutable artifact manifest and controlled unsealing.

Invoke only after sourcing scripts/env.sh. Reference files are not opened here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import traceback
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def code_fingerprint():
    paths=[]
    for base in ('cloud_pcb','contracts','scripts','tests'):
        paths.extend(p for p in (ROOT/base).rglob('*') if p.suffix in ('.py','.mjs','.sh') and '__pycache__' not in p.parts)
    paths.extend(ROOT/n for n in ('requirements.txt','package.json','package-lock.json','bin/fastroute','bin/freerouting-cli','bin/chromium'))
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def run_all():
    from cloud_pcb.general_solver.run_blind import run_blind_solver
    from PIL import Image
    import numpy as np
    manifest=dict(started_at=datetime.now(timezone.utc).isoformat(),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),code=code_fingerprint(),status='running',cases=[])
    target=ROOT/'artifacts/blind_v3_freeze.json'
    target.write_text(json.dumps(manifest,indent=2)+'\n')
    for case in range(1,6):
        assert code_fingerprint()==manifest['code'],'code changed during blind run'
        spec=ROOT/'benchmark_case'/('blind_spec.json' if case==1 else f'blind_spec_case{case}.json')
        out=ROOT/'artifacts'/f'blind_v3_case{case}'
        if out.exists(): shutil.rmtree(out)
        out.mkdir(parents=True,exist_ok=True)
        started=time.monotonic(); entry=dict(case=case,spec_sha256=hashlib.sha256(spec.read_bytes()).hexdigest(),outdir=str(out.relative_to(ROOT)))
        print(json.dumps(dict(event='start',case=case)),flush=True)
        try:
            report=run_blind_solver(spec,out)
            browser=subprocess.run(['node','scripts/inspect_assembly.mjs',str(out)],cwd=ROOT,capture_output=True,text=True,timeout=180)
            brpath=out/'assembly_browser_report.json'
            br=json.loads(brpath.read_text()) if brpath.exists() else dict(passed=False,error=browser.stderr)
            for view in ('oblique','top','side'):
                image=out/f'assembly_{view}.png'
                br[f'{view}_pixel_std']=float(np.asarray(Image.open(image).convert('RGB'),dtype=float).std()) if image.exists() else 0
            br['passed']=br.get('passed',False) and all(br[f'{v}_pixel_std']>10 for v in ('oblique','top','side'))
            brpath.write_text(json.dumps(br,indent=2)+'\n')
            entry.update(status='complete',browser_passed=br['passed'],unrouted_nets=report['bakeoff'][0].unrouted_nets)
        except Exception as exc:
            error=dict(case=case,error=type(exc).__name__,message=str(exc),traceback=traceback.format_exc())
            (out/'execution_failure.json').write_text(json.dumps(error,indent=2)+'\n')
            entry.update(status='failed',error=str(exc))
        entry['runtime_s']=round(time.monotonic()-started,3)
        entry['artifacts']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}
        manifest['cases'].append(entry);target.write_text(json.dumps(manifest,indent=2)+'\n')
        print(json.dumps(dict(event='finish',case=case,status=entry['status'],runtime_s=entry['runtime_s'])),flush=True)
    assert code_fingerprint()==manifest['code'],'code changed during blind run'
    manifest['status']='frozen' if all(c['status']=='complete' for c in manifest['cases']) else 'incomplete'
    manifest['frozen_at']=datetime.now(timezone.utc).isoformat()
    target.write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def unseal():
    manifest=json.loads((ROOT/'artifacts/blind_v3_freeze.json').read_text())
    assert manifest['status']=='frozen' and len(manifest['cases'])==5,'all five complete outputs must be frozen first'
    assert code_fingerprint()==manifest['code'],'code changed after freeze'
    for case in manifest['cases']:
        spec=ROOT/'benchmark_case'/('blind_spec.json' if case['case']==1 else f"blind_spec_case{case['case']}.json")
        assert hashlib.sha256(spec.read_bytes()).hexdigest()==case['spec_sha256'],'blind input changed after freeze'
        for name,sha in case['artifacts'].items():
            assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha,f'frozen artifact changed: {name}'
    for case in (4,5):
        directory=ROOT/'benchmark_case'/f'sealed_case{case}'
        record=json.loads((directory/'manifest.json').read_text())
        reference=ROOT/record['reference_path'];reference.chmod(0o400)
        assert hashlib.sha256(reference.read_bytes()).hexdigest()==record['reference_sha256']
        record.update(status='unsealed',unsealed_at=datetime.now(timezone.utc).isoformat(),freeze_manifest='artifacts/blind_v3_freeze.json')
        (directory/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    print('References unsealed after verifying all five artifact and code hashes.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--unseal',action='store_true');args=parser.parse_args()
    if args.unseal: unseal()
    else: run_all()
