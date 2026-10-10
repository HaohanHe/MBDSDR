// SPDX-License-Identifier: MIT
// lrpt_demod.h -- LRPT QPSK 前段（IQ->CADU 字节流），规范 mbdsdr_ai/lrpt_modem.py。
#pragma once

#include <complex>
#include <cstdint>
#include <optional>
#include <vector>

namespace mbdsdr {

// 一帧解调结果。
struct LrptFrame {
    int syncSymbolIndex = 0;
    int hamming = 0;
    int phase = 0;
    bool swapped = false;
    std::vector<uint8_t> caduBytes;  // 含 4 字节 ASM 的 1024 字节（成功定位时）
};

// IQ -> QPSK 解调 + 64-bit ASM 帧同步 -> CADU 字节流。
// fs/sym_rate 默认 576000/72000（8 SPS）。无信号/纯噪声 -> 空 vector（诚实空态）。
std::vector<LrptFrame> lrptDemodulate(const std::complex<float>* iq, int n,
                                      int fs = 576000, int symRate = 72000);

} // namespace mbdsdr
