// SPDX-License-Identifier: MIT
// End-to-end offline RDS chain test. No UI, no hardware:
//
//   composite MPX (1 kHz audio tone + 57 kHz RDS BPSK subcarrier, encoded by
//   the same reference encoder as tests/test_rds.cpp)
//     -> FM modulator (+/-75 kHz deviation) -> complex IF IQ at 240 kHz
//     -> real DemodWFM (quadrature discriminator -> 50 us de-emphasis)
//     -> MPX tap (de-emphasized, pre-15 kHz LPF)
//     -> real RdsDecoder
//
// Asserts the station name (PS "MBDSDR  ") and PTY=16 survive the entire
// desktop receive chain, and that the MPX tap is the stream the decoder sees.
#include <QtTest/QtTest>

#include "dsp/demod.h"
#include "dsp/rds_decoder.h"

#include <cmath>
#include <cstdint>
#include <vector>

using namespace mbdsdr::dsp;

// ---------------------------------------------------------------------------
// Independent (test-only) EN 300 401 reference encoder, kept byte-identical to
// tests/test_rds.cpp so this chain test exercises the real decoder against a
// known-good bit stream. CRC G(x)=x^10+x^8+x^7+x^5+x^4+x^3+1 (poly 0x5B9).
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

static uint16_t makeBlockB(int groupType, int versionB, int pty, int field4to0) {
    return (uint16_t)(((groupType & 0xF) << 12) |
                      ((versionB & 1) << 11) |
                      ((pty & 0x1F) << 5) |
                      (field4to0 & 0x1F));
}

// Concatenate frames -> biphase NRZ-I -> 57 kHz BPSK subcarrier (float), at fs.
static std::vector<float> rdsSubcarrier(const std::vector<std::vector<bool>>& frames,
                                        double fs) {
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
    const int leadIn = 600;                   // let de-emphasis / strobe settle
    const int total = leadIn + (int)(syms.size() * sps) + 200;
    std::vector<float> out(total, 0.0f);
    for (int n = 0; n < total; ++n) {
        if (n < leadIn) continue;
        int k = (int)((n - leadIn) / sps);
        if (k < (int)syms.size())
            out[n] = syms[k] * std::cos((float)(2.0 * M_PI * 57000.0 * n / fs));
    }
    return out;
}

class TestRdsChain : public QObject {
    Q_OBJECT
private slots:
    void fmIqToStation();
};

void TestRdsChain::fmIqToStation() {
    // WFM IF rate -- exactly what VfoChannel::rebuild() uses for a WFM channel.
    const double fs = 240000.0;
    const uint16_t PI = 0xDAB8;
    const int PTY = 16;
    const char ps[8] = {'M','B','D','S','D','R',' ',' '};

    // Repeat the four 0A PS segments several times so the block sync and the
    // 8-char PS assembly converge through the real chain.
    std::vector<std::vector<bool>> frames;
    for (int rep = 0; rep < 6; ++rep) {
        for (int seg = 0; seg < 4; ++seg) {
            uint16_t B = makeBlockB(0, 0, PTY, seg);       // 0A, seg in B[1:0]
            uint16_t D = (uint16_t(ps[seg*2] << 8) | uint16_t(ps[seg*2+1]));
            frames.push_back(buildFrame(PI, B, 0x0000, D));
        }
    }

    std::vector<float> rds = rdsSubcarrier(frames, fs);
    const int n = (int)rds.size();

    // Composite MPX = a small 1 kHz audio tone + the RDS subcarrier. The RDS
    // subcarrier is attenuated ~25 dB by the 50 us de-emphasis (corner 3.2 kHz),
    // so it must start out comparatively large; the audio tone is kept small so
    // its post-mix 56/58 kHz sidebands do not swamp the already-weak RDS baseband
    // at the decoder's strobe. Peak MPX 0.85 -> +/-64 kHz deviation, inside the
    // +/-75 kHz budget.
    std::vector<std::complex<float>> iq(n);
    double phase = 0.0;
    for (int i = 0; i < n; ++i) {
        const double t = i / fs;
        const double audio = 0.05 * std::cos(2.0 * M_PI * 1000.0 * t);
        const double mpx = audio + 0.8 * rds[i];
        phase += 2.0 * M_PI * 75000.0 * mpx / fs;
        iq[i] = {(float)std::cos(phase), (float)std::sin(phase)};
    }

    // Stream the IF IQ through the real WFM demod in blocks (like the engine
    // does), tapping the de-emphasized MPX into the real RDS decoder.
    DemodWFM demod(fs, 200000.0);
    RdsDecoder dec(fs);
    const int block = 24000;
    for (int p = 0; p < n; p += block) {
        const int c = std::min(block, n - p);
        std::vector<std::complex<float>> chunk(iq.begin() + p, iq.begin() + p + c);
        demod.process(chunk);
        dec.feed(demod.mpxOut());
    }

    RdsInfo info = dec.info();
    QVERIFY2(info.haveAny, "RDS decoder locked through the real DemodWFM chain");
    QCOMPARE(info.pty, PTY);
    QCOMPARE(info.programService, QString::fromLatin1(ps, 8));
}

QTEST_MAIN(TestRdsChain)
#include "test_rds_chain.moc"
