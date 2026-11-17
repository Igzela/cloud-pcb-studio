# 通用盲测 PCB v3：调研、实现边界与复现

本轮从 `aff93d3e3d8d68fac3a8045b8f892850d0feffd5` 开始。性能与达标情况以最终逐板报告为准，本文不宣称已经达到人工资深工程师 80%–85% 水准。

## 调研依据与实际选择

### 纯网表分组与同构

[MIT 2025 论文](https://dspace.mit.edu/entities/publication/d88562ab-d0b5-43e9-ac1b-1003d3f37bb4)摘要支持带物理/电气权重的 Louvain / Leiden 聚类方向；本轮没有以摘要代替全文实验验证。[NTU 子图匹配论文出版记录](https://scholars.lib.ntu.edu.tw/entities/publication/eacc19f0-6f4a-4ed9-9793-1a82283e73be)说明网表图匹配用于 PCB 放置的研究背景，本实现没有读取任何人工布局模板。

实际采用已有 NetworkX 的 Louvain，避免为 Leiden 引入新底层依赖。公共电源网络不参与社区边，其他超边按 `1/(degree-1)` 投影，避免高扇出网络吞并社区。封装几何/型号/值构成节点签名，WL 筛选之后使用有属性精确同构验证，不能把相同哈希当作同构证明。仅使用当前网表内部的重复结构；重命名网络与位号的独立测试验证发现行为等价。

实现了社区宏目标、重复通道锚点对齐、引脚距离驱动的局部合法化，以及将去耦放在普通信号无源件之前以预留位置。**仍是启发式布局**：没有完成 Leiden、力导向+模拟退火的完整层次优化，没有保证整个同构子电路的全部器件严格镜像/等距摆放。锚点对齐是软目标，后续合法化可以移动它。

### 颈缩与内层电源岛

已核对 fastroute v0.1.14 源码，版本 commit `19c3b75322da6a0dc2f87a616fc0afe34a98f07e`；仓库实际二进制的 `--version` 为 0.1.14，`--help` 确认参数存在。

- [固定源码 network.rs](https://github.com/parisxmas/fastroute/blob/19c3b75322da6a0dc2f87a616fc0afe34a98f07e/crates/fr-io/src/network.rs)：per-net width 可以载入，但显式 class 随后重新分配网络；per-net clearance 未实现。
- [固定源码 model.rs](https://github.com/parisxmas/fastroute/blob/19c3b75322da6a0dc2f87a616fc0afe34a98f07e/crates/fr-dsn/src/model.rs)：per-net layer_rule 未实现。
- [固定源码 inserter.rs](https://github.com/parisxmas/fastroute/blob/19c3b75322da6a0dc2f87a616fc0afe34a98f07e/crates/fr-engine/src/autoroute/inserter.rs)：引脚附近根据几何尝试缩窄。
- [固定版本说明](https://github.com/parisxmas/fastroute/blob/19c3b75322da6a0dc2f87a616fc0afe34a98f07e/README.md)：`--no-neckdown-classes` 禁止指定类别颈缩，适合受控阻抗类别；并非打开局部颈缩的选项。

因此导出按网络独立的互斥规则类，默认电源主干至少 0.50 mm，有明确电流要求时调用项目现有 IPC-2221 估算。未指定电流时明确标为未核实，不能将 0.50 mm 当作任意电流的安全保证。保留局部颈缩及全局制造最小线宽；差分对各自按目标阻抗校准规则，不再共用第一对的阻抗值。

根据真实 via/PTH 位置及端点数量/电流建立 In2.Cu 电源岛候选，只有至少两个镀通连接点的区域才能晋升，避开异网镀通铜、RF 区域及底层信号参考走廊。In1.Cu 保留 GND。此方案尚未实现 DC 压降/温升/完整回流仿真，填铜面积不能替代电源完整性证明。

### 物理与 RF 语义

[Espressif 官方指南](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32/pcb-layout-design.html)支持模组天线净空和完整地参考方向，但该产品具体尺寸不能复制成全体无线模块的常量。[TI 开关回路资料](https://www.ti.com/document-viewer/lit/html/SSZTBF4)和 [ADI 电感布局资料](https://www.analog.com/en/resources/analog-dialogue/raqs/raq-issue-164.html)用于确认高 di/dt 回路与电感位置应分开考虑；本轮没有完成所有开关拓扑的自动热回路提取。

实现天线身份与封装无焊盘悬出区域的交叉推断；只有名称、缺少方向几何时标为未知。天线 lobe 尽量朝板外，焊盘仍维持板边铜距；生成四层禁走线/过孔/覆铜 rule area，在 DSN、实际填铜和逐层检查中使用相同几何约束。推断的 lobe 只覆盖可见的无焊盘实体区，不等于已经验证完整射频净空或外壳影响。旧盲规范缺少模组本体几何时不能声称 RF 已通过。

### ECAD/MCAD

实际安装依赖包括 `@tscircuit/simple-3d-svg@0.0.41`、`@tscircuit/jscad@0.0.6`、`circuit-json-to-kicad@0.0.275`；`board1_tscircuit.py` 的 sanitizer 会剥除不完整的 `(model ...)`，保留完整模型表达式。

[simple-3d-svg](https://github.com/tscircuit/simple-3d-svg)适合投影和快照；[tscircuit 3d-viewer](https://github.com/tscircuit/3d-viewer)有交互和模型加载能力。实际实现选择固定 `three@0.179.1`，使用 [Three.js](https://threejs.org/docs/) 的实体几何、OrbitControls 和 Raycaster；esbuild 将代码及场景嵌入单个 HTML，无 CDN 和远程模型依赖。

`assembly_scene.json` 包含封装、尺寸来源、估计高度、位姿与网表身份；HTML 渲染 FR4 挤出、钻孔、金色露铜、走线、过孔、芯片本体及金属端子、排针、USB、电解圆柱等参数外形，支持旋转缩放、图层开关、黑/绿阻焊切换和网络高亮。每个输出 KiCad 封装还含可回读的 `MCAD_Envelope` 属性，保存包络尺寸、高度、依据和验证状态。交付使用**包络元数据**途径，没有虚构指向不存在 STEP/WRL 的 `(model ...)`。

机械检查使用实体 XY 多边形和 Z 区间，包括元件互撞、安装孔/紧固件占位和明确机械禁布体。未知高度或只有焊盘的包络会标为 `unverified`；零包络碰撞和“所有实体已充分验证”分别报告，不能混称真实外壳零干涉。参数外形不是厂商精确 STEP 模型。

无头浏览器实际检查 WebGL、非平面元件网格数量、图层交互、运行错误、外网请求，以及斜视/俯视/侧视截图的像素变化。系统 Chromium 在环境中连 about:blank 都启动超时，项目 `bin/chromium` 优先使用已安装的 Playwright Chromium；原门禁阈值未改变。

## 独立检查发现并修复的原型问题

新增逐层铜几何并查集 LVS，跨层合并必须有真实 plated via/PTH，填铜孤岛分开处理。原投影式统计仍保留为 legacy 字段，不再用它证明实际跨层连接。

检查暴露出旧 `_slit_polygon_no_holes` 用带宽度的切缝分割孔洞，造成贯穿地平面的 1 微米绝缘缝。本轮改为零宽度精确分割，新增 19 个孔洞的面积与连通性测试；没有以容差弥合真实切缝。非圆 PTH 的内层 antipad 也改为基于真实焊盘多边形外扩。

GND 过孔候选加入差分通道占用代价；窄板拥塞时仅对小型 SMD 无源件尝试背面放置，保留通孔占位限制并处理相应焊盘层和 GND 扇出层。规则仍是保守几何启发式，并不等于最优信号通道规划。

第二轮自检修正了省略焊盘角度的 KiCad 约定：省略角度代表板级 0°，而非继承封装角度。脱敏器将它正确归一化到封装局部坐标，旋转不变性测试也同时处理隐式零角度。保留椭圆钻槽和钻孔偏移，实际孔环检查使用完整铜几何，缺铜或缺层不能被跳过。

Gerber 几何合成按文件中的暗/透明极性顺序执行，不能将所有透明区域最后统一相减，否则会错误擦掉后续绘制的焊盘。圆角与非直角焊盘使用真实区域图形。圆弧长度改为精确圆弧长度，差分对长度统计计入实际/估计的镀通过孔柱高度。

最终审计将 4 个铜层、双面阻焊、双面钢网、双面丝印和板框共 11 个 Gerber 全部送入严格 PyGerber 解析；双面丝印分别与相应阻焊开窗相交后合计。旧实现只解析 7 层且只检查正面丝印，此范围缺口在解封前修正并重新全量运行，没有将未检查层计为通过。

为解决外层拥塞，普通和电源网络允许使用 In2.Cu，差分对保留外层，In1.Cu 始终作为地参考。实际内层信号从地铜和异网电源岛中扣除，后加的地缝合过孔禁止进入电源岛及内层信号净空。细间距差分端点如果无法容纳整段目标阻抗线宽，则允许引脚附近颈缩；这不等于已经验证整条差分链路的信号完整性。

路由器原始间距告警完整保留，只对同一封装、同一非空网络、真实局部焊盘几何有面积重叠的条目作有证据的误报分类；详细记录不齐全的告警仍计入真实数量。禁止按整颗器件、整条网络或类别屏蔽告警。首轮产物保存在 `artifacts/v3_attempts/attempt1/`，其暴露的问题在统一修改后全量重跑，新参考没有提前解封。

后续自检还发现：原 DSN 只保护 GND 扇出过孔，未保护预布的 GND 短线，自动走线可能跨过这些短线而短路。修复把真实短线几何导出为 `wire_keepout` 和 `via_keepout`，前者移除已由焊盘保护的内部区域；类型语义已核对固定 fastroute 源码，不能用普通 keepout 误伤同网焊盘。对应独立测试验证两种障碍均进入 DSN。发现该问题时停止了中间尝试，未解封参考，并重新执行统一全量运行。

对耦合差分路由未收敛的板，增加保留目标线宽、独立布线并按对调长的候选，按真实违例数、未布通数、线长依序选优。最终报告明确标出模式，不能把该模式描述为已经证明全程差分耦合。3D 查看器按交互重绘，避免软件 WebGL 在静止时持续重绘造成截图超时，截图仍需通过相同的实体和错误检查。

电解电容单独识别 `C_Elec_DxH` / `CP_Elec_DxH` 标准封装身份：D 是圆柱直径、H 是高度，不能把它们当作平面长宽或按低矮 MLCC 渲染。明确标注名义高度来源，并与机械包络检查共用这组尺寸；缺少高度依据的其他器件仍保留未验证标记。

## 密封与冻结

使用独立只读 `scripts/seal_holdout.py` 从 Git object 解析，不输出参考摆位/走线。白名单仅包含板形、孔、边沿 mating connector 接口位姿、封装局部焊盘和本体几何、网表、电气候选需求。接口位姿属于用户允许的机械约束；其余实例坐标、角度、原始走线/覆铜/过孔/人类注释不进入 blind_spec。脚本验证改变非接口摆位/角度及删除铜后脱敏结果不变。

Case 4：`jonathanrandall/four_motor_controller`，commit `66a2616b3bff252392b95749a6af20d1ba99755e`，113 器件、131 网络，4 通道电机驱动和无线模组，CERN-OHL-P-2.0。

Case 5：`OLIMEX/USB-GIGABIT` Rev.E，commit `e30a671f84527391f9f351bd8a0563ab36cceb6f`，52 器件、51 网络，USB 3 与千兆以太网，CERN OHL v1.2。通用后缀发现 9 对差分候选，阻抗由接口拓扑/惯例推断；没有以参考走线反推需求，全部候选仍需电气资料审查。

先筛选的 ethersweep 因硬件销售授权限制排除；CHLinkNet 具有当前未支持的 custom copper pad，未作为本轮盲测。选择发生在查看任何参考布局之前，未依据求解成绩筛选替代项目。Case 4/5 器件规模来自隔离抽取计数。

完整冻结脚本对同一代码顺序运行全部五板，对输入、核心代码、工具、测试、路由板、Gerber、报告和 3D 产物记录哈希；全部完整产物冻结后才能执行解封命令。解封前后不允许改动冻结求解代码。新参考文件在解封前为 mode 000；这只是防误读，不冒充无法绕过的访问隔离。

```bash
source scripts/env.sh
python -m pytest -v
python -m scripts.run_blind_v3
# 仅在所有五板产物完整冻结且哈希验证通过后执行：
python -m scripts.run_blind_v3 --unseal
```

逐板失败会明确写出。`fastroute` 成功不等于板级门禁成功；报告分别列出路由、LVS、Gerber、孔环、丝印、板边、RF 和机械验证。解封后的新案例从此属于已暴露集，后续针对它们的开发不可继续宣称纯陌生盲测成绩。
