# 第十二阶段实现规格：SDR++ 深度研习与落地专项

> 基线：远端 main = b816915（本地已同步）。第十一阶段已交付：端到端演练（acceptance_run.sh）、论文投递包（refs.bib 核验 12 条）、体验深化（单击调谐/snap/ctest 95）、移动端对齐（HDOP/定位权限/flutter 305）。验证基线：cpp 95/95、flutter 305、python 225+7skip。
>
> 用户明确要求：**大量子 agent 深度研究 SDR++，学透后落地到 MBDSDR**。
>
> 上游源码：repos/sdrpp（master 8c9f5ee，已克隆）。已定位：渲染/瀑布 `core/src/gui/widgets/waterfall.{cpp,h}` + `colormaps.{cpp,h}`；解调 `core/src/dsp/demod/{am,fm,ssb,broadcast_fm,cw,psk,gfsk,quadrature}.h`；信号处理 `core/src/dsp/correction/{dc_blocker,frequency_xlator,rx_vfo}.h` 与 `channel/`；UI/交互 `core/src/gui/{main_window,tuner,theme_manager,style}.{cpp,h}` + `widgets/`；插件 `root/modules/` + `core/src/module.{cpp,h}`；设备源插件需精读 agent 定位（hint 含线索）。SDR++ 为 **GPLv3**：只学机制/算法思路/交互范式，按干净室原则用自有代码实现，禁逐字复制。

## 1. Wave1（六个精读 agent，并行）

每个子系统一个 agent：**真读源码**（.cpp/.h/plugins，非 README）→ 学习笔记 `docs/learn/phase12/sdrpp-<sub>.md`（四要素：①上游真实做法+file:line+短代码片段注明 GPL 许可；②MBDSDR 现状 file:line；③差距判定：已实现且真实 / 已实现但缺深度 / 未实现；④落地建议）→ 该子系统差距判定片段（供整合）。

| 子系统 | 上游定位 | 笔记 |
|---|---|---|
| A 频谱/瀑布渲染 | waterfall.{cpp,h}、colormaps、FFT 管线、OpenGL 绘制、调色板 | sdrpp-waterfall.md |
| B 设备抽象与后端 | source 插件模型（root/modules/ 下源插件）、device 接口、采样率/带宽切换、AGC | sdrpp-device.md |
| C 解调链 | dsp/demod/{am,fm,ssb,broadcast_fm,cw,psk,gfsk,quadrature}.h、滤波器组 | sdrpp-demod.md |
| D 信号处理 | dsp/correction/{dc_blocker,frequency_xlator,rx_vfo}.h、decimation、平滑 | sdrpp-dsp.md |
| E UI/交互 | gui/{main_window,tuner,theme_manager,style}、拖拽调谐/滚轮步进/频谱点击/VFO/快捷键/皮肤 | sdrpp-ui.md |
| F 插件架构 | root/modules/、core/src/module.{cpp,h}、插件生命周期/菜单注入/模块注册 | sdrpp-plugin.md |

## 2. Wave2（据差距清单）

整合 `docs/learn/phase12/sdrpp-gap-analysis.md`（六份判定汇总成"已实现且真实/已实现但缺深度/未实现"总表 + 按价值/可验证性排序的落地候选）。据此派落地批次：选真正有价值且**可确定性验证**的差距项落地到 cpp/（桌面）与 mobile/（Flutter），每个落地项有确定性测试；用户视角交互核对（点频谱调谐、滚轮 snap、瀑布滚动、VFO 高亮、带宽拖拽——缺的补、有的验证真实）进收尾。

## 3. 质量门（每批必过）
- cpp：ctest **95/95 基线不破** + 新增全绿；全量构建 0 错误。
- Flutter：**305 基线不破** + 新增全绿；analyze 0。
- Python：pytest 基线 225+7 不破。
- git：只暂存相关文件（禁 add -A）；无"比赛/competition"、无密钥、无敏感信息、**GPL 干净室**（笔记注明许可、落地代码为自有实现）。
- 推送：云环境无凭据——本地建好提交，推送待用户/外部。

## 4. 红线（一贯）
真读源码、file:line 可查；禁逐字复制 GPL 代码；无硬件诚实空态；分批推进、每批独立验证后再推下一批；诚实披露未完成项与环境限制。
