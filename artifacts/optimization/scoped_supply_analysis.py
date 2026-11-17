"""Post-round analysis only. Never edits frozen source, inputs or case outputs."""
import hashlib,json,re,tarfile,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from scripts.optimize_pcb import read_suite,verify_manifest,source_snapshot,active_pointer,baseline_measurements
from cloud_pcb.general_solver.optimization import atomic_json,digest
ROOT=Path.cwd()
last=json.loads(Path('artifacts/optimization/last_run.json').read_text());directory=Path(last['directory']);manifest=json.loads((directory/'manifest.json').read_text());result=json.loads((directory/'result.json').read_text())
assert manifest['status']=='complete' and manifest['variant']=='scoped-supply'
assert source_snapshot('benchmark_case/optimization_suite.json')==manifest['code']
pointer=dict(format='optimization_round',manifest=str(directory/'manifest.json'),sha256=digest(directory/'manifest.json'))
assert pointer['sha256']==last['manifest_sha256'];verify_manifest(pointer)
suite=read_suite('benchmark_case/optimization_suite.json');verify_manifest(suite['baseline']);verify_manifest(manifest['baseline'])
previous_pointer=dict(format='optimization_round',manifest='artifacts/optimization/runs/20261117T074914645440Z-supply-semantics/manifest.json',sha256='d8e9fd5eb915702dec47fd592f9ff6e83d6f5200034758fe0b568f9b85fad978');verify_manifest(previous_pointer)
previous=json.loads(Path(previous_pointer['manifest']).with_name('result.json').read_text());baseline=dict(measurements=baseline_measurements(suite,suite['baseline'])[0])
assert active_pointer(suite)==(pointer if last['promoted'] else manifest['baseline'])
with tarfile.open(directory/'source.tar.gz') as archive:
    for name,sha in manifest['code'].items():assert hashlib.sha256(archive.extractfile(name).read()).hexdigest()==sha
rows=[];supplies=[];browser=[];safety=[]
for record in manifest['cases']:
    case=record['id'];out=Path(record['outdir']);report=json.loads((out/'blind_run_report.json').read_text());br=json.loads((out/'assembly_browser_report.json').read_text());measure=result['measurements'][case]
    assert br['passed'] and br['componentCount']==report['assembly']['component_models']
    assert all(br[v+'_pixel_std']>10 for v in ('oblique','top','side'))
    assert not measure['evidence_errors']
    b=baseline['measurements'][case]['values'];p=previous['measurements'][case]['values'];c=measure['values']
    rows.append(f"| {case} | {b['unrouted']} / {p['unrouted']} / {c['unrouted']} | {b['opens']} / {p['opens']} / {c['opens']} | {b['cap_distance_mm']:.3f} / {p['cap_distance_mm']:.3f} / {c['cap_distance_mm']:.3f} | {report['gnd_pour_stats']['fill_ratio']*100:.2f}% |")
    for net,info in report['power_routing_intent'].items():
        supplies.append(f"| {case} | `{net}` | {info['routing_scope']} | {info['target_width_mm']:.3f} | {info['current_verified']} |")
    safety.append(dict(case=case,shorts=c['shorts'],spacing=c['spacing'],via_in_pad=c['via_in_pad'],gerber_failures=c['gerber_failures'],failed_gates=measure['failed_gates']))
    browser.append(dict(case=case,passed=br['passed'],component_models=br['componentCount'],console_errors=br['console_errors']))
state='已晋升为迭代基线' if last['promoted'] else '未晋升，原基线保留'
summary=['# 局部供电域优化：第二轮实测','',f"结论：**{state}**。这表示与迭代基线的逐板比较结果；工程验收仍未认证，不能宣称已经达到 8–8.5 成目标。",'',
f"全量测试：`{manifest['tests']['summary'].strip('= ')}`。本轮执行时间 `{manifest['runtime_s']:.2f} s`。五板均为已暴露回归数据，不属于新陌生盲测。",'',
'本轮采用同一 `scoped-supply` 命令运行全部五板。局部域须有单一有类型供电脚 IC、回流电容及可辨识 R/L/C 端点，并验证网表与焊盘端点一致。多 IC、外部接口或证据不足保持骨干规则；未按案例选择变体。', '',
'未知电流局部域的默认 0.25 mm 是实验路由意图，骨干保持至少 0.50 mm。明确载流和最小宽度要求始终优先；同时修复 max_current_a 为 null 时正确回退到已提供 current_a 的处理。制造最小线宽、间距、孔环及所有原门禁没有放宽。','',
'## 与稳定基线、上轮宽主干候选比较','',
'每列三个数依次是：稳定 v3 基线 / 上轮 supply-semantics / 本轮 scoped-supply。未布通是引擎连接计数，LVS 开路是独立网络计数，两者可以不同。','',
'| Case | 未布通：基线/上轮/本轮 | LVS 开路：基线/上轮/本轮 | 电容距离代理 mm：基线/上轮/本轮 | 本轮 In1 地填充 |','| --- | --- | --- | --- | --- |',*rows,'',
'电容距离代理可能包含 bulk 电容；未布通板的较短线长不能算作改善。即使某项较好，也不能抵消其他板退步。','',
'## 自动判定原文','', '```json',json.dumps(result['decision'],ensure_ascii=False,indent=2),'```','',
'## 逐网供电意图','', '| Case | 网络身份 | 范围 | 目标宽度 mm | 电流已提供 |','| --- | --- | --- | --- | --- |',*supplies,'',
'这里的范围依据来自盲规范，所有网络名仅用于报告身份。局部域可以含滤波电感及反馈电阻，拓扑范围不等于物理回路已经紧凑，仍需电流、热与布局验证。未知电流没有被填成额定值。','',
'## 原始证据','',
f"[本轮完整比较](../{directory}/review.md) · [离线看板](../{directory}/review.html) · [pytest -v 日志](../{directory}/pytest.log) · [冻结清单](../{directory}/manifest.json)",'',
'[流程说明](optimization-workflow.zh-CN.md) · [首轮记录](optimization-first-round.zh-CN.md)','']
Path('docs/optimization-scoped-supply.zh-CN.md').write_text('\n'.join(summary))
integrity=dict(status='verified',round=pointer,source_files=len(manifest['code']),case_artifacts=sum(len(c['artifacts']) for c in manifest['cases']),source_archive_matches=True,input_and_artifact_hashes_match=True,historical_v3_and_previous_round_unchanged=True,promoted=last['promoted'],active_baseline=active_pointer(suite),pytest=manifest['tests'],safety=safety,browser=browser)
atomic_json('artifacts/optimization/scoped-supply-integrity.json',integrity)
atomic_json('artifacts/optimization/next_hypothesis.json',dict(status='proposed_not_implemented',completed_hypothesis='single-IC supply scope',evidence_manifest=pointer,title='Use geometry-aware congestion evidence and generic escape/rip-up strategy for remaining connectivity failures',remaining_connections={case:m['values']['unrouted'] for case,m in result['measurements'].items() if m['values']['unrouted']},regressions=result['decision']['regressions'],constraints=['all exposed cases remain regression only','no project-specific nets or coordinates','do not loosen DRC or erase failure evidence','current limits and envelope dimensions remain unverified where missing','full suite before baseline promotion']))
print(json.dumps(dict(report='docs/optimization-scoped-supply.zh-CN.md',promoted=last['promoted'],cases=len(manifest['cases']),verified=True),ensure_ascii=False))
