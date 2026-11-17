# Gemini 独立对照入口

用户指定的独立模型为 Google `gemini-4-hydrogen`。它与本项目的开发智能体不同。模型列表 API 已确认存在该 ID；不使用含义可能变化的 `gemini-flash-latest` 别名，也不在失败时自动改用其他模型。

```bash
source scripts/env.sh
python -m scripts.run_gemini_reference \
  --spec benchmark_case/blind_spec.json \
  --model gemini-4-hydrogen \
  --thinking-level low --max-output-tokens 32768 \
  --timeout 420 --attempts 1 \
  --key-file .run/secrets/gemini-api-key
```

凭据文件由运行环境提供，必须为 mode 0600；也可以使用运行时 `GEMINI_API_KEY`。密钥不属于源码、请求留档或测试报告。`.run/` 已被 Git 忽略，命令行只传文件路径，不传实际密钥。

## 信息隔离

输入只白名单保留板框、孔、固定接口、局部封装焊盘、器件值、可得本体尺寸、引脚属性、网表和电气需求。位号和网络身份改为不透明 ID，固定接口和孔位转换到板内局部坐标；规范中的电压/接地含义以独立语义保留。原项目名、仓库 URL、commit、库名前缀及任何求解器位置/结果不进入请求。

本入口不读取人类参考 PCB，不发送本项目算法、优化失败点或求解器产物。当前 Case 1–5 已暴露，只能称为隔离输入的模型对照，不能因此重新宣称是陌生数据集泛化盲测。公开硬件是否进入外部模型训练也无法由本入口证明。

## 当前协议的能力边界

当前接口请求独立的结构化**布局建议**：每个匿名器件的位姿、面别，以及简明最终设计说明。解码器检查完整覆盖、未知/重复器件、有限数值、底面能力限制和固定接口位姿。它不执行模型返回的脚本或命令，不会用本项目求解器补齐缺失器件来伪装成完整模型输出。

这一步尚不包含路由、几何合法化、LVS、DRC、Gerber 或真实外壳认证。成功状态明确为 `placement_generated_unverified`，同时保存 `routing_status=not_run`、`geometry_status=not_run`、`engineering_pass=null`。后续只有把独立布局导入相同构板/路由工具链，保留其原始失败并跑完独立验证，才能得到板级参考比较；不能直接把建议 JSON 当成人工参考板或完整 PCB 求解成绩。

板级验证使用独立命令，不修改模型生成目录：

```bash
source scripts/env.sh
python -m scripts.route_gemini_reference \
  --reference artifacts/gemini_reference/<生成目录> \
  --spec benchmark_case/blind_spec.json
```

该命令验证源规范、模型身份和所有生成文件哈希，通过通用外部布局入口把原坐标直接交给构板与稳定路由后端，生成 PCB、Gerber、独立审计和 3D。不调用内部摆位器，不钳制坐标、不填缺失器件、不自动消除碰撞；固定接口位姿必须不变。电气/机械失败照实保留，完整执行不等于通过门禁。

结果写入 `artifacts/gemini_routed_reference/<UTC时间>/`，与注册表中同一输入的稳定基线比较。它衡量的是 **Gemini 独立布局＋共享路由/检查后端**，不能描述为完全独立的 Gemini 布线求解器；也不会自动替换优化基线。

## 请求与失败记录

每次请求写入 `artifacts/gemini_reference/<UTC时间>/`：

- `anonymous_spec.json`：真正发送的匿名输入。
- `identity_mapping.json`：仅本地使用的身份回映，不发送给模型。
- `request.json`：完整请求正文，无凭据头。
- `model_reply.json`：请求模型 ID、服务报告的模型版本、token 用量、最终文本与每次 API 尝试结果。模型 thought parts 不作为报告保存。
- `placement.json`：只有完整解码成功才生成，且仍明确未路由/未验证。
- `reference_manifest.json`：输入及输出 SHA256、状态和能力边界。

HTTP 429/5xx 及传输错误最多尝试 3 次，间隔 2、4 秒；身份/权限错误不重试。重试不改变模型或预算。503、超时、输出截断、缺少器件或错误接口均保存为真实失败，不计为模型通过，也不生成虚构的路由/LVS 分数。生成失败不代表已经评估了模型的 PCB 能力。

## 思考与最终输出预算

Google [Long decode 文档](https://ai.google.dev/gemini-api/docs/long-decode)说明 Gemini 4 的上限包含思考和最终输出，Hydrogen 单次可到 65,536。实际模型元数据也报告 `outputTokenLimit=65536`。默认使用 `thinkingConfig.thinkingLevel=LOW` 与总预算 32,768；LOW 是努力程度提示，不是保证最终答案一定获得剩余 token 的硬性分配。必须检查 `finishReason`、`thoughtsTokenCount` 和最终文本，不能把 HTTP 200 当作完整结果。

初次 12,000 预算请求曾以 `MAX_TOKENS` 结束，只有约 11,997 个思考 token，最终布局为空。修正调用配置时用 `--retry-of <原生成目录>` 关联失败记录：程序验证匿名输入、模型和任务正文不变，只允许修正配置，不能覆盖已有完整建议来随机重抽。新响应的版本、用量、请求配置及原记录 SHA256 全部保留。参考 [Thinking 文档](https://ai.google.dev/gemini-api/docs/thinking)。

退出码 0 仅表示得到了通过格式/身份检查的布局建议；退出码 2 表示服务不可用或模型输出无效。两者都不代表工程验收通过。保留所有请求，不能删除失败后重抽一个较好建议再冒充首次盲测。
