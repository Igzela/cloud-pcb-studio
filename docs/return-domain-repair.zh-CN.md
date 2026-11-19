# 回流网隔离与第二引擎引脚语法修复

新增电源板的首次运行发现了两个独立问题。预布过孔曾把多个习惯上被称为地的网络当作一个铜网络，产生真实短路。Freerouting 的 JVM 解析器另因复杂端点标识的引号范围读入失败。该轮记录和参考密封状态见 [盲测报告](holdout-cohorts.zh-CN.md)。

扇出、覆铜和 Gerber 都使用确切的网表 ID。按原有优先顺序选出实际有铜焊盘的回流网，其他回流网作为异网铜。仅为没有任何已使用回流域的板保留原来的占位策略。四项合成测试验证了焊盘避让、同封装跨域通孔、同网路由锚点，以及未使用的 GND 占位。

在 Case 10 已冻结的求解器摆位上，仅重放预布扇出。原先两处短路的异网重叠面积分别为 4.513987 mm² 和 0.971583 mm²，修复后独立四层 LVS 检出 0 短路。这是预布扇出证据，完整布线及制造门禁另行报告。[重放证据](../artifacts/holdout-diagnostics/return-domain-case10.json)

Freerouting 的 DSN 端点分别引用元件名与焊盘号，中间保留语法分隔符。不会改变 PCB、网名或主引擎 DSN 序列。真实 JVM 合成测试对含括号、连字符和空格的三个同网焊盘生成实际 SES。路由器报告 0 未布通、0 间距，独立铜并集是一个连通分量。

报告新增主引擎连接计数及未完成连接对应的网络名，以区分路由器未完成连接和独立 LVS 开路网络。历史门禁所读的兼容字段继续保留原始连接计数。

上一轮五板布局回归已记录 Case 1–3 完成、Case 4 完成主板求解及独立 LVS，但执行环境中断时未写完浏览器和最终逐板记录。Case 5 未开始。保留原清单，不能用前三块结果代表全量回归。[中断轮次](../artifacts/optimization/runs/20261119T085316988092Z-placement-recovery/manifest.json)

回流域修复后的五板回归全量完成，耗时 1117.221 秒，pytest 206 项通过，耗时 77.24 秒。五块板的浏览器验证均通过。Case 1、2、4 的未布通、独立 LVS 开短路和真实间距均为零。Case 3 仍有 1 处开路，Case 5 仍有 9 处。与当时的五板基线相比没有退步或指标改善，未晋升。Case 10–11 解封后归入回归集，基线索引保留前五块板的原记录并追加两块板的冻结记录。[五板回归](../artifacts/optimization/runs/20261119T092502081033Z-return-domain-isolation/review.md) · [七板基线](../artifacts/optimization/baselines/exposed-seven-20261119.json)

完整 Case 10 重测中，主引擎未完成连接 55 → 4，独立 LVS 开路 5 → 2，短路 2 → 0；地填充 80.81% → 86.84%。JVM 现在完成真实导入和路由，保留 31 条未完成连接和 12 条间距记录。主引擎仍有 12 条按当前门禁计入的间距记录，最小孔环为 0.0997 mm，未过 0.10 mm 门槛。Gerber 解析、非热焊盘 Via-in-Pad 和浏览器检查通过。Case 10 的放置仍为常规路径。[重测证据](../artifacts/holdouts/power-and-industrial-02-retry-01/case10/metrics.json)

12 条主引擎记录全部是焊盘间距。8 条涉及原网表的同一回流网，DSN 报告相距 0.0001 mm；实际板上测得距离为 0，现有豁免所要求的正面积重叠未由此前检查证明。4 条来自同一对异网焊盘在四个铜层的记录，早先独立测距指出矩形 padstack 和实际椭圆/圆角轮廓可能导致差异。当前门禁状态保留。应分别修正 DSN 的网络归属表达和保守形状表示，再用独立 LVS 核对物理连通。

## 冻结后参考对比

Case 10–11 的完整轮次先冻结，再打开参考。清单 SHA-256 为 `660923f6e434af7923f5b7268da0ce9e10366a6f843096bdaf0a963b2248b064`。这是在两个已知首跑失败信号之后的重测，不能作为此前未经反馈的首次泛化分数。Case 6–9 的参考保持密封。[冻结清单](../artifacts/holdouts/power-and-industrial-02-retry-01/freeze.json)

| Case | 来源与不可变版本 | 总线长，求解 / 人工 | 平均去耦距离代理，求解 / 人工 | LVS 开路，求解 / 人工 |
| --- | --- | --- | --- | --- |
| 10 | Balthazar PSU v3，CERN-OHL-S-2.0 | 1732.568 / 656.244 mm，2.640× | 12.392 / 6.942 mm | 2 / 0 |
| 11 | CAN4VSCP Frankfurt ETH，CERN-OHL-1.2 | 1620.917 / 1410.315 mm，1.149× | 14.280 / 4.200 mm | 1 / 1 |

Case 10 的源提交为 `a7038cd21d99fd1a46fc7791039e0a3209fceb8b`，有样板装配与电池测试记录。Case 11 的源提交为 `cbaa9d3670674f2c2b027690248653f3bbd7b984`，有实物板记录。原始参考未随 Git 分发。[Case 10 源](https://github.com/balthazar-space/Balthazar-PSU-v.3/tree/a7038cd21d99fd1a46fc7791039e0a3209fceb8b) · [Case 11 源](https://github.com/grodansparadis/can4vscp-frankfurt-eth/tree/cbaa9d3670674f2c2b027690248653f3bbd7b984)

两板未达到 1.05× 线长或 4.5 mm 去耦距离目标。两个候选均有开路，线长不得用于奖励低完成度。已推断差分对的整网铜长差在候选上为 0.100 mm；这种代理不能证明端到端时延、耦合或阻抗。Case 11 参考的一个 LVS 开路是本验证器实际检出，未做豁免，也未把该人工板当作硬门禁全过的金标准。

原比较器读取 Case 11 的现代 KiCad 命名网络字段时失败。兼容读法在冻结校验后从已验证原件生成临时副本，把以名字直接标注的网络编号，补出编号与名字定义，并逐项恢复相同的网络归属。去除网络编码字段后，原件与临时副本的其余 S 表达式逐项相等，铜项网络顺序和盲输入投影也逐项一致。原件、临时副本的摘要和变换数量均留档。Case 10 的读取内容与参考原件完全相同。[兼容处理证据](../artifacts/holdout-comparisons/power-and-industrial-02-retry-01/normalization.json)

[全部比较数据](../artifacts/holdout-comparisons/power-and-industrial-02-retry-01/comparison.json) · [Case 10 对照图](../artifacts/holdout-comparisons/power-and-industrial-02-retry-01/case10.png) · [Case 11 对照图](../artifacts/holdout-comparisons/power-and-industrial-02-retry-01/case11.png) · [Case 10 装配](../artifacts/holdouts/power-and-industrial-02-retry-01/case10/assembly.html) · [Case 11 装配](../artifacts/holdouts/power-and-industrial-02-retry-01/case11/assembly.html)
