# Agent C 诊断报告：布局与子电路（去耦距离 / case4 同构缺口 / 逃线共因）

- 运行轮次：`artifacts/optimization/runs/20261117T112159329967Z-octagonal-fanout/`（下称 RUN）
- 性质：只读诊断。未修改任何源码、输入与既有 artifacts；分析脚本均在 `/tmp`（见 §6）。
- 指标口径（来自 `cloud_pcb/general_solver/compare_reference.py:140-182,263-265`）：`cap_distance_mm` = 每个“双引脚且一端为 GND”的电容，其非 GND 焊盘到**同网络最近 IC 焊盘**的距离的平均值。注意该口径**包含 bulk 电容与信号网滤波电容**，且 “IC” 按 `U/IC` 前缀或 ≥4 焊盘自动识别。

## 1. 去耦距离构成：总览

| Case | cap_distance_mm | bypass 类（个数/均值/>4.5 数） | signal_passives 类（个数/均值/>4.5 数） | 主因 |
| --- | --- | --- | --- | --- |
| case1 | 3.345（达标） | 11 / 3.35 / 1 | 0 | — |
| case2 | 7.706 | 14 / 6.94 / 10 | 6 / 9.50 / 6 | 单引脚电源轨容量溢出 + 边缘 IC 被周边占位 |
| case3 | 3.949（达标） | 6 / 2.46 / 0 | 2 / 8.26 / 2 | — |
| case4 | 13.875 | **0（全漏识别）** | 29 / 13.87 / 26 | 电源网识别失败 → 全部电容降级为重心放置 |
| case5 | 6.884 | 6 / 2.97 / 0 | 17 / 8.27 / 12 | 极端密度无席位预留 + /V12 漏识别 |

bypass/signal_passives 分类取自各 case `blind_run_report.json` 的 `placement_meta.groups`；逐电容距离用 `compare_reference._extract_metrics` 在 `routed.kicad_pcb` 上重算。

## 2. 分 case 根因与证据

### 2.1 case4（13.875）：电源网识别失败是首要根因

- 该板 `design_requirements.power_rails` 仅声明 `GND`；`_is_power_net` 的字面集合（`GND/VCC/VDD/...`、前缀 `+`/`VDD_`/`VCC_`）**匹配不到 KiCad 层次网络名** `/3V3`、`/VMOT`、`/5V_USB`。本轮未启用 `derive_electrical_requirements`（effective_spec 中 rails 仍只有 GND）。
- 结果：`bypass_caps = []`，29 个“双引脚+GND”电容**全部**落入 `signal_passives`，走纯 barycenter + 一次重心修正（`run_blind.py:622-647`）：
  - `/3V3` 是多端公共网 → 重心落在板中部，C19/C20/C22/C2/C1 距目标 IC 电源脚（U4.9、U3.1）23.8–27.2mm；
  - `/VMOT`（四个 VNH7040 驱动的公共母线，19 个网络端点）→ bulk 电容 C10(470µF) 距 U6.10 达 18.7mm，C3/C16 10.9–11.5mm；
  - `/M*_CS_ADC` 电流采样滤波电容被重心拉向驱动侧采样电阻（C_CS2→R_CS2 仅 7.55mm），而指标量的最近 IC 脚是 MCU 的 U1.39（32.6mm）；C_CS1..8 距离 8.8–32.6mm。
- 空间并非瓶颈：U1.39、U1.38、U1.4/5 等目标脚 6mm 内邻居数 = 0，MCU 旁明明有空位——是**目标错误**，不是占位。
- 补充：`electrical.derive_electrical_requirements` 对该板只能导出 `/3V3` 系（电压标签）与 `/5V_USB`；`/VMOT` 无电压标签、VNH7040 焊盘 `pin_type='passive'` 且无 `pin_function`（已核实 U5 焊盘属性），typed-supply 证据也覆盖不到 → 需要拓扑规则兜底（提案 P1 第 4 层）。

### 2.2 case5（6.884）：极端密度下无席位预留 + /V12 漏识别

- 板仅 51.5×19mm、52 个器件。`+3V3` 识别正常（6 个 bypass 均值 2.97，全部达标）；`/V12`（19 个端点的主电源轨）未识别 → 8 个电容均值约 10mm（C17/C16/C26/C19/C21/C20/C24/C18，距 U3.24/31/46 等脚 8.4–12.5mm）。
- 晶振负载电容 C7/C8：其网络两引脚 X1.3 与 U3.44 **相距仅 3.57mm**，但 C7/C8 被放到 16.0/13.8mm 外（环扫描显示目标点 0–4mm 环 36 个方向全被 X1/U3/U4 courtyard 与板边占满，5mm 起才有零散空位）。X1 与 U3（QFN48，旋转 270°）courtyard 夹缝仅约 1.5mm，放不下带 0.55mm margin 的电容——X1 作为“次级 IC”放置时未为其直连负载电容保留任何席位。
- 属**空间被占**（IC 阶段先占位、电容最后放置），非搜索算法本身失效。

### 2.3 case2（7.706）：单引脚电源轨容量溢出 + 边缘 IC 周边占位

- `+5V` 全板只有 U2.3 一个 IC 电源脚；5 个 +5V 电容按容值降序“先到先得”同一脚位：C3(100µF)→4.88mm、C14→8.39、C19→9.47、C20→9.63、C21→10.12mm。环扫描证实 U2.3 旁 4mm 起有空位，内环被先放置的电容占掉——**容量溢出**而非无位。
- `/VIN` 只有 U6.7 一个候选脚；U6 位于顶边（y≈5.35），环扫描 0–12mm 全部被 J4（顶边接插件带）、SW1、U2、U1 与板边堵死 → C1（100µF 电解，11.6mm 长）落到 12.77mm。接插件先于电容的放置顺序是共因之一。
- signal_passives 类 6 个（SENOR_VP、U6-COMP、U4-REGOUT、U3-V3、U4-CPOUT、U5-VBUS 的本地滤波电容）均值 9.5mm，barycenter 无法收敛到 IC 脚。
- 注：C1 作为电源入口 bulk 电容，即使贴近 U6 也部分属于口径效应（bulk 计入均值）；诚实预期见 §5。

### 2.4 去耦远 ⇔ 未布通的共因（局部拥塞）

- **case5 强共因**：7 个 LVS 开路全部位于 U3（QFN48 细间距）引脚（U3-3/6/7/10/18/41/44）；同一拥挤环（U2.8 脚 6mm 内 16 个器件）既把 /V12 与晶振电容挤出 8–16mm，又阻断径向逃线。`/LED2` 三个电阻散布在 22.9→44.0mm（板宽 51.5mm），进一步拉长链路。
- **case4 弱共因**：去耦失败是分类/目标问题（目标脚旁有空位），未布通是 `Net-(D3-K)` 的板角局部逃线（D3@(12.8,79.8)、J15@(17.5,87.0)），两者机制不同。
- **case2 无关**：unrouted=0、opens=0，去耦超标是布局容量/顺序问题。

## 3. case4 同构缺口：证据链

在 case4 的 `effective_spec.json` 上用 `topology.py` 原样复现：

- `communities` 规模 [22,18,15,11,10,10,10,10,5,2]；`repeated_channels = [['U5','U6','U7']]`，与 RUN 报告一致。
- U5–U8 同封装（`POWERSSO-36`、值 `VNH7040AYTR`、54 焊盘），`component_signature` 完全相同（`47078dd5…`）；四个驱动各自半径 1 邻域均为 24 节点、115 边。
- WL 桶：`{U5,U6,U7}` 同 hash（`1822a891…`），**U8 单独一桶**（`4ce8162a…`）。对 U5 做带属性 GraphMatcher：U6/U7 同构=True，**U8 同构=False**。
- 节点签名多重集唯一差异（其余全等）：
  - `1k` 组（sig `ba9561c8`）：U5 有 4 个 {R_CS6, R7, R16, R_CS1}；U8 只有 3 个 {R7, R16, R_CS4}；
  - `1K` 组（sig `7021088c`）：U5 有 4 个 {R_INA1, R_INB1, R_PWM1, R42}；U8 有 5 个 {R_INA4, R_INB4, R_PWM4, **R17**, R38}。
- 根源：U5/U6/U7 的 SEL 上拉电阻 R_CS6/R_CS7/R_CS5 值为 `'1k'`（小写，网 `/3V3_M1/_M2/_m3`），U8 对应位置 R17 值为 `'1K'`（大写，网 `/3v3_light`）。`component_signature`（topology.py:11-14）直接对**原始 value 字符串**做 sha256，`'1k'≠'1K'` → 签名分裂 → WL 桶分裂 → 同构失败。结构上四通道完全同构（R_CS6 与 R17 均连接 /3V3 与各自驱动的 SEL 脚，已核实）。
- 后果链：U8 未入组 → `macro_targets` 按 n=3 均匀车道（x=27.7/55.4/83.1，y=0.55H=49.8）→ U8 自由落位 (41.7,50.6)，正好插在 U5(27.7) 与 U6(54.4) 之间 → 四通道实际间距 27.7/41.7/54.4/83.1（理想 4 车道为 22.2/44.3/66.5/88.7），通道模板复用与对称逃线缺一角；`channel_alignment_error` 只统计到 3 锚点（2.83mm）。

## 4. 拓扑质量旁证（`topology_metrics` 在 RUN 放置结果上重算）

| Case | 社区数 | 重复通道组 | 社区 HPWL 跨度 max/mean (mm) | 通道对齐误差 |
| --- | --- | --- | --- | --- |
| case1 | 24 | 0 | 70.8 / 7.9 | — |
| case2 | 25 | 0 | 83.3 / 11.5 | — |
| case3 | 22 | 1 | 55.0 / 11.1 | 5.00 |
| case4 | 10 | 1 | **144.6 / 87.2**（板 110.85×90.62） | 2.83（仅 3 锚点） |
| case5 | 14 | 0 | 51.6 / 11.0 | — |

case4 社区平均跨度 87mm ≈ 板对角线尺度：Louvain 社区本身有效（community 6 恰含 {U6, R_CS2, C_CS2, C_CS5} 等通道成员），但放置阶段没有任何机制把社区成员**留在**社区目标格内——`macro_targets` 只给 IC 提供一次性目标点，无源件阶段完全不受社区约束。C_CS2 落在驱动社区（距 R_CS2 7.55mm）而指标期望其贴近 MCU 侧 ADC 脚。

## 5. 通用改进提案（全部布局侧；只依赖盲规范的拓扑/几何/属性证据；与路由修改隔离）

### P1 电源网识别泛化（修复 case4 全灭、case5 /V12、case2 本地轨）

- **证据**：§2.1/§2.2；case4 `bypass_caps=0`（avg 13.87），case5 /V12 8 电容均值约 10mm。
- **方案**：`_classify_components_and_place` 的 rail 判定在现有两层（声明 + `_is_power_net` 字面）之上扩展为四层证据，全部来自盲规范、无案例名特化：
  1. `design_requirements.power_rails` 声明（现有）；
  2. `_is_power_net` 字面（现有）；
  3. `electrical.py` 的 `voltage_label` 与 typed-supply（`pin_type=power_in/power_out` 且 function 非 GND 系）证据——复用已有推导函数，但不改变 routing scope；
  4. **拓扑规则**：某非 GND 网络若同时连接 ≥2 个“双引脚且另一端为 GND”的电容器（`_passive_kind`=capacitor）与 ≥1 个 ≥3 焊盘的非无源器件，判为去耦目标轨。
- 第 4 层会把 `/M*_CS_ADC`（2 个 GND 分流电容 + MCU ADC 脚）也判为轨——这是期望行为：ADC 输入滤波电容本就该贴 ADC 脚，指标与电气实践一致；单电容的本地滤波网（case2 的 COMP/REGOUT、case5 的晶振网）不会被误判。
- **预期指标**：case4 cap_distance_mm 13.9 → 约 5–7（/3V3、/5V_USB、/VMOT、CS 电容进入脚位目标分配）；case5 6.88 → 约 4.5–5.5；case1/case3 不回退（其轨已识别，新增候选极少，回归验证）。
- **适用条件**：所有板；对含层次命名电源网（`/3V3`、`/VMOT`）的 KiCad 工程收益最大。
- **独立测试**：合成 spec——`/PWR_X` 无标签、无 typed pin、仅拓扑证据 → 断言其电容进入 bypass 组且落在 IC 脚 4.5mm 内；单电容信号网 → 断言仍判 signal_passive；五板回归比较 cap_distance_mm/unrouted 不回退。

### P2 `component_signature` 值规范化（修复 case4 同构缺口）

- **证据**：§3；`lower()` 规范化后 `sig('1k')==sig('1K')` 已验证成立。
- **方案**：签名前把 value 解析为 SI 规范值（扩展 `_parse_capacitance_farads` 风格）：电阻按 EDA 惯例 `k/K=kilo`、`M=mega`、`R` 作小数点（`'4k7'`、`'1R'`），电容 `p/n/u/μ`，电感 `p/n/u`；解析失败回退原字符串（保守）。**禁止简单 `.lower()`**：`'1M'`(mega) 与 `'1m'`(milli) 会被错误合并，必须按单位语义解析。
- **预期指标**：case4 `repeated_channels=[[U5,U6,U7,U8]]` → 4 锚点均匀车道（x≈22.2/44.3/66.5/88.7），`channel_alignment_error` 覆盖全通道；U8 的通道成员随模板对齐，为 P3 的通道内席位创造条件；cap_distance_mm 间接受益（U8 通道的 /VMOT 电容不再漂移）。
- **适用条件**：任何含重复通道的板；case3 既有同构组不受影响（回归确认）。
- **独立测试**：双通道合成 spec 仅差 value 大小写 → 断言同组；解析单测（`'1k'='1K'='1000R'='4k7'` 等价类、`'1M'≠'1m'`、`'10nF'='0.01uF'`）；五板回归 `topology_metrics`。

### P3 去耦/负载席位预留（anchor-attached seat reservation）

- **证据**：case5 C7/C8（引脚对仅 3.57mm，电容 16mm 外，0–4mm 环全占，§2.2）；case2 C1（环 0–12mm 被 J4/SW1/板边占，§2.3）；case4 C_CS2（U1.39 旁 6mm 内 0 邻居却被放到 32mm，§2.1）。
- **方案**：放置锚定器件（≥3 焊盘 IC、晶振等有直连双引脚 GND 分流件的器件）时，立即为每个直连分流件在其相关焊盘旁预留席位——几何派生：焊盘外法线方向 1.2–3.0mm 环带内能容纳该电容 courtyard+margin 的最近合法槽位，记入 `reserved_seats`；后续所有放置阶段（接插件、其它 IC、signal_passives）不得占用他人席位；去耦/无源件阶段把器件放进自己的席位，席位失效（被更高优先级固定件覆盖）时释放并回退现有螺旋搜索。每脚席位上限由环带几何容量自适应决定，不设固定常数。
- **预期指标**：case5 晶振与 /V12 电容 ≤4.5mm（cap_distance_mm → ≤4.5 量级）；case2 C1 获得 U6.7 旁席位（→ ≤6mm）；case4 配合 P1 后 ADC 滤波电容贴 MCU。unrouted 不回退（席位同时减少细间距 IC 旁的随机占位，对逃线有正向作用）。
- **适用条件**：所有板；高密度小板收益最大；席位容量为 0 时自动回退现状，无负作用。
- **独立测试**：合成 QFN+晶振小板 → 断言负载电容 ≤4mm 且 DRC 无重叠；席位被占用时的释放回退单测；与 P1 组合的五板回归（cap_distance_mm、unrouted、mechanical_collisions 不回退）。

### P4 社区成团约束（community cohesion）

- **证据**：case4 社区 HPWL 跨度 mean 87.2/max 144.6mm（§4）；case5 `/LED2` 三电阻散布 22.9→44.0mm；case2 signal 类滤波电容均值 9.5mm。
- **方案**：signal_passives 的 `_hpwl_cost` 增加两项通用项：(a) 社区质心吸引——器件被拉向自己 Louvain 社区内已放置成员质心，权重随已放置社区成员数自适应增长；(b) GND 分流双引脚器件的“最集成端点”规则——优先贴近其信号网内焊盘数最多的器件的相应焊盘（`pin_type=input` 优先），取代纯全网重心。
- **预期指标**：`community_hpwl_mm` 显著下降；case5 LED 链成簇 → 缓解 U3 周边逃线压力（unrouted 7 → 预期 0–2 的布局侧贡献，路由侧归 Agent D）；case2 signal 类 6 个 → 约 4–5mm。
- **适用条件**：所有板；多社区大板收益最大。
- **独立测试**：成本函数单测（mock 社区成员）；五板回归比较 `topology_metrics.community_hpwl_mm` 与 unrouted。

### P5 单引脚电源轨的容量排序与多脚分散

- **证据**：case2 `+5V` 单脚 U2.3 上 5 个电容 4.88→10.12mm 递增（§2.3）。
- **方案**：同一候选脚被多电容共享时，按封装占位面积**升序**分配（小封装先进内环）；分配阶段按 P3 席位容量显式排队；超出容量时允许 bulk 电容分配到该轨的其它物理脚位（含电源入口连接器脚——指标口径外但电气正确，报告中单独标注，不冒充电气性能）。
- **预期指标**：case2 7.71 → 约 6.0–6.5（受单脚物理容量约束，这是诚实上限）；其它板不回退。
- **适用条件**：电源轨在 IC 侧暴露脚位少于去耦电容数的板。
- **独立测试**：单脚轨 + 4 电容合成 spec → 断言最大距离 ≤ 几何容量界；五板回归。

### 优先级与隔离边界

- 依赖关系：P2（签名）与 P1（分类）互不依赖，可独立落地；P3 依赖 P1 的候选集合；P4 独立；P5 依赖 P3。
- 改动面：`cloud_pcb/general_solver/topology.py`（P2）、`run_blind.py` 放置段（P1/P3/P4/P5），复用 `electrical.py` 的识别函数；**不触碰**路由参数、DSN/SES 生成、GND fanout 与任何路由侧行为。
- 验证纪律（按 AGENTS.md）：每个提案先加独立测试，再以单一 `--hypothesis` 跑完整五板轮次；不做筛选式晋升，五板为已暴露回归数据，不用于宣称新盲测分数。

## 6. 复现脚本（/tmp）

- `/tmp/agentc_cap_analysis.py`：逐电容距离分解（分类 × 距离 × 目标脚）。
- `/tmp/agentc_case4_topology.py`：case4 同构复现（WL 桶、GraphMatcher、签名多重集差异）。
- `/tmp/agentc_congestion.py`：值字符串核实 + 拥挤度（6mm 邻居数）× 距离 × 开路明细。
- `/tmp/agentc_final_evidence.py`：分类统计、轨声明覆盖、关键几何（X1-U3 间距、/LED2 散布、D3-J15、车道位置）。
- `/tmp/agentc_blockers.py`：阻塞归因环扫描（目标脚周围 36 方向 × 0–12mm 的占位来源）。
