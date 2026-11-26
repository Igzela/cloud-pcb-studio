# PCB 优化轮次

状态：complete；可晋升基线：False；工程验收：未认证。

改进假设：On fine-pitch fallback, route individually proven same-side IC output decouplers at full default netclass width through a conservatively clearance-checked octilinear path before competing nets consume the IC pad exit; expect fewer independent LVS opens and no regression across all boards.

| Case | 未布通 | LVS 开/短 | 真实间距 | 地填充 | 电容距离代理 mm |
| --- | --- | --- | --- | --- | --- |
| case1 | 0 | 0/0 | 0 | 86.49% | 3.345 |
| case2 | 0 | 0/0 | 0 | 91.54% | 7.706 |
| case3 | 0 | 0/0 | 0 | 89.80% | 3.949 |
| case4 | 0 | 0/0 | 0 | 94.00% | 13.875 |
| case5 | 0 | 0/0 | 0 | 78.67% | 6.884 |
| case10 | 0 | 0/0 | 0 | 87.50% | 12.392 |
| case11 | 0 | 0/0 | 0 | 90.40% | 14.28 |

## 本轮比较

```json
{
  "eligible_for_baseline": false,
  "hard_gates_passed": false,
  "engineering_pass": null,
  "engineering_acceptance": "not_certified: endpoint SI, verified current/thermal limits and human wirelength comparison require separate evidence",
  "regressions": [
    {
      "case": "case5",
      "metric": "ground_fill_deficit",
      "before": 0.06140000000000001,
      "after": 0.06330000000000002
    }
  ],
  "improvements": [
    {
      "case": "case3",
      "metric": "wirelength_mm",
      "before": 1123.486,
      "after": 1115.612
    },
    {
      "case": "case4",
      "metric": "wirelength_mm",
      "before": 3226.585,
      "after": 3215.139
    },
    {
      "case": "case5",
      "metric": "independent_lvs_opens_zero",
      "before": false,
      "after": true
    },
    {
      "case": "case5",
      "metric": "unrouted_zero",
      "before": false,
      "after": true
    },
    {
      "case": "case5",
      "metric": "unrouted",
      "before": 1,
      "after": 0
    },
    {
      "case": "case5",
      "metric": "opens",
      "before": 1,
      "after": 0
    },
    {
      "case": "case10",
      "metric": "wirelength_mm",
      "before": 1618.22,
      "after": 1589.495
    }
  ],
  "invalid": [],
  "full_suite": true,
  "tests_passed": true,
  "policy": "per-case Pareto: every safety/QoR metric non-worse; incomplete wire length never rewarded; improvement is not engineering acceptance"
}
```

## 下一步任务

- P0 `case5` [input_blocker] 固定机械接口与制造规则冲突（证据 ID `d1dfd4f44472`）
- P1 `case1` [input_and_solver] 处理装配碰撞并补齐有依据的外壳尺寸（证据 ID `7447e78cdbab`）
- P1 `case10` [input_and_solver] 处理装配碰撞并补齐有依据的外壳尺寸（证据 ID `35cd5fb0478b`）
- P1 `case11` [input_and_solver] 处理装配碰撞并补齐有依据的外壳尺寸（证据 ID `b822019a7d5b`）
- P1 `case2` [input_and_solver] 处理装配碰撞并补齐有依据的外壳尺寸（证据 ID `87888d43221c`）
- P1 `case2` [input_and_solver] 补齐天线几何证据并保证四层净空（证据 ID `5ca6aa3f5fb2`）
- P1 `case3` [input_and_solver] 处理装配碰撞并补齐有依据的外壳尺寸（证据 ID `dba9bda77b30`）
- P1 `case4` [input_and_solver] 处理装配碰撞并补齐有依据的外壳尺寸（证据 ID `9a2d50027ff1`）
- P1 `case5` [input_and_solver] 处理装配碰撞并补齐有依据的外壳尺寸（证据 ID `383c46192fe7`）
- P2 `case10` [solver] 改进去耦优先级与供电脚局部摆位（证据 ID `cbd4d72e8d19`）
- P2 `case11` [solver] 改进去耦优先级与供电脚局部摆位（证据 ID `047f4558be6c`）
- P2 `case2` [solver] 改进去耦优先级与供电脚局部摆位（证据 ID `9095e413af45`）
- P2 `case4` [solver] 改进去耦优先级与供电脚局部摆位（证据 ID `7adc194e43f7`）
- P2 `case4` [input_and_solver] 检查是否缺少非地供电语义或电气需求（证据 ID `94c403e28d30`）
- P2 `case5` [solver] 改进去耦优先级与供电脚局部摆位（证据 ID `6002e3407c36`）
- P2 `case5` [solver] 改善地参考完整性（证据 ID `0ee0f830844c`）

所有已解封案例只能用于回归。该流程不读取参考 PCB，不自动修改源代码、输入约束或 DRC 门槛。差分网级铜长差不是端到端时延证明。
