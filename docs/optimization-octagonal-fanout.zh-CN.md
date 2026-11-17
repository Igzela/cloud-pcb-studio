# GND 扇出保护几何实验

本轮只改变预布地过孔在 DSN 中的障碍形状，验证是否能释放细间距引脚的斜向逃线空间。Case 1–5 全部属于已暴露回归集；本轮不读取人类参考 PCB，也不产生陌生盲测泛化成绩。

## 几何依据

旧方案以过孔中心为中心、保护半径 `r` 为半边长，放置一个方形普通 `keepout`。`r` 已包含实际铜半径与原有额外保护量。普通过孔的 `r = 0.55/2 + 0.145 = 0.420 mm`；热焊盘过孔的 `r = 0.50/2 + 0.145 = 0.395 mm`。

新方案采用外切八边形：顶点为 `(±r, ±d)` 与 `(±d, ±r)`，其中 `d` 在 DSN 的 1 µm 网格上向外取整，并满足 `d >= (sqrt(2)-1)*r`。八边形各边与中心的距离均不小于原保护半径。它包含完整保护圆；对于当前已在微米网格上的中心和半径，它同时包含于旧方形之内。对于非网格调用者，增加保守量化余量，避免中心舍入削弱保护。

未量化的理想八边形相对方形约减少 17.16% 面积，释放的部分全部位于保护圆之外。普通过孔实际微米量化后每个保护区释放约 0.121032 mm²。报告中的 `via_guard_area_reclaimed_sum_mm2` 是逐过孔面积差之和，不是障碍并集面积，也不等于可布线面积。

保护层完全保留：普通过孔保护四层；热焊盘过孔继续保护 B.Cu / In1.Cu / In2.Cu，F.Cu 仍由原热焊盘保护。实际过孔、短线、孔径、铜径、接地连通与特殊热焊盘处理均不改变。短线已有 `wire_keepout` / `via_keepout` 不变。路由器原有间距规则仍在此几何之外继续生效。

这项规则只消费实际过孔几何，不以器件位号、工程网络名或参考坐标分支。保留 `--octagonal-fanout` 统一实验开关；未获资格前不替换默认稳定行为。

## 日志与独立验证

主 fastroute、差分独立路由候选、freerouting 的原始 stdout/stderr 现分别保存为日志；`router_processes.json` 记录实际命令、退出码及超时。超时保留可用的部分输出，退出码记为 null，不伪造成功。未增加 debug 日志级别、预算、随机种子或引擎参数。

新增 14 项测试包含：连续保护圆的最小边界距离证明、旧方形包含关系、量化后的 DSN 四层多边形、非网格输入、无效输入拒绝、普通/热焊盘实际铜几何及接地端点一致、短线禁布区一致，以及失败/超时日志保真。

完整五板轮次冻结在 `artifacts/optimization/runs/20261117T112159329967Z-octagonal-fanout/`。结果以其中 `result.json`、`review.md` 与 `manifest.json` 为准。晋升资格与完整工程验收是两件事；不能用几何测试通过代替 LVS、DRC、Gerber 或机械尺寸证明。

## 实测结果

本轮完整执行，**没有晋升基线**。完整测试 `123 passed, 5156 warnings in 73.42s`，包含新增的 14 项测试；全轮耗时 1174.201 秒（约 19 分 34 秒）。警告为已有第三方 PyGerber/PyParsing 弃用警告，原始输出保留在 [pytest.log](../artifacts/optimization/runs/20261117T112159329967Z-octagonal-fanout/pytest.log)。

下表按「稳定 v3 基线 → 本轮候选」列示。未布通为路由器连接计数，LVS 开路为独立检查的网络计数，二者不能相互替代。

| Case | 未布通 | LVS 开路 | 总线长 mm | In1 地填充率 |
| --- | --- | --- | --- | --- |
| 1 | 0 → 0 | 0 → 0 | 953.306 → 919.241 | 86.49% → 86.54% |
| 2 | 0 → 0 | 0 → 0 | 1248.608 → 1262.940 | 91.54% → 91.40% |
| 3 | 1 → 1 | 1 → 1 | 1144.005 → 1162.052 | 89.33% → 89.47% |
| 4 | 1 → 2 | 1 → 1 | 3224.045 → 3175.318 | 94.04% → 93.98% |
| 5 | 9 → 7 | 9 → 7 | 935.400 → 976.115 | 75.51% → 75.04% |

只有 Case 1/2 前后均完整连通，允许比较线长优化。Case 3–5 的线长仅作原始事实记录，不奖励未完成板较短的线长。Case 1 缩短 34.065 mm（约 3.6%）；Case 5 未布通和 LVS 开路均少了 2 项。但 Case 2 的完整板线长增长、Case 4 多出一条未布通连接、Case 5 的地填充不足量增大，构成三项明确退步。自动判定保留稳定 v3 基线，不通过平均得分抵消单板退步，也不按板切换实验变体。

Case 3 原开路 `/M1_TACHO` 已接通，但新增 `/Left Driver/ADVANCE` 开路。这是问题转移，不能宣称解决了该板连接性。这里的网络名只用于报告诊断，不出现在新增算法条件中。

五板全部保持：独立 LVS 短路 0、真实间距违例 0、非热焊盘 Via-in-Pad 0、11/11 层 Gerber 解析通过、最小孔环 0.1247 mm、双面丝印压焊盘面积 0。Case 1–4 最小板边铜距为 0.50 mm；Case 5 仍为 0.30 mm，与固定机械接口和 0.50 mm 门槛冲突，未移动接口或豁免检查。Case 4 的已识别 RF 区域四层保持零铜；Case 2 的天线几何证据仍不足，RF 门禁没有通过。

去耦距离代理值因布局不变而保持 3.345 / 7.706 / 3.949 / 13.875 / 6.884 mm，Case 2/4/5 仍超目标。本轮没有增加差分端到端路径、电流/温升或厂家机械尺寸的证明，不能据此声称达到 8–8.5 成资深工程师目标。既有调研和来源记录见 [v3 设计决策](v3-design-decisions.zh-CN.md) 与 [v3 报告](v3-final-report.zh-CN.md)；本轮五板全部为回归数据，没有新增陌生盲测成绩。

## 3D 与工具链故障

五板无头 Chromium 的斜视、俯视、侧视均通过，未出现控制台错误或外部资源依赖；图层开关、阻焊颜色切换检查通过。参数包络碰撞均为 0，但未核实外壳/高度的器件数量仍为 46 / 59 / 74 / 108 / 52，机械门禁全部保留失败，不能把渲染成功等同于装配认证。

- [Case 1 自包含 3D 预览](../artifacts/optimization/runs/20261117T112159329967Z-octagonal-fanout/case1/assembly.html) · [斜视截图](../artifacts/optimization/runs/20261117T112159329967Z-octagonal-fanout/case1/assembly_oblique.png)
- [Case 4 自包含 3D 预览](../artifacts/optimization/runs/20261117T112159329967Z-octagonal-fanout/case4/assembly.html) · [侧视截图](../artifacts/optimization/runs/20261117T112159329967Z-octagonal-fanout/case4/assembly_side.png)
- [Case 5 自包含 3D 预览](../artifacts/optimization/runs/20261117T112159329967Z-octagonal-fanout/case5/assembly.html) · [斜视截图](../artifacts/optimization/runs/20261117T112159329967Z-octagonal-fanout/case5/assembly_oblique.png)

本轮新增的原始日志定位出一个此前只体现为超时的故障：仓库自带 Freerouting 原生程序，在五板上都因 GraalVM 的 `ObstacleArea` 序列化配置缺失抛出 `UnsupportedFeatureError`，工作线程退出后主进程等待至 60 秒超时。五次均保存异常栈、部分输出和超时状态。这些执行不是有效的第二引擎布线对比；主 fastroute 的实际板级审计结果单独保留。示例见 [Case 1 Freerouting 日志](../artifacts/optimization/runs/20261117T112159329967Z-octagonal-fanout/case1/freerouting.log)。此外仍有封装解析警告，需要一并核验，不能只换启动方式后假设第二引擎输入正确。

## 冻结与后续依据

[几何证据](../artifacts/optimization/octagonal-fanout-geometry-evidence.json) 独立核验了五板共 817 个分层八边形：它们都包含完整保护圆且位于旧方框内。将它们还原为方框后，每板 DSN 与稳定 v3 基线逐字节一致。实际器件位置、板面、预布过孔和短线铜几何也与旧基线相同。

[完整性核验](../artifacts/optimization/octagonal-fanout-verification.json) 验证了当前 66 个源码/配置快照项、源码归档成员、五板 187 个产物文件和输入哈希。历史 v3、三个此前完成轮次的归档与产物保持完整，活动基线未改变。原始决策和失败点见 [result.json](../artifacts/optimization/runs/20261117T112159329967Z-octagonal-fanout/result.json)，离线审阅入口为 [review.html](../artifacts/optimization/runs/20261117T112159329967Z-octagonal-fanout/review.html)。冻结目录不再修改。

接下来的依据是：先修复第二引擎的真实运行与封装解析问题；对主求解器继续研究把实际 DSN 保护区及间距纳入地过孔候选的逃线通道代价。Case 3 的开路转移表明，仅释放方角无法稳定消除拥塞，不能靠重复随机运行选择较好结果。每个后续算法修改仍须独立测试、统一五板冻结回归，并遵守既有逐板晋升规则。
