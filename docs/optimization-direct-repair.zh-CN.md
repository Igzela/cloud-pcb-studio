# 同封装通孔直连补线实验

从已布板的 fastroute 报告读取未完成连接。候选必须是同一封装中两个不同、可唯一定位的镀通孔，符合原网表的同网关系。当前实验只处理普通信号网，使用原规则要求的完整线宽，限同层 0°、45° 或 90° 直线。用实际焊盘、铜迹、过孔、钻孔、板边和禁布区检查整个线段的净空。找不到充足几何证据时跳过。求解规则不包含板号、位号、网络名或人工参考坐标。

候选用原布线 SES 与同规则的 DSN 做两次 fastroute 检查。一份是原始输入，一份只增加固定补线。两次输出的常规 SES 铜几何必须相同。固定补线出现在路由器板状态中，但不写入重放的 SES，因此最终 KiCad 板保留原 SES，并显式添加通过检查的固定线段。重新覆铜后，独立四层 LVS 须确认开路网络实际减少且没有新短路或非热焊盘过孔问题，随后仍执行整套板级门禁。

上一轮 [Freerouting JVM 回归](optimization-freerouting-jvm.zh-CN.md) 验证了第二引擎的可用性。本轮沿用同一第二引擎配置，单独改变主引擎的布线补全步骤。

```bash
source scripts/env.sh
python -m scripts.optimize_pcb run \
  --label direct-repair \
  --variant direct-repair \
  --hypothesis '统一尝试通过几何与实测复查的同封装通孔直连，减少普通信号开路'
```

本轮目录为 `artifacts/optimization/runs/20261118T010740458884Z-direct-repair/`，使用五个已知回归案例。完整 pytest 已实测 171 项通过，74.37 秒。可以审阅 [测试日志](../artifacts/optimization/runs/20261118T010740458884Z-direct-repair/pytest.log) 和 [冻结清单](../artifacts/optimization/runs/20261118T010740458884Z-direct-repair/manifest.json)。

Case 4 的已选主 SES 与 JVM 工具链轮逐字节相同。最终 KiCad 板相对该轮只增加一条 2.54 mm、0.160 mm 宽的 F.Cu 线段，原线段和过孔均无删减或改动。fastroute 两次重放的原始间距记录均为 2，已由原规则核实为原有同网重叠。重放确认未布通 1 → 0，重建覆铜后的独立 LVS 开路为 1 → 0。主板短路、真实间距违例、非热焊盘 Via-in-Pad 均为 0，11 层 Gerber 解析通过，已定义 RF 区四层覆铜率为 0。机械门禁因 108 项外壳尺寸或高度待核实仍失败。[补线证据](../artifacts/optimization/runs/20261118T010740458884Z-direct-repair/case4/direct_repair.json) · [板级报告](../artifacts/optimization/runs/20261118T010740458884Z-direct-repair/case4/blind_run_report.json) · [3D 预览](../artifacts/optimization/runs/20261118T010740458884Z-direct-repair/case4/assembly.html)

| Case | 主引擎未布通 | 独立 LVS 开路 | 短路 / 真实间距 | 总走线 mm（候选） | In1 地填充 |
| --- | --- | --- | --- | ---: | ---: |
| 1 | 0 → 0 | 0 → 0 | 0 / 0 | 953.306 | 86.49% |
| 2 | 0 → 0 | 0 → 0 | 0 / 0 | 1248.608 | 91.54% |
| 3 | 1 → 1 | 1 → 1 | 0 / 0 | 1144.005 | 89.33% |
| 4 | 1 → 0 | 1 → 0 | 0 / 0 | 3226.585 | 94.04% |
| 5 | 9 → 9 | 9 → 9 | 0 / 0 | 935.400 | 75.51% |

全轮耗时 1068.673 秒。五板都通过 Chromium 3D 检查和 11 层 Gerber 解析。以冻结 v3 为比较基线，两个连接性指标及对应门禁在 Case 4 改善，其余指标没有退步或无效证据。优化器将本轮产物晋升为活动回归基线。源码默认行为仍使用原求解配置，复现本轮应指定上述变体。[轮次结果](../artifacts/optimization/runs/20261118T010740458884Z-direct-repair/result.json) · [基线记录](../artifacts/optimization/state.json)

工程门禁仍需补齐。Case 3 与 5 分别保留 1 和 9 个 LVS 开路，Case 5 的板边铜距为 0.30 mm，低于 0.50 mm 门槛，地填充为 75.51%。Case 2 天线缺可核实的尺寸与禁布区。五板待核实封装外壳或高度依次为 46、59、74、108、52 项。五板 3D 浏览器渲染通过，参数包络碰撞检查均为 0。
