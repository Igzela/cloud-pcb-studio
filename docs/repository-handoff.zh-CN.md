# 仓库接手入口

本仓库保留求解器、五板冻结产物、3D 预览、持续优化实验及独立 Gemini 布局参考。原始起点为 `aff93d3`。

## 先读与先查

1. 读取根目录 `AGENTS.md` 和 [优化流程](optimization-workflow.zh-CN.md)。
2. 读取 `artifacts/optimization/last_run.json` 及其指向的 `result.json` 和 `manifest.json`。[最新直连实验](optimization-direct-repair.zh-CN.md) · [Freerouting JVM 比较流程](optimization-freerouting-jvm.zh-CN.md)
3. 按下面的环境要求安装依赖，然后执行 `source scripts/env.sh && python -m scripts.optimize_pcb status`。

Case 1–5 均已暴露，只能用作回归。直连实验的产物已晋升为回归基线，指针保存在 `artifacts/optimization/state.json`。五板统一启用 `direct-repair` 变体可以复现这次设置。新泛化成绩须另做密封实验。

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

最新源码冻结轮次为 `20261118T010740458884Z-direct-repair`，完整测试实测 **171 项通过，74.37 秒**。Case 4 未布通与独立开路均从 1 降到 0，其余四板的比较指标持平。Case 3 和 5 仍分别有 1 和 9 个开路网络，Case 2 的 RF 几何、Case 5 的固定接口板边铜距，以及五板机械尺寸证据仍限制工程验收。[实测报告](optimization-direct-repair.zh-CN.md)

Freerouting 原生程序存在 GraalVM `ObstacleArea` 序列化异常。需要第二引擎时，安装官方 JVM 发行版并指定 `freerouting-jvm` 变体，见 [安装命令与逐板证据](optimization-freerouting-jvm.zh-CN.md)。

接手者可使用 Agent 集群分别研究工具链、拥塞/扇出、布局、ECAD/MCAD 和独立审查；由主 Agent 控制文件所有权和冻结屏障。完整优化轮次运行期间，全体 Agent 停止修改源码、测试、配置、输入和工具二进制。
