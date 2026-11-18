# PCB 优化轮次

状态：complete；可晋升基线：True；工程验收：未认证。

改进假设：依据已布板实际几何和原网表，统一补全可安全直连的同封装普通信号通孔，保持剩余铜不变；双路重放和独立 LVS 同时证实收益，沿用已实测的 JVM 工具链和全部门禁。

| Case | 未布通 | LVS 开/短 | 真实间距 | 地填充 | 电容距离代理 mm |
| --- | --- | --- | --- | --- | --- |
| case1 | 0 | 0/0 | 0 | 86.49% | 3.345 |
| case2 | 0 | 0/0 | 0 | 91.54% | 7.706 |
| case3 | 1 | 1/0 | 0 | 89.33% | 3.949 |
| case4 | 0 | 0/0 | 0 | 94.04% | 13.875 |
| case5 | 9 | 9/0 | 0 | 75.51% | 6.884 |

## 本轮比较

```json
{
  "eligible_for_baseline": true,
  "hard_gates_passed": false,
  "engineering_pass": null,
  "engineering_acceptance": "not_certified: endpoint SI, verified current/thermal limits and human wirelength comparison require separate evidence",
  "regressions": [],
  "improvements": [
    {
      "case": "case4",
      "metric": "independent_lvs_opens_zero",
      "before": false,
      "after": true
    },
    {
      "case": "case4",
      "metric": "unrouted_zero",
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
- P1 `case2` [input_and_solver] 处理装配碰撞并补齐有依据的外壳尺寸（证据 ID `87888d43221c`）
- P1 `case2` [input_and_solver] 补齐天线几何证据并保证四层净空（证据 ID `5ca6aa3f5fb2`）
- P1 `case3` [solver] 按独立 LVS 断开组诊断布线拥塞（证据 ID `16d11a2f5145`）
- P1 `case3` [input_and_solver] 处理装配碰撞并补齐有依据的外壳尺寸（证据 ID `dba9bda77b30`）
- P1 `case4` [input_and_solver] 处理装配碰撞并补齐有依据的外壳尺寸（证据 ID `9a2d50027ff1`）
- P1 `case5` [solver] 按独立 LVS 断开组诊断布线拥塞（证据 ID `c2e203b23f1d`）
- P1 `case5` [input_and_solver] 处理装配碰撞并补齐有依据的外壳尺寸（证据 ID `383c46192fe7`）
- P2 `case2` [solver] 改进去耦优先级与供电脚局部摆位（证据 ID `9095e413af45`）
- P2 `case4` [solver] 改进去耦优先级与供电脚局部摆位（证据 ID `7adc194e43f7`）
- P2 `case4` [input_and_solver] 检查是否缺少非地供电语义或电气需求（证据 ID `94c403e28d30`）
- P2 `case5` [solver] 改进去耦优先级与供电脚局部摆位（证据 ID `6002e3407c36`）
- P2 `case5` [solver] 改善地参考完整性（证据 ID `0ee0f830844c`）

所有已解封案例只能用于回归。该流程不读取参考 PCB，不自动修改源代码、输入约束或 DRC 门槛。差分网级铜长差不是端到端时延证明。
