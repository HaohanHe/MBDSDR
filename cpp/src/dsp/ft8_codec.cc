// SPDX-License-Identifier: MIT
// ft8_codec.cc -- FT8 C++ 解码层实现（CRC14 + LDPC 归一化 min-sum BP + 77-bit unpack）。
// LDPC 消息更新与 Python mbdsdr_ai/ft8_ldpc.ldpc_bp_decode 同源：归一化
// min-sum（因子 0.75）、flooding 并行调度、同款早停。此前用精确 tanh-BP，
// 在环图弱信号上锁进非码字不动点（WSJT-X 官方样本 -14/-15dB 三行实测
// ncheck 卡死 31/3/36，400 轮不收敛——迭代上限无关）；0.75 归一化即阻尼，
// 三行 1~6 轮收敛。跨实现对拍的意义是同一算法双侧一致，见
// tests/test_ft8_wsjtx_cross.cpp 与 tests/test_ft8_cross_impl_fixture.py。
#include "ft8_codec.h"

#include <cmath>
#include <cstdio>
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
    // WSJT-X CRC14 在 96 bit 块上求余：77 bit 消息 + 5 bit 零填充 + 14 bit CRC。
    // 与 Python ft8_decode.check_crc14 同构；不得只把 77 bit 消息直接送进寄存器。
    int mc[96];
    for (int i = 0; i < 77; ++i) mc[i] = bits91[i] & 1;
    for (int i = 77; i < 82; ++i) mc[i] = 0;
    for (int i = 82; i < 96; ++i) mc[i] = bits91[77 + i - 82] & 1;
    int reg[15];
    for (int i = 0; i < 15; ++i) reg[i] = mc[i];
    for (int i = 0; i < 82; ++i) {
        reg[14] = mc[i + 14];
        int fb = reg[0];
        if (fb) for (int j = 0; j < 15; ++j) reg[j] ^= kPoly[j];
        for (int j = 0; j < 14; ++j) reg[j] = reg[j + 1];
        reg[14] = fb;
    }
    for (int i = 0; i < 14; ++i)
        if (reg[i] != 0) return false;
    return true;
}

std::optional<Ft8Decoded> Ft8Codec::decode(const double* llr, std::size_t n,
                                          int maxIter) const {
    if (n != (std::size_t)kN) return std::nullopt;
    std::vector<std::vector<double>> r(kM);   // check j -> var ei 的消息
    for (int j = 0; j < kM; ++j) r[j].assign(checkVars_[j].size(), 0.0);
    std::vector<double> tov(kN, 0.0);         // = Σ_j r[j][v]
    std::vector<int> cw(kN, 0);
    // 早停判据与 Python ft8_ldpc 同款：连续 5 轮未下降、已迭代 >=10 轮且
    // 残差仍 >15 则提前退出（只省无效迭代，不改变收敛路径）。
    int nclast = -1, ncnt = 0;
    for (int it = 0; it < maxIter; ++it) {
        // check -> var：归一化 min-sum（因子 0.75，flooding 并行调度）
        std::vector<double> newTov(kN, 0.0);
        for (int j = 0; j < kM; ++j) {
            const int m = (int)checkVars_[j].size();
            std::vector<double> q(m), aq(m);
            for (int ei = 0; ei < m; ++ei) {
                // 变量->校验置信（排除本消息）：llr + Σ其他校验消息
                q[ei] = llr[checkVars_[j][ei]] + tov[checkVars_[j][ei]] - r[j][ei];
                aq[ei] = std::fabs(q[ei]);
            }
            for (int ei = 0; ei < m; ++ei) {
                double sign = 1.0, mn = 1e30;
                for (int k = 0; k < m; ++k) {
                    if (k == ei) continue;
                    if (q[k] < 0) sign = -sign;
                    if (aq[k] < mn) mn = aq[k];
                }
                double nr = sign * mn * 0.75;
                r[j][ei] = nr;
                newTov[checkVars_[j][ei]] += nr;
            }
        }
        tov = std::move(newTov);
        // 变量后验硬判决 + 全校验
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
                // 报告/收尾字段（WSJT-X 官方用户手册标准消息语义，2026-11-02
                // 跨实现对拍修正，与 Python ft8_codec.unpack77 同源）：
                //   irpt=1 仅两呼号（exchange=""）；2=RRR；3=RR73；4=73；
                //   >=5: isnr=irpt-35（>50 回绕 -=101），报告数；
                //   exchange = (R 标志 ? "R" : "") + "%+03d"（"-07"/"R-07"/"+05"）。
                // 此前 `std::to_string(igrid-32400)` 把 irpt 原值当报告数
                // （"R27" 而非 "-08"）且 RRR/RR73/73/空全没做——grid 用例
                // 一路绿灯从未测过报告类。
                long long irpt = (long long)igrid - 32400;
                if (irpt == 1) {
                    d.exchange = "";
                } else if (irpt == 2) {
                    d.exchange = "RRR";
                } else if (irpt == 3) {
                    d.exchange = "RR73";
                } else if (irpt == 4) {
                    d.exchange = "73";
                } else if (irpt >= 5) {
                    long long isnr = irpt - 35;
                    if (isnr > 50) isnr -= 101;
                    char buf[8];
                    std::snprintf(buf, sizeof buf, "%+03lld", isnr);
                    d.exchange = (d.report ? "R" : "") + std::string(buf);
                } else {
                    return std::nullopt;
                }
            }
            return d;
        }
        if (nclast >= 0) {
            int nd = ncheck - nclast;
            ncnt = (nd < 0) ? 0 : ncnt + 1;
            if (ncnt >= 5 && it >= 10 && ncheck > 15) return std::nullopt;
        }
        nclast = ncheck;
    }
    return std::nullopt;
}

} // namespace mbdsdr
