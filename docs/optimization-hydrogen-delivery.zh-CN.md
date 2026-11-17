# 本轮优化与 Hydrogen 独立对照交付

本轮没有达到全部工程验收标准。已完成局部供电域实验、真实 Gemini 4 Hydrogen 独立布局对照、通用外部布局入口及默认模式全回归。失败指标全部保留，未晋升有退步的候选，未修改原有 DRC/LVS/Gerber 门槛。

## 实验结果

- 局部供电域：Case 4 保持完全布通；Case 5 从上轮的 15 个未布通连接/13 个独立开路恢复为 12/11，但仍差于稳定基线 9/9，且地填充退步，因此候选未晋升。
- 新增外部布局入口：按完整器件集合、有限数值、面别能力、固定接口验证输入，按原坐标构板，不调用内部摆位器或修补坐标；未知电流与外壳尺寸不转成 PASS。
- 默认兼容：统一稳定模式跑完 Case 1–5，与历史基线逐板所有比较指标一致，零退步、零改善；本次是兼容验证，不当作性能提升。

## Gemini 独立对照

真实服务请求模型与返回版本均为 `gemini-4-hydrogen`。修正 LOW/32,768 总预算后得到完整 46 器件布局；匿名输入和任务正文与首次截断请求相同。未向模型发送求解器代码/布局/失败点或人工参考板。

同一稳定路由后端对其原始布局生成实际 PCB、Gerber、独立检查与 3D。回读 `.kicad_pcb` 确认坐标、角度及面别没有被替换。

| Case 1 指标 | 项目稳定基线 | Gemini 独立布局＋共享路由 |
| --- | --- | --- |
| 未布通连接 | 0 | 3 |
| 独立 LVS 开路/短路 | 0/0 | 3/4 |
| 真实间距违例 | 0 | 14 |
| 参数包络碰撞 | 0 | 9 |
| 高度/外壳未验证 | 46 | 46 |
| Gerber 解析 | 11/11 | 11/11 |

这是一份真实、可检查、但未过门禁的机器参照，不是优秀人工参考，也不能据一个 LOW 配置的单次布局评价模型整体能力。Case 1–5 已暴露，模型输入隔离不等于新陌生数据泛化盲测。

## 测试与完整性

最终完整 pytest -v：`109 passed, 5156 warnings in 72.07s (0:01:12)`。当前源码/测试/工具/依赖锁/注册表共 63 个文件已归档并验证哈希；历史 v3、首轮、局部域轮次和 Gemini 生成/路由产物哈希均保持不变。

凭据只从忽略目录的私有文件读取；源码、文档、请求及报告扫描未发现凭据。报告保留实际模型版本、token 用量、超时/503/截断记录与请求血缘，不记录凭据头。

[局部供电域完整实测](optimization-scoped-supply.zh-CN.md) · [Gemini 完整对照](gemini-hydrogen-comparison.zh-CN.md) · [独立对照协议](gemini-reference.zh-CN.md)

[默认兼容回归完整比较](../artifacts/optimization/runs/20261117T104333444712Z-external-placement-compat/review.md) · [完整 pytest 日志](../artifacts/optimization/runs/20261117T104333444712Z-external-placement-compat/pytest.log)

[Gemini 交互 3D](../artifacts/gemini_routed_reference/20261117T104011257428Z/board/assembly.html) · [Gemini PCB](../artifacts/gemini_routed_reference/20261117T104011257428Z/board/routed.kicad_pcb) · [斜视对比 PNG](../artifacts/gemini_routed_reference/comparison_oblique.png) · [完整性记录](../artifacts/optimization/latest-integration-integrity.json)

下一轮保留一般性拥塞诊断方向：先记录路由器的完整日志，区分原始固定障碍阻断与已布线拥塞，再优化 GND 扇出和信号逃线通道。外部模型若加入自校验反馈，也应只接收自身产物的错误，固定修订预算并保留每轮，不泄漏本项目答案。当前尚未实现这两项后续工作。
