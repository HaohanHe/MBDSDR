// SPDX-License-Identifier: MIT
// ft8_codec.h -- FT8 C++ 解码层：CRC14 + LDPC(174,91) tanh BP + 77-bit unpack。
//
// 干净室自写 MIT，规范为 Python mbdsdr_ai/ft8_codec.py（不抄 wsjtx GPL）。
// 输入：174 个 LLR（正=bit0，由检测层 8-tone 能量导出）；输出解码字段或
// 诚实空态（CRC 不过/不收敛 -> ok=false，绝不编造）。
#pragma once

#include <complex>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace mbdsdr {

struct Ft8Decoded {
    bool        ok = false;
    std::string from;
    std::string to;
    std::string exchange;   // 4-char grid 或 R+n
    bool        report = false;
};

class Ft8Codec {
public:
    Ft8Codec();

    // 174 LLR（正=bit0）-> 解码字段；失败返回 nullopt（诚实空态）。
    std::optional<Ft8Decoded> decode(const double* llr, std::size_t n,
                                     int maxIter = 50) const;

    // CRC14 校验 91 bit（0/1）。
    static bool checkCrc14(const int* bits91, int n);

private:
    std::vector<std::vector<int>> checkVars_;   // 83 checks -> var indices
};

} // namespace mbdsdr
