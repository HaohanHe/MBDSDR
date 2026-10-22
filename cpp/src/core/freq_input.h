// SPDX-License-Identifier: MIT
//
// core/freq_input.h -- 人类友好频率输入解析（仪器民主化：说人话就能调台）。
//
// 语义（无歧义、可解释）：
//   "137.62M" / "137.62MHz"  -> 137.62 MHz
//   "7050k"   / "7050kHz"    -> 7.05 MHz
//   "1.42G"   / "1.42GHz"    -> 1420 MHz
//   "145.5"                  -> 145.5 MHz（裸数 = MHz，与调谐框 " MHz" 口径一致）
//   "7050000hz" / "7050000Hz"-> 7.05 MHz（裸数 + hz 后缀 = Hz）
// 失败一律诚实报错（返回 false + 人话错误），绝不猜测、绝不静默夹值。
#pragma once

#include <QString>

namespace mbdsdr {
namespace core {

/// 解析人类友好频率文本，输出 Hz。失败返回 false 并写 errOut（可空）。
bool parseHumanFrequency(const QString& text, double* hzOut, QString* errOut);

} // namespace core
} // namespace mbdsdr
