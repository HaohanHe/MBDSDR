// SPDX-License-Identifier: MIT
//
// SYNTHETIC standalone test for the ACARS decoder (link verification only).
// All frames are generated locally by an independent TX-side model that mirrors
// the public ARINC 618 character framing over MSK (2400 b/s, +/-600 Hz).
// dsp::acars_decoder walks the DECODE direction; round-trip agreement validates
// the protocol core. No real captures, no stored callsigns, fixed seed.
//
// Build (from cpp/):
//   g++ -std=c++17 -D_GNU_SOURCE -I src \
//     tests/test_acars_decode.cpp src/dsp/acars_decoder.cpp \
//     src/dsp/fsk_demod.cpp src/dsp/demod.cpp src/dsp/agc.cpp \
//     -I /home/user/Qt/6.8.2/gcc_64/include \
//     -I /home/user/Qt/6.8.2/gcc_64/include/QtCore \
//     -L /home/user/Qt/6.8.2/gcc_64/lib -lQt6Core -o /tmp/test_acars
//   LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib /tmp/test_acars
#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "dsp/acars_decoder.h"

using namespace mbdsdr::dsp;

static int failures = 0;
static void check(bool c, const char* m) {
    if (!c) { ++failures; std::printf("FAIL: %s\n", m); }
}

static const double kSampleRate = 48000.0;
static const int    kSps = (int)(kSampleRate / kAcarsSymbolRateBd);   // 20

// ---------------------------------------------------------------------------
// TX-side helpers (independent synthetic generator -- mirror of the RX chain).
// ---------------------------------------------------------------------------

// Build wire bytes: preamble x4, SYN, BOT, header, text, ETX, CRC(hi,lo), EOT.
static std::vector<uint8_t> buildFrame(uint8_t bot, const std::string& mode,
                                      const std::string& label, char blockId,
                                      char ack, const std::string& text) {
    std::vector<uint8_t> f;
    for (int i = 0; i < 4; ++i) f.push_back(kAcarsPreambleChar);  // >=3 required
    f.push_back(kAcarsSynChar);
    f.push_back(bot);
    f.push_back(static_cast<uint8_t>(mode[0]));
    f.push_back(static_cast<uint8_t>(mode[1]));
    f.push_back(static_cast<uint8_t>(label[0]));
    f.push_back(static_cast<uint8_t>(label[1]));
    f.push_back(static_cast<uint8_t>(blockId));
    f.push_back(static_cast<uint8_t>(ack));
    for (char c : text) f.push_back(static_cast<uint8_t>(c));
    const int etxIdx = (int)f.size();
    f.push_back(kAcarsEtxChar);
    // Block check over BOT..ETX inclusive. BOT sits at index 5.
    const uint16_t crc = AcarsDecoder::crc16(f.data() + 5, (etxIdx + 1) - 5);
    f.push_back(static_cast<uint8_t>((crc >> 8) & 0xFF));
    f.push_back(static_cast<uint8_t>(crc & 0xFF));
    f.push_back(kAcarsEotChar);
    return f;
}

// Serialize bytes LSB-first (ARINC 618 sends data bits LSB first).
static std::vector<int> bytesToBits(const std::vector<uint8_t>& f) {
    std::vector<int> bits;
    bits.reserve(f.size() * 8);
    for (uint8_t b : f)
        for (int i = 0; i < 8; ++i) bits.push_back((b >> i) & 1);
    return bits;
}

// Bits -> continuous-phase 2-FSK/MSK complex baseband at unit magnitude.
// bit=1 -> +deviation, bit=0 -> -deviation (matches FskDemod sign decision).
static std::vector<std::complex<float>> bitsToIq(const std::vector<int>& bits) {
    std::vector<std::complex<float>> iq;
    double phase = 0.0;
    auto emitSymbol = [&](int bit) {
        const double freq = bit ? kAcarsDeviationHz : -kAcarsDeviationHz;
        for (int s = 0; s < kSps; ++s) {
            phase += 2.0 * M_PI * freq / kSampleRate;
            iq.push_back({static_cast<float>(std::cos(phase)),
                          static_cast<float>(std::sin(phase))});
        }
    };
    // Alternating warm-up so the Mueller-Muller strobe settles before preamble.
    for (int i = 0; i < 24; ++i) emitSymbol(i & 1);
    for (int b : bits) emitSymbol(b);
    // Trailing idle symbols flush the final EOT strobe at end-of-buffer
    // (real streaming always has more samples; the MM loop otherwise drops the
    // last pending symbol). Idle = bit 0 -> no accidental sync in Hunt.
    for (int i = 0; i < 24; ++i) emitSymbol(0);
    return iq;
}

// Deterministic LCG uniform + Box-Muller Gaussian (fixed seed -> reproducible).
struct Rng {
    unsigned int s;
    explicit Rng(unsigned int seed) : s(seed) {}
    float u() {
        s = s * 1103515245u + 12345u;
        return static_cast<float>((s >> 16) & 0x7fff) / 32768.0f;
    }
};
static float gauss(Rng& r) {
    const float u1 = std::max(r.u(), 1e-9f);
    const float u2 = r.u();
    return std::sqrt(-2.0f * std::log(u1)) * std::cos(6.2831853f * u2);
}

// Add complex AWGN to unit-magnitude IQ for a desired Eb/SNR (dB).
// signal power = E|z|^2 = 1; noise power = 2*sigma^2.
static void addNoise(std::vector<std::complex<float>>& iq, double snrDb, Rng& r) {
    const double lin = std::pow(10.0, snrDb / 10.0);
    const double sigma = std::sqrt(1.0 / (2.0 * lin));
    for (auto& z : iq)
        z += std::complex<float>(static_cast<float>(sigma * gauss(r)),
                                 static_cast<float>(sigma * gauss(r)));
}

static std::vector<AcarsPacket> runIq(const std::vector<std::complex<float>>& iq) {
    AcarsDecoder dec(kSampleRate);
    dec.feed(iq);
    return dec.takeNewPackets();
}

// ---------------------------------------------------------------------------
int main() {
    // --- CRC known-answer (CRC-16/CCITT-FALSE check value) ------------------
    {
        const char* s = "123456789";
        check(AcarsDecoder::crc16(reinterpret_cast<const uint8_t*>(s), 9) == 0x29B1,
              "crc16('123456789') == 0x29B1");
    }

    // --- clean air frame -> fields exact + crcOk ----------------------------
    {
        auto f = buildFrame(kAcarsBotAir, "S1", "H1", '1', '_',
                            "HELLO ACARS TEST 123");
        auto iq = bitsToIq(bytesToBits(f));
        auto pk = runIq(iq);
        check(pk.size() == 1, "air frame: exactly one packet emitted");
        if (!pk.empty()) {
            const AcarsPacket& p = pk[0];
            check(p.crcOk,               "air frame: crcOk true");
            check(p.direction == AcarsPacket::Direction::Air, "air frame: direction Air");
            check(p.mode == "S1",        "air frame: mode == S1");
            check(p.label == "H1",       "air frame: label == H1");
            check(p.blockId == "1",      "air frame: blockId == 1");
            check(p.ack == "_",          "air frame: ack == _");
            check(p.text == "HELLO ACARS TEST 123", "air frame: text exact");
        }
    }

    // --- clean ground frame -> direction Ground -----------------------------
    {
        auto f = buildFrame(kAcarsBotGround, "Q0", "AA", 'A', '_',
                            "UPLINK CMD 77");
        auto iq = bitsToIq(bytesToBits(f));
        auto pk = runIq(iq);
        check(pk.size() == 1, "ground frame: exactly one packet emitted");
        if (!pk.empty()) {
            check(pk[0].crcOk, "ground frame: crcOk true");
            check(pk[0].direction == AcarsPacket::Direction::Ground,
                  "ground frame: direction Ground");
            check(pk[0].mode == "Q0" && pk[0].label == "AA" &&
                  pk[0].blockId == "A" && pk[0].text == "UPLINK CMD 77",
                  "ground frame: fields exact");
        }
    }

    // --- tampered byte -> crcOk=false, frame still EMITTED (honest) ----------
    {
        auto f = buildFrame(kAcarsBotAir, "S1", "H1", '1', '_', "TAMPER ME NOW");
        auto bits = bytesToBits(f);
        // Flip one bit deep in the message body (text region, post-CRC computed
        // at TX). Sync/BOT/ETX structure stays intact -> frame still emitted,
        // but the block check must now fail.
        bits[130] ^= 1;
        auto iq = bitsToIq(bits);
        auto pk = runIq(iq);
        bool found = false;
        for (auto& p : pk)
            if (p.direction == AcarsPacket::Direction::Air) found = true;
        check(found, "tampered frame still emitted (not dropped)");
        bool badCrc = false;
        for (auto& p : pk) if (p.direction == AcarsPacket::Direction::Air && !p.crcOk) badCrc = true;
        check(badCrc, "tampered frame reported crcOk=false");
    }

    // --- SNR gradient: 40 frames per point, honest 3-column table ------------
    std::printf("\n=== ACARS SNR gradient (40 frames/point, air frame) ===\n");
    std::printf("%-8s %-10s %-10s %-10s\n", "SNR(dB)", "emitted", "crcOK", "crcBAD");
    const double kSnrPoints[] = {20, 18, 16, 14, 12, 10, 8, 6, 4, 2, 0};
    const int kTrials = 40;
    int lastGoodSnr = -1, firstBadSnr = -1;
    for (double snr : kSnrPoints) {
        int emitted = 0, ok = 0, bad = 0;
        for (int t = 0; t < kTrials; ++t) {
            auto f = buildFrame(kAcarsBotAir, "S1", "H1", '1', '_',
                                "SNR GRADIENT PAYLOAD");
            auto iq = bitsToIq(bytesToBits(f));
            Rng rng(0xC0FFEEu + (unsigned)t * 7919u + (unsigned)(snr * 100));
            addNoise(iq, snr, rng);
            auto pk = runIq(iq);
            for (auto& p : pk) {
                ++emitted;
                if (p.crcOk) ++ok; else ++bad;
            }
        }
        std::printf("%-8.0f %-10d %-10d %-10d\n", snr, emitted, ok, bad);
        if (ok >= (int)(kTrials * 0.8)) lastGoodSnr = (int)snr;
        else if (firstBadSnr < 0) firstBadSnr = (int)snr;
    }
    std::printf("honest failure SNR: >80%% CRC-OK down to %d dB; collapses at ~%d dB\n",
                lastGoodSnr, firstBadSnr);

    // --- pure Gaussian noise (200k samples) -> 0 packets (no fabrication) ----
    {
        std::vector<std::complex<float>> noise(200000);
        Rng rng(0xBEEFu);
        for (auto& z : noise)
            z = std::complex<float>(gauss(rng), gauss(rng));
        auto pk = runIq(noise);
        check(pk.empty(), "pure Gaussian noise 200k samples -> 0 packets");
    }

    // --- idle zeros (silence) -> 0 packets ----------------------------------
    {
        std::vector<std::complex<float>> idle(200000, {0.0f, 0.0f});
        auto pk = runIq(idle);
        check(pk.empty(), "idle zeros 200k samples -> 0 packets");
    }

    std::printf("acars_decoder: %s\n", failures ? "FAILURES" : "all green");
    return failures ? 1 : 0;
}
