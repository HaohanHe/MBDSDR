// SPDX-License-Identifier: MIT
// Synthetic VOR (ICAO Annex 10) receiver tests.
//
// SYNTHETIC FIXTURE -- the encoder below is a test-only, clean-room model of the
// VOR composite baseband (a 30 Hz spatial tone + a 9960 Hz subcarrier frequency-
// modulated by a reference 30 Hz + a 1020 Hz on/off-keyed Morse ID). It mirrors
// mbdsdr_ai/vor_decoder.py's encoder; no real radio, no station database, no
// pre-baked bearings. We drive it through the real VorReceiver and check:
//   * known injected bearings are recovered inside tolerance (0/90/180/270 + an
//     arbitrary angle),
//   * the 1020 Hz Morse station ID decodes to the injected string,
//   * pure noise / silence yields an HONEST unlocked/empty state -- the decoder
//     must NOT fabricate a bearing or guess an ID.
#include <QtTest/QtTest>

#include "dsp/vor_receiver.h"
#include "core/tokens.h"   // kVorRadialToleranceDeg / kVorQualityLock

#include <cmath>
#include <cstdint>
#include <random>
#include <string>
#include <unordered_map>
#include <vector>

using namespace mbdsdr::dsp;

namespace {
constexpr double kFs = 44100.0;
constexpr double kTwoPi = 6.28318530717958647692;

// Synthetic Morse keying (1020 Hz) for an upper-case ID. PARIS timing: a dot is
// 1.2/wpm seconds. Test-only.
std::vector<float> morseKey(const std::string& id, double wpm) {
    static const std::unordered_map<char, std::string> code = {
        {'A',".-"},{'B',"-..."},{'C',"-.-."},{'D',"-.."},{'E',"."},{'F',"..-."},
        {'G',"--."},{'H',"...."},{'I',".."},{'J',".---"},{'K',"-.-"},{'L',".-.."},
        {'M',"--"},{'N',"-."},{'O',"---"},{'P',".--."},{'Q',"--.-"},{'R',".-."},
        {'S',"..."},{'T',"-"},{'U',"..-"},{'V',"...-"},{'W',".--"},{'X',"-..-"},
        {'Y',"-.--"},{'Z',"--.."},
        {'0',"-----"},{'1',".----"},{'2',"..---"},{'3',"...--"},{'4',"....-"},
        {'5',"....."},{'6',"-...."},{'7',"--..."},{'8',"---.."},{'9',"----."}};
    const double unit = 1.2 / wpm;
    const int spb = static_cast<int>(std::round(unit * kFs));
    std::vector<float> s;
    for (int i = 0; i < spb / 2; ++i) s.push_back(0.f);           // lead quiet
    for (char ch : id) {
        auto it = code.find(ch);
        if (it == code.end()) continue;
        const std::string& c = it->second;
        for (size_t i = 0; i < c.size(); ++i) {
            const int dur = (c[i] == '.') ? spb : 3 * spb;
            for (int k = 0; k < dur; ++k)
                s.push_back(static_cast<float>(std::sin(kTwoPi * 1020.0 * k / kFs)));
            if (i + 1 < c.size())
                for (int k = 0; k < spb; ++k) s.push_back(0.f);     // intra-char gap
        }
        for (int k = 0; k < 2 * spb; ++k) s.push_back(0.f);          // inter-char gap
    }
    for (int i = 0; i < spb / 2; ++i) s.push_back(0.f);           // tail quiet
    return s;
}

// Synthetic VOR composite baseband. bearingDeg sets the variable-30 Hz phase;
// the reference 30 Hz is carried as FM of the 9960 Hz subcarrier (index 16).
std::vector<float> encodeVor(double bearingDeg, const std::string& id,
                             double wpm = 20.0, double noiseStd = 0.0,
                             unsigned seed = 12345) {
    const double b = bearingDeg * kTwoPi / 360.0;
    std::vector<float> morse = morseKey(id, wpm);
    const double dur = 3.0;  // seconds; > one 2.0 s measurement block
    const int n = static_cast<int>(std::round(dur * kFs));
    std::vector<float> x(n, 0.f);
    std::mt19937 rng(seed);
    std::normal_distribution<double> noise(0.0, noiseStd);
    const double modIdx = 480.0 / 30.0;  // FM modulation index = 16
    for (int i = 0; i < n; ++i) {
        const double t = i / kFs;
        const double var = 0.30 * std::cos(kTwoPi * 30.0 * t + b);
        const double sub = 0.30 * std::cos(kTwoPi * 9960.0 * t +
                                           modIdx * std::sin(kTwoPi * 30.0 * t));
        x[i] = static_cast<float>(var + sub + noise(rng));
    }
    // Place the 1020 Hz ID early so it fully completes inside the first block.
    const int start = static_cast<int>(0.1 * kFs);
    for (int i = 0; i < static_cast<int>(morse.size()) && start + i < n; ++i)
        x[start + i] += 0.20f * morse[i];
    return x;
}

// Smallest unsigned angular difference (0..180 deg).
double angDiff(double a, double b) {
    double d = std::fmod(a - b, 360.0);
    if (d < 0) d += 360.0;
    if (d > 180.0) d = 360.0 - d;
    return d;
}
} // namespace

class TestVorReceiver : public QObject {
    Q_OBJECT
private slots:
    void knownBearingsLockAndMatch();
    void morseIdDecodes();
    void mildNoiseStillLocks();
    void pureNoiseIsHonestUnlock();
    void silenceIsHonestUnlock();
};

// Inject known bearings, expect a locked radial within tolerance and the ID.
void TestVorReceiver::knownBearingsLockAndMatch() {
    const std::string id = "MBD";
    for (double br : {0.0, 90.0, 180.0, 270.0, 37.0, 213.0}) {
        VorReceiver rx(kFs);
        rx.feed(encodeVor(br, id));
        VorResult r = rx.take();
        QVERIFY2(r.locked, QByteArray("not locked at bearing=").append(QByteArray::number(br)));
        const double err = angDiff(r.radialDeg, br);
        QVERIFY2(err <= mbdsdr::tokens::kVorRadialToleranceDeg,
                 QByteArray("bearing=").append(QByteArray::number(br))
                     .append(" measured=").append(QByteArray::number(r.radialDeg))
                     .append(" err=").append(QByteArray::number(err)));
    }
}

void TestVorReceiver::morseIdDecodes() {
    VorReceiver rx(kFs);
    rx.feed(encodeVor(90.0, "MB"));
    VorResult r = rx.take();
    QVERIFY(r.locked);
    QCOMPARE(r.morseId, QStringLiteral("MB"));
}

// Mild additive noise: still locks, bearing error stays inside tolerance.
void TestVorReceiver::mildNoiseStillLocks() {
    VorReceiver rx(kFs);
    rx.feed(encodeVor(120.0, "MBD", 20.0, 0.05, 777));
    VorResult r = rx.take();
    QVERIFY2(r.locked, "should still lock at mild noise");
    QVERIFY2(angDiff(r.radialDeg, 120.0) <= mbdsdr::tokens::kVorRadialToleranceDeg,
             "bearing error exceeds tolerance under mild noise");
}

// Pure noise: honest empty state. No fabricated bearing, no guessed ID.
void TestVorReceiver::pureNoiseIsHonestUnlock() {
    std::mt19937 rng(42);
    std::normal_distribution<double> ng(0.0, 0.2);
    const int n = static_cast<int>(3.0 * kFs);
    std::vector<float> x(n);
    for (int i = 0; i < n; ++i) x[i] = static_cast<float>(ng(rng));

    VorReceiver rx(kFs);
    rx.feed(x);
    VorResult r = rx.take();
    QVERIFY2(!r.locked, "pure noise must NOT report a locked bearing");
    QVERIFY2(r.morseId.isEmpty(), "pure noise must NOT fabricate an ID");
    QVERIFY2(r.quality < mbdsdr::tokens::kVorQualityLock, "noise scatter must read low");
}

// Total silence: also an honest unlock (guards the atan2(0,0)==0 false-lock).
void TestVorReceiver::silenceIsHonestUnlock() {
    std::vector<float> x(static_cast<int>(3.0 * kFs), 0.0f);
    VorReceiver rx(kFs);
    rx.feed(x);
    VorResult r = rx.take();
    QVERIFY2(!r.locked, "silence must NOT lock");
}

QTEST_MAIN(TestVorReceiver)
#include "test_vor_receiver.moc"
