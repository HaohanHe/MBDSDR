# 第二十一阶段实现规格：移除自动 fallback 测试信号，无硬件诚实空态

> 基线：HEAD = 95fab64，ctest 112/112（e2e_smoke 云 VM 无音频环境性红项不计）。
>
> 头号真实性问题：未连接真实设备时引擎自动 fallback 合成 TestSignalSource（main_window.cpp:244 banner「使用测试信号」、:3801 status「(test)」、:3805 注释）。违背「只接真实数据、无硬件诚实空态」。
>
> 勘察事实：引擎 spectrum_engine.cpp 在 **6 处** fallback：`:49`（启动无设备）、`:274`、`:286`、`:315`（设备打开失败）、`:381`（离线文件失败）、`:442`（设备丢失）。**20+ 测试**依赖 TestSignalSource/openOfflineFile（test_agc/agent/ai_real_link/audio_sink/control_hub/digital_e2e/engine_audio_e2e/engine_hotplug/engine_integration/file_source/leo_soak/shortcuts/soak/spyserver/squelch_autogate/tool_registry/ui_integration/wav_roundtrip/ui_screenshot_*）。命令行入口 src/main.cpp。

## Step 1（地基，单个 agent；独占 spectrum_engine / test_signal / main.cpp / 一个引擎空态 test）
1. **移除全部自动 fallback**（6 处）：无真实设备（本地 RTL-SDR/rtl_tcp）且未显式选测试源、未加载离线文件时，`source_` 为空/不产数据的 NullSource，引擎**不产合成 IQ**；设备打开失败/丢失 → 诚实空态（不 fallback 合成）。
2. **显式测试源**：
   - API `setTestSourceEnabled(bool)` / `isTestSourceEnabled()`、合成时状态标 `isSynthetic=true`（显著可查）；
   - main.cpp 解析命令行 `--test-source` 与环境变量 `MBDSDR_TEST_SOURCE=1`（任一命中显式启用）；默认生产路径绝不自动合成。
3. **引擎状态查询**：hasRealSource()/isSynthetic()/hasData()（供 UI 与 ControlHub 判定空态/禁用）。
4. **引擎层 ctest**：默认无设备=空态（不产数据、hasData=false）；显式 API/命令行/环境变量启用才出合成数据且 isSynthetic=true。

## Step 2（Step1 冻结 API 后并行，两个 agent，文件域互斥）
- **UI agent（main_window + UI test）**：
  - srcTypeCombo 加「测试信号（离线调试·合成，非真实接收）」项，用户主动选中才合成、显著标「合成/调试」；
  - banner 改「RTL-SDR 未连接」（删「使用测试信号」）、statusLabel 不显示「(test)」、recordBtn 未连接且未开测试源时禁用；解调/录音/解码/扫描等依赖控件无数据时禁用；
  - 所有数据面板（频谱/瀑布/解调/POCSAG/m17/VOR/ADS-B/录制/扫描）无真实数据且未开测试源时统一诚实空态；
  - 首跑轻引导卡片（无 QSettings 时）：「①连接 RTL-SDR ②调谐频率 ③选解调模式」，可关闭、QSettings 永久记忆、空态给「去连接」入口；
  - UI ctest：默认空态/显式测试源两态、引导卡片。
- **测试迁移 agent（tests/ 批量）**：把 20+ 依赖默认 fallback 的离线 ctest 一律改为**显式启用测试源**（启动引擎处 setTestSourceEnabled(true) 或设环境变量），不再依赖默认 fallback；确保全量不回归。

## 质量门 / 红线
- ctest 112 基线不破 + 新增/调整全绿（offscreen，全量构建全部目标）；干净室 MIT；通用非专用；测试源是显式开发工具非默认路径（合成仅测试/调试且标注）；不硬编码/不预存；无硬件=诚实空态。
- 只暂存相关文件（禁 add -A）；**不自行 push**。如实报告 file:line、ctest 实跑数（全量输出）、未解决项（MainAgent 全量构建+跑 ctest+查 git 复核）。
