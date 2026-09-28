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
