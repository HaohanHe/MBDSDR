# 第四阶段实现规格：真实实验 + 真机闭环 + 移动原生

> **交付状态（Phase6 复核，2026-10-02）：已交付。** B1 真机自检 `tools/hw_selfcheck`（selfcheck.py + 离线测试）、
> B2 可复现实验管线（`experiments/common/` + 脚本）、B3 移动原生审计、B4 缺口学习笔记均落盘；
> Wave2 落地 C++ 断流看门狗 / 设备列表 diff / 增益吸附离散档（ctest 基线见 phase5/6 规格）。本文件保留为历史规划快照。

> 基线：远端 main = d331681（本地已同步）。前三阶段已交付：模型文档精读（16 篇+总稿）、真 function calling 循环（408e739）、工具化做全做实（d331681：C++ 8 工具 / Flutter 6 工具、渲染对齐、UI #919cac 收敛、诚实披露）。
>
> 本规格冻结范围：Wave1 四个审计/自检（B1–B4）→ Wave2 实现批次（实验管线 / 移动原生 / 学习缺口）→ 独立验证 → 增量推送。

## 0. 环境事实（2026-10-01 云 VM 自检结论）
- **无任何硬件透传**：无 lsusb、无 /dev/dvb、无 /dev/ttyUSB*、无 /dev/serial、无 /dev/snd、无 gpsd。
- rtl-sdr 命令行工具已装（~/.local/bin/rtl_test|rtl_sdr|rtl_eeprom）；**pyrtlsdr、SoapySDR Python 绑定未装**；无声卡。
- 真机（RTL-SDR + GNSS + 天线）在用户本地机器：云侧交付**可在真机一键运行的自检工具 + 明确报告/待办**，真机执行结果由用户回传。
- repos/ 已克隆 50+ 上游：sdrpp、gnuradio、SatDump、sdrangel、librtlsdr、SoapySDR、gr-osmosdr、multimon-ng、dump1090、RTKLIB 等——学习以这些真实源码为准。
- experiments/ 已有 9 个 exp_*.py（AMR/AX.25/数字模式/FHSS/频偏/PNT 融合/频谱感知/SSTV/弱模型工具调用），**全部为合成信号口径（脚本内已诚实标注"纯软件、可复现"）**，输出目录 paper/experiments/。
- 移动：Dart 音频契约（channel `mbdsdr/audio`，start/write/setVolume/setMuted/dispose）+ PCM 量化 + 单测就绪；**Kotlin/Swift 原生端未落地**（云无法编译原生）；录音模型 RecordingMeta 是诚实空态（无文件录制）；pubspec 无 USB-serial/音频文件包；geolocator（手机内置 GNSS）已在依赖。
- mbdsdr_ai 已有 serial_gnss.py（NMEAParser/SerialGNSSReader，GGA/RMC/GSA/GSV/VTG/ZDA、多星座 talker）与 gnss_monitor.py——Dart NMEA 移植以此为蓝本（自有 MIT 代码）。

## 1. Wave1（审计/自检，只读为主）

| Agent | 范围 | 交付 |
|---|---|---|
| **B1 真机自检（直接交付工具）** | 写一个真机一键自检脚本（Python，stdlib 优先；放 `tools/hw_selfcheck/`）：①USB/udev（lsusb 找 RTL2838/0bda:2838、dialout/plugdev 组、udev 规则）；②rtl_test -t 设备枚举与 tuner 型号；③短时 rtl_sdr 读流统计丢包（read samples / dropped）；④声卡枚举（arecord -l/aplay -l）；⑤GNSS 串口枚举（/dev/ttyUSB*、波特率、NMEA 首行）；⑥依赖检查（pyrtlsdr/SoapySDR/gpsd）。每项输出 PASS/WARN/FAIL + 修复建议；无硬件时整体诚实报告"未检测到设备"+待办清单。云 VM 内先跑一遍产出**云侧自检报告**（docs/learn/phase4/B1-cloud-selfcheck.md）。脚本确定性、零破坏（只读设备、rtl_sdr 读 ≤3s）。 | 脚本 + 云侧报告 + 真机待办 |
| **B2 实验管线审计（只读）** | 通读 experiments/ 全部脚本、paper/ 现有产出、mbdsdr_ai 解码/AMR/DSP 模块；产出 docs/learn/phase4/audits/B2-experiment-pipeline.md：①逐脚本现状表（被测对象/数据源=合成 or 录制/输出/口径标注/样本数）；②缺口：解码成功率 vs **Eb/N0**（现有是 SNR？映射关系）、录制 IQ 摄取（file_source/WAV）、**基线对比（经典 vs AI/Agent）**、**多普勒定轨收敛**实验、数据图自动生成（图题/轴/口径/样本数/置信区间）；③管线目录设计与文件所有权；④哪些可在云内用合成跑通（明确标"仿真"）、哪些必须等用户录制（诚实空态）。 | 审计报告 + 管线设计 |
| **B3 移动原生审计（只读）** | 通读 mobile/lib/audio/*、models/recording.dart、pages/recordings_page.dart、android MainActivity.kt、pubspec；对照 mbdsdr_ai/serial_gnss.py；产出 docs/learn/phase4/audits/B3-mobile-native.md：①录音落盘方案（纯 Dart WAV writer + sidecar JSON，云可测；格式/采样率/字节数校验）；②回放方案（复用 mbdsdr/audio 通道，原生 Kotlin AudioTrack/Swift AVAudioEngine 代码清单——云写不编译、诚实标注）；③外部 GNSS：Dart NMEA 解析器（移植 serial_gnss，逐语句+校验和+定位/授时字段）+ USB-serial 平台通道原生代码清单（uncompiled 标注）；④手机内置 GNSS（geolocator）与外部 GNSS 的显示分工；⑤测试清单（Dart 全离线确定性）。 | 审计报告 + 原生契约 |
| **B4 学习缺口（只读+笔记）** | 对照 repos/sdrpp、gnuradio、SatDump、librtlsdr 真实源码，审 cpp/src/dsp 与 mobile 对应层；产出 docs/learn/phase4/audits/B4-gap-learning.md（学习笔记体）：①实时声卡播放（桌面 qt_audio_sink 是否已接线、SDR++ 的 sink 源管理）；②多 VFO 实战（vfo_manager 现状 vs SDR++ VFO Mgr）；③ANR（anr.cpp 现状 vs gr-noise/blanker）；④设备热插拔（libusb hotplug vs 当前无）；⑤分段增益（librtlsdr 逐 tuner gain 表/setTunerGain 现状）；每项给"上游怎么做（file:line）→ 我们缺什么 → 最小真实实现"，不铺空壳。 | 学习/缺口报告 |

## 2. Wave2（实现，按 Wave1 报告分派；预计 4–5 个 agent）
- **W2-exp 论文管线**：按 B2 设计落地——Eb/N0↔SNR 换算与解码成功率实验（数字模式：FSK/PSK/CW 等，先仿真口径跑通并明确标"仿真"）、录制 IQ 摄取器（吃 file_source/WAV，无录制时诚实空态、绝不合成冒充 OTA）、基线对比（经典解调 vs AI/Agent 路径同一数据集）、多普勒定轨收敛实验（固定 TLE 的确定性合成轨道可作"仿真验证"，OTA 待录制）、数据图自动生成（PNG + 图题含口径/样本数/日期，落 paper/experiments/）；全部脚本可复现（固定种子、CLI、README）。
- **W2-mobile 原生补齐**：按 B3——Dart WAV writer（dart:io，云可测）+ sidecar JSON + RecordingStore 真实读写；recordings 页真实列表/回放接线；Dart NMEA 解析器（全语句、校验和动态计算）；原生 Kotlin/Swift 代码（AudioTrack/AVAudioEngine 回放 + USB-serial 读 NMEA）写进 android/ios 并**逐处标注"云未编译、真机待验"**；Dart 单测做全。
- **W2-gap 真实缺口**：按 B4 挑可在云内确定性验证的项落地（热插拔轮询/分段增益表/多 VFO/声卡接线/ANR 改进），每项带离线测试；不做无法验证的空壳。
- 各 agent 严格文件所有权；CMake/pubspec 改动遵循追加模式。

## 3. 验收（每批必过）
- cpp：基线 **ctest 84/84 不破坏** + 新增测试全绿；全量构建 0 错误。
- Flutter：**246 基线不破坏** + 新增全绿；analyze 0 issue。
- Python：实验脚本在云内跑通（仿真口径），pytest 受影响集全绿。
- 数据图：每张图可由脚本重新生成，图题/文件名含口径（仿真/录制/OTA）与样本数。
- git：`git status/log` 核对，**只暂存本批相关文件**（禁 add -A），排除 build/scratch/figma/key/record 原始数据（大文件/敏感另议）；文档与代码**不得出现"比赛/competition"**。
- 推送：云环境无有效凭据（GITHUB_TOKEN 被拒、gh 未登录、无 SSH key）——本地建好提交，推送待用户凭据/外部执行（与前几阶段一致）。

## 4. 红线（四阶段一贯）
先学后做、真读代码（不只看 README）；禁假数据/假执行/假成功/静默 mock；无硬件与无录制一律诚实空态 + 明确待办；MIT 干净室（不逐字复制 GPL）；无魔法数（tokens.h/AppTokens）；设计气质 Apple/小米车机（克制、4pt、#919cac、弹性布局）；分批推进、每批独立验证后再推下一批。
