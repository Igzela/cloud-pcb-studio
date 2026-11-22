# 高密逃逸、物理回流平面 DSN 与工程修复轮次（`dense-escape-repair`）

本轮基于 `origin/work/pcb-research-handoff`（叠加 PR #3 / #4 / #5）完成了一次多模块审计与通用求解器改进，并将未验证的实验路由策略收敛在统一变体 `--variant dense-escape-repair` 下（按全套件统一执行，无按板分支，不含任何项目位号、网络名或人工参考坐标硬编码）。

## 通用修改与审计修复

1. **Gerber 中空光圈与独立层检查（`cloud_pcb/verify_gate/gerber_drc.py`、`cloud_pcb/general_solver/run_blind.py`）**
   - Gerber 解析与独立 LVS 铜几何恢复现支持圆形、矩形、长圆中空光圈（`C`/`R`/`O` 带内孔参数，减去内孔避免误判短路），并补充 `unit is None` 保护。
   - 修复盲跑 Gerber 导出中内层（`In1_Cu`/`In2_Cu`）通孔焊盘按全部 `_is_gnd_net` 误闪的问题，收紧为仅对当前铺铜参考地网络 `pad.net.number == gnd_net` 闪盘；拆分正反面丝印（`F_SilkS`/`B_SilkS`）与阻焊（`F_Mask`/`B_Mask`）裁剪，并为背面封装正确分配 `B.SilkS`/`B.Fab`/`B.CrtYd`。

2. **3D 装配自动构图与背面器件渲染修复（`cloud_pcb/general_solver/assembly.py`、`cloud_pcb/general_solver/assembly_viewer.mjs`）**
   - 整合自动贴合相机构图（斜视/俯视/侧视均在 HUD 侧栏右侧安全区自适应视口偏移），并修复手动拖动后窗口缩放未调用 `camera.clearViewOffset()` 及抬高器件引脚未按 `base_z_mm`/`standoff_mm` 偏移的两个缺陷。
   - 修复 `assembly.py` 读取封装实际所在层（`fp.layer == "B.Cu"`），确保双面装配器件在 3D 视图中正确渲染于板底面。

3. **双面布局压缩加速与回滚（`cloud_pcb/general_solver/two_side_packing.py`、`cloud_pcb/general_solver/placement_recovery.py`、`cloud_pcb/general_solver/run_blind.py`）**
   - 为 `Occupancy` 引入缓存包围盒（AABB）预筛与惰性几何变换，30 器件高密双面布局+Shapely 验证实测 0.49 秒完成。
   - 将 `placement_recovery` 的平铺微米探测替换为三层跨全板宽度/高度的坐标分层搜索，并接入带边界与探测预算的事务性阻塞器件重排回滚；`two_sided_recovery` 中补充对 `PlacementGeometryUnsupported` 的兜底与背面焊盘 `x_rel_mm` 镜像。

4. **统一变体 `dense-escape-repair`（`cloud_pcb/layout_route/dsn_ses_bridge.py`、`cloud_pcb/general_solver/direct_repair.py`、`cloud_pcb/general_solver/direct_repair_replay.py`、`cloud_pcb/general_solver/run_blind.py`、`scripts/optimize_pcb.py`）**
   - **安全引脚引用**：主 DSN 启用 `safe_pin_references=True`，修复带括号/标点位号在 FastRoute 解析中被静默丢弃的问题。
   - **细长热焊盘与邻接接地焊盘传播**：支持 `min(sx, sy) >= 1.2 mm` 且面积 `>= 2.0 mm²` 的内部热焊盘识别，以及环路后接触焊盘接地状态传播。
   - **电源类同封装直连补线与部分孤岛合并**：`direct_repair` 支持 `DEFAULT` 与 `POWER*` 类别按原网类线宽/间距/层规则生成直连补线（仍禁止阻抗与差分网类），并在独立 LVS 不新增开路、不迁移开路的前提下接受部分连通岛合并；重放支持向非空 `(wiring ...)` 追加 `(type fix)` 线段。
   - **按失败触发的高密逃逸候选（`_run_dense_escape_candidate`）**：仅在常规模式仍存在未布通或真实间距违例时触发（对已 0/0/0 洁净板零扰动）。候选启用物理 `In1.Cu` 参考地平面 DSN（保留全部接地焊盘网络归属、居中热焊盘多层 padstack 以满足 `(via_at_smd off)`、保守外框与异网通孔/NPTH 挖窗、失效封闭降级）、按网类规则尺寸过孔（如 `0.50/0.25 mm`）、八边形禁布保护、带 SMD 包围盒保护的固定地过孔间距（`smd_via_clearance = max(0.165, clr + 0.02)` + `use_smd_bbox_guard=True`）、对齐结构净空以及与组内耦合间距解耦的差分外网间距，最终由严格板内帕累托门槛决定是否采纳。

5. **第二轮根因收敛：同层 C 形绕行、受阻热焊盘局域传播与非差分缩颈重试（`c-bypass-and-epad-bridge`）**
   - **同封装共线受阻引脚的同层三段正交 C 形绕行（`cloud_pcb/general_solver/direct_repair.py`）**：当同一封装内轴对齐共线的两个电源类通孔/焊盘之间夹有异网引脚（1 段直线被阻断）时，在 `allow_power_classes=True` 下按有界偏移序列 `offset_um ∈ (650, 750, 800, 900, 1000, 1150, 1300, 1500)` 及两侧方向搜索同层三段正交 C 形绕行路径，闭合共线受阻的同封装电源孤岛（如 `case10` 上被 4 个信号脚隔开的 `VBUS` 引脚对）。
   - **中心热过孔受阻时的同封装邻接焊盘局域重排与在线接地传播（`cloud_pcb/general_solver/run_blind.py`）**：保持全局 `gnd_smd_pads` 排序完全不变（确保中心热过孔成功的板生成的 `board.dsn` 100% 字节级一致）；仅当某个 `is_interior_epad` 因背面器件冲突无法打中心热过孔而进入 `unvia_records` 时，将该封装（`fp_idx`）剩余待处理接地焊盘中与热焊盘接触的引脚排在孤立外围引脚之前，并在接地打孔成功时通过 `_mark_grounded` 在线传播给 `unvia_records` 中的热焊盘，使同封装孤立接地脚直接向内短接至热焊盘（`0.31 mm`），消除向外斜伸的固定扇出桩线与过孔对相邻反馈脚（如 `case10` `Net-(U3-FB)`）逃逸通道的封锁。
   - **非差分细间距板 `125 µm` 缩颈重试（`cloud_pcb/general_solver/run_blind.py`）**：解除 `_run_dense_escape_candidate` 中 `--router.neck_width_um=125` 仅对含差分对板触发的限制；当无差分对的细间距板（如 `case3` `0.40 mm` 间距 QFN）在 `160 µm` 逃逸后仍有未布通时自动执行 `125 µm` 缩颈重试，仅在 `pairs_lines` 非空时附加 `--tune`。
   - **候选后处理内层反焊盘净空与铺铜规则对齐（`cloud_pcb/general_solver/run_blind.py`）**：保持路由前 `_build_physical_ground_plane_dsn_spec` 的保守挖窗不变，将高密逃逸候选路由后的 `_build_4layer_pours_and_stitching` 内层异网过孔/通孔反焊盘净空对齐至 `0.221 mm`（与 `z.clearance = 0.22 mm` 及 `inner_layer_cutouts` 一致），使 `case5` 高密逃逸候选不仅将开路从 `9` 降至 `5`，同时将 `In1.Cu` 地参考填充从 `75.51%` 提升至 `76.31%`。

6. **第三轮根因收敛：保守外切多边形精确几何复核与细间距二级微逃逸（`exact-geometry-and-micro-escape`）**
   - **严格外切凸多边形焊盘导出与只读精确几何复核回放（`cloud_pcb/layout_route/dsn_ses_bridge.py`、`cloud_pcb/general_solver/exact_geometry_replay.py`）**：在 `board_to_dsn` 中新增可选参数 `exact_pad_shapes=True` 与 `snap_angle="none"`，对 `oval`、`roundrect` 及非 90° 旋转焊盘导出 `+1.0 µm` 外扩的 CCW 严格外切凸多边形（实测对所有测试形状、奇微米尺寸与任意倾角均满足 `0.000000 mm²` 漏铜，零漏报）。当路由后仍存在含曲面/旋转焊盘的 `unfixable: true` 45° 八边形包络误报时（如 `case10` 上真实解析间隙 `0.1466 mm > 0.130 mm` 却被默认 45° 八边形截断为 `0.0731 mm` 的 `J3-1`/`J2-S1`），自动执行双控只读 FastRoute 复核回放（锁定零过孔改动、零走线端点/层/网偏移、零线宽缩窄，仅允许因焊盘外扩产生的 `≤ +0.01 mm` 保守入口桩线增宽，并保留 `fastroute_45deg_report.json` 原始报告备查），将 `case10` 真实间距违例从 **4 归零至 0**。
   - **按失败触发的细间距二级微逃逸升级（`cloud_pcb/general_solver/run_blind.py`）**：仅当常规高密逃逸（Tier 1）仍存在未布通且板面包含超细间距焊盘（`min_pad_gap <= 0.22 mm`）时触发（对已 `0` 未布通的 `case1 / case2 / case3 / case4 / case10 / case11` 零扰动）：
     - 修复 `_add_gnd_fanout_vias` 中与通孔热过孔重叠的 SMD 热焊盘漏调用 `_mark_grounded` 的缺陷，并在 Step 2 选择最短内桥（平局取最大异网净空）目标，消除 QFN 外围接地脚向外斜伸固定过孔封锁相邻差分脚的问题；
     - 引入细间距多引脚 IC（`≥ 12` 焊盘、间距 `≤ 0.55 mm`）外缘 `2.0 mm` 逃逸环避让惩罚，防止周边无源器件固定地过孔侵入 IC 逃逸走廊；
     - 在满足 4 层制造门禁（`min_drill = 0.20 mm`、`min_annular_ring ≥ 0.10 mm`、`min_trace = 3.5 mil / 88.9 µm`）的前提下，采用 **`0.42/0.20 mm` 过孔**（孔环 `0.110 mm ≥ 0.10 mm`）与 **`100 µm` 缩颈**（翻转 `0.40 mm` 间距跨 1 脚逃逸不等式为正裕量），同时对未指定载流/线宽的细间距电源类限宽 `≤ 0.30 mm`、同步内层反焊盘与内层铺铜净空至 `0.160 mm`，将 `case5` 未布通与 LVS 开路从 **5 降至 1**、`In1.Cu` 地填充提升至 **78.86%**。

## 复现命令与验证结果

### 第一轮（`20261120T174242641430Z-dense-escape-repair`）

```bash
source scripts/env.sh
python -m scripts.optimize_pcb run \
  --label dense-escape-repair \
  --variant dense-escape-repair \
  --cases all \
  --hypothesis "Integrate hollow Gerber apertures, 3D auto-framing camera fixes, multi-ground Gerber and bottom-silk isolation, AABB/3-tier two-sided packing with rollback, safe primary DSN pin quoting, narrow-EPAD and touching-pad ground propagation, power-class partial-island direct repair, and unrouted/spacing-triggered dense-escape candidate with physical In1.Cu return plane, rule-sized 0.50/0.25 vias, SMD bbox guard, aligned structure clearance, and decoupled diff-pair foreign clearance"
```

- 轮次目录：`artifacts/optimization/runs/20261120T174242641430Z-dense-escape-repair/`（`pytest` **292 passed in 92.96s**，全轮 **2551.209s**，**0 项退步、6 项改善**，晋升基线）。

### 第二轮（`20261121T113551029733Z-c-bypass-and-epad-bridge`）

```bash
source scripts/env.sh
python -m scripts.optimize_pcb run \
  --label c-bypass-and-epad-bridge \
  --variant dense-escape-repair \
  --cases all \
  --hypothesis "Add same-layer 3-segment orthogonal C-bypass in direct_repair for collinear blocked same-footprint TH pins, reorder same-footprint EPAD-touching tabs with online ground propagation when center thermal via is blocked, enable 125um necking retry on non-diff-pair boards in dense-escape candidate, and align candidate post-route inner-plane antipad clearance with 0.221mm zone clearance"
```

- 轮次目录：`artifacts/optimization/runs/20261121T113551029733Z-c-bypass-and-epad-bridge/`（`pytest` **295 passed in 96.03s**，全轮 **2631.325s**，**0 项退步、11 项改善**，晋升基线）。

### 第三轮（`20261122T134458373666Z-exact-geometry-and-micro-escape`，当前活动基线）

```bash
source scripts/env.sh
python -m scripts.optimize_pcb run \
  --label exact-geometry-and-micro-escape \
  --variant dense-escape-repair \
  --cases all \
  --hypothesis "Add strictly-circumscribed convex polygon padstack export and conservative check-only exact-geometry FastRoute verification replay for unfixable curved/rotated pad spacing artifacts, plus failure-triggered Tier-2 fine-pitch micro-escape (intersecting-EPAD registration, shortest inward GND bridge, fine-IC escape-ring avoidance, 0.42/0.20mm vias with 100um neck-down, unrated fine-pitch power width cap, and 0.160mm inner antipad alignment)"
```

- 轮次目录：`artifacts/optimization/runs/20261122T134458373666Z-exact-geometry-and-micro-escape/`
- 单元测试：完整 `pytest` **300 passed**（耗时 **92.12 秒**，较初始基线共新增 **53 项**独立测试），见 [pytest.log](../artifacts/optimization/runs/20261122T134458373666Z-exact-geometry-and-micro-escape/pytest.log) 与 [manifest.json](../artifacts/optimization/runs/20261122T134458373666Z-exact-geometry-and-micro-escape/manifest.json)。
- 全轮 7 板总耗时 **2792.348 秒**；7 板全部通过 13 层 Gerber 解析、Chromium 3D 装配渲染检查与第二引擎 Freerouting JVM 对比运行。
- 比较基线：第二轮 `artifacts/optimization/runs/20261121T113551029733Z-c-bypass-and-epad-bridge/manifest.json`（初始基线为 `artifacts/optimization/runs/20261119T165010331810Z-annular-exact-seven/manifest.json`）。
- 决策结论：第三轮相对于第二轮再获 **0 项退步、5 项改善**（三轮累计 **22 项改善、0 项退步**；7 板全部达成 **0 真实间距违例、0 短路、0 非热过孔压焊盘、0 孔环违例、0 丝印压焊盘**，其中 **5/7 板通过全部 10/10 电气与制造硬门禁**），优化器已自动将第三轮晋升为活动回归基线（`promoted: true`），见 [result.json](../artifacts/optimization/runs/20261122T134458373666Z-exact-geometry-and-micro-escape/result.json)、[review.md](../artifacts/optimization/runs/20261122T134458373666Z-exact-geometry-and-micro-escape/review.md) 与 [state.json](../artifacts/optimization/state.json)。

| Case | 主引擎未布通（初 → R1 → R2 → R3） | 独立 LVS 开/短（初 → R1 → R2 → R3） | 真实间距违例（初 → R1 → R2 → R3） | In1 地填充（初 → R1 → R2 → R3） | 总走线 mm（R3） | 电容距离代理 mm | 备注 |
| --- | :---: | :---: | :---: | ---: | ---: | ---: | --- |
| case1 | `0 → 0 → 0 → 0` | `0/0 → 0/0 → 0/0 → 0/0` | `0 → 0 → 0 → 0` | `86.49% → 86.49% → 86.49% → 86.49%` | `953.306` | `3.345` | 零扰动；**10/10 电气与制造硬门禁通过** |
| case2 | `0 → 0 → 0 → 0` | `0/0 → 0/0 → 0/0 → 0/0` | `0 → 0 → 0 → 0` | `91.54% → 91.54% → 91.54% → 91.54%` | `1248.608` | `7.706` | 零扰动；仅缺天线几何与外壳核实 |
| case3 | **`1 → 1 → 0 → 0`** | **`1/0 → 1/0 → 0/0 → 0/0`** | `0 → 0 → 0 → 0` | `89.33% → 89.33% → 89.83% → 89.83%` | `1123.486` | `3.949` | **10/10 电气与制造硬门禁全部通过** |
| case4 | `0 → 0 → 0 → 0` | `0/0 → 0/0 → 0/0 → 0/0` | `0 → 0 → 0 → 0` | `94.04% → 94.04% → 94.04% → 94.04%` | `3226.585` | `13.875` | 零扰动；**10/10 电气与制造硬门禁通过** |
| case5 | **`9 → 9 → 5 → 1`** | **`9/0 → 9/0 → 5/0 → 1/0`** | `0 → 0 → 0 → 0` | `75.51% → 75.51% → 76.31% →` **`78.86%`** | `986.125` | `6.884` | 二级微逃逸消除全部 5 项旧开路（仅余 1 项 `Net-(U3-DVDD10_UPS)`），地填充亏空 **`0.0949 → 0.0614`** |
| case10 | **`4 → 2 → 0 → 0`** | **`2/0 → 2/0 → 0/0 → 0/0`** | **`12 → 4 → 4 → 0`** | `86.84% → 87.09% → 87.62% → 87.62%` | `1618.220` | `12.392` | 保守精确几何复核归零间距误报；**10/10 电气与制造硬门禁全部通过** |
| case11 | **`1 → 0 → 0 → 0`** | **`1/0 → 0/0 → 0/0 → 0/0`** | `0 → 0 → 0 → 0` | `90.03% → 90.01% → 90.40% → 90.40%` | `1651.384` | `14.280` | **10/10 电气与制造硬门禁全部通过** |

## 边界说明与待办事项

- **数据边界**：Case 1–5 与 Case 10–11 均为已解封回归集（其中 **7/7 板全部达到 0 真实间距违例与 0 短路，6/7 板 0 开路，5/7 板（`case1 / case3 / case4 / case10 / case11`）通过全部 10/10 电气与制造硬门禁**），本轮结果**不构成新的泛化分数**。Case 6–9 与 Case 12–13 保持封印，本轮未读取任何参考 PCB 或封印案例。
- **工程验收状态**：`engineering_pass: null`（未认证）。全部 7 板仍保留未核实封装包络（46–108 项不等）、未知载流/温升上限及端到端 SI 实测缺口，均如实保留，未弱化任何门禁。
- **下一步高优先级任务**：
  1. `case5`：收敛剩余最后 1 项开路（`Net-(U3-DVDD10_UPS)`：`C22-1` ↔ `U3-27`）；固定机械接口 `USB1` 板边 `0.30 mm < 0.50 mm` 冲突仍为输入阻塞项，须人工机械评审确认，不可静默移动或豁免。
  2. `case2` / `case4` / `case5` / `case10` / `case11`：推进去耦电容局部摆位优先级改进，并持续补齐有依据的封装外壳尺寸与 Case 2 天线几何证据。
