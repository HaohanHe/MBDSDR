# 开源软件使用说明 / Notices

本产品（MBDSDR）在开发中参考并移植了以下开源项目的代码与设计。
本文件仅用于署名与合规说明，不改变根目录 `LICENSE` 中 MBDSDR 自有代码的 MIT 许可。

## SDR++

- 版权所有：Copyright (C) Alexandre Rouma / Ryzerth
- 项目主页：https://github.com/AlexandreRouma/SDRPlusPlus
- 许可证：GNU General Public License v3.0 或任何更高版本（GPL-3.0-or-later）

本项目的统一频谱/瀑布显示画布 `src/ui/spectrum_display.{h,cpp}`（SPDX:
`GPL-3.0-or-later`）移植并借鉴了 SDR++ 中 `ImGui::WaterFall` widget 的几何布局与绘制思路，
用 Qt / QPainter 重新实现。具体移植/参考的部分包括：

- **统一几何模型**：线频谱、共享频率刻度条、滚动瀑布三者共享同一左边界与宽度，
  频率→x 映射完全一致，从构造上保证频率轴对齐（对应 SDR++ `fftAreaMin/fftAreaMax`、
  `freqAreaMin/freqAreaMax`、`wfMin/wfMax` 的布局关系）。
- **可拖拽分隔条与高度钳制**：在频谱区与瀑布区之间拖动改变比例，并把频谱高度钳制在
  一个合理区间、保证瀑布始终有最小可见高度（对应 SDR++ FFT resize bar 的 clamp 行为）。
- **瀑布历史与调色板**：滚动历史缓冲、dB→颜色查找表（LUT）、按滚动速度抽帧、
  以及可视频率窗口到历史图列的裁剪映射，均沿用自原 `WaterfallWidget`（其源头即
  SDR++ 瀑布显示的数据流思路）。
- **频谱线下的柔和填底（shadow）**：参考 SDR++ `drawFFT` 中线到基线的垂直线填充做法。

### 许可说明

- `src/ui/spectrum_display.{h,cpp}` 以 **GPL-3.0-or-later** 分发（与被移植代码一致）。
- MBDSDR 的其余自有文件仍为 **MIT** 许可（见根目录 `LICENSE`）。
- 由于组合了 GPL 代码，本作品作为整体应以 GPL-3.0-or-later 分发。
- 我们未随产品分发 SDR++ 的源代码二进制原件；上述为借鉴几何与数据流后用 Qt 重写的实现。

## 多 VFO 管理器（设计参考）

- 版权所有：Copyright (C) Alexandre Rouma / Ryzerth（SDR++）
- 许可证：GNU General Public License v3.0 or later（GPL-3.0-or-later）

`src/dsp/vfo_manager.{h,cpp}` 是 MBDSDR 原创实现（SPDX: MIT），但在架构上参考了
SDR++ 的多 VFO 模型：源宽带 IQ 扇出到 N 个独立接收信道，每个 VFO 持有自己的
channelizer / 解调器 / 重采样器与跨块状态，VFO 频率以相对源中心的 NCO 偏移表示，
GUI 上在瀑布/频谱为每个 VFO 绘制半透明带宽框。对应的 SDR++ 参考文件（只读、未修改）：
`repos/sdrpp/core/src/signal_path/vfo_manager.{h,cpp}`、
`repos/sdrpp/core/src/dsp/channel/rx_vfo.h`。本项目未逐行移植其 C++ 代码，
而是按该模型用 Qt6 / 自有 DSP 块重新实现；共享下游（静噪 / AGC / 音频输出 / 门录）
仅作用于选中 VFO 的音频，以保持单 VFO 路径与旧单信道接收机一致。

## GNSS 接入 / SGP4 轨道 / 离线地图 / 天空图（本批新增模块）

本批新增的子系统均为 MBDSDR 原创实现（SPDX: MIT），**未引用任何 GPL 代码**：

- `src/gnss/*`（NMEA 0183 解析、串口/文件/内存传输、接收线程）：原创。
- `src/dsp/sgp4.{h,cpp}`：近地轨道 SGP4  propagator 为原创实现，依据公版
  **Spacetrack Report #3**（Hoots & Roeber）及其公开修订
  Vallado, Crawford, Hujsak & Kelso, *Revisiting Spacetrack Report #3*,
  AIAA 2006-6753。验证用参考星历取自 CelesTrak 公开发布的 `tforver.out`：
  https://celestrak.org/publications/AIAA/2006-6753/ 。
  本仓库未复制其源码，仅按公开算法公式重新实现并对照公开星历验证精度。
- `src/ui/coastline_data.h`：离线世界海岸线矢量数据来自 **Natural Earth**
  （https://www.naturalearthdata.com/ ）110m 陆地/海岸线，属 **公有领域（Public Domain）**，
  以数组形式内嵌，无需联网、无 API key。
- `src/ui/map_projection.{h,cpp}`：等距圆柱（plate carrée / equirectangular）投影，
  属无版权的通用数学投影，未使用任何第三方投影库。

> 说明：以上新增模块本身不含 GPL 代码。历史模块 `src/ui/spectrum_display.*`
> （见上方 SDR++ 章节）仍以 GPL-3.0-or-later 分发，与本节无关；本文件不改变根目录
> `LICENSE` 中 MBDSDR 自有代码的 MIT 许可声明。

## FM RDS 数据链路解码器（本批新增模块）

- 标准依据：ETSI EN 300 401 *Radio Data System (RDS)*。
- 版权参考（仅算法与常数参考，**未复制其代码**）：
  - `repos/gqrx/`（GPLv3+）：`src/dsp/rds/constants.h`（offset word / syndrome 常数表）、
    `decoder_impl.cc`（块同步与 CRC 状态机）、`parser_impl.cc`（Block B 字段位段布局）。
  - `repos/redsea/`（ISC）：`src/dsp/subcarrier.cc`（57 kHz 混频、低通、biphase/NRZ-I
    符号恢复的数据流思路）。
- 实现文件 `src/dsp/rds_decoder.{h,cpp}` 为 MBDSDR 原创实现（SPDX: MIT），
  按 EN 300 401 标准干净重写：57 kHz 数字混频 + 低通、2375 sym/s biphase 差分译码、
  26bit 块滑动同步（生成多项式 G(x)=x^10+x^8+x^7+x^5+x^4+x^3+1，poly 0x5B9）、
  0A 节目名（PS）/ PTY / 2A 无线电文本（RT）解析。未逐行移植 gqrx 或 redsea 的
  C++ 源码；常数（offset word {252,408,360,436,848}、对应 syndrome {383,14,303,663,748}）
  与位段位置均对照上述只读参考文件核对，并以 `tests/test_rds.cpp` 内的参考编码器做
  端到端已知向量钉死。
- 本轮未实现（代码内留 TODO）：type 4 时钟（CT）、type 8 TMC、0B/2B 组版本回退。

## 1090 MHz Mode S / ADS-B 解码器（本批新增模块）

- 标准依据：ICAO Annex 10 Vol. IV（1090 ES / Mode S 链路）、DF17 扩展 Squitter 格式。
- 版权参考（仅算法、常数与位段布局参考，**未复制其源码**）：
  - 本地只读参考 `repos/dump1090/`（FlightAware / mutability 版 dump1090，
    GPL-2.0-or-later；源自 Salvatore Sanfilippo / antirez 的 dump1090，后经
    Malcolm Robb、Oliver Jowett 等持续维护）。对照参考文件：
    `crc.c`（24 位校验多项式与 CRC 奇偶表）、`cpr.c`（CPR 全局/本地位置解码与
    NL 经度带断点表）、`mode_s.c`（DF17 下行格式位段拆解、呼号 6-bit 字符表）、
    `demod_2400.c`（8 µs 前导检测与 PPM 比特判决思路）、`ais_charset.c`
    （Mode S 呼号字符集映射）。
  - Junzi Sun, *The 1090 Megahertz Riddle*（https://mode-s.org ）：CPR 坐标与
    DF 字段语义说明的教学参考。
- 实现文件 `src/dsp/adsb_decoder.{h,cpp}` 与 `src/ui/aircraft_tracker.{h,cpp}` 为
  MBDSDR 原创实现（SPDX: MIT），按上述标准干净重写：速率自适应前导匹配 + PPM
  采样判决、CRC-24 校验（多项式 0xFFF409）、DF17 位置（CPR 全局/本地）、呼号与
  速度消息拆解；`aircraft_tracker` 按 ICAO 合并帧、按字段诚实覆盖（缺失字段不被
  假默认覆盖）、位置尾迹定长截断、TTL 过期清理，并只对真实定位飞机输出地图点。
  未逐行移植 dump1090 的 C 源码，也未随产品分发其源代码或二进制原件；CRC 多项式、
  NL 断点表、CPR 公式、呼号 6-bit 布局与前导/PPM 时序仅作公开算法对照，端到端
  已知向量由 `tests/test_adsb_cpr.cpp`、`tests/test_adsb_decode.cpp`、
  `tests/test_aircraft_tracker.cpp` 钉死。

## NOAA APT 气象卫星解码（本批新增模块）

- 标准依据：NOAA APT（Automatic Picture Transmission）广播格式；2400 Hz AM 副载波，
  每行 2080 像素 = A/B 两通道各 1040 像素（同步帧 39 + 间隔 47 + 视频 909 +
  遥测楔 45），像素率 4160 Hz，2 行/秒。
- 版权参考（仅行布局常量与同步算法思路参考，**未复制其源码**）：
  - **noaa-apt**（GPL-3.0-or-later，作者 Simone Margaritelli / etc.）：
    本地只读参考 `repos/noaa-apt/`（`src/decode.rs`）。对照其 APT 行布局常量、
    2400 Hz 副载波 AM 包络检波思路与 7 脉冲同步字滑动互相关的算法流程。
  - **SatDump**（GPL-3.0）：本地只读参考 `repos/SatDump/`。对照其 APT 图像
    组帧（A/B 视频带拼接为 1818 宽灰度图）与同步恢复的数据流思路。
- 实现文件 `src/dsp/apt_decoder.{h,cpp}`、`src/ui/weather_panel.{h,cpp}` 与引擎
  集成 `src/dsp/spectrum_engine.{h,cpp}` 为 MBDSDR 原创实现（SPDX: MIT），
  **clean-room 按 APT 标准与上述算法常量重写**：2400 Hz 数字混频 I/Q 双臂 +
  低通恢复视频包络、7 脉冲同步字滑动归一化互相关行锁定、4160 px/s 分数累加器
  无漂移像素选通、A/B 视频带逐行拼成逐行增长的 `Format_Grayscale8` QImage。
  未逐行移植 noaa-apt 的 Rust 源码或 SatDump 的 C++ 源码，也未随产品分发其
  源代码或二进制原件；行布局常量（39/47/909/45/1040/2080、4160 Hz、2400 Hz）
  与同步互相关算法仅作公开标准对照，端到端已知向量（无噪 Pearson 0.987 /
  有噪 0.876）由 `tests/test_apt.cpp` 钉死。UI 面板 `weather_panel` 只绘制引擎
  真实喂入的图像，无信号时显示诚实空态，从不伪造云图。

## 内置离线 TLE 与下行频率参考（本批新增）

`src/dsp/tle_client.{h,cpp}` 在无网络、无缓存 TLE 时提供一份**离线演示用星历快照**：

- **TLE 来源**：内置的近地 TLE（00005 / 06251 / 28057 / 29238 等，line1/line2
  原样取自随仓库校验用的 `SGP4-VER.TLE`）来自 **CelesTrak 公开发布的
  AIAA-2006-6753 验证星历快照**，2006 历元。
  原始发布页：https://celestrak.org/publications/AIAA/2006-6753/ 。
  这些历元之后星历**必然已经过时**，仅用于在没有 GPS / 网络时离线跑通过境预测与
  Doppler 演示流程，**不是用户当前位置的实时星历**；拿到缓存或联网刷新后即以真实
  星历覆盖。
- **下行频率参考**：`downlinkHzFor()` 按卫星名子串匹配的标称下行频率（如 NOAA 系列
  ~137 MHz 气象广播、ISS 语音下行 145.800 MHz）均为**公开广播频率参考值**，仅用于
  预测 Doppler 与建议调谐频率；名字匹配不到的星诚实返回 0（不显示建议频率）。
- 本仓库未随产品分发任何私有星历或订阅数据；星历与频率均为公开信息，代码内已按上
  述来源标注。本小节不改变根目录 `LICENSE` 的许可声明。

## 频率扫描器与频率管理器（设计参考）

- 版权所有：Copyright (C) Alexandre Rouma / Ryzerth（SDR++）
- 项目主页：https://github.com/AlexandreRouma/SDRPlusPlus
- 许可证：GNU General Public License v3.0 或任何更高版本（GPL-3.0-or-later）

`src/dsp/frequency_scanner.{h,cpp}` 与 `src/ui/bookmark_manager.{h,cpp}` 为 MBDSDR
原创实现（SPDX: MIT），但在行为与架构上参考了 SDR++ 的 scanner / frequency_manager
杂项模块：

- **频率扫描器**：按「起始/终止/步进」逐频调谐，每步驻留固定 dwell（先经短暂
  settle 忽略调谐残留电平），命中判定复用基于真实 RSSI 的 `SignalWatch` 门限；命中后
  停留——「直到信号消失并持续一段 linger」或「固定时长」两种可配，支持向上 / 向下 /
  来回方向与暂停 / 继续 / 停止；书签扫描只在书签频率间跳转、不访问中间频率。对应
  只读参考 `repos/sdrpp/misc_modules/scanner/src/main.cpp`（start/stop/interval、
  tuning time、linger time、level、scanUp/reverseLock 等行为）。
- **频率管理器**：书签字段 name / frequency / mode / bandwidth，按分组（list）组织、
  按 (分组, 频率) 排序，JSON 持久化、启动恢复；**默认书签列表为空、绝不内置任何
  FM/广播电台**。对应只读参考
  `repos/sdrpp/misc_modules/frequency_manager/src/main.cpp`（`FrequencyBookmark`
  字段与 lists 组织、JSON 持久化）。

本项目未逐行移植其 C++ 代码，而是按上述行为用 Qt6 / 自有 DSP 块重新实现；命中电平
始终来自真实 RSSI（离线测试夹具明确标注 FIXTURE/SYNTHETIC -- NOT HARDWARE），不造假
命中、不造假频率；扫描/书签的端到端行为由 `tests/test_scanner.cpp`、
`tests/test_bookmark.cpp` 钉死。本小节不改变根目录 `LICENSE` 的许可声明。

## WFM 立体声（FM Stereo）解码器（本批新增模块）

- 标准依据：经典 FM 立体声复合基带（M=(L+R)/2 占 0–15 kHz、19 kHz 导频、
  以 38 kHz 抑制载波双边带携带 S=(L−R)/2，23–53 kHz），恢复矩阵 L=M+S、R=M−S。
- 版权参考（仅算法结构、环路形式与数据流思路参考，**未复制其代码**）：
  - **SDR++**（GPL-3.0-or-later，Copyright (C) Alexandre Rouma / Ryzerth）：
    本地只读参考 `repos/sdrpp/core/src/dsp/demod/broadcast_fm.h`。对照其导频带通 +
    导频 PLL、对 PLL 输出取共轭并两次相乘把 38 kHz 双边带下变频、2× 幅度恢复与
    和/差矩阵的数据流。
  - **Gqrx**（GPLv3+，FM 立体声实现作者 **Alex Grinkov** a.grinkov(at)gmail.com，
    Copyright 2012 Alexandru Csete OZ9AEC）：本地只读参考
    `repos/gqrx/src/dsp/stereo_demod.cpp`。对照其和/差两路匹配低通、重采样、
    矩阵后再分别去加重（L/R 各 50 µs）的处理顺序。
  - **CubicSDR**（GPL-2.0-or-later，Copyright (c) Charles J. Cliffe）：本地只读参考
    `repos/CubicSDR/src/modules/modem/analog/ModemFMStereo.cpp`。对照其 19 kHz
    切比雪夫带通 + NCO 锁相环、两次 38 kHz mix-down 折叠 S、以及左右独立 50/75 µs
    去加重的实现。
  - **GNU Radio**（GPLv3+）：本地只读参考
    `repos/gnuradio/gr-analog/python/analog/wfm_rcv_pll.py` 与
    `repos/gnuradio/gr-analog/lib/fmdet_cf_impl.cc` 的 PLL 参考载波（pll_refout）
    调频接收思路。
- 实现文件 `src/dsp/wfm_stereo.{h,cpp}`、`src/dsp/demod.{h,cpp}` 中新增的鉴频后/
  去加重前 raw MPX 抽头（`DemodWFM::rawMpxOut()`），以及音频通路立体声扩展
  （`IAudioSink::writeStereo` 与 Memory/Qt 后端）均为 MBDSDR 原创实现（SPDX: MIT），
  clean-room 按上述信号模型重写：19 kHz 二阶（比例+积分）导频锁相环（不使用导频
  带通 FIR，避免导频路径群延迟）、倍频再生 38 kHz 副载波、下变频恢复 S，M 与 S
  走【完全相同】的 15 kHz 线性相位 FIR 与 50 µs 去加重以保证样本对齐；立体声混合
  系数随真实导频质量渐变，无导频 / 弱信号 / 失锁时平滑回退单声道。立体声状态完全
  由真实恢复的导频决定，离线夹具明确标注 SYNTHETIC -- NOT HARDWARE，从不伪造立体声。
  未逐行移植 SDR++ / Gqrx / CubicSDR / GNU Radio 的源码，也未随产品分发其源代码或
  二进制原件；端到 end 已知向量（声道分离度、去导频回退、弱信号回退、中等导频渐变）
  由 `tests/test_wfm_stereo.cpp` 钉死。本小节不改变根目录 `LICENSE` 的许可声明。



