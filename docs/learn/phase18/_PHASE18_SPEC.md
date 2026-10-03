# 第十八阶段实现规格：把 POCSAG / m17 / VOR 接入生产链路

> 基线：HEAD = 39cce45，ctest **110/110**。Phase17 已交付三个 decoder（dsp/{pocsag_decoder,m17_decoder,vor_receiver}），但未接生产链路（孤儿）。
>
> 勘察事实：VfoManager（vfo_manager.h:132）持有 `std::vector<VfoChannel>`；VfoChannel（:64）每信道有 demod/digitalDemod/rds/stereo、audio48k 输出；`rebuild(sourceSr,sourceCenterHz)` 按 mode 字符串 if/else 构建解调（vfo_manager.cpp:85-96）；`process(iq,...)` 返回音频。引擎 include cw/adsb/apt decoder。所有 VfoManager 方法在引擎 run() 线程、同一 mutex 下调用。UI 面板范式 radio_panel/weather_panel。

## Wave1（地基，单个 agent；独占 vfo_manager / spectrum_engine / 模式注册 / 一个集成 test）

### 1. 模式注册（通用、不写台名）
- 把 `POCSAG`、`m17`、`VOR` 加入模式清单/注册表（与 NFM/WFM/AM… 并列；模式清单实际定义位置由 agent 定位，UI 下拉与 ControlHub 模式校验共用同一来源）。

### 2. 解调链衔接（VfoChannel）
- **POCSAG/m17**：VfoChannel 增加 `FskDemod`（复用 dsp/fsk_demod）；按模式配 baud/频偏/带宽（POCSAG：1200 baud 默认、±4.5k；m17：4800 sym/s、4FSK——协议固定值走 decoder 头/tokens 具名常量）；process 中 IQ→fskDemod.process→drain bits→feed 给 pocsag/m17 decoder。
  - 注：fsk_demod 是 2-FSK 二进制 sign 判决；m17 是 4FSK——若 fsk_demod 不能直接出 dibit，则 m17 decoder 已自带 4FSK 前端（m17_decoder feedIQ/feed），优先把 channelizer 后的 IQ 直接喂 m17 decoder 自带前端；POCSAG 走 fsk_demod bits。以最小改动、不重写为准。
- **VOR**：复用 AM demod（配 VOR 公开航空频段 108–118MHz、相应带宽/采样率）→ 把解调音频 feed 给 vor_receiver。
- 模式切换 rebuild 时：正确创建对应链、清空旧 decoder 输出；参数派生复用现有 rebuild 机制，不硬编码。

### 3. 只读快照 + 重置（冻结下游接口）
VfoManager（及 SpectrumEngine 转发）提供，按当前/指定 channel：
- POCSAG：`std::vector<PocsagMessage>`（地址/func/文本）
- m17：`std::vector<M17Call>`（src/dst/type/meta/payload/crcOk/voiceUndecoded）
- VOR：`VorResult`（radial/识别码/locked/quality）
- 写：`clearDigitalOutputs(channel)`（重置三类输出）
- 引擎在新解码产出时 emit 变化信号（如 pocsagMessagesChanged / m17CallsChanged / vorRadialChanged），供 UI 订阅；无数据时快照为空、VOR locked=false（诚实空态）。

### 4. 集成 ctest（agent 自行 CMake 注册该 test）
- 合成 IQ/帧走「模式注册→channelizer→解调→decoder→引擎快照」完整闭环：POCSAG 已知地址+文本、m17 已知呼号、VOR 已知角度，断言快照正确；模式切换清空；纯噪声各快照空态（不造假、不预存）。

## Wave2（地基冻结实际接口后并行，三个 agent，文件域互斥）
- **ControlHub**：新增只读 get_pocsag_messages / get_m17_calls / get_vor_radial（真实快照、空态）；clear/reset 写命令纳入写门。
- **Agent 工具**：注册对应只读工具 + tool_schema；**同步更新 test_ai_real_link 工具完整性清单**（勿再漏）。
- **UI 面板**：POCSAG 消息列表（可清空）、m17 呼叫（语音帧诚实标未解码）、VOR 径向仪表+Morse+锁定态；弹性布局/tokens::scaled、窄窗不溢出、空态、不预存；主窗口挂接。
- 各自 ctest：ControlHub 新命令、Agent 契约、UI 集成（合成帧→面板显示）。

## 质量门 / 红线
- ctest 110 基线不破 + 新增全绿（offscreen）；干净室 MIT；通用非专用；不硬编码/不预存；合成仅测试；只暂存相关文件（禁 add -A）；**不自行 push**。
- 如实报告 file:line、ctest 实跑数、未解决项（MainAgent 全量构建+跑 ctest+查 git 复核）。
