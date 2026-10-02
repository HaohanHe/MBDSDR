# SDR++ 差距清单（Phase 12 整合稿）

> 来源：六份精读笔记（docs/learn/phase12/sdrpp-{waterfall,device,demod,dsp,ui,plugin}.md，均为真读源码产出，file:line 可查）。SDR++ = repos/sdrpp（GPLv3，只学机制）。MBDSDR 基线：cpp 95 / flutter 305 / python 225+7。
> 判定口径：已实现且真实 / 已实现但缺深度 / 未实现。

## 1. 总表（按落地价值排序）

| # | 子系统 | 差距 | 判定 | 上游 file:line | MBDSDR 现状 | 落地价值 | 云内确定性验证 |
|---|---|---|---|---|---|---|---|
| L1 | 解调链/信号处理 | **信道化有理比重采样**（幂二预抽 + GCD 有理残差）：2.048M→48k 现取整 round 得 47.62k，音高/RDS 57k 漂移 | ✅ 已落地（2026-10-02） | `rational_resampler.h:120-165`、`decim/plans.h:36-140` | `channelizer.cpp:55-86`（整除走原整数抽取；非整除走幂二预抽×32 + GCD 残差 interp=3/decim=4），新 `rational_resampler.h` 多相 | **高** | ✅ `ctest rational_resampler`（1s 样本≈47977/48000、1kHz→1000.01Hz、整数路径回退、确定性复算） |
| L2 | UI/交互 | **滚轮级联倍率**（Shift×10 / Alt×0.1）+ **滚轮平移视图**（刻度带 wheel→pan）+ **调谐后画布跟随** | ✅ 已落地（2026-10-02，干净室） | `main_window.cpp:553-610`、`tuner.cpp:22-109` | 纯函数 `spectrum_tune.h:113-207`：`WheelTier`+`wheelStepFreq`(:139) 倍率级联（Shift×10/Alt×0.1，snap 到有效步长网格）、`FftBand`+`wheelPanView`(:160) 刻度带平移/捕获带边界夹持、`ViewWindow`+`followCenterAfterTune`(:188) 出视口 10% 边距跟随（带内不动/出视口平移/出捕获带钳边）；接线 `spectrum_display.cpp:1080-1106`（wheelEvent 区域分流：Ctrl 缩放 / 刻度带 pan / 迹线调谐+跟随）、`:454 followTunedFrequency`、`main_window.cpp:2488`（键盘 nudge 后画布跟随） | 高 | ✅ `test_spectrum_tune`：`wheelCascadeModifierMapping`（无键/Shift/Alt）、`wheelStripPanClampsToBand`（边界夹持）、`tuneFollowThreeStates`（带内/出视口/出捕获带）；套件 12/12 过 |
| L3 | 设备后端 | **tune 重试+回读**（上游最多 10 次 + get_center_freq 校验） | ✅ 已落地（2026-10-02，重试上限取 5 次，干净室重写非照抄） | `source_modules/rtl_sdr_source/src/main.cpp:344-359` | `rtl_sdr_source.cpp` 原单次写无回读 → 经 `RtlLibOps` 缝重试+回读，耗尽即响亮告警、请求保留不掩盖 | 高 | ✅（fake rtlsdr 注入；`test_rtl_sdr_tune`：重试收敛/耗尽诚实） |
| L4 | 设备后端 | **set_tuner_bandwidth 显式调用**（start 时 (dev,0)=自动） | ✅ 已落地（2026-10-02） | `main.cpp:310` | 原完全不调用 → start 显式 `set_tuner_bandwidth(dev,0)` | 中（一行） | ✅（fake 记录调用序列：恰好 1 次、参数=0） |
| L5 | 插件架构 | **C++ 工具注册表重构**（tool_schema 单一事实源，消除 agent_tools if-else 漂移与 writeTools 双写） | **已落地**（2026-10-02）：声明式 spec 表承载 write 标志（`ToolSchemaSpec::write`），agent_tools 改为按名查 dispatch 表分发；新增 `executorToolNames()` 可 introspect，`test_tool_registry` 双向断言 schema↔executor 一致 | `module.h:33-41`（注册表模式，干净室只学机制） | `tool_schema.cpp:56-` 单表 + `agent_tools.cpp` dispatchTable()；`tests/test_tool_registry.cpp` | 中-高 | ✅（test_tool_registry：完整性/Flutter 读写 parity/未知工具诚实路径） |
| L6 | 渲染/瀑布 | **doZoom 峰保持降采样**（块内取 max，防缩水时漏峰） | 已实现但缺深度 | `waterfall.cpp` doZoom | 频谱引擎降采样 | 高 | 部分 |
| L7 | 渲染/瀑布 | **改色板/拖量程整段重染历史**（保留 raw dB 行，避免丢历史） | 已实现但缺深度 | `waterfall.cpp` | 环形行缓冲（改色板需重染） | 高 | ✅ |
| L8 | 渲染/瀑布 | **外部 JSON 色板文件**（用户可扩展） | 未实现 | `colormaps.cpp` maps 扫描 | tokens 编译期 256 LUT | 中 | ✅ |
| L9 | 解调链 | **解调链内 AGC**（载波/音频双 AGC） | 已实现但缺深度 | `am.h:34-35,103-106` | `demod.cpp:46-59` abs+DC 阻断，AGC 挂音频后端 | 中 | ✅ |
| L10 | 解调链 | **NFM 鉴频后带宽匹配 FIR**（cutoff=bw/2, trans=0.1·bw） | 已实现但缺深度 | `fm.h:121` | `demod.cpp:65-66` 仅单极点去加重 | 中 | ✅ |
| L11 | 信号处理 | **VOLK NCO 频移**（相位跨块连续） | 已实现但缺深度（性能） | `frequency_xlator.h:43-50` | 手写 cos/sin | 中（纯性能） | ✅ |
| L12 | 信号处理 | **RxVFO 带宽短路+参数化过渡带**（带宽≠率才低通） | 已实现但缺深度 | `rx_vfo.h:89-117` | 截止写死 0.85 奈奎斯特 | 中 | ✅ |
| L13 | 解调链 | **GFSK/FSK 数字解调链**（鉴频→浮点 RRC→MM） | 未实现 | `gfsk.h:31-34,131-135` | `digital_demod.h` 仅 BPSK/QPSK | 中（新特性） | ✅ |
| L14 | 设备后端 | **read_async 回调直写双缓冲零拷贝** | 已实现但缺深度（性能） | `main.cpp:526-539` | `read_sync` 每帧 new vector+拷贝 | 中（先 benchmark） | 🟡 |
| L15 | 渲染/瀑布 | GL 纹理上传路径（vs Qt raster） | 架构取向 | waterfall GL 段 | Qt QImage | 中 | 否 |
| L16 | 设备后端 | **配置按 dongle 分桶**（devices.[VID PID Serial]） | 未实现（单 dongle 无感） | `main.cpp:200-255` | 全局设置 | 低 | 🟡 |
| L17 | 解调链 | WFM 立体声群延时对齐重写 | 等价（架构不同） | `broadcast_fm.h:47-48` | `wfm_stereo.h:12-22` 同 FIR 双路 | 不落地 | — |

## 2. 反向提示（MBDSDR 已领先、不照抄）
- 瀑布环形 O(1) 滚动优于上游整帧 memmove；Flattop 窗；256 项 LUT 对 8-bit 屏足够；快捷键目录化（shortcuts_catalog.h）反超上游散硬编码；VFO 高亮视觉体系超上游；Tune/Pan/Grab 命中分层齐备。

## 3. 用户视角五项核对（研习E 结论）
点频谱调谐 ✅ 真实（click<3px 跳频）；滚轮 snap ✅ 有网格、🟡 缺 Shift×10/Alt×0.1 倍率（→L2）；瀑布滚动 ✅ 真实（自动下滚+刻度带拖平移+双击居中）；VFO 高亮 ✅ 超上游；带宽拖拽 ✅ 真实（边命中钳 100Hz–500kHz）。

## 4. Wave2 落地候选（本轮派发 4 项）
**落地1（C++ DSP）**：L1 信道化有理比重采样（幂二预抽+GCD 有理残差，修 47.62k 漂移；对应研习D-G1 合并）。✅ 已完成 2026-10-02：新 `rational_resampler.h`（GCD 化简 + 多相窗函数插值/抽取，commutator `phase+=M; offset+=phase/L; phase%=L`），`channelizer.cpp:55-86` 整除走原整数抽取、非整除走幂二预抽×32 + 残差 interp=3/decim=4；`ctest rational_resampler`（1s≈47977 样本、1kHz→1000.01Hz、整数回退、确定性复算）。
**落地2（C++ UI）**：L2 滚轮级联倍率+滚轮平移+调谐跟随（spectrum_tune.h 纯函数+接线；用户视角收尾）。✅ 已完成 2026-10-02：纯函数 `spectrum_tune.h:113-207`（`wheelStepFreq` Shift×10/Alt×0.1 级联、`wheelPanView` 刻度带平移+捕获带边界夹持、`followCenterAfterTune` 出视口 10% 边距跟随），接线 `spectrum_display.cpp:1080-1106`（wheelEvent 区域分流）+ `main_window.cpp:2488`（键盘 nudge 跟随）；`test_spectrum_tune` 三新用例（倍率映射/边界夹持/跟随三态），套件 12/12 过，spectrum_interaction/display/autorange/shortcuts 回归全绿。干净室只学机制，未 commit/push。
**落地3（C++ 设备）**：L3+L4 tune 重试回读 + set_tuner_bandwidth（fake 驱动单测）。✅ 已完成 2026-10-02：`rtl_sdr_ops.h` 注入缝 + `test_rtl_sdr_tune`（重试收敛/耗尽诚实/带宽=0/打开失败诚实/stub 回归），ctest 全绿。注：fake rtlsdr 为测试替身（内存函数指针+调用日志），非硬件 mock，不碰 USB/RTL2832。
**落地4（C++ AI）**：L5 工具注册表重构（tool_schema 单一事实源+一致性单测）。

暂缓 backlog：L6/L7/L8（渲染深度，下批）、L9/L10/L13（解调增强）、L11/L12（DSP 性能/参数化）、L14（先 benchmark）、L16。
