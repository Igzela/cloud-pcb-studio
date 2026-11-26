# 局部供电输出去耦预布线实验

七板活动基线中，Case 5 有一处未布通和一处独立 LVS 开路，均对应 IC 的供电输出脚与同面旁路电容。重放选中路由的 SES 后，FastRoute 将该连接判为受阻。只保留预布地铜时，IC 焊盘外侧有一条满足净距的通道。邻网布线及过孔随后封住这条通道。[基线结果](../artifacts/optimization/runs/20261122T134458373666Z-exact-geometry-and-micro-escape/result.json)

对三个摆位方案进行了筛选。提前分配局部电容使实际焊盘距离从 3.668 mm 增至 4.039 mm 或 6.850 mm，取决于与既有旁路电容分配的先后关系。保持邻件不动的局部细化找不到更短的合法摆位。冻结 DSN 诊断样本预铺一条全宽三段走线后，FastRoute 报告未布通和间距违例均为 0。重建覆铜后，独立 LVS 开路和短路均为 0，In1 地铜填充从 78.86% 降到 78.69%。正式轮次使用求解器自动选出的路径。

## 求解器候选

`local-output-preroute` 沿用 `dense-escape-repair` 的前序路由，在进入细间距微逃逸时检索两端点局部去耦网络。候选需具备有类型的 IC 供电输出、明确的电容回流端、同面 SMD 铜焊盘和一致的网表端点。已声明电源轨、高速和差分网络沿用原规则。

预布线从 IC 焊盘长轴朝封装外侧出线，在 8 mm 焊盘间距内按确定性顺序搜索正交及 45° 三段路径。路径使用原 `DEFAULT` 网类线宽，按既有板边、异网铜、钻孔及禁布几何复核。可用的短路径作为固定铜写入 DSN 和候选 KiCad 板。现有候选比较与全量回归继续审计其余网络、覆铜、制造和装配证据。[优化工作约定](../AGENTS.md) · [全量流程](optimization-workflow.zh-CN.md)

```bash
source scripts/env.sh
python -m scripts.optimize_pcb run \
  --label local-output-preroute \
  --variant local-output-preroute \
  --cases all \
  --hypothesis 'On fine-pitch fallback, route individually proven same-side IC output decouplers at full default netclass width through a conservatively clearance-checked octilinear path before competing nets consume the IC pad exit; expect fewer independent LVS opens and no regression across all boards.'
```

## Case 5 正式轮次

候选自动生成三段 F.Cu 走线，线宽 0.160 mm，合计约 4.15 mm。主路由未完成连接和独立 LVS 开路均从 1 降至 0，短路、真实间距、非热过孔压焊盘和孔环违例均为 0。固定接口板边铜距仍为 0.30 mm。

In1 地铜面积从 771.658 mm² 降至 769.819 mm²，填充率从 78.86% 降至 78.67%。异网过孔和通孔反焊盘数量从 102 增至 106。下一轮应验证能否在完整连通时减少净增的异网过孔，使填充至少恢复到 78.86%，并继续追踪长期 85% 目标。[自动预布证据](../artifacts/optimization/runs/20261126T012118417153Z-local-output-preroute/case5/dense_escape_candidate/micro_escape/local_output_preroute.json) · [逐项指标](../artifacts/optimization/runs/20261126T012118417153Z-local-output-preroute/case5/metrics.json)

## 七板比较与晋升决定

七板轮次 `20261126T012118417153Z-local-output-preroute` 已完成，耗时 3091.300 秒。311 项 pytest 通过，耗时 101.76 秒。七板均通过离线 Chromium 检查，未布通、独立 LVS 开短路及真实间距违例均为 0。Case 1、2 和 11 的比较指标与基线一致。Case 3、4 和 10 的线长分别缩短 7.874、11.446 和 28.725 mm。这三块板未启用局部预布线。[七板对照](../artifacts/optimization/runs/20261126T012118417153Z-local-output-preroute/review.md) · [完整测试日志](../artifacts/optimization/runs/20261126T012118417153Z-local-output-preroute/pytest.log)

优化器记录 7 项改善和 1 项退步。Case 5 的 In1 地铜填充下降，候选未晋升。活动回归基线指向 11 月 22 日的微逃逸轮次。候选与活动基线的清单均已复核，候选清单覆盖 434 个逐板产物。工程验收状态为未认证。[候选决策](../artifacts/optimization/runs/20261126T012118417153Z-local-output-preroute/result.json) · [冻结清单](../artifacts/optimization/runs/20261126T012118417153Z-local-output-preroute/manifest.json) · [基线指针](../artifacts/optimization/state.json)

## 减少异网过孔的 Case 5 诊断

冻结的完整布通候选有 84 个异网 SES 过孔，In1 地填充为 78.67%。同一预布线输入的诊断结果如下。LVS、真实间距和非热焊盘内过孔均独立复查。

| FastRoute 诊断 | 异网 SES 过孔 | In1 地填充 | LVS 开路 | 非热焊盘内过孔 |
| --- | ---: | ---: | ---: | ---: |
| 冷启动，过孔代价 100 | 69 | 79.33% | 3 | 0 |
| 从完整 SES 热启动，过孔代价 100 | 82 | 78.80% | 0 | 2 |
| 热启动，160 个焊盘过孔禁布区，90 秒 | 82 | 78.76% | 0 | 0 |
| 同一防护，完整首遍，实际早停阈值 50% | 80 | 78.84% | 0 | 0 |
| 同一防护，240 秒，实际早停阈值 0.5% | 77 | 79.02% | 0 | 0 |

FastRoute 将 `0.5` 读作 50% 的比例。用 `0.005` 完成预定的 0.5% 试验后，第二遍结果被保留到退出。真实间距和短路均为 0，160 个禁布区无过孔侵入，三段预布线保持原几何。独立比较在 18 个受保护网络中发现 10 个网络的走线或过孔几何有变。`/U2D_P/N` 的全网铜长差代理值由 0.666 mm 增至 4.137 mm，仍需根据实际端点和叠层验证。[完整试验独立审计](../artifacts/optimization/diagnostics/20261126-case5-via-cost/guarded-240/independent-audit.json) · [受保护网络差异](../artifacts/optimization/diagnostics/20261126-case5-via-cost/guarded-240/checkpoint-pass2-protected-route-audit.json) · [逐网铜长比较](../artifacts/optimization/diagnostics/20261126-case5-via-cost/guarded-240/geometry-comparison.json)

这些诊断保持活动七板基线。下一步验证能否锁定已经核实的走线，或独立验证改动后的差分端到端要求。所有命令以当时的 `.run` 输出路径记录。重放时可给 `-do` 和 `--report` 新路径，并将带防护的 `-de` 指向留档的 `guarded-90/protected.dsn`。[前三次命令](../artifacts/optimization/diagnostics/20261126-case5-via-cost/probe-commands.json) · [最终命令](../artifacts/optimization/diagnostics/20261126-case5-via-cost/guarded-240/argv.json) · [证据哈希](../artifacts/optimization/diagnostics/20261126-case5-via-cost/SHA256SUMS)
