# 仓库接手入口

本仓库已包含 v3 求解器、五板冻结产物、3D 预览、四轮持续优化实验，以及独立 Gemini 布局参考。原始起点为 `aff93d3`；不能仅依据早期 README 或原来的 26 项测试判断当前状态。

## 先读与先查

1. 读取根目录 `AGENTS.md` 和 [优化流程](optimization-workflow.zh-CN.md)。
2. 读取 [最新实验报告](optimization-octagonal-fanout.zh-CN.md)、`artifacts/optimization/last_run.json` 及其指向的 `result.json` / `manifest.json`。
3. 按下面的环境要求安装依赖，然后执行 `source scripts/env.sh && python -m scripts.optimize_pcb status`。

Case 1–5 均已暴露，只能用作回归。冻结 v3 仍为活动基线；实验变体未获晋升，不可按案例挑选变体。新泛化成绩须另做密封实验。历史失败、归档和日志均随仓库保留。

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

最新源码冻结轮次为 `20261117T112159329967Z-octagonal-fanout`，完整测试实测 **123 passed，73.42 秒**，五板运行完整，但存在退步而未晋升。该轮 `pytest.log`、各路由器日志和源码归档均随产物保留，原有 `*.log` 忽略规则没有导致这些冻结证据丢失。

本轮实测 Case 1 线长缩短约 3.6%，Case 5 未布通及 LVS 开路从 9 降至 7；但 Case 2 线长、Case 4 未布通数、Case 5 地填充不足量退步。所有结论、机械未知项和板边约束冲突以 [完整报告](optimization-octagonal-fanout.zh-CN.md) 为准，不能宣称已经达到最终工程验收目标。

Freerouting 原生程序存在 GraalVM `ObstacleArea` 序列化异常，工作线程退出后主进程等待至超时。现有第二引擎结果不是有效布线对比；修复时还需核验封装解析警告。主 fastroute 的独立板级审计证据单独保留。

接手者可使用 Agent 集群分别研究工具链、拥塞/扇出、布局、ECAD/MCAD 和独立审查；由主 Agent 控制文件所有权和冻结屏障。完整优化轮次运行期间，全体 Agent 停止修改源码、测试、配置、输入和工具二进制。
