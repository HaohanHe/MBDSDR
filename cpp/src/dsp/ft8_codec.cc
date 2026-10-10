// SPDX-License-Identifier: MIT
// ft8_codec.cc -- FT8 C++ 解码层实现（CRC14 + LDPC tanh BP + 77-bit unpack）。
#include "ft8_codec.h"

#include <cmath>
#include <vector>

namespace mbdsdr {

namespace {
constexpr int kN = 174, kK = 91, kM = 83;
constexpr int kMN[174][3] = {
    {16,45,73},{25,51,62},{33,58,78},{1,44,45},{2,7,61},{3,6,54},{4,35,48},
    {5,13,21},{8,56,79},{9,64,69},{10,19,66},{11,36,60},{12,37,58},{14,32,43},
    {15,63,80},{17,28,77},{18,74,83},{22,53,81},{23,30,34},{24,31,40},{26,41,76},
    {27,57,70},{29,49,65},{3,38,78},{5,39,82},{46,50,73},{51,52,74},{55,71,72},
    {44,67,72},{43,68,78},{1,32,59},{2,6,71},{4,16,54},{7,65,67},{8,30,42},
    {9,22,31},{10,18,76},{11,23,82},{12,28,61},{13,52,79},{14,50,51},{15,81,83},
    {17,29,60},{19,33,64},{20,26,73},{21,34,40},{24,27,77},{25,55,58},{35,53,66},
    {36,48,68},{37,46,75},{38,45,47},{39,57,69},{41,56,62},{20,49,53},{46,52,63},
    {45,70,75},{27,35,80},{1,15,30},{2,68,80},{3,36,51},{4,28,51},{5,31,56},
    {6,20,37},{7,40,82},{8,60,69},{9,10,49},{11,44,57},{12,39,59},{13,24,55},
    {14,21,65},{16,71,78},{17,30,76},{18,25,80},{19,61,83},{22,38,77},{23,41,50},
    {7,26,58},{29,32,81},{33,40,73},{18,34,48},{13,42,64},{5,26,43},{47,69,72},
    {54,55,70},{45,62,68},{10,63,67},{14,66,72},{22,60,74},{35,39,79},{1,46,64},
    {1,24,66},{2,5,70},{3,31,65},{4,49,58},{1,4,5},{6,60,67},{7,32,75},{8,48,82},
    {9,35,41},{10,39,62},{11,14,61},{12,71,74},{13,23,78},{11,35,55},{15,16,79},
    {7,9,16},{17,54,63},{18,50,57},{19,30,47},{20,64,80},{21,28,69},{22,25,43},
    {13,22,37},{2,47,51},{23,54,74},{26,34,72},{27,36,37},{21,36,63},{29,40,44},
    {19,26,57},{3,46,82},{14,15,58},{33,52,53},{30,43,52},{6,9,52},{27,33,65},
    {25,69,73},{38,55,83},{20,39,77},{18,29,56},{32,48,71},{42,51,59},{28,44,79},
    {34,60,62},{31,45,61},{46,68,77},{6,24,76},{8,10,78},{40,41,70},{17,50,53},
    {42,66,68},{4,22,72},{36,64,81},{13,29,47},{2,8,81},{56,67,73},{5,38,50},
    {12,38,64},{59,72,80},{3,26,79},{45,76,81},{1,65,74},{7,18,77},{11,56,59},
    {14,39,54},{16,37,66},{10,28,55},{15,60,70},{17,25,82},{20,30,31},{12,67,68},
    {23,75,80},{27,32,62},{24,69,75},{19,21,71},{34,53,61},{35,46,47},{33,59,76},
    {40,43,83},{41,42,63},{49,75,83},{20,44,48},{42,49,57},
};
constexpr int kPoly[15] = {1,1,0,0,1,1,1,0,1,0,1,0,1,1,1};
constexpr char kA1[38] = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
constexpr char kA2[38] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
constexpr char kA3[12] = "0123456789";
constexpr char kA4[29] = " ABCDEFGHIJKLMNOPQRSTUVWXYZ";
constexpr long long kNTOKENS = 2063592, kMAX22 = 4194304;

std::string unpack28(long long n28) {
    long long n = n28 - kNTOKENS - kMAX22;
    if (n < 0) return "???";
    int i1 = n / (360LL * 19683); n %= (360LL * 19683);
    int i2 = n / (10LL * 19683); n %= (10LL * 19683);
    int i3 = n / 19683; n %= 19683;
    int i4 = n / 729; n %= 729;
    int i5 = n / 27; int i6 = n % 27;
    std::string s;
    s += kA1[i1]; s += kA2[i2]; s += kA3[i3];
    s += kA4[i4]; s += kA4[i5]; s += kA4[i6];
    while (!s.empty() && s.back() == ' ') s.pop_back();
    while (!s.empty() && s.front() == ' ') s.erase(0, 1);
    return s;
}
} // namespace

Ft8Codec::Ft8Codec() {
    checkVars_.resize(kM);
    for (int v = 0; v < kN; ++v)
        for (int j = 0; j < 3; ++j)
            checkVars_[kMN[v][j] - 1].push_back(v);
}

bool Ft8Codec::checkCrc14(const int* bits91, int n) {
    if (n != kK) return false;
    int reg[15];
    for (int i = 0; i < 15; ++i) reg[i] = bits91[i];
    for (int i = 0; i < 77; ++i) {
        reg[14] = (i + 14 < 77) ? bits91[i + 14] : 0;
        int fb = reg[0];
        if (fb) for (int j = 0; j < 15; ++j) reg[j] ^= kPoly[j];
        for (int j = 0; j < 14; ++j) reg[j] = reg[j + 1];
        reg[14] = fb;
    }
    for (int i = 0; i < 14; ++i)
        if (reg[i] != bits91[77 + i]) return false;
    return true;
}

std::optional<Ft8Decoded> Ft8Codec::decode(const double* llr, std::size_t n,
                                          int maxIter) const {
    if (n != (std::size_t)kN) return std::nullopt;
    std::vector<std::vector<double>> r(kM);   // check j -> var ei 的消息
    for (int j = 0; j < kM; ++j) r[j].assign(checkVars_[j].size(), 0.0);
    std::vector<double> tov(kN, 0.0);         // = Σ_j r[j][v]
    std::vector<int> cw(kN);
    int nclast = 0, ncnt = 0;
    for (int it = 0; it <= maxIter; ++it) {
        for (int v = 0; v < kN; ++v) cw[v] = (llr[v] + tov[v] < 0) ? 1 : 0;
        int ncheck = 0;
        for (int j = 0; j < kM; ++j) {
            int s = 0; for (int v : checkVars_[j]) s ^= cw[v];
            ncheck += s;
        }
        if (ncheck == 0 && checkCrc14(cw.data(), kK)) {
            Ft8Decoded d; d.ok = true;
            auto field = [&](int lo, int w) { long long v = 0; for (int k = 0; k < w; ++k) v = (v << 1) | cw[lo + k]; return v; };
            long long n28a = field(0, 28), n28b = field(29, 28);
            d.report = field(58, 1) != 0;
            long long igrid = field(59, 15);
            int i3 = field(74, 3);
            if (i3 != 1 && i3 != 2) return std::nullopt;
            d.from = unpack28(n28a); d.to = unpack28(n28b);
            if (d.from == "???" || d.to == "???") return std::nullopt;
            if (igrid <= 32400) {
                int j1 = igrid / 1800, j2 = (igrid % 1800) / 100;
                int j3 = (igrid % 100) / 10, j4 = igrid % 10;
                d.exchange = std::string(1, (char)('A' + j1)) + (char)('A' + j2)
                           + std::to_string(j3) + std::to_string(j4);
            } else {
                d.exchange = "R" + std::to_string(igrid - 32400);
            }
            return d;
        }
        if (it > 0) {
            int nd = ncheck - nclast;
            ncnt = (nd < 0) ? 0 : ncnt + 1;
            if (ncnt >= 5 && it >= 10 && ncheck > 15) return std::nullopt;
        }
        nclast = ncheck;
        std::vector<double> newTov(kN, 0.0);
        for (int j = 0; j < kM; ++j) {
            int m = (int)checkVars_[j].size();
            std::vector<double> t(m);
            for (int ei = 0; ei < m; ++ei) {
                int v = checkVars_[j][ei];
                double q = llr[v] + tov[v] - r[j][ei];
                t[ei] = std::tanh(std::max(-20.0, std::min(20.0, q / 2.0)));
            }
            for (int ei = 0; ei < m; ++ei) {
                double prod = 1.0;
                for (int k = 0; k < m; ++k) if (k != ei) prod *= t[k];
                double at = std::max(-0.999999, std::min(0.999999, prod));
                double nr = 2.0 * std::atanh(at);
                r[j][ei] = nr;
                newTov[checkVars_[j][ei]] += nr;
            }
        }
        tov = std::move(newTov);
    }
    return std::nullopt;
}

} // namespace mbdsdr
