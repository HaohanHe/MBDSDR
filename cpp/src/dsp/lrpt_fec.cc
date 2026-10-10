// SPDX-License-Identifier: MIT
// lrpt_fec.cc -- CCSDS Viterbi + 解扰 + RS(255,223) 实现（规范 lrpt_fec.py/fec.py）。
#include "lrpt_fec.h"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace mbdsdr {

namespace {
constexpr int K = 7, NSTATES = 64, POLY1 = 79, POLY2 = 109;

// CCSDS 字节级 PN 表（fec.py CCSDS_PN，周期 255）。
constexpr uint8_t kPN[255] = {
    0xff,0x48,0x0e,0xc0,0x9a,0x0d,0x70,0xbc,0x8e,0x2c,0x93,0xad,0xa7,0xb7,0x46,0xce,
    0x5a,0x97,0x7d,0xcc,0x32,0xa2,0xbf,0x3e,0x0a,0x10,0xf1,0x88,0x94,0xcd,0xea,0xb1,
    0xfe,0x90,0x1d,0x81,0x34,0x1a,0xe1,0x79,0x1c,0x59,0x27,0x5b,0x4f,0x6e,0x8d,0x9c,
    0xb5,0x2e,0xfb,0x98,0x65,0x45,0x7e,0x7c,0x14,0x21,0xe3,0x11,0x29,0x9b,0xd5,0x63,
    0xfd,0x20,0x3b,0x02,0x68,0x35,0xc2,0xf2,0x38,0xb2,0x4e,0xb6,0x9e,0xdd,0x1b,0x39,
    0x6a,0x5d,0xf7,0x30,0xca,0x8a,0xfc,0xf8,0x28,0x43,0xc6,0x22,0x53,0x37,0xaa,0xc7,
    0xfa,0x40,0x76,0x04,0xd0,0x6b,0x85,0xe4,0x71,0x64,0x9d,0x6d,0x3d,0xba,0x36,0x72,
    0xd4,0xbb,0xee,0x61,0x95,0x15,0xf9,0xf0,0x50,0x87,0x8c,0x44,0xa6,0x6f,0x55,0x8f,
    0xf4,0x80,0xec,0x09,0xa0,0xd7,0x0b,0xc8,0xe2,0xc9,0x3a,0xda,0x7b,0x74,0x6c,0xe5,
    0xa9,0x77,0xdc,0xc3,0x2a,0x2b,0xf3,0xe0,0xa1,0x0f,0x18,0x89,0x4c,0xde,0xab,0x1f,
    0xe9,0x01,0xd8,0x13,0x41,0xae,0x17,0x91,0xc5,0x92,0x75,0xb4,0xf6,0xe8,0xd9,0xcb,
    0x52,0xef,0xb9,0x86,0x54,0x57,0xe7,0xc1,0x42,0x1e,0x31,0x12,0x99,0xbd,0x56,0x3f,
    0xd2,0x03,0xb0,0x26,0x83,0x5c,0x2f,0x23,0x8b,0x24,0xeb,0x69,0xed,0xd1,0xb3,0x96,
    0xa5,0xdf,0x73,0x0c,0xa8,0xaf,0xcf,0x82,0x84,0x3c,0x62,0x25,0x33,0x7a,0xac,0x7f,
    0xa4,0x07,0x60,0x4d,0x06,0xb8,0x5e,0x47,0x16,0x49,0xd6,0xd3,0xdb,0xa3,0x67,0x2d,
    0x4b,0xbe,0xe6,0x19,0x51,0x5f,0x9f,0x05,0x08,0x78,0xc4,0x4a,0x66,0xf5,0x58};

// GF(256) 表（prim 0x187）。
uint8_t gfExp[512], gfLog[256];
bool gfInit_ = false;
void gfInit() {
    if (gfInit_) return;
    int x = 1;
    for (int i = 0; i < 255; ++i) {
        gfExp[i] = (uint8_t)x; gfLog[x] = (uint8_t)i;
        x <<= 1; if (x & 0x100) x ^= (0x187 | 0x100);
    }
    for (int i = 255; i < 512; ++i) gfExp[i] = gfExp[i - 255];
    gfInit_ = true;
}
uint8_t gfMul(int a, int b) {
    if (!a || !b) return 0;
    return gfExp[gfLog[a] + gfLog[b]];
}
uint8_t gfDiv(int a, int b) {
    if (!a) return 0;
    return gfExp[(gfLog[a] - gfLog[b] + 255) % 255];
}
uint8_t gfPow(int a, int n) {
    if (n == 0) return 1;
    return gfExp[(gfLog[a] * n) % 255];
}
} // namespace

std::vector<int> viterbiDecodeK7(const double* soft, int nPairs) {
    // 转移表
    int nextSt[NSTATES][2]; uint8_t outTab[NSTATES][2][2];
    for (int s = 0; s < NSTATES; ++s)
        for (int b = 0; b < 2; ++b) {
            int c0 = b, c1 = b;
            for (int i = 0; i < K - 1; ++i) {
                int ui = (s >> i) & 1;
                if ((POLY1 >> (i + 1)) & 1) c0 ^= ui;
                if ((POLY2 >> (i + 1)) & 1) c1 ^= ui;
            }
            outTab[s][b][0] = c0; outTab[s][b][1] = c1;
            nextSt[s][b] = ((s << 1) | b) & (NSTATES - 1);
        }
    std::vector<double> pm(NSTATES, -1e18), nm(NSTATES);
    pm[0] = 0.0;
    std::vector<std::vector<int>> hist(nPairs, std::vector<int>(NSTATES, 0));
    for (int t = 0; t < nPairs; ++t) {
        double s0 = soft[2*t], s1 = soft[2*t+1];
        std::fill(nm.begin(), nm.end(), -1e18);
        for (int s = 0; s < NSTATES; ++s) {
            if (pm[s] < -1e17) continue;
            for (int b = 0; b < 2; ++b) {
                int ns = nextSt[s][b];
                double reward = (2*outTab[s][b][0]-1)*s0 + (2*outTab[s][b][1]-1)*s1;
                double tot = pm[s] + reward;
                if (tot > nm[ns]) { nm[ns] = tot; hist[t][ns] = s; }
            }
        }
        pm.swap(nm);
    }
    int bestEnd = 0; double bestV = -1e18;
    for (int s = 0; s < NSTATES; ++s) if (pm[s] > bestV) { bestV = pm[s]; bestEnd = s; }
    std::vector<int> decoded(nPairs);
    int cur = bestEnd;
    for (int t = nPairs - 1; t >= 0; --t) {
        int prev = hist[t][cur];
        decoded[t] = cur & 1;
        cur = prev;
    }
    if (nPairs > K - 1) decoded.resize(nPairs - (K - 1));
    return decoded;
}

std::vector<uint8_t> ccsdsDescramble(const uint8_t* data, int n) {
    std::vector<uint8_t> out(n);
    for (int i = 0; i < n; ++i) out[i] = data[i] ^ kPN[i % 255];
    return out;
}

std::optional<std::vector<uint8_t>> rsDecode255(const uint8_t* codeword) {
    gfInit();
    constexpr int Kk = 223, NSYM = 32, FCR = 112;
    std::vector<uint8_t> rcvd(255);
    for (int i = 0; i < 255; ++i) rcvd[i] = codeword[i] ^ 0xFF;  // ccsds_invert
    // 伴随式
    std::vector<int> synd(NSYM, 0);
    for (int i = 0; i < NSYM; ++i) {
        uint8_t root = gfPow(2, FCR + i);
        int s = 0;
        for (int byte : rcvd) s = gfMul(s, root) ^ byte;
        synd[i] = s;
    }
    bool any = false;
    for (int s : synd) if (s) { any = true; break; }
    auto cleanOut = [&]() -> std::vector<uint8_t> {
        std::vector<uint8_t> d(Kk);
        for (int i = 0; i < Kk; ++i) d[i] = rcvd[i] ^ 0xFF;
        return d;
    };
    if (!any) return cleanOut();
    // Berlekamp-Massey
    std::vector<int> C = {1}, B = {1}; int L = 0, m = 1, b = 1;
    for (int N = 0; N < NSYM; ++N) {
        int d = synd[N];
        for (int i = 1; i <= L; ++i) d ^= gfMul(C[i], synd[N - i]);
        if (d == 0) { ++m; }
        else if (2*L <= N) {
            std::vector<int> T = C;
            int coef = gfDiv(d, b);
            int need = (int)B.size() + m;
            if ((int)C.size() < need) C.resize(need, 0);
            for (int i = 0; i < (int)B.size(); ++i) C[i + m] ^= gfMul(coef, B[i]);
            L = N + 1 - L; B = T; b = d; m = 1;
        } else {
            int coef = gfDiv(d, b);
            int need = (int)B.size() + m;
            if ((int)C.size() < need) C.resize(need, 0);
            for (int i = 0; i < (int)B.size(); ++i) C[i + m] ^= gfMul(coef, B[i]);
            ++m;
        }
    }
    // Chien search
    std::vector<int> errPos;
    for (int j = 0; j < 255; ++j) {
        int val = 0, xn = 1;
        for (int i = 0; i < (int)C.size(); ++i) {
            val ^= gfMul(C[i], xn);
            xn = gfMul(xn, gfPow(2, j));
        }
        if (val == 0) errPos.push_back((j - 1 + 255) % 255);
    }
    int nErr = (int)errPos.size();
    if (nErr == 0 || nErr > NSYM / 2) return std::nullopt;
    // Forney
    std::vector<int> omega(NSYM, 0);
    for (int i = 0; i < NSYM; ++i) {
        int acc = 0;
        for (int j = 0; j < std::min(i + 1, (int)C.size()); ++j) acc ^= gfMul(synd[i - j], C[j]);
        omega[i] = acc;
    }
    std::vector<int> deriv(std::max(0, (int)C.size() - 1), 0);
    for (int i = 1; i < (int)C.size(); ++i) if (i % 2 == 1) deriv[i - 1] = C[i];
    std::vector<uint8_t> out = rcvd;
    for (int pos : errPos) {
        int d = 254 - pos;
        uint8_t Xk = gfPow(2, d);
        uint8_t XkInv = gfDiv(1, Xk);
        int num = 0, xn = 1;
        for (int i = 0; i < NSYM; ++i) { num ^= gfMul(omega[i], xn); xn = gfMul(xn, XkInv); }
        int den = 0; xn = 1;
        for (int i = 0; i < (int)deriv.size(); ++i) { den ^= gfMul(deriv[i], xn); xn = gfMul(xn, XkInv); }
        if (den == 0) return std::nullopt;
        int mag = gfDiv(num, den);
        mag = gfMul(mag, gfPow(Xk, 1 - FCR + 255));
        out[pos] ^= mag;
    }
    std::vector<uint8_t> data(Kk);
    for (int i = 0; i < Kk; ++i) data[i] = out[i] ^ 0xFF;
    return data;
}

std::optional<std::vector<uint8_t>> lrptDecodeCadu(const uint8_t* vb, int n) {
    if (n < 1024) return std::nullopt;
    // 跳 4 字节 ASM，1020 字节解扰
    std::vector<uint8_t> der = ccsdsDescramble(vb + 4, 1020);
    // 4 路解交织
    std::vector<std::vector<uint8_t>> blocks(4, std::vector<uint8_t>(255));
    for (int i = 0; i < 1020; ++i) blocks[i % 4][i / 4] = der[i];
    std::vector<uint8_t> payload;
    for (auto& blk : blocks) {
        auto r = rsDecode255(blk.data());
        if (!r) return std::nullopt;   // 全 4 块可纠才算有效
        payload.insert(payload.end(), r->begin(), r->end());
    }
    return payload;
}

} // namespace mbdsdr
