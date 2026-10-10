// SPDX-License-Identifier: MIT
#include <QtTest/QtTest>
#include "dsp/band_preset.h"
using namespace mbdsdr;

class TestBandPreset : public QObject {
    Q_OBJECT
private slots:
    void tableHasExpectedBands();
    void hitsInsideBand();
    void missOutsideBands();
    void edgesInclusive();
    void byNameAliases();
};

void TestBandPreset::tableHasExpectedBands() {
    const auto& t = bandPresets();
    QVERIFY(t.size() >= 6);
    bool hasFm = false, hasAir = false;
    for (const auto& p : t) {
        if (QString::fromUtf8(p.name) == QString::fromUtf8("FM广播")) hasFm = true;
        if (QString::fromUtf8(p.name) == QString::fromUtf8("航空")) hasAir = true;
    }
    QVERIFY(hasFm && hasAir);
}

void TestBandPreset::hitsInsideBand() {
    auto fm = bandPresetForFrequency(98.5e6);
    QVERIFY(fm.has_value());
    QCOMPARE(QString(fm->mode), QString("WFM"));
    auto air = bandPresetForFrequency(122.8e6);
    QVERIFY(air.has_value());
    QCOMPARE(QString(air->mode), QString("AM"));
}

void TestBandPreset::missOutsideBands() {
    // 10 MHz and 300 MHz are in no listed band.
    QVERIFY(!bandPresetForFrequency(10e6).has_value());
    QVERIFY(!bandPresetForFrequency(300e6).has_value());
}

void TestBandPreset::edgesInclusive() {
    QVERIFY(bandPresetForFrequency(87.5e6).has_value());  // FM lower edge
    QVERIFY(bandPresetForFrequency(108.0e6).has_value()); // FM upper edge
    QVERIFY(bandPresetForFrequency(136.0e6).has_value()); // air upper edge
}

void TestBandPreset::byNameAliases() {
    auto air = bandPresetByName("航空");
    QVERIFY(air.has_value());
    QCOMPARE(QString(air->mode), QString("AM"));
    auto m20 = bandPresetByName("20米");
    QVERIFY(m20.has_value());
    QCOMPARE(QString(m20->mode), QString("USB"));
    auto c70 = bandPresetByName("70厘米");
    QVERIFY(c70.has_value());
    QVERIFY(!bandPresetByName("火星频道").has_value()); // honest miss
}

QTEST_MAIN(TestBandPreset)
#include "test_band_preset.moc"
