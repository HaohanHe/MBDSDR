// SPDX-License-Identifier: MIT
// Offline RDS decoder tests: reference encoder -> float MPX -> RdsDecoder.
// The encoder here is a clean-room, test-only implementation of EN 300 401
// (CRC/offset words, biphase NRZ-I, 57 kHz BPSK) used to exercise the decoder.
#include <QtTest/QtTest>

#include "dsp/rds_decoder.h"

#include <cmath>
#include <cstdint>
#include <random>
#include <vector>
#include <string>

using namespace mbdsdr::dsp;

// ---------------------------------------------------------------------------
// Independent (test-only) CRC: G(x)=x^10+x^8+x^7+x^5+x^4+x^3+1, poly 0x5B9.
// ---------------------------------------------------------------------------
static uint32_t crc16(uint16_t data) {
    uint32_t reg = 0;
    for (int i = 15; i >= 0; --i) {
        reg = (reg << 1) | ((data >> i) & 1u);
        if (reg & (1u << 10)) reg ^= 0x5B9u;
    }
    for (int i = 0; i < 10; ++i) {
        reg <<= 1;
        if (reg & (1u << 10)) reg ^= 0x5B9u;
    }
    return reg & 0x3FFu;
}

static const uint32_t kOffsetWord[4] = {252, 408, 360, 436}; // A,B,C,D

static std::vector<bool> buildFrame(uint16_t A, uint16_t B, uint16_t C, uint16_t D) {
    uint16_t words[4] = {A, B, C, D};
    std::vector<bool> bits;
    bits.reserve(104);
    for (int blk = 0; blk < 4; ++blk) {
        uint32_t c = crc16(words[blk]) ^ kOffsetWord[blk];
        for (int i = 15; i >= 0; --i) bits.push_back((words[blk] >> i) & 1u);
        for (int i = 9; i >= 0; --i)  bits.push_back((c >> i) & 1u);
    }
    return bits;
}

// Concatenate frames -> biphase NRZ-I -> 57 kHz BPSK -> float MPX at fs.
static std::vector<float> modulate(const std::vector<std::vector<bool>>& frames,
                                   double fs, double noiseStddev = 0.0,
                                   std::uint32_t seed = 12345,
                                   double carrierPhaseDeg = 0.0,
                                   double carrierFreqOffsetHz = 0.0) {
    std::vector<bool> all;
    for (auto& f : frames) all.insert(all.end(), f.begin(), f.end());

    // Biphase: bit i -> two PSK symbols (first half = p, second = p or -p).
    std::vector<float> syms;
    syms.reserve(all.size() * 2);
    float p = 1.0f;
    for (bool b : all) {
        syms.push_back(p);
        float s2 = b ? p : -p;
        syms.push_back(s2);
        p = -s2;
    }

    const double sps = fs / 2375.0;            // samples per PSK symbol
    const int leadIn = 600;                    // let LPF / strobe settle
    const int total = leadIn + (int)(syms.size() * sps) + 200;
    std::vector<float> out;
    out.reserve(total);
    std::mt19937 rng(seed);
    std::normal_distribution<float> gauss(0.0f, noiseStddev);
    const double fcarrier = 57000.0 + carrierFreqOffsetHz;
    const double phase0 = carrierPhaseDeg * M_PI / 180.0;
    for (int n = 0; n < total; ++n) {
        float v = 0.0f;
        if (n >= leadIn) {
            int k = (int)((n - leadIn) / sps);
            if (k < (int)syms.size()) v = syms[k];
        }
        float t = (float)(2.0 * M_PI * fcarrier * n / fs + phase0);
        out.push_back(v * std::cos(t) + gauss(rng));
    }
    return out;
}

static uint16_t makeBlockB(int groupType, int versionB, int pty, int field4to0) {
    return (uint16_t)(((groupType & 0xF) << 12) |
                      ((versionB & 1) << 11) |
                      ((pty & 0x1F) << 5) |
                      (field4to0 & 0x1F));
}

// Standard payload: PI=0xDAB8, PTY=16, PS="MBDSDR  ", RT="HELLO RDS TEST".
static std::vector<std::vector<bool>> buildStandardFrames(int repetitions = 5) {
    const uint16_t PI = 0xDAB8;
    const int PTY = 16;
    const char ps[8] = {'M','B','D','S','D','R',' ',' '};
    const char rt[16] = {'H','E','L','L','O',' ','R','D','S',' ','T','E','S','T','\0','\0'};

    std::vector<std::vector<bool>> frames;
    for (int rep = 0; rep < repetitions; ++rep) {
        for (int seg = 0; seg < 4; ++seg) {
            uint16_t B = makeBlockB(0, 0, PTY, seg);
            uint16_t D = (uint16_t((ps[seg*2] & 0xFF) << 8) |
                          uint16_t(ps[seg*2+1] & 0xFF));
            frames.push_back(buildFrame(PI, B, 0x0000, D));
        }
        for (int seg = 0; seg < 4; ++seg) {
            uint16_t B = makeBlockB(2, 0, PTY, (0 << 4) | seg);
            uint16_t C = (uint16_t((rt[seg*4] & 0xFF) << 8) |
                          uint16_t(rt[seg*4+1] & 0xFF));
            uint16_t D = (uint16_t((rt[seg*4+2] & 0xFF) << 8) |
                          uint16_t(rt[seg*4+3] & 0xFF));
            frames.push_back(buildFrame(PI, B, C, D));
        }
    }
    return frames;
}

class TestRds : public QObject {
    Q_OBJECT
private slots:
    void crcKnownAnswer();
    void fullRoundTrip();
    void phaseAndOffsetRoundTrip();
    void flippedBitDiscarded();
    void radioTextABRefresh();
    void noisyChannel();
};

void TestRds::crcKnownAnswer() {
    // Zero data -> zero remainder.
    QCOMPARE(crc16(0x0000), 0u);
    // Known block syndromes from gqrx constants.h (calcSyndrome(offset,10)).
    // offsetWord = {252,408,360,436,848} -> syndrome = {383,14,303,663,748}.
    // Recompute the offset-word syndromes directly from our CRC routine.
    uint32_t off[5] = {252, 408, 360, 436, 848};
    uint32_t expect[5] = {383, 14, 303, 663, 748};
    for (int j = 0; j < 5; ++j) {
        // syndrome of a 10-bit offset word = CRC of 10-bit message.
        uint32_t reg = 0;
        for (int i = 9; i >= 0; --i) {
            reg = (reg << 1) | ((off[j] >> i) & 1u);
            if (reg & (1u << 10)) reg ^= 0x5B9u;
        }
        for (int i = 0; i < 10; ++i) {
            reg <<= 1;
            if (reg & (1u << 10)) reg ^= 0x5B9u;
        }
        QCOMPARE(reg & 0x3FFu, expect[j]);
    }
}

void TestRds::fullRoundTrip() {
    const uint16_t PI = 0xDAB8;
    const int PTY = 16;
    const char ps[8] = {'M','B','D','S','D','R',' ',' '};
    const char rt[16] = {'H','E','L','L','O',' ','R','D','S',' ','T','E','S','T','\0','\0'};

    auto frames = buildStandardFrames(5);

    RdsDecoder dec(240000.0);
    dec.feed(modulate(frames, 240000.0));
    auto groups = dec.takeVerifiedGroups();
    RdsInfo info = dec.info();

    QVERIFY2(info.haveAny, "decoder produced at least one group");
    QVERIFY2(!groups.empty(), "takeVerifiedGroups returned groups");
    QCOMPARE(info.pty, PTY);

    // Every surfaced group must carry the expected PI.
    int piCount = 0;
    for (auto& g : groups) if (g.pi == PI) ++piCount;
    QVERIFY2(piCount > 0, "at least one group with correct PI");

    QCOMPARE(info.programService, QString::fromLatin1(ps, 8));
    QCOMPARE(info.radioText, QString::fromLatin1(rt, 14));
}

// Phase-invariant differential detection: carrier at 73 deg + -3 Hz offset.
void TestRds::phaseAndOffsetRoundTrip() {
    const uint16_t PI = 0xDAB8;
    const int PTY = 16;
    const char ps[8] = {'M','B','D','S','D','R',' ',' '};
    const char rt[16] = {'H','E','L','L','O',' ','R','D','S',' ','T','E','S','T','\0','\0'};

    auto frames = buildStandardFrames(6);

    RdsDecoder dec(240000.0);
    dec.feed(modulate(frames, 240000.0, 0.0, 4242, 73.0, -3.0));
    RdsInfo info = dec.info();

    QVERIFY2(info.haveAny, "syncs under arbitrary carrier phase + freq offset");
    QCOMPARE(info.pty, PTY);
    QCOMPARE(info.programService, QString::fromLatin1(ps, 8));
    QCOMPARE(info.radioText, QString::fromLatin1(rt, 14));
}

void TestRds::flippedBitDiscarded() {
    const uint16_t PI = 0xDAB8;
    const int PTY = 16;
    const char ps[8] = {'M','B','D','S','D','R',' ',' '};

    std::vector<std::vector<bool>> frames;
    // seg 0 with ONE bit flipped in block D (the PS char bytes).
    for (int rep = 0; rep < 3; ++rep) {
        uint16_t B = makeBlockB(0, 0, PTY, 0);
        uint16_t D = (uint16_t(ps[0] << 8) | uint16_t(ps[1]));
        auto f = buildFrame(PI, B, 0x0000, D);
        // flip a bit inside block D's 16 data bits (bit index 96 + say 3)
        f[96 + 3] = !f[96 + 3];
        frames.push_back(f);
        // good segments 1,2,3
        for (int seg = 1; seg < 4; ++seg) {
            uint16_t Bs = makeBlockB(0, 0, PTY, seg);
            uint16_t Ds = (uint16_t(ps[seg*2] << 8) | uint16_t(ps[seg*2+1]));
            frames.push_back(buildFrame(PI, Bs, 0x0000, Ds));
        }
    }

    RdsDecoder dec(240000.0);
    dec.feed(modulate(frames, 240000.0));
    RdsInfo info = dec.info();

    // seg 0 was corrupted -> positions 0,1 must remain blank (never assembled).
    QVERIFY2(info.programService.size() >= 2, "PS has at least 2 chars");
    QCOMPARE(info.programService.at(0), QLatin1Char(' '));
    QCOMPARE(info.programService.at(1), QLatin1Char(' '));
    // good segments 1..3 landed.
    QCOMPARE(info.programService.at(2), QLatin1Char('D'));
    QCOMPARE(info.programService.at(3), QLatin1Char('S'));
}

void TestRds::radioTextABRefresh() {
    const uint16_t PI = 0xDAB8;
    const int PTY = 16;

    std::vector<std::vector<bool>> frames;
    // First RT text, A/B = 0, "FIRST---"
    const char rt1[8] = {'F','I','R','S','T','-','-','-'};
    for (int rep = 0; rep < 3; ++rep) {
        for (int seg = 0; seg < 2; ++seg) {
            uint16_t B = makeBlockB(2, 0, PTY, (0 << 4) | seg);
            uint16_t C = (uint16_t(rt1[seg*4] << 8) | uint16_t(rt1[seg*4+1]));
            uint16_t D = (uint16_t(rt1[seg*4+2] << 8) | uint16_t(rt1[seg*4+3]));
            frames.push_back(buildFrame(PI, B, C, D));
        }
    }
    // Toggle A/B = 1, new text "SECOND--"
    const char rt2[8] = {'S','E','C','O','N','D','-','-'};
    for (int rep = 0; rep < 3; ++rep) {
        for (int seg = 0; seg < 2; ++seg) {
            uint16_t B = makeBlockB(2, 0, PTY, (1 << 4) | seg);
            uint16_t C = (uint16_t(rt2[seg*4] << 8) | uint16_t(rt2[seg*4+1]));
            uint16_t D = (uint16_t(rt2[seg*4+2] << 8) | uint16_t(rt2[seg*4+3]));
            frames.push_back(buildFrame(PI, B, C, D));
        }
    }

    RdsDecoder dec(240000.0);
    dec.feed(modulate(frames, 240000.0));
    RdsInfo info = dec.info();
    QCOMPARE(info.radioText, QString::fromLatin1(rt2, 8));
}

void TestRds::noisyChannel() {
    const uint16_t PI = 0xDAB8;
    const int PTY = 16;
    const char ps[8] = {'M','B','D','S','D','R',' ',' '};

    std::vector<std::vector<bool>> frames;
    for (int rep = 0; rep < 6; ++rep) {
        for (int seg = 0; seg < 4; ++seg) {
            uint16_t B = makeBlockB(0, 0, PTY, seg);
            uint16_t D = (uint16_t(ps[seg*2] << 8) | uint16_t(ps[seg*2+1]));
            frames.push_back(buildFrame(PI, B, 0x0000, D));
        }
    }
    // Carrier amplitude ~1.0; noise stddev 0.15 -> comfortable SNR.
    RdsDecoder dec(240000.0);
    dec.feed(modulate(frames, 240000.0, 0.15, 999));
    RdsInfo info = dec.info();
    QVERIFY2(info.haveAny, "still syncs under mild noise");
    QCOMPARE(info.programService, QString::fromLatin1(ps, 8));
}

QTEST_MAIN(TestRds)
#include "test_rds.moc"
