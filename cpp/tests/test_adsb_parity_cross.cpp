// SPDX-License-Identifier: MIT
// ADS-B / Mode-S parity cross-impl truth tests.
//
// 共享真值（与 tests/test_adsb_cross_impl_parity.py、tests/test_adsb_dump1090_golden.py
// 同源）：dump1090 作者官方测试样本 testfiles/modes1.bin README 示例帧——
//   DF11  5D4D20237A55A6  (ICAO 4D2023, IID=0, 纯校验 AP==CRC(data))
//   DF4   20000F1F684A6C  (ICAO 4D2023, 地址奇偶 AP==CRC(data)^ICAO)
//
// 钉死的数学语义：教科书余数 F(x) mod G = crc24(数据段) ^ AP。
// crc24() 是 CRC 惯例（F·x^24 mod G）：DF4 全帧输入给 0x1EB2B4
// (= 0x4D2023·x^24 mod G)，不是地址 0x4D2023——checkParity 曾直接把
// 全帧 LFSR 当地址：DF4/5 还原垃圾 ICAO、DF20/21/24 合法帧全数误拒。
#include <cstdio>
#include <cstdint>
#include <vector>
#include "dsp/adsb_decoder.h"

using namespace mbdsdr::dsp;

static int failures = 0;
static int checks = 0;
static void check(bool c, const char* m, const char* detail="") {
    ++checks;
    if (!c) { ++failures; std::printf("FAIL: %s %s\n", m, detail); }
    else    { std::printf("  ok:  %s %s\n", m, detail); }
}

static std::vector<uint8_t> fromHex(const char* h) {
    std::vector<uint8_t> v;
    for (const char* p = h; *p && *(p+1); p += 2) {
        int b; std::sscanf(p, "%2x", &b); v.push_back((uint8_t)b);
    }
    return v;
}

// 地址奇偶长帧：DF/CA + AA + MB(7B) + AP = crc24(head) ^ addr
static std::vector<uint8_t> buildAddrParityLong(uint8_t dfCa, uint32_t addr,
                                                const std::vector<uint8_t>& mb7) {
    std::vector<uint8_t> f;
    f.push_back(dfCa);
    f.push_back((addr >> 16) & 0xFF); f.push_back((addr >> 8) & 0xFF); f.push_back(addr & 0xFF);
    for (auto b : mb7) f.push_back(b);
    uint32_t ap = ADSBDecoder::crc24(f.data(), 11) ^ addr;
    f.push_back((ap >> 16) & 0xFF); f.push_back((ap >> 8) & 0xFF); f.push_back(ap & 0xFF);
    return f;
}

// 纯校验长帧（DF17 式）：AP = crc24(head)
static std::vector<uint8_t> buildPureCrcLong(uint8_t dfCa, uint32_t addr,
                                             const std::vector<uint8_t>& me7) {
    std::vector<uint8_t> f;
    f.push_back(dfCa);
    f.push_back((addr >> 16) & 0xFF); f.push_back((addr >> 8) & 0xFF); f.push_back(addr & 0xFF);
    for (auto b : me7) f.push_back(b);
    uint32_t ap = ADSBDecoder::crc24(f.data(), 11);
    f.push_back((ap >> 16) & 0xFF); f.push_back((ap >> 8) & 0xFF); f.push_back(ap & 0xFF);
    return f;
}

int main() {
    const uint32_t ICAO = 0x4D2023;

    // ---- x^24 陷阱实证（文档性钉点：为什么全帧 LFSR 不能当地址）----
    {
        auto df4 = fromHex("20000f1f684a6c");
        uint32_t full = ADSBDecoder::crc24(df4.data(), 7);
        check(full == 0x1EB2B4u, "DF4 truth full-frame CRC = 0x1EB2B4 (ICAO*x^24 mod G)",
              "not 0x4D2023");
    }

    // ---- DF11 真值：纯校验，IID==0 自发 squitter ----
    {
        auto f = fromHex("5d4d20237a55a6");
        uint32_t icao = 0;
        bool ok = ADSBDecoder::checkParity(11, f.data(), 7, icao);
        check(ok, "DF11 truth accepted (IID==0)");
        check(icao == ICAO, "DF11 truth ICAO from plaintext AA");
    }

    // ---- DF4 真值：地址奇偶还原 ICAO（回归地板：旧实现给 0x1EB2B4）----
    {
        auto f = fromHex("20000f1f684a6c");
        uint32_t icao = 0;
        bool ok = ADSBDecoder::checkParity(4, f.data(), 7, icao);
        check(ok, "DF4 truth accepted (address parity)");
        check(icao == ICAO, "DF4 truth ICAO recovered = 0x4D2023", "address parity");
    }

    // ---- DF20 地址奇偶：合法帧必须过（回归地板：旧 syndrome!=aa 全拒）----
    {
        auto f = buildAddrParityLong(0xA0, ICAO, {1,2,3,4,5,6,7});  // DF20 CA=0
        uint32_t icao = 0;
        bool ok = ADSBDecoder::checkParity(20, f.data(), 14, icao);
        check(ok, "DF20 synthetic address-parity accepted");
        check(icao == ICAO, "DF20 ICAO = plaintext AA == recovered");
        // 明文 AA 与还原地址不符 → 拒（协议事实：DF20/21 有明文 AA 可对拍）
        auto bad = f;
        bad[13] ^= 0x01;                       // 动 AP 最低位
        uint32_t icao2 = 0;
        check(!ADSBDecoder::checkParity(20, bad.data(), 14, icao2),
              "DF20 with corrupted AP rejected (recovered != AA)");
    }

    // ---- DF20 纯 CRC 包装（无地址异或）→ 余数 0 ≠ AA，拒 ----
    {
        auto f = buildPureCrcLong(0xA0, ICAO, {1,2,3,4,5,6,7});
        uint32_t icao = 0;
        check(!ADSBDecoder::checkParity(20, f.data(), 14, icao),
              "DF20 pure-CRC AP (address 0) rejected");
    }

    // ---- DF4 单比特误码：地址奇偶是"识别"不是"检错"——公式钉死 ----
    {
        auto f = fromHex("20000f1f684a6c");
        auto bad = f;
        bad[2] ^= 0x08;                        // 数据段翻一位
        uint32_t icao = 0;
        bool ok = ADSBDecoder::checkParity(4, bad.data(), 7, icao);
        // 还原地址 = crc24(坏数据)^AP，测试侧独立复算
        uint32_t ap = ((uint32_t)bad[4] << 16) | ((uint32_t)bad[5] << 8) | bad[6];
        uint32_t expect = ADSBDecoder::crc24(bad.data(), 4) ^ ap;
        check(ok, "DF4 single-bit error still accepted (identifies, not detects)");
        check(icao == expect && icao != ICAO, "DF4 corrupted -> different ICAO (formula)");
    }

    // ---- DF17 纯校验对照：单比特误码被检出 ----
    {
        auto f = buildPureCrcLong(0x8D, ICAO, {0x11,0x22,0x33,0x44,0x55,0x66,0x77});
        uint32_t icao = 0;
        check(ADSBDecoder::checkParity(17, f.data(), 14, icao), "DF17 pure-CRC accepted");
        check(icao == ICAO, "DF17 ICAO from plaintext AA");
        auto bad = f;
        bad[5] ^= 0x01;
        check(!ADSBDecoder::checkParity(17, bad.data(), 14, icao),
              "DF17 single-bit error detected (syndrome != 0)");
    }

    // ---- DF11 IID!=0：按现行政策只收 IID==0 ----
    {
        auto f = fromHex("5d4d20237a55a6");
        auto bad = f;
        // 把 AP 改成 CRC(data)^5 → 余数 = 5（IID=5）
        uint32_t crc = ADSBDecoder::crc24(bad.data(), 4);
        uint32_t ap = crc ^ 0x5u;
        bad[4] = (ap >> 16) & 0xFF; bad[5] = (ap >> 8) & 0xFF; bad[6] = ap & 0xFF;
        uint32_t icao = 0;
        check(!ADSBDecoder::checkParity(11, bad.data(), 7, icao),
              "DF11 IID=5 rejected (policy: IID==0 only)");
    }

    std::printf("\n== checks: %d, failures: %d ==\n", checks, failures);
    return failures ? 1 : 0;
}
