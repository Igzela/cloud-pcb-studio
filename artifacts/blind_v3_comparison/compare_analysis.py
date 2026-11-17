"""Read-only post-freeze evaluation. Does not change the solver or frozen outputs."""
import copy
import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path.cwd();sys.path.insert(0,str(ROOT))
from reference_adapter import adapt_reference
from kiutils.board import Board
from kiutils.items.brditems import Segment,Via,Arc
from cloud_pcb.general_solver.compare_reference import compare_boards,_extract_metrics,_board_bounds
from cloud_pcb.general_solver.assembly import export_assembly
from cloud_pcb.general_solver.semantics import mechanical_check
from scripts.run_blind_v3 import code_fingerprint
from shapely.geometry import Polygon
from cloud_pcb.verify_gate.gerber_drc import _gerber_layer_union
from shapely.ops import unary_union

def ground_fill(path, area):
    board=Board.from_file(str(path));nets={n.number:n.name for n in board.nets};layers={}
    for z in board.zones:
        if nets.get(z.net,z.netName).upper() not in {'GND','AGND','DGND','PGND','VSS'}: continue
        for p in z.filledPolygons:
            points=[(q.X,q.Y) for q in p.coordinates]
            if len(points)>=3:layers.setdefault(p.layer,[]).append(Polygon(points).buffer(0))
    return {k:round(unary_union(v).area/area,6) for k,v in layers.items()}


manifest=json.loads((ROOT/'artifacts/blind_v3_freeze.json').read_text())
assert manifest['status']=='frozen' and code_fingerprint()==manifest['code']
for case in (4,5): assert json.loads((ROOT/f'benchmark_case/sealed_case{case}/manifest.json').read_text())['status']=='unsealed'
refs={1:'benchmark_case/sealed_reference/can_sensor_board_kicad/Sensor_Board.kicad_pcb',2:'benchmark_case/sealed_case2/Rev2/KiCad/flight_controller_rev2.kicad_pcb',3:'benchmark_case/sealed_case3/pcb_design/Driver/Driver Example.kicad_pcb',4:'benchmark_case/sealed_case4/reference.kicad_pcb',5:'benchmark_case/sealed_case5/reference.kicad_pcb'}
old={1:'artifacts/blind_benchmark_v2',2:'artifacts/blind_case2',3:'artifacts/blind_case3'}
base=ROOT/'artifacts/blind_v3_comparison';base.mkdir(exist_ok=True)
results=[]
for number in range(1,6):
    directory=base/f'case{number}';directory.mkdir(exist_ok=True)
    spec_path=ROOT/'benchmark_case'/('blind_spec.json' if number==1 else f'blind_spec_case{number}.json')
    spec=json.loads(spec_path.read_text());out=ROOT/f'artifacts/blind_v3_case{number}';run=json.loads((out/'blind_run_report.json').read_text())
    result=dict(case=number,run=run,browser=json.loads((out/'assembly_browser_report.json').read_text()))
    gerber_rf=[]
    for region in run['keepouts']:
        if region.get('kind')!='rf':continue
        geom=Polygon([(x,float(spec['board_outline_mm']['height'])-y) for x,y in region['polygon_mm']]);coverage={}
        for layer in ('F.Cu','In1.Cu','In2.Cu','B.Cu'):
            copper,_,_=_gerber_layer_union(out/'gerbers'/(layer.replace('.','_')+'.gbr'))
            overlap=copper.intersection(geom).area
            coverage[layer]=dict(copper_area_mm2=overlap,coverage_percent=100*overlap/max(geom.area,1e-12))
        gerber_rf.append(dict(refdes=region['refdes'],layers=coverage,passed=all(v['copper_area_mm2']<1e-8 for v in coverage.values())))
    result['exported_gerber_rf']=gerber_rf
    try:
        reference_path=directory/'reference_for_analysis.kicad_pcb'
        result['reference_format_adapter']=adapt_reference(ROOT/refs[number],reference_path)
        compare=compare_boards(out/'routed.kicad_pcb',reference_path,directory/'comparison.json',directory/'comparison_2d.png',spec_path,title_blind=f'Blind v3 — Case {number}',title_ref=f'Human reference — Case {number}')
        for side in ('blind_solver','human_reference'):
            met=compare[side];opens={x['net'] for x in met.get('independent_4layer',{}).get('lvs_opens',[])}
            for pair in met['diff_pairs']:
                pair['valid_connected_pair']=pair['pos_len_mm']>0 and pair['neg_len_mm']>0 and pair['pos_net'] not in opens and pair['neg_net'] not in opens
        result['comparison']=compare
        for side,path in (('blind_solver',out/'routed.kicad_pcb'),('human_reference',reference_path)):
            compare[side]['serialized_gnd_fill_by_layer']=ground_fill(path,run['gnd_pour_stats']['board_area_mm2'])
        # Reference assembly uses the same neutral package library, normalized
        # to the allowed board frame. Native reference models are not assumed.
        b=Board.from_file(str(reference_path));ox,oy,_,_=_board_bounds(b)
        for f in b.footprints: f.position.X-=ox;f.position.Y-=oy
        for t in b.traceItems:
            for attr in (('position',) if isinstance(t,Via) else ('start','end') if isinstance(t,Segment) else ('start','mid','end') if isinstance(t,Arc) else ()):
                p=getattr(t,attr);p.X-=ox;p.Y-=oy
        for z in b.zones:
            for q in z.filledPolygons:
                for p in q.coordinates:p.X-=ox;p.Y-=oy
        placed={f.properties.get('Reference',''):(f.position.X,f.position.Y,float(f.position.angle or 0)) for f in b.footprints}
        ref_spec=copy.deepcopy(spec);sides={f.properties.get('Reference',''):f.layer for f in b.footprints}
        for c in ref_spec['components']:c['side']='bottom' if sides.get(c['refdes'])=='B.Cu' else 'top'
        compare['human_reference']['independent_4layer']['mechanical']=mechanical_check(ref_spec,placed)
        export_assembly(b,ref_spec,placed,directory/'reference_3d')
        proc=subprocess.run(['node','scripts/inspect_assembly.mjs',str(directory/'reference_3d')],capture_output=True,text=True,timeout=180)
        result['reference_3d_returncode']=proc.returncode
        from PIL import Image, ImageDraw
        pair=Image.new('RGB',(2880,1040),'#101820');draw=ImageDraw.Draw(pair)
        for column,(label,image) in enumerate((('Blind v3',out/'assembly_side.png'),('Human reference (same parametric library)',directory/'reference_3d/assembly_side.png'))):
            if image.exists():pair.paste(Image.open(image).convert('RGB'),(column*1440,40))
            draw.text((column*1440+20,12),label,fill='white')
        pair.save(directory/'comparison_side.png')
        (directory/'comparison.json').write_text(json.dumps(compare,indent=2)+'\n')
        if number in old:
            previous=ROOT/old[number]
            result['v2_report']=json.loads((previous/'blind_run_report.json').read_text())
            result['v2_measured_with_current_metrics']=_extract_metrics(previous/'routed.kicad_pcb','v2 under v3 audit',spec=spec)
    except Exception as e:
        import traceback
        result['evaluation_error']=dict(error=type(e).__name__,message=str(e),traceback=traceback.format_exc())
    results.append(result)
    (directory/'evaluation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(case=number,error=result.get('evaluation_error'),compared='comparison' in result)),flush=True)
summary=dict(source_commit=manifest['source_commit'],frozen_at=manifest['frozen_at'],analysis_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),cases=results)
(base/'all_cases.json').write_text(json.dumps(summary,indent=2)+'\n')
shutil.copyfile(__file__,base/'compare_analysis.py')
shutil.copyfile(Path(__file__).with_name('reference_adapter.py'),base/'reference_adapter.py')
assert code_fingerprint()==manifest['code']
