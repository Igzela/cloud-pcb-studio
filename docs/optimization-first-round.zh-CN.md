# 持续优化流程首轮实测

优化流程已实现并实际完成一轮。候选未晋升，稳定默认求解行为和历史 v3 基线保留。

全量测试：`66 passed, 5156 warnings in 69.71s (0:01:09)`。本轮执行时间 `1050.62 s`。全部五板 3D 浏览器检查通过；所有数据集均为已暴露回归集，不是新陌生盲测。

| Case | 未布通 基线→候选 | 独立 LVS 开路 基线→候选 | 平均电容距离代理 mm 基线→候选 |
| --- | --- | --- | --- |
| case1 | 0 → 0 | 0 → 0 | 3.345 → 3.345 |
| case2 | 0 → 0 | 0 → 0 | 7.706 → 7.706 |
| case3 | 1 → 1 | 1 → 1 | 3.949 → 3.949 |
| case4 | 1 → 0 | 1 → 0 | 13.875 → 10.423 |
| case5 | 9 → 15 | 9 → 13 | 6.884 → 5.089 |

Case 4 识别到 6 条电源支路，产生 2 个电源岛；已识别电源走线约 76.5% 长度达到 ≥0.50 mm，独立开路清零。但 Case 5 退步，因此严格的逐板判定拒绝候选。该实验功能保留在 `--variant supply-semantics` 开关之后，没有把 Case 4 的成功变成仅对 Case 4 启用的补丁。

所有五板真实路由间距违例、独立短路、非热 Via-in-Pad 为零，55 个 Gerber 层文件全部解析通过。机械高度/外壳未全部验证，工程验收仍未认证。更短的未布通走线没有计作质量收益。

下轮假设：依据网表端点类型、器件角色及明确电流需求区分“局部 IC 去耦供电”和“板级分发骨干”，保留推导证据，但避免对未知电流的片内/局部电源一律施加宽主干。需要独立测试：单 IC+去耦电容、跨多个 IC、连接器/稳压器/电感端点、明确载流要求、随机网名/位号重命名。不得通过按案例选择变体绕过 Case 5。

[流程说明](optimization-workflow.zh-CN.md) · [首轮完整比较](../artifacts/optimization/runs/20261117T074914645440Z-supply-semantics/review.md) · [离线看板](../artifacts/optimization/runs/20261117T074914645440Z-supply-semantics/review.html) · [完整 pytest 日志](../artifacts/optimization/runs/20261117T074914645440Z-supply-semantics/pytest.log) · [完整性检查](../artifacts/optimization/integrity.json)
