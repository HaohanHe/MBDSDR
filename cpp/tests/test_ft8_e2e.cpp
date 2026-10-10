// SPDX-License-Identifier: MIT
// test_ft8_e2e.cpp -- 运行时全链路 e2e：Python 合成 IQ -> 检测->LLR->解码。
#include "dsp/spectrum_engine.h"
#include <QtTest/QtTest>
#include <complex>
#include <fstream>
#include <vector>

using namespace mbdsdr; using mbdsdr::dsp::SpectrumEngine;

class TestFt8E2e : public QObject {
    Q_OBJECT
private slots:
    void initTestCase() {}
    void decodesSyntheticFrame();     // 合成编码帧 -> 解码字段精确匹配
    void feedPathRingBufferDecodes(); // 分块喂入环形缓冲 -> 满窗触发解码
    void pureNoiseHonestEmpty();      // 纯噪声 -> active=false, decoded 空
    void disabledHonestEmpty();       // 未启用 -> 空态
    void cleanupTestCase() {}
};

static std::vector<std::complex<float>> loadRaw(const char* path, std::size_t n) {
    std::ifstream f(path, std::ios::binary);
    std::vector<std::complex<float>> buf(n);
    if (f) f.read((char*)buf.data(), n * sizeof(std::complex<float>));
    return buf;
}

void TestFt8E2e::decodesSyntheticFrame() {
    auto buf = loadRaw(SRCDIR "/ft8_e2e_iq.raw", 180000);
    SpectrumEngine eng;
    eng.setFt8Enabled(true);
    eng.processFt8Window(buf.data(), buf.size());
    QVERIFY2(eng.ft8Present(), "合成编码帧应被检出");
    QCOMPARE(QString::fromStdString(eng.ft8DecodedText()),
             QString("K1ABC K2DEF EM12"));
}

void TestFt8E2e::feedPathRingBufferDecodes() {
    auto buf = loadRaw(SRCDIR "/ft8_e2e_iq.raw", 180000);
    SpectrumEngine eng;
    eng.setFt8Enabled(true);
    // 分块喂入（模拟 run loop 块），环形缓冲满窗后应触发解码。
    constexpr int kChunk = 12000;
    for (std::size_t off = 0; off < buf.size(); off += kChunk) {
        std::size_t n = std::min<std::size_t>(kChunk, buf.size() - off);
        eng.feedFt8Baseband(buf.data() + off, n);
    }
    QVERIFY2(eng.ft8Present(), "环形缓冲满窗后应检出帧");
    QCOMPARE(QString::fromStdString(eng.ft8DecodedText()),
             QString("K1ABC K2DEF EM12"));
    QVERIFY2(eng.ft8DecodedFrameCount() >= 1, "去重后至少计数 1 帧");
}

void TestFt8E2e::pureNoiseHonestEmpty() {
    std::vector<std::complex<float>> buf(180000, {0.0f, 0.0f});
    SpectrumEngine eng;
    eng.setFt8Enabled(true);
    eng.processFt8Window(buf.data(), buf.size());
    QVERIFY2(!eng.ft8Present(), "纯噪声应诚实空态");
    QVERIFY2(eng.ft8DecodedText().empty(), "纯噪声 decoded_text 应为空");
}

void TestFt8E2e::disabledHonestEmpty() {
    auto buf = loadRaw(SRCDIR "/ft8_e2e_iq.raw", 180000);
    SpectrumEngine eng;
    eng.setFt8Enabled(false);
    eng.processFt8Window(buf.data(), buf.size());
    QVERIFY2(!eng.ft8Present(), "未启用应空态");
    QVERIFY2(eng.ft8DecodedText().empty(), "未启用 decoded_text 应为空");
}

QTEST_MAIN(TestFt8E2e)
#include "test_ft8_e2e.moc"
