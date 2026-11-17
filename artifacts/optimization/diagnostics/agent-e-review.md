# Agent E 独立审查：value-normalize 候选补丁

审查日期：2026-11-17。对象：工作树未提交改动（`git -C /workspace diff`，4 文件：topology.py / run_blind.py / optimize_pcb.py / tests/test_general_v3.py）。方法：只读源码审查 + 只读验证脚本（脚本存于 /tmp，未改动任何源码/测试/输入）。

## 总判断

**暂不可冻结执行。** 补丁核心设计合格（零过拟合、默认路径逐字节对拍 HEAD 一致、变体纪律完备、正反例测试有效），但存在 **1 个轮次阻塞缺陷**（全套 pytest 1 failed）和 **1 个影响面误判**（canonical 化会扰动全部五板的社区排序与宏观布局目标，"其余四板签名不变"不成立）。修复阻塞项后可以执行轮次，但按当前设计晋升概率低，需在 hypothesis 中如实预登记影响面。

---

## 1. 零过拟合 —— 通过

- `canonical_component_value()`（topology.py:11-18）只做 `str→strip→µ/μ→u→casefold`，是纯值字符串等价化；不读取 refdes、net 名、坐标、案例 ID，无任何项目特定条件。
- `component_signature` / `analyze_topology` 仅把 value 参与签名，拓扑/几何输入不变。
- run_blind.py 与 optimize_pcb.py 的改动是纯 flag 透传。
- 新测试使用合成 refdes（u0–u3/r0–r3）与匿名网络名，无参考设计信息。

## 2. 影响面 —— value 直接消费点安全；发现 1 个遗漏的间接消费点

### 2a. 已确认不受影响的消费点（逐个核对源码）
| 消费点 | 位置 | 结论 |
| --- | --- | --- |
| 电容解析 `_parse_capacitance_farads` | run_blind.py:131,578 | 用原始字符串，自带 `lower()+μ→u`，不受变体影响 |
| KiCad Value 丝印字段 | run_blind.py:782 `fp.properties={"Value": comp.get("value","")}` | 原始值 |
| `component_envelope` / `infer_rf` label | semantics.py:34,64 | `(fp+value).lower()` 自行小写，原始值 |
| 无源件类型识别 | electrical.py:47 | 自行 `strip().lower()+µμ→u`，原始值 |
| 装配导出/工作台展示 | assembly.py:24, generate_workbench.py:150 | 原始值 |

### 2b. mappings 无消费点（清单担忧项排除）
`analyze_topology` 返回的 `repeated_channels[*].mappings`（同构映射）在 run_blind.py 中**没有任何消费点**（grep 全仓只有 topology.py 内部构造；run_blind.py:545 只读 `anchors`；macro_targets 不用 mappings）。"同构 mapping 影响锚点镜像几何推导"在当前代码中不存在。mappings 仅随 placement_meta → blind_run_report.json 输出为证据数据（内容是 refdes→refdes，不受签名哈希影响）。

### 2c. 遗漏的间接消费点（实测证实，重要）
- topology.py:42 社区排序 key = `(-len, signature元组)`；topology.py:50 通道分桶 key = 含 signature 的 WL 哈希。canonical 化使所有 `canonical(v) != v` 的组件哈希字节改变，**同长度社区并列时顺序翻转 → macro_targets（topology.py:66-78）格子重排**。
- 实测五板（复现：`python /tmp/agent_e_check.py`，脚本按 suite 逐板调用 default vs normalized）：

| Case | canonical 受扰组件 | 社区数 | repeated 通道 | macro_targets 移动组件 |
| --- | --- | --- | --- | --- |
| case1 | 43/46 | 24→24（顺序变） | 0→0 | **20/46** |
| case2 | 42/61 | 25→25（顺序变） | 0→0 | **18/61** |
| case3 | 22/74 | 22→22（7 个单元素社区换序） | 1→1（anchors [U201,U301] 不变） | **7/74** |
| case4 | 101/113 | 10→10（4 个通道社区换序） | **1→1，anchors [U5,U6,U7]→[U5,U6,U7,U8]** | **40/113** |
| case5 | 52/52 | 14→14（**两个 14 元素大社区互换**） | 0→0 | **36/52** |

- 结论：实验意图中"其余四板签名不变"**不成立**。等价类不变，但哈希字节扰动传导为 case1/2/5 的大范围宏观目标重排。case4 的布局变化也是"4 锚 lane 重排 + 4 通道社区格子换序"的复合效果，不能把改善单独归因于 4 核成组。
- 次要记录：placement_input.py:34 的外部放置路径固定 default 签名，与变体组合时 meta['hierarchy'] 风格不一致——仅元数据差异，本轮不用外部放置，风险低。

## 3. 变体纪律 —— 通过

- **默认路径逐字节一致（实证）**：`git show HEAD:cloud_pcb/general_solver/topology.py` 动态加载旧实现，对五板全部组件比较 `component_signature`，并对整板比较 `analyze_topology` 输出（含 communities/repeated_channels/mappings）：**case1–5 全部 signatures_equal=True、analyze_equal=True（PARITY_OK）**。复现：`python /tmp/agent_e_parity.py`。
- kwarg 均为 keyword-only 且默认 False；False 分支表达式 `comp.get('value','')` 与 HEAD 完全相同。
- variant 映射：optimize_pcb.py:183 仅 `--variant value-normalize` 追加 `--value-normalize`，与其他 variant 无串扰；flag 在 case 循环内统一注入全部选中板，无 per-case 变体选择。
- experiment_key（optimize_pcb.py:151）= digest(code+**variant**+inputs+case_timeout)，variant 已涵盖。历史 4 个 complete run（supply-semantics / scoped-supply / stable / octagonal-fanout）无该变体，不会撞 key 查重。
- `run_blind_solver` 其他调用点（legacy run_blind_v3、route_gemini_reference）不传新 kwarg，保持默认行为。

## 4. 测试质量 —— 新测试有效；发现 1 个轮次阻塞的测试回归

- `python -m pytest tests/test_general_v3.py -v`：**21 passed（1.13s）**，含 2 个新测试。
- 测试 1（大小写合并）：先断言 default 下 4 通道不成组（负例基线），再断言 normalized 成组（正例），最后断言 `s['components'][4]['value']=='1K'` 原始值未被改写。能捕获"规范化未生效"和"规范化改写原始输入"两类缺陷。
- 测试 2（负例）：'1k' vs '4k7' 在 default 与 normalized 下均不合组，正确覆盖真实不同值。
- 盲区（均实测行为正确但无测试守卫）：
  - µ/μ 归一无直接断言（实测 '4u7'/'4µ7'/'4μ7' 三方等价，见 /tmp/agent_e_counter.py 输出）；
  - strip 无直接断言（实测 ' 1k ' → '1k' 合并）；
  - 非字符串 value 无测试（canonical(None)='none'、数字 str 化；不崩溃，低风险）；
  - **无"normalized 不扰动无关板分组/排序"的守卫**——正是 §2c 暴露的全板扰动没有任何测试网。
- **阻塞缺陷**：`python -m pytest`（全套）**1 failed, 124 passed（90.4s）**：
  ```
  FAILED tests/test_external_placement.py::test_external_entry_bypasses_internal_placer_but_default_entry_keeps_it
  TypeError: ...place() got an unexpected keyword argument 'value_normalize'
  ```
  根因：tests/test_external_placement.py:64 的 monkeypatch stub `def place(s):` 不接受新 kwarg，而 run_blind.py:1842 现在**总是**以 `value_normalize=...` 关键字调用（即使 False）。run_round 在跑板前强制 `tests_rc==0`（optimize_pcb.py:172），因此本轮命令必然 `pytest failed; no expensive board runs started` → 轮次无效。

## 5. 晋升判定影响 —— 高退步风险

- compare_candidate 为逐板 Pareto：HARD+QUALITY 指标任一非劣被破坏、或任何 gate 翻红，即 regressions → 不可晋升；`aggregate gains cannot hide a board regression`。
- case3 repeated group 数实测 default=1、normalized=1（anchors 不变），该维度无扰动；但 case3 有 7 个组件宏观目标移动。
- **最可能退步路径**：case5（全组件哈希扰动 + 两个 14 元素大社区互换 + 36/52 目标移动）与 case1（20/46）、case2（18/61）布局整体重排 → unrouted/opens/spacing/cap_distance/ground_fill 任一恶化即退步。case4 单板改善（即使 4 锚 lane 生效）需要同时对冲三块板的扰动，按既往指标波动幅度判断晋升概率低。
- 这不是流程缺陷而是候选设计后果：值等价化哈希必然全局扰动（要合并 '1K'/'1k' 的桶，签名必须变）。若想缩小影响面（例如把 communities 排序 key 与 buckets key 解耦），属于**新的源码变更**，必须另立候选与轮次，不得与当前补丁合并。

## 6. 冻结与证据链 —— 除 pytest 门外其余通过

- `read_suite`：5 板 spec sha256 全部校验通过，schema OK（实测）。本审查未改动任何 benchmark 输入。
- pytest 全过门：**当前不满足**（见 §4），必须先修 stub。
- experiment_key 查重：variant 入 key，无历史冲突。
- label 'value-normalize' 匹配 `[a-z0-9][a-z0-9_-]{0,47}` ✓。
- state.json 当前不存在 → active_pointer 回落 suite baseline（artifacts/blind_v3_freeze.json，verify_manifest 可用）。
- 轮内 source 快照/assert_unchanged 机制完好；本轮审查全程只读，未引入源漂移。

## 7. 主动反例 —— 1 个 PASS，1 个真实语义反例（低风险，需记录）

复现：`python /tmp/agent_e_counter.py`（独立双通道 + 公共控制网构造）。

- **正对照**：同封装、独立通道、'1K' vs '1k' → default 不成组、normalized 成组 ✓（意图生效）。
- **反例 A（跨封装过合并）—— PASS**：'1K' vs '1k' 但封装不同（R_0805 vs R_0603，pad 几何不同）→ default 与 normalized 均不合组。签名包含 footprint_name+几何，规范化未引入跨封装过合并。
- **反例 B（SI 前缀歧义）—— 真实反例**：同封装 '1M'（1 MΩ）vs '1m'（1 mΩ 分流电阻）→ `canonical('1M')=='canonical('1m')=='1m'`，normalized 下两通道成组（anchors [[u1,u2]]），default 下不成组。casefold 把相差 10⁹ 倍的阻值等价化。后果评估：仅影响重复通道检测这一**布局启发式**（锚点对齐 lane），不触碰电气解析（electrical.py 自行解析原始值）、线宽、间隙与全部硬门禁；同拓扑位混写 '1M'/'1m' 的真实设计罕见。接受为已知局限，建议在 hypothesis/轮次报告中显式登记，并留一条后续测试。
- **B2/C**：strip 合并 ' 1k ' / '1k' ✓；µ/μ/u 三方归一 ✓。

---

## 需先修复（阻塞）

1. **tests/test_external_placement.py:64**：stub 签名改为接受新 kwarg（如 `def place(s, *, value_normalize=False):` 或 `def place(s, **kwargs):`），恢复全套 pytest 全绿。这是本轮命令有效性的硬前提（修复属源码/测试编辑，须由提交者在启动轮次前完成，本审查未代改）。

## 建议（非阻塞）

2. hypothesis 与轮次报告须如实预登记 §2c 的影响面：canonical 化扰动全部五板社区排序与 macro_targets（"其余四板签名不变"不成立），并预期 case5/1/2 存在真实退步风险。
3. 后续候选可探索把 communities 排序 key 与通道分桶 key 解耦（排序用与值无关的确定性键），把变体影响面收敛到 case4——须另立轮次验证。
4. 补一个 µ/μ 归一与 '1M'/'1m' 局限的直接单测，防回归。

## 审查所用只读脚本与产物

- /tmp/agent_e_check.py（五板 default vs normalized 全量对比）
- /tmp/agent_e_parity.py（HEAD 对拍 + 边界值）
- /tmp/agent_e_counter.py（反例 A/B/B2/C）
- 本报告：artifacts/optimization/diagnostics/agent-e-review.md
