// SPDX-License-Identifier: MIT
// test_lrpt_cpp.cpp -- LRPT C++ 解码链跨语言 round-trip。
#include "dsp/lrpt_fec.h"
#include "dsp/lrpt_demod.h"
#include "dsp/spectrum_engine.h"
#include <QtTest/QtTest>
#include <complex>
#include <fstream>
#include <vector>

using namespace mbdsdr;

class TestLrptCpp : public QObject {
    Q_OBJECT
private slots:
    void initTestCase() {}
    void caduDecodesByteForByte();   // Python CADU -> C++ payload 逐字节一致
    void pureNoiseCaduHonestEmpty(); // 随机字节 -> nullopt
    void viterbiKnownVector();       // 已知软位 -> 硬判决
    void engineFeedDrivesReadback(); // 引擎 feed CADU -> 真实驱动 sync/frames
    void iqToPayloadRoundTrip();     // IQ -> QPSK 解调 -> Viterbi -> FEC -> payload 逐字节
    void pureNoiseDemodHonestEmpty(); // 纯噪声 IQ -> 空 frames
    void cleanupTestCase() {}
};

static std::vector<uint8_t> loadBin(const char* path, int n) {
    std::ifstream f(path, std::ios::binary);
    std::vector<uint8_t> v(n);
    if (f) f.read((char*)v.data(), n);
    return v;
}

void TestLrptCpp::caduDecodesByteForByte() {
    auto cadu = loadBin(SRCDIR "/lrpt_cadu.raw", 1024);
    auto exp  = loadBin(SRCDIR "/lrpt_payload.raw", 892);
    auto d = lrptDecodeCadu(cadu.data(), (int)cadu.size());
    QVERIFY2(d.has_value(), "Python CADU 应解码成功");
    QCOMPARE((int)d->size(), 892);
    QCOMPARE(*d, exp);   // 逐字节精确匹配
}

void TestLrptCpp::pureNoiseCaduHonestEmpty() {
    // 随机 1024 字节 -> RS 不可纠 -> nullopt（诚实空态）。
    std::vector<uint8_t> junk(1024, 0x55);
    for (int i = 0; i < 1024; ++i) junk[i] = (uint8_t)(i * 37 + 11);
    auto d = lrptDecodeCadu(junk.data(), (int)junk.size());
    QVERIFY2(!d.has_value(), "随机字节应诚实空态（RS 不可纠）");
}

void TestLrptCpp::viterbiKnownVector() {
    // 全 0 信息位（尾比特归零）的编码应全 0 c0,c1 -> 软位全 +1 -> 译码全 0。
    // 全 0 信息位编码后 c0,c1 全 0 -> 软位全 -1（正=bit1）-> 译码全 0。
    std::vector<double> soft(200, -1.0);  // 100 pair
    auto bits = viterbiDecodeK7(soft.data(), 100);
    for (int b : bits) QCOMPARE(b, 0);
}

void TestLrptCpp::engineFeedDrivesReadback() {
    auto cadu = loadBin(SRCDIR "/lrpt_cadu.raw", 1024);
    mbdsdr::dsp::SpectrumEngine eng;
    QVERIFY(!eng.lrptSyncLocked());
    eng.setLrptEnabled(true);
    eng.feedLrptCadu(cadu.data(), (int)cadu.size());
    QVERIFY2(eng.lrptSyncLocked(), "合法 CADU 应驱动 syncLocked=true");
    QCOMPARE(eng.lrptDecodedFrames(), 1);
    // 未启用 -> 诚实空态
    mbdsdr::dsp::SpectrumEngine eng2;
    eng2.feedLrptCadu(cadu.data(), (int)cadu.size());
    QVERIFY(!eng2.lrptSyncLocked());
}

void TestLrptCpp::iqToPayloadRoundTrip() {
    std::ifstream f(SRCDIR "/lrpt_e2e_iq.raw", std::ios::binary | std::ios::ate);
    int samples = (int)f.tellg() / 8; f.seekg(0);
    std::vector<float> raw(samples * 2);
    f.read((char*)raw.data(), samples * 2 * 4);
    std::vector<std::complex<float>> iq(samples);
    for (int i = 0; i < samples; ++i) iq[i] = {raw[2*i], raw[2*i+1]};
    auto frames = lrptDemodulate(iq.data(), samples);
    QVERIFY2(!frames.empty(), "应检出同步帧（ham=0）");
    QVERIFY2(frames[0].hamming <= 4, "同步汉明距应 <=4");
    QCOMPARE((int)frames[0].caduBytes.size(), 1024);
    auto d = lrptDecodeCadu(frames[0].caduBytes.data(), (int)frames[0].caduBytes.size());
    QVERIFY2(d.has_value(), "IQ 全链应解出 payload");
    auto exp = loadBin(SRCDIR "/lrpt_e2e_payload.raw", 892);
    QCOMPARE(*d, exp);   // IQ -> payload 逐字节精确匹配
}

void TestLrptCpp::pureNoiseDemodHonestEmpty() {
    // 纯零 IQ -> 无同步 -> 空 frames（诚实空态）。
    std::vector<std::complex<float>> iq(10000, {0.0f, 0.0f});
    auto frames = lrptDemodulate(iq.data(), (int)iq.size());
    QVERIFY2(frames.empty(), "纯零 IQ 应诚实空态");
}

QTEST_MAIN(TestLrptCpp)
#include "test_lrpt_cpp.moc"
