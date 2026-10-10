// SPDX-License-Identifier: MIT
// Deterministic tests for the NanoVNA "一句话测驻波" onramp (product offline path).
//
// A self-made scripted replay transport (synthetic protocol text, labelled below)
// is injected into engine.vnaClient() via attachTransport; no hardware, no fake
// measurement baked into the product path. Covers the four branches
// (disconnected / uncalibrated / good / bad) plus target-point selection and
// pure-function thresholds.
#include <QtTest/QtTest>

#include "ai/vna_onramp.h"
#include "ai/agent_tools.h"
#include "vna/nanovna_client.h"
#include "vna/vna_rf.h"
#include "dsp/spectrum_engine.h"

#include <cmath>
#include <complex>
#include <memory>
#include <vector>

using namespace mbdsdr;

namespace {

// Synthetic replay: each write() must equal the expected command; reply lines are
// queued plus the "ch>" prompt. Authored independently (same wire format).
class ReplayTransport : public vna::VnaTransport {
public:
    struct Step { QString expect; QStringList lines; };
    explicit ReplayTransport(std::vector<Step> script) : script_(std::move(script)) {}
    bool open() override { open_ = true; return true; }
    void close() override { open_ = false; }
    bool isOpen() const override { return open_; }
    QString errorString() const override { return {}; }
    void write(const QByteArray& data) override {
        QString cmd = QString::fromUtf8(data).trimmed();
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
private:
    std::vector<Step> script_;
    QStringList queue_;
    int cursor_ = 0; bool open_ = false;
};

// Synthetic S11 sweep across [startHz,stopHz]; |g| peaks to `targetGamma` at the
// target index. Only used as a test fixture (labelled synthetic).
std::vector<ReplayTransport::Step> calibratedScript(double targetGamma) {
    const long start = 438000000, stop = 439000000;
    const int N = 101;
    QStringList freqs, data0;
    for (int i = 0; i < N; ++i) {
        long f = start + (stop - start) * i / (N - 1);
        freqs.push_back(QString::number(f));
        double g = targetGamma + 0.4 * std::abs(i - 50) / 50.0;  // dip at i=50
        data0.push_back(QString::number(g, 'f', 6) + " 0.000000");
    }
    return {
        {"help", {}},
        {"version", {"1.0.174-hugen"}},
        {"info", {"NanoVNA-H"}},
        {"cal", {"load open short thru cal'ed"}},
        {"sweep 438000000 439000000 101", {}},
        {"frequencies", freqs},
        {"data 0", data0},
        {"data 1", {}},
    };
}

} // namespace

class TestVnaOnramp : public QObject {
    Q_OBJECT
private slots:
    void parseTarget();
    void classifyThresholds();
    void planSweepSymmetric();
    void nearestIndex();
    void disconnectedHonestGuidance();
    void uncalibratedGuidance();
    void goodReading();
    void badReading();
    void noFrequencyAsksForOne();
    void intentClassification();
    void cableVfTable();
    void tdrNoTypeListsOptions();
    void resonanceNoNominalAsks();
    void resonanceRlcTheoryPin();
};

void TestVnaOnramp::parseTarget() {
    auto v = ai::parseTargetMhz(QString::fromUtf8("天线在438.5MHz驻波多少"));
    QVERIFY(v.has_value());
    QCOMPARE(*v, 438.5);
    QVERIFY(!ai::parseTargetMhz(QString::fromUtf8("随便听听")).has_value());
}

void TestVnaOnramp::classifyThresholds() {
    QCOMPARE(ai::classifyVswr(1.2), QStringLiteral("good"));
    QCOMPARE(ai::classifyVswr(1.7), QStringLiteral("ok"));
    QCOMPARE(ai::classifyVswr(2.6), QStringLiteral("bad"));
}

void TestVnaOnramp::planSweepSymmetric() {
    long s, e; int p;
    ai::planSweep(438500000, s, e, p);
    QCOMPARE(s, 438000000L);
    QCOMPARE(e, 439000000L);
    QCOMPARE(p, 101);
}

void TestVnaOnramp::nearestIndex() {
    QList<double> f{438e6, 438.5e6, 439e6};
    QCOMPARE(ai::nearestIndex(f, 438.49e6), 1);
    QCOMPARE(ai::nearestIndex({}, 1.0), -1);
}

void TestVnaOnramp::disconnectedHonestGuidance() {
    dsp::SpectrumEngine engine;  // honest empty: no transport
    QString out = ai::runVswrOnramp(&engine, QString::fromUtf8("438.5MHz驻波多少"));
    QVERIFY(out.contains(QString::fromUtf8("未检测到 NanoVNA")));
    QVERIFY(out.contains(QString::fromUtf8("PORT 1")));
    QVERIFY(out.contains(QString::fromUtf8("0483:5740")));
}

void TestVnaOnramp::uncalibratedGuidance() {
    dsp::SpectrumEngine engine;
    // Same handshake but "cal" yields no "cal'ed" -> uncalibrated branch.
    auto script = calibratedScript(0.1);
    script[3] = {"cal", {"load open short thru"}};  // no cal'ed
    engine.vnaClient().attachTransport(
        std::make_unique<ReplayTransport>(std::move(script)));
    QString out = ai::runVswrOnramp(&engine, QString::fromUtf8("438.5MHz驻波多少"));
    QVERIFY(out.contains(QString::fromUtf8("尚未校准")));
    QVERIFY(out.contains(QString::fromUtf8("OSL")));
}

void TestVnaOnramp::goodReading() {
    dsp::SpectrumEngine engine;
    // targetGamma=0.1 -> VSWR ~1.22 (good)
    engine.vnaClient().attachTransport(
        std::make_unique<ReplayTransport>(calibratedScript(0.1)));
    QString out = ai::runVswrOnramp(&engine, QString::fromUtf8("438.5MHz驻波多少"));
    QVERIFY(out.contains(QString::fromUtf8("匹配良好")));
    QVERIFY(out.contains(QString::fromUtf8("VSWR")));
    QVERIFY(out.contains(QString::fromUtf8("438.500 MHz")));
    QVERIFY(out.contains(QString::fromUtf8("VSWR")));
    QVERIFY(out.contains(QString::fromUtf8("438.500 MHz")));
}

void TestVnaOnramp::badReading() {
    dsp::SpectrumEngine engine;
    // targetGamma=0.55 -> VSWR ~3.4 (bad)
    engine.vnaClient().attachTransport(
        std::make_unique<ReplayTransport>(calibratedScript(0.55)));
    QString out = ai::runVswrOnramp(&engine, QString::fromUtf8("438.5MHz驻波多少"));
    QVERIFY(out.contains(QString::fromUtf8("驻波偏高")));
}

void TestVnaOnramp::noFrequencyAsksForOne() {
    dsp::SpectrumEngine engine;
    engine.vnaClient().attachTransport(
        std::make_unique<ReplayTransport>(calibratedScript(0.1)));
    QString out = ai::runVswrOnramp(&engine, QString::fromUtf8("我的天线驻波怎么样"));
    QVERIFY(out.contains(QString::fromUtf8("请在问题里给出目标频率")));
}

void TestVnaOnramp::intentClassification() {
    using ai::VnaIntent;
    QCOMPARE(ai::classifyVnaIntent(QString::fromUtf8("天线在438.5MHz驻波多少")), VnaIntent::Vswr);
    QCOMPARE(ai::classifyVnaIntent(QString::fromUtf8("8MHz晶体谐振频率Q是多少")), VnaIntent::Resonance);
    QCOMPARE(ai::classifyVnaIntent(QString::fromUtf8("RG58电缆多长")), VnaIntent::Tdr);
    QCOMPARE(ai::classifyVnaIntent(QString::fromUtf8("这个电感多大")), VnaIntent::Lc);
    QVERIFY(ai::isVnaOnrampIntent(QString::fromUtf8("电缆多长")));
    QVERIFY(!ai::isVnaOnrampIntent(QString::fromUtf8("今天天气怎么样")));
}

void TestVnaOnramp::cableVfTable() {
    auto vf = ai::parseCableVf(QString::fromUtf8("RG-58 电缆"));
    QVERIFY(vf.has_value());
    QCOMPARE(vf->second, 0.66);
    QCOMPARE(ai::parseCableVf(QString::fromUtf8("LMR400 多长"))->second, 0.85);
    QVERIFY(!ai::parseCableVf(QString::fromUtf8("随便什么线")).has_value());
    QVERIFY(ai::cableVfOptionsText().contains("RG-58"));
}

void TestVnaOnramp::tdrNoTypeListsOptions() {
    dsp::SpectrumEngine engine;
    auto script = calibratedScript(0.1);
    engine.vnaClient().attachTransport(std::make_unique<ReplayTransport>(std::move(script)));
    QString out = ai::runVswrOnramp(&engine, QString::fromUtf8("电缆多长"));
    QVERIFY(out.contains(QString::fromUtf8("RG-58")));
    QVERIFY(out.contains(QString::fromUtf8("速度因子")));
}

void TestVnaOnramp::resonanceNoNominalAsks() {
    dsp::SpectrumEngine engine;
    auto script = calibratedScript(0.1);
    engine.vnaClient().attachTransport(std::make_unique<ReplayTransport>(std::move(script)));
    QString out = ai::runVswrOnramp(&engine, QString::fromUtf8("晶体谐振频率Q多少"));
    QVERIFY(out.contains(QString::fromUtf8("标称频率")));
}

void TestVnaOnramp::resonanceRlcTheoryPin() {
    // 已知串联 RLC：L=1uH, C=1pF, Rs=10Ω -> 理论 fr = 1/(2π√(LC)) ≈ 159.15 MHz。
    const double L = 1e-6, C = 1e-12, Rs = 10.0, Z0 = 50.0;
    const double frTheory = 1.0 / (2 * M_PI * std::sqrt(L * C));
    std::vector<double> f; std::vector<std::complex<double>> s11;
    for (int i = 0; i < 401; ++i) {
        double fHz = 140e6 + (180e6 - 140e6) * i / 400.0;
        double xs = 2 * M_PI * fHz * L - 1.0 / (2 * M_PI * fHz * C);
        std::complex<double> z(Rs, xs);
        f.push_back(fHz);
        s11.push_back((z - Z0) / (z + Z0));
    }
    vna::ResonanceResult r = vna::analyzeResonance(f, s11);
    QVERIFY(r.valid);
    // 钉扎：测得串联谐振在理论值 ±0.5 MHz 内。
    QVERIFY(std::abs(r.series_fr_hz - frTheory) < 0.5e6);
    // ESR 应接近 Rs=10Ω（容差 ±2Ω）。
    QVERIFY(std::abs(r.esr - Rs) < 2.0);
}

QTEST_MAIN(TestVnaOnramp)
#include "test_vna_onramp.moc"
