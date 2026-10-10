// SPDX-License-Identifier: MIT
// lrpt_demod.cc -- QPSK 前段实现（前馈：AGC->CFO 扫描->抽取->硬判决->ASM 相关）。
#include "lrpt_demod.h"
#include "lrpt_fec.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>

namespace mbdsdr {
namespace {
constexpr uint64_t kSyncWord = 0xFCA2B63DB00D9794ULL;
constexpr int kSyncBits = 64;

// 64-bit 同步字 MSB-first 位。
void syncBits(uint8_t* b) {
    for (int i = 0; i < 64; ++i) b[i] = (kSyncWord >> (63 - i)) & 1;
}

// 对 64 位做 QPSK 相位旋转 + swap（镜像 Python _rotate_bits_qpsk）。
void rotateBits(const uint8_t* src, int phaseDeg, bool swap, uint8_t* out) {
    int8_t bI[32], bQ[32];
    for (int i = 0; i < 32; ++i) {
        bI[i] = src[2*i] ? 1 : -1;
        bQ[i] = src[2*i+1] ? 1 : -1;
    }
    if (swap) std::swap(bI, bQ);
    for (int i = 0; i < 32; ++i) {
        int8_t nI, nQ;
        if (phaseDeg == 90)      { nI = -bQ[i]; nQ = bI[i]; }
        else if (phaseDeg == 180){ nI = -bI[i]; nQ = -bQ[i]; }
        else if (phaseDeg == 270){ nI = bQ[i]; nQ = -bI[i]; }
        else                     { nI = bI[i];  nQ = bQ[i]; }
        out[2*i]   = nI > 0 ? 1 : 0;
        out[2*i+1] = nQ > 0 ? 1 : 0;
    }
}

int hamming(const uint8_t* a, const uint8_t* b, int n) {
    int d = 0;
    for (int i = 0; i < n; ++i) d += a[i] ^ b[i];
    return d;
}
} // namespace

std::vector<LrptFrame> lrptDemodulate(const std::complex<float>* iq, int n,
                                      int fs, int symRate) {
    std::vector<LrptFrame> out;
    int sps = fs / symRate;
    if (n < sps * 32) return out;  // 诚实空态

    // 同步假设表（4 相位 × swap）。
    uint8_t base[64], hyp[8][64];
    int hp[8]; bool hsw[8];
    syncBits(base);
    int hi = 0;
    for (int phase : {0, 90, 180, 270})
        for (int sw = 0; sw <= 1; ++sw) {
            rotateBits(base, phase, sw, hyp[hi]);
            hp[hi] = phase; hsw[hi] = sw; ++hi;
        }

    // 1. AGC RMS 归一化。
    double rms = 0;
    for (int i = 0; i < n; ++i) rms += std::norm(iq[i]);
    rms = std::sqrt(rms / n);
    float scale = rms > 1e-9 ? (float)(1.0 / rms) : 1.0f;

    // 2. CFO 粗扫（前馈）：试候选频偏，去旋转后取最小同步汉明距。
    double bestH = 999, bestFreq = 0; int bestOff = 0;
    std::vector<double> cfoCandidates;
    for (double f = -50000; f <= 50000; f += 1000) cfoCandidates.push_back(f);
    cfoCandidates.push_back(0.0);

    for (double f0 : cfoCandidates) {
        double w = 2.0 * M_PI * f0 / fs;
        for (int off = 0; off < sps; ++off) {
            // 去旋转 + 抽取，硬判决前 64 位。
            uint8_t bits[64]; bool have = true;
            for (int k = 0; k < 32; ++k) {
                int idx = off + k * sps;
                if (idx >= n) { have = false; break; }
                double ph = -w * idx;
                float I = iq[idx].real() * scale * (float)std::cos(ph) - iq[idx].imag() * scale * (float)std::sin(ph);
                float Q = iq[idx].real() * scale * (float)std::sin(ph) + iq[idx].imag() * scale * (float)std::cos(ph);
                bits[2*k]   = I > 0 ? 1 : 0;
                bits[2*k+1] = Q > 0 ? 1 : 0;
            }
            if (!have) continue;
            int minH = 64;
            for (int h = 0; h < 8; ++h) minH = std::min(minH, hamming(bits, hyp[h], 64));
            if (minH < bestH) { bestH = minH; bestFreq = f0; bestOff = off; }
        }
    }
    if (bestH > 4) return out;  // 诚实空态：同步不可靠

    // 3. 用最佳 freq/off 生成全位串。
    double w = 2.0 * M_PI * bestFreq / fs;
    int nsym = (n - bestOff) / sps;
    std::vector<uint8_t> bits(nsym * 2);
    for (int k = 0; k < nsym; ++k) {
        int idx = bestOff + k * sps;
        double ph = -w * idx;
        float I = iq[idx].real()*scale*(float)std::cos(ph) - iq[idx].imag()*scale*(float)std::sin(ph);
        float Q = iq[idx].real()*scale*(float)std::sin(ph) + iq[idx].imag()*scale*(float)std::cos(ph);
        bits[2*k]   = I > 0 ? 1 : 0;
        bits[2*k+1] = Q > 0 ? 1 : 0;
    }

    // 4. 滑窗找同步。
    for (int start = 0; start + kSyncBits <= (int)bits.size(); start += 2) {
        int bestHyp = -1, bestD = 64;
        for (int h = 0; h < 8; ++h) {
            int d = hamming(&bits[start], hyp[h], 64);
            if (d < bestD) { bestD = d; bestHyp = h; }
        }
        if (bestD <= 4) {
            LrptFrame fr;
            fr.syncSymbolIndex = start / 2;
            fr.hamming = bestD; fr.phase = hp[bestHyp]; fr.swapped = hsw[bestHyp];
            // 从 start 起的硬位流（c0,c1）做 Viterbi 软判决译码 -> CADU 字节。
            int nPairs = (int)bits.size() - start;
            nPairs = nPairs / 2;
            std::vector<double> soft(nPairs * 2);
            for (int k = 0; k < nPairs * 2; ++k) soft[k] = bits[start + k] ? 1.0 : -1.0;
            auto info = viterbiDecodeK7(soft.data(), nPairs);
            int nb = (int)info.size() / 8;
            fr.caduBytes.resize(nb);
            for (int i = 0; i < nb; ++i) {
                uint8_t byte = 0;
                for (int b = 0; b < 8; ++b) byte = (byte << 1) | (uint8_t)info[i*8 + b];
                fr.caduBytes[i] = byte;
            }
            out.push_back(fr);
            start += kSyncBits;  // 跳过同步字
        }
    }
    return out;
}

} // namespace mbdsdr
