# FT8 第②步落地：LDPC(174,91) + CRC14 + log-domain BP + 77-bit unpack

HEAD=6873cef。干净室自写 MIT，不抄 WSJT-X GPL 代码；机制证据见
`docs/learn/phase63/ft8-mechanism-study.md` §3-4 与 wsjtx 公开源码 file:line。

## 1. 机制 → 实现对照

| 机制 | 实现 file:line |
|---|---|
| CRC14 poly 0x6757（get_crc14.f90:12） | `mbdsdr_ai/ft8_codec.py:46`（CRC14_POLY）、`:105` crc14_bits、`:123` check_crc14 |
| LDPC(174,91) H 矩阵列重 3（ldpc_174_91_c_parity.f90 Mn） | `ft8_codec.py:58` MN_CHECKS、`:165` Ft8Codec.__init__ 建 H |
| 系统编码 p = H_parity⁻¹·H_info·m（GF(2)） | `ft8_codec.py:134` _gf2_invert、`:177` encode |
| log-domain tanh BP + CRC14 早停 + 放弃准则（bpdecode174_91.f90:99-112） | `ft8_codec.py:188` decode（max_iter=50，ncheck=0 验 CRC；ncnt≥5 且 ncheck>15 放弃） |
| 28-bit 呼号打包（pack28.f90:703，iarea=2/3 归一化） | `ft8_codec.py:240` _normalize_callsign、`:252` pack28、`:262` unpack28 |
| 4 字符网格（to_grid4.f90:1644） | `ft8_codec.py:276` pack_grid4、`:283` unpack_grid4 |
| 77-bit 布局 28+1+28+1+1+15+3（packjt77.f90:535） | `ft8_codec.py:293` pack77、`:307` unpack77 |
| tone 能量 → LLR 软判决注入接口 | `ft8_codec.py:335` llrs_from_tone_energies（逆格雷分组，正=bit0） |
| Ft8Modulator.encode_message 接线（第①步占位消除） | `mbdsdr_ai/ft8_modem.py:144` |

## 2. BP 选择理由

选 **log-domain tanh BP**（而非 min-sum）：与 wsjtx 公开机制同构，在 ±5 LLR
量级下收敛稳定；max_iter=50。实测对硬翻转错误的纠错能力见 §3。

## 3. 纠错能力实测（固定 seed=42，20 trials/点）

| 翻转 bit 数 | 恢复成功 |
|---|---|
| 0 | 20/20 |
| 1 | 20/20 |
| 2 | ≥16/20 |
| 3 | ≥10/20 |
| 4+ | 不保证 |

公开文献给 FT8 LDPC(174,91) 最小距离 d_min≈8，理论可纠 t=⌊(d_min-1)/2⌋=3。
实测 BP 对绝大多数 0-3 翻转恢复，但存在少量陷阱集（trapping set）使部分 2-3
翻转模式卡在 ncheck=4 不收敛——这是 BP（非 ML）的固有局限，诚实记录。

## 4. 全链路 SNR 曲线（符号→LLR→BP→CRC，合成闭环）

CSV：`paper/experiments/ft8_step2_msg_decode_vs_snr.csv`（20 trials/点）。

| SNR dB | 消息解码成功率 |
|---|---|
| -25 | 0% |
| -20 | 90% |
| -15 | 100% |
| -10 | 100% |
| -5 | 100% |
| 0 | 100% |

**诚实边界**：此门限为合成 AWGN 闭环（无衰落/多径/真实相位噪声/真实时隙
对齐），与 WSJT-X 真实弱信号链路（~-24 dB  PSD）不直接类比。BP 为干净室
自写，未做 min-sum 优化。

## 5. pytest 计数

`mbdsdr_ai/tests/test_ft8_codec.py`（新）+ `test_ft8_modem.py`（第①步 19，
其中 1 个 NotImplementedError 断言改为接线断言）= **39 passed**。

断言覆盖：CRC 自洽/翻转检出、编码 round-trip、H 列重 3、bitflip 扫描、
纯噪声 LLR→None、全 0 LLR→退化全 0 码字（诚实标注）、呼号/网格 round-trip、
pack77/unpack77、非标消息拒绝、LLR 确定性、encode_message 接线、全链路
编码→BP→unpack。

## 6. 诚实未完成项（第③步）

- C++ 移植（cpp/* 本轮隔离未碰）；
- 真实时隙对齐（79 符号 = 12.64 s 的 15 s 窗内多帧检测）；
- SIC（连续干扰消除，多信号同框）；
- 呼号哈希回退分支（pack28.f90: 非标准呼号走 22-bit 哈希，本轮仅标准呼号）；
- min-sum 优化以提升陷阱集收敛率；
- 真实接收机前端数据验证。
