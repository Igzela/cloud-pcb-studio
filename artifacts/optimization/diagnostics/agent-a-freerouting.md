# Agent A 诊断报告：freerouting-cli v2.5.0 原生二进制故障与修复路径

- 日期：2026-11-17
- 实验人：Agent A（只读诊断；未修改 /workspace 源码、bin/、benchmark_case 及其他 artifacts 目录）
- 目标轮次：`artifacts/optimization/runs/20261117T112159329967Z-octagonal-fanout/`（下称 RUN）
- 实验目录：`/tmp/freerouting-exp/`；证据副本：`artifacts/optimization/diagnostics/agent-a-freerouting/`

---

## 1. 结论摘要

1. **故障根因（已一手复现）**：`bin/freerouting-cli`（GraalVM native image，v2.5.0，build 2026-10-05）的 reachability metadata 未把 `app.freerouting.board.model.items.ObstacleArea` 注册为可序列化。布线线程在 `BoardSnapshotManager.getHash()`（Java 序列化求 MD5）处抛 `UnsupportedFeatureError` 死亡，主进程不退出、无限等待，管线 60 秒超时将其杀死，SES 永不生成。五块板全部如此。
2. **官方已修但未发布**：上游 issue #957（与本次完全同症）由 PR #966（2026-10-09 合入 master，晚于 v2.5.0 发布 4 天）修复，明确把 `ObstacleArea` 等 49 个类型标记为 `serializable: true` 并加了线程死亡防护；后续 PR #1088（2026-11-06）又补了 EnumSet 类型（修复 #966 引入的 EnumMap 回归 #1019）。**但最新 release 仍是 v2.5.0，修复只在 master，无可下载的新原生二进制**。
3. **可立即落地的修复：改用 JVM 发行版**。v2.5.0 release 附件含 `freerouting-2.5.0.jar`（64.6 MB，Main-Class `app.freerouting.Freerouting`，与原生 CLI 同参数）。已下载到 /tmp 并用 RUN/case1、case2 的 DSN 实测：JVM 下无序列化问题，两块板均**真实完成布线并输出可被项目 `parse_ses` 解析的 SES**，case1 任务耗时 11.4s、case2 12.3s（含 JVM 启动全程 <20s，远在 60s 超时内）。
4. **CLI 参数无法绕过 getHash**：`--router.optimizer.enabled=false` 不能跳过批量循环（管线本来就带着它仍崩），源码中 `getHash()` 被 `AutorouteBatchLoop.run` 无条件调用，无对应开关。
5. **`Rect_T_850x650_um` 告警与 Y1 封装无关**：该 padstack 在 DSN library 中定义完整，Y1 的三条网络都真实连到了 Y1 焊盘坐标。告警真实对象是 **H1–H4 安装孔**：`board_to_dsn` 在 placement 段放了 `IMG_H1..IMG_H4`，但 library 段没有这些 image → freerouting 丢弃这 4 个机械件并按上游日志 bug 打印了过时的 scanner 标识符（恰好是最后解析的焊盘名）。各 case 告警数与孔数严格吻合（4/3/0/4/0）。fastroute 用的 `board.dsn` 与 `board_freerouting.dsn` 逐字节相同（仅第 1 行文件名不同），它只是静默容忍缺失 image。

## 2. 故障链证据

### 2.1 管线调用方式

`cloud_pcb/general_solver/run_blind.py:1994-2051`：生成 `board_freerouting.dsn`（`dsn_ses_bridge.board_to_dsn`），随后以 60s 超时执行：

```
freerouting-cli -de board_freerouting.dsn -do board_freerouting.ses \
  --gui.enabled=false --router.copperToEdgeClearanceUm=500 \
  --router.autorouter.max_passes=1 --router.optimizer.enabled=false
```

SES 由 `dsn_ses_bridge.parse_ses` 解析（要求 `(routes ...)` 与 `(network_out ...)`），`merge_ses_into_board` 合回 KiCad 板。`router_processes.json` 记录五块板 freerouting 均 `timed_out: true, returncode: null`。

### 2.2 崩溃栈（RUN/case1/freerouting.log，与其余 4 块板一致）

```
Exception in thread "Thread-1" com.oracle.svm.core.jdk.UnsupportedFeatureError:
SerializationConstructorAccessor class not found for declaringClass:
app.freerouting.board.model.items.ObstacleArea ...
    at java.io.ObjectOutputStream.writeObject(...)
    at BoardSnapshotManager.serialize(BoardSnapshotManager.java:33)
    at BoardSnapshotManager.getHash(BoardSnapshotManager.java:61)
    at BasicBoard.getHash(BasicBoard.java:185)
    at AutorouteBatchLoop.run(AutorouteBatchLoop.java:65)
    at BatchAutorouter.runBatchLoop(BatchAutorouter.java:527)
    at RoutingPipeline.runRoutingStage(RoutingPipeline.java:106)
[runner: process timed out; partial output preserved]
```

`getHash()` 自己只 catch `Exception`，而 `UnsupportedFeatureError` 是 `Error` → 直接杀线程。`RoutingJobSchedulerActionThread` 在 v2.5.0 中没有线程死亡防护（该防护正是 PR #966 加的），主进程挂死直到管线超时。

### 2.3 /tmp 一手复现（2026-11-17 20:52）

```
timeout 70 /workspace/bin/freerouting-cli -de board_freerouting.dsn -do native_repro.ses \
  --gui.enabled=false --router.copperToEdgeClearanceUm=500 \
  --router.autorouter.max_passes=1 --router.optimizer.enabled=false
```

结果：~1s 内抛出同样的 `UnsupportedFeatureError`（4 条 `package not found` 告警照旧），随后进程挂死，70s 由 timeout 强杀，**无 SES 输出**。与 RUN 记录完全一致。

## 3. 修复选项调查与验证

### 选项 a：等待/构建官方原生修复 —— 上游已修、未发布

证据链：

- issue #957「Native CLI 2.5.0 (macOS arm64): UnsupportedFeatureError for ComponentOutline on first board snapshot, routing thread dies and process hangs」——与本次同症（报错类不同：ComponentOutline vs ObstacleArea，同属 `board.model.items`）。
- PR #966「fix(cli): register serializable classes for GraalVM and guard against thread death (#957)」，2026-10-09 合入 master。其 diff（已留存 `agent-a-freerouting/pr966.diff`）在 `reachability-metadata.json` 新增 49 个 `serializable: true` 类型，**显式包含** `ObstacleArea`、`ComponentObstacleArea`、`ViaObstacleArea`、`ComponentOutline` 等，并修改 `RoutingJobSchedulerActionThread` 增加线程死亡防护（以后再有遗漏类会快速失败而非挂死）。
- v2.5.0 自带的 `reachability-metadata.json` 已核对：**没有任何 ObstacleArea 条目**（此即崩溃直接原因）；master 同文件中 4 个 ObstacleArea 系条目均带 `serializable: true`。
- PR #1088（2026-11-06 合入）注册 EnumSet 类型，修复 #966 之后的 EnumMap 回归（issue #1019）。
- **release 现状**：官方 releases 最新为 v2.5.0（2026-10-05），无 v2.5.1/snapshot 二进制可下载。要用上修复只能：等官方发版，或用 GraalVM + Gradle 从 master 自行构建 native image（重，本机未验证）。

结论：方向上正确但短期不可用；除非愿意投入本机构建工具链（未实验），不建议作为当前修复路径。

### 选项 b：JVM 发行版（推荐，已实测）

v2.5.0 release 附件列表含 `freerouting-2.5.0.jar`。MANIFEST：`Main-Class: app.freerouting.Freerouting`，`Created-By: 25.0.4.1 (Eclipse Adoptium)`——与本机 `/root/.local/share/mise/installs/java/25/bin/java`（Temurin 25.0.4.1）完全同版本系。`java -jar freerouting-2.5.0.jar -h` 输出与原生 CLI 相同的参数（`-de/-do/-mp/-mt/-us/-hr/-is` 及 `--key=value`）。

**case1 实测**（命令与管线参数逐字相同，仅把 `freerouting-cli` 换成 `java -jar ...`，完整日志见 `agent-a-freerouting/case1_jvm.log`）：

- 无 `UnsupportedFeatureError`，无其他异常；
- Fanout：89 个 SMD 引脚需扇出，逃逸 48/89（53.9%）；
- 自动布线：82 个连接起步，单 pass 8.07s，final score 951.22（**3 unrouted，0 violations**）；
- 任务总耗时 11.36s（含 JVM 启动全程 ~16s wall），峰值堆 242.6 MB；
- 输出 `board_freerouting.ses` 39963 字节。

**SES 可解析性**（用项目 `parse_ses` 原样解析）：

| 指标 | case1 | case2 |
|---|---|---|
| routed_nets | 36/36 | 50/50 |
| segments | 437 | （解析正常） |
| via_count | 36 | — |
| total_wirelength_mm | 841.991 | — |
| layers_used | F.Cu, In2.Cu, B.Cu | — |

坐标尺度与 fastroute 的 SES 一致（两引擎输出均为 DSN 值的 10 倍，`parse_ses` 的 `mm_per_unit=1e-4` 处理后落回正确 mm——这是既有约定，非 JVM 路线引入的新问题）。

**连通性审计**（脚本留存 `agent-a-freerouting/audit_connectivity.py`：把 DSN 每个网络的每个引脚坐标与 SES 走线/过孔做并查集比对）：

- case1：36 个多引脚网络中 33 个全连通；**3 个不完整 = freerouting 自报 "3 unrouted" 完全吻合**，分别是 `+3V3` 的 U3-5、`CAN_H` 的 J12-2、`CAN_RX` 的 J6-1（各差 1 个引脚未触及）。对照 fastroute 报告（unrouted=0），这是引擎布线能力差异，不是故障。
- 补测 `-mp 10`：10 个 pass 最好成绩仍是第 1 pass 的 3 unrouted（后续 pass 撕开重布反而 19–22 unrouted），即这三个引脚对 freerouting 是真难点，加 pass 无效。
- case2：50 个网络中 2 个不完整（`/usb_esd-`、`Net-(R12-Pad2)`，各断成 2 个连通域），与日志 "2 unrouted" 吻合；另有 2 个装载前已存在的间距违规、终态 3 violations。

**导入计数对比**（DSN 声明 → freerouting 实际）：

| 项 | DSN 声明 | 导入（SES placement 计数） | 说明 |
|---|---|---|---|
| 放置组件 | 50 | 46 | 缺 H1–H4（机械孔，library 无 IMG_H*，见 §4） |
| 电气 image/padstack | 46 个 image / 160 引脚 / 21 个 padstack（含 `Rect_T_850x650_um`） | 全部正常 | Y1 焊盘导入正常，Y1 三条网络均连到精确焊盘坐标 |
| 网络 | 36（118 个引脚引用） | 36 | 其中 3 个网络各缺 1 个引脚未连通 |

### 选项 c：CLI 参数绕过 getHash —— 不可行

- `-h` 全部参数如上，无任何禁用快照/哈希的开关；
- 源码 `AutorouteBatchLoop.run()` 无条件调用 `router.board.getHash()`（用于 TaskStateChangedEvent 与 BoardHistory 排名），`--router.optimizer.enabled=false` 只关优化阶段，批量布线循环仍走 `getHash`——管线本来就带这个参数，照样崩（RUN 日志为证）。

### 3.4 关于"换更新版本的原生 CLI"

无 v2.5.0 之后的 release（见选项 a）。若未来 v2.5.1 发布，可直接替换 `bin/freerouting-cli`；这是中期选项。

## 4. `Rect_T_850x650_um` 封装告警根因

**不是 padstack 缺失，也不是 freerouting 解析限制。** 结论分三层：

1. **告警打印名是误导**。freerouting 源码 `io/specctra/parser/Network.java` 的 `insertComponent`（v2.5.0 第 934-945 行）在查不到组件 package 时打印 `scanner.getScopeIdentifier()`——那是网络段读完后的陈旧 scanner 状态，恰好停在最后解析的焊盘名（case1 是 IMG_Y1 的 `Rect_T_850x650_um`，case2 是 `Rect_T_2290x3000_um`）。真正的失败键是 image 名，但没被打印（上游日志 bug）。
2. **真实缺失对象是安装孔 image**。`board_freerouting.dsn` 的 placement 段有 `(component IMG_H1 (place H1 ...))` 等 4 项，但 library 段没有 `IMG_H1..IMG_H4` 定义 → freerouting 告警并丢弃这 4 个组件。告警次数与各 case 孔位放置数严格吻合：case1=4、case2=3、case3=0、case4=4、case5=0。SES placement 实收 46/50，缺的正是 H1–H4（纯机械、无网络，对布线零影响）。
3. **Y1 的 `Rect_T_850x650_um` 完全正常**。该 padstack 在 library 段第 704-707 行定义齐全；JVM 布线结果中 `Net-(Y1-TRI_STATE)`、`/RCC_OSC_IN`、`+3V3` 三条网络的走线端点分别精确落在 Y1-1/Y1-3/Y1-4 的焊盘坐标（已逐点核对，如 (18.235,13.585)、(19.785,15.235)、(19.785,13.585) mm）。`board.dsn`（fastroute 用）与 `board_freerouting.dsn` 仅第 1 行文件名不同，fastroute 只是不报告缺失 image。

**顺带的 DSN 生成改进建议**（归属 `dsn_ses_bridge.board_to_dsn`，本次未改代码）：要么为安装孔补发 `IMG_H*` image + 对应 padstack，要么不在 placement 中放置无库组件。可消除误导性告警并保持 placement 完整（50/50）。

## 5. 推荐方案与风险

### 推荐（短期，立即可做）

把 freerouting 调用从原生二进制切换为 JVM jar：

```
java -jar <path>/freerouting-2.5.0.jar -de board_freerouting.dsn -do board_freerouting.ses \
  --gui.enabled=false --router.copperToEdgeClearanceUm=500 \
  --router.autorouter.max_passes=1 --router.optimizer.enabled=false
```

- 已验证两块板在 60s 超时内完成（任务耗时 ~11-12s，全程 <20s）；
- SES 与现有 `parse_ses`/`merge_ses_into_board` 完全兼容；
- jar 放置位置由仓库维护者决定（本任务遵守约束未动 `bin/`）；Java 25 已在标准位置（mise），`which java` 可见。

风险与注意：

1. **性能裕度**：当前五块板都很小（≤64 组件），JVM 全程 <20s。更大板材时需复测 60s 裕度；JVM 预热后单板 ~8-12s，风险低。
2. **内存**：峰值堆 ~243MB，分配 churn 4.5GB（G1 回收正常），6.5GB 机器无压力。
3. **版本一致性**：jar 与当前原生 CLI 同为 v2.5.0（同一 build-revision `b647a2f...`），布线行为与原生版一致——包括 case1 的 3 unrouted、case2 的 2 unrouted+3 violations 这些**既有质量差距**（fastroute 对照为 0 unrouted）。换 JVM 只修"崩溃无输出"，不提升 freerouting 的布线完成率。
4. **运行依赖**：依赖本机 Java 25（mise 安装路径）。若部署环境变动需把 `java` 纳入环境检查。
5. 60s 超时对 JVM 路线仍适用，无需调整；若未来换更大板可酌情提高。

### 中期

- 关注 freerouting 官方 v2.5.1+ release（含 #966、#1088），发布后可评估换回原生单二进制（无 JVM 依赖、启动更快）。届时需复测 ObstacleArea 场景与 EnumMap/EnumSet 回归。
- 修复 `board_to_dsn` 的安装孔 image 缺失（§4），消除误导性告警。

### 不建议

- 等待期间自行用 GraalVM 从 master 构建 native image：工具链重、未验证，且 JVM 路线已满足需求。
- 试图用 CLI 参数绕过（§3 选项 c 已证不可行）。

## 6. 实验产物索引（可审计）

| 文件（`artifacts/optimization/diagnostics/agent-a-freerouting/`） | 内容 |
|---|---|
| `case1_jvm.log` | JVM jar 跑 RUN/case1 DSN 完整日志（COMPLETED，3 unrouted，0 violations） |
| `case2_jvm.log` | JVM jar 跑 RUN/case2 DSN 完整日志（COMPLETED，2 unrouted，3 violations） |
| `board_freerouting.ses` | case1 的 JVM 布线输出（39963 B，可被 parse_ses 解析） |
| `case2.ses` | case2 的 JVM 布线输出（56756 B） |
| `audit_connectivity.py` | DSN 引脚 vs SES 走线连通性审计脚本（`/tmp` 路径参数化） |
| `pr966.diff` | 上游修复 PR #966 完整 diff（含 reachability-metadata.json 新增 49 个 serializable 类型） |

原生崩溃复现命令（2026-11-17 20:52，/tmp/freerouting-exp）：`timeout 70 /workspace/bin/freerouting-cli -de board_freerouting.dsn -do native_repro.ses --gui.enabled=false --router.copperToEdgeClearanceUm=500 --router.autorouter.max_passes=1 --router.optimizer.enabled=false` → 1s 内抛 UnsupportedFeatureError，挂死至超时，无 SES。
