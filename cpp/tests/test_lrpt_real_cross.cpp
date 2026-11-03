// SPDX-License-Identifier: MIT
// test_lrpt_real_cross.cpp -- LRPT 真录软位流跨实现对拍（Python 侧孪生：
// tests/test_lrpt_cross_impl_fixture.py）。共享 fixture
// tests/golden/lrpt_meteor_real_soft_*.f32（Meteor-M2 真录赢家帧软位流，
// 星座变换已在抽取时套上——两端输入逐位相同）。
#include "dsp/lrpt_fec.h"
#include <QtTest/QtTest>
#include <cstdint>
#include <fstream>
#include <vector>

using namespace mbdsdr;

class TestLrptRealCross : public QObject {
    Q_OBJECT
private slots:
    void initTestCase() {}
    void realSoftFrame0DecodesToPinnedVcdu();
    void realSoftFrame1DecodesToPinnedVcdu();
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

// 真录 VCDU 载荷头 32B（manifest 真值，2026-11-03 实证）
static const uint8_t PIN0[32] = {
    0x40,0x73,0x00,0xec,0xc3,0x2d,0xf0,0xd3,0xad,0xce,0x12,0x02,0x95,0x87,0x1f,0x42,
    0x67,0x55,0x21,0x26,0xd2,0x23,0xf8,0x98,0x9b,0x94,0x2f,0xfe,0x5e,0xc7,0xf6,0x04
};
static const uint8_t PIN1[32] = {
    0x40,0x62,0x00,0x0a,0x26,0x08,0x76,0x08,0x00,0x02,0x00,0x00,0xfd,0xda,0xcc,0x73,
    0x71,0x4b,0xe1,0xa5,0x67,0x41,0x3e,0x5b,0x43,0x33,0x57,0x92,0xbd,0x92,0x79,0x13
};

void TestLrptRealCross::realSoftFrame0DecodesToPinnedVcdu() {
    auto soft = loadF32(GOLDENDIR "/lrpt_meteor_real_soft_0.f32");
    if (soft.empty()) QSKIP("真录软位流 fixture 未提供");
    auto d = decodeSoft(soft);
    QVERIFY2(d.has_value(), "真录帧 0 应解出 VCDU");
    QCOMPARE((int)d->size(), 892);
    for (int i = 0; i < 32; ++i) {
        QCOMPARE((*d)[i], PIN0[i]);
    }
}

void TestLrptRealCross::realSoftFrame1DecodesToPinnedVcdu() {
    auto soft = loadF32(GOLDENDIR "/lrpt_meteor_real_soft_1.f32");
    if (soft.empty()) QSKIP("真录软位流 fixture 未提供");
    auto d = decodeSoft(soft);
    QVERIFY2(d.has_value(), "真录帧 1 应解出 VCDU");
    QCOMPARE((int)d->size(), 892);
    for (int i = 0; i < 32; ++i) {
        QCOMPARE((*d)[i], PIN1[i]);
    }
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
