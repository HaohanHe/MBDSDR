// SPDX-License-Identifier: MIT
// Deterministic NanoVNA client tests: a self-made scripted replay transport
// drives the exact protocol text (same wire format as the Python fixture, but
// authored independently in C++ — no upstream code copied).
//
// Covers: handshake/parsing, command sequence ordering, sweep validation,
// frequency/data parsing, RF conversion math, and honest empty state when no
// transport is injected.
#include <QtTest/QtTest>

#include "vna/nanovna_client.h"

#include <cstring>
#include <memory>
#include <vector>

using namespace mbdsdr::vna;

namespace {

// Scripted replay: each write() must equal the expected command (trimmed); on
// match we enqueue the scripted reply lines plus the "ch>" prompt, then pop one
// line per readLine().
class ReplayTransport : public VnaTransport {
public:
    // frameHex: optional hex payload served by readBytes() (the `capture`
    // binary frame). Empty -> readBytes() returns 0 (text-only steps).
    struct Step { QString expect; QStringList lines; QString frameHex; };
    explicit ReplayTransport(std::vector<Step> script) : script_(std::move(script)) {}

    bool open() override { open_ = true; return true; }
    void close() override { open_ = false; }
    bool isOpen() const override { return open_; }
    QString errorString() const override { return {}; }

    void write(const QByteArray& data) override {
        QString cmd = QString::fromUtf8(data).trimmed();
        sent_.push_back(cmd);
        QVERIFY(cursor_ < (int)script_.size());
        QCOMPARE(cmd, script_[cursor_].expect);
        queue_ = script_[cursor_].lines;
        queue_.push_back(QStringLiteral("ch>"));
        frame_ = QByteArray::fromHex(script_[cursor_].frameHex.toUtf8());
        ++cursor_;
    }
    bool readLine(QByteArray& out) override {
        if (queue_.isEmpty()) { out.clear(); return false; }
        out = queue_.takeFirst().toUtf8();
        return true;
    }
    int readBytes(char* buf, int maxLen) override {
        const int n = qMin<int>(maxLen, frame_.size());
        if (n > 0) memcpy(buf, frame_.constData(), (size_t)n);
        frame_.remove(0, n);
        return n;
    }
    void resetInputBuffer() override { queue_.clear(); }

    QStringList sent_;
private:
    std::vector<Step> script_;
    QStringList queue_;
    QByteArray frame_;
    int cursor_ = 0;
    bool open_ = false;
};

} // namespace

class TestNanoVnaClient : public QObject {
    Q_OBJECT
private slots:
    void rfMathKnownValues();
    void handshakeParsesModelAndVersion();
    void sweepValidatesAndOrdersCommands();
    void frequenciesAndDataParse();
    void honestEmptyWithoutTransport();
    // --- Calibration command family (P0 #59 client side) ---------------------
    void calStepsEmitWordsAndRefreshCalReadback();
    void calSaveLoadSlotValidatesSlotRange();
    void calCommandsRejectWhenDisconnected();
    void calCoverageTrackedOnlyAfterSweep();
    void scanPauseResumeEmitWords();
    // --- Device misc command family (P1 #61 client side) ---------------------
    void deviceMiscWordsAndParsing();
    void deviceMiscRejectWhenDisconnected();
    // --- tcxo readback + capture screen (2026-10 audit backfill) ------------
    void tcxoParsesCurrentLine();
    void tcxoRejectsUnparseableReply();
    void captureScreenReadsExactFrame();
    void captureScreenShortFrameIsHonest();
};

void TestNanoVnaClient::rfMathKnownValues() {
    // Shorted port: S11 = -1 -> |g|=1 -> VSWR inf, RL 0 dB.
    QCOMPARE(returnLossDb({-1.0, 0.0}), 0.0);
    QVERIFY(std::isinf(vswr({-1.0, 0.0})));
    // Matched: S11 = 0 -> VSWR 1, RL +inf.
    QCOMPARE(vswr({0.0, 0.0}), 1.0);
    QVERIFY(std::isinf(returnLossDb({0.0, 0.0})));
    // |g| = 0.5 -> VSWR = (1.5)/(0.5) = 3.0.
    QCOMPARE(vswr({0.5, 0.0}), 3.0);
    QCOMPARE(returnLossDb({0.5, 0.0}), -20.0 * std::log10(0.5));
    // Impedance: S11=0 -> Z=50 Ohm real.
    auto z = s11ToImpedance({0.0, 0.0});
    QCOMPARE(z.real(), 50.0);
    QCOMPARE(z.imag(), 0.0);
    // S21 gain: |a|=1 -> 0 dB.
    QCOMPARE(s21GainDb({1.0, 0.0}), 0.0);
    // Phase: (1,1) -> 45 deg.
    QCOMPARE(s21PhaseDeg({1.0, 1.0}), 45.0);
}

void TestNanoVnaClient::handshakeParsesModelAndVersion() {
    auto tr = std::make_unique<ReplayTransport>(std::vector<ReplayTransport::Step>{
        ReplayTransport::Step{QStringLiteral("help"), {QStringLiteral("sweep"), QStringLiteral("frequencies")}},
        ReplayTransport::Step{QStringLiteral("version"), {QStringLiteral(" NanoVNA-v2-2024-01-01 ")}},
        ReplayTransport::Step{QStringLiteral("info"), {QStringLiteral("NanoVNA H4"), QStringLiteral("Hardware 4.2")}},
    });
    NanoVnaClient client(std::move(tr));
    QVERIFY(client.connect());
    QVERIFY(client.isConnected());
    QCOMPARE(client.version(), QStringLiteral("NanoVNA-v2-2024-01-01")); // trimmed
    QCOMPARE(client.model(), QStringLiteral("NanoVNA H4"));        // first info line
}

void TestNanoVnaClient::sweepValidatesAndOrdersCommands() {
    auto tr = std::make_unique<ReplayTransport>(std::vector<ReplayTransport::Step>{
        ReplayTransport::Step{QStringLiteral("help"), {}},
        ReplayTransport::Step{QStringLiteral("version"), {QStringLiteral("v1")}},
        ReplayTransport::Step{QStringLiteral("info"), {QStringLiteral("H4")}},
        ReplayTransport::Step{QStringLiteral("sweep 1000000 100000000 101"), {}},
    });
    ReplayTransport* raw = tr.get();
    NanoVnaClient client(std::move(tr));
    QVERIFY(client.connect());

    QString err;
    // stop <= start -> rejected before any command
    QVERIFY(!client.setSweep(100.0, 100.0, 101, &err));
    QVERIFY(!client.setSweep(100.0, 50.0, 101, &err));
    // points <= 0 -> rejected
    QVERIFY(!client.setSweep(100.0, 200.0, 0, &err));
    // valid -> issues the sweep command as the 4th sent command
    QVERIFY(client.setSweep(1000000.0, 100000000.0, 101, &err));
    QCOMPARE(raw->sent_.size(), 4);
    QCOMPARE(raw->sent_.at(3), QStringLiteral("sweep 1000000 100000000 101"));
    QVERIFY(client.hasSweep());
    QCOMPARE(client.sweepStartHz(), 1000000L);
}

void TestNanoVnaClient::frequenciesAndDataParse() {
    auto tr = std::make_unique<ReplayTransport>(std::vector<ReplayTransport::Step>{
        ReplayTransport::Step{QStringLiteral("help"), {}},
        ReplayTransport::Step{QStringLiteral("version"), {QStringLiteral("v1")}},
        ReplayTransport::Step{QStringLiteral("info"), {QStringLiteral("H4")}},
        ReplayTransport::Step{QStringLiteral("frequencies"), {QStringLiteral("1000000"), QStringLiteral("2000000"), QStringLiteral("3000000")}},
        ReplayTransport::Step{QStringLiteral("data 0"), {QStringLiteral("0.5 0.0"), QStringLiteral(" 0.3 -0.2 ")}},
        ReplayTransport::Step{QStringLiteral("cal"), {QStringLiteral("s011 s11 error")}},
    });
    NanoVnaClient client(std::move(tr));
    QVERIFY(client.connect());

    auto freqs = client.readFrequencies();
    QCOMPARE(freqs.size(), (size_t)3);
    QCOMPARE(freqs[0], 1000000L);

    auto s11 = client.readData(0);
    QCOMPARE(s11.size(), (size_t)2);
    QCOMPARE(s11[0].real(), 0.5);
    QCOMPARE(s11[1].imag(), -0.2);

    QStringList cal = client.readCalStatus();
    QStringList expectCal{QStringLiteral("s011"), QStringLiteral("s11"), QStringLiteral("error")};
    QCOMPARE(cal, expectCal);
}

void TestNanoVnaClient::honestEmptyWithoutTransport() {
    NanoVnaClient client;  // no transport
    QVERIFY(!client.isConnected());
    QVERIFY(!client.connect());          // cannot connect with no transport
    QVERIFY(client.readFrequencies().empty());
    QVERIFY(client.readData(0).empty());
    QVERIFY(client.readCalStatus().isEmpty());
    QVERIFY(client.model().isEmpty());
    QString err;
    QVERIFY(!client.setSweep(1, 2, 10, &err));  // honest: not connected
    QVERIFY(!err.isEmpty());
}

void TestNanoVnaClient::calStepsEmitWordsAndRefreshCalReadback() {
    // Every cal step emits "cal <word>" then a follow-up "cal" readback query.
    auto tr = std::make_unique<ReplayTransport>(std::vector<ReplayTransport::Step>{
        ReplayTransport::Step{QStringLiteral("help"), {}},
        ReplayTransport::Step{QStringLiteral("version"), {QStringLiteral("v1")}},
        ReplayTransport::Step{QStringLiteral("info"), {QStringLiteral("H4")}},
        ReplayTransport::Step{QStringLiteral("cal open"), {}},
        ReplayTransport::Step{QStringLiteral("cal"), {QStringLiteral("open")}},
        ReplayTransport::Step{QStringLiteral("cal short"), {}},
        ReplayTransport::Step{QStringLiteral("cal"), {QStringLiteral("short")}},
        ReplayTransport::Step{QStringLiteral("cal load"), {}},
        ReplayTransport::Step{QStringLiteral("cal"), {QStringLiteral("load")}},
        ReplayTransport::Step{QStringLiteral("cal thru"), {}},
        ReplayTransport::Step{QStringLiteral("cal"), {QStringLiteral("thru done c1")}},
    });
    ReplayTransport* raw = tr.get();
    NanoVnaClient client(std::move(tr));
    QVERIFY(client.connect());

    QString err;
    QVERIFY(client.calOpen(&err));
    QCOMPARE(raw->sent_.at(3), QStringLiteral("cal open"));
    QCOMPARE(raw->sent_.at(4), QStringLiteral("cal"));
    QCOMPARE(client.calStatus(), QStringList{QStringLiteral("open")});

    QVERIFY(client.calShort(&err));
    QCOMPARE(raw->sent_.at(5), QStringLiteral("cal short"));
    QCOMPARE(client.calStatus(), QStringList{QStringLiteral("short")});

    QVERIFY(client.calLoad(&err));   // 50 Ohm load standard step
    QCOMPARE(raw->sent_.at(7), QStringLiteral("cal load"));
    QCOMPARE(client.calStatus(), QStringList{QStringLiteral("load")});

    QVERIFY(client.calThru(&err));
    QCOMPARE(raw->sent_.at(9), QStringLiteral("cal thru"));
    // cal reply carries multiple tokens incl. done/c1 -> space-split set items
    QStringList expect{QStringLiteral("thru"), QStringLiteral("done"), QStringLiteral("c1")};
    QCOMPARE(client.calStatus(), expect);
}

void TestNanoVnaClient::calSaveLoadSlotValidatesSlotRange() {
    auto tr = std::make_unique<ReplayTransport>(std::vector<ReplayTransport::Step>{
        ReplayTransport::Step{QStringLiteral("help"), {}},
        ReplayTransport::Step{QStringLiteral("version"), {QStringLiteral("v1")}},
        ReplayTransport::Step{QStringLiteral("info"), {QStringLiteral("H4")}},
        ReplayTransport::Step{QStringLiteral("cal save 1"), {}},
        ReplayTransport::Step{QStringLiteral("cal"), {QStringLiteral("c1")}},
        ReplayTransport::Step{QStringLiteral("cal load 4"), {}},
        ReplayTransport::Step{QStringLiteral("cal"), {QStringLiteral("c4")}},
    });
    ReplayTransport* raw = tr.get();
    NanoVnaClient client(std::move(tr));
    QVERIFY(client.connect());

    QString err;
    // Out-of-range slots rejected before any command is sent.
    QVERIFY(!client.calSave(0, &err));
    QVERIFY(!err.isEmpty());
    QVERIFY(!client.calSave(5, &err));
    QVERIFY(!client.calLoadSlot(0, &err));
    QVERIFY(!client.calLoadSlot(5, &err));
    // In-range slots emit "cal save N" / "cal load N" then the cal readback.
    QVERIFY(client.calSave(1, &err));
    QCOMPARE(raw->sent_.at(3), QStringLiteral("cal save 1"));
    QCOMPARE(client.calStatus(), QStringList{QStringLiteral("c1")});
    QVERIFY(client.calLoadSlot(4, &err));
    QCOMPARE(raw->sent_.at(5), QStringLiteral("cal load 4"));
    QCOMPARE(client.calStatus(), QStringList{QStringLiteral("c4")});
}

void TestNanoVnaClient::calCommandsRejectWhenDisconnected() {
    NanoVnaClient client;   // no transport -> honest empty state
    QString err;
    QVERIFY(!client.isConnected());
    QVERIFY(!client.calReset(&err));
    QCOMPARE(err, QStringLiteral("NanoVNA 未连接"));
    QVERIFY(!client.calOpen(&err));
    QCOMPARE(err, QStringLiteral("NanoVNA 未连接"));
    QVERIFY(!client.calShort(&err));
    QVERIFY(!client.calLoad(&err));
    QVERIFY(!client.calIsolation(&err));
    QVERIFY(!client.calThru(&err));
    QVERIFY(!client.calDone(&err));
    QVERIFY(!client.calSave(1, &err));
    QVERIFY(!client.calLoadSlot(1, &err));
    QVERIFY(!client.scan(&err));
    QVERIFY(!client.pauseScan(&err));
    QVERIFY(!client.resumeScan(&err));
}

void TestNanoVnaClient::calCoverageTrackedOnlyAfterSweep() {
    auto tr = std::make_unique<ReplayTransport>(std::vector<ReplayTransport::Step>{
        ReplayTransport::Step{QStringLiteral("help"), {}},
        ReplayTransport::Step{QStringLiteral("version"), {QStringLiteral("v1")}},
        ReplayTransport::Step{QStringLiteral("info"), {QStringLiteral("H4")}},
        // calDone before any sweep: coverage must NOT be recorded.
        ReplayTransport::Step{QStringLiteral("cal done"), {}},
        ReplayTransport::Step{QStringLiteral("cal"), {QStringLiteral("done")}},
        // Now set a sweep range, then calDone -> coverage becomes known.
        ReplayTransport::Step{QStringLiteral("sweep 1000000 100000000 101"), {}},
        ReplayTransport::Step{QStringLiteral("cal done"), {}},
        ReplayTransport::Step{QStringLiteral("cal"), {QStringLiteral("done c1")}},
    });
    NanoVnaClient client(std::move(tr));
    QVERIFY(client.connect());

    QString err;
    QVERIFY(!client.calCoverageKnown());
    QCOMPARE(client.lastCalStartHz(), 0L);
    QCOMPARE(client.lastCalStopHz(), 0L);

    // No sweep active: the step still succeeds but coverage is not recorded.
    QVERIFY(client.calDone(&err));
    QVERIFY(!client.calCoverageKnown());
    QCOMPARE(client.lastCalStartHz(), 0L);
    QCOMPARE(client.lastCalStopHz(), 0L);

    // With a sweep active: coverage becomes known at the sweep range.
    QVERIFY(client.setSweep(1000000, 100000000, 101, &err));
    QVERIFY(client.calDone(&err));
    QVERIFY(client.calCoverageKnown());
    QCOMPARE(client.lastCalStartHz(), 1000000L);
    QCOMPARE(client.lastCalStopHz(), 100000000L);
}

void TestNanoVnaClient::scanPauseResumeEmitWords() {
    auto tr = std::make_unique<ReplayTransport>(std::vector<ReplayTransport::Step>{
        ReplayTransport::Step{QStringLiteral("help"), {}},
        ReplayTransport::Step{QStringLiteral("version"), {QStringLiteral("v1")}},
        ReplayTransport::Step{QStringLiteral("info"), {QStringLiteral("H4")}},
        ReplayTransport::Step{QStringLiteral("scan"), {}},
        ReplayTransport::Step{QStringLiteral("pause"), {}},
        ReplayTransport::Step{QStringLiteral("resume"), {}},
    });
    ReplayTransport* raw = tr.get();
    NanoVnaClient client(std::move(tr));
    QVERIFY(client.connect());

    QString err;
    QVERIFY(client.scan(&err));
    QCOMPARE(raw->sent_.at(3), QStringLiteral("scan"));
    QVERIFY(client.pauseScan(&err));
    QCOMPARE(raw->sent_.at(4), QStringLiteral("pause"));
    QVERIFY(client.resumeScan(&err));
    QCOMPARE(raw->sent_.at(5), QStringLiteral("resume"));
    // scan/pause/resume do NOT refresh cal_ readback.
    QVERIFY(client.calStatus().isEmpty());
}

void TestNanoVnaClient::deviceMiscWordsAndParsing() {
    auto tr = std::make_unique<ReplayTransport>(std::vector<ReplayTransport::Step>{
        ReplayTransport::Step{QStringLiteral("help"), {}},
        ReplayTransport::Step{QStringLiteral("version"), {QStringLiteral("v1")}},
        ReplayTransport::Step{QStringLiteral("info"), {QStringLiteral("H4")}},
        // Happy path: vbat reply line -> volts; freqoffset read -> hz.
        ReplayTransport::Step{QStringLiteral("vbat"), {QStringLiteral("3.82")}},
        ReplayTransport::Step{QStringLiteral("freqoffset"), {QStringLiteral("-150")}},
        // Writes: exact wire words + argument.
        ReplayTransport::Step{QStringLiteral("freqoffset 200"), {}},
        ReplayTransport::Step{QStringLiteral("edelay 1200"), {}},
        ReplayTransport::Step{QStringLiteral("averages 8"), {}},
        // Parse failure: firmware prints a non-numeric line -> honest error.
        ReplayTransport::Step{QStringLiteral("vbat"), {QStringLiteral("battery?")}},
    });
    ReplayTransport* raw = tr.get();
    NanoVnaClient client(std::move(tr));
    QVERIFY(client.connect());

    QString err;
    double volts = 0.0;
    QVERIFY(client.readVbat(volts, &err));
    QVERIFY(qFuzzyCompare(volts, 3.82));

    long fh = 0;
    QVERIFY(client.readFreqOffset(fh, &err));
    QCOMPARE(fh, -150L);

    QVERIFY(client.setFreqOffset(200, &err));
    QCOMPARE(client.freqOffsetHz(), 200L);

    QVERIFY(client.setElectricalDelay(1200, &err));
    QCOMPARE(client.edelayPs(), 1200);

    // Averaging range guard (1..64) rejects before any wire write.
    QVERIFY(!client.setAveraging(0, &err));
    QVERIFY(!err.isEmpty());
    QVERIFY(!client.setAveraging(65, &err));
    QVERIFY(client.setAveraging(8, &err));
    QCOMPARE(client.averagingCount(), 8);

    // Parse failure on vbat -> false + honest error.
    double volts2 = 0.0;
    QVERIFY(!client.readVbat(volts2, &err));
    QVERIFY(!err.isEmpty());

    // Command word/arg order on the wire (3 handshake + the 6 replay steps).
    QCOMPARE(raw->sent_.size(), 9);
    QCOMPARE(raw->sent_.at(3), QStringLiteral("vbat"));
    QCOMPARE(raw->sent_.at(4), QStringLiteral("freqoffset"));
    QCOMPARE(raw->sent_.at(5), QStringLiteral("freqoffset 200"));
    QCOMPARE(raw->sent_.at(6), QStringLiteral("edelay 1200"));
    QCOMPARE(raw->sent_.at(7), QStringLiteral("averages 8"));
    QCOMPARE(raw->sent_.at(8), QStringLiteral("vbat"));
}

void TestNanoVnaClient::deviceMiscRejectWhenDisconnected() {
    NanoVnaClient client;   // no transport -> honest empty state
    QString err;
    double volts = 0.0;
    long fh = 0;
    QVERIFY(!client.isConnected());
    QVERIFY(!client.readVbat(volts, &err));
    QCOMPARE(err, QStringLiteral("NanoVNA 未连接"));
    QVERIFY(!client.readFreqOffset(fh, &err));
    QCOMPARE(err, QStringLiteral("NanoVNA 未连接"));
    QVERIFY(!client.setFreqOffset(100, &err));
    QCOMPARE(err, QStringLiteral("NanoVNA 未连接"));
    QVERIFY(!client.setElectricalDelay(100, &err));
    QCOMPARE(err, QStringLiteral("NanoVNA 未连接"));
    QVERIFY(!client.setAveraging(4, &err));
    QCOMPARE(err, QStringLiteral("NanoVNA 未连接"));
}

void TestNanoVnaClient::tcxoParsesCurrentLine() {
    auto tr = std::make_unique<ReplayTransport>(std::vector<ReplayTransport::Step>{
        ReplayTransport::Step{QStringLiteral("help"), {}, {}},
        ReplayTransport::Step{QStringLiteral("version"), {QStringLiteral("1.2.50")}, {}},
        ReplayTransport::Step{QStringLiteral("info"), {QStringLiteral("NanoVNA-H")}, {}},
        ReplayTransport::Step{QStringLiteral("tcxo"),
            {QStringLiteral("usage: tcxo {TCXO frequency(Hz)}"),
             QStringLiteral("current: 26000000")}, {}},
    });
    NanoVnaClient client(std::move(tr));
    QVERIFY(client.connect());
    long hz = 0;
    QString err;
    QVERIFY(client.readTcxo(hz, &err));
    QCOMPARE(hz, 26000000L);
}

void TestNanoVnaClient::tcxoRejectsUnparseableReply() {
    auto tr = std::make_unique<ReplayTransport>(std::vector<ReplayTransport::Step>{
        ReplayTransport::Step{QStringLiteral("help"), {}, {}},
        ReplayTransport::Step{QStringLiteral("version"), {QStringLiteral("1.0.0")}, {}},
        ReplayTransport::Step{QStringLiteral("info"), {QStringLiteral("H")}, {}},
        // Old firmware without the tcxo word: usage echoed, no current line.
        ReplayTransport::Step{QStringLiteral("tcxo"), {QStringLiteral("unknown")}, {}},
    });
    NanoVnaClient client(std::move(tr));
    QVERIFY(client.connect());
    long hz = -1;
    QString err;
    QVERIFY(!client.readTcxo(hz, &err));
    QCOMPARE(hz, 0L);          // honest: value untouched-from-zero, not fabricated
    QVERIFY(!err.isEmpty());
}

void TestNanoVnaClient::captureScreenReadsExactFrame() {
    const QByteArray frame(kCaptureFrameBytes, '\x5a');
    auto tr = std::make_unique<ReplayTransport>(std::vector<ReplayTransport::Step>{
        ReplayTransport::Step{QStringLiteral("help"), {}, {}},
        ReplayTransport::Step{QStringLiteral("version"), {QStringLiteral("1.2.50")}, {}},
        ReplayTransport::Step{QStringLiteral("info"), {QStringLiteral("NanoVNA-H")}, {}},
        ReplayTransport::Step{QStringLiteral("capture"), {}, frame.toHex()},
    });
    ReplayTransport* raw = tr.get();
    NanoVnaClient client(std::move(tr));
    QVERIFY(client.connect());

    QByteArray out;
    QString err;
    QVERIFY(client.captureScreen(out, &err));
    QCOMPARE(out.size(), kCaptureFrameBytes);
    QVERIFY(out == frame);
    // 3 handshake + the capture word.
    QCOMPARE(raw->sent_.size(), 4);
    QCOMPARE(raw->sent_.at(3), QStringLiteral("capture"));
}

void TestNanoVnaClient::captureScreenShortFrameIsHonest() {
    // Only 100 bytes of the frame -> must fail with an error, never a half frame.
    auto tr = std::make_unique<ReplayTransport>(std::vector<ReplayTransport::Step>{
        ReplayTransport::Step{QStringLiteral("help"), {}, {}},
        ReplayTransport::Step{QStringLiteral("version"), {QStringLiteral("1.2.50")}, {}},
        ReplayTransport::Step{QStringLiteral("info"), {QStringLiteral("NanoVNA-H")}, {}},
        ReplayTransport::Step{QStringLiteral("capture"), {}, QByteArray(100, '\x11').toHex()},
    });
    NanoVnaClient client(std::move(tr));
    QVERIFY(client.connect());
    QByteArray out;
    QString err;
    QVERIFY(!client.captureScreen(out, &err));
    QVERIFY(out.isEmpty());
    QVERIFY(!err.isEmpty());
}

QTEST_MAIN(TestNanoVnaClient)
#include "test_nanovna_client.moc"
