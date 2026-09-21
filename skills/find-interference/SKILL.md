---
name: find-interference
description: Use when the user asks to find, locate, or diagnose an RF interference source, a suspicious signal, an unexpected carrier, or band pollution.
when_to_use: 用户说找干扰源/找信号/定位干扰/排查异常载波/频谱污染时
---

# 找干扰源（Find Interference）

这是一个**技能**，不是固定按钮：当用户要找一个未知/异常信号时，按以下认知流程走，而不是直接开频谱。

## 流程

1. **宽扫建底图**：先 `sdr_sweep_scan` 扫一段宽带，记录本底噪声和已知强信号，得到"正常"基线。
2. **识别异常**：对比当前频谱与基线，标出不在基线里的载波/突发。优先看持续载波、非预期调制、能量异常点。
3. **测向与定位**：
   - 用 `sdr_spectrum_find_signals` 拿到峰值频率；
   - 转动天线（八木/锅），让 AI 指挥用户转方向——这是"AI 反向指挥人"：信号最强方向即来向；
   - 必要时用 IMU 记录指向，多次观测三角定位。
4. **辨识性质**：用 AMR/制式识别判断是通信、广播、雷达还是噪声；必要时录 IQ 后离线分析。
5. **给结论**：频率、估计来向、可能类型、建议下一步，而不是只丢一张频谱图。

## 原则

- 不要假设信号是什么；先测、再判。
- 本底用中位数+MAD，不要用全局 std（会被 OOK 脉冲抬高）。
- AI 的价值是把"扫→比→转天线→判"串成自主闭环，不是替用户看频谱。
