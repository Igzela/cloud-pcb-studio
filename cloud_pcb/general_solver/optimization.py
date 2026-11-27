"""Evidence-based PCB optimization metrics, promotion policy and failure triage.

No reference layouts are loaded. A baseline is a historical artifact, not a pass.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
from pathlib import Path

GERBER_LAYERS = frozenset(f'{side}_{kind}.gbr' for side in ('F','B') for kind in ('Cu','Mask','Paste','SilkS')) | {'In1_Cu.gbr','In2_Cu.gbr','Edge_Cuts.gbr'}
HARD_METRICS = ('unrouted', 'opens', 'shorts', 'spacing', 'via_in_pad', 'gerber_failures', 'annular_failures', 'silk_overlap_mm2', 'edge_deficit_mm', 'rf_failures', 'mechanical_collisions', 'unverified_envelopes')
QUALITY_METRICS = ('cap_distance_mm', 'ground_fill_deficit')
REQUIRED_GATES = frozenset(('unrouted_zero','router_spacing_zero','independent_lvs_opens_zero','independent_lvs_shorts_zero','nonthermal_via_in_pad_zero','gerber_all_parsed','annular_ring_ge_010','silk_overlap_zero','copper_edge_ge_050','rf_all_four_layers_clear','mechanical_zero_with_verified_envelopes'))
# The micro-escape router uses the smallest supported trace width.
MINIMUM_TRACK_WIDTH_MM = .100
TRACK_WIDTH_GATE = 'minimum_track_width_ge_010'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def input_conflicts(spec, clearance_mm=.5):
    """Prove fixed mating interface copper conflicts using blind geometry only."""
    from shapely.geometry import Polygon
    from cloud_pcb.general_solver.run_blind import _rounded_rect_points
    from cloud_pcb.general_solver.semantics import local_pad_polygon
    from shapely.affinity import rotate, translate, scale
    outline = spec['board_outline_mm']; board = Polygon(outline.get('vertices_mm') or _rounded_rect_points(float(outline['width']), float(outline['height']), float(outline.get('corner_radius_mm', 0))))
    comps = {c['refdes']: c for c in spec['components']}; result = []
    for interface in spec.get('edge_connectors', []):
        comp = comps[interface['refdes']]
        x = float(interface['x_mm']) - float(outline.get('origin_x', 0)); y = float(interface['y_mm']) - float(outline.get('origin_y', 0))
        for pad in comp['pads']:
            if pad.get('type') == 'np_thru_hole' or not any(l.endswith('.Cu') for l in pad.get('layers', [])):
                continue
            copper = local_pad_polygon(pad)
            if comp.get('side') == 'bottom': copper = scale(copper, xfact=-1, origin=(0,0))
            copper = translate(rotate(copper, -float(interface.get('angle_deg', 0)), origin=(0,0)), xoff=x, yoff=y)
            inside = board.covers(copper); margin = board.boundary.distance(copper) if inside else 0.
            if not inside or margin < clearance_mm - 1e-8:
                result.append(dict(kind='fixed_interface_edge', refdes=comp['refdes'], pin=str(pad['num']), actual_mm=round(margin,6), required_mm=clearance_mm, outside_board=not inside, action='revise mechanical input through explicit engineering review; never silently move interface or waive gate'))
    return result


def report_metrics(report, pcb=None, spec=None):
    """Recompute safety facts from raw audit fields; missing evidence is an error."""
    errors=[]; values={k:None for k in HARD_METRICS + QUALITY_METRICS + ('wirelength_mm',)}
    try:
        audit=report['audit']; independent=audit['independent_4layer']; mech=independent['mechanical']
        gates=report['hard_gates']
        if not REQUIRED_GATES.issubset(gates) or any(type(v) is not bool for v in gates.values()): errors.append('missing or non-boolean hard gates')
        flags=audit['pygerber_by_layer']
        if any(type(v) is not bool for v in flags.values()): errors.append('non-boolean Gerber parse result')
        margins=audit['copper_edge_margins_mm']
        if set(margins) != {'F_Cu.gbr','In1_Cu.gbr','In2_Cu.gbr','B_Cu.gbr'}:errors.append('four copper edge measurements required')
        ring=audit['min_annular_ring_mm']
        values.update(unrouted=report['bakeoff'][0]['unrouted_nets'],opens=len(independent['lvs_opens']),shorts=len(independent['lvs_shorts']),spacing=report['router_spacing']['real_count'],via_in_pad=len(independent['via_in_pad_violations']),gerber_failures=sum(flags.get(k) is not True for k in GERBER_LAYERS | set(flags)),annular_failures=audit['annular_ring_violations']+int(ring is None or ring<.1),silk_overlap_mm2=sum(audit['silk_to_mask_overlap_by_side_mm2'].values()) if 'silk_to_mask_overlap_by_side_mm2' in audit else audit['silk_to_mask_overlap_mm2'],edge_deficit_mm=max(0.,.5-min(margins.values())),rf_failures=len(report['rf_unresolved'])+sum(not r['passed'] for r in independent['rf_keepout']),mechanical_collisions=mech['collision_count'],unverified_envelopes=len(mech['unverified_envelopes']),ground_fill_deficit=max(0.,.85-float(report['gnd_pour_stats']['fill_ratio'])))
        if mech['collision_count'] != len(mech['collisions']):errors.append('mechanical collision count inconsistent')
        if mech['all_envelopes_verified'] != (not mech['unverified_envelopes']):errors.append('mechanical verification state inconsistent')
        expected_rf={k['refdes'] for k in report.get('keepouts',[]) if k.get('kind')=='rf'}
        for ref in expected_rf:
            layers=[k['layer'] for k in independent['rf_keepout'] if k['refdes']==ref]
            if sorted(layers)!=sorted(('F.Cu','In1.Cu','In2.Cu','B.Cu')):errors.append('incomplete four-layer RF audit: '+ref)
        for region in independent['rf_keepout']:
            if region['passed'] and (region['copper_mm2']>1e-8 or region['coverage_fraction']>1e-8):errors.append('RF pass contradicts copper evidence')
        # A stricter auditor may discover a new issue. A claimed pass can never
        # override the measured issue, even if a hand-edited summary says true.
        checks=dict(unrouted_zero=values['unrouted']==0,router_spacing_zero=values['spacing']==0,independent_lvs_opens_zero=values['opens']==0,independent_lvs_shorts_zero=values['shorts']==0,nonthermal_via_in_pad_zero=values['via_in_pad']==0,gerber_all_parsed=values['gerber_failures']==0,annular_ring_ge_010=values['annular_failures']==0,silk_overlap_zero=values['silk_overlap_mm2']==0,copper_edge_ge_050=values['edge_deficit_mm']<=1e-4,rf_all_four_layers_clear=values['rf_failures']==0,mechanical_zero_with_verified_envelopes=values['mechanical_collisions']==0 and values['unverified_envelopes']==0)
        for name, passed in checks.items():
            if gates.get(name) is True and not passed:errors.append('claimed gate contradicts audit: '+name)
        if report['hard_gates_passed'] != (bool(gates) and all(gates.values())):errors.append('overall gate result inconsistent')
        for key,value in values.items():
            if value is not None and (not isinstance(value,(float,int)) or isinstance(value,bool) or not math.isfinite(value) or value<0):errors.append('invalid metric: '+key)
    except (KeyError,TypeError,ValueError,IndexError) as exc:errors.append('incomplete audit: '+str(exc))
    derived = {}
    gates = report.get('hard_gates', {})
    failed = {key for key, passed in gates.items() if passed is not True}
    if pcb is not None:
        from cloud_pcb.verify_gate.trace_widths import audit_trace_width_file
        track_widths = audit_trace_width_file(Path(pcb), MINIMUM_TRACK_WIDTH_MM)
        derived['track_widths'] = track_widths
        if track_widths['status'] != 'PASS':
            failed.add(TRACK_WIDTH_GATE)
        if track_widths['status'] == 'UNKNOWN':
            errors.append('minimum routed track width could not be verified')
    if pcb is not None and spec is not None:
        from cloud_pcb.general_solver.compare_reference import _extract_metrics
        measured=_extract_metrics(Path(pcb),'optimization candidate',spec=spec,independent_audit=False)
        values['cap_distance_mm']=measured['avg_decoupling_dist_mm']; values['wirelength_mm']=measured['total_wirelength_mm']
    return dict(values=values, failed_gates=sorted(failed), hard_pass=not errors and bool(gates) and not failed,
                evidence_errors=errors, **derived)


def compare_candidate(baseline, candidate, *, full_suite, tests_passed):
    """Per-case Pareto guard; aggregate gains cannot hide a board regression."""
    regressions=[]; improvements=[]; invalid=[]
    for case, before in baseline.items():
        if case not in candidate:invalid.append(dict(case=case,reason='case not evaluated'));continue
        after=candidate[case]
        for stage,measurement in (('baseline',before),('candidate',after)):
            if measurement['evidence_errors']:invalid.append(dict(case=case,reason=stage+' evidence errors',details=measurement['evidence_errors']))
        if 'track_widths' in before and 'track_widths' not in after:
            invalid.append(dict(case=case, reason='minimum routed track width evidence missing from candidate'))
        for gate in set(before['failed_gates']) ^ set(after['failed_gates']):
            change=dict(case=case,metric=gate,before=gate not in before['failed_gates'],after=gate not in after['failed_gates'])
            (regressions if gate in after['failed_gates'] else improvements).append(change)
        names=list(HARD_METRICS+QUALITY_METRICS)
        # A shorter incomplete board is not an optimization win. Wire length is
        # comparable only when both runs have complete independent connectivity.
        if all(v['values']['unrouted']==0 and v['values']['opens']==0 and v['values']['shorts']==0 for v in (before,after)):names.append('wirelength_mm')
        for name in names:
            old,new=before['values'][name],after['values'][name]
            if old is None and new is None and name=='cap_distance_mm':continue
            if old is None or new is None:invalid.append(dict(case=case,reason='metric unavailable: '+name));continue
            if not math.isfinite(old) or not math.isfinite(new):invalid.append(dict(case=case,reason='nonfinite metric: '+name));continue
            if abs(new-old)<=1e-8:continue
            change=dict(case=case,metric=name,before=old,after=new)
            (regressions if new>old else improvements).append(change)
    if set(candidate)!=set(baseline):invalid.append(dict(reason='dataset coverage mismatch'))
    eligible=full_suite and tests_passed and not invalid and not regressions and bool(improvements)
    return dict(eligible_for_baseline=eligible,hard_gates_passed=full_suite and tests_passed and bool(candidate) and all(c['hard_pass'] for c in candidate.values()),engineering_pass=None,engineering_acceptance='not_certified: endpoint SI, verified current/thermal limits and human wirelength comparison require separate evidence',regressions=regressions,improvements=improvements,invalid=invalid,full_suite=full_suite,tests_passed=tests_passed,policy='per-case Pareto: every safety/QoR metric non-worse; incomplete wire length never rewarded; improvement is not engineering acceptance')


def triage(case, report, metrics, conflicts):
    items=[]
    def add(category,priority,kind,title,evidence):
        items.append(dict(id=hashlib.sha256(f'{case}:{category}'.encode()).hexdigest()[:12],case=case,category=category,priority=priority,kind=kind,title=title,evidence=evidence))
    if conflicts:add('input_constraints',0,'input_blocker','固定机械接口与制造规则冲突',conflicts)
    if metrics['evidence_errors']:add('evidence',0,'verification','审计证据不完整或互相矛盾',metrics['evidence_errors'])
    values=metrics['values']; independent=report.get('audit',{}).get('independent_4layer',{})
    for key,label in (('shorts','修复真实铜短路'),('spacing','修复路由间距违例'),('via_in_pad','移除非热焊盘内过孔'),('gerber_failures','修复实际 Gerber 导出'),('annular_failures','修复孔环'),('silk_overlap_mm2','修复双面丝印压焊盘')):
        if values[key]:add(key,0,'solver',label,dict(metric=key,value=values[key]))
    track_widths = metrics.get('track_widths', {})
    if track_widths.get('status') == 'FAIL':
        add('track_width', 0, 'solver', '修复导出铜层中低于下限的走线', track_widths)
    if values['opens'] or values['unrouted']:add('connectivity',1,'solver','按独立 LVS 断开组诊断布线拥塞',dict(unrouted=values['unrouted'],opens=independent.get('lvs_opens',[]),routing_candidates=report.get('routing_candidates',[])))
    if values['rf_failures']:add('rf',1,'input_and_solver','补齐天线几何证据并保证四层净空',dict(unresolved=report.get('rf_unresolved',[]),layers=independent.get('rf_keepout',[])))
    if values['mechanical_collisions'] or values['unverified_envelopes']:add('mechanical',1,'input_and_solver','处理装配碰撞并补齐有依据的外壳尺寸',independent.get('mechanical',{}))
    if values['cap_distance_mm'] is not None and values['cap_distance_mm']>4.5:add('decoupling',2,'solver','改进去耦优先级与供电脚局部摆位',dict(proxy_average_mm=values['cap_distance_mm'],target_mm=4.5,limitation='bulk caps may be included'))
    if values['ground_fill_deficit']:add('ground_reference',2,'solver','改善地参考完整性',dict(deficit=values['ground_fill_deficit'],target=.85))
    if not report.get('power_width_distribution_mm'):add('supply_evidence',2,'input_and_solver','检查是否缺少非地供电语义或电气需求',dict(non_ground_width_samples=0))
    return items


def write_review(directory, result):
    directory=Path(directory); decision=result.get('decision',{}); tasks=result['tasks']
    lines=['# PCB 优化轮次', '', f"状态：{result['status']}；可晋升基线：{decision.get('eligible_for_baseline',False)}；工程验收：未认证。", '', '改进假设：'+result.get('hypothesis','历史基线诊断'), '', '| Case | 未布通 | LVS 开/短 | 真实间距 | 地填充 | 电容距离代理 mm |', '| --- | --- | --- | --- | --- | --- |']
    for case,data in result['measurements'].items():
        v=data['values'];fill=None if v['ground_fill_deficit'] is None else max(0,.85-v['ground_fill_deficit'])
        # Preserve the actual fill; the deficit saturates above the 85% target.
        fill=result.get('case_details',{}).get(case,{}).get('ground_fill_ratio',fill)
        lines.append(f"| {case} | {v['unrouted']} | {v['opens']}/{v['shorts']} | {v['spacing']} | {fill:.2%} | {v['cap_distance_mm']} |" if fill is not None else f"| {case} | 证据缺失 | — | — | — | — |")
    lines += ['', '## 本轮比较', '', '```json', json.dumps(decision,ensure_ascii=False,indent=2), '```', '', '## 下一步任务', '']
    for task in sorted(tasks,key=lambda t:(t['priority'],t['case'],t['category'])):lines.append(f"- P{task['priority']} `{task['case']}` [{task['kind']}] {task['title']}（证据 ID `{task['id']}`）")
    lines += ['', '所有已解封案例只能用于回归。该流程不读取参考 PCB，不自动修改源代码、输入约束或 DRC 门槛。差分网级铜长差不是端到端时延证明。', '']
    (directory/'review.md').write_text('\n'.join(lines))
    esc=html.escape
    rows=[]
    for case,data in result['measurements'].items():
        v=data['values'];rows.append('<tr>'+''.join('<td>'+esc(str(x))+'</td>' for x in (case,v['unrouted'],f"{v['opens']}/{v['shorts']}",v['spacing'],v['cap_distance_mm'],data['failed_gates']))+'</tr>')
    todo=''.join(f'<li data-case="{esc(t["case"])}">P{t["priority"]} · {esc(t["case"])} · {esc(t["title"])}</li>' for t in sorted(tasks,key=lambda t:(t['priority'],t['case'])))
    options=''.join(f'<option>{esc(c)}</option>' for c in result['measurements'])
    document='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>PCB 优化记录</title><style>body{max-width:1300px;margin:32px auto;background:#101820;color:#edf2f5;font:15px system-ui}td,th{padding:12px;border-bottom:1px solid #345}table{border-collapse:collapse}a{color:#7dd3fc}li{padding:8px}</style>'
    document+=f'<h1>PCB 优化证据</h1><p>{esc(result["status"])} · 基线候选 {decision.get("eligible_for_baseline",False)} · 工程验收 未认证</p><p>{esc(result.get("hypothesis","历史基线诊断"))}</p><a href="review.md">完整比较</a> · <a href="result.json">原始 JSON</a><table><tr><th>Case</th><th>未布通</th><th>LVS 开/短</th><th>真实间距</th><th>电容距离代理 mm</th><th>失败门禁</th></tr>{"".join(rows)}</table><h2>下一步任务</h2><select id="case"><option value="">全部案例</option>{options}</select><ul>{todo}</ul><script>document.querySelector("#case").onchange=e=>document.querySelectorAll("li[data-case]").forEach(li=>li.hidden=!!e.target.value&&li.dataset.case!==e.target.value)</script></html>'
    (directory/'review.html').write_text(document)
