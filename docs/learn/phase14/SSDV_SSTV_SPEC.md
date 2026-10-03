# Phase 14 — 卫星 SSTV / SSDV 图像接收任务（规格与研究笔记）

> 用途：完成 2026-10-08 至 10-10「JAMX01（静安梦想星）」「ASRTU-1（阿斯图友谊号）」
> 两颗卫星下传的 SSTV（模拟慢扫描）与 SSDV（数字慢扫描）共 34 幅图像接收任务。
> 本文是干净室（MIT）实现的权威规格。参考实现均为 GPL，**只学机制、不复制代码**。

## 1. 活动参数（微信文章实读，口径：主办方文章）

- 时间：2026-10-08 / 09 / 10（具体过境时段、轨道排班、频率**以主办方后续日程为准**，文章未给）。
- 卫星：JAMX01 静安梦想星；ASRTU-1 阿斯图友谊号。
- 图像：共 34 幅 = 10 静安地标航拍 + 10 静安青少年 AI 航天科幻画 + 4 航天 70 周年 Logo
  + 10 哈工大航天主题。
- 合作电台：BJ1BJA（上海静安区青少年活动交流中心）、BY2HIT（哈工大业余无线电俱乐部）。
- 发起：中国航天基金会 + 上海静安区青少年活动交流中心 + 哈工大紫丁香学生微纳卫星团队；公益免费。
- 成功反馈：发邮件至 **CASC70@asesspace.com**，附 姓名/单位/呼号/接收时间/地点/设备信息/截图
  （团队可附活动照片），换联合证书。

## 2. 两种模式的完整接收链路

### 2.1 SSTV（模拟）—— 已闭环（提交 bbf2a18）

RTL-SDR IQ → FM 解调（max_dev 5 kHz）→ 重采样 48 kHz → `sstv_decoder.decode_audio(mode="auto")`
→ PNG。已覆盖 Martin M1/M2、Scottie S1/S2/DX、Robot36/72、PD90..290；端到端测试连跑稳定 PASS。

### 2.2 SSDV（数字）—— 本规格要实现的部分

SSDV 标准只定义「256 字节包 + RS FEC」，**不定义物理层调制**；物理层由传输系统决定。
以 BY2HIT / LilacSat 风格（ASRTU-1 高度疑似沿用）为例，完整链：

```
RTL-SDR IQ
  → FM 窄带语音信道解调（max_dev 以官方为准，约 5 kHz）
  → 重采样到音频（参考 19200 Hz；AFSK 1200 或 2400 baud）
  → AFSK 解调（1200 baud: mark 1200 / space 2200；2400 baud: mark 1200 / space 2400）→ 比特
  → 帧同步（preamble 约 150 bit + 4 字节 ASM 同步字 + 帧 + trailer）
  → （可选）卷积码 K=7, r=1/2 → Viterbi 解码
  → CCSDS 同步解扰（255 字节 PN，自逆）
  → （可选）RS(255,223) 解码
  → SSDV 256 字节包流
  → SSDV 解码（包校验 → RS 纠错 → CRC → 按 MCU 重组 JPEG）
  → JPEG 文件
```

物理层（速率/频率/ASM/是否卷积）等官方日程，**代码须做成可插拔的「字节/包输入」接口**：
解调链每一级输出可独立喂入，拿到官方参数后只需选择对应解调器与常量，不改 SSDV 核心。

## 3. fsphil SSDV 包格式（权威，口径：repos/ssdv_ref/ssdv.c，GPL 只学机制）

包长固定 256 字节。Normal（FEC）模式 payload = 256 − 15 头 − 4 CRC − 32 RS = **205 字节**；
No-FEC 模式 payload = 256 − 15 − 4 = **237 字节**。

| 偏移 | 长度 | 字段 | 编码 |
|---|---|---|---|
| 0 | 1 | Sync | 固定 `0x55` |
| 1 | 1 | Type 标识 | `0x66`=Normal(FEC)，`0x67`=No-FEC；type = byte−0x66 |
| 2..5 | 4 | Callsign | base-40，大端，最多 6 字符 |
| 6 | 1 | Image ID | 0..255，每换一张图应改变 |
| 7..8 | 2 | Packet ID | 大端 |
| 9 | 1 | 宽 | width/16（MCU 列数） |
| 10 | 1 | 高 | height/16（MCU 行数） |
| 11 | 1 | Flags | bit2=EOI；bits3..5=quality（编码值 ^4）；bits0..1=mcu_mode |
| 12 | 1 | Packet MCU offset | 该包内首个 MCU 的字节偏移 |
| 13..14 | 2 | Packet MCU ID | 大端，该包首个 MCU 编号 |
| 15.. | 205/237 | Payload | 重新编码后的 JPEG MCU 码流 |
| 之后 | 4 | CRC32 | 覆盖 **byte[1] .. payload 末**（不含 sync 0x55）；标准 CRC32（poly 0xEDB88320 反射，init/xorout 0xFFFFFFFF） |
| 之后 | 32 | RS parity | Normal 模式；Phil Karn `encode_rs_8`，从 byte[1] 起，pad 到 223 消息 |

**RS 权威参数（已用 Phil Karn rs8.c 编译 KAT 逐字节验证，勿再改动）**：
- GF(256) 本原多项式 **0x187**（与标准 CCSDS 相同，α=2；fsphil ALPHA_TO 表首 8 项 1,2,4,...,0x87 证实）。
- 根生成元 **gamma = α^11**（`PRIM=11`），生成多项式根 = α^(11·(112+i))，i=0..31 —— **不是** 标准 CCSDS 的 α^(112+i)。
- **无 0xFF 符号反转**（fsphil `ssdv.c` 直接喂原始字节给 `encode_rs_8/decode_rs_8`；0xFF 仅属 JPEG/CRC 字段）。
- 位置域映射：Chien 找到 λ 根 α^j 后，字节位置 = (IPRIM·j − 1) mod 255，IPRIM = 11⁻¹ mod 255 = 116；差错特征值 X_k = α^(11·(254−pos))。
- 参考向量（msg[i]=i, i=0..222 → 32 字节校验）：
  `2fbd4fb4748494b9acd554627212eeb3ebed41191de1d36320ea49290b25abcf`
  （由 rs8.c `FCR=112, PRIM=11, GENPOLY 0x187` 编译生成；仅验证事实，GPL 源码不入库）

- mcu_mode：0=2×2（彩色 4 Y）、1=2×1、2=1×2、3=1×1（灰度）。
- JPEG 限制：灰度或 YUV/YCbCr；宽高为 16 倍数（≤4080）；Baseline DCT；MCU 总数 ≤65535。
- **MCU 级封装的意义**：编码端把标准 JPEG 完全解码成原始 MCU，用统一/固定的量化+Huffman 表
  重新编码并分包；解码端收集包、按 MCU 重合成标准 JPEG。丢包只导致对应 MCU 局部花屏，
  不会像普通 JPEG 那样整图损坏，且解码端无需随包传表。

## 4. 资产盘点（已入库，可复用）

- `mbdsdr_ai/fec.py`
  - `ReedSolomon`：RS(255,223) 编/解码（BM+Chien+Forney）。参数可配：`fcr`（默认 112）、
    `prim`（根序列步进，默认 1 = 标准 CCSDS α^(112+i)；**SSDV 用 prim=11**，根 γ=α^11）、
    `prim_poly`（默认 0x187）、`ccsds_invert`（默认 True = 标准 CCSDS 符号反转；**SSDV 为 False**）。
    编/解码均已按 IPRIM 位置映射 + prim 拉伸特征值正确处理 prim≠1（KAT 对拍 fsphil 参考向量）。
  - `Scrambler`：CCSDS 255 字节 PN 同步扰码/解扰（自逆，与 SatDump randomization 一致）。
  - `DifferentialEncoder`：DBPSK/DQPSK 差分。
- `mbdsdr_ai/ax25.py` `AFSKModem`、`mbdsdr_ai/multimon_decoders.py` `AFSK1200Demod`：
  AFSK 1200（Bell202, 1200/2200）解调，可扩展卫星 mark/space 与 2400 baud。
- `tools/onboarding/onboard.py`：`_demod_fm`（差分相位）、MODES 注册、step_decode/output 框架。
- C++ 端 `cpp/src/dsp/ssdv_packet.*`：**SP5WWP 变体（6 字节头、JPEG 字节直拼），与 fsphil
  方言不同**，保留但不作为本任务核心。

## 5. 缺口与实现任务（干净室 MIT）

1. `mbdsdr_ai/ssdv_decoder.py`（新建）：
   - 256 字节包识别（sync 0x55 + type 0x66/0x67）、RS 解码纠错（复用/适配 fec.py，注意 fsphil
     RS 从 byte[1] 起、pad 约定）、CRC32 校验、15 字节头解析。
   - 包收集：按 image id 分组、packet id 排序去重、记录丢包；按 packet_mcu_id/offset 放置 payload。
   - MCU 重组：用内置标准 JPEG DQT/DHT 表（干净室，JPEG  Annex K 公开表）+ 收集到的 MCU 码流，
     重合成完整、可被标准 JPEG 解码器打开的 JPEG；处理 EOI、mcu_mode、灰度/彩色、缺失 MCU。
   - 同时提供编码方向（仅测试需要）：JPEG → MCU → 分包 → CRC → RS，供往返测试。
2. `mbdsdr_ai/ccsds_rx.py`（新建）：
   - 卷积码 K=7 r=1/2 编码 + Viterbi 解码；CCSDS ASM 帧同步（preamble/同步字/帧长状态机）；
   - 串联 AFSK → ASM → Viterbi → 解扰 → RS → SSDV 包；物理层参数全部具名常量、可插拔。
3. `tools/onboarding/onboard.py`：注册 `ssdv` 模式（采样率/时长/频率提示以官方为准，未确定前
   频率字段留空并在文档说明，不硬编码猜测），step_decode 增加 ssdv 分支，step_output 输出 JPEG。
4. 测试 `tests/test_ssdv_e2e.py`（新建）：
   - 往返：合成/读取一张小 JPEG → SSDV 编码分包 → 加 RS/CRC → 注入随机丢包与误码 →
     RS 纠错 + MCU 重组 → 还原 JPEG，断言可解码、尺寸一致、丢包超纠错能力时诚实报缺失；
   - CCSDS：卷积→解扰→RS 往返、ASM 同步、AFSK 卫星速率往返；
   - 无数据时诚实空态，不渲染假图、不预存呼号（callsign 由参数传入）。

## 6. 验收标准（MainAgent 亲自核对）

- 上述文件真实存在；`py_compile` 通过；新增 pytest 全绿并看到真实通过数。
- 往返测试中：在 RS(32) 纠错能力内丢包/误码可完全恢复；超出能力时报告具体缺失 MCU，不造假图。
- 重组出的 JPEG 能被标准解码器打开，宽高与原图一致。
- 全仓无 GPL 代码逐字复制（文件 SPDX 为 MIT，NOTICE 承载致谢）；无密钥；无「比赛/competition」字样。
- 不默认内置任何呼号（含 BI4MIB）；callsign、频率、速率均为参数，未确定即留空并说明。
- 物理层未定不阻塞核心开发与离线测试；拿到官方日程后仅需填参数/选解调器。

## 7. 参考实现位置（GPL，只学机制，勿复制、勿改动）

- `repos/ssdv_ref/`（fsphil/ssdv，现代经典 SSDV；ssdv.c/ssdv.h、rs8.c Phil Karn、main.c）。
  已归档，新位置 codeberg.org/fsphil/ssdv。
- `repos/by2hit_arcssd-go/`（BY2HIT：gRPC 外壳 + engine/ccsds，调用外部 SSDV.exe；
  ccsds/ 含 direwolf AFSK、viterbi27、rs、randomizer、ccsds 状态机）。
- `repos/by2hit_arcss_panel_pc/`、`repos/by2hit_pcsi/`（面板与 PCSI，参考用）。
- 旧 V0 habhub API（sync/0x66/base40 早期描述）：https://ssdv.habhub.org/about.php（注意与现代版差异）。

---

## 8. P3 活动 SSDV 方言核实（2026-10-04，Phase23 A 块；最高优先）

> 本节回答「P3 活动两颗星的 SSDV 到底是哪一种方言、我方 `mbdsdr_ai/ssdv_decoder.py`
> 能不能直接解」。结论基于联网检索的**真实信号解码旁证**，非主办方官方规格书
> （主办方未公开包格式）。所有来源 URL 附后，未确证项单列。

### 8.1 一句话结论

**ASRTU-1（阿斯图友谊号 = RS64S = AO-123，NORAD 61781）的 SSDV 方言 = DSLWP 变体
（218 字节包 / 9 字节头 / 无 sync / CRC32 魔数初值 / 包内无 RS）**，
**不是**我方 `ssdv_decoder.py` 实现的 fsphil 经典（256 字节 / 15 头 / sync 0x55 / 包内 RS）。
我方**包层（同步/头/CRC/RS）有实质差距，不能直接解**；但 **MCU 级 JPEG 重组核心是同一
fsphil 家族，可复用**。JAMX01（静安梦想星）按活动分工只发 SSTV（模拟），不走 SSDV。

### 8.2 活动分工与频率（旁证，来源见 8.6）

- 主办方活动说明（Libre Space 社区，BG2GFC 转中国航天基金会口径，2026-09-24）：
  「Jing'an Dream Star (JAMX01) and ASRTU Friendship Satellite (ASRTU-1) will **respectively
  transmit SSTV and SSDV** during the event period (October 8-10), for a total of 34 images.」
  → 按 respectively 读法：**JAMX01 发 SSTV，ASRTU-1 发 SSDV**。
  （每星各发几幅的精确排班仍待主办方日程；34 幅 = 24 静安相关 + 10 哈工大相关。）
- ASRTU-1 SSDV 下行：**436.210 MHz，BPSK 9600**，USB 接收、约 24 kHz 滤波窗
  （DL7NDR 实测；VA3ROM 在活动帖确认 436.210 MHz）。
- JAMX01 IARU 协调频率（2025-01 预发射协调，待活动日程确认）：
  信标/SSTV 下行 **435.075 MHz**；FM 数字转发器下行 435.175；遥测下行 435.500；上行 145.950。

### 8.3 方言判定：ASRTU-1 = DSLWP 变体（证据链）

三条独立旁证均用 **daniestevez/ssdv（fsphil ssdv 的 fork）的 `-D` / `--satellite DSLWP`
模式** 成功从 ASRTU-1 真实录制解出 320×256 图像（2024-12 ~ 2025-01，PE0SAT/DL7NDR/
SA2KNG/JH4XSY）：

1. 标准接收链：`gr_satellites 61781 --rawint16 iq.wav --kiss_out out.kss`
   → `gr_satellites_ssdv --satellite DSLWP out.kss out.jpg`（PE0SAT 亲授，DL7NDR 跟做成功）。
2. 解码器打印：`Callsign: DSLWP / Image ID: 21 / Resolution: 320x256 / MCU blocks: 320 /
   Sampling factor: 2x2 / Quality level: 3 / Read 60 packets` —— 全是 fsphil 包头语义，
   但 callsign 是 fork 在 DSLWP 模式下**硬编码 "DSLWP"**（包内无呼号字段）。
3. fork 源码（GPL，只学机制）定义三种模式常量：
   - fsphil 经典：`PKT_SIZE=256`，`HEADER=15`，sync 0x55 + type 0x66/0x67 + base-40 呼号；
   - **DSLWP：`PKT_SIZE_DSLWP=218`，`HEADER_DSLWP=9`**；
   - JY1SAT：`PKT_SIZE_JY1SAT=200`，`HEADER_JY1SAT=11`。

**DSLWP 包布局（9 字节头，大端；来源 daniestevez/ssdv ssdv.h / ssdv.c，GPL 只学机制）**：

| 偏移 | 长度 | 字段 | 说明 |
|---|---|---|---|
| 0 | 1 | image_id | 每换图 +1 |
| 1..2 | 2 | packet_id | 大端 |
| 3 | 1 | width/16 | MCU 列数 |
| 4 | 1 | height/16 | MCU 行数 |
| 5 | 1 | flags | bits3..5 = quality^4；bit2 = EOI；bits0..1 = mcu_mode |
| 6 | 1 | packet_mcu_offset | 本包内首个 MCU 字节偏移 |
| 7..8 | 2 | packet_mcu_id | 大端；0xFFFF = 本包无新 MCU |
| 9.. | 205 | payload | JPEG MCU 码流（218 − 9 头 − 4 CRC） |
| 末 4 | 4 | CRC32 | 覆盖 **byte[0]..payload 末**；初值 **0x4EE4FDE1**（DSLWP 魔数，非 0xFFFFFFFF），大端存放 |

关键差异：**无 sync 0x55、无 type 0x66、无 base-40 呼号、包内无 RS 校验字节**
（FEC 由 CCSDS 信道级提供，见 8.4）。

**信道帧结构（ASRTU-1.yml，SA2KNG 简化版，同 DL7NDR 帖）**：
`9k6 BPSK, 436.210 MHz, framing: CCSDS Concatenated, precoding: differential,
RS basis: conventional, frame size: 223`。
即：差分 BPSK 解调 → ASM 帧同步 → 卷积 K=7 r=1/2 Viterbi → CCSDS 解扰 →
RS(255,223) 信道解码 → 223 字节帧载荷 → 拼出 SSDV 字节流 → 切成 218 字节 DSLWP 包。

### 8.4 与我方实现的差距（如实报告；代码属 C 块域，本节不改代码）

| 维度 | 我方 `mbdsdr_ai/ssdv_decoder.py` | ASRTU-1 实际（DSLWP 变体） | 差距 |
|---|---|---|---|
| 包长 | 256 | **218** | 实质差距 |
| 头长/位置 | 15 字节，byte0=sync 0x55, byte1=type 0x66/0x67 | **9 字节，byte0=image_id**，无 sync/type | 实质差距 |
| 呼号 | base-40 4 字节在 byte2..5 | **无呼号字段**（fork 解码时硬编码 "DSLWP"） | 我方多解析 4 字节 |
| CRC32 初值 | 0xFFFFFFFF，覆盖 byte[1..payload] | **0x4EE4FDE1 魔数**，覆盖 byte[0..payload] | 校验不过 |
| 包内 RS | 有（prim=11, gamma=α^11, fcr=112, 无 0xFF 反转） | **无包内 RS**（FEC 在 CCSDS 信道级） | 我方多解一层 RS |
| MCU 重组 | 标准 Annex K DQT/DHT、mcu_mode、quality、MCU blob | **完全相同的 fsphil 家族** | **可复用** |
| 信道 | 我方 ccsds_rx 待建（卷积/解扰/RS 占位） | CCSDS Concatenated 223B 帧 | 需对齐常量 |

**结论**：我方 `SsdvDecoder.feed()` 靠「找 0x55 + 0x66/0x67」锁包，DSLWP 包没有这两个字节，
**feed() 永远不会锁包**。要解 ASRTU-1，需新增一条 **DSLWP 包层路径**（218B 定长切包、
9 字节头解析、魔数 CRC、跳过包内 RS），而 **MCU→JPEG 重组核心（`SsdvImage.build` /
`_build_jpeg_header` / Huffman/DQT 表）可直接复用**。C++ 端 `ssdv_packet.h`（SP5WWP 6 头）
与本方言也不匹配（已被真实解码证据排除）。

### 8.5 候选解码顺序与验证动作（无法官方确证时的落地策略）

> 主办方未公开包格式，以上为真实信号旁证。按以下顺序试解，先用最可能的 DSLWP 变体：

1. **首选（最高概率）：DSLWP 变体** —— 218B 包 / 9 头 / 魔数 CRC 0x4EE4FDE1 / 无包内 RS。
   由 PE0SAT 等多人真实解码成功佐证（见 8.6）。
2. **次选：fsphil 经典（我方现状）** —— 256B / 15 头 / sync 0x55 / 0x66/0x67 / 包内 RS。
   若活动临时改用标准 fsphil（habhub V0 同此），我方代码可直接解。
3. **兜底：SP5WWP 6 头变体** —— 256B / 6 小端头 / JPEG 直拼（C++ `ssdv_packet.h` 是这种）。
   已被真实解码证据排除，但留作兜底。

**验证动作（真实录制样本回放确定，必须做）**：
- 拿一段 ASRTU-1 真实 IQ/音频录制（SatNOGS 公开观测、或活动前一次过境实录），
  先 `gr_satellites 61781`（或我方 ccsds_rx）解出 223B 帧载荷拼出的字节流；
- 把字节流按 218B 定长切分，用魔数 CRC 0x4EE4FDE1 校验：**CRC 通过率高 = DSLWP 实锤**；
- 若 218B/魔数 CRC 全不过，再退而按 0x55/0x66 同步找 fsphil 包；仍不过再试 SP5WWP。
- 这一步同时验证我方 DSLWP 包层（待 C 块补）与 MCU 重组核心的组合是否出图。

### 8.6 来源 URL（均为联网实读）

- 活动分工「JAMX01 发 SSTV、ASRTU-1 发 SSDV，共 34 幅」：
  https://community.libre.space/t/china-space-industry-70th-anniversary-amateur-radio-image-transmission-event/15362
- ASRTU-1 SSDV 实解专帖（PE0SAT 授 DSLWP 流程、ASRTU-1.yml、436.210 BPSK 9k6）：
  https://community.libre.space/t/ssdv-fm-rs64s-asrtu-1/12729
- ASRTU-1 = NORAD 61781 / 9k6 BPSK / CCSDS Concatenated（gr-satellites 支持列表）：
  https://gr-satellites.readthedocs.io/en/latest/supported_satellites.html
- DSLWP/fsphil/JY1SAT 三包型常量与 218B/9 头布局（daniestevez/ssdv fork，GPL 只学机制）：
  https://raw.githubusercontent.com/daniestevez/ssdv/master/ssdv.h
  https://raw.githubusercontent.com/daniestevez/ssdv/master/ssdv.c
- BY70-4 沿用 ASRTU 解码器（旁证 ASRTU 工具链）：
  https://community.libre.space/t/cz-6c-launches-jamx01-by70-4-2026-08-25-03-30utc/15173
- JAMX01 IARU 协调频率（435.075 SSTV 下行等）：
  https://www.hellocq.net/forum/read.php?tid=377723
- habhub V0（= fsphil 经典包格式）文档：https://ssdv.habhub.org/about.php

### 8.7 未确证项（如实登记）

- **无主办方/哈工大官方包格式规格书**：以上方言判定来自外部接收者真实解码旁证
  （PE0SAT 等多人复现成功），非团队官方文档；高置信但非「官方确证」。
- **每星图像精确数量/排班**：仅知「respectively SSTV/SSDV」与总数 34，每星各几幅待官方日程。
- **活动期 ASRTU-1 是否仍用日常 436.210 MHz SSDV 信道**：VA3ROM/BC2GFC 推断为 436.21，
  但活动专用排班频率待主办方确认。
- **DSLWP 包在 223B 帧流中的确切分包/重组细节**（是否每帧直接拼字节流、218B 边界对齐方式）
  未逐行核实 gr_satellites_ssdv.py，留待 C 块补 DSLWP 包层时对照真实录制样本确定。
- PE0SAT 曾评「越来越多团队用自己的标准，或许要给 gr_satellites_ssdv 加 ASRTU-1 专用模式」
  —— 提示 ASRTU-1 与 DSLWP 可能存在细微差异，但现有 DSLWP 模式已能解出图，细节以实测为准。
