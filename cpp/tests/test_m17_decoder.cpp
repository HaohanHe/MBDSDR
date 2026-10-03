// SPDX-License-Identifier: MIT
//
// SYNTHETIC test fixture for the M17 decoder (synthetic, link-verification
// only).  All frames below are generated locally by an independent TX-side
// model that mirrors the public M17 spec; no real captures, no stored callsigns.
//
// The TX model lives in this test and walks the ENCODE direction
// (LSF bytes -> conv -> puncture -> interleave -> randomize -> sync), while
// dsp::m17_decoder walks the DECODE direction.  Round-trip agreement is what
// validates the protocol core.
#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "dsp/m17_decoder.h"

using namespace mbdsdr::dsp;

static int failures = 0;
static void check(bool c, const char* m) {
    if (!c) { ++failures; std::printf("FAIL: %s\n", m); }
}

// ---------------------------------------------------------------------------
// TX-side helpers (synthetic generator) -- mirror of the RX chain.
// ---------------------------------------------------------------------------
static const uint8_t kDc[46] = {
    0xD6,0xB5,0xE2,0x30,0x82,0xFF,0x84,0x62,
    0xBA,0x4E,0x96,0x90,0xD8,0x98,0xDD,0x5D,
    0x0C,0xC8,0x52,0x43,0x91,0x1D,0xF8,0x6E,
    0x68,0x2F,0x35,0xDA,0x14,0xEA,0xCD,0x76,
    0x19,0x8D,0xD5,0x80,0xD1,0x33,0x87,0x13,
    0x57,0x18,0x2D,0x29,0x78,0xC3};

static int convOutTx(int poly, int mem) {
    int v = poly & mem, p = 0;
    while (v) { p ^= v & 1; v >>= 1; }
    return p;
}

// infoBits -> 2*(infoBits+4) coded bits, g1 then g2.
static std::vector<int> convEncode(const std::vector<int>& info) {
    int mem = 0;
    std::vector<int> out;
    auto feed = [&](int x) {
        mem = ((mem << 1) | (x & 1)) & 31;
        out.push_back(convOutTx(031, mem));
        out.push_back(convOutTx(027, mem));
    };
    for (int b : info) feed(b);
    for (int i = 0; i < 4; ++i) feed(0);
    return out;
}

// 488 coded bits -> 368 kept bits (P1).
static std::vector<int> punctureP1(const std::vector<int>& coded) {
    int p[61]; for (int i=0;i<61;++i) p[i]=1;
    for (int i=2;i<61;i+=4) p[i]=0;
    std::vector<int> out;
    int pi = 0;
    for (int b : coded) {
        if (p[pi]) out.push_back(b);
        if (++pi == 61) pi = 0;
    }
    return out;
}

// interleave: out[p(i)] = in[i].
static std::vector<int> interleave368(const std::vector<int>& in) {
    std::vector<int> out(368, 0);
    for (int i = 0; i < 368; ++i) {
        long idx = (45L*i + 92L*(long)i*i) % 368L;
        out[idx] = in[i];
    }
    return out;
}

static void randomize368(int* bits) {
    for (int i = 0; i < 368; ++i) {
        int dc = (kDc[i/8] >> (7 - (i%8))) & 1;
        bits[i] ^= dc;
    }
}

// 30-byte LSF -> 192 dibits (LSF frame).
static std::vector<int> buildLsfDibits(const uint8_t lsf[30]) {
    std::vector<int> info;
    for (int i = 0; i < 240; ++i) info.push_back((lsf[i/8] >> (7 - (i%8))) & 1);
    std::vector<int> coded = convEncode(info);      // 488
    std::vector<int> kept = punctureP1(coded);      // 368
    std::vector<int> inter = interleave368(kept);  // 368
    int pl[368]; std::copy(inter.begin(), inter.end(), pl);
    randomize368(pl);                               // transmit order

    // Assemble 384-bit frame: 16 sync bits (0x55F7 MSB) + 368 payload bits.
    std::vector<int> bits;
    for (int i = 15; i >= 0; --i) bits.push_back((0x55F7 >> i) & 1);
    for (int i = 0; i < 368; ++i) bits.push_back(pl[i]);

    // bits -> dibits: dibit = (b[2i]<<1) | b[2i+1]
    std::vector<int> dibits;
    for (int i = 0; i < 384; i += 2) dibits.push_back((bits[i] << 1) | bits[i+1]);
    return dibits;
}

// Build a 30-byte LSF for given src/dst/type.
static std::vector<uint8_t> makeLsf(const std::string& src, const std::string& dst,
                                     uint16_t type) {
    uint8_t lsf[30] = {0};
    M17Decoder::encodeCallsign(src, lsf + 0);
    M17Decoder::encodeCallsign(dst, lsf + 6);
    lsf[12] = (type >> 8) & 0xFF;
    lsf[13] = type & 0xFF;
    uint16_t crc = M17Decoder::crc16(lsf, 28);
    lsf[28] = (crc >> 8) & 0xFF;
    lsf[29] = crc & 0xFF;
    return std::vector<uint8_t>(lsf, lsf + 30);
}

// Golay(24,12) encode (TX side) matching decoder's syndrome.
static uint32_t golayEncode(int data12) {
    uint32_t reg = data12 & 0xFFFu;
    for (int i = 0; i < 12; ++i) {
        if (reg & 1u) reg ^= 0xC75u;
        reg >>= 1;
    }
    uint32_t cw = reg | ((uint32_t)(data12 & 0xFFF) << 11);
    int pc = 0; uint32_t t = cw; while (t) { pc ^= t & 1; t >>= 1; }
    return ((cw << 1) | pc) & 0xFFFFFFu;
}

// ---------------------------------------------------------------------------
int main() {
    // --- CRC16 known-answer vectors (from m17_adapter.py self-test) ---------
    {
        uint8_t a = 'A';
        check(M17Decoder::crc16(&a, 1) == 0x206E, "CRC16('A') == 0x206E");
        const char* s = "123456789";
        check(M17Decoder::crc16((const uint8_t*)s, 9) == 0x772B, "CRC16('123456789') == 0x772B");
    }

    // --- callsign base-40 round trip ---------------------------------------
    {
        uint8_t enc[6];
        check(M17Decoder::encodeCallsign("W9GL", enc), "encode W9GL ok");
        check(M17Decoder::decodeCallsign(enc) == "W9GL", "W9GL round-trip");
        uint8_t allFf[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};
        check(M17Decoder::decodeCallsign(allFf) == "BROADCAST",
              "all-ones -> BROADCAST");
        uint8_t enc2[6];
        M17Decoder::encodeCallsign("", enc2);
        check(M17Decoder::decodeCallsign(enc2) == "BROADCAST", "empty dst -> BROADCAST");
        check(M17Decoder::encodeCallsign("AB12XYZ", enc), "encode long cs");
        check(M17Decoder::decodeCallsign(enc) == "AB12XYZ", "long cs round-trip");
    }

    // --- Golay(24,12) error correction --------------------------------------
    {
        uint32_t cw = golayEncode(0x123);
        uint32_t copy = cw;
        check(M17Decoder::golay24Correct(copy) == 0, "Golay clean word");
        // flip 1,2,3 errors -> correctable
        for (int n = 1; n <= 3; ++n) {
            uint32_t bad = cw ^ ((1u << (3*n)) | (1u << (5*n+1)) | (1u << (7*n)));
            int e = M17Decoder::golay24Correct(bad);
            check(bad == cw && e >= n && e <= 3, "Golay corrects up to 3");
        }
        // flip 4 errors that is genuinely uncorrectable (syndrome not in the
        // weight-<=3 table) -> honest -1
        uint32_t bad4 = cw ^ 0x800007u;
        int e4 = M17Decoder::golay24Correct(bad4);
        check(e4 == -1, "Golay flags uncorrectable 4-bit errors");
    }

    // --- LSF frame full decode (dibit level, deterministic) ----------------
    {
        auto lsf = makeLsf("W9GL", "", 0x0005);   // stream voice
        auto dibits = buildLsfDibits(lsf.data());
        M17Decoder dec;
        dec.feedDibits(dibits);
        auto calls = dec.takeCalls();
        check(calls.size() == 1, "exactly one LSF call");
        if (!calls.empty()) {
            check(calls[0].crcOk, "LSF CRC ok");
            check(calls[0].src == "W9GL", "decoded source callsign");
            check(calls[0].dst == "BROADCAST", "decoded dst = broadcast");
            check(calls[0].isStream == true, "TYPE says stream");
            check(calls[0].payloadClass == 2, "TYPE payload class = voice");
            check(calls[0].voiceUndecoded == true, "voice flagged undecoded");
            check(calls[0].meta.size() == 14, "META length 14");
        }
    }

    // --- LSF with explicit destination -------------------------------------
    {
        auto lsf = makeLsf("DL1XYZ", "SM6YMP", 0x0003);  // stream + data class
        auto dibits = buildLsfDibits(lsf.data());
        M17Decoder dec;
        dec.feedDibits(dibits);
        auto calls = dec.takeCalls();
        check(calls.size() == 1 && calls[0].crcOk, "explicit-dst LSF crc ok");
        if (!calls.empty()) {
            check(calls[0].src == "DL1XYZ", "src DL1XYZ");
            check(calls[0].dst == "SM6YMP", "dst SM6YMP");
            check(calls[0].isStream == true, "stream flag");
            check(calls[0].payloadClass == 1, "data class");
        }
    }

    // --- a few channel bit errors must still decode (Viterbi/Golay robust) -
    {
        auto lsf = makeLsf("N0CALL", "W1AW", 0x0005);
        auto dibits = buildLsfDibits(lsf.data());
        // Flip 3 payload dibits (small noise). Viterbi should absorb it.
        for (int i = 20; i < 23; ++i) dibits[i] ^= 1;
        M17Decoder dec;
        dec.feedDibits(dibits);
        auto calls = dec.takeCalls();
        check(calls.size() == 1 && calls[0].crcOk,
              "LSF survives a few flipped bits (FEC)");
        if (!calls.empty()) check(calls[0].src == "N0CALL", "FEC still recovers callsign");
    }

    // --- pure noise -> honest empty (no invented callsign) ------------------
    {
        std::vector<int> noise(4000);
        unsigned int s = 12345;
        for (auto& b : noise) { s = s * 1103515245u + 12345u; b = (s >> 16) & 3; }
        M17Decoder dec;
        dec.feedDibits(noise);
        auto calls = dec.takeCalls();
        bool anyReal = false;
        for (auto& c : calls) if (c.crcOk && !c.src.empty()) anyReal = true;
        check(!anyReal, "pure noise yields no fabricated call");
    }

    // --- data frame (0xFF5D) is reported as raw payload ---------------------
    {
        // sync 0xFF5D + 368 random-but-consistent payload bits.
        std::vector<int> bits;
        for (int i = 15; i >= 0; --i) bits.push_back((0xFF5D >> i) & 1);
        int pl[368];
        unsigned int s = 99;
        for (int i = 0; i < 368; ++i) { s = s*1103515245u+12345u; pl[i]=(s>>16)&1; }
        // Data frames are transmitted already-derandomized on wire? For this
        // fixture we feed derandomized bits directly (decoder derandomizes).
        int tx[368]; std::copy(pl, pl+368, tx);
        // The decoder XORs DC, so feed DC^pl to land on pl after derandomize.
        for (int i=0;i<368;++i){ int dc=(kDc[i/8]>>(7-(i%8)))&1; bits.push_back(tx[i]^dc); }
        std::vector<int> dibits;
        for (int i = 0; i < 384; i += 2) dibits.push_back((bits[i]<<1)|bits[i+1]);
        M17Decoder dec;
        dec.feedDibits(dibits);
        auto calls = dec.takeCalls();
        bool found = false;
        for (auto& c : calls) if (c.frameKind == 2) { found = true; check(c.payload.size()==46, "data frame payload 46 bytes"); }
        check(found, "data frame (0xFF5D) detected");
    }

    // --- RF round-trip: dibits -> 4FSK IQ -> feedIQ -> decoded call --------
    {
        auto lsf = makeLsf("R2D2", "BROADCAST", 0x0005);
        auto dibits = buildLsfDibits(lsf.data());
        // dibit -> level {+1,+3,-1,-3}; level*625 Hz offset; 10 sps @48k.
        static const int kLevel[4] = {1, 3, -1, -3};
        const double sr = 48000.0, spacing = 625.0;
        const int sps = 10;
        std::vector<std::complex<float>> iq;
        double phase = 0.0;
        // small preamble of alternating symbols to let the strobe settle.
        for (int pre = 0; pre < 20; ++pre) {
            int lvl = kLevel[pre & 1];
            double f = spacing * lvl;
            for (int s = 0; s < sps; ++s) {
                phase += 2.0 * M_PI * f / sr;
                iq.push_back({(float)std::cos(phase), (float)std::sin(phase)});
            }
        }
        for (int d : dibits) {
            double f = spacing * kLevel[d];
            for (int s = 0; s < sps; ++s) {
                phase += 2.0 * M_PI * f / sr;
                iq.push_back({(float)std::cos(phase), (float)std::sin(phase)});
            }
        }
        M17Decoder dec;
        dec.feed(iq);
        auto calls = dec.takeCalls();
        bool ok = false;
        for (auto& c : calls)
            if (c.crcOk && c.src == "R2D2") ok = true;
        check(ok, "RF 4FSK round-trip decodes source callsign");
    }

    std::printf("m17_decoder: %s\n", failures ? "FAILURES" : "all green");
    return failures ? 1 : 0;
}
