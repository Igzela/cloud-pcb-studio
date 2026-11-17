"""Uniform post-freeze reference preview extraction, including legacy ref text."""
import copy,hashlib,json,shutil,subprocess,sys
from pathlib import Path
ROOT=Path.cwd();sys.path.insert(0,str(ROOT))
from kiutils.board import Board
from kiutils.items.brditems import Segment,Via,Arc
from cloud_pcb.general_solver.compare_reference import _board_bounds
from cloud_pcb.general_solver.assembly import export_assembly
from cloud_pcb.general_solver.semantics import mechanical_check
from scripts.run_blind_v3 import code_fingerprint
from PIL import Image,ImageDraw
base=ROOT/'artifacts/blind_v3_comparison';data=json.loads((base/'all_cases.json').read_text());freeze=json.loads(Path('artifacts/blind_v3_freeze.json').read_text());assert freeze['status']=='frozen' and code_fingerprint()==freeze['code']
for result in data['cases']:
    number=result['case'];directory=base/f'case{number}';target=directory/'reference_3d';previous=json.loads((target/'assembly_browser_report.json').read_text());result['reference_3d_initial_browser_report']=previous
    if not previous['passed']:shutil.copyfile(target/'assembly_browser_report.json',ROOT/f'artifacts/v3_validation/reference-case{number}-initial-browser-failure.json')
    spec=json.loads((ROOT/'benchmark_case'/('blind_spec.json' if number==1 else f'blind_spec_case{number}.json')).read_text());b=Board.from_file(str(directory/'reference_for_analysis.kicad_pcb'));ox,oy,_,_=_board_bounds(b)
    for f in b.footprints:
        if not f.properties.get('Reference'):
            ref=next((str(g.text) for g in f.graphicItems if getattr(g,'type','')=='reference'),'')
            if ref:f.properties['Reference']=ref
        f.position.X-=ox;f.position.Y-=oy
    for t in b.traceItems:
        for attr in (('position',) if isinstance(t,Via) else ('start','end') if isinstance(t,Segment) else ('start','mid','end') if isinstance(t,Arc) else ()):
            point=getattr(t,attr);point.X-=ox;point.Y-=oy
    for z in b.zones:
        for q in z.filledPolygons:
            for point in q.coordinates:point.X-=ox;point.Y-=oy
    placed={f.properties.get('Reference',''):(f.position.X,f.position.Y,float(f.position.angle or 0)) for f in b.footprints};refspec=copy.deepcopy(spec);sides={f.properties.get('Reference',''):f.layer for f in b.footprints}
    for c in refspec['components']:c['side']='bottom' if sides.get(c['refdes'])=='B.Cu' else 'top'
    assert all(c['refdes'] in placed for c in refspec['components'])
    assembly=export_assembly(b,refspec,placed,target);assert assembly['component_models']==len(spec['components'])
    result['comparison']['human_reference']['independent_4layer']['mechanical']=assembly['mechanical']
    proc=subprocess.run(['node','scripts/inspect_assembly.mjs',str(target)],capture_output=True,text=True,timeout=180)
    br=json.loads((target/'assembly_browser_report.json').read_text());result['reference_3d_returncode']=proc.returncode;result['reference_3d_browser_report']=br
    assert proc.returncode==0 and br['passed'],br
    pair=Image.new('RGB',(2880,1040),'#101820');draw=ImageDraw.Draw(pair)
    for column,(label,path) in enumerate((('Blind v3',ROOT/f'artifacts/blind_v3_case{number}/assembly_side.png'),('Human reference (same parametric library)',target/'assembly_side.png'))):
        pair.paste(Image.open(path).convert('RGB'),(column*1440,40));draw.text((column*1440+20,12),label,fill='white')
    pair.save(directory/'comparison_side.png');(directory/'comparison.json').write_text(json.dumps(result['comparison'],indent=2)+'\n');(directory/'evaluation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(case=number,reference_component_models=assembly['component_models'],browser_passed=br['passed'])),flush=True)
data['reference_preview_script_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();(base/'all_cases.json').write_text(json.dumps(data,indent=2)+'\n');shutil.copyfile(__file__,base/'reference_previews.py');assert code_fingerprint()==freeze['code']
