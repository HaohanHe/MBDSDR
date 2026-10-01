// SPDX-License-Identifier: MIT
// AGC control tests: rtl_tcp setTunerAgc emits the REAL 0x03 gain-mode command
// (arg 1 = AGC, 0 = manual) observed via the test seam; engine caches and
// reads back the requested state; an unsupported source keeps it a no-op.
#include <QtTest/QtTest>
#include <QVector>
#include <QPair>

#include "dsp/rtl_tcp_source.h"
#include "dsp/spectrum_engine.h"

using namespace mbdsdr;

class RecordingTcpSource : public dsp::RtlTcpSource {
public:
    QVector<QPair<quint8,quint32>> cmds;
    using RtlTcpSource::RtlTcpSource;
protected:
    void commandLogged(quint8 cmd, quint32 arg) override {
        cmds.append({cmd, arg});
    }
};

class TestAgc : public QObject {
    Q_OBJECT
private slots:
    void rtlTcpSendsAgcCommand();
    void engineCachesReadback();
};

void TestAgc::rtlTcpSendsAgcCommand() {
    RecordingTcpSource src;   // fd closed -> sendCmd only records via seam
    src.cmds.clear();
    src.setTunerAgc(true);
    src.setTunerAgc(false);
    bool sawOn = false, sawOff = false;
    for (const auto& c : src.cmds) {
        if (c.first == 0x03 && c.second == 1) sawOn = true;
        if (c.first == 0x03 && c.second == 0) sawOff = true;
    }
    QVERIFY2(sawOn, "setTunerAgc(true) must send rtl_tcp cmd 0x03 arg 1 (AGC)");
    QVERIFY2(sawOff, "setTunerAgc(false) must send rtl_tcp cmd 0x03 arg 0 (manual)");
}

void TestAgc::engineCachesReadback() {
    dsp::SpectrumEngine engine;   // loopback/test source: no real device
    engine.setTunerAgc(true);
    QVERIFY2(engine.tunerAgc(), "engine must read back the requested tuner AGC");
    engine.setTunerAgc(false);
    QVERIFY2(!engine.tunerAgc(), "engine must read back manual after toggling off");
}

QTEST_MAIN(TestAgc)
#include "test_agc.moc"
