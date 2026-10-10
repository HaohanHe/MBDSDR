// SPDX-License-Identifier: MIT
// Deterministic tests for vna_rf pure functions.
//
// Impedance transforms are pinned against hand-computed values (the same closed
// forms RFTools.py uses); resonance and TDR are verified against synthetic sweep
// data built in the test fixture (no hardware, no mock of the device).
#include <QtTest/QtTest>

#include "vna/vna_rf.h"

#include <cmath>
#include <vector>

using namespace mbdsdr::vna;

class TestVnaRf : public QObject {
    Q_OBJECT
private slots:
    void seriesParallelKnownPoint();
    void reactanceToLC();
    void resonancePinchesSyntheticTank();
    void tdrFindsSyntheticCable();
    void honestEmpty();
};

// Series Rs=50, Xs=50 -> Q=1 -> Rp = 50*(1+1)=100, Xp = 50*(1+1)=100.
void TestVnaRf::seriesParallelKnownPoint() {
    SeriesParallel sp = seriesParallel(50.0, 50.0, 1e6);
    QCOMPARE(sp.q, 1.0);
    QCOMPARE(sp.rp, 100.0);
    QCOMPARE(sp.xp, 100.0);
}

// X=2*pi*1e6*10uH ~= 62.83 Ohm at 1MHz.
void TestVnaRf::reactanceToLC() {
    double L = reactanceToHenries(62.83185, 1e6);
    QVERIFY(std::abs(L - 10e-6) < 1e-9);       // ~10 uH
    double C = reactanceToFarads(-159.155, 1e6);
    QVERIFY(std::abs(C - 1e-9) < 1e-11);       // ~1 nF
    QCOMPARE(reactanceToHenries(-1.0, 1e6), -1.0);   // wrong sign -> sentinel
    QCOMPARE(reactanceToFarads(1.0, 1e6), -1.0);
}

// Synthetic series RLC: R=50, L=1uH, C=100pF -> fr = 1/(2pi sqrt(LC)) ~= 15.9 MHz.
void TestVnaRf::resonancePinchesSyntheticTank() {
    std::vector<double> f; std::vector<std::complex<double>> s11;
    double L = 1e-6, C = 100e-12, R = 50.0, Z0 = 50.0;
    for (int i = 0; i < 201; ++i) {
        double fr = 5e6 + 25e6 * i / 200.0;
        double w = 6.2831853 * fr;
        double Xl = w * L, Xc = -1.0 / (w * C);
        std::complex<double> Z(R, Xl + Xc);
        std::complex<double> s = (Z - Z0) / (Z + Z0);
        f.push_back(fr); s11.push_back(s);
    }
    ResonanceResult r = analyzeResonance(f, s11, Z0);
    QVERIFY(r.valid);
    QVERIFY(std::abs(r.series_fr_hz - 15.9e6) < 0.5e6);   // ~15.9 MHz
    QVERIFY(std::abs(r.esr - 50.0) < 5.0);                   // ESR ~= R
    QVERIFY(r.q > 0.0 && r.q < 50.0);
}

// Synthetic short at distance: S11 flat magnitude ~ -1 phase linearly with freq
// gives a TDR echo. Just check it does not crash and reports a positive length.
void TestVnaRf::tdrFindsSyntheticCable() {
    std::vector<double> f; std::vector<std::complex<double>> s11;
    for (int i = 0; i < 64; ++i) {
        double fr = 1e6 + 30e6 * i / 63.0;
        f.push_back(fr);
        // small reflection midway: phase ramp ~ a 50ns round-trip.
        double ph = 6.2831853 * fr * 50e-9;
        s11.push_back(0.1 * std::complex<double>(std::cos(ph), std::sin(ph)));
    }
    TdrResult t = tdrCable(f, s11, 0.66, 50.0);
    QVERIFY(t.valid);
    QVERIFY(t.cable_length_m > 0.0);
}

void TestVnaRf::honestEmpty() {
    ResonanceResult r = analyzeResonance({1, 2}, {{0, 0}, {0, 0}}, 50.0);
    QVERIFY(!r.valid);                       // too few points
    TdrResult t = tdrCable({}, {}, 0.66, 50.0);
    QVERIFY(!t.valid);
    QCOMPARE(coaxLossDbPerM({1}, {{1, 0}}, 1.0, 0.66), 0.0);
}

QTEST_MAIN(TestVnaRf)
#include "test_vna_rf.moc"
