// SPDX-License-Identifier: MIT
// test_lrpt_cpp.cpp -- LRPT C++ 解码链跨语言 round-trip。
#include "dsp/lrpt_fec.h"
#include "dsp/spectrum_engine.h"
#include <QtTest/QtTest>
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

QTEST_MAIN(TestLrptCpp)
#include "test_lrpt_cpp.moc"
