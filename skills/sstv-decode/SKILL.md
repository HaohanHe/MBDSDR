---
name: sstv-decode
description: Use when the user has SSTV audio/wave recording and wants to decode it to an image, or asks which SSTV mode is on the air.
when_to_use: 用户给 SSTV 录音/波形，要解码成图，或问空中是什么 SSTV 制式时
---

# SSTV 解码（SSTV Decode）

## 流程

1. **先识别制式，别假设**：用数据驱动时序识别（不是固定查 VIS 头，VIS 检测常不可靠）。
   支持 Martin M1/M2、Scottie S1/S2/DX、Robot 36/72、PD90~290。
2. **采样率对齐**：读 wav 后按目标采样率重采样；在瞬时频率上做解码。
3. **按模式解码**：
   - Robot36：隔行共享色度；
   - Robot72：每行 Y/Cr/Cb 三段；
   - 段长按实测同步周期反推，不要写死常解错的毫秒数。
4. **校验**：解码出的图像应有合理的色彩块/内容；右窗偏绿青通常是 Cr/Cb 交换。
5. **输出**：保存 PNG，报告识别到的模式、行数、是否成功。

## 已知坑

- 鉴频不能用 np.repeat 零阶保持（污染），用 np.interp/cumsum；
- 归一化相关 sync 峰信号约 6、噪声 2~3.5，门限取 4.5；
- Wraase SC2-180 时序参数未确定，先标注待真机标定。

## 原则

- SSTV 解码是成熟开源早有的能力，AI 价值在自动识别+自动选模式+自动纠错，不在"我能解 SSTV"。
