# NanoVNA 第②轮·线 B：仪器 tab + VNA 面板 + 三档快照

> 范围：把线 A 的 `vna::NanoVnaClient` 接到桌面右栏「仪器」tab；真实串口连接流程；
> S11(VSWR)/S21(增益) 最小可视化；MBD_VNASHOT 三档快照。纯 UI/测试层，
> 不碰 `cpp/*` 冻结面之外的既有逻辑、不改 tool_schema/agent_tools/control_hub。
> 全仓 MIT；干净室自写，不抄 GPL/WTFPL 上游代码。

## 1. 串口接线 file:line

- 连接入口（线 B 新增）：`cpp/src/vna/nanovna_client.h` `NanoVnaClient::connectSerial(device)`；
  实现 `cpp/src/vna/nanovna_client.cpp:connectSerial`——内部 `tr_ = createSerialTransport(device); return connect();`，
  即 termios 115200 8N1 后端（`nanovna_client.cpp` `TermiosVnaTransport`）+ help/version/info 握手。
- UI 连接按钮：`cpp/src/ui/vna_panel.cpp` `VnaPanel::onConnectClicked()`——
  读端口输入框 → `engine_->vnaClient().connectSerial(dev)`；失败诚实停在空态，不伪造。
- 轮询：`VnaPanel::poll()`（QTimer 1s，`vna_panel.cpp`）读 `readFrequencies()/readData(0)/readData(1)`。
- 端口提示：VID:PID `0483:5740`（STM32 CDC），文案在 `vna_panel_format.cpp vnaPortHintText()`；
  端口由用户显式输入，不硬编码 tty 名。

## 2. 仪器 tab / 面板 file:line

- 新 tab 接入：`cpp/src/ui/main_window.cpp` 在「电台」tab 后
  `rightTabs_->addTab(new ui::VnaPanel(engine_, rightCard), "VNA")`（右栏 QTabWidget）。
- 面板：`cpp/src/ui/vna_panel.{h,cpp}`
  - 状态行 / 连接按钮 / 端口输入 / VID:PID 提示 / 起·止·点数 spinbox / 开始测量按钮；
  - `VnaTraceWidget`（`vna_panel.h`）自绘曲线，idiom 同 RssiTrendWidget（tokens + scaled）；
  - S11→VSWR 曲线、S21→增益 dB 曲线；最小 VSWR 读数（`applyReadout()`）。
- 文案/格式抽纯函数：`cpp/src/ui/vna_panel_format.{h,cpp}`（`vnaStatusText/vnaReadoutText/vnaPortHintText`），
  单测 `cpp/tests/test_vna_panel_format.cpp`（7 项）。

## 3. 快照结论（MBD_VNASHOT，OCR 证据）

harness 种子块在 `cpp/tests/ui_screenshot_narrow.cpp`（`MBD_VNASHOT=1` 时
`findChild<VnaPanel*>()->seedSnapshotForTest(...)`，自造 41 点 1–30 MHz 回放数据，
14 MHz 谐振 S11 凹点 + S21 带通）。三档：

| 宽度 | 输出 | 结论 |
|---|---|---|
| 640 | 764×1058（触最小宽 floor） | 窄档优雅裁切/换行，无崩溃；非 OCR 证据档 |
| 960 | 960×640 | 控件齐全可读，无叠字 |
| 1920 | 1920×640 | 状态/VID:PID/扫频/两条曲线/最小VSWR读数全清晰，0 裁切 0 叠字 |

证据 PNG：`/tmp/vnashot/vna_{640,960,1920}.png`（运行产物，未入仓）。

## 4. phase31 工具文档

`docs/learn/phase31/agent-tool-documentation.md` 头部 55→58，手工补
`set_vna_sweep`/`get_vna_data`/`get_vna_status` 三段（schema 与 control_hub.cpp 真实签名对齐）。

## 5. 测试计数

- `test_vna_panel_format`：7 passed
- `test_nanovna_client`：7 passed（线 A 无回归）
- `test_ui_integration`：26 passed（无回归）

## 6. 增量协议核对（新源，本轮并入）

- `repos/NanoVNA-App-OneOfEleven/NanoVNA_v1_comms.cpp`：`data 0/1`=测量 S11/S21，
  `data 2..7`=校准误差项（App 常规轮询只取 0/1）；八通道全支持落档第③轮。
- V2 二进制寄存器命令集（CMD_V2_READ/WRITE/FIFO/INDICATE）仅落档，为 V2 Plus4 留路，本轮不实现。
- cal 状态字段以官方 `repos/nanovna-docs` page_2 Calibration 为准（open/short/load/thru）。

## 7. 诚实边界 / 未完成项

- 真机未在环：`connectSerial` 仅 termios 编译路径在，握手/真机取数待用户插 H/H4 回传。
- 串口自动枚举（list_ports）本轮未做——端口由用户输入；第③轮补枚举下拉。
- S21 相位/群延迟曲线、八通道 data 2-7、Smith 圆图均未做，留第③轮。
- `MBDSDR_TEST_SOURCE` 未设；offscreen 运行；未 git add/commit/push。
