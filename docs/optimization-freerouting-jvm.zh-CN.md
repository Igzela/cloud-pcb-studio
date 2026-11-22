# Freerouting JVM 修复与五板回归

官方 Freerouting JVM 版在五块回归板上都正常完成并生成可解析的 SES。独立审计仍检出开路，因此五块板的第二引擎结果均未通过。主引擎指标与冻结 v3 基线一致，本轮未晋升。2026 年 11 月 18 日实测 142 项测试通过，耗时 70.38 秒，完整轮次耗时约 16 分 30 秒。[轮次报告](../artifacts/optimization/runs/20261118T004500921717Z-freerouting-jvm/review.md) · [pytest 日志](../artifacts/optimization/runs/20261118T004500921717Z-freerouting-jvm/pytest.log)

实验基于 `12fa87891a5febe9563d7198544fb953bfbec170`。该提交合入了值归一化实验，记录 127 项测试通过，候选因 Case 4 去耦距离从 13.875 mm 增至 13.931 mm 而未晋升。Case 1–5 全部属于回归集。[上一轮报告](optimization-value-normalize.zh-CN.md)

## 工具链修复和复现

归档的原生 Freerouting 在序列化 `ObstacleArea` 时抛出 GraalVM 异常，布线线程退出后主进程等待至超时。本次比较改用同版本官方 JVM 发行版。安装程序通过 SHA-256 校验下载文件，求解和冻结清单记录实际 JAR 与 Java 运行时指纹。

```bash
source scripts/env.sh
python -m scripts.install_freerouting_jvm
source scripts/env.sh
python -m scripts.optimize_pcb run \
  --label freerouting-jvm \
  --variant freerouting-jvm \
  --hypothesis '修复二路引擎崩溃并独立校验输出，主路由规则保持一致'
```

默认安装适用于 Linux x86_64，实测使用 Freerouting 2.5.0 JAR 和 Temurin 25.0.5+8。已有 Java 25 时，可使用 `--jar-only`，并按需将 `FREEROUTING_JAVA` 指向 Java 可执行文件。安装文件写入 `.run/`，比较运行使用校验后的本地副本。优化器会拒绝已经完整执行过的相同实验。如果只需审阅此轮，直接查看已归档证据。[Freerouting 发行版](https://github.com/freerouting/freerouting/releases/tag/v2.5.0) · [安装脚本](../scripts/install_freerouting_jvm.py)

第二引擎 DSN 为无电气引脚的封装增加空 image，给圆孔和长槽 NPTH 输出四层物理禁布区。它仍会报告 `In1.Cu` 被视为信号层的警告。比较流程核对实际 SES 的网络与几何，记录引擎自报的连接与间距计数，再将该铜几何导入独立板，重建覆铜并运行四层 LVS、Via-in-Pad 与禁布区检查。SES 中违反源网络层分配的线段会单独计数。主引擎仍按相同的 DSN、规则和正式工程门禁评估。

这些改动依据 DSN 封装和钻孔局部几何，以及输入中原有的网类与层规则，对所有回归板统一执行。五板日志中的 `component package not found` 和 `ObstacleArea` 异常均已消失，`In1.Cu` 按信号层处理的警告仍存在。独立审计应继续保留实际违反层规则的线段计数，供后续轮次核查。[Case 1 JVM 日志](../artifacts/optimization/runs/20261118T004500921717Z-freerouting-jvm/case1/freerouting.log)

## 五板实测

五个 JVM 进程均退出 0，无超时，日志均包含实际作业完成状态及最终评分。引擎自报未布通按连接计数，独立 LVS 开路按重建覆铜后的网络计数。

| Case | 主引擎未布通 / LVS 开路 | JVM 未布通 / 间距违例 | JVM 独立 LVS 开路 / 短路 | JVM 作业时间（秒） | 全板验证时间（秒） |
| --- | --- | --- | --- | ---: | ---: |
| 1 | 0 / 0 | 3 / 0 | 3 / 0 | 7.20 | 53.583 |
| 2 | 0 / 0 | 4 / 2 | 4 / 0 | 8.61 | 95.977 |
| 3 | 1 / 1 | 24 / 0 | 21 / 0 | 19.32 | 186.611 |
| 4 | 1 / 1 | 21 / 2 | 19 / 0 | 27.61 | 297.386 |
| 5 | 9 / 9 | 29 / 0 | 24 / 0 | 19.01 | 285.049 |

第二引擎五板均无独立 LVS 短路、非热焊盘 Via-in-Pad 或越层线段。Case 4 已定义的 RF 区域四层铜覆盖均为 0。Case 2 缺少可核验的天线几何，仍无法确认 RF 净空。JVM 输出仍有开路及原始间距告警，只留作比较证据。[Case 2 板级报告](../artifacts/optimization/runs/20261118T004500921717Z-freerouting-jvm/case2/blind_run_report.json) · [Case 5 板级报告](../artifacts/optimization/runs/20261118T004500921717Z-freerouting-jvm/case5/blind_run_report.json)

主引擎的五份 `board.dsn` 都与冻结 v3 逐字节一致。去除 `tedit` 与 `tstamp` 元数据后，五份最终主板也与冻结 v3 一致。五板主引擎均通过独立短路、真实间距、非热焊盘 Via-in-Pad、11 层 Gerber、孔环和丝印重叠检查。Case 3、4、5 分别仍有 1、1、9 个开路网络。Case 5 固定接口板边铜距仍为 0.30 mm，低于 0.50 mm 门槛，地填充为 75.51%，低于 85% 目标。Case 2、4、5 的去耦距离代理值仍高于 4.5 mm。[完整决策与原始指标](../artifacts/optimization/runs/20261118T004500921717Z-freerouting-jvm/result.json)

主引擎五板 3D 浏览器检查全部通过。参数包络碰撞数为 0，待核实的外壳及高度依次为 46、59、74、108、52 项，五板机械门禁均失败。实际装配及完整电气工程认证仍未取得。[Case 1 自包含 3D 页](../artifacts/optimization/runs/20261118T004500921717Z-freerouting-jvm/case1/assembly.html) · [Case 4 装配侧视截图](../artifacts/optimization/runs/20261118T004500921717Z-freerouting-jvm/case4/assembly_side.png)

## 冻结证据

`verify_manifest` 成功重验本轮 195 个逐板产物、5 项输入和 5 项轮级证据，涵盖 pytest 与各路由器原始日志。源码归档的 72 个成员均与清单哈希一致，当前测试时的源码快照与冻结清单一致。活动 v3 基线也通过独立重验。上一轮 `value-normalize` 缺少 23 个原始日志，原始清单仍保留，用于追踪缺失事实。新增的 Git 忽略例外覆盖后续优化轮的日志路径。`scripts.optimize_pcb status` 会分别显示活动基线诊断与最近一轮的证据完整性。

| 文件 | SHA-256 |
| --- | --- |
| [本轮清单](../artifacts/optimization/runs/20261118T004500921717Z-freerouting-jvm/manifest.json) | `c240d69ed1acb837b366cdd3763fd8391ce63324c90d0d2daa7590b32a6332be` |
| [源码归档](../artifacts/optimization/runs/20261118T004500921717Z-freerouting-jvm/source.tar.gz) | `e48760bd7cfcb8d9b2be5691aea5e20db84218f860b55b1d81aa2bb38efe7083` |
| [pytest 日志](../artifacts/optimization/runs/20261118T004500921717Z-freerouting-jvm/pytest.log) | `5f67fdca5d884770b77d9d030ec64aec9214a612c979ec6efdb933fa527fa389` |
| [活动 v3 基线清单](../artifacts/blind_v3_freeze.json) | `e2e141a907321b27ef511a8d4bc08dc9d7e70c17ccf4d2d0f887c037418d6a59` |
