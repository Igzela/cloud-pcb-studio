# PCB 通用求解器 v3：前置调研与待接入设计

日期：2026-11-17。状态：**外部技术调研已开展；项目接入阻塞，尚未完成 Phase 1 的仓库依赖审查，更未实施 Phase 2 / Phase 3。**

本环境未找到用户指定的 `/home/antigravity/cloud-pcb-studio`，也未在 `/workspace` 找到项目、`scripts/env.sh`、历史测试或 Case 1–3 数据。不能核实用户陈述的 26 项既有测试及历史成绩。没有执行 Python、autorouter 或 pytest；没有创建替代 `env.sh`。待项目接入后，所有这些命令均须先 `source scripts/env.sh`。

未读取任何候选硬件项目的 `.kicad_pcb`，未查看其布局图片、Gerber 或 3D 装配图。检索仅取得仓库元数据和 README 文本。候选尚未密封或正式编入 Case 4–6。没有修改用户的核心代码或检查门禁。

## 1. 网表分组与对称布局

依据：MIT 2025 年硕士论文摘要明确研究了带物理、电气权重的图及 Louvain / Leiden 聚类；目前仅核实摘要，未据此宣称全文实验效果。2024 年 PCB 子图匹配研究的出版记录支持把网表转为图做同构识别，但参考布局复用不适用于本轮密封测试。

- [MIT：Clustering Algorithms for Component Placement in Printed Circuit Boards](https://dspace.mit.edu/entities/publication/d88562ab-d0b5-43e9-ac1b-1003d3f37bb4)
- [NTU：Subgraph matching-based reference placement for printed circuit board designs](https://scholars.lib.ntu.edu.tw/entities/publication/eacc19f0-6f4a-4ed9-9793-1a82283e73be)

拟采用以下原创工程组合；这些设计尚待实现和验证，并非论文已证明的结果：

1. 保留器件—网络二部超图，使用封装焊盘几何、引脚电气类型、电气需求作属性。位号、网络字符串仅作输出身份，不能作为聚类特征。
2. 社区发现的投影权重可取 `w(i,j)=Σ criticality(n)/(degree(n)-1)`。这使高扇出公共电源和地不会把全板强行聚为一团；高电流回路、差分端点、时钟等约束另行保留。
3. 两端电容连接电源输入引脚和回流网络时，形成去耦候选。根据焊盘到电源脚距离及共享电源脚数量分配，不能以位号前缀或人为坐标绑定。区分储能、滤波、去耦，不把所有电容都拉到最近大 IC。
4. 以 Leiden 为首选候选、Louvain 为可选轻依赖方案；固定随机种子和参数。先检查仓库是否已有 NetworkX/igraph，避免在未审查依赖前引入并行的图栈。
5. 对候选局部子图做带类型的 Weisfeiler–Lehman 签名筛选，再做精确同构验证。签名相同不是同构证明；保留共享控制器和公共电源作为边界节点。只在同一盲网表内寻找重复通道，不读人工模板。
6. 局部先放 IC、去耦和高 di/dt 回路；同构通道共用局部模板并计算引脚映射。宏单元包络含器件实体、装配余量、布线通道和扇出空间。全局力导向初始化后模拟退火，随后合法化。
7. 几何镜像不能直接翻转实体封装引脚手性。通道的模板镜像必须通过合法旋转和同构引脚映射验证；不能为视觉对称悄悄交换电气端子。
8. 目标包含 HPWL、通道拥塞、去耦脚距、回路面积、同构通道相对位姿偏差和朝向惩罚。物理碰撞、孔位和 RF 禁区为硬约束；对称为软约束，不能压倒电气正确性。

零过拟合验证：随机重命名位号和网络、打乱 JSON 顺序后，拓扑特征及目标值应等价。局部坐标以封装本体和焊盘为基准；不得读取参考布局。对完全等价的节点，允许确定性择序造成旋转等价的解，但不能改变合规指标。

## 2. 颈缩、主干与内层电源岛

已核对 fastroute **v0.1.14 的真实源码**，tag 对应 commit：

`19c3b75322da6a0dc2f87a616fc0afe34a98f07e`

本地只读调研副本：`/workspace/pcb-v3-research/fastroute-source`。

可复查证据：

- [固定版本 README](https://github.com/parisxmas/fastroute/blob/19c3b75322da6a0dc2f87a616fc0afe34a98f07e/README.md)：自动颈缩、板级最小线宽、禁止指定 class 颈缩、差分对和报告选项。
- [DSN 解析器 model.rs](https://github.com/parisxmas/fastroute/blob/19c3b75322da6a0dc2f87a616fc0afe34a98f07e/crates/fr-dsn/src/model.rs#L947)：`read_net` 接收 `rule`，但对 net 的 `layer_rule` 发出未实现告警。
- [加载器 network.rs](https://github.com/parisxmas/fastroute/blob/19c3b75322da6a0dc2f87a616fc0afe34a98f07e/crates/fr-io/src/network.rs#L376)：per-net `Width` 生成/选择线宽类；per-net `Clearance` 仅告警未实现。
- [同一加载器](https://github.com/parisxmas/fastroute/blob/19c3b75322da6a0dc2f87a616fc0afe34a98f07e/crates/fr-io/src/network.rs#L474)：后加载的显式 class 对所属 net 调用 `set_class`；class 的线宽、间距和 layer rules 可载入。因此混写 per-net 宽度和包含该 net 的通用 class 可能使预期宽度被覆盖。
- [control.rs](https://github.com/parisxmas/fastroute/blob/19c3b75322da6a0dc2f87a616fc0afe34a98f07e/crates/fr-engine/src/autoroute/control.rs#L239)：禁止颈缩类会关闭普通颈缩并设置 `no_neckdown`。
- [inserter.rs](https://github.com/parisxmas/fastroute/blob/19c3b75322da6a0dc2f87a616fc0afe34a98f07e/crates/fr-engine/src/autoroute/inserter.rs#L251)：引脚附近几何判定后才尝试局部颈缩。
- [Specctra Design Language Reference，镜像](https://cdn.hackaday.io/files/1666717130852064/specctra.pdf)：已发现，尚未逐节核对；本轮具体兼容性结论以固定版本解析器和加载器为依据，不能把完整商业 Specctra 的能力直接归给 fastroute。

设计决定：

1. 为每个 net 从电流、温升/压降预算、铜厚、层及阻抗需求计算路由意图。`>=0.50 mm` 是本轮目标下限，不能被描述为任意电流下都足够的工程线宽。
2. DSN 导出以相同规则元组归并为**互斥的 class**；必要时一个网络一个 class。标识使用规则内容的稳定摘要，不通过网络名正则区分工程。
3. 保留电源主干宽度；仅在真实引脚逃逸瓶颈附近允许几何约束下颈缩，并在报告中统计细段总长、最大连续细段、距端点距离。不能仅检查 DSN 文本中有宽度就算成功。
4. `--no-neckdown-classes` 的作用是禁止指定类别颈缩，适合阻抗受控信号；把全部电源放入其中可能使细间距电源脚不可达。`--router.min_trace_width_um` 是全局最小宽度，不能拿它表达每条网络不同的主干宽度。
5. 对无法可靠表达的 pin rule，不写入未经支持证实的伪字段。必要时生成经独立几何 DRC 验证的短逃逸段，并验证 SES 往返是否保留它。
6. In1.Cu 优先完整 GND。依据电源端点分布、外层布线拥塞和电流需求，评估 In2.Cu 电源岛；计算同网端点的几何连接区域，再避让 RF、安装孔及高速信号参考走廊。
7. 底层高速线邻近 In2.Cu 时，电源岛切缝可能破坏参考回流。必须检查实际邻接层和沿线参考铜连续性，必要时调整信号层或电源岛；不能仅凭 In1.Cu 完整就声称所有高速线参考良好。
8. 浇铜声明不等于已连通。填铜后重新解析实际铜多边形，以 via/pad 和同层铜的真实接触重建连接；孤岛和热焊桥的瓶颈必须报告。禁止在 LVS 中把同一网络的所有 zone 自动合并。
9. GND 扇出候选从 pad/孔/clearance 几何产生；代价同时考虑回流距离、过孔数量、差分/总线通道占用及平面开窗。普通元件 via-in-pad 为禁止约束，热焊盘例外必须来自封装或明确属性。

## 3. RF、开关电源、晶振与机械语义

- [Espressif 官方 PCB 指南](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32/pcb-layout-design.html)：建议完整地参考和模组天线净空。其特定产品净空尺寸不得推广为所有模组的统一常量。
- [TI：Power Tips — How to Find Switching Loops in a Power Supply](https://www.ti.com/document-viewer/lit/html/SSZTBF4)：页面及检索摘录支持根据开关回路识别高 di/dt 区域；未取得完整正文，实施前应继续读完整小节。
- [Analog Devices：Guidelines for Placing the Inductor on a Switch Mode Power Supply PCB](https://www.analog.com/en/resources/analog-dialogue/raqs/raq-issue-164.html)：已发现相关技术资料，尚需完整正文核对。

拟实现具有证据和置信度的语义记录，而不是名称命中即执行硬规则：

- 天线候选综合模组/天线名称和值、焊盘缺口、器件外框相对焊盘的悬出、封装局部 rule area。无焊盘区域也可能是连接器插接空间，不能仅凭缺口判为天线。
- 优先从允许输入中的封装机械/天线约束取得净空与方位；若缺少高度、天线方向或具体净空，报告信息不足。参考板原有 zone、track、摆位绝不能用于补足。
- 高置信度天线端朝向板外并贴边，对板内净空生成 F.Cu、In1.Cu、In2.Cu、B.Cu 的规则区。除覆铜外还应禁止新 trace/via 进入；不能删除模组本身合法的天线金属。
- 独立 RF 检查应交叉检查输出 PCB 的全部实际铜与最终 Gerber 铜。若某层缺失、解析失败或未填充，不能记作零覆铜。
- 高功率开关回路利用电感、开关/整流引脚、输入/输出电容和供电端的拓扑辨识，区分 buck 与 boost 等拓扑；不把所有电感都当作最关键的热回路组成。开关节点紧凑，反馈/精密模拟远离噪声区。
- 晶振及其负载电容围绕时钟引脚成小簇；保护回路和参考地，不能为整齐跨模块拉远。
- 连接器朝向由外壳开口、焊盘及板边接口约束推导；安装孔检查同时考虑钻孔和螺钉/垫圈/工具包络。孔径不能替代螺钉头占位。

## 4. 无桌面 KiCad 的 3D 装配与检查

外部工具能力已查明，但仓库内 `package.json`、锁文件、`board1_tscircuit.py` 和 `(model ...)` 处理逻辑均未取得，不能声称已经检查其实际依赖版本或兼容性。

- [KiCad board 格式](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/)
- [KiCad 10 PCB Editor](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html)
- [simple-3d-svg README](https://github.com/tscircuit/simple-3d-svg)：面向场景投影和快照，可辅助侧视图，但不能直接证明实体机械零干涉。
- [tscircuit 3d-viewer README](https://github.com/tscircuit/3d-viewer)：支持交互、Circuit JSON 和外部模型；当前上游功能不等于项目锁定版本已有的功能。
- [Three.js 官方文档](https://threejs.org/docs/)：候选渲染基础，实施时固定依赖版本并读取该版本 API。

架构决定：

1. 以毫米、板面 Z-up、统一顶底面变换建立 `assembly_scene.json`。分别存 body、lead、mounting feature、安装高度及尺寸来源/置信度。不要将焊盘包围盒直接称为真实元件壳体。
2. 优先用封装提供的 F.Fab / B.Fab / courtyard 和实际 3D model；为标准系列建立由封装尺寸驱动的 QFN、LQFP、SOIC、R/C、排针、USB-C、电感、电解电容参数模型。模型显示与碰撞检测必须使用同一局部到板坐标变换。
3. 有真实本地模型才写可解析的 `(model ...)` 路径；优先项目相对路径并打包资产。没有模型文件则写明包络元数据，不生成指向不存在的 STEP/WRL 引用。
4. 主交付选用**自包含 Three.js HTML**：构建时打包 JS、模型和场景，不依赖 CDN 或网络。FR4 挤出板形并扣孔，显示阻焊、露铜焊盘、走线、过孔和实体元件，提供旋转/缩放、图层开关、net hover。可增补 GLB，但 GLB 不是交互 UI 的替代品。
5. Raycaster 命中实体后按 `net_id` 高亮 pad/track/via；不把一个多引脚 IC 的整个实体随意归到单一 net。
6. 机械检查采用 XY 多边形空间索引粗筛、实体多边形与 Z 区间精查。分别检查元件互撞、孔/紧固件、机械禁布体。电气 RF keepout 与实体机械禁布体具有不同语义，不能统一当作无限高禁止空间。
7. 对底面、旋转、壳体悬出、THT 针脚等使用真实位姿；授权的连接器针脚入孔及引脚焊接接触不能误报为壳体冲突。零碰撞判定必须指明已验证的几何模型范围。
8. 未知高度、缺失外壳、未经核实的通用替代模型标为 `unverified`。`collision_count==0` 与 `all_envelopes_verified==true` 都满足才可声明装配门禁通过。
9. 浏览器验证必须禁止外网请求，监控 pageerror/console error/模型加载失败；断言 scene ready、实际元件网格数量、非零 Z 高度，截取俯视/斜视/侧视。非白屏像素统计仅是辅助，不能用一块平面板通过“元件已装配”。测试旋转、缩放、图层开关和网络拾取。

## 5. 新盲测候选：尚未入选、尚未密封

| 候选 | 已取得 commit SHA | README 明确的特征 | 待隔离抽取核实 |
|---|---|---|---|
| [jonathanrandall/four_motor_controller](https://github.com/jonathanrandall/four_motor_controller) | `66a2616b3bff252392b95749a6af20d1ba99755e` | 声明 4 层，4 路电机、ESP32-S3、GPIO 扩展，CERN-OHL-P-2.0 | 实际层数、器件数、最小 pitch、天线型号/方向、参考板门禁质量 |
| [Neumi/ethersweep](https://github.com/Neumi/ethersweep) | `67f2547e582de59cb77d4b3c2a25c8a6326ed864` | STM32F103、W5500 Ethernet、USB 编程、TMC2209 | 层数尚未从 PCB 抽取确认；器件数、多个差分对及速率需求、硬件许可证、参考质量 |

两者 SHA 来自 GitHub commit 查询，不是 README blob SHA。候选特征并不能证明满足全部选型条件，不应提前将它们命名为已通过资格审查的 Case 4 / Case 5。PBrick 因其 README 的非商业限制排除；OpenDualMotorDriver 的硬件授权范围仍不清晰，也未纳入。

未来只读密封流程应在修改核心求解器前执行：

1. 先读取项目现有 blind_spec schema 与已有抽取脚本，仅检查旧回归输入；拒绝直接搬用参考 PCB 的完整 footprint 表达式。
2. 一次性隔离导入器以固定 SHA 获取候选，参考二进制只写入密封目录且不打印内容。只向外输出允许字段、器件总数、层数与输入/参考哈希。
3. 明确白名单：板外形、安装孔、允许的边沿接口约束、器件局部焊盘/封装几何、网表、引脚属性、电气需求。不输出绝对放置坐标、角度、焊盘绝对坐标、tracks、vias、zones、ratsnest 提示、模型内部工程绝对路径、原图层选择或 UUID 层次信息。孔位与获准边沿连接器的坐标除外。
4. 器件几何须保留 footprint 本地坐标，去除人类 instance 摆位；不能丢失焊盘本地朝向。处理通过翻面实例还原的情况。
5. 对剥离器做独立合成测试：改变参考元件摆位、走线和覆铜后脱敏输出完全相同；移动允许的孔或接口时对应机械约束才改变。
6. 封装目录只读并记录 SHA256。权限限制只是防误读措施，同一用户仍可能绕过，不能宣称构成密码学访问隔离。状态机/审计脚本还需禁止比较工具在正式解封前运行。
7. 所有 Case 使用同一冻结代码、策略、种子和资源预算。运行时不向求解器传参考路径或来源项目名称。
8. 冻结所有产物及自检结果（包括失败记录）后才能解封。若仍修复通用问题，必须全量重跑并重新冻结；不得先读参考再修最后一版代码，同时继续声称是纯陌生盲测。
9. 一旦解封即标为 exposed。后续针对已解封集的优化只能算回归，泛化评估需要另引入未见集。

## 6. 接入后的门禁和指标口径

保留原有要求，不降低阈值、不扩大忽略项。LVS 使用真实铜几何建立跨层连接；差分长度包含过孔层间高度，报告成对长度、绝对失配、并行耦合覆盖及间隙，不能只调等长。

GND 覆铜率同时报告板形内原始面积比例，以及扣除明确 RF 禁铜和安装机械要求后的比例，并列出扣除项；不能通过扩大禁区或缩小分母凑够 85%。安装孔、板边安全距和原始板外形也必须独立核验。

总走线长度并列报告所有线段、信号网络、各电源网络及过孔高度。人工通过 plane 实现的连接不具有等价的线段长度，因此仍照实给出用户要求的总长比，同时标注 plane 连接的差异，不能悄悄改统计口径。

Gerber 用真实产物全层解析，不把“解析成功”写成“电气通过”。孔环使用真实钻孔和铜环几何，丝印压焊盘用输出丝印与露铜/阻焊开窗几何相交，板边距使用铜实体外边缘而非中心线。

原有同网封装焊盘误报仅可通过明确的几何与同网证据分类。输出过滤前/后计数及原因，不能屏蔽整个器件或网络。额外门禁要以失败样例证明会失败，不能以所有报告都为零的 mock 验证。

预期新增必要测试：

- 网表重命名/顺序打乱不改变分组；公共 GND 不吞并社区；同构与近似但不同构反例；镜像引脚手性。
- 一个细间距焊盘不降低无关电源主干；DSN class 互斥及实际载入宽度；合法局部颈缩及过长细段失败；电源岛浮岛与高速参考切缝失败。
- RF 方向歧义；非天线连接器缺口；旋转后四层禁铜；任一层 trace/via/zone 进入均失败。
- 顶底面旋转实体、已知高度相交/不相交、孔头包络、未知模型不报 PASS；浏览器实际元件高度及离线运行。

执行模板仅在项目到位后使用，不能据此宣称运行过：

```bash
cd /home/antigravity/cloud-pcb-studio
source scripts/env.sh
python -m pytest -v
```

实际盲跑与比较命令需先检查项目 CLI，再写冻结脚本，不能凭文件名猜参数。当前测试计数、耗时、Case 指标与 3D 截图均为 **未执行 / 不可报告**。
