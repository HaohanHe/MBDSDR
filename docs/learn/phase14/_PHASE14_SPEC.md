# 第十四阶段实现规格：SSTV/SSDV 活动接收冲刺

> 基线：远端 main = f1af803（本地已同步）。第十三阶段已交付：渲染深度/解调增强/信号参数化/截图收尾。验证基线：cpp 101/101、flutter 305、python 225+7skip。
>
> 背景：2026-10-08~10 "航天七十载·星火传未来"业余无线电图像通联活动（剩 6 天）。卫星 JAMX01（静安梦想星）、ASRTU-1（阿斯图友谊号）下 SSTV + SSDV 图像共 34 幅，地面接收后发邮件 CASC70@asesspace.com 换证书。频率/过境排班待主办方发布——**不硬编码猜测频率**。
>
> 勘察事实：sstv_decoder.py 现仅吃 WAV 文件（`_read_wav` :135，有 `_resample_if_needed` :183、`_instantaneous_frequency` :197）；onboard MODES（:64）有 adsb/ax25（ax25 已用 AFSKModem 音频解调参考），无 sstv/ssdv；fec.py ReedSolomon（:148，默认 nsym=32/fcr=112 即 SSDV 标准 RS(255,223)，需核对适配）；SSDV 全仓无实现；GFSK 零件：cpp fsk_demod + mbdsdr_ai demod_nfm/analog_demod。

## 1. Wave1（四个并行批次）

| 批次 | 范围 | 交付 |
|---|---|---|
| **P1 SSTV 真机闭环（高优先、快）** | ①sstv_decoder 加直接吃音频数组入口（`decode_audio(samples, sample_rate)`，复用现有重采样/解码，WAV 路径保持）；②onboard 新增 "sstv" 模式：采集→NFM/WFM 解调得音频（卫星 SSTV 走 FM 语音信道，2m 144.x 为主）→重采样到 sstv_decoder 要求率→解码出 PNG；写 SigMF/旁证，无信号诚实空态；③端到端确定性测试（合成 SSTV 信号→解码出图，至少 Martin M1/Robot36 往返）。**本批独占 onboard.py 编辑**。 | sstv 数组入口 + onboard sstv + 测试 |
| **P2 SSDV 完整解码器（高优先、中工作量）** | 按公开 SSDV 规范（UKHAS/AMSAT 数字慢扫描，只学协议标准、不抄 GPL 实现代码）干净室实现 `mbdsdr_ai/ssdv_decoder.py`：①包同步/解析（图像ID、包序号、包类型、JPEG 载荷，SSDV 包 256 字节结构）；②调 fec.py ReedSolomon（核对 SSDV nsym=32/fcr=112 参数适配）纠错丢包；③按序号重组 JPEG、输出可解码图像；④物理层 4FSK/GMSK 复用现有 GFSK/数字零件串接（评估后落地可测部分）。**本批不碰 onboard.py**（onboard ssdv 模式待 P1 完成后追加）。确定性测试：构造 SSDV 包→加 RS→模拟丢包纠错→重组 JPEG 往返。 | ssdv_decoder.py + 测试 |
| **P3 活动专项准备** | docs/learn/phase14/：①活动参数文档（卫星/模式/34 幅/邮件 CASC70@asesspace.com，频率/TLE 录入位留空待官方发布，**不猜测**）；②接收步骤手册（SSTV/SSDV 两路径，从插设备到出图）；③邮件材料清单（姓名/单位/呼号/接收时间/地点/设备/截图）；④过境预测与多普勒补偿对接（低轨 2m/70cm 多普勒，FM SSTV 建议开补偿）——脚本/配置录入位 + 验证。 | 活动文档 + 录入位 |
| **P4 C++ 端评估** | 评估把 SSTV/SSDV 移植 cpp/ 的工作量（对照 cpp/src/dsp 现有 fsk_demod/channelizer/demod）：产出评估文档（模块拆分/工作量/可复用零件/风险）；时间允许落地能落地项（带确定性测试），时间不够以 Python 端"能收到图"为第一优先，C++ 诚实列入 backlog。 | 评估文档（+可落地项） |

## 2. Wave2（收尾）
- P1 完成后：onboard 追加 "ssdv" 模式（复用 P2 ssdv_decoder，由 P1 或收尾统一落地，带测试）。
- 全链路联调：合成 SSTV/SSDV → onboard 两模式出图往返。

## 3. 质量门（每批必过）
- Python：pytest **225+7 基线不破** + 新增 SSTV/SSDV 往返全绿。
- cpp/flutter：若未改保持（101/305）；改则基线不破。
- git：只暂存相关文件（禁 add -A）；无"比赛/competition"、无密钥、无敏感信息、**SSDV 只学协议标准不抄 GPL 代码、全仓 MIT**。
- 推送：云环境无凭据——本地建好提交，推送待用户/外部。

## 4. 红线（一贯）
先学后做、真读代码/规范；禁逐字复制 GPL 代码；无硬件/无信号诚实空态；分批推进、每批独立验证后再推下一批；诚实披露未完成项与环境限制。
