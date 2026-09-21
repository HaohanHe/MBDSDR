---
name: sat-track
description: Use when the user wants to receive a satellite (NOAA weather, ISS, LRPT, FM repeater), needs AOS/LOS pass prediction, Doppler correction, or antenna pointing guidance.
when_to_use: 用户说收卫星/NOAA/ISS/过境预测/多普勒/指向天线/锅/八木对准卫星时
---

# 卫星过境跟踪（Sat Track）

## 流程

1. **查过境**：用 `astronomy`/TLE 计算目标卫星下一次过境，给 AOS/LOS 时间、仰角、方位角、最大仰角。
2. **AI 反向指挥人架天线**：
   - 过境前提醒用户把锅/八木架到预测方位；
   - 仰角变化时指挥用户转怪手/方位；
   - 手机端可当指向器（IMU+罗盘），在天空图上标出当前卫星位置。
3. **多普勒校正**：接收时按过境曲线估多普勒频偏，用 `cfo` 工具校正本振。
4. **接收与解码**：
   - NOAA APT：过境时连续录 IQ/音频，过站后 `sdr_decode_noaa_apt`；
   - SSTV/RS、FM 中继：对准后按对应模式解调；
   - 授时、仰角、GIS 标注可叠加在天空图上（新时空融合）。
5. **记录**：过境时间、仰角曲线、是否成功解码、图像。

## 原则

- 卫星轨道是开放信息，TLE 要新鲜；过期 TLE 会错过过境。
- 低轨卫星过境只有几分钟，提前准备比事后追悔有用。
- AI 不只是预测，还指挥人把物理天线对准。
