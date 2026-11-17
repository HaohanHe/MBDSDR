# Spec 状态报告

<!-- generated-by: scripts/spec_status.py -->
> 本文件由 `scripts/spec_status.py` 从 spec 文档自动生成，**请勿手改**。
> 行内文本均为 spec 源文件的逐字引文，不构成本文件的独立断言；状态以源文件为准。
> 数字全部来自下列文件，脚本不解析代码、不补默认值。
> 数据源：`.trae/specs/mbdsdr-system-lifecycle/tasks.md`、`.trae/specs/mbdsdr-system-lifecycle/checklist.md`
> 生成时间：2026-11-18 07:17:31+08:00

## 总览

| 域 | tasks 完成 | checklist 完成 | not_verified |
| --- | --- | --- | --- |
| 0. 跨域不变量 | 0/0 | 6/6 | 0 |
| D1 设备与信号链 | 13/13 | 13/13 | 3 |
| D2 数字解码 | 11/11 | 9/9 | 0 |
| D3 录制与数据交换 | 7/7 | 9/9 | 0 |
| D4 时空与卫星 | 16/16 | 13/13 | 0 |
| D5 PNT / GNSS / 授时 / RTK | 11/11 | 8/8 | 1 |
| D6 AI 与工具面 | 33/33 | 17/17 | 0 |
| D7 交互与四端 | 34/34 | 25/26 | 4 |
| D8 平台与交付 | 18/18 | 17/18 | 3 |
| D9 多文化天空 | 18/18 | 13/13 | 0 |
| D10 SDR 与 GNSS 常驻 | 70/70 | 21/24 | 5 |
| 追加任务（89–130，按时间顺序；跨域，编号不代表归属） | 164/164 | 0/0 | 4 |
| 生命周期门 | 0/0 | 7/8 | 1 |
| 完成判定 | 0/0 | 0/0 | 1 |
| （前言/说明） | 0/0 | 0/0 | 1 |
| **合计** | **395/395** | **158/164** | **23** |

## 未完成项（6）

- `checklist` · D7 交互与四端 · 第 129 行：真实相机/IMU/望远镜端到端——`not_verified`（无硬件，未做夜空验证）（缺硬件/工具链，补齐后才能验）
- `checklist` · D8 平台与交付 · 第 149 行：桌面 GUI 人工视觉走查——`not_verified`（深色主题截图已生成，仍需人看交互与观感）（源行未写原因：缺硬件或人工复核，补齐后才能验）
- `checklist` · D10 SDR 与 GNSS 常驻 · 第 181 行：真机 SDR 采集——`not_verified`：设备可连接/调谐，但 USB 数据通路 0 样本（驱动层，非参数）；CLI 已改为 2 s 有界退出（退出码 3）（源行未写原因：缺硬件或人工复核，补齐后才能验）
- `checklist` · D10 SDR 与 GNSS 常驻 · 第 192 行：真实 SDR 数据通路复测——`not_verified`：本机 5 个残留进程仍占着 USB 句柄且无法终止（`HasExited=True` 仍烧 CPU、`taskkill` 报 no running instance），需重启系统后再验
- `checklist` · D10 SDR 与 GNSS 常驻 · 第 193 行：真机断线重连验证——`not_verified`（假后端验证通过，无真实 SDR 复测）（源行未写原因：缺硬件或人工复核，补齐后才能验）
- `checklist` · 生命周期门 · 第 204 行：退役：本轮无适配器退役（源行未写原因：缺硬件或人工复核，补齐后才能验）

## not_verified 清单（23 行 / 23 次）

- `tasks` · D1 设备与信号链 · 第 26 行 · 已勾选：SubTask 7.2: 无真机时标 `not_verified`（流程项：要求在缺条件时如实标注，本条不是能力验证结论）
- `tasks` · D1 设备与信号链 · 第 27 行 · 非勾选行：- **验证**：`scripts/hil_tx_check.py` 无硬件退出码 3（本机实测）；`docs/COORDINATION.md` 已记 `not_verified`（缺硬件/工具链，补齐后才能验）
- `tasks` · D7 交互与四端 · 第 177 行 · 已勾选：SubTask 49.1: 无 Flutter SDK 时标 `not_verified`（流程项：要求在缺条件时如实标注，本条不是能力验证结论）
- `tasks` · D7 交互与四端 · 第 179 行 · 非勾选行：- **验证**：`mobile/README.md` 写明「验证状态：not_verified」；本机无 flutter，未编译（缺硬件/工具链，补齐后才能验）
- `tasks` · D8 平台与交付 · 第 236 行 · 非勾选行：- **验证**：`scripts/release_gate.py` 实跑：工具面/文档一致性通过，关键测试未执行列为 `not_verified`；定向回归 **2202 passed / 1 xfailed**（186 文件）（源行未写原因：缺硬件或人工复核，补齐后才能验）
- `tasks` · D10 SDR 与 GNSS 常驻 · 第 285 行 · 非勾选行：- **验证**：`_publish_runtime_fix` + `_publishing` 防递归；断开边沿只发布一次；`test_gnss_poll_publish` 5 + `test_gnss_order_preserve` 3 passed。真实 GNSS 模块实机复测仍 `not_verified`。（源行未写原因：缺硬件或人工复核，补齐后才能验）
- `tasks` · 追加任务（89–130，按时间顺序；跨域，编号不代表归属） · 第 387 行 · 非勾选行：- **验证**：`docs/limitations-register.md` L66/L67 标 `not_verified`，注明未改代码（源行未写原因：缺硬件或人工复核，补齐后才能验）
- `tasks` · 追加任务（89–130，按时间顺序；跨域，编号不代表归属） · 第 513 行 · 已勾选：SubTask 115.1: 解析 tasks/checklist，按域统计勾选与 not_verified（流程项：要求在缺条件时如实标注，本条不是能力验证结论）
- `tasks` · 追加任务（89–130，按时间顺序；跨域，编号不代表归属） · 第 516 行 · 非勾选行：- **验证**：`scripts/spec_status.py` 按 `## 0./D1–D10/追加任务/生命周期门` 分组；真实统计 tasks **335/335**、checklist 152/157、not_verified 20；`test_spec_status.py` 12 passed；报告 `docs/spec-status.md`（源行未写原因：缺硬件或人工复核，补齐后才能验）
- `tasks` · 追加任务（89–130，按时间顺序；跨域，编号不代表归属） · 第 553 行 · 非勾选行：- **验证**：`rtk_solver.py`；`test_rtk_fixed.py` 8 passed（合成基线位置误差 < 5 cm、相位 RMS < 1 cm）；模块 docstring 明写「不能证明真实空口的厘米级」；真机 RTK 仍 `not_verified`（源行未写原因：缺硬件或人工复核，补齐后才能验）
- `checklist` · （前言/说明） · 第 3 行 · 非勾选行：> 勾选只认证据（测试输出 / 实测数字 / 文件内容）。`not_verified` 不算完成。（说明性引用：本条不构成待验收项，缺可验证对象）
- `checklist` · D1 设备与信号链 · 第 29 行 · 已勾选：发射 HIL 项标 `not_verified`（`scripts/hil_tx_check.py` 无硬件退出码 3，已记台账）（缺硬件/工具链，补齐后才能验）
- `checklist` · D5 PNT / GNSS / 授时 / RTK · 第 78 行 · 已勾选：固定解与已知基线对比在厘米级（**合成数据**：0.056 cm；真实空口 not_verified）（源行未写原因：缺硬件或人工复核，补齐后才能验）
- `checklist` · D7 交互与四端 · 第 113 行 · 已勾选：手机端在无 SDK 时标 `not_verified`（`mobile/README.md`）（流程项：要求在缺条件时如实标注，本条不是能力验证结论）
- `checklist` · D7 交互与四端 · 第 129 行 · 未勾选：真实相机/IMU/望远镜端到端——`not_verified`（无硬件，未做夜空验证）（缺硬件/工具链，补齐后才能验）
- `checklist` · D8 平台与交付 · 第 140 行 · 已勾选：Spec 状态报告自动生成：按域统计 + 未完成/not_verified 清单（`scripts/spec_status.py`，`test_spec_status.py` 12 passed）（源行未写原因：缺硬件或人工复核，补齐后才能验）
- `checklist` · D8 平台与交付 · 第 149 行 · 未勾选：桌面 GUI 人工视觉走查——`not_verified`（深色主题截图已生成，仍需人看交互与观感）（源行未写原因：缺硬件或人工复核，补齐后才能验）
- `checklist` · D10 SDR 与 GNSS 常驻 · 第 181 行 · 未勾选：真机 SDR 采集——`not_verified`：设备可连接/调谐，但 USB 数据通路 0 样本（驱动层，非参数）；CLI 已改为 2 s 有界退出（退出码 3）（源行未写原因：缺硬件或人工复核，补齐后才能验）
- `checklist` · D10 SDR 与 GNSS 常驻 · 第 185 行 · 已勾选：Pluto IIO / BladeRF 无超时路径已登记 `not_verified`，不宣称已修（状态项：源行只登记未验证，缺对应硬件才能销账）
- `checklist` · D10 SDR 与 GNSS 常驻 · 第 192 行 · 未勾选：真实 SDR 数据通路复测——`not_verified`：本机 5 个残留进程仍占着 USB 句柄且无法终止（`HasExited=True` 仍烧 CPU、`taskkill` 报 no running instance），需重启系统后再验
- `checklist` · D10 SDR 与 GNSS 常驻 · 第 193 行 · 未勾选：真机断线重连验证——`not_verified`（假后端验证通过，无真实 SDR 复测）（源行未写原因：缺硬件或人工复核，补齐后才能验）
- `checklist` · 生命周期门 · 第 200 行 · 已勾选：验证：单元/集成/离屏/仿真全绿；HIL 与真实硬件标 `not_verified`（源行未写原因：缺硬件或人工复核，补齐后才能验）
- `checklist` · 完成判定 · 第 208 行 · 非勾选行：- 代码级条目全部勾选；`not_verified` 保留：桌面 GUI 人工视觉走查、（源行未写原因：缺硬件或人工复核，补齐后才能验）
