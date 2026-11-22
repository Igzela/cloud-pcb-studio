"""Self-contained WebGL assembly export, sharing physical envelopes with MCAD checks."""
from __future__ import annotations

import html
import json
import subprocess
from pathlib import Path

from kiutils.items.brditems import Segment, Via

from cloud_pcb.general_solver.semantics import component_elevation, component_envelope, mechanical_check

ROOT=Path(__file__).resolve().parents[2]


def assembly_scene(board,spec,placed):
    from cloud_pcb.general_solver.run_blind import _pad_world_polygon_kicad
    nets={n.number:n.name for n in board.nets}; comps={c['refdes']:c for c in spec['components']}
    scene=dict(outline=spec['board_outline_mm'],thickness_mm=float(board.general.thickness),components=[],pads=[],tracks=[],vias=[],holes=[],zones=[])
    for fp in board.footprints:
        ref=fp.properties.get('Reference',''); comp=comps.get(ref)
        if comp:
            env = component_envelope(comp)
            side = comp.get('side', 'top')
            elevation = component_elevation(
                side=side, board_thickness_mm=scene['thickness_mm'],
                height_mm=env['height_mm'], standoff_mm=float(comp.get('standoff_mm', 0)),
            )
            scene['components'].append(dict(refdes=ref, value=comp.get('value', ''),
                                            position=placed[ref], side=side, **env, **elevation))
        for p in fp.pads:
            if p.type=='np_thru_hole':
                from cloud_pcb.general_solver.geometry import drill_geometry
                g=drill_geometry(fp,p)
                scene['holes'].append(dict(x=g.centroid.x,y=g.centroid.y,drill_mm=float(p.drill.diameter),polygon=list(g.exterior.coords)))
                continue
            if not any(l.endswith('.Cu') or l=='*.Cu' for l in p.layers): continue
            g=_pad_world_polygon_kicad(fp,p)
            scene['pads'].append(dict(refdes=ref,num=p.number,net=p.net.name if p.net else '',polygon=list(g.exterior.coords),layers=p.layers))
            if p.type=='thru_hole' and p.drill:
                from cloud_pcb.general_solver.geometry import drill_geometry
                drill=drill_geometry(fp,p)
                scene['holes'].append(dict(x=drill.centroid.x,y=drill.centroid.y,drill_mm=float(p.drill.diameter),polygon=list(drill.exterior.coords)))
    for t in board.traceItems:
        if isinstance(t,Segment): scene['tracks'].append(dict(start=[t.start.X,t.start.Y],end=[t.end.X,t.end.Y],width=t.width,layer=t.layer,net=nets.get(t.net,'')))
        elif isinstance(t,Via): scene['vias'].append(dict(x=t.position.X,y=t.position.Y,size=t.size,drill=t.drill,net=nets.get(t.net,'')))
    for z in board.zones:
        if z.keepoutSettings: continue
        for polygon in z.filledPolygons:
            scene['zones'].append(dict(net=nets.get(z.net,z.netName),layer=polygon.layer,polygon=[[p.X,p.Y] for p in polygon.coordinates]))
    scene['mechanical'] = mechanical_check(spec, placed, board_thickness_mm=scene['thickness_mm'])
    return scene


def export_assembly(board,spec,placed,outdir):
    outdir=Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    scene=assembly_scene(board,spec,placed)
    (outdir/'assembly_scene.json').write_text(json.dumps(scene,indent=2)+'\n')
    bundle=ROOT/'.run/assembly-viewer.js'
    source=ROOT/'cloud_pcb/general_solver/assembly_viewer.mjs'
    bundle.parent.mkdir(exist_ok=True)
    subprocess.run([str(ROOT/'node_modules/.bin/esbuild'),str(source),'--bundle','--format=iife','--minify',f'--outfile={bundle}'],cwd=ROOT,check=True,capture_output=True)
    payload=json.dumps(scene).replace('<','\\u003c')
    js=bundle.read_text().replace('</script','<\\/script')
    text='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>PCB Assembly</title>
<style>html,body{margin:0;height:100%;background:#101820;color:#eef2f7;font:14px system-ui}canvas{display:block}#panel{position:absolute;top:16px;left:16px;background:#172431ee;padding:16px;border-radius:8px;max-width:330px}label{display:inline-block;margin:4px}#hover{position:absolute;bottom:24px;left:24px;background:#172431ee;padding:12px}button{margin:4px;padding:6px}</style>
<div id="panel"><b>PCB · 3D 装配</b><p>拖动旋转 · 滚轮缩放 · 右键平移</p><div id="layers"></div><label>阻焊 <select id="mask-color"><option value="green">绿油</option><option value="black">黑油</option></select></label><br><button data-view="oblique">斜视</button><button data-view="top">俯视</button><button data-view="side">侧视</button><p id="status"></p><small>封装外形为参数包络；尺寸来源与未知高度见 assembly_scene.json。</small></div><div id="hover">悬浮查看器件与网络</div>'''
    text+=f'<script id="scene-data" type="application/json">{payload}</script><script>{js}</script></html>'
    (outdir/'assembly.html').write_text(text)
    return dict(html=str(outdir/'assembly.html'),scene=str(outdir/'assembly_scene.json'),component_models=len(scene['components']),mechanical=scene['mechanical'])
