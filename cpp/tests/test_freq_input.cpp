// SPDX-License-Identifier: MIT
//
// 人类友好频率解析契约测试：单位语义（k/M/G/hz/裸数=MHz）、边界、
// 诚实失败（不猜、不夹值、错误文案非空）。
#include <QtTest>

#include "core/freq_input.h"
#include "core/tokens.h"

using namespace mbdsdr;

class TestFreqInput : public QObject {
    Q_OBJECT
private slots:
    void parsesSuffixUnits();
    void parsesBareNumberAsMhz();
    void parsesBareHzSuffix();
    void rejectsJunkHonestly();
    void rejectsOutOfRangeHonestly();
};

void TestFreqInput::parsesSuffixUnits() {
    double hz = 0.0;
    QString err;
    QVERIFY(core::parseHumanFrequency(QStringLiteral("137.62M"), &hz, &err));
    QCOMPARE(hz, 137.62e6);
    QVERIFY(core::parseHumanFrequency(QStringLiteral("137.62MHz"), &hz, &err));
    QCOMPARE(hz, 137.62e6);
    QVERIFY(core::parseHumanFrequency(QStringLiteral("145050k"), &hz, &err));
    QCOMPARE(hz, 145.05e6);
    QVERIFY(core::parseHumanFrequency(QStringLiteral("145050kHz"), &hz, &err));
    QCOMPARE(hz, 145.05e6);
    QVERIFY(core::parseHumanFrequency(QStringLiteral("1.42G"), &hz, &err));
    QCOMPARE(hz, 1.42e9);
    QVERIFY(core::parseHumanFrequency(QStringLiteral("1.42GHz"), &hz, &err));
    QCOMPARE(hz, 1.42e9);
    // 大小写宽容、允许空白。
    QVERIFY(core::parseHumanFrequency(QStringLiteral("  98.5 m "), &hz, &err));
    QCOMPARE(hz, 98.5e6);
}

void TestFreqInput::parsesBareNumberAsMhz() {
    double hz = 0.0;
    QVERIFY(core::parseHumanFrequency(QStringLiteral("145.5"), &hz, nullptr));
    QCOMPARE(hz, 145.5e6);
    // 裸数 + hz 词尾 = Hz（两种写法都指向 145.5 MHz）。
    QVERIFY(core::parseHumanFrequency(QStringLiteral("145500000hz"), &hz, nullptr));
    QCOMPARE(hz, 145.5e6);
    QVERIFY(core::parseHumanFrequency(QStringLiteral("145500000Hz"), &hz, nullptr));
    QCOMPARE(hz, 145.5e6);
}

void TestFreqInput::parsesBareHzSuffix() {
    double hz = 0.0;
    QVERIFY(core::parseHumanFrequency(QStringLiteral("144000000hz"), &hz, nullptr));
    QCOMPARE(hz, 144.0e6);
}

void TestFreqInput::rejectsJunkHonestly() {
    double hz = -1.0;
    QString err;
    QVERIFY(!core::parseHumanFrequency(QString(), &hz, &err));
    QVERIFY(!err.isEmpty());
    QVERIFY(!core::parseHumanFrequency(QStringLiteral("abc"), &hz, &err));
    QVERIFY(!err.isEmpty());
    QVERIFY(!core::parseHumanFrequency(QStringLiteral("12,5M"), &hz, &err));  // 逗号小数诚实拒绝
    QVERIFY(!core::parseHumanFrequency(QStringLiteral("-5M"), &hz, &err));
    QVERIFY(!core::parseHumanFrequency(QStringLiteral("12.5T"), &hz, &err)); // 未知倍率
    // 失败绝不改写输出。
    QCOMPARE(hz, -1.0);
}

void TestFreqInput::rejectsOutOfRangeHonestly() {
    // 接收域 = tokens::kFreqMinHz..kFreqMaxHz（24 MHz..1700 MHz）；域外输入
    // 诚实拒绝不夹值，边界本身放行——与 freqSpin_ 的域同一常量。
    double hz = -1.0;
    QString err;
    QVERIFY(!core::parseHumanFrequency(QStringLiteral("0.001"), &hz, &err)); // 1 kHz < min
    QVERIFY(!err.isEmpty());
    QVERIFY(!core::parseHumanFrequency(QStringLiteral("999G"), &hz, &err));
    QVERIFY(!err.isEmpty());
    QCOMPARE(hz, -1.0);
    QVERIFY(!core::parseHumanFrequency(QStringLiteral("23.9M"), &hz, &err));  // 差一点到下界
    QVERIFY(!core::parseHumanFrequency(QStringLiteral("1701M"), &hz, &err));  // 越过上界
    QVERIFY(core::parseHumanFrequency(QStringLiteral("24M"), &hz, &err));     // 下界放行
    QCOMPARE(hz, tokens::kFreqMinHz);
    QVERIFY(core::parseHumanFrequency(QStringLiteral("1700M"), &hz, &err));   // 上界放行
    QCOMPARE(hz, tokens::kFreqMaxHz);
}

QTEST_MAIN(TestFreqInput)
#include "test_freq_input.moc"
