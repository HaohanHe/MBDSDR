// SPDX-License-Identifier: MIT
// test_ft8_codec.cpp -- C++ FT8 解码层确定性 round-trip。
#include "dsp/ft8_codec.h"
#include <QtTest/QtTest>
#include <fstream>
#include <vector>

using namespace mbdsdr;

class TestFt8Codec : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void crc14KnownVector();
    void decodesKnownCodeword();      // Python 生成 LLR -> C++ 解码字段精确匹配
    void noiseLlrHonestEmpty();       // 全零 LLR -> nullopt（诚实空态）
    void corruptedCodewordFails();    // 翻转多 bit -> nullopt 或字段不符
    void cleanupTestCase() {}
};

void TestFt8Codec::initTestCase() {}

void TestFt8Codec::crc14KnownVector() {
    // K1ABC/K2DEF/EM12 标准消息（Python pack77 生成，77 bit）。
    int b[91] = {0,0,0,0,1,0,0,1,1,0,1,1,1,1,0,1,1,1,1,0,0,0,1,1,0,1,0,1,0,
                 0,0,0,0,1,0,0,1,1,1,0,0,0,0,1,1,0,0,1,1,1,1,1,1,0,1,1,1,0,0,0,
                 1,0,0,0,0,0,1,1,0,1,1,1,0,0,0,0,1,1,0,1,1,1,0,0,1,0,0,1,1,0,0};
    QVERIFY(Ft8Codec::checkCrc14(b, 91));
}

static std::vector<double> loadLlr() {
    std::ifstream f(":/ft8_known_llr.txt");
    std::vector<double> v; double x;
    while (f >> x) v.push_back(x);
    return v;
}

void TestFt8Codec::decodesKnownCodeword() {
    // 由 Python ft8_codec 固定 seed 生成的确定性 LLR 文件（174 个 ±5）。
    std::vector<double> llr(174);
    // 内联已知 LLR：从 /tmp/ft8_llr.txt 烤入（K1ABC/K2DEF/EM12）。
    std::ifstream f(SRCDIR "/ft8_known_llr.txt");
    QVERIFY2(f.good(), "ft8_known_llr.txt 应存在");
    int i = 0; double x;
    while (f >> x && i < 174) llr[i++] = x;
    QCOMPARE(i, 174);
    Ft8Codec c;
    auto d = c.decode(llr.data(), llr.size(), 50);
    QVERIFY2(d.has_value(), "确定性码字应解码成功");
    QCOMPARE(QString::fromStdString(d->from),  QString("K1ABC"));
    QCOMPARE(QString::fromStdString(d->to),    QString("K2DEF"));
    QCOMPARE(QString::fromStdString(d->exchange), QString("EM12"));
    QVERIFY(!d->report);
}

void TestFt8Codec::noiseLlrHonestEmpty() {
    std::vector<double> llr(174, 0.0);
    Ft8Codec c;
    auto d = c.decode(llr.data(), llr.size(), 30);
    QVERIFY2(!d.has_value(), "全零 LLR 应诚实空态（CRC 不过）");
}

void TestFt8Codec::corruptedCodewordFails() {
    std::ifstream f(SRCDIR "/ft8_known_llr.txt");
    std::vector<double> llr; double x;
    while (f >> x) llr.push_back(x);
    QCOMPARE((int)llr.size(), 174);
    // 翻转 20 bit（幅度取反）-> 应无法收敛（诚实空态或字段错误）。
    for (int i = 0; i < 20; ++i) llr[i] = -llr[i];
    Ft8Codec c;
    auto d = c.decode(llr.data(), llr.size(), 30);
    // 不应误报为 K1ABC/K2DEF（即使偶发收敛，字段必错）。
    if (d.has_value()) {
        QVERIFY2(!(d->from == "K1ABC" && d->to == "K2DEF"),
                 "20 bit 翻转不应精确恢复原帧");
    }
}

QTEST_MAIN(TestFt8Codec)
#include "test_ft8_codec.moc"
