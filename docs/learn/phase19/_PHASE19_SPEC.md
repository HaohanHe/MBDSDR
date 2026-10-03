# 第十九阶段实现规格：频谱/瀑布差距收口 + 气象卫星解码深研对标

> 基线：HEAD = 79a7ca5，ctest 112/112（e2e_smoke 云 VM 无音频环境性红项不计）。
>
> 勘察事实：
> - A：`SpectrumDisplay::loadColormapFromJson/loadColormapFromFile` 已在 spectrum_display.cpp:472-486 **实现**（Phase13）；但**无任何调用方（无 UI 入口）**，且 **grep 无"改色板后重染历史瀑布行"逻辑**（L8 缺口实锤）；doZoom 峰保持有机制注释（:738 引 SDR++ waterfall.cpp:65-90）需核查列上真实生效。
> - B：自有 `mbdsdr_ai/meteor_sat.py`（Meteor-M LRPT 参考）；C++ `dsp/apt_decoder.h:47-85` 已是完整 NOAA APT 流式解码器（feed→image，干净室）；SDR++ 参考 `decoder_modules/meteor_demodulator`、`decoder_modules/weather_sat_decoder`（GPL 只学机制）。

## A 组：频谱/瀑布最后差距收口（独占 ui/ 域）
1. **L8 色板文件闭环**：确认解析正确（JSON `{"stops":[...]}` 或数组→256 LUT 重生成）；**补"改色板后整段重染历史瀑布行"**（环形行缓冲保留 raw dB、改色板后重染不丢历史，仿 SDR++ 改色板行为）；**补 UI 入口**（设置/右键菜单选色板文件，诚实报解析错误）；对照 Figma/小米克制、弹性 tokens::scaled。
2. **doZoom 峰保持核查**：确认真实生效（块内取 max 防缩水漏峰）；未生效则补。
3. 改色板/拖量程后历史瀑布重染一致性验证。
4. 测试：JSON 色板解析（合法/坏文件诚实报错）、重染一致性、doZoom 峰保持；新 ctest 目标，112 基线不回归。

## B 组：气象卫星 decoder 深研对标（独占 decoder/图像管线域）
1. 精读 repos/sdrpp/decoder_modules/{meteor_demodulator,weather_sat_decoder}/src 源码原文（禁只看 README），产出 **2 篇带 file:line 的精读笔记**（docs/learn/phase19/）。
2. **weather_sat（NOAA APT）**：现有 apt_decoder 已覆盖 → **跳过不重复造**，笔记中写明对照结论。
3. **Meteor-M LRPT**：对照自有 meteor_sat.py 与 SDR++ meteor 机制，**先评估 C++ 移植必要性再决策（YAGNI 诚实）**：价值 vs 成本（云内无硬件、合成验证、image_enhance 管线是否可复用、python 侧是否已完整）；**决定不做要写明理由与回补条件**；决定做则落地（帧同步 0x7A7A、Viterbi、RS、解交织、JPEG 重组、复用 image_enhance）+ 合成 QPSK/已知帧 ctest + 噪声空态。

## 质量门 / 红线
- ctest 112 基线不破 + 新增全绿（offscreen）；干净室 MIT、不复制 GPL；通用非专用；不硬编码/不预存、无硬件诚实空态；合成仅测试夹具；先学后做（真读源码原文）。
- 只暂存相关文件（禁 add -A）；**不自行 push**。如实报告 file:line、ctest 实跑数（全量）、精读笔记路径、未解决项（MainAgent 全量构建+跑 ctest+查 git 复核）。
