# FT8 收尾轮 2：UI 控件 + 状态徽标

HEAD=10e5024。干净室自写 MIT，镜像 main_window.cpp ctcss/cdcss badge 先例。

## 1. UI 落地 file:line

| 组件 | file:line |
|---|---|
| tokens 键 kSettingsKeyFt8Enabled="rx/ft8Enabled" | `cpp/src/core/tokens.h:875` |
| ft8Check_/ft8Badge_ 成员 | `cpp/src/ui/main_window.h:559` |
| FT8 checkbox + badge 构造（gRx 数字模式区，零新 dock） | `main_window.cpp:771`（"数字模式"/"FT8状态" 行） |
| updateFt8Badge()（诚实四态：未开/检测中/检出帧/已解码截断） | `main_window.cpp:5748` |
| 250ms 轮询接入（ctcssPollTimer_ 复用） | `main_window.cpp:2512` |
| saveSettings 持久化 | `main_window.cpp:4446` |
| restoreUiState 恢复 | `main_window.cpp:4836` |

**徽标诚实状态**（镜像 ctcssBadge 先例）：
- 未开启 → "--"（secondary）；
- 已开无候选 → "检测中"（secondary）；
- 检出帧但 BP 未出文本 → "检出帧"（warning）；
- 已解码 → success-green，截断前 18 字符+"…"（防溢出），tooltip 全文。

## 2. 快照结论

MBD_FT8 门控快照（ui_shot_narrow，MBD_OUT 设置）：
- harness 种子块：`cpp/tests/ui_screenshot_narrow.cpp:170`（MBD_FT8=1 持久化
  rx/ft8Enabled=true，BEFORE MainWindow 恢复，镜像 MBD_CDCSS 块先例）；
- MBD_SCROLL=ft8Check 滚动到该控件（复用既有 findChild+ensureWidgetVisible 逻辑）；
- 640/960/1920 三档均出图（harness 固定 960×640 输出），0 裁切 0 叠字。
- **OCR 证据**：三档均捕捉到 "数字模式 ✓ FT8"（checkbox 勾选）+
  "FT8状态 检测中"（secondary 徽标文本）；无挤压（对照 ctcss 窄轨先例，
  FT8 行与 DCS码/数字亚音行同排可见）。截图见 /tmp/mbd_ft8/。

## 3. vfo 真实 12k 窄带喂数（限制如实）

本轮**未接 vfo_manager channelizer 运行时喂数**。processFt8Window 为公开入口，
测试直调已验证；真实 12k 窄带链（channelizer 后 15s 环形缓冲 → processFt8Window）
留下一步。诚实标注：当前触发路径仍为测试注入，引擎 run loop 未自动喂数。
15s 窗内存 ≈ 1.44MB（12kS/s × 15s × 4B），启用时 CPU 增量待实测。

## 4. 金集 92 保持 / 回归

- 无新工具；get_ft8_status 字段不变（step4 decoded_text 已加）；
- test_ui_integration 26 passed（0 failed，UI 构造断言无回归）；
- ft8 三套件：detector 5 + codec 6 + e2e 5 = 16 全绿；
- mobile catalog 53 不变。

## 5. 诚实未完成项

- vfo_manager channelizer → processFt8Window 运行时喂数（15s 环形缓冲）；
- 时隙对齐（15s 多帧窗自动滚动）、SIC；
- 与 wsjtx 真实弱信号链路不直接类比（合成闭环）。
