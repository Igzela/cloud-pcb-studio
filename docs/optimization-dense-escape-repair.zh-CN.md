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

## 复现命令与验证结果

```bash
source scripts/env.sh
python -m scripts.optimize_pcb run \
  --label dense-escape-repair \
  --variant dense-escape-repair \
  --cases all \
  --hypothesis "Integrate hollow Gerber apertures, 3D auto-framing camera fixes, multi-ground Gerber and bottom-silk isolation, AABB/3-tier two-sided packing with rollback, safe primary DSN pin quoting, narrow-EPAD and touching-pad ground propagation, power-class partial-island direct repair, and unrouted/spacing-triggered dense-escape candidate with physical In1.Cu return plane, rule-sized 0.50/0.25 vias, SMD bbox guard, aligned structure clearance, and decoupled diff-pair foreign clearance"
```

- 轮次目录：`artifacts/optimization/runs/20261120T174242641430Z-dense-escape-repair/`
- 单元测试：完整 `pytest` **292 passed**（耗时 **92.96 秒**，较上一基线新增 45 项独立测试），见 [pytest.log](../artifacts/optimization/runs/20261120T174242641430Z-dense-escape-repair/pytest.log) 与 [manifest.json](../artifacts/optimization/runs/20261120T174242641430Z-dense-escape-repair/manifest.json)。
- 全轮 7 板总耗时 **2551.209 秒**；7 板全部通过 13 层 Gerber 解析、Chromium 3D 装配渲染检查与第二引擎 Freerouting JVM 对比运行。
- 比较基线：`artifacts/optimization/runs/20261119T165010331810Z-annular-exact-seven/manifest.json`。
- 决策结论：**0 项退步、6 项改善**，优化器已自动将本轮晋升为活动回归基线（`promoted: true`），见 [result.json](../artifacts/optimization/runs/20261120T174242641430Z-dense-escape-repair/result.json)、[review.md](../artifacts/optimization/runs/20261120T174242641430Z-dense-escape-repair/review.md) 与 [state.json](../artifacts/optimization/state.json)。

| Case | 主引擎未布通 | 独立 LVS 开/短 | 真实间距违例 | In1 地填充 | 总走线 mm | 电容距离代理 mm | 备注 |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| case1 | 0 → 0 | 0/0 → 0/0 | 0 → 0 | 86.49% → 86.49% | 953.306 → 953.306 | 3.345 → 3.345 | 零扰动；10/11 门禁通过（仅缺外壳核实） |
| case2 | 0 → 0 | 0/0 → 0/0 | 0 → 0 | 91.54% → 91.54% | 1248.608 → 1248.608 | 7.706 → 7.706 | 零扰动；缺天线几何与外壳核实 |
| case3 | 1 → 1 | 1/0 → 1/0 | 0 → 0 | 89.33% → 89.33% | 1144.005 → 1144.005 | 3.949 → 3.949 | 零退步；仍余 `/M1_TACHO` 1 开路 |
| case4 | 0 → 0 | 0/0 → 0/0 | 0 → 0 | 94.04% → 94.04% | 3226.585 → 3226.585 | 13.875 → 13.875 | 零扰动；10/11 门禁通过（仅缺外壳核实） |
| case5 | 9 → 9 | 9/0 → 9/0 | 0 → 0 | 75.51% → 75.51% | 935.400 → 935.400 | 6.884 → 6.884 | 高密候选将未布通降至 5（0 间距违例），因地填充低于 75.51% 被板内帕累托拒绝以保零退步 |
| case10 | **4 → 2** | 2/0 → 2/0 | **12 → 4** | **86.84% → 87.09%** | 1732.568 → 1591.621 | 12.392 → 12.392 | 物理地平面+包围盒保护消除 8 项同回流网与 2 项固定过孔违例；`VBUS` 2×0.50mm 补线合并连通岛 4→2 |
| case11 | **1 → 0** | **1/0 → 0/0** | 0 → 0 | 90.03% → 90.01% | 1620.917 → 1651.384 | 14.280 → 14.280 | **10/10 电气与制造硬门禁全部通过**（仅余 69 项未核实机械外壳） |

## 边界说明与待办事项

- **数据边界**：Case 1–5 与 Case 10–11 均为已解封回归集，本轮结果**不构成新的泛化分数**。Case 6–9 与 Case 12–13 保持封印，本轮未读取任何参考 PCB 或封印案例。
- **工程验收状态**：`engineering_pass: null`（未认证）。全部 7 板仍保留未核实封装包络（46–108 项不等）、未知载流/温升上限及端到端 SI 实测缺口，均如实保留，未弱化任何门禁。
- **下一步高优先级任务**：
  1. `case10`：剩余 4 项真实间距违例（固定连接器封装相邻 45° 八边形包络）与 2 项开路（`VBUS` 剩余 2 岛与 `Net-(U3-FB)`）。
  2. `case5`：在保持 `In1.Cu` 地参考填充不低于 `75.51%` 的前提下优化过孔密度/共逃逸，以释放高密逃逸候选的开路收敛；固定机械接口板边 0.30 mm < 0.50 mm 冲突仍为输入阻塞项，须人工机械评审确认，不可静默移动或豁免。
  3. `case3`：攻克剩余 1 项 `/M1_TACHO` 拥塞开路。
  4. `case2` / `case4` / `case5` / `case10` / `case11`：推进去耦电容局部摆位优先级改进，并持续补齐有依据的封装外壳尺寸与 Case 2 天线几何证据。
