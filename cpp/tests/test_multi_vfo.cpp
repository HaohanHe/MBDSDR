// SPDX-License-Identifier: MIT
//
// *** NOT HARDWARE / 非硬件合成 ***
//
// Multi-VFO fan-out test. We synthesize a WIDE-BAND block of IQ in software
// (two independent FM carriers at different offsets, each with a different
// modulation tone) and feed it straight into VfoManager -- no radio, no file,
// no network. Each VFO channelizer+demod+resampler must lock onto its own
// carrier and recover its own tone, without crosstalk.
#include <QtTest/QtTest>
#include <complex>
#include <vector>
#include <cmath>
#include <QDir>
#include <QStandardPaths>

#include "dsp/vfo_manager.h"

using namespace mbdsdr::dsp;

namespace {
// Single-bin DFT energy at targetFreq over x (real float @ sampleRate).
double toneEnergy(const std::vector<float>& x, double sampleRate, double targetFreq) {
    double re = 0.0, im = 0.0;
    for (std::size_t n = 0; n < x.size(); ++n) {
        const double ph = 2.0 * M_PI * targetFreq * static_cast<double>(n) / sampleRate;
        re += static_cast<double>(x[n]) * std::cos(ph);
        im -= static_cast<double>(x[n]) * std::sin(ph);
    }
    return re * re + im * im;
}
} // namespace

class TestMultiVfo : public QObject {
    Q_OBJECT
private slots:
    void twoVfosDecodeOwnTones();
    void addRemoveSelect();
    void singleVfoDefaultIsNfmc();
};

// Build a wideband IQ with two FM carriers:
//   carrier A at +100 kHz, modulated by a 1.0 kHz tone (deviation 3 kHz)
//   carrier B at -300 kHz, modulated by a 2.0 kHz tone (deviation 3 kHz)
// *** SYNTHETIC TEST DATA -- NOT HARDWARE ***
static std::vector<std::complex<float>> makeWidebandIq(double fs, int n) {
    std::vector<std::complex<float>> out(n);
    double phA = 0.0, phB = 0.0;
    const double offA = 100e3, offB = -300e3;
    const double fdev = 3e3;
    const double toneA = 1000.0, toneB = 2000.0;
    for (int i = 0; i < n; ++i) {
        const double t = static_cast<double>(i);
        phA += 2.0 * M_PI * fdev / fs * std::cos(2.0 * M_PI * toneA * t / fs);
        phB += 2.0 * M_PI * fdev / fs * std::cos(2.0 * M_PI * toneB * t / fs);
        const double cA = 2.0 * M_PI * offA * t / fs + phA;
        const double cB = 2.0 * M_PI * offB * t / fs + phB;
        out[i] = std::complex<float>(static_cast<float>(std::cos(cA) + std::cos(cB)),
                                     static_cast<float>(std::sin(cA) + std::sin(cB)));
    }
    return out;
}

void TestMultiVfo::twoVfosDecodeOwnTones() {
    const double fs = 2.4e6;
    const double center = 0.0;   // relative baseband; offsets are absolute here
    VfoManager mgr;
    mgr.initDefault(fs, center, "NFM", 12500.0);
    // VFO #1 sits at center (offset 0). Add VFO #2 at +100 kHz.
    const int idA = 1;             // default VFO
    mgr.setFreq(idA, 0.0);         // A at offset 0  -- but our carriers are at +/-
    // Repoint: put VFO A on the +100 kHz carrier, add VFO B at -300 kHz.
    mgr.setFreq(idA, +100e3);
    const int idB = mgr.addVfo(-300e3);
    mgr.setMode(idB, "NFM");

    // Warm up: fill channelizer/demod/resampler state, discard output.
    for (int blk = 0; blk < 30; ++blk) {
        auto iq = makeWidebandIq(fs, 8192);
        mgr.process(iq, fs, center);
    }
    // Now accumulate steady-state 48k audio from BOTH channels.
    std::vector<float> audioA, audioB;
    for (int blk = 0; blk < 30; ++blk) {
        auto iq = makeWidebandIq(fs, 8192);
        mgr.process(iq, fs, center);
        const VfoChannel* chAw = mgr.channel(idA);
        const VfoChannel* chBw = mgr.channel(idB);
        QVERIFY(chAw && chBw);
        audioA.insert(audioA.end(), chAw->audio48k.begin(), chAw->audio48k.end());
        audioB.insert(audioB.end(), chBw->audio48k.begin(), chBw->audio48k.end());
    }
    QVERIFY(audioA.size() > 2000);
    QVERIFY(audioB.size() > 2000);

    // VFO A (on +100 kHz carrier) should recover the 1 kHz tone.
    const double a1k = toneEnergy(audioA, 48000, 1000.0);
    const double a2k = toneEnergy(audioA, 48000, 2000.0);
    QVERIFY2(a1k > a2k * 5.0, "VFO A should decode its own 1 kHz tone, not B's 2 kHz");

    // VFO B (on -300 kHz carrier) should recover the 2 kHz tone.
    const double b1k = toneEnergy(audioB, 48000, 1000.0);
    const double b2k = toneEnergy(audioB, 48000, 2000.0);
    QVERIFY2(b2k > b1k * 5.0, "VFO B should decode its own 2 kHz tone, not A's 1 kHz");

    // Selected audio follows selection.
    QCOMPARE(mgr.selectedId(), idB);   // addVfo selects the new VFO
    mgr.selectVfo(idA);
    auto iq = makeWidebandIq(fs, 8192);
    const std::vector<float>& sel = mgr.process(iq, fs, center);
    QVERIFY(!sel.empty());
    QCOMPARE(mgr.selectedId(), idA);
    QCOMPARE(&sel, &mgr.selected()->audio48k);   // process() returns selected buffer
}

void TestMultiVfo::addRemoveSelect() {
    VfoManager mgr;
    mgr.initDefault(2.4e6, 98.5e6, "NFM", 12500.0);
    QCOMPARE(mgr.count(), 1);
    const int only = mgr.selectedId();
    QVERIFY(!mgr.removeVfo(only));      // cannot drop the last VFO
    QCOMPARE(mgr.count(), 1);

    const int b = mgr.addVfo(99.0e6);
    QCOMPARE(mgr.count(), 2);
    QCOMPARE(mgr.selectedId(), b);     // addVfo selects the new one
    const int c = mgr.addVfo(99.5e6);
    QCOMPARE(mgr.count(), 3);
    QVERIFY(mgr.removeVfo(b));
    QCOMPARE(mgr.count(), 2);
    QVERIFY(mgr.hasId(c));
    QVERIFY(mgr.selectVfo(only));
    QCOMPARE(mgr.selectedId(), only);

    // setMode / setBandwidth mark the channel dirty but don't crash.
    QVERIFY(mgr.setMode(c, "AM"));
    QVERIFY(mgr.setBandwidth(c, 8000.0));
    auto iq = makeWidebandIq(2.4e6, 8192);
    mgr.process(iq, 2.4e6, 98.5e6);

    auto markers = mgr.markers();
    QCOMPARE(markers.size(), 2);
    bool foundSelected = false;
    for (const auto& mk : markers) if (mk.selected) foundSelected = true;
    QVERIFY(foundSelected);
}

void TestMultiVfo::singleVfoDefaultIsNfmc() {
    VfoManager mgr;
    mgr.initDefault(2.4e6, 98.5e6, "NFM", 12500.0);
    QCOMPARE(mgr.count(), 1);
    const VfoChannel* sel = mgr.selected();
    QVERIFY(sel);
    QCOMPARE(sel->mode, QString("NFM"));
    QCOMPARE(sel->bandwidthHz, 12500.0);
    QCOMPARE(sel->freqHz, 98.5e6);
    // Offsets relative to center are zero for the default VFO.
    auto iq = makeWidebandIq(2.4e6, 8192);
    mgr.process(iq, 2.4e6, 98.5e6);
    QVERIFY(!sel->audio48k.empty());
}

QTEST_MAIN(TestMultiVfo)
#include "test_multi_vfo.moc"
