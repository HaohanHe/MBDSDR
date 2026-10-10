// SPDX-License-Identifier: MIT
// Deterministic tests for the NanoVNA panel's widget-free text/format helpers.
#include <QtTest/QtTest>

#include "ui/vna_panel_format.h"

using namespace mbdsdr::ui;

class TestVnaPanelFormat : public QObject {
    Q_OBJECT
private slots:
    void statusDisconnected();
    void statusConnectedWithCal();
    void statusConnectedNoCal();
    void readoutEmpty();
    void readoutWithValues();
};

void TestVnaPanelFormat::statusDisconnected() {
    QCOMPARE(vnaStatusText(false, "", "", {}), QString::fromUtf8("未连接 NanoVNA"));
}

void TestVnaPanelFormat::statusConnectedWithCal() {
    QStringList cal{QString::fromUtf8("load"), QString::fromUtf8("open"),
                    QString::fromUtf8("cal'ed")};
    QString s = vnaStatusText(true, "NanoVNA-H", "1.0.174", cal);
    QVERIFY(s.startsWith(QString::fromUtf8("NanoVNA-H")));
    QVERIFY(s.contains(QString::fromUtf8("1.0.174")));
    QVERIFY(s.contains(QString::fromUtf8("cal: load open cal'ed")));
}

void TestVnaPanelFormat::statusConnectedNoCal() {
    QString s = vnaStatusText(true, "H4", "v2", {});
    QVERIFY(s.contains(QString::fromUtf8("未校准")));
}

void TestVnaPanelFormat::readoutEmpty() {
    QCOMPARE(vnaReadoutText(std::nan(""), 0, std::nan("")),
             QString::fromUtf8("无数据"));
}

void TestVnaPanelFormat::readoutWithValues() {
    QString s = vnaReadoutText(1.35, 14050000L, -1.2);
    QVERIFY(s.contains(QString::fromUtf8("1.35")));
    QVERIFY(s.contains(QString::fromUtf8("14.050 MHz")));
    QVERIFY(s.contains(QString::fromUtf8("-1.2 dB")));
}

QTEST_MAIN(TestVnaPanelFormat)
#include "test_vna_panel_format.moc"
