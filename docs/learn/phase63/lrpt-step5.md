# LRPT 线 A 第⑤轮：QPSK 前段 C++ 移植（IQ→CADU 全链闭环）

HEAD=f7e7874。干净室自写 MIT，规范 `mbdsdr_ai/lrpt_modem.py` + `lrpt_pipeline.py`。

## 1. 落地 file:line

| 组件 | file:line |
|---|---|
| QPSK 解调（AGR RMS→CFO 粗扫→8SPS 抽取→硬判决→64-bit ASM 滑窗） | `cpp/src/dsp/lrpt_demod.cc:42` |
| 同步假设表（4 相位 × I/Q swap） | `cpp/src/dsp/lrpt_demod.cc:18` |
| 帧内 Viterbi 硬位流→CADU 字节 | `cpp/src/dsp/lrpt_demod.cc:150` |
| 全链 IQ→payload：demod + lrptDecodeCadu | `cpp/tests/test_lrpt_cpp.cpp:80` |

## 2. 跨语言 round-trip 结论（test_lrpt_cpp 8 passed）

Python `LrptE2EPipeline.build_tx_iq(seed=123 payload)` 合成确定性 IQ
（576kS/s，8 SPS，零频偏/无时偏/无噪）落盘 `lrpt_e2e_iq.raw`；
C++ `lrptDemodulate` 检出帧（hamming=0，phase=180，32 符号对齐）→
Viterbi → CADU（前 4B ASM `1a cf fc 1d`）→ `lrptDecodeCadu` →
**892B payload 与 Python 基准逐字节一致**（`QCOMPARE(*d, exp)`）。

- 纯零 IQ → 空 frames（诚实空态）。
- 频偏/时偏容忍：CFO 粗扫 ±50kHz@1kHz 步长 + 8 采样偏移穷举选最佳同步汉明距；
  本测试向量零频偏基线；真实弱频偏（<5kHz）机制可捕获，未做量化 SNR 扫描。

## 3. vfo 喂数结论

`feedLrptCadu` 入口已留（第④轮）。C++ 全链当前接收 576kS/s 复基带；
真实 vfo channelizer 输出需重采样到 576k/72k=8SPS 域，本轮未接 run-loop
（接入点限制如实标注：48k 引擎域需整数倍重采样到 576k，留后续轮）。

## 4. 测试计数

test_lrpt_cpp **8 passed**（caduDecodesByteForByte / pureNoiseCaduHonestEmpty /
viterbiKnownVector / engineFeedDrivesReadback / iqToPayloadRoundTrip /
pureNoiseDemodHonestEmpty + init/cleanup）。金集 94 保持；mobile catalog 55 保持。

## 5. 诚实未完成项

- vfo run-loop 真实喂数（重采样到 576kS/s 域）未接；
- Costas 符号速率环精跟踪（Python 原型也未闭环，前馈同步已足够）；
- SNR 扫描与频偏/时偏量化容忍未做实验；
- 与真实 Meteor-M 接收链路不直接类比。
