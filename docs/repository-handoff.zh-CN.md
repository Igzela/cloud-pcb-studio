# 仓库接手入口

本仓库保留求解器、冻结板级产物、3D 预览、持续优化实验及独立 Gemini 布局参考。原始起点为 `aff93d3`。

## 先读与先查

1. 读取根目录 `AGENTS.md`、[项目路线图](project-roadmap.zh-CN.md)和[优化流程](optimization-workflow.zh-CN.md)。
2. 对照 `artifacts/optimization/state.json` 的活动基线和 `artifacts/optimization/last_run.json` 的最近轮次，查看对应的 `result.json` 和 `manifest.json`。[基线实测](optimization-dense-escape-repair.zh-CN.md)
3. 按下面的环境要求安装依赖，然后执行 `source scripts/env.sh && python -m scripts.optimize_pcb status`。

Case 1–5、10–11 共七块已暴露板用于回归。Case 6–9、12–13 保持密封。活动基线的全量设置采用统一的 `dense-escape-repair` 变体。新泛化成绩通过独立密封实验获得。[密封流程](holdout-cohorts.zh-CN.md)

## 环境与凭据

依赖目录 `.venv/`、`node_modules/`、`.mamba/` 和 `.run/` 不随 Git 分发，应在新环境重建。已实测的 Python 为 **3.12**；不要直接用新环境的默认 Python 3.14 推断项目依赖或测试失败。先确认解释器版本，再创建虚拟环境。例如系统已有 Python 3.12 时：

```bash
source scripts/env.sh
python3.12 -m venv .venv
source scripts/env.sh
python -m pip install -r requirements.txt
npm ci
npx playwright install chromium
```

完整门禁还需要可运行的 ngspice 和 Chromium 的系统依赖；原实测使用 ngspice 41。`bin/fastroute`、`bin/freerouting-cli` 随仓库保留，`bin/chromium` 为浏览器选择包装器。缺少工具时先补齐依赖，不修改测试来跳过检查。

每次 Python、pytest 或路由器执行前均加载 `scripts/env.sh`。该脚本会把缓存放到项目下 `.run/`，安装 Playwright 时也应使用相同环境。完整回归命令：

```bash
source scripts/env.sh && python -m pytest -v
source scripts/env.sh && python -m scripts.optimize_pcb status
```

Gemini 凭据不写入公开 Git 历史。独立模型流程读取运行时 `GEMINI_API_KEY` 或私有凭据文件；新克隆不会自带旧机器的 `.run/secrets/gemini-api-key`。运行环境需通过私有凭据注入渠道提供该值。准确模型名为 `gemini-4-hydrogen`，不得静默替换模型。详见 [外部参考协议](gemini-reference.zh-CN.md)。

## 当前结果与已知故障

2026 年 11 月 22 日晋升的基线为 `20261122T134458373666Z-exact-geometry-and-micro-escape`，完整测试 **300 项通过，92.12 秒**。七板独立短路与真实间距违例均为 0。Case 5 还有 1 个未布通、1 个独立 LVS 开路，固定接口板边铜距为 0.30 mm，门槛为 0.50 mm。Case 2 缺天线几何，七板均有待核实的机械尺寸。工程验收状态为未认证。[逐板结果](optimization-dense-escape-repair.zh-CN.md)

11 月 26 日的局部输出预布线候选完成七板回归，311 项测试通过。Case 5 的未布通及独立 LVS 开路均降为 0，In1 地填充下降 0.19 个百分点，候选未晋升。[候选实验](optimization-local-output-preroute.zh-CN.md)

Freerouting 原生程序存在 GraalVM `ObstacleArea` 序列化异常。活动基线的 `dense-escape-repair` 变体使用官方 JVM 版作第二引擎比较，先执行 `source scripts/env.sh && python -m scripts.install_freerouting_jvm`。[安装与比较证据](optimization-freerouting-jvm.zh-CN.md)

接手者可使用 Agent 集群分别研究工具链、拥塞/扇出、布局、ECAD/MCAD 和独立审查；由主 Agent 控制文件所有权和冻结屏障。完整优化轮次运行期间，全体 Agent 停止修改源码、测试、配置、输入和工具二进制。
