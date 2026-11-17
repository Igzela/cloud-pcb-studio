"""Post-freeze report only; never mutates solver inputs or frozen outputs."""
import hashlib, json, html, re, shutil
from pathlib import Path
ROOT=Path.cwd()
import sys
sys.path.insert(0,str(ROOT))
from scripts.run_blind_v3 import code_fingerprint
freeze=json.loads(Path('artifacts/blind_v3_freeze.json').read_text())
assert freeze['status']=='frozen' and code_fingerprint()==freeze['code']
data=json.loads(Path('artifacts/blind_v3_comparison/all_cases.json').read_text())
cases=data['cases'];assert len(cases)==5
for c in freeze['cases']:
    for path,sha in c['artifacts'].items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==sha,path
log=Path('.run/v3-acceptance-pytest.log').read_text();testline=[s for s in log.splitlines() if '45 passed' in s][-1]
Path('artifacts/v3_validation').mkdir(exist_ok=True)
shutil.copyfile('.run/v3-acceptance-pytest.log','artifacts/v3_validation/pytest-final-45-pass.log')
shutil.copyfile('.run/blind-v3-acceptance.log','artifacts/v3_validation/blind-five-case.log')
shutil.copyfile('/workspace/pcb-v3-research/PHASE1_DECISIONS.zh-CN.md','artifacts/v3_validation/phase1-before-code.zh-CN.md')
shutil.copyfile('/workspace/pcb-v3-research/retrieval_evidence.json','artifacts/v3_validation/research-evidence.json')

def n(x,d=3):return 'N/A' if x is None else f'{x:.{d}f}'
def row(xs):return '| '+' | '.join(map(str,xs))+' |'
def table(head,rows):return '\n'.join([row(head),row(['---']*len(head)),*[row(r) for r in rows]])
def dp(m):
    pairs=m.get('diff_pairs',[]); valid=[p for p in pairs if p.get('valid_connected_pair')]
    return f"{len(valid)}/{len(pairs)}；max {n(max((p['length_mismatch_mm'] for p in valid),default=None))}"
def mech(r):return r['assembly']['mechanical']
def audit(r):return r['audit']['independent_4layer']
report=['# 通用盲测 PCB v3 最终实测报告','',
'**结论：本轮没有达到全部硬性通过标准，不能宣称达到资深工程师 8–8.5 成。** 已交付通用模块、严格审计和离线 3D 装配预览；路由收敛、去耦布局、差分等长及可认证机械尺寸仍存在短板。失败项保留在最终板和报告中，没有改门禁换取通过。','',
'## 运行身份与盲测纪律','',f"起始仓库 commit：`{freeze['source_commit']}`。冻结时间：`{freeze['frozen_at']}`。本地修改未推送，精确源码与工具哈希见 [冻结清单](../artifacts/blind_v3_freeze.json)，交付源码见 [归档](../artifacts/v3_validation/frozen-source.tar.gz)。",'',
'Case 1–3 仅作为已知回归集。Case 4/5 在修改核心求解器前通过隔离白名单脚本从固定 Git 对象抽取；主求解器仅接收板框、孔、固定接口、局部封装/焊盘、网表和可得电气需求。非接口人工摆位/角度、布线、过孔和覆铜未进入规范；独立变形测试改变这些信息后规范保持不变。中间失败运行及哈希清单保存在 `artifacts/v3_attempts/`。最终五板完整产物冻结并验证全部哈希后才解封参考；冻结不等于门禁达标。解封后未再修改求解器，新案例自此属于已暴露集。','',
'五板统一命令为 `source scripts/env.sh && python -m scripts.run_blind_v3`，相同预算与规则，未按案例切换算法。独立布线候选也由通用失败触发条件选择，完整候选日志随板保存。','',
'## 通用算法与调研决策','',
'完整引用与实现边界见 [技术设计决策](v3-design-decisions.zh-CN.md)，原调研草稿与检索证据见 `artifacts/v3_validation/phase1-before-code.zh-CN.md`、`research-evidence.json`。','',
'- 网表：去除公共电源超边，按扇出归一化构图；Louvain 分组，WL 筛选后精确有属性同构确认重复通道。社区宏目标、重复锚点与局部几何合法化使用本板拓扑，不读取布局模板。去耦优先于普通信号无源件放置。尚未实现完整 Leiden / 力导向 / 模拟退火和全子电路严格对称优化。',
'- 电源：核对 fastroute v0.1.14 固定源码，避开 per-net 规则被 class 覆盖及未实现 clearance 的陷阱；使用每网互斥 class，主干默认至少 0.50 mm，细脚局部颈缩。有电流数据才做 IPC-2221 估算；至少两个真实镀通锚点才能建立 In2.Cu 电源岛，In1.Cu 保留 GND。没有用默认线宽冒充载流验证。',
'- 语义：用天线身份和局部无焊盘悬出几何共同推断 RF 区域；信息不足明确 unknown。四层 RF rule area 同时约束 DSN、填铜和独立审计。GND 扇出避让细间距逃线及差分走廊，固定扇出短线也进入真实 DSN 障碍。完整开关热回路和晶振保护尚未完成。',
'- 3D：Three.js 与几何打包在单个 HTML，无 CDN、无 KiCad GUI。FR4、黑/绿阻焊、露铜、走线过孔及参数封装实体可旋转缩放、切换层和高亮网络。KiCad 封装含 `MCAD_Envelope` 元数据；XY 多边形与 Z 区间检查元件、安装孔和机械禁布体。未知高度保留未认证状态，参数包络不能替代厂家 STEP。',
'- 独立检查：逐层铜并查集，只允许真实 plated 连接跨层；修复旧地平面切缝、Gerber 极性顺序、隐式焊盘角度、槽孔孔环等问题。全部 11 个 RS-274X 文件严格解析，丝印检查双面。门禁阈值未放宽。','',
'上述规则依据封装几何、引脚连接、协议/角色和电气需求推导，没有新增针对某个工程位号、网络或人工坐标的布局/布线补丁。','',
'## 新陌生盲测来源','']
source_rows=[]
for i in (4,5):
 m=json.loads(Path(f'benchmark_case/sealed_case{i}/manifest.json').read_text())
 source_rows.append([i,f"[{m['repo_url'].split('/')[-1].removesuffix('.git')}]({m['repo_url']})",f"`{m['source_commit']}`",f"{m['component_count']} / {m['net_count']}",m['layer_count'],'四通道电机驱动、细间距、无线天线' if i==4 else 'USB 3 + 千兆以太网、9 对差分候选'])
report += [table(['Case','来源','commit SHA','器件 / 网络','铜层','难点'],source_rows),'',
'Case 4 许可 CERN-OHL-P-2.0；Case 5 CERN OHL v1.2。Case 4 同时覆盖多通道驱动和 RF，故未添加可选 Case 6。两块板均缺少完整的已验证电流/阻抗/机械高度规格；自动推断的差分候选不能当作原作者明确的高速约束；上游参考也未被预先认证为全部满足本轮门槛。来源文件及参考 SHA256 见各 `sealed_caseN/manifest.json`。','',
'## 最终硬门禁实测','',
'`unrouted_nets` 保留引擎原字段名；fastroute 实际计的是未布通连接数。LVS 另列独立开路网络数，两者不应混为一谈。','']
rows=[]
for c in cases:
 r=c['run'];a=r['audit'];ia=audit(r);sp=r['router_spacing'];rf=ia['rf_keepout'];unknown=r['rf_unresolved']
 rftext='未知' if unknown else ('四层均 0%' if rf and all(z['passed'] for z in rf) else 'FAIL' if rf else '无识别候选')
 rows.append([c['case'],r['bakeoff'][0]['unrouted_nets'],f"{len(ia['lvs_opens'])} / {len(ia['lvs_shorts'])}",f"{sp['raw_count']} / {sp['excluded_same_net_pad_overlaps']} / {sp['real_count']}",len(ia['via_in_pad_violations']),f"{sum(a['pygerber_by_layer'].values())}/{len(a['pygerber_by_layer'])}",n(a['min_annular_ring_mm'],4),n(a['silk_to_mask_overlap_mm2'],4),n(min(a['copper_edge_margins_mm'].values()),4),rftext])
report += [table(['Case','未布通','LVS 开 / 短','间距 原始/误报/真实','非热 VIP','Gerber','最小孔环 mm','双面丝印重叠 mm²','最小板边铜距 mm','RF'],rows),'',
'同网重叠焊盘误报必须同时满足同封装、同一非空网络及真实几何重叠；原始记录和逐条证据保留，记录不足仍计真实违例。','',
'**Case 5 输入约束冲突：** 固定 USB 接口两处屏蔽壳焊盘的板边铜距为 0.300 mm，低于 0.500 mm 要求。证明仅使用盲规范的允许几何，见 [约束冲突证据](../artifacts/v3_input_constraints.json)。本轮没有移动固定接口、修改板框或免检壳焊盘。','',
'RF 的 0% 已在 KiCad 铜几何及导出的四层 Gerber 实际铜中分别相交验证（后者见比较产物 `exported_gerber_rf`），只证明推断的无焊盘天线实体区域在四铜层无铜，不能推及未提供的厂家扩大净空或外壳射频性能。Case 2 无充分封装本体信息，RF 门禁保持失败。','',
'## Case 1–3 回归：v2 → v3','']
rows=[]
for c in cases[:3]:
 v2=c.get('v2_report',{});v3=c['run'];met=c.get('v2_measured_with_current_metrics',{});cmp=c.get('comparison',{});b=cmp.get('blind_solver',{})
 oldaudit=met.get('independent_4layer',{});oldlegacy=v2.get('audit',{})
 rows.append([c['case'],f"{v2.get('bakeoff',[{}])[0].get('unrouted_nets','?')} → {v3['bakeoff'][0]['unrouted_nets']}",f"{len(oldlegacy.get('lvs_opens',[]))}/{len(oldlegacy.get('lvs_shorts',[]))}",f"{len(oldaudit.get('lvs_opens',[]))}/{len(oldaudit.get('lvs_shorts',[]))} → {len(audit(v3)['lvs_opens'])}/{len(audit(v3)['lvs_shorts'])}",f"{n(met.get('avg_decoupling_dist_mm'))} → {n(b.get('avg_decoupling_dist_mm'))}",f"{n(met.get('total_wirelength_mm'))} → {n(b.get('total_wirelength_mm'))}"])
report += [table(['Case','未布通 v2→v3','v2 原始投影 LVS 开/短','同一新审计 LVS v2→v3','平均电容距离 mm v2→v3','平面总线长 mm v2→v3'],rows),'',
'旧 LVS 基于投影，v3 基于实际层和 plated 连接。重测旧板发现的问题不能写成历史门禁原来已经发现，也不能只比较两种不同口径的“0”。电容距离沿用比较器的代理指标：双引脚电容接地且另一端到相同网络 IC 焊盘的最近距离，可能包含 bulk 电容；不是逐颗人工鉴定后的去耦专属统计。','',
'## 五板与人工参考的量化对比','']
rows=[]
for c in cases:
 cmp=c.get('comparison',{});b=cmp.get('blind_solver',{});h=cmp.get('human_reference',{});r=c['run'];ratio=b.get('total_wirelength_mm',0)/max(h.get('total_wirelength_mm',0),1e-9)
 rows.append([c['case'],f"{n(b.get('avg_decoupling_dist_mm'))} / {n(h.get('avg_decoupling_dist_mm'))}",f"{n(b.get('total_wirelength_mm'))} / {n(h.get('total_wirelength_mm'))}",n(ratio),f"{dp(b)} / {dp(h)}",f"{r['gnd_pour_stats']['fill_ratio']*100:.2f}%",n(h.get('serialized_gnd_fill_by_layer',{}).get('In1.Cu',0)*100,2)+'%' if 'In1.Cu' in h.get('serialized_gnd_fill_by_layer',{}) else '未序列化/无地铜'])
report += [table(['Case','电容距 v3 / 人工 mm','平面线长 v3 / 人工 mm','长度比','差分 连通候选/总；max网长差 mm：v3 / 人工','v3 In1 地填充','参考 In1 地填充'],rows),'',
'**未布通板的较短线长不能认作优于人工。** 总线长不包含以覆铜完成的连接，参考与求解器使用平面策略不同也会改变该比值。表中平面长度含精确圆弧；差分列为两条网络全部铜线长度之差，包含分支与桩线，不是已验证的端到端传播路径失配，不能用它单独证明高速等长达标；还计入镀通过孔柱长度，未提供真实叠层时使用估计并保留限制。无有效连通差分对时记 N/A，不能将两条未连接的 0 长度写成完美匹配；有效候选数也不证明整条线路全程受控阻抗和差分耦合。','',
'去耦目标为平均 ≤3.5–4.5 mm；完整布通后才有意义的总长度目标 ≤1.05× 人工；通用差分 ≤0.50 mm、高速 ≤0.20 mm；In1 地填充 ≥85%。各值如实列出，不以单个较好指标抵消硬失败。','',
'### 电源主干与通道规整性','']
rows=[]
for c in cases:
 r=c['run'];dist=r['power_width_distribution_mm'];total=sum(sum(v.values()) for v in dist.values());wide=sum(length for v in dist.values() for width,length in v.items() if float(width)>=.5-1e-9)
 top=c.get('comparison',{}).get('blind_solver',{}).get('topology',{});htop=c.get('comparison',{}).get('human_reference',{}).get('topology',{})
 rows.append([c['case'],f"{wide:.2f}/{total:.2f} ({wide/max(total,1e-9)*100:.1f}%)" if total>0 else "N/A（无已识别非地电源）",len(r['gnd_pour_stats']['power_islands']),top.get('repeated_channel_groups','?'),str([round(v,3) for v in top.get('channel_alignment_error_mm',[])]),str([round(v,3) for v in htop.get('channel_alignment_error_mm',[])]),f"{sum(top.get('community_hpwl_mm',[])):.2f} / {sum(htop.get('community_hpwl_mm',[])):.2f}"])
report += [table(['Case','电源 ≥0.50mm 长度/电源总长 mm','电源岛','重复组','v3 锚点偏差 mm','人工 锚点偏差 mm','社区包围 HPWL合计 v3/人工 mm'],rows),'',
'Case 4 盲规范中只识别到 GND，未可靠识别非地电源需求，未建立电源岛；该板电源主干目标不能宣称通过。Case 5 In1 地填充仅 75.51%，低于 85% 目标。逐网线宽的长度加权完整分布保存在每板 `power_width_distribution_mm` 和比较 JSON。≥0.50 mm 主干与局部窄线可以并存；电源岛数量不代表已证明载流/压降。社区 HPWL 是同一分组在不同摆位下的包围框跨度；锚点偏差仅测重复核心对齐，不是完整通道严格对称证明。','',
'## 3D 实际检查与交付入口','',
'[全部案例预览与截图目录](../artifacts/blind_v3_index.html)。HTML 可直接用浏览器打开，无本地服务器和网络依赖。每板有斜视、俯视、侧视、黑油截图及浏览器报告；解封后的人工参考使用同一参数封装库展示，不把它当成厂家真实 STEP。','']
rows=[]
for c in cases:
 r=c['run'];b=c['browser'];m=mech(r);i=c['case']
 rows.append([i,r['assembly']['component_models'],m['collision_count'],len(m['unverified_envelopes']),'PASS' if m['passed'] else 'FAIL（尺寸待核）','PASS' if b['passed'] else 'FAIL',len(b.get('console_errors',[])),f"[交互 HTML](../artifacts/blind_v3_case{i}/assembly.html)",f"[侧视对比](../artifacts/blind_v3_comparison/case{i}/comparison_side.png)"])
report += [table(['Case','器件实体数','包络碰撞','未认证包络','完整机械门禁','WebGL/交互','控制台错误','预览','截图'],rows),'',
'零包络碰撞不能等同全部器件真实外壳零干涉。孔位检查包含紧固件占位；没有厂商高度/精确外壳的器件仍未认证，不能靠估计形状美化为硬通过。浏览器另外验证外网请求为零、实体非平面、黑绿切换、图层交互及三个视角非白屏。Case 1/5 另做实际网络拾取与同网高亮检查，见 network-hover-check.json。人工参考的原始 fp_text 位号与新版属性位号在统一展示脚本中等价读取；五板参考也按各自完整器件数验证，初次旧位号提取失败的报告保留。','',
'## 逐板失败与限制','']
for c in cases:
 r=c['run'];ia=audit(r);i=c['case'];opens=[x.get('net',x) for x in ia['lvs_opens']]
 report += [f"- **Case {i}**：未过门禁 `{', '.join(k for k,v in r['hard_gates'].items() if not v)}`。独立开路网络：`{opens}`。路由模式 `{r['routing_mode']}`。"]
 if c.get('evaluation_error'):report += [f"  对比执行异常：`{c['evaluation_error']}`。"]
report += ['',
'3D 额外限制：旧规范仅有圆角半径而无轮廓顶点时，当前查看器基板仍用矩形外包轮廓；侧视比较中的参考圆弧走线未全部转换为三维实体。电气审计仍使用实际板框和精确圆弧，不能把预览近似当作机械精密模型。','',
'Case 3 历史 `/M2_BRAKE` 是否消除不能只看原网络：最终仍有任何其他网络开路就属于失败。完整网络名列于上方和逐板 LVS JSON，未改网表删去失败连接。所有对比是在冻结之后执行，不依据新参考进行补救。','',
'全局短板：Case 4 的同构发现只把四个驱动核心中的三个识别为同一重复组，尚未覆盖完整四通道模板；未实现完整层次模拟退火与全通道镜像、所有开关热回路/晶振拓扑保护、完整差分耦合和 SI 验证、经明确电流验证的 PI/温升、厂家级 STEP 精确装配。Case 5 的固定接口与板边门槛还有直接约束冲突。因而本轮只能报告实际改进与失败，不能给出 8–8.5 成结论。','',
'## pytest 与可复现文件','',f'最终 `source scripts/env.sh && python -m pytest -v`：**原有 26 + 新增 19 = 45 项全部通过**。实际输出：`{testline.strip("= ")}`。5156 条第三方 PyGerber/PyParsing 弃用警告保留在日志中。','',
'[完整 pytest -v 日志](../artifacts/v3_validation/pytest-final-45-pass.log)；[五板统一运行日志](../artifacts/v3_validation/blind-five-case.log)；[逐板全部对比 JSON](../artifacts/blind_v3_comparison/all_cases.json)。新增测试覆盖图同构及重命名、分网线宽/电源岛、RF 不确定性及四层净空、机械碰撞与未知高度、跨层 LVS、真实孔环、地平面连通性、焊盘旋转、Gerber 极性、GND 固定短线障碍及包络元数据回读。','',
'Case 4 原参考使用新版 KiCad 的仅网络名表达式，旧 kiutils 不兼容。对比阶段使用通用语法适配器为所有参考生成分析副本：把显式网络名映射为数字表和旧版焊盘网络表达式；递归验证转换前后网名、坐标、几何、连通语义等价。原参考字节和哈希不变，适配记录保存在 reference_format_adapter；它不进入求解器，也不修改冻结代码。初次解析异常日志保留在验证目录。本报告由冻结产物和解封后测量生成。后处理脚本与其 SHA256 随比较产物保存。生成报告时再次验证所有冻结输入/代码/产物 SHA256；报告和对比写入独立目录，没有回写冻结案例。','']
rfrows=[]
for c in cases:
    cmp=c.get('comparison',{})
    for side,label in (('blind_solver','v3'),('human_reference','人工参考')):
        for entry in cmp.get(side,{}).get('independent_4layer',{}).get('rf_keepout',[]):
            rfrows.append([c['case'],label,entry['refdes'],entry['layer'],n(entry['copper_mm2'],6),n(entry['coverage_fraction']*100,6)])
if rfrows:
    report += ['## RF 区域逐层参考对比','',table(['Case','版本','器件','铜层','区域内铜 mm²','铜覆盖 %'],rfrows),'']
Path('docs/v3-final-report.zh-CN.md').write_text('\n'.join(report))
# Local catalog: all links remain relative and browser-readable offline.
parts=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>PCB v3 实测交付</title><style>body{font:16px system-ui;background:#101820;color:#edf2f5;max-width:1200px;margin:32px auto}a{color:#7dd3fc}article{padding:20px;margin:20px 0;background:#172431;border-radius:12px}img{width:48%;vertical-align:top}p{line-height:1.7}</style><h1>PCB v3 装配与盲测实测</h1><p>未满足全部硬门禁。参数包络包含未验证尺寸，不代表厂商真实外壳认证。所有预览离线独立；每板报告保留失败。</p><p><a href="../docs/v3-final-report.zh-CN.md">完整中文报告</a> · <a href="blind_v3_freeze.json">冻结清单</a></p>']
for c in cases:
 i=c['case'];r=c['run'];parts += [f'<article><h2>Case {i} — {html.escape(r["project_name"])}</h2><p>未布通 {r["bakeoff"][0]["unrouted_nets"]}；LVS 开路 {len(audit(r)["lvs_opens"])} / 短路 {len(audit(r)["lvs_shorts"])}；完整机械门禁未通过。</p><p><a href="blind_v3_case{i}/assembly.html">旋转查看 v3 装配</a> · <a href="blind_v3_comparison/case{i}/reference_3d/assembly.html">人工参考参数装配</a> · <a href="blind_v3_comparison/case{i}/comparison.json">量化对比 JSON</a> · <a href="blind_v3_case{i}/blind_run_report.json">全部门禁</a></p><img src="blind_v3_case{i}/assembly_oblique.png"><img src="blind_v3_case{i}/assembly_black.png"><p><a href="blind_v3_comparison/case{i}/comparison_side.png">侧视对比 PNG</a> · <a href="blind_v3_comparison/case{i}/comparison_2d.png">2D 对比 PNG</a></p></article>']
Path('artifacts/blind_v3_index.html').write_text('\n'.join(parts))
Path('artifacts/v3_validation/final-integrity.json').write_text(json.dumps(dict(frozen_at=freeze['frozen_at'],source_files=len(freeze['code']),frozen_artifacts=sum(len(c['artifacts']) for c in freeze['cases']),code_hashes_match=True,artifact_hashes_match=True,pytest_result=testline,all_hard_gates_passed=all(c['run']['hard_gates_passed'] for c in cases)),indent=2)+'\n')
print('Report, local preview catalog and final integrity record written.')
