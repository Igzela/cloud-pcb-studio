# Agent D 独立验证与 ECAD/MCAD 审查报告

**审查对象**：`artifacts/optimization/runs/20261117T112159329967Z-octagonal-fanout/`（下称 RUN），只读诊断，未改动任何源码、输入或既有产物。

**审查范围**：
1. `cloud_pcb/general_solver/run_blind.py` 的 `_audit_blind_output`（L1682–1827）与 `run_blind_solver` 硬门禁汇总（L2073–2085）
2. `cloud_pcb/verify_gate/gerber_drc.py`（重点 `audit_general_board` L607–704、`audit_annular_geometry` L292–307、`classify_router_violations` L579–604）
3. `cloud_pcb/general_solver/compare_reference.py`（`_extract_metrics` 独立审计路径）
4. `cloud_pcb/general_solver/semantics.py`（`component_envelope`/`infer_rf`/`mechanical_check`）
5. `cloud_pcb/general_solver/assembly_viewer.mjs` 与 `scripts/inspect_assembly.mjs`，及包装层 `scripts/optimize_pcb.py` 的 `inspect_browser`（L122–132）

RUN 五板状态核对：机械门禁五板全败（碰撞 0，unverified 46/59/74/108/52，合计 339），RF 仅 case2 败（U1 天线证据 unknown），浏览器检查五板全过（含像素 std 25.98–46.46，阈值 10）。以下"盲点"均按"补证据/更严格"方向给出，不涉及任何放宽门禁的建议。

---

## 一、验证盲点清单

### 1.1 已确认覆盖良好的部分（非盲点）

- **独立 LVS（`audit_general_board`）** 按层建模，跨层只认真实 plated 特征（PTH 焊盘、按 `t.layers` 跨度解析的过孔）；覆盖 pads（含 `*.Cu`）、Segment、Arc（`copper_arc_points` 精确采样）、Via、Zone `filledPolygons`（逐填充块，不假设同网络孤岛连通）。短路检查覆盖全部四层；开路并查集对 plated 相交用 `area>1e-7`、焊盘质心匹配 ≤0.0002mm，方向偏严。
- **report_metrics（optimization.py L53–87）** 从原始审计字段独立重算门禁，并与 solver 声称值交叉校验（"claimed gate contradicts audit" → evidence error），evidence error 会使 `compare_candidate` 判 invalid。反假通过设计有效。
- `classify_router_violations` 的豁免仅针对"同器件、同网络、几何相交"且带证明；`real_count=max(raw-excluded, len(real))`，缺失记录不会被静默算作清除。
- 机械 `mechanical_check` 对未核实包络一律判 unverified → 门禁失败；`report_metrics` 另核 `collision_count==len(collisions)`、`all_envelopes_verified==(not unverified)` 一致性。

### 1.2 盲点与加固建议（均为加严方向）

| # | 盲点 | 位置 | 风险方向 | 建议（加严/补全，不降门禁） |
|---|------|------|----------|------------------------------|
| B1 | **内层（In1/In2）无独立最小间距审计**：`audit_general_board` 只查短路（相交面积），不计算内层最小净间距；`min_inter_net_clearance_mm` 仅在旧版 `_audit_blind_output` 中算 F/B，且未入门禁；内层间距仅靠路由器报告间接覆盖 | gerber_drc.py L651–657；run_blind.py L1796–1809、L2073–2085 | 内层亚阈值间距可静默通过 | 将逐层最小净间距纳入独立审计并以 netclass clearance 为 required value 入门禁 |
| B2 | **孔环只测 F/B 两层**，内层 via/PTH 焊环未验证；且 `audit_annular_geometry` 用"钻孔中心到铜并集边界距离 − 半径"，焊盘与铺铜经热桥合并时会**高估**孔环（度量的是铜范围而非焊盘 land） | gerber_drc.py L292–307 | 内层焊环缺失不可见；个别孔环读数虚高 | 扩展至 In1/In2；在钻孔邻域窗口内测量"与孔相连的 land 边界"而非整层并集边界 |
| B3 | **NPTH（安装孔）到铜的间距无任何独立审计**（`_build_4layer_pours_and_stitching` 已做 0.5mm 减除，但无验证）；安装孔仅在机械侧对器件体检查 | run_blind.py L1328–1335；semantics.py L110–115 | 孔壁到铜间距不足可静默通过 | 新增 NPTH→各铜层最小间距审计（钻径+电镀补偿为基准） |
| B4 | **通用路径无最小钻径门禁**（board1/board2 专用审计有，RUN 通用硬门禁没有） | gerber_drc.py L1026–1035（专用）vs run_blind.py L2073–2085（通用） | 小于工艺极限的钻孔可过门禁（RUN 实际最小 0.2mm，未触发） | 以 fab 规则为基准把 min-drill 纳入通用硬门禁 |
| B5 | **机械 Z 区间硬编码板厚 1.6mm**（`(-1.6-z0-h, -1.6-z0)`），与 scene 使用的 `board.general.thickness` 脱节 | semantics.py L104 | 板厚变化时底面碰撞检查静默失真（RUN 五板均 1.6，未触发） | 改用 spec/板实际厚度参数 |
| B6 | **机械检查不含器件体对板框的外伸/出界审计**（只有体-体、体-安装孔、体-keepout）；器件整体落出板框不会产生碰撞 | semantics.py L96–120 | 出界放置逃过机械门禁 | 新增 body⊆outline 审计（`edge_connectors` 声明的器件按证据豁免） |
| B7 | **Gerber 与板级铜几何无交叉核对（导出保真度）**：审计一半读 Gerber（parse/孔环/丝印/板边距）、一半读 KiCad 板（LVS），二者从未做逐层对称差验证 | run_blind.py L1682–1827 vs gerber_drc.py L607–704 | 导出 bug（丢对象/坐标系错）可在两套各自通过的检查间漏网 | 增加 board 派生几何 vs Gerber 并集的逐层 symmetric-difference 上限门禁 |
| B8 | **via-in-pad 热焊盘判定为启发式**（≥6 焊盘封装内被其余焊盘凸包包含且 min 尺寸 ≥0.8 且同网络即豁免）：8 针连接器内部大焊盘理论上可被误豁免 | gerber_drc.py L691–703 | 边缘场景假豁免（RUN 未触发） | 豁免需附加"焊盘网络与过孔网络一致且焊盘为封装内部 EPAD 形态"双重证据，豁免清单随报告留痕 |
| B9 | **焊盘形状覆盖 rect/circle/oval/roundrect；KiCad `custom`/带 primitives/`trapezoid` 焊盘会静默退化为外接 box**（LVS 与导出共用 `_pad_world_polygon_kicad`） | run_blind.py L903–928；gerber_drc.py L356–385 | 未来引入异形焊盘时 box 近似可掩盖开路（连通性虚增） | 对未支持形状显式报 unknown 并阻塞，而非静默近似（RUN 五板仅 rect/roundrect/circle/oval，未触发） |
| B10 | **RF 候选识别依赖关键词表**（`antenna/wroom/wireless/wifi/bluetooth`），未命中词的 RF 模块不会产生 keepout、也不会进 unresolved——`all(... for k in [])` 为真空真，RF 门禁**默认通过** | semantics.py L64–66；run_blind.py L2083 | RF false-negative：未识别模块静默过门禁 | 扩大候选检测（`rf_/module/radio/ble/zigbee/lora/gsm/lte/gps/2g4/868/915` 等证据词 + "大焊盘数+屏蔽框焊盘"形态启发式），任何候选缺本体几何一律 unresolved → 门禁失败 |
| B11 | `_gerber_layer_union` 极性状态机以首对象默认 dark 起步；若文件以 clear 极性开头，首个 batch 会被并而非减 | gerber_drc.py L150–177 | 理论边缘场景（当前导出均 dark 开头） | 以首对象真实极性初始化状态机 |
| B12 | 丝印只查 mask 重叠；丝印到板边/丝印压钻孔未查（轻微） | run_blind.py L1713–1717 | 轻微 | 可选：丝印-钻孔、丝印-板框距离审计 |

**说明**：`_audit_blind_output` 的旧版投影 LVS 结果现被独立审计整体覆盖（run_blind.py L2068–2069），其"全层投影并查集"把跨层连通性建立在投影假设上的旧问题已随覆盖失效，但旧值仍以 `legacy_projected_lvs_opens` 保留在报告里，注意不要在下游再消费它。

---

## 二、机械未知项证据补齐方案

### 2.1 现状统计（RUN 五板，assembly_scene.json × effective_spec.json 交叉核对）

unverified 合计 339（46/59/74/108/52），72 个不同封装名。按证据等级分类（分析脚本：/tmp/agent_d_categorize.py、/tmp/agent_d_breakdown.py，只读，不触产物）：

| 等级 | 类别 | 数量 | 占比 | 依据来源（类比电解电容 C_Elec_DxH 先例） |
|------|------|------|------|------------------------------------------|
| A1 | EIA 片状件（R/C/L/D/LED/Fuse 的 0402/0603/0805/1206，含 OLIMEX `*_5MIL_DWS` 变体） | **245** | 72.3% | KiCad 库命名内嵌公制本体码（`C_0603_1608Metric` → 本体 1.6×0.8mm，IPC-7351B 命名约定）；高度按保守上限（见 2.2） |
| A2 | JEDEC 注册标准封装（SOT-23×7、SOIC/SSOP×5、DO-214 SMA/SMB×4、SOT-223×2、QFN×2、LQFP×1、SOD-123×1） | **22** | 6.5% | JEDEC 注册外形高度上限：MS-012 SOIC ≤1.75、MS-013 SSOP、TO-236 SOT-23 ≤1.12、TO-261 SOT-223 ≤1.8、MO-220 QFN ≤1.0、MS-026 LQFP ≤1.6；名称多已编码本体 XY（`SOIC-8_3.9x4.9mm`、`LQFP-32_7x7mm`） |
| A3 | 封装身份即具体 MPN（JST XH×6、JST GH×4、Phoenix MKDS×4、POWERSSO-36×4、USB-C GCT/HRO×3、AMASS XT30/XT60×2、XKB TS-1187A×2、ESP32-WROOM×2、TL3342×2、ASM330LHHXTR、ASDMB、WS2812B、Sunlord SWPA6028S、AP63203WU 各 1，Buzzer 12×9.5 见下注） | **35** | 10.3% | 器件手册标注尺寸（如 JST B4B-XH-A 顶入式高 7.0mm；Phoenix MKDS-3-2 高 10mm；ESP32-WROOM-32D 本体 18.0×25.5×3.35mm max） |
| A4 | 名称内嵌高度 token（`L_7.3x7.3_H4.5`） | **1** | 0.3% | KiCad 库 `_H4.5` 约定 = 高 4.5mm（现有 `explicit_h` 正则要求 "mm" 后缀而漏配，属正则覆盖缺口，非证据缺失） |
| B1 | 2.54mm 排针/排母（PinHeader×16、PinSocket×1） | 17 | 5.0% | **必须保持 unknown**：名称只编码针距/排数/方向，高度变体多（8.5/10/11.96mm…），无编码证据 |
| B2 | SolderWirePad（无实体本体） | 8 | 2.4% | 无本体证据（见 2.4 处理选项） |
| B3 | Fiducial（无实体本体） | 4 | 1.2% | 同上 |
| B4/B5 | 自定义封装（WQFN24_RRW_TEX、2-Pin_PSK）与其他无证据项（OLIMEX CD32、TM211Q01FM22、USB3_A_MALE、Oscillator-3.2x2.5mm） | 7 | 2.1% | **必须保持 unknown**（value 中 MPN 可在后续以手册证据逐项升级，如 NRS3015→3×3×1.5，但需逐条引用） |

> 注：Buzzer_12x9.5RM7.6 名称只编码本体 XY（12×9.5）与脚距 RM7.6，高度未编码，保守起见应从 A3 移至"条件项"，**稳妥可推导数为 302**；value 字段带 `0402/0805/1206` token 但 footprint 名无证据者仅 case5 X1 一例（3225 晶体，高度不可由 3225 推，保持 unknown）。

### 2.2 可安全覆盖的推导规则（补证据，不是降门禁）

1. **EIA 片状件（245 项）**：XY 由公制码直接得（1608→1.6×0.8、2012→2.0×1.25、3216→3.2×1.6、1005→1.0×0.5）；高度取**保守上限**——厚膜电阻按 IEC/JIS 标称（0402 0.35、0603 0.45、0805 0.55、1206 0.60mm）；MLCC 按村田/TDK 各 case size 的最大 T（0402 ≤0.55、0603 ≤0.9、0805 ≤1.45、1206 ≤1.8mm）；LED/二极管/保险丝取同尺寸上限。**取上限使 Z 重叠更易触发 → 只会更严，不会放宽碰撞判定**，与"不降低门禁"一致。
2. **JEDEC 标准封装（22 项）**：高度一律取 JEDEC 注册上限（同上表），XY 用名称编码值或注册外形。
3. **MPN 身份（34+1 条件项）**：逐项手册引用，与电解电容 `C_Elec_DxH` 先例同一证据等级（名称即身份）。每个条目必须在代码中带手册编号/版本引用，禁止无引用的"典型值"。
4. **A4 高度 token（1 项）**：把 `(?:^|_)h([0-9.]+)mm` 扩展为同时接受 `_H4.5` 形式（仅当 token 位于封装名本体段，避免误匹配网络名/值）。
5. 推导结果应带 `height_source='JEDEC MS-012 cap'/'EIA max-height table'/'MPN datasheet <id>'` 等**可审计来源串**，并在 scene JSON 保留推导前后对照。

### 2.3 推导后的剩余量预估

| Case | unverified 现状 | 可推导 | 预计剩余 | 剩余构成 |
|------|------|------|------|----------|
| case1 | 46 | 30 | **16** | 8 排针 + 8 SolderWirePad |
| case2 | 59 | 55 | **4** | 4 排针 |
| case3 | 74 | 70 | **4** | 1 排座 + 2×WQFN24 自定义 + 2-Pin_PSK 自定义 |
| case4 | 108 | 104 | **4** | 4 排针 |
| case5 | 52 | 44 | **8** | 4 Fiducial + CD32/TM211/USB3_A/X1 |
| **合计** | **339** | **303** | **36** | B1×17、B2×8、B3×4、B5×7 |

机械门禁不会因此自动转绿：剩余 36 项仍需输入侧补 `height_mm`/`body_bbox_mm`（如排针实际选型的手册值），或由 B2/B3 处理选项（见 2.4）消化 12 项后余 **24 项**。若排针选型确定（如标准 8.5mm 直针），B1 的 17 项可由输入证据（而非启发式默认）消除。

### 2.4 无本体项（Fiducial/SolderWirePad）的处理选项

- **选项 1（推荐，更精确而非放宽）**：新增"非组件制造特征"类别——封装身份为 fiducial 标记或裸焊盘时，从碰撞集合剔除但单独留痕（`non_component_features` 列表入审计），机械门禁语义变为"每个**有实体的**包络均已核实"。剔除必须带身份证据（封装名正则 + 无电气网络或仅单网络），并在报告中可见。
- **选项 2（最保守默认）**：保持现状计入 unverified，直到输入侧显式标注其非组件属性。两者都不削弱任何既有检查；选项 1 只是消除信号稀释。

### 2.5 负例测试方案（证明无依据器件不会被标为已核实）

1. **单元负例**：构造合成组件——(a) 自定义封装名 + 无 `height_mm` → 必须 unverified；(b) value 含 "0402" 但封装名无证据（如 case5 X1 形态）→ 必须 unverified（尺寸 token 只允许来自封装名身份，value 仅作辅助，除非条目级 MPN 表命中）；(c) 畸形电解 token（`C_Elec_x7.7`、`C_Elec_6.3x`）→ unverified；(d) `_H4.5` 出现在 value 而非封装名 → 不得触发 A4。
2. **属性测试**：随机生成 refdes/封装名/value 三元组，断言"verified ⇒ （本体来源 ≠ pad envelope fallback）且（高度来源 ∈ 证据表）"恒成立；断言证据表外输入绝不 verified。
3. **回归钉死**：对五板 spec 重跑 `mechanical_check`，断言 (a) 原 verified 集合只增不减、(b) 新增 verified 逐项映射到带来源串的表条目、(c) 剩余 unverified 恰为预期 36（或 24，取决于 2.4 选项）、(d) 任何表的改动都会改变该计数并触发测试失败。
4. **上限方向验证**：取已知更高变体（如 0805 MLCC 1.45mm 高容值料），断言推导高度 ≥ 该变体实高（证明取的是上限而非典型值，碰撞判定方向偏严）。
5. **抽样手册核对**：每类别抽 1–2 条目与 KiCad 官方 3D 模型参数/器件 PDF 比对并留引用，防止表内出现无源数值。

---

## 三、RF（case2 U1）结论

**判定：输入数据缺失为主，推断逻辑无缺陷；不应降门禁，应补输入证据。**

- case2 U1：`value=ESP32-WROOM-32E`，`footprint_name=RF_Module:ESP32-WROOM-32D`，60 焊盘，pad bounds [-8.75, -8.25, 8.75, 9.51]，**spec 中无 `body_bbox_mm` 且无 `antenna_keepout_mm`**（inputs/case2.json 与 effective_spec 一致）。`infer_rf` 按设计拒绝凭空发明几何，返回 `confidence=unknown` → `rf_unknown` 非空 → 硬门禁失败。这是刻意的严格行为，不是逻辑缺口。
- 对照 case4 U1（ESP32-S3-WROOM-1）：spec 携带 `body_bbox_mm=[-9,-12.75,9,12.75]`，同一 `infer_rf` 逻辑算出 -Y 侧 7.49mm 无焊盘悬垂（>2.0 阈值）→ 生成四层 keepout，独立审计四层铜覆盖均为 0.0 → 通过。证明推断链路与门禁本身工作正常。
- **可推导性**：ESP32-WROOM-32D/E 为具体 MPN，Espressif 手册本体 18.0×25.5mm（±0.1）。若输入补齐 `body_bbox_mm=[-9,-12.75,9,12.75]`，现有推断会在 +Y 侧得到 3.24mm（≥2.0）悬垂并生成 keepout [-9, 9.51, 9, 12.75]——**无需新增逻辑分支，只缺输入证据**。与 2.2-A3 的 MPN 证据等级一致。
- 处理建议（不降低门禁）：
  1. **修输入**：在 case2 blind spec 中为 U1 增补手册依据的 `body_bbox_mm`（或直接 `antenna_keepout_mm`），输入重钉 sha256 后重跑——这是 review.md 已列的 P1 任务"补齐天线几何证据"。
  2. **保持拒绝**：无本体几何的天线候选继续判 unknown → 门禁失败；禁止以"模块常见布局"合成 keepout。
  3. **防 false-negative（B10）**：扩展候选检测词表/形态启发式，使未命中词的 RF 模块也进入 unknown（门禁失败）而非真空通过；可选增强：keepout 区除"四层零铜"外追加"区域完全位于板框内且与板边关系符合手册禁布区方向"的一致性核对。

---

## 四、3D 检查（assembly_viewer.mjs + inspect_assembly.mjs）审查

### 4.1 现状结构

浏览器侧断言：`webgl`（`!!renderer.getContext()`）、`componentCount>0`、`nonFlatMeshes>=componentCount`、首复选框 DOM 状态翻转、`maskColor` 标志翻转、无 console/pageerror、无外部请求。包装层 `optimize_pcb.inspect_browser`（L122–132）追加 oblique/top/side 三图像素 std>10，且 `rc==0` 才算通过；失败 → `evidence_errors` → 候选判 invalid。RUN 五板 std 25.98–46.46 全部通过。

### 4.2 可漏检点与加强建议

| # | 可漏检点 | 证据 | 加强建议（加严方向） |
|---|----------|------|----------------------|
| V1 | **`nonFlatMeshes` 统计包含引脚网格**（非 pin 器件引脚 z 高度 0.13 > 0.1 阈值），任何带焊盘的器件即使**本体网格塌陷为 0 高**也自动计入 nonFlat——本体渲染失败可假阳性通过 | viewer L30、L38（引脚 `BoxGeometry(...,.13)`）；inspect L19 | 只统计 userData 含 `refdes` 且不含 `pad` 的本体网格；并断言本体 z 伸展与 scene `height_mm` 一致（如 ≥min(height,0.1) 且偏差 ≤20%），把渲染与场景数据绑定 |
| V2 | **截图本身在 inspect_assembly.mjs 内完全未验证**；像素 std 只在包装层、且只看 oblique/top/side——`assembly_black.png`（黑油切换后）无任何像素验证；std>10 可被 HTML 面板文字/镀铬单独满足（WebGL 全空但面板在，std 仍可能过 10） | inspect L22–30；optimize_pcb.py L128–130 | canvas 区域裁剪后统计（剔除 DOM 面板）；增加唯一颜色数与非背景像素占比下限；对 black 截图同样做 std/占比检查 |
| V3 | **图层切换只断言复选框 DOM 状态翻转**（原生行为），不验证 `groups[name].visible` 与渲染变化——处理器失效仍可通过 | inspect L32–35 | 在页面上下文断言 `pcbViewer.groups[name].visible===false`；切换前后截图像素差必须超阈值（证据：某层真的消失/复现） |
| V4 | **阻焊换色只断言 `window.pcbViewer.maskColor` 标志**（监听器赋值），不验证材质颜色或像素变化 | inspect L27–31；viewer L49 | 暴露并读取 `maskMaterials[0].color` 断言色值变化；黑/绿截图同区域像素差超阈值 |
| V5 | **悬停拾取与拖拽/滚轮完全无断言**（mouse move/down/up/wheel 执行后即丢弃）——Raycaster、拾取高亮、相机控制全坏也照样通过 | inspect L36 | 悬停已知器件中心，断言 `#hover` 文本含其 refdes/网络；断言拖拽后 `camera.position` 变化、滚轮后相机距离变化 |
| V6 | `webgl` 断言为 `!!getContext()`——**恒真**，上下文丢失也测不出 | inspect L20 | 改为 `!renderer.getContext().isContextLost()` 并断言渲染后 `renderer.info.render.calls>0` |
| V7 | viewer 的 In1/In2 层 z 位置硬编码（-0.245/-1.328，对应 JLC04161 四层），与 scene `thickness_mm` 不联动（B.Cu 用 -T 已对） | viewer L25 | 层 z 由 stackup/thickness 派生；不同板厚时视觉检查不致静默失真（视觉层，轻微） |
| V8 | 控制台监听只收 `pageerror` 与 console `error`；WebGL 运行时警告、资源加载 `requestfailed` 未覆盖（file:// 下影响小） | inspect L10–12 | 可选：收集 warning 级别并只允许白名单；记录 failed 请求为失败 |

**优先级**：V1/V2/V3 为可能产生假阳性通过的实质缺口，建议先补；V4/V5/V6 补齐交互真实性；V7/V8 为健壮性增强。所有建议均为"检查更严格"，不触碰现有通过标准。

---

## 五、结论摘要

1. 验证栈的反假通过骨架（独立 LVS + 重算交叉校验 + 证据错误判 invalid）是健全的；发现 12 项可加严盲点，最实质的是 **B1 内层无独立间距审计、B3 NPTH-铜间距无审计、B7 Gerber↔板导出保真度无交叉核对、B10 RF 候选漏识别时门禁真空通过**。
2. 机械 339 个 unverified 中 **303 个（89%）有标准证据可推导**（EIA 245 + JEDEC 22 + MPN 35 + 高度 token 1；含 1 个 Buzzer 条件项则稳妥值 302），剩余 36 个（排针/排母 17、无本体 12、自定义/杂项 7）必须保持 unknown 或由输入侧补证据；五板剩余预估 16/4/4/4/8。推导一律取保守上限/手册值并带来源串，负例测试方案见 2.5。
3. case2 RF 失败是**输入证据缺失**（`body_bbox_mm`/`antenna_keepout_mm` 未随 spec 给出），推断与门禁逻辑按设计工作；修复路径是补输入证据，不是改逻辑放行。
4. 3D 检查存在 V1（引脚网格冒充立体本体）、V2（black 截图无像素验证）、V3（图层切换只看 DOM）三个可假阳性缺口，建议按 4.2 加严。
