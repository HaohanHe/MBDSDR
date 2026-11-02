// SPDX-License-Identifier: MIT
// test_ft8_wsjtx_cross.cpp -- 跨实现对拍：共享 LLR fixture -> C++ 解码文本 ==
// WSJT-X 真值。与 Python tests/test_ft8_cross_impl_fixture.py 读**同一份**
// fixture（cpp/tests/ft8_wsjtx_llr.txt）：同一输入 -> 两家解码层输出一致，
// 即 Python mbdsdr_ai/ft8_codec 与 C++ dsp/ft8_codec 的实现对拍。
//
// fixture 溯源：前 5 行为 WSJT-X 2.7.0 官方样本 210703_133430.wav 真实信号
// 软 LLR（jt9 官方解码文本为真值）；后 2 行合成（编码器生成，钉 R 标志与
// RRR 特例——真实样本的 14 条全是纯报告/网格，没覆盖 R 路）。
// 2026-11-02：本测试曾逼出两处报告字段真雷（C++ "R27" 数值映射、
// Python R 位置渲染），已按 WSJT-X 官方用户手册标准消息形态修正。
#include "dsp/ft8_codec.h"

#include <QtTest/QtTest>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

using namespace mbdsdr;

class TestFt8WsjtxCross : public QObject {
    Q_OBJECT
private slots:
    void decodesSharedFixtureToTruthTexts();
};

void TestFt8WsjtxCross::decodesSharedFixtureToTruthTexts() {
    std::ifstream f(SRCDIR "/ft8_wsjtx_llr.txt");
    QVERIFY2(f.good(), "ft8_wsjtx_llr.txt 应存在（与 Python 侧共享的对拍 fixture）");
    Ft8Codec codec;
    int rows = 0;
    std::string line;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#') continue;
        // 格式：<tag> | <expected text> | <174 LLR 空格分隔>
        const auto p1 = line.find(" | ");
        const auto p2 = line.find(" | ", p1 + 3);
        QVERIFY2(p1 != std::string::npos && p2 != std::string::npos,
                 "fixture 行格式应为 '<tag> | <text> | <llr>'");
        const std::string expected = line.substr(p1 + 3, p2 - p1 - 3);
        std::istringstream is(line.substr(p2 + 3));
        std::vector<double> llr;
        double v;
        while (is >> v) llr.push_back(v);
        QCOMPARE((int)llr.size(), 174);

        const auto d = codec.decode(llr.data(), llr.size(), 50);
        QVERIFY2(d.has_value(), qPrintable(QString::fromStdString(
            "fixture 行未解码（诚实空态）: " + expected)));
        QVERIFY(d->ok);
        // 文本重建规则与 Python 对拍测试相同：from to [exchange]
        std::string got = d->from + " " + d->to;
        if (!d->exchange.empty()) got += " " + d->exchange;
        QCOMPARE(QString::fromStdString(got), QString::fromStdString(expected));
        ++rows;
    }
    QCOMPARE(rows, 7);   // 5 真实行 + 2 合成行
}

QTEST_MAIN(TestFt8WsjtxCross)
#include "test_ft8_wsjtx_cross.moc"
