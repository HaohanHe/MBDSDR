# MBDSDR

**AI 定义的软件定义无线电平台。** 插上一支廉价 RTL-SDR，就能得到一台专业接收机；同时它是一个可被任意 AI 直接驱动、能力可插拔的无线电智能体。

- 桌面：原生 PySide6（Windows / Linux）
- 移动：Flutter
- 内核：Python（DSP、协议编解码、AI Agent）
- 许可证：GPL-3.0；论文与文档：CC-BY

---

## 名字由来

**MBDSDR = MB + BDS + SDR**

- **MB**：取自维护者呼号（呼号在「设置」中配置，**程序内不写死任何呼号**）；
- **BDS**：北斗卫星导航系统，代表 GNSS / 新时空方向；
- **SDR**：软件定义无线电。

---

## 核心理念：科学、理性、务实，不造假

MBDSDR 只呈现真实数据，这是一条硬约束：

- **设备列表只显示真实探测到的硬件**。通过系统 API（librtlsdr / SoapySDR / gr-osmosdr / 硬件 SDK）实际枚举到的设备才会出现；不拥有、未组装的设备不会列出来。
- **没有模拟/演示模式**。无硬件时显式显示「未连接」，不生成假频谱、假 RSSI、假设备。
- **GNSS、IMU 等外设无硬件即整组隐藏**，不显示示例定位或示例姿态。
- **不预存任何地区 FM 电台**（各地频率不同）；书签只提供扫频段与全球通用的标准信标。
- 频谱、瀑布、天空图全部由真实 IQ 经 FFT / SGP4 计算得到。

---

## 主要能力

### 实时接收
- 频谱 + 瀑布（多档 FFT、多种窗函数、SDR++ 配色），点谱线跳频、滚轮步进、键盘微调、VFO 拖拽（真接数字下变频 NCO）。
- 模拟解调：AM / FM（WFM、NFM）/ USB / LSB / CW，静噪滞回、实时声卡播放。
- IQ 前端校正：DC 尖峰消除、IQ 不平衡镜像校正、抗混叠抽取。
- IQ 录制 / 回放（含 SigMF 标准元数据），设备热插拔看门狗与自动重连。

### 数字模式与协议
- 业余数字：FT8 / FST4、CW、SSTV、AX.25 / APRS、M17、JS8、ARDOP、Winlink(B2F)。
- 数据链 / 帧层：ADS-B（含航路图）、RDS、DMR / YSF / P25、rtl433 物联网仪表。
- 自动调制识别（AMR）与信号活动检测、值守触发录制、宽段扫频。
- 协议分析三层抽象（Bit / Symbol / Protocol）与游标测量。

### 卫星与新时空
- 气象卫星云图接收：GK-2A、风云四号 / 三号、GOES、NOAA（LRIT/HRIT 全链路 + 图像增强与投影）。
- 轨道与过境：SGP4 传播、过境预报、多普勒校正、云台/旋转器指向。
- 多普勒定轨：复基带频偏观测 + EKF / RLS（含 J2 摄动、坐标变换）。
- 新时空：GNSS 监测、PPP-RTK、LEO PNT、授时、天空图标注仰角与轨迹、GIS 融合。

### AI Agent
- 工具调用（Function Calling）驱动全部无线电能力，工具结果真实喂回模型。
- 模型感知上下文窗口、超阈值自动摘要、多轮对话、流式输出。
- 对外提供 **MCP 服务端**：任意 AI IDE（Cursor、Claude 等）可直接控制硬件；也可作为 **Skill / MCP** 被通用智能体复用。
- API Key、呼号、地面站坐标等全部在「设置」中配置，**不硬编码、不入仓库**。

---

## 快速开始

```bash
pip install -r requirements.txt
```

桌面端（任选其一，均为无控制台入口）：

- Windows：双击 `MBDSDR.vbs`，或 `desktop/run_gui.bat`
- Linux：`desktop/run_gui.sh`
- 也可执行：`python -m desktop.main`

首次打开后，在「设置」中配置呼号与（可选）AI API Key；在设备选择中选择实际枚举到的 SDR。

---

## 项目结构

```text
mbdsdr_ai/          Python 内核：SDR 后端、DSP、协议编解码、AI Agent、新时空
desktop/            PySide6 桌面：主窗口、各功能面板、频谱/天空控件
mobile/             Flutter 移动端（lib/ 下 Dart 源码）
ai-sdr-mini-kicad/  自研硬件 KiCad 工程（原理图、BOM、连接表）+ Arduino 固件
tests/              测试套件（全量离线可跑，无需真实硬件）
docs/               文档与学习笔记
scripts/            自检、串口 GNSS 探测、启动等实用脚本
```

说明：主线软件使用市售 RTL-SDR 即可完整工作；`ai-sdr-mini-kicad/` 为自研硬件的开源设计资料（KiCad 工程、BOM、连接表，Arduino 固件可直接烧录），不影响纯软件使用。

---

## 硬件支持

- **RTL-SDR**（含 Fitipower FC0012 / E4000 / R820T 等调谐器），USB 直连。
- **rtl_tcp** 远程接收（需提供真实主机地址与端口）。
- **SoapySDR / gr-osmosdr** 通用层：实际枚举到的设备自动出现。
- **PlutoSDR** 等：仅在真实探测到时列出。
- HackRF、bladeRF、LimeSDR、USRP 等设备：仅当对应驱动/SDK 实际枚举到硬件时才出现，不预置。

---

## 专利与声码器合规

DMR / YSF 的 **AMBE+2/AMBE**、P25 的 **IMBE** 等语音声码器受专利保护。MBDSDR 只实现**数据层**（成帧、时隙、呼号、通话组、色码等），**不逆向、不重实现**这些声码器；语音解码需用户**自备合法声码器**并自行接入。

---

## 上游开源项目复用声明

本项目在开发中学习、复用了以下开源项目的设计与算法（各自遵循其许可证）：

| 项目 | 复用内容 |
| --- | --- |
| [SDR++](https://github.com/AlexandreRouma/SDRPlusPlus) | 频谱/瀑布、设备管理、解调带宽、配色与交互 |
| [GNU Radio](https://github.com/gnuradio/gnuradio) | 流图处理、DSP 算法、osmosdr 源 |
| [SatDump](https://github.com/SatDump/SatDump) | 气象卫星下行链路、解调与解码参数 |
| [rtl-sdr](https://github.com/osmocom/rtl-sdr) | RTL2832U 接口、调谐器枚举、FC0012 特性 |
| [Stellarium](https://github.com/Stellarium/stellarium) | 天空视图与天文渲染理念 |
| [inspectrum](https://github.com/miek/inspectrum) / [URH](https://github.com/jopohl/urh) / [SigDigger](https://github.com/BatchDrake/SigDigger) | 信号分析、游标测量、协议提取、信道检测 |
| [M17-Project](https://github.com/M17-Project) | M17 数字语音协议 |
| gr-satnogs / r2cloud | 卫星地面站流水线与调度 |

各第三方组件的许可证与版权归其各自作者所有；商用或再分发时请遵循对应许可证要求。

---

## 许可证

- 代码：[GPL-3.0](LICENSE)
- 论文与文档：CC-BY
