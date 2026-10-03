// SPDX-License-Identifier: MIT
//
// *** NOT HARDWARE / 非硬件合成 — SYNTHETIC FIXTURE, TEST ONLY ***
//
// Phase18 Wave1 closed-loop integration for the POCSAG / m17 / VOR decoders.
// Phase17 built the three decoders in isolation; this test wires them into the
// REAL production receive chain and verifies end-to-end:
//
//   synthetic RF/baseband IQ  ->  VfoChannel channelizer  ->  front-end
//     (FskDemod 2-FSK / m17 built-in 4FSK / DemodAM envelope)
//     ->  PocsagDecoder / M17Decoder / VorReceiver
//     ->  VfoManager read-only snapshot  (pocsagMessages / m17Calls / vorResult)
//
// We drive VfoManager DIRECTLY (same style as test_multi_vfo) at a fixed 48 kHz
// source rate with the VFO at offset 0, so the channelizer acts as its channel
// filter and the front-ends see exactly the IF they are configured for. No
// radio, no file, no network, no station database; no pre-stored message /
// callsign / bearing. Pure noise must yield the honest empty state.
#include <QtTest/QtTest>

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdint>
#include <random>
#include <string>
#include <vector>

#include "dsp/vfo_manager.h"
#include "dsp/pocsag_decoder.h"
#include "dsp/m17_decoder.h"
#include "dsp/vor_receiver.h"
#include "core/tokens.h"

using namespace mbdsdr;
using namespace mbdsdr::dsp;

namespace {
constexpr double kPi   = 3.14159265358979323846;
constexpr double kTwoPi = 2.0 * kPi;
constexpr double kFs   = 48000.0;   // synthetic source rate
constexpr double kCenter = 100.0e6; // arbitrary RF centre; VFO offset is 0

// --- 2-FSK modulator (POCSAG) -------------------------------------------
// bits -> continuous-phase complex baseband @ kFs, baud, +/- deviation Hz.
// bit=1 -> +deviation, bit=0 -> -deviation (decoder auto-detects polarity).
std::vector<std::complex<float>> fsk2Iq(const std::vector<int>& bits,
                                        double baud, double dev) {
    const int sps = static_cast<int>(std::lrint(kFs / baud));
    std::vector<std::complex<float>> iq;
    iq.reserve(bits.size() * sps);
    double phase = 0.0;
    for (int b : bits) {
        const double f = (b ? dev : -dev);
        for (int s = 0; s < sps; ++s) {
            phase += kTwoPi * f / kFs;
            iq.push_back({static_cast<float>(std::cos(phase)),
                          static_cast<float>(std::sin(phase))});
        }
    }
    return iq;
}

// --- m17 TX-side helpers (synthetic generator; mirror of test_m17_decoder) -
const std::uint8_t kDc[46] = {
    0xD6,0xB5,0xE2,0x30,0x82,0xFF,0x84,0x62,
    0xBA,0x4E,0x96,0x90,0xD8,0x98,0xDD,0x5D,
    0x0C,0xC8,0x52,0x43,0x91,0x1D,0xF8,0x6E,
    0x68,0x2F,0x35,0xDA,0x14,0xEA,0xCD,0x76,
    0x19,0x8D,0xD5,0x80,0xD1,0x33,0x87,0x13,
    0x57,0x18,0x2D,0x29,0x78,0xC3};

int convOutTx(int poly, int mem) {
    int v = poly & mem, p = 0;
    while (v) { p ^= v & 1; v >>= 1; }
    return p;
}
std::vector<int> convEncode(const std::vector<int>& info) {
    int mem = 0; std::vector<int> out;
    auto feed = [&](int x) {
        mem = ((mem << 1) | (x & 1)) & 31;
        out.push_back(convOutTx(031, mem));
        out.push_back(convOutTx(027, mem));
    };
    for (int b : info) feed(b);
    for (int i = 0; i < 4; ++i) feed(0);
    return out;
}
std::vector<int> punctureP1(const std::vector<int>& coded) {
    int p[61]; for (int i=0;i<61;++i) p[i]=1;
    for (int i=2;i<61;i+=4) p[i]=0;
    std::vector<int> out; int pi=0;
    for (int b : coded) { if (p[pi]) out.push_back(b); if (++pi==61) pi=0; }
    return out;
}
std::vector<int> interleave368(const std::vector<int>& in) {
    std::vector<int> out(368, 0);
    for (int i=0;i<368;++i) { long idx=(45L*i+92L*(long)i*i)%368L; out[idx]=in[i]; }
    return out;
}
void randomize368(int* bits) {
    for (int i=0;i<368;++i) { int dc=(kDc[i/8]>>(7-(i%8)))&1; bits[i]^=dc; }
}
std::vector<int> buildLsfDibits(const std::uint8_t lsf[30]) {
    std::vector<int> info;
    for (int i=0;i<240;++i) info.push_back((lsf[i/8]>>(7-(i%8)))&1);
    std::vector<int> coded=convEncode(info);
    std::vector<int> kept=punctureP1(coded);
    std::vector<int> inter=interleave368(kept);
    int pl[368]; std::copy(inter.begin(),inter.end(),pl); randomize368(pl);
    std::vector<int> bits;
    for (int i=15;i>=0;--i) bits.push_back((0x55F7>>i)&1);
    for (int i=0;i<368;++i) bits.push_back(pl[i]);
    std::vector<int> dibits;
    for (int i=0;i<384;i+=2) dibits.push_back((bits[i]<<1)|bits[i+1]);
    return dibits;
}
std::vector<std::uint8_t> makeLsf(const std::string& src, const std::string& dst,
                                  std::uint16_t type) {
    std::uint8_t lsf[30]={0};
    M17Decoder::encodeCallsign(src, lsf+0);
    M17Decoder::encodeCallsign(dst, lsf+6);
    lsf[12]=(type>>8)&0xFF; lsf[13]=type&0xFF;
    std::uint16_t crc=M17Decoder::crc16(lsf,28);
    lsf[28]=(crc>>8)&0xFF; lsf[29]=crc&0xFF;
    return std::vector<std::uint8_t>(lsf, lsf+30);
}

// dibits -> 4FSK complex baseband @ kFs (4800 sym/s = 10 sps, level*625 Hz).
std::vector<std::complex<float>> fsk4Iq(const std::vector<int>& dibits) {
    static const int kLevel[4]={1,3,-1,-3};
    const double spacing=625.0; const int sps=10;
    std::vector<std::complex<float>> iq; double phase=0.0;
    auto tone=[&](int lvl){
        double f=spacing*lvl;
        for (int s=0;s<sps;++s){ phase+=kTwoPi*f/kFs;
            iq.push_back({(float)std::cos(phase),(float)std::sin(phase)}); }
    };
    for (int pre=0; pre<20; ++pre) tone(kLevel[pre&1]);   // settle strobe
    for (int d : dibits) tone(kLevel[d&3]);
    return iq;
}

// --- VOR composite audio (synthetic; mirrors test_vor_receiver encoder) -----
// bearingDeg: phase of the variable 30 Hz tone; reference 30 Hz carried as FM
// of the 9960 Hz subcarrier (index 16). Output is composite audio m(t).
std::vector<float> vorComposite(double bearingDeg, double seconds) {
    const double b = bearingDeg*kTwoPi/360.0;
    const int n = static_cast<int>(seconds*kFs);
    std::vector<float> x(n, 0.f);
    const double modIdx = 480.0/30.0;
    for (int i=0;i<n;++i){
        double t=i/kFs;
        double var=0.30*std::cos(kTwoPi*30.0*t+b);
        double sub=0.30*std::cos(kTwoPi*9960.0*t+modIdx*std::sin(kTwoPi*30.0*t));
        x[i]=static_cast<float>(var+sub);
    }
    return x;
}
// composite audio -> AM-modulated complex baseband (real-valued carrier at DC,
// amplitude = 1 + m(t)). DemodAM envelope-detects this back to m(t).
std::vector<std::complex<float>> amModulate(const std::vector<float>& audio) {
    std::vector<std::complex<float>> iq(audio.size());
    for (std::size_t i=0;i<audio.size();++i)
        iq[i]={1.0f+audio[i], 0.0f};
    return iq;
}

// Feed a whole IQ stream to the manager in ~4k chunks (streaming state persists).
void feedAll(VfoManager& mgr, const std::vector<std::complex<float>>& iq) {
    constexpr std::size_t kChunk = 4096;
    for (std::size_t off=0; off<iq.size(); off+=kChunk) {
        std::vector<std::complex<float>> blk(iq.begin()+off,
                                             iq.begin()+std::min(off+kChunk, iq.size()));
        mgr.process(blk, kFs, kCenter);
    }
}
} // namespace

class TestDigitalLinkIntegration : public QObject {
    Q_OBJECT
private slots:
    void pocsagDecodesViaChain();
    void m17DecodesViaChain();
    void vorRadialViaChain();
    void modeSwitchClearsOutput();
    void pureNoiseIsHonestEmpty();
};

// POCSAG: known address + numeric text over 2-FSK through channelizer->FskDemod
// -> PocsagDecoder must surface the SAME message on the read-only snapshot.
void TestDigitalLinkIntegration::pocsagDecodesViaChain() {
    VfoManager mgr;
    mgr.initDefault(kFs, kCenter, "POCSAG", 12000.0);
    const int id = mgr.selectedId();

    // Known-answer frame (numeric BCD), identical to the isolated unit test.
    std::vector<int> bits = PocsagDecoder::buildFrameBits(12345, "08671234");
    auto iq = fsk2Iq(bits, tokens::kPocsagBaudBd, tokens::kPocsagDeviationHz);
    feedAll(mgr, iq);

    auto msgs = mgr.pocsagMessages(id);
    QCOMPARE(msgs.size(), std::size_t(1));
    QCOMPARE(msgs[0].address, std::uint32_t(12345));
    QCOMPARE(msgs[0].text, std::string("08671234"));
}

// m17: known SRC callsign in an LSF frame over 4FSK through channelizer ->
// built-in 4FSK front-end -> M17Decoder must surface a CRC-OK call with SRC.
void TestDigitalLinkIntegration::m17DecodesViaChain() {
    VfoManager mgr;
    mgr.initDefault(kFs, kCenter, "m17", 9600.0);
    const int id = mgr.selectedId();

    auto lsf = makeLsf("R2D2", "BROADCAST", 0x0005);
    auto dibits = buildLsfDibits(lsf.data());
    auto iq = fsk4Iq(dibits);
    feedAll(mgr, iq);

    auto calls = mgr.m17Calls(id);
    bool ok = false;
    for (const auto& c : calls)
        if (c.crcOk && c.src == "R2D2") ok = true;
    QVERIFY2(ok, "m17 chain must decode a CRC-OK LSF with SRC=R2D2");
}

// VOR: a known injected bearing over AM composite through channelizer->DemodAM
// -> VorReceiver must lock and report the radial within tolerance.
void TestDigitalLinkIntegration::vorRadialViaChain() {
    VfoManager mgr;
    mgr.initDefault(kFs, kCenter, "VOR", 24000.0);
    const int id = mgr.selectedId();

    const double bearing = 137.0;
    auto audio = vorComposite(bearing, 3.0);   // > one 2 s measurement block
    auto iq = amModulate(audio);
    feedAll(mgr, iq);

    VorResult r = mgr.vorResult(id);
    QVERIFY2(r.locked, "VOR chain must lock on a clean synthetic bearing");
    double diff = std::fabs(r.radialDeg - bearing);
    if (diff > 180.0) diff = 360.0 - diff;
    QVERIFY2(diff <= tokens::kVorRadialToleranceDeg,
             qPrintable(QString("VOR radial %1 vs injected %2 (tol %3 deg)")
                        .arg(r.radialDeg).arg(bearing).arg(tokens::kVorRadialToleranceDeg)));
}

// Switching a channel away from a digital mode must CLEAR its accumulated
// snapshot (rebuild recreates the decoders and empties the queues).
void TestDigitalLinkIntegration::modeSwitchClearsOutput() {
    VfoManager mgr;
    mgr.initDefault(kFs, kCenter, "POCSAG", 12000.0);
    const int id = mgr.selectedId();

    std::vector<int> bits = PocsagDecoder::buildFrameBits(12345, "08671234");
    feedAll(mgr, fsk2Iq(bits, tokens::kPocsagBaudBd, tokens::kPocsagDeviationHz));
    QVERIFY(!mgr.pocsagMessages(id).empty());

    // Switch to NFM -> marks needsRebuild; next process() rebuilds + clears.
    mgr.setMode(id, "NFM");
    std::vector<std::complex<float>> quiet(4096, {1.0f, 0.0f});
    mgr.process(quiet, kFs, kCenter);
    QVERIFY2(mgr.pocsagMessages(id).empty(),
             "mode switch must clear the POCSAG snapshot");

    // Explicit clearDigitalOutputs also empties a fresh channel's queues.
    mgr.setMode(id, "m17");
    mgr.clearDigitalOutputs(id);
    QVERIFY(mgr.m17Calls(id).empty());
    QVERIFY(!mgr.vorResult(id).locked);
}

// Pure noise / tone must NOT fabricate a message, a callsign, or a bearing.
void TestDigitalLinkIntegration::pureNoiseIsHonestEmpty() {
    std::mt19937 rng(12345);
    std::normal_distribution<double> g(0.0, 0.1);
    auto noise = [&](int n){
        std::vector<std::complex<float>> v(n);
        for (auto& s : v) s={static_cast<float>(g(rng)), static_cast<float>(g(rng))};
        return v;
    };

    VfoManager mgr;
    mgr.initDefault(kFs, kCenter, "POCSAG", 12000.0);
    const int id = mgr.selectedId();
    for (int b=0; b<20; ++b) mgr.process(noise(4096), kFs, kCenter);
    QVERIFY2(mgr.pocsagMessages(id).empty(), "pure noise must not fabricate a POCSAG message");

    mgr.setMode(id, "m17");
    for (int b=0; b<20; ++b) mgr.process(noise(4096), kFs, kCenter);
    QVERIFY2(mgr.m17Calls(id).empty(), "pure noise must not fabricate an m17 call");

    mgr.setMode(id, "VOR");
    for (int b=0; b<30; ++b) mgr.process(noise(4096), kFs, kCenter);
    VorResult r = mgr.vorResult(id);
    QVERIFY2(!r.locked, "pure noise must report VOR unlocked (no fabricated bearing)");
}

QTEST_MAIN(TestDigitalLinkIntegration)
#include "test_digital_link_integration.moc"
