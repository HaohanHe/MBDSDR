// SPDX-License-Identifier: MIT
// test_lrpt_real_cross.cpp -- LRPT 真录软位流跨实现对拍（Python 侧孪生：
// tests/test_lrpt_cross_impl_fixture.py）。共享 fixture
// tests/golden/lrpt_meteor_real_soft_*.f32（Meteor-M2 真录赢家帧软位流，
// 星座变换已在抽取时套上——两端输入逐位相同）。
#include "dsp/lrpt_fec.h"
#include <QtTest/QtTest>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <optional>
#include <vector>

using namespace mbdsdr;

class TestLrptRealCross : public QObject {
    Q_OBJECT
private slots:
    void initTestCase() {}
    void realSoftFrame0NotBlindCorrectable();
    void realSoftFrame1NotBlindCorrectable();
    void asmRecoveredOnBothFrames();
    void cleanupTestCase() {}
};

static std::vector<float> loadF32(const char* path) {
    std::ifstream f(path, std::ios::binary | std::ios::ate);
    if (!f) return {};
    auto sz = f.tellg();
    f.seekg(0);
    std::vector<float> v((size_t)sz / sizeof(float));
    f.read((char*)v.data(), sz);
    return v;
}

// soft -> Viterbi -> bytes (MSB-first, ASM in front) -> lrptDecodeCadu
static std::optional<std::vector<uint8_t>> decodeSoft(const std::vector<float>& soft) {
    if (soft.empty()) return std::nullopt;
    std::vector<double> s(soft.begin(), soft.end());
    auto bits = viterbiDecodeK7(s.data(), (int)(s.size() / 2));
    int nbytes = ((int)bits.size() + 7) / 8;
    std::vector<uint8_t> raw(nbytes, 0);
    for (int i = 0; i < (int)bits.size(); ++i) {
        if (bits[i]) raw[i >> 3] |= (uint8_t)(0x80 >> (i & 7));
    }
    return lrptDecodeCadu(raw.data(), (int)raw.size());
}

// 真录 VCDU 载荷头 32B（manifest 真值，2026-11-07 重钉）
// 旧钉子（407300ec… / 4062000a…，以及后续 400509c0…）已全部作废：法证证明
// 那是 RS 误纠产物（4 块纠正后重编码与收到码字距离 31-32 字节，远超 t=16，
// 见 D:/mbdsdr/output/_probe_vcdu_forensics.log）。补上伴随式验证后，这两帧
// 在**盲解**下不可纠——C++ 侧尚无擦除路径（Python 有，2e+f ≤ 32），所以这里
// 只钉「不许假成功」，不钉假 VCDU。
void TestLrptRealCross::realSoftFrame0NotBlindCorrectable() {
    auto soft = loadF32(GOLDENDIR "/lrpt_meteor_real_soft_0.f32");
    if (soft.empty()) QSKIP("真录软位流 fixture 未提供");
    auto d = decodeSoft(soft);
    // 2026-11-07 真值重钉（与 Python 侧同步）：
    // 这两帧**盲解不可纠**——旧实现缺伴随式归零验证，超 t=16 时 BM 仍给
    // 不自洽解并当成功，钉在这里的 PIN 其实是误纠产物（4 块重编码距离
    // 31–32 > t=16，见 D:/mbdsdr/output/_probe_vcdu_forensics.log）。
    // 现在 C++ 与 Python 一致：盲解超预算就诚实返回 nullopt；
    // 真录要救回来需要擦除（2e+f ≤ 32），C++ 侧尚未实现，故此处钉「不许
    // 假成功」而不是钉一串假 VCDU。
    QVERIFY2(!d.has_value(),
             "该真录帧盲解不可纠：返回 payload 即为 RS 误纠假成功");
}

void TestLrptRealCross::realSoftFrame1NotBlindCorrectable() {
    auto soft = loadF32(GOLDENDIR "/lrpt_meteor_real_soft_1.f32");
    if (soft.empty()) QSKIP("真录软位流 fixture 未提供");
    auto d = decodeSoft(soft);
    QVERIFY2(!d.has_value(),
             "该真录帧盲解不可纠：返回 payload 即为 RS 误纠假成功");
}

void TestLrptRealCross::asmRecoveredOnBothFrames() {
    for (int k = 0; k < 2; ++k) {
        char path[512];
        snprintf(path, sizeof(path), GOLDENDIR "/lrpt_meteor_real_soft_%d.f32", k);
        auto soft = loadF32(path);
        if (soft.empty()) QSKIP("fixture 未提供");
        std::vector<double> s(soft.begin(), soft.end());
        auto bits = viterbiDecodeK7(s.data(), (int)(s.size() / 2));
        QVERIFY(bits.size() >= 32);
        // 前 32 位 = ASM 0x1ACFFC1D（MSB first）
        uint32_t asmVal = 0;
        for (int i = 0; i < 32; ++i) asmVal = (asmVal << 1) | (uint32_t)bits[i];
        QCOMPARE(asmVal, 0x1ACFFC1Du);
    }
}

QTEST_MAIN(TestLrptRealCross)
#include "test_lrpt_real_cross.moc"
