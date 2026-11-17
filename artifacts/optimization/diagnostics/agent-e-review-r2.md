# Agent E 独立复审（第二轮）：value-normalize 修订补丁

复审日期：2026-11-17。对象：工作树未提交改动（相对上一轮的修订：topology.py 重写为 `canonical_component_values` 限制性等价类；tests/test_external_placement.py stub 改 `**kwargs`；tests/test_general_v3.py 增至 4 个规范化测试）。方法：只读源码审查 + 只读验证（脚本存 /tmp：agent_e_r2_verify.py / agent_e_r2_seed.py / agent_e_r2_order.py / agent_e_r2_details.py / agent_e_r2_probe.py），未改动任何源码/测试/输入。上轮报告：agent-e-review.md。

## 总判断

**可冻结执行。** 上一轮三个问题全部解决：① 全套 pytest **127 passed（90.38s）** 实测通过；② 全板扰动收敛为"仅拼写不一致板受扰"——case2/3/5 修订后与默认路径**完全零扰动**；③ '1M'/'1m' 由 unsafe 判定拦截且有测试守卫。默认路径与 HEAD 五板逐字节一致（PARITY_OK）。冻结执行的全部前置条件就绪。以下登记项须写入 hypothesis，不阻塞。

---

## 1. 上轮三个问题的解决情况

### 1a. stub 不接受 kwarg —— 已解决 ✓
tests/test_external_placement.py:64 改为 `def place(s, **kwargs):`。实测 `python -m pytest -q` → **127 passed, 5156 warnings in 90.38s**（123 基线 + 4 新增），run_round 的 pytest 硬门恢复有效。

### 1b. casefold 全板扰动 —— 已解决（影响面收敛实证）✓
新设计仅当同一规范内出现 ≥2 种大小写拼写才建等价类。五板实测（脚本 PART 1）：

| Case | components | map | 社区划分 | 社区顺序 | repeated anchors | macro_targets 移动 |
| --- | --- | --- | --- | --- | --- | --- |
| case1 | 46 | {'1K':'1k','1k':'1k'} | 相同 | **变**（见 §2） | [] → [] | **4/46** |
| case2 | 61 | {} | 相同 | 相同 | [] → [] | 0 |
| case3 | 74 | {} | 相同 | 相同 | [['U201','U301']] 不变 | 0 |
| case4 | 113 | {'10K':'10k','10k':'10k','1K':'1k','1k':'1k'} | 相同 | **变** | [['U5','U6','U7']] → **[['U5','U6','U7','U8']]** | **40/113** |
| case5 | 52 | {} | 相同 | 相同 | [] → [] | 0 |

与提交者声称的实测一致，且优于上轮（上轮 canonical 全量时 case2/5 分别 18/61、36/52 目标移动，现全部归零）。**已复核，非采信。**

### 1c. '1M'/'1m' 语义反例 —— 已解决 ✓
`unsafe` 判定实测：'1M'/'1m' → map={}；'4M7'/'4m7'（中间位置）→ map={}；测试 `test_value_normalization_never_merges_mega_with_milli` 提供守卫。上轮反例 B 关闭。

### 残留新发现（均非阻塞，须登记）

**(a) 等价化能力收窄为"仅大小写差异"，help 文本与此不符。**
分组 key 在 µ→u 替换与 strip **之后**计算，导致 µ/μ 差异与空白差异永远被视为"一致拼写"→ 永不合并。实测：'4u7'+'4µ7' 共存 → map={}；' 1k '+'1k' 共存 → map={}。另外 map 键是归一后字符串而 signature 用**原始**值查找，带空格原始值即使类内合并也会 miss（实测 ' 1K ' 与 '1k'：map={'1K':'1k','1k':'1k'}，但 ' 1K ' 签名不归一）。五板survey：仅 case5 FB1 值含前导空格（' 600OHM/...'），无配对拼写，不触发。方向保守（漏合并优于错合并），可接受；但 run_blind.py `--value-normalize` help 写 "case/micro sign"，**micro 与实现不符**，建议改为 "case-only" 类表述（docstring 本身准确）。

**(b) zip 长度不齐：确定、不崩、机制上倾向保守。**
- 确定性：`zip(*members)` 第 i 个元组恒为各成员第 i 字符的组合，set 迭代顺序只改变元组内排列、不改变 `set(position)`，故 `unsafe=any(...)` 天然顺序无关；`mapping.update` 键唯一、内容与插入序无关。**实测 7 个 PYTHONHASHSEED（0/1/7/42/12345/999/31337）下 case1/case4/ß 组/ßm 组 map 输出逐字节一致。**
- 同一 raw 值多类问题：不可能——folded=f(raw) 为纯函数，同一 raw 恒落同一类。
- 长度不齐行为：按最短截断、位置错位。实测 'ß'/'ss'（casefold 展开）成组合并 map={'ß':'ss','ss':'ss'}——值等价合理、不崩。漏判构造：对 'ßm'(→'ssm')/'ssM'(→'ssm') 穷举两种顺序 unsafe 均 True（'m'/'M' 必与对方字符构成含 M/m 的混合位置）；多轮构造漏判均失败——**长度不齐时倾向误判 unsafe（保守侧）而非漏判**。建议（未来非阻塞）：显式 `len({len(m) for m in members})>1 → unsafe` 并补一条长度不齐测试，消除对截断语义的隐式依赖。

## 2. case1 排序置换的定性

slot5/6 两个 2 元素社区 `['D3','R8']` 与 `['D1','R4']` 互换货架格（对角置换，(4.58,16.88)↔(50.42,5.63)），4/46 组件宏观目标改变——摆放确实会变，但远小于"架构位置换"的担忧。

- **合法性**：变体为统一 flag、全板一致启用，无 per-case 选择；排序键 `(-len, signature元组)` 确定性可复现（map 确定 → 签名确定 → louvain seed=0）。属统一变体的合法行为变化，非设计缺陷。
- **Pareto 捕捉能力**：compare_candidate（optimization.py:90-115）为逐板判定——HARD+QUALITY 指标（含 cap_distance_mm、ground_fill_deficit）任一恶化记 regressions，gate 集合对称差单独记录，`eligible=full_suite and tests_passed and not invalid and not regressions and bool(improvements)`；wirelength 仅在双方 unrouted/opens/shorts 全零时比较。若 D1/R4/D3/R8 跨板移动导致任一指标恶化即不可晋升，聚合改善不能掩盖单板退步。**机制能如实捕捉。**
- 同理 case4 的 40/113 目标移动与 anchors 3→4 扩组也受同一逐板判定约束。

## 3. 新测试盲区

已覆盖：大小写合并正例（含原始值不被改写断言）、'1k'≠'4k7' 双向负例、'1M'≠'1m' unsafe、一致拼写时 map 为空且层级/签名不变（即上轮缺失的"零扰动守卫"）。

未覆盖（实测行为均正确/无害，非阻塞）：
- int/None/float value：canonical 侧 `str()` 化安全；signature 侧 raw 原样、`value_map.get(int)` miss 后不变，default/normalized 行为一致，不崩（实测 int 123 + '123' → map={}）。
- 空 components：canonical={}、analyze 不崩（与 HEAD 同路径）。
- µ/μ 与空白差异"不合并"的收窄行为无断言（若未来按 help 文本"修复"micro 行为，会无守卫地改变影响面）。
- 长度不齐 casefold（ß 类）无测试（见 1b）。

## 4. 默认路径 parity（HEAD 对拍）

动态加载 `git show HEAD:cloud_pcb/general_solver/topology.py`，五板全部组件 `component_signature(comp)` 及整板 `analyze_topology(spec)` 输出（communities/repeated_channels/mappings/signal_edges）JSON 逐字节比较，macro_targets 一并比较：**case1–5 sig=True / analyze=True / macro=True，PARITY_OK**。default 分支表达式 `comp.get('value','')` 与 HEAD 完全相同，kwarg 为 keyword-only 且默认 False。

## 5. 冻结执行就绪判定

| 项 | 状态 |
| --- | --- |
| pytest 硬门 | ✓ 127 passed（90.38s），实测 |
| experiment_key | ✓ variant 入 key（optimize_pcb.py:151）；历史 4 个 complete run（supply-semantics / scoped-supply / stable / octagonal-fanout）均无本变体，无撞 key |
| read_suite | ✓ 5 板 spec sha256 校验通过（实测），输入未改动（git status 仅源码/测试/诊断文档） |
| variant 接线 | ✓ `--value-normalize` 在 case 循环内统一注入全部选中板；choices 已扩展 |
| state/baseline | ✓ state.json 不存在 → active_pointer 回落 artifacts/blind_v3_freeze.json |
| label 规则 / 源快照 / assert_unchanged | ✓ 机制未动；本审查全程只读，未引入源漂移 |
| 晋升路径 | ✓ compare_candidate 逐板 Pareto + gate 对称差 + eligible 条件完整 |

## 需登记进 hypothesis / 轮次报告（不阻塞）

1. 影响面如实声明：case4 40/113 宏观目标移动 + anchors [U5,U6,U7]→[U5,U6,U7,U8]；case1 4/46（D1/R4/D3/R8 对角互换）；case2/3/5 零扰动。case4 单板改善为预期收益，case1 扰动为已知代价。
2. 等价化范围为"仅大小写差异"：µ/μ 与空白差异不在本候选能力内（help 文本 "case/micro sign" 表述过宽，建议后续修正文案）。
3. 保留上轮建议 3（communities 排序键与通道分桶键解耦以进一步收敛影响面）为后续独立候选。

## 审查所用只读脚本

/tmp/agent_e_r2_verify.py（五板 map+扰动+HEAD parity+语义探针）、/tmp/agent_e_r2_seed.py 与 agent_e_r2_probe.py（跨 PYTHONHASHSEED 确定性）、/tmp/agent_e_r2_order.py（set 顺序内省）、/tmp/agent_e_r2_details.py（case1 换序细节+五板值 survey+run 历史）、/tmp/agent_e_r2.py（首轮主脚本，PART 4 因子进程环境失败，由 seed 脚本取代）。
