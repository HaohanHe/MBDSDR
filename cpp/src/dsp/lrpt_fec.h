// SPDX-License-Identifier: MIT
// lrpt_fec.h -- LRPT CCSDS 解码链（干净室自写 MIT，规范 mbdsdr_ai/lrpt_fec.py）。
#pragma once

#include <cstdint>
#include <optional>
#include <vector>

namespace mbdsdr {

// Viterbi K=7 r=1/2 CCSDS {79,109} 软判决译码。
// soft: c0,c1,c0,c1,... 软位（正=bit1）。返回译码信息位（不含 6 尾比特）。
std::vector<int> viterbiDecodeK7(const double* soft, int nPairs);

// CCSDS 字节级解扰（PN x^8+x^7+x^5+x^3+1 init 0xff，周期 255）。
std::vector<uint8_t> ccsdsDescramble(const uint8_t* data, int n);

// RS(255,223) fcr=112 prim=1 ccsds_invert。
// 返回纠正后 223 字节；不可纠返回 nullopt。
std::optional<std::vector<uint8_t>> rsDecode255(const uint8_t* codeword);

// CADU 解码：Viterbi 字节流（含 4 字节 ASM）-> 解扰 -> 4 路解交织 -> RS×4。
// 全 4 块可纠返回 892 字节 payload；否则 nullopt（诚实空态）。
std::optional<std::vector<uint8_t>> lrptDecodeCadu(const uint8_t* viterbiBytes, int n);

} // namespace mbdsdr
