# 开源软件使用说明 / Notices

MBDSDR 的全部自有代码均以 **MIT 许可证**发布（见仓库根目录 `LICENSE`）。
本文件仅用于署名与合规说明。下列各功能模块均**依据公开标准、规范或数据手册独立实现**；
所提及的第三方开源项目仅作为**技术参考或致谢**，本仓库**未包含其源代码**，也未随产品
分发其源代码或二进制原件。标准中规定的协议常量、命令编号、位段布局、多项式系数与
几何/数据流思路属于不受版权保护的事实与思想。

## 频谱与瀑布显示

- `src/ui/spectrum_display.{h,cpp}`、`src/ui/spectrum_widget.{h,cpp}`：依据频谱分析与
  滚动频谱图（spectrogram）的通用原理独立实现。频谱迹线、频率刻度条与瀑布图共享同一
  左边界与数据宽度以使频率轴对齐、可拖拽分隔条并钳制最小高度、dB→颜色查找、按滚动
  速度抽帧、可视窗到历史列的映射，均为通用的几何与数据流思路，以自有结构、命名与
  QPainter 绘制代码重新表达。
- SDR++（https://github.com/AlexandreRouma/SDRPlusPlus ）仅为技术参考与致谢，**未包含
  其代码**。

## 多 VFO、信道化与重采样

- `src/dsp/vfo_manager.{h,cpp}`、`src/dsp/channelizer.{h,cpp}`、
  `src/dsp/audio_resampler.{h,cpp}`：依据多信道软件无线电与多速率信号处理的公开方法
  独立实现（宽带 IQ 扇出到多个接收信道、NCO 下变频后滤波抽取、加窗 sinc 滤波与分数
  延迟重采样）。
- SDR++ 仅为架构层面的技术参考与致谢，**未包含其代码**。

## FM RDS 数据链路

- 标准依据：**ETSI EN 300 401** *Radio Data System (RDS)*。
- `src/dsp/rds_decoder.{h,cpp}` 依据该标准独立实现：57 kHz 数字混频与低通、
  2375 符号/秒双相（biphase）差分译码、26 比特块滑动同步
  （生成多项式 G(x)=x¹⁰+x⁸+x⁷+x⁵+x⁴+x³+1）、节目名（PS）/节目类型（PTY）/
  无线电文本（RT）解析。
- gqrx、redsea 仅为算法与常数的技术参考，**未包含其代码**；端到端已知向量由
  `tests/test_rds.cpp` 固定。

## WFM 立体声（FM Stereo）

- 标准依据：经典 FM 立体声复合基带——单声道和信号 M=(L+R)/2（0–15 kHz）、19 kHz
  导频、以 38 kHz 抑制载波双边带携带差信号 S=(L−R)/2（23–53 kHz），恢复矩阵
  L=M+S、R=M−S。
- `src/dsp/wfm_stereo.{h,cpp}` 依据该信号模型独立实现：19 kHz 导频锁相环、倍频再生
  38 kHz 副载波、下变频恢复 S，M 与 S 走相同的 15 kHz 低通与 50 µs 去加重以保证样本
  对齐，立体声混合系数随真实导频质量渐变，失锁时平滑回退单声道。
- SDR++、Gqrx、CubicSDR、GNU Radio 仅为技术参考与致谢，**未包含其代码**；端到端
  已知向量由 `tests/test_wfm_stereo.cpp` 固定。

## 1090 MHz Mode S / ADS-B

- 标准依据：**ICAO Annex 10 第 IV 卷**（1090 ES / Mode S 链路）、DF17 扩展 Squitter
  格式。
- `src/dsp/adsb_decoder.{h,cpp}`、`src/ui/aircraft_tracker.{h,cpp}` 依据该标准独立
  实现：前导匹配与脉冲位置（PPM）判决、CRC-24 校验、DF17 位置（CPR 全局/本地）、
  呼号与速度消息拆解，以及按 ICAO 地址合并帧、TTL 过期清理。
- dump1090 仅为算法、常数与位段布局的技术参考；Junzi Sun《The 1090 Megahertz Riddle》
  （https://mode-s.org ）为教学参考；**均未包含其代码**。端到端已知向量由
  `tests/test_adsb_cpr.cpp`、`tests/test_adsb_decode.cpp`、
  `tests/test_aircraft_tracker.cpp` 固定。

## NOAA APT 气象卫星图像

- 标准依据：**NOAA APT**（Automatic Picture Transmission）广播格式——2400 Hz AM
  副载波，每行 2080 像素（A/B 两通道各 1040），像素率 4160 Hz，每秒 2 行。
- `src/dsp/apt_decoder.{h,cpp}`、`src/ui/weather_panel.{h,cpp}` 依据该格式独立实现：
  2400 Hz 数字混频与低通恢复包络、同步字滑动互相关行锁定、分数累加器像素选通、
  A/B 视频带逐行拼接为灰度图。
- noaa-apt、SatDump 仅为行布局常量与同步算法思路的技术参考，**未包含其代码**；
  端到端已知向量由 `tests/test_apt.cpp` 固定。

## SGP4 轨道预报与 TLE

- 方法依据：**Spacetrack Report #3**（Hoots & Roeber）及其公开修订
  Vallado, Crawford, Hujsak & Kelso, *Revisiting Spacetrack Report #3*, AIAA
  2006-6753。
- `src/dsp/sgp4.{h,cpp}` 依据上述公开算法公式独立实现，并对照 CelesTrak 公开发布的
  验证星历（https://celestrak.org/publications/AIAA/2006-6753/ ）核对精度，**未包含
  参考实现的代码**。
- `src/dsp/tle_client.{h,cpp}` 内置的离线 TLE 快照取自上述 CelesTrak 公开验证星历，
  仅用于无网络时的离线演示，历元必然过时，联网或获得缓存后即以真实星历覆盖。

## GNSS 接入（NMEA 0183）

- 标准依据：**NMEA 0183** 串行接口规范。
- `src/gnss/*`（语句解析、串口/文件/内存传输、接收线程）依据该规范独立实现。

## 离线地图与天空图

- `src/ui/coastline_data.h`：离线海岸线矢量数据来自 **Natural Earth**
  （https://www.naturalearthdata.com/ ）110m 陆地/海岸线，属**公有领域（Public
  Domain）**，以数组形式内嵌。
- `src/ui/map_projection.{h,cpp}`：等距圆柱（equirectangular / plate carrée）投影，
  为无版权的通用数学投影，未使用第三方投影库。
- `src/ui/{world_view,sky_view,elevation_plot}.{h,cpp}`：依据通用地图投影与极坐标/
  直角坐标绘图原理独立实现。

## 频率扫描与书签管理

- `src/dsp/frequency_scanner.{h,cpp}`、`src/ui/bookmark_manager.{h,cpp}`：依据频率扫描
  与书签持久化的通用行为独立实现（按起始/终止/步进逐频驻留、基于真实 RSSI 门限判定
  命中、书签字段与分组 JSON 持久化；默认书签列表为空，不内置任何电台）。
- SDR++ 的 scanner / frequency_manager 仅为行为层面的技术参考与致谢，**未包含其代码**。
- 端到端行为由 `tests/test_scanner.cpp`、`tests/test_bookmark.cpp` 固定。

## rtl_tcp 与 RTL-SDR 接入

- `src/dsp/rtl_tcp_source.{h,cpp}`、`src/dsp/rtl_sdr_source.{h,cpp}`：依据 **rtl_tcp
  开放线协议**（Osmocom rtl-sdr 工具集的公开事实标准）与 RTL-SDR 器件接口独立实现，
  未使用上游代码，仅按公开协议/接口重新实现。

## AX.25 与 KISS

- 标准依据：**TAPR AX.25** 链路层规范、**KISS TNC** 规范（公开的串行封装协议，
  FEND/FESC/TFEND/TFESC 转义表）。
- `src/radio/ax25.{h,cpp}`、`src/radio/kiss.{h,cpp}` 依据上述公开规范独立实现。
- direwolf 仅为技术参考与致谢，**未包含其代码**；端到端向量由
  `tests/test_ax25.cpp` 固定。

## 串口电台 CAT（CI-V / Kenwood）

- 依据：Icom **CI-V** 与 Kenwood TS 系列**公开命令手册**。
- `src/radio/cat_client.{h,cpp}` 依据上述公开命令格式独立实现（频率/模式读写、
  小端 BCD 与命令字节）。
- hamlib（LGPL）仅为命令对照的技术参考，**未包含其代码**。

## CW 莫尔斯键控

- 依据：**ITU 莫尔斯电码**（公开域）与公开的键速计时约定（PARIS 公式）。
- `src/radio/cw_keyer.{h,cpp}`、`src/dsp/cw_decoder.{h,cpp}` 依据上述公开内容独立
  实现（串口 RTS/DTR 键控时序、点划时长）。

## 构建期依赖（各自保留其许可证）

- **Qt 6**（https://www.qt.io/ ）：GUI、音频与串口等，以动态链接方式使用，遵循其
  自有许可。
- **SoapySDR**（https://github.com/pothosware/SoapySDR ，BSL-1.0）：硬件抽象的可选
  依赖。
- 其余 DSP（FFT、调制/解调、滤波、AGC、降噪等）均为本项目自包含实现，未依赖
  第三方 DSP 库源码。
