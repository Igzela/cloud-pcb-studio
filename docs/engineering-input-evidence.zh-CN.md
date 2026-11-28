# Case 2 与 Case 5 工程输入证据

本记录基于活动基线 `20261122T134458373666Z-exact-geometry-and-micro-escape` 的公开回归板和厂家资料。尺寸采用毫米。[冻结清单](../artifacts/optimization/runs/20261122T134458373666Z-exact-geometry-and-micro-escape/manifest.json)

## Case 5 · P0 · USB 插头板边铜距

Rev.E 冻结输入将 `USB1` 定义为 `USB1065`，封装为 `OLIMEX_Connectors-FP:USB3_A_MALE`。原理图确认了器件型号。两个外壳固定脚均编号 `0`，铜盘为 1.60 × 4.00 的长圆，镀槽为 1.00 × 2.50。连接器固定姿态在板框局部坐标为 `(51.85, 9.50, -90°)`，变换后两槽中心为 `(49.20, 3.65)` 和 `(49.20, 15.35)`。向右最外铜点为 `x = 51.20`，板边为 `x = 51.50`。因此两铜盘的板边距均为 **0.30 mm**，镀槽边距为 1.05 mm。冻结 F.Cu 与 B.Cu Gerber 均实测为 0.30 mm，低于项目 0.50 mm 门槛。[冻结输入](../artifacts/optimization/runs/20261122T134458373666Z-exact-geometry-and-micro-escape/case5/effective_spec.json) · [冲突证据](../artifacts/optimization/runs/20261122T134458373666Z-exact-geometry-and-micro-escape/result.json#L208) · [板级审计](../artifacts/optimization/runs/20261122T134458373666Z-exact-geometry-and-micro-escape/case5/blind_run_report.json) · [OLIMEX Rev.E 原理图](https://github.com/OLIMEX/USB-GIGABIT/blob/e30a671f84527391f9f351bd8a0563ab36cceb6f/HARDWARE/USB-GIGABIT-Rev.E/USB-GIGABIT_Rev_E.pdf)

GCT 的 USB1065 推荐焊盘图给出相同的铜盘和镀槽尺寸，两个外壳脚间距 11.70 mm，槽中心到 `PCB EDGE` 标称 2.65 mm。冻结板的对应距离为 2.30 mm，相差 0.35 mm。厂家图对应的标称铜距为 0.65 mm。应优先用订货资料、连接器实物和装配尺寸核对这 0.35 mm 的基准差，确认插接深度、壳体干涉、板厂成形公差与镀槽孔环后，再评审板框或接口的输入修订。0.50 mm 门槛及冻结证据纳入复测。[GCT 厂家图纸公开副本](https://www.quick-teck.co.uk/Management/EEUploadFile/1426248819.pdf) · [OLIMEX 产品与盒体设计入口](https://www.olimex.com/Products/USB-Modules/Ethernet/USB-GIGABIT/open-source-hardware)

将固定姿态沿板边法线向内移动 0.35 mm 的几何对照中，两处铜距均达到 0.65 mm。该值仅量化了铜距对接口基准的敏感性，仍需确认插接配合。[铜距几何对照](../artifacts/optimization/diagnostics/20261128-case2-antenna-geometry/connector-datum-comparison.json)

## Case 2 · P1 · U1 天线区域与四层净空

冻结 `U1` 的器件值为 `ESP32-WROOM-32E`，KiCad 库项为 `RF_Module:ESP32-WROOM-32D`。Espressif 的 32E 内置 PCB 天线。2026 年 8 月 5 日发布的数据表 v2.1 在图 3 对照了 32UE 的外接天线版本，§11.1 链接了 32D 和 32E 共用的官方焊盘 DXF。冻结输入中 1/38 脚为 GND，2 脚为 3V3，3 脚为 EN。中央焊盘 39 为 GND，冻结输入将它表示为多个 SMD 铜图元和热过孔。实际采购型号仍需由 BOM 和模组标识核实。冻结输入缺少外形与天线禁布区，RF 审计将 `U1` 记为未解析。[冻结输入 U1](../artifacts/optimization/runs/20261122T134458373666Z-exact-geometry-and-micro-escape/case2/effective_spec.json#L2785) · [冻结 RF 审计](../artifacts/optimization/runs/20261122T134458373666Z-exact-geometry-and-micro-escape/case2/blind_run_report.json) · [Espressif 数据表 v2.1](https://www.espressif.com/sites/default/files/documentation/esp32-wroom-32e_esp32-wroom-32ue_datasheet_en.pdf#page=43) · [厂家焊盘 DXF](https://www.espressif.com/sites/default/files/modules-dxf/ESP32-WROOM-32D%2632E%2632SE%2CESP32-SOLO-1%20PCB%20Footprint_0.dxf)

图 13 中天线区标称 18.00 × 6.19 mm，模组顶边到 1/38 脚中心线为 7.49 mm。冻结 1/38 脚中心为 `x = ±8.75, y = -8.25`，14/25 脚中心为 `y = 8.26`，与图中 16.51 mm 的纵向距离一致。由此得到天线区局部角点 `(-9.00, -15.74)` 至 `(9.00, -9.55)`。按冻结顶层姿态 `(33.763, 24.086, 0°)` 投影，得到基板坐标 `(24.763, 8.346)` 至 `(42.763, 14.536)`。独立复算得到以下铜面积。[Espressif 图 13](https://www.espressif.com/sites/default/files/documentation/esp32-wroom-32e_esp32-wroom-32ue_datasheet_en.pdf#page=43) · [冻结布局与覆铜](../artifacts/optimization/runs/20261122T134458373666Z-exact-geometry-and-micro-escape/case2/routed.kicad_pcb)

| 铜层 | 天线投影内铜面积 mm² |
| --- | ---: |
| F.Cu | 78.2997 |
| In1.Cu | 108.0833 |
| In2.Cu | 88.8367 |
| B.Cu | 104.1264 |

预检时先核对 U1 型号，再用官方 DXF 和数据表对齐 1/38 与 14/25 脚、模组顶边和天线区，并核实馈点位置。记录局部坐标、板面、旋转，以及与板框和板边接口的关系。2026 年 11 月 27 日版 ESP32 布局指南的图 23 与图 24 优先推荐天线伸出基板、馈点靠近板边。天线无法伸出时，图 25 示范右侧馈点布局的切口，天线左侧标注至少 15 mm，右侧和外缘分别标注最多 2 mm 和 1 mm。[Espressif 布局指南图 23–25](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32/pcb-layout-design.html#pcb-layout-design-on-a-base-board) · [32E 厂家 STEP](https://www.espressif.com/sites/default/files/3dmodel/ESP32-WROOM-32E_20210903_0.STEP)

Espressif FAQ 要求天线区域及向外 15 mm 保持无铜、走线与元件。对选定布置记录适用的板边与馈点几何，复测天线投影和所采用的净空区域在四层的铜相交面积、邻近器件和基板切口。成品壳体内的天线净空也需核对，指南建议各方向至少 15 mm，并通过实物测试核查吞吐量和通信距离。[Espressif FAQ](https://docs.espressif.com/projects/esp-faq/en/latest/hardware-related/hardware-design.html#for-modules-with-pcb-antennas-what-should-be-noted-when-i-design-the-pcb-and-the-housing-of-the-antenna) · [Espressif 基板布局与机壳指南](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32/pcb-layout-design.html#pcb-layout-design-on-a-base-board)

### 标称天线区摆位与布线对照

单板诊断在冻结输入的副本中只增加 `U1.antenna_keepout_mm`。现有摆位算法把模块移到 `(33.7625, 9.2100, 0°)`，天线投影从与板框重叠 111.42 mm² 变为完全伸出板框，最近处与板边相距 0.34 mm。重建板的 50 个网络全部布通，独立 LVS 开短路、真实间距违例和非热焊盘内过孔均为 0。重新读取保存的 KiCad 文件后，标称天线投影在四层均测得零铜。最小实际线宽 0.125 mm，Gerber、钻孔和离线 Chromium 预览通过。[初始诊断与摆位](../artifacts/optimization/diagnostics/20261128-case2-antenna-geometry/preflight.json) · [实板复核](../artifacts/optimization/diagnostics/20261128-case2-antenna-geometry/independent-audit.json) · [浏览器审计](../artifacts/optimization/diagnostics/20261128-case2-antenna-geometry/assembly_browser_report.json)

输出板实测线长从 1248.608 mm 增至 1439.605 mm。参考铜的 F.Cu、B.Cu 和 In1.Cu 分别减少 36.119、88.380 和 6.031 mm²。装配检查中 59 项外形仍待确认，馈点和周边净空及 RF 性能尚待独立核对。该单板诊断用作后续比较，活动基线继续使用原冻结产物。[逐层参考铜差异](../artifacts/optimization/diagnostics/20261128-case2-antenna-geometry/reference-pour-comparison.json) · [保存的 KiCad 板](../artifacts/optimization/diagnostics/20261128-case2-antenna-geometry/routed.kicad_pcb)
