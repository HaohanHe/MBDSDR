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

#include <memory>
#include <vector>

using namespace mbdsdr::vna;

namespace {

// Scripted replay: each write() must equal the expected command (trimmed); on
// match we enqueue the scripted reply lines plus the "ch>" prompt, then pop one
// line per readLine().
class ReplayTransport : public VnaTransport {
public:
    struct Step { QString expect; QStringList lines; };
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
        ++cursor_;
    }
    bool readLine(QByteArray& out) override {
        if (queue_.isEmpty()) { out.clear(); return false; }
        out = queue_.takeFirst().toUtf8();
        return true;
    }
    void resetInputBuffer() override { queue_.clear(); }

    QStringList sent_;
private:
    std::vector<Step> script_;
    QStringList queue_;
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

QTEST_MAIN(TestNanoVnaClient)
#include "test_nanovna_client.moc"
