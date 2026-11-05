# 解码器能力矩阵：这个解码器到底是不是真的

> SPDX-License-Identifier: MIT
>
> **本文件由 `scripts/decoder_capability_report.py --write` 生成，请勿手改。**
> 改能力就改 `mbdsdr_ai/decode_router.py` 或本脚本的 EVIDENCE 表，再重新生成；
> `tests/test_decoder_capability_matrix.py` 会校验文档与生成结果逐字一致。

## 一句话怎么读这张表

- **真实度 = 真实**：有外部真值或真录对拍（第三方样本按需下载、SHA-256 钉值，离线诚实 skip）；「结论」列会写出命中多少条。
- **真实度 = 部分**：只有自洽合成链（自家编码→自家解码）或标准原文逐位钉扎，**没有 OTA 实绩**——能证明「代码符合协议」，不能证明「能在天上收到」。
- **真实度 = 缺样本**：连一段真录都没有，只有协议数学性质；「最小采样条件」列写明补到什么样一段信号才能破零。

**「来历」列是硬约束**：只有 `external-truth`（外部真值/标准原文/第三方实现输出）才允许出现在「真实」栏；`self-generated`（自家编码器产出）与`synthetic-loopback`（自家编码→自家解码回环）一律不得算作验证。由 `tests/test_golden_provenance.py` 守卫。

## 能力矩阵

| 模式 | 真实度 | 来历 | 证据位置 | 复跑方式 | 最小采样条件 | 结论 |
|---|---|---|---|---|---|---|
| `acars` | **部分** | 合成回环 | tests/test_acars_roundtrip.py / tests/test_acars_consistency.py（MSK 往返：默认 1200 bd + 2400 bd 兼容档 + 字段一致性） | `python -m pytest tests/test_acars_roundtrip.py tests/test_acars_consistency.py -q` | 131.55MHz AM 下行解调音频 ≥ 5s，含 ≥ 1 条 1200baud MSK ACARS报文且 CRC 通过 | 半个真：合成链自洽，缺真实航司报文真录 |
| `adsb` | **真实** | 外部真值 | tests/test_adsb_dump1090_golden.py（dump1090 官方 modes1.bin 逐帧对拍）；tests/test_adsb_consistency.py（合成链）；本机 1090MHz 真录 2026-10-29 三次 0 CRC 帧 = 负证据（如实保留） | `python -m pytest tests/test_adsb_dump1090_golden.py tests/test_adsb_consistency.py -q` | 1090MHz 下行 .cf32，fs ≥ 2 MS/s（任意档由 any_fs 入口带限重采样），≥ 3 个干净 DF17 前导（CRC-24 通过与 dump1090 对拍；多帧扫描见 adsb.decode_baseband） | 真（外部真值）：dump1090 官方样本两条帧逐字命中；通用入口已做多帧扫描（每个前导独立 CRC），但本机 OTA 真录尚未破零 |
| `ais` | **缺样本** | 合成回环 | tests/test_ais_decoder.py（GMSK→HDLC→FCS-16→M.1371 字段，合成链） | `python -m pytest tests/test_ais_decoder.py -q` | 161.975/162.025MHz 信道化复基带 .cf32，fs=192000 或 96000，≥ 5s 且含 ≥ 3 条 FCS 通过的 AIS 报文（含 Type 1 位置） | 尚不算真：无 AIS 信道真录，只有协议性质钉扎 |
| `aprs` | **部分** | 合成回环 | tests/test_decode_pipeline_e2e.py（合成 2 帧：NFM 鉴频→Bell202 AFSK→NRZI→HDLC→CRC-16→AX.25，呼号/digipeater 路径/payload 逐字节对拍；噪声/截断/CRC 三条失败原因码） | `python -m pytest tests/test_decode_pipeline_e2e.py -q` | 现有证据仅为**合成验证**（仓内调制 2 帧、逐字节对拍），**无 OTA 证据**；补真录条件：144.640/144.390/144.800MHz NFM 下行复基带 ≥ 5s，含至少 1 个 1200bd Bell202 AX.25 帧（CRC-16 通过） | 合成验证（synthetic, NOT OTA）：链路已接线，但只有仓内调制 fixture 逐字节对拍，无 OTA 证据 |
| `ft4` | **部分** | 合成回环 | mbdsdr_ai/tests/test_decoders_ft4_wspr.py（自体往返 + 窄带拒绝契约） | `python -m pytest mbdsdr_ai/tests/test_decoders_ft4_wspr.py -q` | SSB 解调后 12kHz PCM16 单声道 .wav，含完整 FT4 帧（105 符号 / 5.04s）；本实现**无 FT4 官方样例对拍**，真值仅为仓内合成帧（provenance: self-generated） | 半个真：算法链自洽，缺 OTA 真录对拍 |
| `ft8` | **真实** | 外部真值 | tests/test_ft8_wsjtx_golden.py（WSJT-X 官方 210703_133430.wav，5/14 精确命中、零假接受）；tests/test_ft8_cross_impl_fixture.py | `python -m pytest tests/test_ft8_wsjtx_golden.py tests/test_ft8_cross_impl_fixture.py -q` | 真录已在 test_ft8_* 中对拍；通用 WAV 入口未接线，需调用ft8_lite.decode_ft8_audio 或走专用工具 | 真（外部真值）：官方样本 5/14 命中；通用 WAV 入口未接线（not_implemented） |
| `meteor` | **部分** | 合成回环 | tests/test_meteor_lrpt.py（合成向量）；真录证据在 LRPT 链见下一行 | `python -m pytest tests/test_meteor_lrpt.py -q` | Meteor-M2 137.9MHz 下行 .cf32 ≥ 20s，Viterbi 可锁且 RS 全纠帧 ≥ 2（本仓 LRPT 真录链可作真值） | 半个真：通用入口只到解调骨架（输出 symbols/bits，不产图像）；图像链真录证据在 LRPT 真录链 |
| `navtex` | **缺样本** | 合成回环 | tests/test_navtex_decoder.py（SITOR-B 100bd + CCIR 476 + 时间分集，合成链） | `python -m pytest tests/test_navtex_decoder.py -q` | 518/490kHz USB 解调后音频 ≥ 60s，含完整 ZCZC 帧与至少 3 行正文（SITOR-B 相位锁定成立） | 尚不算真：无 518kHz 真录，只有协议性质钉扎 |
| `pocsag` | **部分** | 合成回环 | tests/test_pocsag_roundtrip.py（BCH(31,21) 纠错 + 报文往返） | `python -m pytest tests/test_pocsag_roundtrip.py -q` | 137/153/466MHz NFM 下行 .cf32 ≥ 10s（fs ≥ 2×占用带宽），含 ≥ 2 条 POCSAG 批（1200bd、±4.5kHz 偏移、BCH(31,21) 可纠）；仓内已用真实 NFM-FSK 合成 IQ 端到端验证，无 OTA 真录 | 半个真：合成链自洽，缺真实寻呼台真录 |
| `wspr` | **部分** | 合成回环 | mbdsdr_ai/tests/test_decoders_ft4_wspr.py（四音网格 + 时间/频率偏移搜索） | `python -m pytest mbdsdr_ai/tests/test_decoders_ft4_wspr.py -q` | USB 解调后 12kHz PCM16 .wav，含完整 110.592s WSPR 传输；本实现**未做 OTA/弱信号灵敏度验收**，不声明 dB 门限（wsprd/wsjtx 仅作外部工具登记，不代表已对拍） | 半个真：算法链自洽，缺 OTA 真录对拍 |
| `lrpt（LRPT 图像链）` | **真实** | 外部真值 | tests/test_lrpt_meteor_real_golden.py（TU Berlin 79MB Meteor-M2 真录，SHA-256 钉真值）；tests/test_lrpt_iq_chain.py；tests/test_lrpt_cross_impl_fixture.py | `python -m pytest tests/test_lrpt_meteor_real_golden.py tests/test_lrpt_iq_chain.py -q` | Meteor-M2 137.9MHz .cf32 ≥ 20s，重采样到整数倍符号率（如 144kS/s = 2sps） | 真（真录）：79MB 过境真录帧同步锁定（ham=0 完美命中） |
| `dvbs2-sof（DVB-S2 SOF 同步层）` | **部分** | 合成回环 | tests/test_dvbs2_sof_sync.py（ETSI EN 302 307-1 §5.1.2 同步字/π 旋转，合成链 + 标准原文口径） | `python -m pytest tests/test_dvbs2_sof_sync.py -q` | 卫星下行符号率级复基带 .cf32 ≥ 3 个 PLFRAME，CFO ≪ 符号率/26 | 半个真：标准层逐位钉扎，缺 DVB-S2 OTA 真录 |
| `dvbs2-vcdu（DATAFIELD→VCDU 重组）` | **部分** | 合成回环 | tests/test_dvbs2_datafield_vcdu.py（BBHEADER CRC-8/DFL/SYNCD + VCDU 主头，合成链自洽 + 编码侧互逆对拍） | `python -m pytest tests/test_dvbs2_datafield_vcdu.py -q` | 同 dvbs2-sof；且 DFL/SYNCD 整字节、帧长 892B | 半个真：全链自洽，缺 DVB-S2 OTA 真录 |

## 资产来历（golden / fixture）

每个 `provenance.json` 逐文件声明来历、来源名称、SHA-256 与日期；缺声明或把 `self-generated` 标成外部真值都会被守卫测试拦下。**自生成的黄金值只能证明「代码与自己的编码器一致」，不能算验证。**

| 目录 | 整目录类型 | 文件数 | 三类分布 |
|---|---|---|---|
| `mbdsdr_ai/tests/fixtures` | 外部真值 | 1 | 外部真值 1 |
| `mbdsdr_ai/tests/fixtures/openngc` | 外部真值 | 2 | 外部真值 2 |
| `tests/fixtures` | 自生成 | 3 | 自生成 3 |
| `tests/fixtures/decode_pipeline` | 自生成 | 9 | 自生成 9 |
| `tests/fixtures/iridium107` | 外部真值 | 2 | 外部真值 2 |
| `tests/fixtures/real_fm_88m1` | 外部真值 | 2 | 外部真值 2 |
| `tests/fixtures/sensitivity` | 自生成 | 8 | 自生成 8 |
| `tests/golden` | mixed | 13 | 外部真值 8、自生成 5 |

没有入仓 golden、但属于「自家编码→自家解码回环」的链：

| 链 | 来历 | 证据 | 说明 |
|---|---|---|---|
| M17（C++ 侧） | 合成回环 | `cpp/tests/test_m17_decoder.cpp` | 仓内编码→仓内解码回环，无入仓 golden；不得写成外部对拍 |
| CCSDS RX 链 | 合成回环 | `mbdsdr_ai/tests/test_ccsds_rx.py` | 仓内卷积编码→Viterbi 回环；CCSDS TC/CLTU 的黄金向量是官方 PDF 转录（见资产表） |

## 复跑整张表

```bash
# 只跑全部最小命令（离线、不开声卡、不碰硬件）
python scripts/decoder_capability_report.py --verify
# 或逐条复制上表「复跑方式」列
```

环境（仓内约定，保证不露窗不发声）：

```bash
export MBDSDR_NULL_AUDIO=1 MBDSDR_NO_HW=1 QT_QPA_PLATFORM=offscreen
```

## 灵敏度基线（合成 AWGN，仅供相对比较）

> 全部为**合成 fixture + AWGN，非 OTA**。不同模式的噪声域/带宽口径不同，**dB 值不可横向比较**；这张表只钉方向性、90% 档、100% 档、临界区间与首个失败码，不钉具体 dB 数值。

| 模式 | 口径（噪声域｜带宽｜SNR） | 档位网格 | 90% 档 | 100% 档 | 临界区间 | 首个失败码 | 是否单调 |
|---|---|---|---|---|---|---|---|
| `ais` | 复基带（基带 IQ）｜噪声带宽 = fs/2 = 120 kHz（fs=240 kS/s，全带白噪，未按 9600 bit/s 占用带宽归一）｜每样本：突发（含前导）内平均信号功率 / 噪声方差；GMSK 1 bit/符号（25 样本/符号） | -10..16 dB（14 档 / 20 次） | 8 dB | 10 dB | 6..10 dB | `nothing_detected` | 是 |
| `adsb` | 复基带（基带 IQ）｜噪声带宽 = fs/2 = 4 MHz（fs=8 M，全带白噪，未按 PPM 主瓣归一）｜每样本：整帧平均信号功率 / 噪声方差；PPM 1 bit/符号（8 样本/bit） | -10..16 dB（14 档 / 20 次） | 2 dB | 2 dB | -2..2 dB | `preamble_not_found` | 是 |
| `aprs` | 音频域（NFM 鉴频后）｜噪声带宽 = fs/2 = 24 kHz（全带白噪，未按 1200 bd AFSK 占用带宽归一）｜每样本：整段平均信号功率 / 噪声方差；AFSK 1 bit/符号 | -10..16 dB（14 档 / 20 次） | 0 dB | 2 dB | -2..2 dB | `crc_failed` | 是 |
| `ft4` | 复基带（基带 IQ）｜噪声带宽 = fs/2 = 6 kHz（全带白噪，未按 4-FSK 约 83 Hz 占用带宽归一）｜每样本：整帧（5.04 s）平均信号功率 / 噪声方差；4-FSK 2 bit/符号（LDPC(174,91)） | -24..0 dB（13 档 / 20 次） | -20 dB | -20 dB | -22..-20 dB | `ldpc_parity_failed` | 是 |
| `pocsag` | 音频域（FM 鉴频后）｜噪声带宽 = fs/2 = 11.025 kHz（全带白噪，未按 1200 bd FSK 占用带宽归一）｜每样本：整段（含 576 bit 前导）平均信号功率 / 噪声方差；FSK 1 bit/符号 + BCH(31,21) | 0..18 dB（10 档 / 20 次） | 6 dB | 8 dB | 2..8 dB | `address_not_recovered` | 是 |
| `acars` | 音频域（AM/FM 鉴频后）｜噪声带宽 = fs/2 = 24 kHz（全带白噪，未按 2400 bd MSK 占用带宽归一）｜每样本：整段平均信号功率 / 噪声方差；MSK 1 bit/符号 + 奇校验/CRC-16 | -6..14 dB（6 档 / 20 次） | 2 dB | 2 dB | -2..2 dB | `nothing_detected` | 是 |
| `navtex` | 音频域（NFM 鉴频后）｜噪声带宽 = fs/2 = 6 kHz（fs=12 kS/s，全带白噪，未按 100 bd FSK 占用带宽归一）｜每样本：整段（定相 + 报文）平均信号功率 / 噪声方差；SITOR-B 100 bd，4:7 恒比 + 分集 | 8..40 dB（9 档 / 20 次） | 36 dB | 36 dB | 32..36 dB | `nothing_detected` | 是 |
| `apt` | 音频域（AM 包络解调后）｜噪声带宽 = fs/2 = 12 kHz（fs=24 kS/s，全带白噪，未按 2400 Hz 副载波占用带宽归一）｜每样本：整段平均信号功率 / 噪声方差；AM 2400 Hz 副载波，104 行/min | -8..16 dB（7 档 / 20 次） | 4 dB | 8 dB | 4..8 dB | `no_sync` | 是 |
| `wspr` | 复基带（基带 IQ）｜噪声带宽 = fs/2 = 187.5 Hz（fs=375 S/s，全带白噪，未按 1.46 Hz 符号率归一）｜每样本：整帧（110.592 s）平均信号功率 / 噪声方差；4-FSK 1 bit/符号（K=32 卷积 + 交织） | -32..-8 dB（7 档 / 20 次） | -12 dB | -12 dB | -16..-12 dB | `sync_not_found` | 是 |
| `lrpt` | 复基带（基带 IQ）｜噪声带宽 = fs/2 = 288 kHz（fs=576 kS/s，全带白噪，未按 72 kSym/s 占用带宽归一）｜每样本：整帧平均信号功率 / 噪声方差；QPSK 2 bit/符号（sps=8，64 bit 同步） | -8..16 dB（7 档 / 20 次） | 16 dB | 16 dB | 12..16 dB | `sync_not_found` | 是 |

临界区间 = 「首个成功率 > 0 的档」到「首次 100% 的档」；相邻档成功数允许回落 ≤1 次（20 次/档下的 ±1 次抖动），回落更多即判非单调。每个模式的噪声域/带宽/SNR 口径由同一份 `MODE_BASIS` 生成，改口径必须改 `scripts/decode_sensitivity.py` 并刷新本文件。

### 频偏容差（与 SNR 正交的另一维）

固定各模式自己的高 SNR 档，每偏移 10 次；负/正两翼都扫。`N/A` = 该偏移超过 `0.45·fs`（奈奎斯特不可表示），**不伪造失败**（WSPR fs=375 S/s 只可能扫 0；FT4/NavTex fs=12 kS/s 扫不到 ±10 kHz；POCSAG fs=22.05 kS/s 同理）。

| 模式 | 0 kHz | ±0.5 kHz | ±1 kHz | ±2 kHz | ±5 kHz | ±10 kHz | 临界频偏 | 是否单调 |
|---|---|---|---|---|---|---|---|---|
| `ais` | 100% | 100% | 100% | 0% | 0% | 100% | 1 kHz | 否（边缘效应，未解释机理） |
| `adsb` | 100% | 100% | 100% | 100% | 100% | 100% | 10 kHz（扫描上界） | 是 |
| `aprs` | 100% | 0% | 0% | 0% | 0% | 0% | 0 kHz | 是 |
| `ft4` | 100% | 0% | 0% | 0% | 0% | N/A | 0 kHz | 是 |
| `pocsag` | 100% | 0% | 0% | 0% | 100%/0% | N/A | 0 kHz | 否（边缘效应，未解释机理） |
| `acars` | 100% | 0% | 0% | 0% | 0%/100% | 0% | 0 kHz | 否（边缘效应，未解释机理） |
| `navtex` | 100% | 0% | 0% | 0% | 0% | N/A | 0 kHz | 是 |
| `apt` | 100% | 100% | 100% | 0% | 0% | 100%/20% | 1 kHz | 否（边缘效应，未解释机理） |
| `wspr` | 100% | N/A | N/A | N/A | N/A | N/A | 0 kHz | 是 |
| `lrpt` | 100% | 100% | 100% | 100% | 100% | 100% | 10 kHz（扫描上界） | 是 |

临界频偏 = **从 0 向外连续达标**的最大 |偏移|（两翼取较差侧），不是「所有达标档里的最大值」。AIS 用 1 符号差分相位判决（`angle(x[n+sps]·conj(x[n]))`），恒定频偏只给判决量加一个固定相位旋转；偏移接近符号率整数倍时旋转回到 2π 附近、极性又被判对，于是 ±10 kHz 反而 100%——**那是周期折叠，不是容差**。POCSAG / ACARS / APT 在个别档位也看到同型恢复（-5 kHz 或 +5 kHz 突然回到 100%），一律判非单调、标「边缘效应，未解释机理」，不挑好看档位当结论。

**ADS-B 的两种观测并存**：本表用库内合成 8 MS/s / 3 帧 fixture（`decode_pipeline/adsb_three_frames.cf32`），固定该模式高 SNR 档（2 dB）、每偏移 10 次，±10 kHz 全过（10/10）。另一组用**同一** fixture、SNR 0 dB、6 次/偏置，则出现 0 kHz 5/6、±2 kHz 5/6、±5 kHz 1/6、±10 kHz 5/6、±20 kHz 4/6 的**非单调**（包络检波 + 抽取滤波/位定时边缘效应）。两组只差 SNR 与样本量：临界附近 6 次样本本就抖，**不拿任一组的「恢复」当卖点**，只钉「高 SNR 下容差≥10 kHz、临界附近不稳」。

**POCSAG 的 fixture 敏感性**：上表固定 1200 bd / 地址 54321 / HELLO。探索阶段在同一解码器上换 512/2400 bd 或其他地址时，出现过高速档回落（如 25 dB 反而掉到 17/20）与低于 100% 的平台——未解释机理，故本表只报这一组固定 fixture 的档位，不外推到其他波特率/地址。

### 未做灵敏度基线的模式（缺什么写清楚，不写「已完善」）

- `meteor`：没有可兑现的载荷真值：mbdsdr_ai.meteor_sat 只给 symbols/bits，仓库内无 MSU-MR 图像解码器，成功无法定义（解出 bits ≠ 解出图像）；没有 TU Berlin Meteor 实录 fixture 入库（现有 golden 只有回环）；没有图像质量判据（同步行数/逐像素相关）可供打分

快照日期 2026-11-06，种子 20261104，每档 20 次、每偏移 10 次。**以实跑为准**，过期就重新生成：

```bash
python scripts/decode_sensitivity.py --trials 20 --seed 20261104 \
    --offset-trials 10 \
    --out paper/experiments/decode_sensitivity.json \
    --md paper/experiments/decode_sensitivity.md
```

三条必须一起读的限定：合成波形无多径/无采样时钟误差（频偏是另行单列的正交维度，见上表）；该 SNR 按各模式自己的域与fs/2 噪声带宽定义，不是天线口面 SNR，也不是任何法规门限；它只证明「本实现相对本实现的耐噪性」，不能证明「能在天上收到」。**全部结论的输入都是仓内编码器合成的 fixture（synthetic, NOT OTA）**，不是 OTA 实测。

## 离线批处理与结构化审计

要把一段/一目录捕获按这张表批量跑一遍，用批处理入口（同样离线、不碰硬件）：

```bash
python scripts/decode_captures.py --mode ais --file cap.cf32 --fs 192000 --out report.json
python scripts/decode_captures.py --mode all --dir captures/ --out report.json
```

- 报告逐文件给出 `ok` / `status` / `error_code` / `num_messages` 与 SHA-256；**零报文是合法结果**，不折叠成成功或失败。
- 退出码只表示「报告是否生成成功」，不表示「解出东西没有」。
- 另设 `MBDSDR_DECODE_AUDIT_DIR=logs/` 后，每次解码追加一行 `decode_audit.jsonl`（只记元数据与输入指纹，**不抄报文正文**）。

## 死分支与可达性登记

- **ACARS handler 内的 `unsupported_format` 是死分支**：路由层中央格式门（`decode_router` 的 `input_formats` 检查）先拦，handler 里的 else 分支在当前能力表下不可达；保留只为防御，不计入实际失败路径。
- **`not_implemented` 仅 ft8 通用入口可达**：能力表 `dependency=""`，handler 直接返回该码；其余模式均已接线。
- **`missing_dependency` 现在有真实可达路径**：路由先 `find_spec`，再`importlib.import_module`；环境缺 numpy/scipy 等第三方库时模块文件存在但导入抛 `ImportError`，走该码（不再只能靠 monkeypatch 复现）。
- **路由层运行期校验**：IQ 模式先校验采样率整数倍（ADS-B 1MHz / AIS 9600 / Meteor 72k），再校验「最短一帧」样本量；不足返回 `sample_rate_mismatch` / `insufficient_samples`，绝不 `ok=true` + 0 条。

## 诚实边界

- 「真实」不等于「现在能在你的设备上收到」：外部真值证明解码链正确，能否收到还取决于天线、增益、过境时间与多普勒补偿。
- 标 `not_implemented` 的模式（仅 ft8 通用入口）不会假装解码，调用会得到明确错误码而不是空结果。
- 缺真录的模式（ais/navtex/aprs）在补到真录之前，任何「空口已解码」的宣称都不成立。
- **APRS 已接线但不等于「空口支持」**：`run_aprs_pipeline` 真实执行 NFM 鉴频→Bell202 AFSK→NRZI→HDLC→CRC-16→AX.25，但现有证据仅为仓内合成 fixture（synthetic, NOT OTA）逐字节对拍；不得写成「OTA 验证通过」。失败时给 `nothing_detected` / `truncated_frame` / `crc_failed` 等具体原因码。
- **本矩阵与 UI 都不声称「实时解码」**：解码是离线/同步处理，实时率与跟不上时的后果见 `mbdsdr_ai.decode_pipeline.performance_summary` / `behind_report`；后者明说积压恒为 0 且观测不到 SDR 缓冲层溢出。
- **两种「丢」的用词**：`dropped_frames` 只计 FCS/CRC 校验失败，不包含「跟不上导致样本没被处理」——后者不归本模块计数。

