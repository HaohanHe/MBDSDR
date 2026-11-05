# 归属与第三方致谢声明（NOTICE）

本文件为 MBDSDR 全仓统一的「归属与第三方致谢」声明，与 `cpp/NOTICE.md`、
`mobile/NOTICE.md` 对称，后两者保留各模块更细粒度的组件与标准说明。

## 一、全仓自有代码许可

- MBDSDR 的全部自有代码均以 **MIT 许可证**发布，许可证全文见仓库根目录 `LICENSE`。
- 全仓 SPDX 标识：`SPDX-License-Identifier: MIT`。
- 版权声明：`Copyright (c) 2026 the MBDSDR contributors`。
- 本文件仅用于署名与合规说明，不改变上述许可证的任何条款。
- **许可证总体策略与 GPL 组件使用/隔离政策，见 `LICENSING.md`**：主项目自有代码保持
  MIT；随产物分发的 GPL 组件（pyrtlsdr / librtlsdr）逐项登记并随包附许可证全文（见 §二）；
  仓外运行的 GPL 工具仍按独立进程方式接入。

## 二、第三方打包依赖（各自保留其原始许可证与版权声明）

下列依赖作为第三方组件随构建/运行环境引入，**本仓库未修改其源代码**。桌面产物
（`desktop/MBDSDR.spec`）随包携带项目 `LICENSE`、`NOTICE.md` 与目录
`third_party_licenses/`；copyleft 组件（GPL / LGPL）的许可证全文在该目录内提供，
其余宽松许可组件的版权与许可声明以各包自带的 LICENSE / NOTICE 文件为准。

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
| setuptools | MIT | 构建/运行支撑（可选注释项；随包 pyrtlsdr 0.2.x 运行期提供 `pkg_resources`） |
| websocket-client | Apache-2.0 | 连接 ai-sdr Mini 硬件（MCP 客户端） |
| websockets | BSD-3-Clause | 无硬件时的设备模拟器服务端 |
| sgp4 | Apache-2.0 | 卫星轨道计算（SGP4/SDP4） |
| skyfield | MIT | 高精度天文计算（轨道 / 多普勒 / 过境预测） |
| pyserial | BSD-3-Clause | GNSS 串口 NMEA 读取（USB 转串口） |
| Pillow | MIT-CMU / HPND | 图像处理（NOAA APT / SSTV 解码输出 PNG） |
| sounddevice | MIT | 解调后实时音频输出到默认声卡 |
| PySide6 | LGPL-3.0 | `desktop/` 桌面 GUI 框架（动态链接）；许可证全文 `third_party_licenses/LGPL-3.0.txt` |
| shiboken6 | LGPL-3.0 | PySide6 绑定生成器运行时（Qt for Python 同许可）；许可证全文同上 |
| pyrtlsdr | GPL-3.0 | RTL-SDR 原生绑定（`rtlsdr` 模块随产物分发）；许可证全文 `third_party_licenses/GPL-3.0.txt` |

上表宽松许可依赖的许可证与版权声明以各 PyPI 包自带的 LICENSE 为准；copyleft 组件的
许可证全文见产物内 `third_party_licenses/`。

### pyrtlsdr（GPL-3.0）与 librtlsdr（GPL-2.0）

- **pyrtlsdr 0.2.93**（GPL-3.0）：RTL-SDR 原生绑定，**实际随桌面产物分发**——产物内
  有 `rtlsdr` 模块与 `pyrtlsdr-0.2.93.dist-info`。许可证全文
  `third_party_licenses/GPL-3.0.txt`；源码 <https://github.com/pyrtlsdr/pyrtlsdr>。
- **librtlsdr / `rtlsdr.dll`**（GPL-2.0，osmocom rtl-sdr）：pyrtlsdr 的底层驱动，
  产物 `_internal/librtlsdr.dll` 与 `_internal/rtlsdr.dll` 实际随包分发。许可证全文
  `third_party_licenses/GPL-2.0.txt`；源码 <https://github.com/osmocom/rtl-sdr>。
- 上述两个 GPL 组件已按 `LICENSING.md` §2 / §4 登记；项目自有代码仍以 MIT 发布。

## 三、技术参考 / 标准与向量来源

本节登记实现和测试实际使用的标准、协议资料、协议常量及输出向量。除明确说明外，
相关上游实现的源代码不随本仓库分发；各资料的权利与许可按原出处条款处理。本说明只
记录来源与协议事实，不对任何资料的版权状态或许可合规作绝对法律保证。

- **Stellarium `modern_iau` 星座连线**（<https://github.com/Stellarium/stellarium/tree/master/skycultures/modern_iau>，
  **CC BY-SA 4.0**，<https://creativecommons.org/licenses/by-sa/4.0/>，图形由
  Stellarium 与 Sky & Telescope 的 Roger Sinnott、Rick Fienberg 合作制作并发布于
  IAU 网站）：`mbdsdr_ai/data/constellations_iau.json` 的 148 条折线 / 842 段
  连线数据来源；本仓保留署名，该派生数据继续以 CC BY-SA 4.0 分发。文件内
  88 个中文名中 61 个另取自 **Wikidata**（CC0-1.0）固定快照
  `mbdsdr_ai/data/constellation_names_cc0.json`（7,574 字节，`fetched_at`
  2026-11-05T16:45:46+00:00，文件 SHA-256
  `75597803a485cc217b5716265f5d165e35b3804aaf1fbaaba50cad292e73d2f3`，
  抓取脚本 `scripts/fetch_constellation_names_cc0.py`）；其余 27 个为项目
  `constellation_culture.json`。连线结构未改，名称替换决策见 `LICENSING.md` §4c。
- **OpenNGC**（<https://github.com/mattiaverga/OpenNGC>，CC BY-SA 4.0，
  <https://creativecommons.org/licenses/by-sa/4.0/>）：深空天体目录
  `mbdsdr_ai/data/dso_catalog.json` 的坐标/星等/角径/类型来源；该派生数据库以
  CC BY-SA 4.0 同许可分发。中文名取自 Wikidata（CC0 1.0）。
- **缺字段补充来源**（`mbdsdr_ai/data/dso_supplement.json`，9 条，只填
  OpenNGC 为 `null` 的字段，快照含逐条原始行与 `payload_sha256`，抓取脚本
  `scripts/fetch_dso_supplement.py`）：
  - **HEASARC `MESSIER` 表**（NASA HEASARC W3Browse，据 Sky Catalogue 2000.0
    Vol. 2 表 III 编成，bibcode `1985skca.book.....H`）：M22/M35/M62/M69/M70/
    M71/M107 的 V 波段视星等（`vmag` 列原值）；
  - **Washington Double Star Catalog (WDS)**（U.S. Naval Observatory）：M40
    （Winnecke 4，WDS J12222+5805）的双星分离，取最近一次观测 53.2″
    （2017 年）/60 = 0.887′；
  - **Lynga, Catalogue of Open Cluster Data, 3rd ed.**（CDS `VII/92A`，
    bibcode `1987CoORo...16....1L`）：M73（NGC 6994）的
    `angular_diameter = 2.8′`。
  - 以上均取编目原值，不做插值或估算；字段级出处写在每条记录的
    `magnitude_source` / `size_source`。
- **CCSDS 232.0-B-4 / CCSDS 230.1-G-3 Annex F**：TC 主头为 5 字节；Example 10
  样例 `001B000E06010203040506070814BB` 用于 `tc_uplink.py` 与帧布局的逐字节对拍。
- **ETSI EN 302 307-1 V1.4.1 (2014-11)**：DVB-S2 专用 BCH 参数来源，对应
  `dvbs2_bch.py` 与 `data/dvbs2_bch.json`；其 Annexes B/C 为 normal/short LDPC
  地址表来源，对应 `dvbs2_ldpc.py` 与 `data/dvbs2_ldpc_tables.json`。
- **drmpeg/gr-dvbs2**（提交 `99a2ac5592fa41e9c0f8f757995f3451ab8184a6`，
  GPL-3.0-or-later）：仓外运行未修改的上游编码器，生成独立 LDPC-only FECFRAME
  参考输出及 DVB 专用 BCH 编码 KAT。**drmpeg/gr-dvbs2rx**（提交
  `74df1dadbd20b02ce0cdfd3244f9c97022981235`，GPL-3.0-or-later）：仓外运行未修改的
  上游解码器，为 BCH KAT 提供独立译码参照。21 组 BCH 模式向量覆盖编码、无错译码和
  固定 5 错译码，向量 SHA-256 为
  `d7764e85634bdacfc77c2ef9a4c00b3c8ad03dba888d92ee3153a3a4b313e03e`。
  LDPC-only 输出与 BCH KAT 均不是 ETSI 官方向量，BCH/LDPC 级联自洽不等于完整
  空口互操作；上游源码和驱动保留在仓库外的参考目录，不复制进主代码库。
- **2020 年 QEX《FT4 and FT8 Protocols》及 ARRL 附件校验材料**：FT4/FT8
  共用 LDPC(174,91)；FT4 白化、Gray 映射、同步序列与 GFSK 参数的协议事实来源。
- **WSJT-X 官方仓库**（提交 `967c85a6119110788b0df3782cf796485b2cee14`）的
  `samples/FT4/000000_000002.wav`（SHA-256
  `d9e91fa04ba138a7b9f41b4103823c77ca1c3a9775101f6b14d60935bcd3813b`）：用于
  FT4 统一入口的官方 WAV 解码验证。该样本没有可比的官方逐条文本真值，不能将有效
  载荷恢复写成逐条文本对拍，也不是 OTA 证据。
- **WSJT 官方仓库**（提交 `b4f9a431bcf6449df8f37b56de79d48b665b044c`）的
  WSPR 手册、协议常量及未修改 `wsprsim` 编码输出：经典 WSPR Type 1 采用 50 bit
  消息、卷积码、交织和 162 个 4FSK 符号，不是 LDPC；统一入口已用官方 `wsprsim`
  独立合成音频在受限时频网格复验，仍不是 OTA、宽带盲捕或弱信号门限证据。上游
  GPL 源码仅用于仓外协议核对与参考输出生成，未复制进主代码库。

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

下列上游源码以 WTFPL（或同类极宽松许可）发布，本项目在独立实现纪律下以其**协议事实与
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
