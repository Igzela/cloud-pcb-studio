"""Request an independent anonymous Gemini placement proposal, not a DRC pass."""
import argparse
import hashlib
import json
import os
from datetime import datetime,timezone
from pathlib import Path

from cloud_pcb.general_solver.gemini_reference import DEFAULT_MODEL,anonymous_spec,reference_request,call_gemini,decode_placement
from cloud_pcb.general_solver.optimization import atomic_json,digest
from scripts.optimize_pcb import ROOT


def retry_lineage(path, anonymous_sha, model, request):
    path=Path(path)
    previous=json.loads((path/'reference_manifest.json').read_text())
    if previous['status'] not in ('unavailable','invalid_model_output'):
        raise ValueError('cannot replace a generated placement by resampling')
    if previous['anonymous_input_sha256']!=anonymous_sha or previous['requested_model']!=model:
        raise ValueError('retry must preserve anonymous input and model identity')
    original=json.loads((path/'request.json').read_text())
    if original['contents']!=request['contents']:
        raise ValueError('retry must preserve the original task prompt')
    if previous['status']=='invalid_model_output' and original.get('generationConfig')==request.get('generationConfig'):
        raise ValueError('invalid output requires a concrete request configuration correction')
    return dict(manifest=str(path/'reference_manifest.json'),sha256=digest(path/'reference_manifest.json'),original_status=previous['status'],prompt_unchanged=True,original_generation_config=original.get('generationConfig'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',type=Path,required=True)
    parser.add_argument('--model',default=DEFAULT_MODEL)
    parser.add_argument('--key-file',type=Path)
    parser.add_argument('--timeout',type=int,default=120)
    parser.add_argument('--attempts',type=int,default=3)
    parser.add_argument('--max-output-tokens',type=int,default=32768)
    parser.add_argument('--thinking-level',choices=('low','medium','high'),default='low')
    parser.add_argument('--retry-of',type=Path)
    args=parser.parse_args()
    if os.environ.get('PCB_STUDIO_ROOT')!=str(ROOT):parser.error('source scripts/env.sh before Python')
    key=os.environ.get('GEMINI_API_KEY','')
    if args.key_file:
        if args.key_file.stat().st_mode & 0o077:parser.error('credential file must have mode 0600')
        key=args.key_file.read_text().strip()
    if not key:parser.error('set GEMINI_API_KEY or provide a private --key-file')
    original=json.loads(args.spec.read_text());anonymous,mapping=anonymous_spec(original);request=reference_request(anonymous,max_output_tokens=args.max_output_tokens,thinking_level=args.thinking_level)
    directory=ROOT/'artifacts/gemini_reference'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');directory.mkdir(parents=True)
    atomic_json(directory/'anonymous_spec.json',anonymous);atomic_json(directory/'identity_mapping.json',mapping);atomic_json(directory/'request.json',request)
    lineage=retry_lineage(args.retry_of,digest(directory/'anonymous_spec.json'),args.model,request) if args.retry_of else None
    reply=call_gemini(request,key,model=args.model,timeout=args.timeout,attempts=args.attempts);atomic_json(directory/'model_reply.json',reply)
    import cloud_pcb.general_solver.gemini_reference as protocol
    result=dict(status=reply['status'],requested_model=args.model,reported_model_version=reply.get('reported_model_version'),spec_sha256=digest(args.spec),anonymous_input_sha256=digest(directory/'anonymous_spec.json'),response_sha256=digest(directory/'model_reply.json'),protocol_sha256=digest(protocol.__file__),runner_sha256=digest(__file__),generation_config=request['generationConfig'],retry_of=lineage,reference_kind='independent placement proposal; not a routed PCB',engineering_pass=None)
    if reply['status']=='generated':
        try:
            placement=decode_placement(reply,anonymous,mapping);atomic_json(directory/'placement.json',placement);result['status']=placement['status']
        except (ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:result.update(status='invalid_model_output',error=str(exc))
    result['artifacts']={str(p.relative_to(directory)):digest(p) for p in directory.iterdir() if p.is_file()}
    atomic_json(directory/'reference_manifest.json',result)
    print(json.dumps(dict(directory=str(directory.relative_to(ROOT)),**result),ensure_ascii=False))
    return 0 if result['status']=='placement_generated_unverified' else 2


if __name__=='__main__':raise SystemExit(main())
