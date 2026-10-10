# 归属与第三方致谢声明（NOTICE）

本文件为 MBDSDR 全仓统一的「归属与第三方致谢」声明，与 `cpp/NOTICE.md`、
`mobile/NOTICE.md` 对称，后两者保留各模块更细粒度的组件与标准说明。

## 一、全仓自有代码许可

- MBDSDR 的全部自有代码均以 **MIT 许可证**发布，许可证全文见仓库根目录 `LICENSE`。
- 全仓 SPDX 标识：`SPDX-License-Identifier: MIT`。
- 版权声明：`Copyright (c) 2026 Haohan He and the MBDSDR contributors`。
- 本文件仅用于署名与合规说明，不改变上述许可证的任何条款。
- **许可证总体策略与 GPL 组件使用/隔离政策，见 `LICENSING.md`**：主项目保持 MIT；个别必须
  使用的 GPL 组件以独立进程、可替换外部工具后端方式接入，不改变主项目的 MIT 许可。

## 二、第三方打包依赖（各自保留其原始许可证与版权声明）

下列依赖作为第三方组件随构建/运行环境引入，**本仓库未修改其源代码**，其许可证与
版权声明以各包自带的 LICENSE / NOTICE 文件为准。

### 移动端（`mobile/`）

- **fftea**（MIT）：`mobile/` 端 FFT 计算依赖，经 Flutter package 引入；详见
  `mobile/NOTICE.md` 的表述，保留其原始许可证与版权声明。

### 桌面端（`cpp/`）

- **Qt 6**（LGPL-3.0）：`cpp/` 桌面 GUI、音频与串口等，以**动态链接**方式使用，
  遵循其自有许可。
- **SoapySDR**（BSL-1.0）：SDR 硬件抽象的**可选**依赖（HackRF / Pluto / Airspy /
  SDRplay 等），未安装时不影响 RTL-SDR 等其余路径。

### Python 运行时依赖

以下清单汇总自仓库根 `requirements.txt` 与 `desktop/requirements.txt` 中实际
打包（非注释）的依赖；`setuptools` 已降为可选注释项，一并列出仅供参考：

| 依赖 | 许可证标识 | 用途 |
| --- | --- | --- |
| numpy | BSD-3-Clause | DSP、频谱处理 |
| scipy | BSD-3-Clause | 滤波 / 重采样 / 频谱 |
| requests | Apache-2.0 | LLM / 多模态 API 调用、TLE 下载 |
| setuptools | MIT | 构建/运行支撑（可选注释项；仅当用户自装 pyrtlsdr 0.2.x 时为其提供 `pkg_resources`） |
| websocket-client | Apache-2.0 | 连接 ai-sdr Mini 硬件（MCP 客户端） |
| websockets | BSD-3-Clause | 无硬件时的设备模拟器服务端 |
| sgp4 | Apache-2.0 | 卫星轨道计算（SGP4/SDP4） |
| skyfield | MIT | 高精度天文计算（轨道 / 多普勒 / 过境预测） |
| pymap3d | BSD-2-Clause | 坐标转换（ECEF / ENU / 经纬高） |
| pyserial | BSD-3-Clause | GNSS 串口 NMEA 读取（USB 转串口） |
| Pillow | MIT-CMU / HPND | 图像处理（NOAA APT / SSTV 解码输出 PNG） |
| sounddevice | MIT | 解调后实时音频输出到默认声卡 |
| PySide6 | LGPL-3.0 | `desktop/` 桌面 GUI 框架（动态链接） |

上表各依赖的许可证与版权声明以各 PyPI 包自带的 LICENSE 为准。

### pyrtlsdr（GPL-3.0）

- **pyrtlsdr**（GPL-3.0）：可选的 RTL-SDR 原生绑定，用户自装、不随包分发。

## 三、技术参考 / 标准致谢（仅参考 / 致谢，未包含其代码）

下列公开标准、协议、算法与数据集仅作为**技术参考或致谢**，本仓库依据其公开规范
**独立实现**，**未包含其源代码**，也未随产品分发其源码或二进制原件。标准中的协议
常量、命令编号、位段布局、多项式系数与几何/数据流思路属于不受版权保护的事实与思想。

- **ETSI EN 300 401**（Radio Data System, RDS）：对应 FM RDS 数据链路解码模块。
- **ICAO Annex 10 第 IV 卷**（Mode S / 1090 ES / ADS-B）：对应 1090 MHz 民航
  监视解码与飞机追踪模块。
- **NOAA APT**（Automatic Picture Transmission）：对应 NOAA 气象卫星云图接收
  解码模块。
- **SGP4 / Spacetrack Report #3**（Hoots & Roeber）及其公开修订——Vallado,
  Crawford, Hujsak & Kelso, *Revisiting Spacetrack Report #3*（AIAA 2006-6753）：
  对应卫星轨道预报模块。
- **rtl_tcp 开放线协议**（Osmocom rtl-sdr 工具集的公开事实标准）：对应网络远程
  RTL-SDR 接收源模块。
- **Natural Earth**（https://www.naturalearthdata.com/ ）110m 陆地 / 海岸线数据：
  离线海岸线矢量，属**公有领域（Public Domain）**，内嵌于离线地图模块。
- **CelesTrak**（https://celestrak.org/ ）公开发布的 TLE / 验证星历：用于 SGP4
  精度核对与（无网络时的）离线演示星历，历元必然过时。

## 三b、WTFPL / 宽松许可参考实现（保留归属）

下列上游源码以 WTFPL（或同类极宽松许可）发布，本项目在干净室纪律下以其**协议事实与
互操作接口**为参考实现依据（不整文件拷入主代码库）；保留其许可与归属，详见
`LICENSING.md` §4b。

- **NanoVNA-App**（OneOfEleven，https://github.com/OneOfEleven/NanoVNA-App ，
  WTFPL v2）：NanoVNA 文本协议（`data 0-7` 等）与 V2 二进制寄存器协议的参考实现，
  对应 MBDSDR 的 NanoVNA 仪器接入模块（`mbdsdr_ai/nanovna_client.py`、
  `cpp/src/vna/`）。

## 四、模块级说明

更细粒度的模块拆分、构建期依赖与端到端测试向量说明，分别见：

- `cpp/NOTICE.md`
- `mobile/NOTICE.md`
