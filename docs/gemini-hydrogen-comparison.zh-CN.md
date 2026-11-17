# Gemini 4 Hydrogen 独立布局对照实测

本次已取得真实 API 结果并完成构板、共享路由、独立 LVS/DRC、Gerber 与 3D 检查。它是 **Gemini 独立布局＋共享稳定路由后端** 的一次对照，不是完全独立的 Gemini PCB 求解器，也不是优秀人工参考板。输入为已暴露的 Case 1 回归规范，不能声称新陌生数据泛化成绩或排除外部模型预训练记忆。

API 请求与服务报告的模型均为 `gemini-4-hydrogen`。最终响应为 `STOP`，完整给出 46 个器件位姿。未给模型提供求解器代码、求解器布局/失败点、人工参考 PCB 或原项目标识。位号、网络身份被匿名化；板框、固定接口和必要电气信息保留。

## 预算问题及修正

初次 12,000 token 请求耗尽在思考，未产生完整布局，原记录保留。依据 Google Thinking / Long decode 文档与实际模型元数据，将调用配置改为 LOW、总预算 32,768、单次等待 420 秒；任务正文、匿名规范与模型 ID 不变。LOW 不作为严格的思考 token 硬上限承诺。

最终用量：`{"promptTokenCount": 16836, "candidatesTokenCount": 1973, "totalTokenCount": 21660, "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 16836}], "candidatesTokensDetails": [{"modality": "TEXT", "tokenCount": 1973}], "thoughtsTokenCount": 2851, "serviceTier": "standard"}`。这些是收到响应时的服务报告，不对超时请求的账单作推断。

## 板级实测比较

| 指标 | 项目稳定基线 | Gemini 独立布局＋共享路由 |
| --- | --- | --- |
| 未布通连接数 | 0 | 3 |
| 独立 LVS 开路网络 | 0 | 3 |
| 独立 LVS 短路 | 0 | 4 |
| 真实间距违例 | 0 | 14 |
| 非热 Via-in-Pad | 0 | 0 |
| 参数包络碰撞 | 0 | 9 |
| 尺寸未验证器件 | 46 | 46 |
| 平均电容距离代理 mm | 3.345 | 4.126 |
| 平面铜线总长 mm | 953.306 | 933.091 |

两板 Gerber 均为 11/11 严格解析通过；本次模型板最小孔环 0.1247 mm，丝印重叠 0.0 mm²。3D 浏览器通过，控制台错误为零。模型板虽能导出、显示，但电气/机械门禁失败。较短的未布通板走线不构成优于基线的证据。

Gemini 坐标、角度、面别已从最终 `.kicad_pcb` 独立回读，与原始布局逐件核对一致。外部入口没有调用内部摆位器，没有钳制/补齐/修复坐标。9 处是统一参数包络检查的碰撞；两板各有 46 个外壳/高度未充分验证，不能把基线的零包络碰撞描述为厂家真实外壳认证。

本次为一个明确预算配置的独立布局响应，不据此评价 Gemini 全部能力，也没有将单次失败提炼为“本求解器已达到资深工程师水准”。

## 原始产物

- [Gemini 交互 3D](../artifacts/gemini_routed_reference/20261117T104011257428Z/board/assembly.html)
- [原样路由 PCB](../artifacts/gemini_routed_reference/20261117T104011257428Z/board/routed.kicad_pcb)
- [逐项比较 JSON](../artifacts/gemini_routed_reference/20261117T104011257428Z/comparison.json)
- [原始模型最终响应与用量](../artifacts/gemini_reference/20261117T103251099457Z/model_reply.json)
- [匿名输入](../artifacts/gemini_reference/20261117T103251099457Z/anonymous_spec.json)
- [模型生成清单](../artifacts/gemini_reference/20261117T103251099457Z/reference_manifest.json)
- [路由冻结清单](../artifacts/gemini_routed_reference/20261117T104011257428Z/reference_route_manifest.json)
- [最初截断记录](../artifacts/gemini_reference/20261117T090956487803Z/reference_manifest.json)

[接入与独立性说明](gemini-reference.zh-CN.md) · [本轮局部供电优化](optimization-scoped-supply.zh-CN.md)

官方依据：[Thinking](https://ai.google.dev/gemini-api/docs/thinking)、[Long decode](https://ai.google.dev/gemini-api/docs/long-decode)、[generateContent](https://ai.google.dev/api/generate-content)。生成与检查之间的信息流、实际请求配置、模型版本及所有失败记录均留档。
