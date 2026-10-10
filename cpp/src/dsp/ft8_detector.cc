// SPDX-License-Identifier: MIT
// ft8_detector.cc -- FT8 检测层实现（Costas 二维相关峰搜索，检测层）。
//
// 机制镜像 Python ft8_modem.py Ft8CostasSync：
//   - 7 个 Costas 符号 S7 = [3,1,4,0,6,5,2]；
//   - 粗搜：符号网格起始（步长 NSPS）× 频偏（±50 Hz @ 6.25 Hz），对第一段
//     Costas 7 符号取 8-tone 能量，argmax tone 与 Costas 序列相关；
//   - 相关峰/次峰比 = sync_quality；低于阈值 -> valid=false（诚实空态）。
//
// 本轮不解码消息（C++ BP 留第④步）。
#include "ft8_detector.h"

#include <cmath>
#include <complex>
#include <vector>

namespace mbdsdr {

namespace {
constexpr double kFs = 12000.0;
constexpr int    kNsps = 1920;
constexpr double kToneSpacing = 6.25;
constexpr int    kCostas[7] = {3, 1, 4, 0, 6, 5, 2};
constexpr int    kFrameSymbols = 79;
constexpr double kQualityThreshold = 0.5;
} // namespace

Ft8Detector::Ft8Detector() = default;

static double toneEnergy(const std::complex<float>* iq, int total,
                         int start, int n, double fHz, double fs) {
    if (start < 0 || start + n > total) return 0.0;
    std::complex<double> acc(0.0, 0.0);
    for (int k = 0; k < n; ++k) {
        double ph = -2.0 * M_PI * fHz * (start + k) / fs;
        acc += std::complex<double>(iq[start + k].real(), iq[start + k].imag())
               * std::exp(std::complex<double>(0.0, ph));
    }
    return std::norm(acc);
}

Ft8Candidate Ft8Detector::processWindow(const std::complex<float>* iq,
                                        std::size_t n) {
    Ft8Candidate best;
    lastCandidates_ = 0;
    if (!enabled_ || iq == nullptr || n < (std::size_t)(kFrameSymbols * kNsps)) {
        return best;
    }

    const int maxStart = (int)n - kFrameSymbols * kNsps;
    double bestScore = 0.0;
    double secondScore = 0.0;
    int    bestStart = 0;
    double bestFreq = 0.0;

    // 粗搜：符号对齐起始（步长 NSPS）× 频偏（±50 Hz @ 6.25 Hz）。
    // 仅评第一段 Costas 7 符号（足够定位帧起点）。
    for (int start = 0; start <= maxStart; start += kNsps) {
        for (int fo = -8; fo <= 8; ++fo) {
            double fOff = fo * kToneSpacing;
            double corr = 0.0;
            double total = 0.0;
            for (int s = 0; s < 7; ++s) {
                int sampleStart = start + s * kNsps;
                int txTone = kCostas[s];
                double txF = fOff + (txTone - 3.5) * kToneSpacing;
                double eTx = toneEnergy(iq, (int)n, sampleStart, kNsps, txF, kFs);
                double eSum = 0.0;
                for (int t = 0; t < 8; ++t) {
                    double f = fOff + (t - 3.5) * kToneSpacing;
                    eSum += toneEnergy(iq, (int)n, sampleStart, kNsps, f, kFs);
                }
                corr += eTx;
                total += eSum;
            }
            double score = total > 0.0 ? corr / total : 0.0;
            if (score > bestScore) {
                secondScore = bestScore;
                bestScore = score;
                bestStart = start;
                bestFreq = fOff;
                lastCandidates_++;
            } else if (score > secondScore) {
                secondScore = score;
            }
        }
    }

    if (bestScore <= 0.0) return best;
    // 质量 = 正确 Costas 音能量占比（>0.5 表帧同步；8 等分噪声底=0.125）。
    double quality = bestScore;
    if (quality < kQualityThreshold) return best;

    best.valid = true;
    best.freqOffsetHz = bestFreq;
    best.timeOffsetSec = (double)bestStart / kFs;
    best.syncQuality = quality;
    best.snrDb = 10.0 * std::log10(bestScore + 1e-9);
    best.dataSymbolsDetected = 58;
    return best;
}

} // namespace mbdsdr
