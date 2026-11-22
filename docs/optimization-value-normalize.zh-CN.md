# 优化轮次报告：value-normalize（拓扑签名器件值大小写等价化）

- 日期：2026-11-17
- 轮目录：`artifacts/optimization/runs/20261117T133839160986Z-value-normalize/`
- 变体：`value-normalize`（`--value-normalize` 实验开关，默认关闭）
- 结论：**complete，promoted=false，候选被拒**。活动基线仍为冻结 v3（`artifacts/blind_v3_freeze.json`）。

---

## 1. 改动内容、推导依据及为何通用

### 改动

| 文件 | 内容 |
| --- | --- |
| `cloud_pcb/general_solver/topology.py` | 新增 `canonical_component_values(spec)`：仅当同一规范内存在同值不同大小写拼写（如 `1K` 与 `1k` 并存）才建立等价类；`component_signature` / `analyze_topology` 通过 `value_map` 使用它。含 `unsafe` 侧判定：等价类内出现 `M/m` 单位歧义（如 `1M` 对 `1m`）或按位混合即整体放弃合并。默认路径（无 kwarg）表达式与原实现逐字符相同。 |
| `cloud_pcb/general_solver/run_blind.py` | 求解链路传入 `value_normalize` 开关；新增 `--value-normalize` CLI。 |
| `scripts/optimize_pcb.py` | variant choices 增加 `value-normalize`，variant 纳入 experiment_key。 |
| `tests/test_general_v3.py` | 新增 4 个测试：大小写合并正例（原始值不被改写）、`1k`≠`4k7` 负例、`1M`≠`1m` unsafe 负例、一致拼写时 map 为空且签名/层级零扰动守卫。 |
| `tests/test_external_placement.py` | stub `place(s, **kwargs)` 以接受新 kwarg。 |

### 推导依据（来自只读诊断，非算法条件）

诊断发现某板内同值器件存在 `1K` 与 `1k` 两种拼写，导致拓扑签名按值匹配时把本应同构的重复通道识别为不同结构。等价化条件完全来自盲规范自身：**只有规范内部出现拼写不一致时才生效**；不引用任何位号、网络名、案例编号或坐标。等价化能力收窄为"仅大小写差异"：µ/μ 与空白差异不合并（分组 key 在 µ→u 替换与 strip 之后计算，方向保守：漏合并优于错合并）。

### 为何通用

- 等价类由"规范内存在 ≥2 种大小写拼写"这一全局事实触发，对所有板统一生效；一致拼写的规范 map 为空，签名与层级逐字节不变。
- `M/m` 歧义类保守不合并（有测试守卫）；跨 PYTHONHASHSEED（0/1/7/42/12345/999/31337）输出逐字节确定。
- Agent E 独立复审两轮，第二轮结论"可冻结执行"，并实测默认路径与 HEAD 五板 parity（PARITY_OK）。

### 预登记影响面（写入 hypothesis）vs 实测

| Case | 预登记 | 实测 |
| --- | --- | --- |
| case1 | 等尺寸社区架位排序置换（4/46 宏观目标），质量指标可能波动 | **零扰动**：排序置换被下游吸收，布线几何与基线逐字节一致 |
| case2/3/5 | 零扰动 | **零扰动**（map 为空，几何与基线逐字节一致） |
| case4 | 重复驱动核心 3→4（U8 加入等价重复组），40/113 宏观目标移动，预期 unrouted/opens/cap_distance 至少一项改善 | 摆放确实受扰（cap_distance 13.875→13.931），但 **unrouted 1→1、opens 1→1，无任何改善，cap_distance 轻微退步** |

## 2. 各 Agent 实际贡献与独立审查结论

本轮集群分工与产出（诊断报告存 `artifacts/optimization/diagnostics/`）：

| Agent | 实际贡献 |
| --- | --- |
| A 路由工具链 | 一手复现并定位 `bin/freerouting-cli`（GraalVM native，v2.5.0）`ObstacleArea` 序列化崩溃链（`UnsupportedFeatureError` → 布线线程死亡 → 主进程挂至 60s 超时，五板全部如此）；确认上游 PR #966/#1088 已修未发版；**实测官方 v2.5.0 jar + Java 25 在 case1/case2 真实布通并产出可解析 SES**（case1 3 unrouted / case2 2 unrouted + 3 violations，任务耗时 11–12s）；查清 `Rect_T_850x650_um` 告警真实对象是 H1–H4 安装孔缺 library image（4/3/0/4/0 与孔数吻合）。报告：`agent-a-freerouting.md`。 |
| B 拥塞与 GND 扇出 | 逐板开路根因证据表：case3 `/Left Driver/ADVANCE` 为软拥塞（In2 全程连通、via 落点充足、纯搜索失败）；case4 两条均连通（`Net-(D3-K)` 与 J1 S1-S1，后者上轮 +1 回归被判定为纯搜索次序翻转）；case5 四条 M 对为**输入级结构性不可布**（DIFF 0.29/0.198 仅 F/B 层 vs 0.4mm 间距，四条出口逐一数值验证均封闭），另三条为扇出 stub/guard 场挤压与同构引脚搜索次序失败；提出 GND 扇出代价模型五缺口（G1 类别盲间距阈值、G2 走廊方向/宽度错配、G3 代价不含 guard、G4 顺序贪心无通道预算、G5 无反馈回路）与通用提案 P1/P2/P3。报告：`agent-b-congestion.md`。 |
| C 布局与子电路 | 定位 case4 去耦 13.875mm 的首因是电源网识别失败（网络名不匹配现有电源网判定），及同构缺口唯一根因是器件值大小写不一致（`1K` vs `1k`）——后者直接催生本候选。报告：`agent-c-placement.md`。 |
| D 独立验证与 ECAD/MCAD | 机械 339 项 unverified 中 303 项可由封装标准（EIA/JEDEC/MPN）有依据推导，剩余真实未知项逐板清点；识别 12 项可加严的验证缺口；确认 case2 RF 属输入缺数据（天线候选无体几何也无显式 keepout）。报告：`agent-d-verification.md`。 |
| E 独立审查 | 两轮只读审查。首轮提出 stub kwarg、全板扰动、`1M/1m` 语义反例三个问题；修订后第二轮实测 127 passed、影响面收敛、map 跨 seed 确定、默认路径 HEAD parity，结论"可冻结执行"，并把影响面与能力收窄登记进 hypothesis。报告：`agent-e-review.md`、`agent-e-review-r2.md`。 |

主 Agent 负责集成、冻结、完整轮次与本判定。本轮未启用 gemini-4-hydrogen 外部参考（无新调用，不涉及凭据）。

## 3. 五板基线 → 候选逐项实测表（含全部退步与失败）

基线 = 冻结 v3（`artifacts/blind_v3_freeze.json`，sha256 `e2e141a9…`）。

| Case | 指标 | 基线 | 候选 | 判定 |
| --- | --- | --- | --- | --- |
| case1 | 未布通 / LVS 开路 / 短路 / 间距 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 持平 |
| case1 | 总线长 mm | 953.306 | 953.306 | 持平（几何与基线逐字节一致） |
| case1 | 地填充 / 电容距离 mm | 86.49% / 3.345 | 86.49% / 3.345 | 持平 |
| case1 | 失败门禁 | 机械（46 项未验证） | 同左 | 持平（未放宽） |
| case2 | 未布通 / 开路 / 短路 / 间距 | 0 / 0 / 0 / 0 | 同左 | 持平 |
| case2 | 总线长 / 地填充 | 1248.608 / 91.54% | 1248.608 / 91.54% | 持平（几何逐字节一致） |
| case2 | RF 四层净空 | 未通过 | 未通过 | 持平（输入缺天线证据，未放宽） |
| case3 | 未布通 / 开路 | 1 / 1 | 1 / 1 | 持平（同一条开路） |
| case3 | 总线长 / 地填充 | 1144.005 / 89.33% | 1144.005 / 89.33% | 持平（几何逐字节一致） |
| case4 | 未布通 / 开路 | 1 / 1 | 1 / 1 | 持平 |
| case4 | **电容距离代理 mm** | **13.875** | **13.931** | **退步 +0.056（本轮唯一登记回归）** |
| case4 | 总线长 mm | 3224.045 | 3354.289 | 因未布通不为 0，按策略**不计收益也不计退步** |
| case4 | 地填充 | 94.04% | 94.05% | 持平（deficit 均 0） |
| case5 | 未布通 / 开路 | 9 / 9 | 9 / 9 | 持平（同 9 条开路） |
| case5 | 总线长 / 地填充 | 935.400 / 75.51% | 935.400 / 75.51% | 持平（几何逐字节一致） |
| case5 | 板边铜距 | 0.30mm < 0.50mm | 0.30mm < 0.50mm | 持平（已记录输入约束冲突，`copper_edge_ge_050` 门禁保持失败，未移动接口未豁免） |

零扰动实证：case1/2/3/5 的 `routed.kicad_pcb` 在剥离每轮随机的 `tedit`/`tstamp` 元数据后与 `artifacts/blind_v3_case{1,2,3,5}/routed.kicad_pcb` **MD5 逐字节一致**——预登记的 case1 排序置换风险实际未传导到最终坐标。

五板共同项（与基线一致，未放宽）：LVS 短路 0、真实间距违例 0、非热焊盘 Via-in-Pad 0、11/11 层 Gerber 解析通过、双面丝印压焊盘 0；机械外壳/高度未核实数依次 46/59/74/108/52——**参数包络碰撞 0 不代表真实装配已认证，五板机械门禁全部保持失败**。

## 4. 测试与耗时

- 冻结清单记录完整 pytest **127 passed**（123 基线 + 4 新增），5156 warnings，**88.86 秒**。[manifest.json](../artifacts/optimization/runs/20261117T133839160986Z-value-normalize/manifest.json)
- 全轮耗时：**1321.479 秒**（case1 117.9 / case2 164.9 / case3 240.2 / case4 370.3 / case5 336.6）。
- 轮级汇总见 [review.md](../artifacts/optimization/runs/20261117T133839160986Z-value-normalize/review.md) 和 [result.json](../artifacts/optimization/runs/20261117T133839160986Z-value-normalize/result.json)。

这轮的 `pytest.log` 和 22 份逐板日志未随原始提交进入 Git，合计缺少冻结清单引用的 23 个文件。原先的 `*.log` 忽略规则覆盖了这些路径。其余 172 项已发布产物、输入和轮级证据均与清单哈希一致，源码归档内 66 项也都匹配。严格的整轮完整性校验仍因日志缺失而失败。只有找回与原哈希一致的日志才能补齐这一轮。后续优化轮的日志路径已加入 `.gitignore` 例外。

## 5. 3D 预览与机械未知项

- 3D 预览（浏览器已验证通过，五板 `browser_passed=true`）：
  [case1 assembly.html](../artifacts/optimization/runs/20261117T133839160986Z-value-normalize/case1/assembly.html) ·
  [case2](../artifacts/optimization/runs/20261117T133839160986Z-value-normalize/case2/assembly.html) ·
  [case3](../artifacts/optimization/runs/20261117T133839160986Z-value-normalize/case3/assembly.html) ·
  [case4](../artifacts/optimization/runs/20261117T133839160986Z-value-normalize/case4/assembly.html) ·
  [case5](../artifacts/optimization/runs/20261117T133839160986Z-value-normalize/case5/assembly.html)
- 截图：各 case 目录 `assembly_top.png` / `assembly_oblique.png` / `assembly_side.png` / `assembly_black.png`。
- 机械未核实封装（外壳/高度）：case1=46、case2=59、case3=74、case4=108、case5=52。碰撞检测 0 仅指参数包络；真实装配认证仍需外壳尺寸证据。Agent D 诊断：其中约 303 项可由封装标准（EIA/JEDEC/MPN）有依据推导，属后续可落地项，但本轮未实施。
- case5 存在 P0 输入阻塞：固定接口焊盘板边铜距 0.30mm（要求 0.50mm），属输入约束冲突，须工程评审修订输入，算法不得绕过。

## 6. 第二引擎（Freerouting）真实性

**未恢复。** 本轮五板 `router_processes.json` 记录 `freerouting-cli` 全部 `timed_out=true, returncode=null`，SES 永不生成；日志仍为 `ObstacleArea` 序列化崩溃。本轮遵守冻结纪律未更换二进制或依赖，第二引擎继续缺席，不参与任何指标。

修复路径已由 Agent A 验证但**尚未实施**：官方 v2.5.0 jar + Java 25 在 case1/case2 实测真实布通（含器件/焊盘/网络导入审计与连通性并查集核验）。其实测质量差距也须如实记录：case1 3 unrouted、case2 2 unrouted + 3 violations（fastroute 对照均为 0 unrouted）——换 JVM 只修复"崩溃无输出"，不提升布线完成率。实施属独立工具变更轮次，须与算法改动分开归因。

## 7. 哈希与历史完整性

本轮（manifest sha256 `16bfe655fbd430fb194eb973913e3552d5033682221a6f10d4a44cc976c63b56`）：

| 项 | 值 |
| --- | --- |
| 源码归档 source.tar.gz | `0c7f7ad53bae0208590f93f35ce455e35d2e8d6c96f445fd5004d5a46633153d`（66 项代码快照入 manifest.code） |
| result.json | `1faffe5f090ca3602470a1d66c510ca0e91225e26c9de4d111ada00abc6aa2bf` |
| review.md / review.html | `2f249942…` / `aa76f49d…` |
| pytest.log | `94a663f37c50d8fcee5f31a4d228b4c60994b13e4be580bc48d5321a90be9a19` |
| 五板输入 spec | case1 `e88bf396…` / case2 `9789bbf3…` / case3 `91047a8d…` / case4 `48f5c233…` / case5 `109598de…`（与 inputs/ 一致，read_suite 校验通过） |
| 基线指针 | `artifacts/blind_v3_freeze.json` sha256 `e2e141a9…`，源归档 `12e1f4d3…` |

历史完整性：`artifacts/blind_v3_case1..5`、四个历史轮目录、Gemini 参考产物均未改动（git 状态仅含本轮源码/测试/诊断文档与新轮目录）；本轮测试未改写三个 tracked 展示产物（`artifacts/verify/chromium_inspect.json`、`gate_report.json`、`workbench_screenshot.png`），无需恢复。`artifacts/optimization/last_run.json` 已更新指向本轮，`promoted=false`。

## 8. 晋升判定与真实差距

**判定：拒绝晋升。** 依据逐板 Pareto 策略：case4 `cap_distance_mm` 13.875→13.931 构成退步，且五板无任何改善项（`improvements=[]`）。假设预期的 case4 连通性/去耦收益未兑现——重复组扩员改变了摆放，却没有改变路由器对该板唯一开路的求解结果。候选代码保留在 `--value-normalize` 开关之后，默认行为未变（零扰动实证），不污染稳定基线。活动基线仍为冻结 v3。

四类事实区分：pytest 通过 ✓；本轮完整执行 ✓；候选有资格晋升 ✗；全部工程门禁通过 ✗；最终工程认证 ✗（未认证：端到端 SI、经验证的电流/热限与人工布线对比均需独立证据）。

离"8–8.5 成工程可用"目标的真实差距（按证据排序）：

1. **连通性硬缺口**：case3 1 条、case4 1 条、case5 9 条开路。case5 四条 M 对是输入级规则-几何冲突（DIFF 仅 F/B 层 vs 0.4mm 间距，任何 DRC 正确的布线器必失败），需工程层面修订层分配规则或间距；其余为可改进的搜索/扇出问题。
2. **第二引擎缺席**：freerouting 崩溃未修复，交叉验证能力缺失（修复路径已验证，待独立轮次实施）。
3. **机械认证缺口**：五板共 339 项封装未核实 + 外壳尺寸未知；包络碰撞 0 不是装配认证。
4. **输入阻塞**：case5 USB 接口 0.30mm 板边铜距 vs 0.50mm 要求；case2 天线缺几何证据（RF 门禁）。
5. **质量指标**：case4 去耦代理 13.9mm（目标 4.5mm，首因电源网识别失败）；case5 地填充 75.51%（目标 85%）。

### 下一步建议（均有诊断证据支撑，待独立轮次）

1. **第二引擎修复轮**（Agent A 路径）：切换 JVM jar 或等 upstream 发版；同时修复 `board_to_dsn` 安装孔 image 缺失。
2. **GND 扇出类别感知间距**（Agent B P1，修 G1）：把固定 0.165 阈值替换为按邻近焊盘类别规则间距；对纯 DEFAULT 板无行为变化。
3. **电源网拓扑识别**（Agent C）：修复 case4 去耦首因，预期直接改善 cap_distance 13.9→可测收益。
4. case5 层分配规则属于输入/需求侧工程决策，须先经工程评审再动算法。
