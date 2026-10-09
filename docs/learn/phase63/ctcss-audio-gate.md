# CTCSS 亚音门控静音闭环（phase63 closed loop）

HEAD `74a2432` 落地了 CTCSS **检测**（`CtcssToneDetector` Goertzel + 三通道工具 + UI
面板）。这一轮把检测闭环成真正的「亚音静噪」：在检测之上新增一个**与检测使能分离的
speaker 门控开关**——开启后，仅当真实检测到匹配亚音才放音 speaker，无亚音 / 不匹配 /
未预热即静音。全程只接真实数据源，空态诚实，无 mock。

## 1. 机制

引擎 run 循环在 squelch 判决后把音频喂给检测器，门控判决与 squelch 组合：

```cpp
// cpp/src/dsp/spectrum_engine.cpp:1568
const bool squelchGate = squelch_.decide(audio, rms);
// 亚音门控（仅 speaker，与检测使能分离）
const bool ctcssOpen = !ctcssGateEnabled_.load() || ctcss_.tonePresent();
const bool gate = squelchGate && ctcssOpen;   // speaker + network tap 放行
```

- `ctcssGateEnabled_` 默认 `false`（`spectrum_engine.h:556`），经
  `setCtcssGateAudio(bool)`（`spectrum_engine.cpp:273`）从任意线程置期望态，引擎线程
  下一块才与 `ctcss_.tonePresent()` 组合——Goertzel 状态永不被 UI/agent 线程竞争。
- `ctcss_.tonePresent()` 本身诚实读 false：检测未使能 / 无信号 / 预热中 / 音调不符都读
  false。因此「门控开但无匹配亚音」⇒ speaker 静音，**绝不编造音频**。
- attack ~200 ms（一个 Goertzel 测量周期），hangover release ~600 ms（连漏 3 个周期），
  短暂音调跌落不会抖动门控。

### speaker 写路径（mono 与 stereo 都被门控）

- mono：`out` 仍是 squelch 门控后的电平（供录制/CW），speaker 副本 `spk` 在
  `!ctcssOpen` 时清零（`spectrum_engine.cpp:1632-1634`）。
- stereo（WFM）：M/S 矩阵的块增益 `g = gate && n>0 ? avg : 0`（`spectrum_engine.cpp:1615`），
  门控关时 L/R 增益归零。
- network tap 镜像**同一个** speaker 帧（mono `spk` / stereo L/R），与本地放音一致。

## 2. speaker-only 取舍（录制不被门控）

刻意只静音 **speaker**，录制路径保持 squelch 先例：

- gated recorder 喂的是真实 `leveled`，触发用 `squelchGate`（`spectrum_engine.cpp:1675`），
  不是组合 `gate`。
- WAV 连续录制连续性用 `squelchGate`（`spectrum_engine.cpp:1697-1705`），门控静音时仍
  写真实 `out`。
- `emit squelchState(squelchGate)`：静噪 LED 只反映 RMS 静噪判决，门控静音由 CTCSS
  badge 单独如实标注。

理由：录制是「事后取证」，门控是「实时听感」。一个被亚音门控静音的载波（用户不想听）
仍然应被值守录制捕获；否则 PL 错误配置会导致静默丢录。若日后判定录制也应门控，
把 `recGate` / WAV 连续性改回组合 `gate` 即可——当前默认只 speaker。

## 3. 与 SDR++ 门控思路对照（clean-room）

参考 SDR++ `ctcss_squelch` 的**门控机制概念**：检测到目标亚音才开放音频路径，未检测到
保持静音。本实现为 clean-room 重写，未抄 GPL 代码；项目保持 MIT。差异：

- SDR++ 的门控与检测通常耦合；本实现把「检测使能」与「speaker 门控」拆成两个独立开关，
  允许只检测不静音（观察模式）或两者同开。
- 本实现门控**只作用 speaker**，录制不受影响；空态（未使能/无信号）一律诚实静音而非
  假播放。

## 4. 三通道工具（gate_audio 选参 + 读回）

`set_ctcss` 加选参 `gate_audio`(bool)，`get_ctcss_status` 输出加 `gate_audio`：

- schema：`cpp/src/ai/tool_schema.cpp:553-596`（选参描述 + get 描述）。
- agent 执行器：`cpp/src/ai/agent_tools.cpp:833-847`（省略则保持当前门控；非布尔诚实报错）
  与 `:865`（读回 `engine->ctcssGateAudio()`）。
- control hub：`cpp/src/control/control_hub.cpp:1362-1367`（写）与 get 读回。
- 写工具仍受手动模式拦截（既有 gate 不变）。

## 5. UI

- 「门控」复选框 `ctcssGateCheck_`（objectName `ctcssGateCheck`），接线
  `setCtcssGateAudio` + 持久化 + `updateCtcssBadge`（`main_window.cpp:668-678`）。
- 持久化 key `rx/ctcssGateAudio`（`tokens.h:867`），保存 `main_window.cpp:4352-4353`，
  恢复 `:4699-4706`。
- badge 如实标注：检测关+门控开 → 琥珀「门控静音」；检测开+无音调+门控开 → 琥珀「静音」；
  匹配 → 绿「检测到」（`main_window.cpp:5515` 起）。
- 960 窄轨微调：音调 spinbox 最小宽由通用 `kComboMinW(120)` 让步为 `scaled(90)` 并设
  `QSizePolicy::Maximum` + 尾随 stretch，让 badge 在窄轨不被挤掉（不影响其他控件）。

## 6. 测试（确定性，禁 mock）

引擎级 e2e（合成 NFM IQ 直驱真实引擎 → `MemoryAudioSink` 捕获 speaker）：
- 无匹配亚音 → speaker RMS `0.0000`（静音）、present=false；
- 嵌入 88.5 Hz 亚音 → speaker RMS `0.2226`、present=true（放行）；
- 门控关 → speaker RMS `0.2345`（squelch-only 透传，行为同前）。

三通道金集（测试槽位数）：tool_registry 8 / agent 35 / control_hub 30 / control_http 15
= **88**（gate_audio 断言并入既有 CTCSS 槽，未新增槽）；引擎 e2e 槽 8→11；UI 集成槽 26
（CTCSS 往返扩展为含门控勾选 + 持久化 + badge「静音」断言）。全部 offscreen 实跑通过。

## 7. 快照

`ui_shot_narrow`（`MBD_CTCSS=88.5 MBD_CTCSSGATE=1 MBD_SCROLL=ctcssGateCheck`）拍 960/1920：
亚音☑、门控☑亚音静噪、音调 88.5 Hz + 琥珀「静音」badge 均可见，0 裁切 0 叠字。
