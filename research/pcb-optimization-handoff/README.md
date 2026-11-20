# PCB 优化研究交接

这些文件归档了停止时已经完成的隔离原型、实验收据和评审补丁。补丁留在研究目录，求解器仍由前置 PR 中提交的代码运行。Case 6–9 和 Case 12–13 的参考板及来源身份继续密封。

| 研究 | 验证记录 |
| --- | --- |
| [准确 DSN 焊盘几何](dsn-pad-geometry-probe/README.md) | 两个引擎在合成板上通过保守轮廓对照。真实 Case 10 的主引擎仍报告 2 个 LVS 开路网络和 12 项间距，保留 45° 搜索树的局限。 |
| [保留预连通回流焊盘网络身份](dsn-physical-plane-probe/README.md) | 合成板验证真实铜、固定短线与内层铜面必须实际连通。分离的小岛与被开窗切断的锚点仍被报告为开路。 |
| [FastRoute 重现性](fast-repro-probe/README.md) | 32 次合成运行记录了墙钟暂停、计数预算、优化器和并行起跑。相同输入可产生两套未布通和间距报告均为零的不同走线。 |
| [差分逃线预留](corridor-witness-20261119/integration/integration.md) | 16 项测试通过。合成差分板经真实引擎续接，两条主干为 0.290 mm，最小对间距为 0.334 mm。真实板续接与 SI 尚待验证。 |
| [联合逃线](corridor-witness-20261119/coescape/README.md) | 4 项测试通过。联合搜索能在合成障碍板同时保住两个网络，拒绝仅转移开路的结果。Case 3 仍有开路。 |
| [双面布局的局部重排](typed-packing-rollback/README.md) | 4 项合成测试通过，两个贪心顺序都失败的一块小板可在移动一个器件后合法完成布局。布线与制造检查尚未验证。 |
| [局部去耦](typed-local-bypass-proposal/local_bypass_proposal.py) | 4 项合成测试通过。供电脚距离由 12.706 mm 缩至 2.000 mm，待验证真实板。 |
| [Gerber 透明内孔](hollow-aperture/hollow-aperture-proposal.patch) | 10 项原始字节测试通过。实心部分按极性顺序合成，覆盖圆形、矩形、长圆形光圈内孔。 |
| [3D 取景](assembly-camera-review/fit-assembly-views.patch) | 11 项无头 Chromium 取景测量通过。底面器件完整显示。附有[侧视图](assembly-camera-review/new-existing-side.png)、[离线预览](assembly-camera-review/new-existing.html)和[测量](assembly-camera-review/measurements.json)。 |

实验脚本保留原来的 `.run/` 路径。需要复现时，将相应归档目录复制到记录中的暂存路径，再从项目根目录加载 `source scripts/env.sh` 后运行。单板或合成测试结果没有用于基线晋升。
