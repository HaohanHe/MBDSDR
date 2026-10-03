# 第十六阶段实现规格：深研 SDR++ 平台层（无头/远程控制 + 统一解码/Source 抽象）

> 基线：HEAD = 6c43861（Phase15 已验收）。ctest 基线 **104/104**。
>
> 目标：前 15 阶段只学透核心 DSP + 频谱瀑布 + RTL-SDR + 基础解调，本轮深研「平台骨架」。真读源码原文（禁只看 README/目录/abstract），产出 file:line 可查笔记；关键架构干净室落地。GPLv3 只学机制、源码不入库。
>
> 勘察事实：SDR++（repos/sdrpp）core/src/signal_path/{source,sink,signal_path,vfo_manager,iq_frontend}.{h,cpp}；core/src/{server.cpp/h,server_protocol.h,backend.h,module_com.cpp/h,core.cpp,config.cpp}；decoder_modules/{radio,atv_decoder,dab_decoder,m17_decoder,meteor_demodulator,pager_decoder,vor_receiver,weather_sat_decoder,ryfi_decoder,falcon9_decoder,kg_sstv_decoder}；source_modules/{rtl_sdr_source,airspy_source,airspyhf_source,file_source,...28 个}；misc_modules/{scheduler,scanner,recorder,frequency_manager,rigctl_client,rigctl_server,iq_exporter}。MBDSDR 现有 cpp/src/ai/{agent_tools,agent,tool_schema}（C++ 工具注册表已落地）、dsp/frequency_scanner、spectrum_engine 引擎。

## 1. Wave1：四个并行精读批次（笔记落 docs/learn/phase16/）

| 批次 | 精读范围 | 笔记 |
|---|---|---|
| **A 信号路径与生命周期** | core/src/signal_path/{signal_path,source,sink,vfo_manager,iq_frontend}.{h,cpp}：源→VFO→解调→sink 数据流与**线程模型**（谁在哪个线程、阻塞/双缓冲、启停时序）；core.cpp 如何编排模块加载/初始化/销毁生命周期。 | `sdrpp-signalpath.md` |
| **B 无头/远程服务（最高优先）** | core/src/{server.cpp/h,server_protocol.h,backend.h,module_com.cpp/h}：SDR++ 作为后台服务被远程驱动的**命令集**（调谐/模式/带宽/增益/录音/扫描/VFO 等，命令名/参数/返回/读写语义，file:line）；模块间通信协议；服务端监听/会话/协议编解码。 | `sdrpp-server.md` |
| **C radio 统一解码框架** | decoder_modules/radio（多模式 NFM/WFM/AM/SSB + RDS + 数字如何在一个流式模块统一注册/切换、音频链组织、菜单/参数）。 | `sdrpp-radio.md` |
| **D Source 统一抽象** | source_modules 统一 Source 接口 + rtl_sdr_source 对照 ≥2 个其他源（airspy/file/airspyhf）：增益模型、采样率协商、流控、错误恢复、设备能力上报。 | `sdrpp-source.md` |

每笔记四要素：①真读 file:line + 注释性短片段（GPL 注明）；②机制总结；③MBDSDR 现状对照（读真实代码，file:line）；④差距判定（已实现且真实/缺深度/未实现 + 通用价值 + 云内可验证方案）。

## 2. Wave2：无头控制层干净室落地（P1 核心，据 A/B 笔记）
- 设计并落地一套**与 GUI 解耦的 MBDSDR 无头控制接口**：命令 → 真实 spectrum_engine（不经过任何 QWidget），**读写分离、写动作可 gate**（复用 ai 现有 read/write 分类思想）；覆盖调谐/模式/带宽/增益/静噪/录音/扫描/VFO/状态回读。
- ctest：命令确定性执行、状态可回读、未知命令诚实报错、写门 gate；合成信号仅测试夹具。
- 只落地通用、可确定性验证的部分；物理/真机相关诚实标注。

## 3. P3（时间够再做，Wave3）
- misc_modules/scheduler（定时/过境自动任务）；decoder 通用价值高的 m17/pager(POCSAG/FLEX)/vor/dab 流式框架精读（笔记 + 判定，不强落地）。

## 4. 质量门
- ctest **104 基线不破** + 新增全绿（offscreen）；干净室 MIT（不复制 GPL、命名/结构自有）；新阈值/命令/率走 core/tokens.h 具名常量 + 弹性派生；通用平台能力（非专用）；无硬件诚实空态（禁假执行/假设备/假完成）。
- git：只暂存相关文件（禁 add -A）；**不自行 push**（MainAgent 验收统一推）。

## 5. 红线
先学后做、真读源码；如实报告精读文件/笔记路径/落地 file:line/ctest 实跑数/未学透清单（MainAgent 独立 find/跑测试/查 git 复核）。
