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

// 真录 VCDU 载荷头 32B（manifest 真值，2026-11-08 重钉）
//
// ⚠ 旧钉子（407300ec… / 4062000a…）已作废：法证证明那是 RS 误纠产物
// （4 块纠正后重编码与收到码字距离 31-32 字节，远超 t=16）。新真值走
// woven 重组 + 伴随式验证，帧计数连续、图像包 APID 64/65/68 双重印证。
// 注意：本文件与 lrpt_fec.cc 的 woven 改动无法在本机编译验证（无 g++/Qt6），
// 由 CI 首跑背书。
static const uint8_t PIN0[32] = {
    0x40,0x05,0x09,0xc0,0xd2,0xb4,0xe4,0x00,0x46,0x08,0x2c,0x28,0x31,0x80,0x38,0x3c,
    0xd7,0xff,0x08,0x44,0x26,0xb8,0x00,0x59,0x00,0x00,0x02,0x96,0x21,0xbc,0x00,0x00
};
static const uint8_t PIN1[32] = {
    0x40,0x05,0x09,0xc0,0xdb,0x00,0x00,0x00,0x00,0xcc,0x8d,0x29,0xc9,0x0f,0x2a,0x65,
    0x86,0x07,0x27,0x1d,0x47,0x62,0x7a,0x74,0x1f,0x89,0xaa,0x17,0xa9,0x24,0x49,0xe6
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
