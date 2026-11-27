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

## 固定受保护铜的 Case 5 诊断

将 18 个受保护网络的 179 段走线和 27 个过孔按原始 0.1 µm 精度写入 DSN 固定铜，从初始 SES 中移除重复几何。只加载的对照实验确认零未布通、零 LVS 开短路和零真实间距违例，In1 填充仍为 78.67%。随后保持同一固定输入，用过孔代价 100 和 240 秒预算优化其余网络。FastRoute 返回 0 未布通，独立 LVS 开短路、真实间距违例及非热焊盘内过孔均为 0。18 个网络的走线、线宽、铜层和过孔几何全部保持一致。异网过孔从 84 降至 76，In1 地填充达到 **79.03%**，活动基线为 **78.86%**。[输入精度与哈希](../artifacts/optimization/diagnostics/20261126-case5-protected-optimizer/fixture-evidence.json) · [只加载对照](../artifacts/optimization/diagnostics/20261126-case5-protected-optimizer/load-control/independent-audit.json) · [优化独立审计](../artifacts/optimization/diagnostics/20261126-case5-protected-optimizer/optimizer/independent-audit.json)

按盲规范端点和板级叠层复核后，活动基线、原始预布线候选和固定铜候选的九个差分对均超过 0.20 mm 几何长度差限制。固定铜保留了预布线的差分几何，其中 `/U2_P/N` 的长度差为 23.431 mm，活动基线实测为 1.020 mm。该候选保留为诊断。下一步先验证独立调长或重布能否收敛这些超限，再启动新的七板候选轮次。[活动基线端到端测量](../artifacts/optimization/diagnostics/20261126-case5-protected-optimizer/optimizer/active-differential-paths.json) · [固定铜端到端测量](../artifacts/optimization/diagnostics/20261126-case5-protected-optimizer/optimizer/locked-differential-paths.json) · [证据哈希](../artifacts/optimization/diagnostics/20261126-case5-protected-optimizer/SHA256SUMS)

## 单独调长与逐对采用

原微逃逸完成前已耗尽全局时间，未执行末尾调长。从冻结的完整预布线 SES 单独调用 FastRoute 调长后，引擎记录 7 组的全网长度进入窗口。独立端到端测量确认其中 3 组通过，另外 6 组的正线含有多个中心线路径，状态保留为 `UNKNOWN`。其中 `/U2_P` 有 19 处中心线环路。[工具原始记录](../artifacts/optimization/diagnostics/20261126-case5-selective-pair-tuning/tuning-only/router.txt) · [端点审计](../artifacts/optimization/diagnostics/20261126-case5-selective-pair-tuning/tuning-only/differential-after.json) · [环路定位](../artifacts/optimization/diagnostics/20261126-case5-selective-pair-tuning/tuning-only/centerline-cycles.json)

仅将通过的 `/M2`、`/M3` 和 `/U3STX` 两路铜线作为整组替换到固定铜候选，重新生成覆铜。三组长度差分别为 0.100039、0.099914 和 0.099609 mm。其余 12 个受保护网络的铜几何一致。组合板的 In1 地填充仍为 79.03%，独立 LVS 开短路和非热焊盘内过孔均为 0，四层实测最小异网铜距为 0.1257 mm。其余六个差分对继续超限，该板保留在诊断阶段。[组合板](../artifacts/optimization/diagnostics/20261126-case5-selective-pair-tuning/composite/reconstructed.kicad_pcb) · [组合审计](../artifacts/optimization/diagnostics/20261126-case5-selective-pair-tuning/composite/independent-audit.json) · [逐对端点证据](../artifacts/optimization/diagnostics/20261126-case5-selective-pair-tuning/composite/differential-paths-cli.json)

单独加载组合 SES 时，FastRoute 报告全部 136 个连接完成且零间距违例。回写补入 52 段焊盘中心连接，使额外五组形成多条可测中心线，状态变为 `UNKNOWN`。原组合板保持为本轮的可测几何，回写 SES 单独保留作为载入诊断证据。[仅载入报告](../artifacts/optimization/diagnostics/20261126-case5-selective-pair-tuning/check-only/router-report.json) · [回写影响](../artifacts/optimization/diagnostics/20261126-case5-selective-pair-tuning/check-only/postcheck-geometry.json) · [证据哈希](../artifacts/optimization/diagnostics/20261126-case5-selective-pair-tuning/SHA256SUMS)

## 保留活动基线差分铜的目标网络试验

将 11 月 22 日活动基线的 18 个差分网络写成固定铜。受这些铜和焊盘过孔禁布区约束，原来的同层单调三段预布线搜索无合法结果。只启用经盲拓扑证明的供电输出去耦目标网络后，FastRoute 找到 5.8693 mm 的 F.Cu/In2.Cu 路径，使用两枚 0.42/0.20 mm 通孔过孔。独立 LVS 验证目标闭合，短路、真实间距违例和非热焊盘内过孔均为 0。18 个差分网络的几何和端到端长度测量与活动基线一致。[原始参数](../artifacts/optimization/diagnostics/20261126-case5-active-protected-local-output/argv.json) · [独立审计](../artifacts/optimization/diagnostics/20261126-case5-active-protected-local-output/independent-audit.json)

该试验的其余网络保持待布状态。FastRoute 记录 79 个被忽略的未完成连接，独立 LVS 仍有 23 个开路网络。接续试验应锁住所得目标路径，再恢复其余网络并验证整板覆铜和差分指标。[输入哈希](../artifacts/optimization/diagnostics/20261126-case5-active-protected-local-output/SHA256SUMS) · [更新比较逻辑后的七板重放](../artifacts/optimization/diagnostics/20261126-case5-selective-pair-tuning/seven-board-replay-with-skew.json)

首次整板热启动锁住目标和 18 个差分网络，删除与目标铜冲突的三个网络的全部旧走线，并保留其余 21 个网络的旧走线和过孔。FastRoute 将初始 40 个未完成连接降至 1。目标保持闭合，但 `/V12` 的 `U3-20` 仍与其余焊盘断开，In1 填充降至 78.68%。短路、真实间距违例和非热焊盘内过孔均为 0，差分几何与活动基线一致。后续试验只移除与固定目标铜实际冲突的走线和过孔，以保留原有供电分支。[删除范围与留存验证](../artifacts/optimization/diagnostics/20261126-case5-whole-net-ripup/preflight.json) · [整板独立审计](../artifacts/optimization/diagnostics/20261126-case5-whole-net-ripup/independent-audit.json)
