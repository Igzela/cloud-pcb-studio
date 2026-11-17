# 局部供电域优化：第二轮实测

结论：**未晋升，原基线保留**。这表示与迭代基线的逐板比较结果；工程验收仍未认证，不能宣称已经达到 8–8.5 成目标。

全量测试：`86 passed, 5156 warnings in 72.50s (0:01:12)`。本轮执行时间 `1050.06 s`。五板均为已暴露回归数据，不属于新陌生盲测。

本轮采用同一 `scoped-supply` 命令运行全部五板。局部域须有单一有类型供电脚 IC、回流电容及可辨识 R/L/C 端点，并验证网表与焊盘端点一致。多 IC、外部接口或证据不足保持骨干规则；未按案例选择变体。

未知电流局部域的默认 0.25 mm 是实验路由意图，骨干保持至少 0.50 mm。明确载流和最小宽度要求始终优先；同时修复 max_current_a 为 null 时正确回退到已提供 current_a 的处理。制造最小线宽、间距、孔环及所有原门禁没有放宽。

## 与稳定基线、上轮宽主干候选比较

每列三个数依次是：稳定 v3 基线 / 上轮 supply-semantics / 本轮 scoped-supply。未布通是引擎连接计数，LVS 开路是独立网络计数，两者可以不同。

| Case | 未布通：基线/上轮/本轮 | LVS 开路：基线/上轮/本轮 | 电容距离代理 mm：基线/上轮/本轮 | 本轮 In1 地填充 |
| --- | --- | --- | --- | --- |
| case1 | 0 / 0 / 0 | 0 / 0 / 0 | 3.345 / 3.345 / 3.345 | 86.49% |
| case2 | 0 / 0 / 0 | 0 / 0 / 0 | 7.706 / 7.706 / 7.706 | 91.54% |
| case3 | 1 / 1 / 1 | 1 / 1 / 1 | 3.949 / 3.949 / 3.949 | 89.33% |
| case4 | 1 / 0 / 0 | 1 / 0 / 0 | 13.875 / 10.423 / 10.423 | 93.73% |
| case5 | 9 / 15 / 12 | 9 / 13 / 11 | 6.884 / 5.089 / 5.089 | 74.38% |

电容距离代理可能包含 bulk 电容；未布通板的较短线长不能算作改善。即使某项较好，也不能抵消其他板退步。

## 自动判定原文

```json
{
  "eligible_for_baseline": false,
  "hard_gates_passed": false,
  "engineering_pass": null,
  "engineering_acceptance": "not_certified: endpoint SI, verified current/thermal limits and human wirelength comparison require separate evidence",
  "regressions": [
    {
      "case": "case5",
      "metric": "unrouted",
      "before": 9,
      "after": 12
    },
    {
      "case": "case5",
      "metric": "opens",
      "before": 9,
      "after": 11
    },
    {
      "case": "case5",
      "metric": "ground_fill_deficit",
      "before": 0.09489999999999998,
      "after": 0.10619999999999996
    }
  ],
  "improvements": [
    {
      "case": "case4",
      "metric": "unrouted_zero",
      "before": false,
      "after": true
    },
    {
      "case": "case4",
      "metric": "independent_lvs_opens_zero",
      "before": false,
      "after": true
    },
    {
      "case": "case4",
      "metric": "unrouted",
      "before": 1,
      "after": 0
    },
    {
      "case": "case4",
      "metric": "opens",
      "before": 1,
      "after": 0
    },
    {
      "case": "case4",
      "metric": "cap_distance_mm",
      "before": 13.875,
      "after": 10.423
    },
    {
      "case": "case5",
      "metric": "cap_distance_mm",
      "before": 6.884,
      "after": 5.089
    }
  ],
  "invalid": [],
  "full_suite": true,
  "tests_passed": true,
  "policy": "per-case Pareto: every safety/QoR metric non-worse; incomplete wire length never rewarded; improvement is not engineering acceptance"
}
```

## 逐网供电意图

| Case | 网络身份 | 范围 | 目标宽度 mm | 电流已提供 |
| --- | --- | --- | --- | --- |
| case1 | `+5V` | board_distribution | 0.500 | False |
| case1 | `+3V3` | board_distribution | 0.500 | False |
| case2 | `+BATT` | board_distribution | 0.500 | False |
| case2 | `/VIN` | board_distribution | 0.500 | False |
| case2 | `+5V` | board_distribution | 0.500 | False |
| case2 | `+3V3` | board_distribution | 0.500 | False |
| case2 | `+3.3V` | board_distribution | 0.500 | False |
| case3 | `+6V` | board_distribution | 0.500 | False |
| case3 | `+3V3` | board_distribution | 0.500 | False |
| case3 | `/Left Driver/+5V` | board_distribution | 0.500 | False |
| case3 | `/Right Driver/+5V` | board_distribution | 0.500 | False |
| case4 | `/3V3` | board_distribution | 0.500 | False |
| case4 | `/3V3_M1` | board_distribution | 0.500 | False |
| case4 | `/3V3_M2` | board_distribution | 0.500 | False |
| case4 | `/3V3_m3` | board_distribution | 0.500 | False |
| case4 | `/3v3_light` | board_distribution | 0.500 | False |
| case4 | `/5V_USB` | board_distribution | 0.500 | False |
| case5 | `+3V3` | board_distribution | 0.500 | False |
| case5 | `/V12` | single_ic_domain | 0.250 | False |
| case5 | `/VBUS` | board_distribution | 0.500 | False |
| case5 | `Net-(U3-DVDD10_UPS)` | single_ic_domain | 0.250 | False |
| case5 | `Net-(U3-VDD5)` | board_distribution | 0.500 | False |

这里的范围依据来自盲规范，所有网络名仅用于报告身份。局部域可以含滤波电感及反馈电阻，拓扑范围不等于物理回路已经紧凑，仍需电流、热与布局验证。未知电流没有被填成额定值。

## 原始证据

[本轮完整比较](../artifacts/optimization/runs/20261117T084530422384Z-scoped-supply/review.md) · [离线看板](../artifacts/optimization/runs/20261117T084530422384Z-scoped-supply/review.html) · [pytest -v 日志](../artifacts/optimization/runs/20261117T084530422384Z-scoped-supply/pytest.log) · [冻结清单](../artifacts/optimization/runs/20261117T084530422384Z-scoped-supply/manifest.json)

[流程说明](optimization-workflow.zh-CN.md) · [首轮记录](optimization-first-round.zh-CN.md)
