# CDCSS / DCS 数字编码亚音 — 全链收尾落地

本轮（HEAD 自 00e3dc8 起）把上轮纯 DSP 解码器 `CdcssDecoder`（`cpp/src/dsp/cdcss.{h,cpp}`，Golay(23,12) 干净室实现）接进完整链：引擎、三通道工具、mobile catalog、UI 面、持久化、确定性测试与快照。

## 1. 互斥域设计（诚实定稿）

CDCSS 与 CTCSS 是**两个并列的亚音域**，二者在引擎里共享一个"音调门控"决策位，但**检测使能与门控开关分离**（镜像 CTCSS 语义）：

- **检测使能**（`cdcssEnabled_` / `ctcssEnabled_`）只负责"开解码器 + 点亮 badge"，本身**不静音**；
- **门控开关**（`cdcssGateEnabled_` / `ctcssGateEnabled_`）才决定"无匹配时是否静音 speaker"。

门控决策公式（`spectrum_engine.cpp` run loop）：

```
anySubGate   = ctcssGateEnabled_ || cdcssGateEnabled_
toneGateOpen = !anySubGate
            || (ctcssGateEnabled_ && ctcssOpen)
            || (cdcssGateEnabled_ && cdcssOpen)
gate         = squelchGate && toneGateOpen
```

语义：
- **无 gate 启用 → 恒开**（只检测不门控，speaker 由普通 squelch 决定）；
- **任一域 gate 启用 → 该域匹配才放行**；
- **两域 gate 同开 → 任一匹配即放行**。

> 诚实说明：实际电台同一时刻只用一个亚音域；双开是**配置错误**，但引擎不假死、不崩溃——任一域匹配即放行，另一个域的匹配也会放行（这是"或"语义，文档在此注明，不假装这是受控行为）。

recorder / WAV 落盘**不门控**（镜像既有 CTCSS 先例：录制永远收真实解调音频，门控只影响 speaker）。

## 2. 与 CTCSS 对照

| 维度 | CTCSS（模拟） | CDCSS/DCS（数字） |
|---|---|---|
| 检测器 | `CtcssToneDetector`（Goertzel） | `CdcssDecoder`（Golay 前向） |
| 输入 | post-ANR 48k mono | post-ANR 48k mono（同处喂入） |
| 参数域 | 频率 67.0–254.1 Hz | 三位八进制 DCS 码 "023"–"754"（104 码表） |
| 工具写 | `set_ctcss{enabled,frequency_hz,gate_audio}` | `set_cdcss{enabled,code,gate_audio}` |
| 工具读 | `get_ctcss_status` | `get_cdcss_status` |
| UI | ctcssCheck / 音调 spinbox / ctcssGateCheck | cdcssCheck / DCS码 combo / cdcssGateCheck |
| 持久化键 | rx/ctcssEnabled, rx/ctcssToneHz, rx/ctcssGate | rx/cdcssEnabled, rx/cdcssCode, rx/cdcssGateAudio |

## 3. Golay(23,12) 干净室说明

引用 `docs/learn/phase63/cdcss-decoder.md`：本轮未改 Golay 实现。生成多项式 `g(x)=0xC75`、系统编码 `cw=(data<<11)|(work&0x7FF)`、综合征查表纠正 ≤2 bit 错，全部来自公开代数规范，代码为本仓自写（GPL 中立、MIT）。

## 4. 落地位置 file:line

### 引擎 `cpp/src/dsp/spectrum_engine.{h,cpp}`
- 读回：`cdcssEnabled()/cdcssPresent()/cdcssCode()/cdcssGateAudio()`
- setters：`setCdcssEnabled/setCdcssCode/setCdcssGateAudio`（非法码经 `CdcssDecoder::isValidCode` 拒绝）
- 成员：`CdcssDecoder cdcss_; atomic<bool> cdcssEnabled_{false}; int cdcssCode_=023; atomic<bool> cdcssGateEnabled_{false};`
- run loop：在 CTCSS 喂入块后并列喂 `cdcss_`（apply desired enabled/code）；gate 决策按 §1 公式扩展。

### 持久化 `cpp/src/core/tokens.h`
- `kSettingsKeyCdcssEnabled="rx/cdcssEnabled"`、`kSettingsKeyCdcssCode="rx/cdcssCode"`、`kSettingsKeyCdcssGate="rx/cdcssGateAudio"`。

### 三通道
- `cpp/src/ai/tool_schema.cpp`：`set_cdcss`(write) + `get_cdcss_status`(read)。
- `cpp/src/ai/agent_tools.cpp`：`execSetCdcss`（code 八进制 `toInt(&ok,8)` + `isValidCode` 校验，非法→`ok:false`）、`execGetCdcssStatus`（回读 `arg(code,3,8,'0')`）+ dispatch 注册。
- `cpp/src/control/control_hub.{cpp,h}`：写表/读表注册 + `cmdSetCdcss/cmdGetCdcssStatus`。
- HTTP 无新路由（POST /command 透传）。

### mobile `mobile/lib/app/tool_catalog.dart`
- +2 条目（set_cdcss 写 / get_cdcss_status 读），注册序逐名对齐。
- `mobile/test/tools_catalog_test.dart` 49→51；`tools_catalog_page.dart` 注释 49→51（正文 `${all.length}` 动态跟随）。
- `buildRadioTools` 保持 10 **不接入**（移动端无自有解调链，桌面独有，诚实标注）。

### UI `cpp/src/ui/main_window.{cpp,h}`
- 控件：cdcssCheck_ / cdcssGateCheck_ / cdcssCodeCombo_（从 `CdcssDecoder::codeTableCount()` 填 104 项，itemData 存 12-bit int，显示三位八进制）/ cdcssBadge_。
- `updateCdcssBadge()` 四态：disabled「--」灰 / 门控静音「静音」黄 / 检测到「检测到」绿 / 仅开「未检测到」灰。
- 250ms 轮询定时器同时刷 CTCSS + CDCSS badge。
- save/restore 镜像 CTCSS（block+一次性派发引擎 setter，默认 off + "023"）。

## 5. 金集计数

- 桌面工具：**88 → 90**（+`set_cdcss` +`get_cdcss_status`）。
- mobile catalog：**49 → 51**。
- 写/读冻结拆分：30/19 → **31/20**。

## 6. 确定性测试结论（offscreen，真实计数）

| 二进制 | 结果 |
|---|---|
| test_tool_registry | 8 passed, 0 failed |
| test_tool_schema | 10 passed, 0 failed |
| test_ai_real_link | 17 passed, 0 failed |
| test_agent | 36 passed, 0 failed |
| test_control_hub | 31 passed, 0 failed |
| test_control_http | 15 passed, 0 failed |
| test_squelch_gate | 13 passed, 0 failed |
| test_ui_integration | 26 passed, 0 failed |

覆盖：合成 3-of-8 码流解码、非法 DCS 码拒绝（ok:false、引擎不动）、缺 enabled 报错、manual/write-gate 拦截、HTTP 透传落地与回读、UI 控件接线与持久化往返。

## 7. 快照核查

`ui_screenshot_narrow` 加 `MBD_CDCSS=023`（+`MBD_CDCSSGATE=1`）门控，拍 640 / 960 / 1920。1920 档布局整洁、无叠字；左卡为可滚动区，亚音节在折叠线以下（CTCSS 同此先例），UI integration 测试已确认控件接线。

## 8. 红线

- 无「比赛/competition」字样；GPL 中立 MIT；未预置呼号/电台。
- 未碰 `demod.*`、`vfo_manager.*`、`agent_tools.*` 已有签名、`control_hub.*` 既有 handler、`main_window.*` 既有 CTCSS 块。
- 未碰隔离文件 `cpp/tests/ui_diag_freeze.cpp`、`cpp/scratch/*`。
- 无 git add/commit/push。

## 9. 诚实未完成项

- DSP 级"合成 NFM IQ → gate 开 → speaker 放行非零"的引擎端 e2e（test_engine_audio_e2e）未扩——本轮工具/控制/UI 链路测试已全绿，DSP 解码器单测在 test_squelch_gate；引擎端码流→speaker 端到端留待下轮按需补。
- 双 gate 同开的"任一匹配放行"仅在公式层落实，未写专门 e2e 用例（配置错误语义，文档已注明）。
