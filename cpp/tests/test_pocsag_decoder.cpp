// SPDX-License-Identifier: MIT
//
// synthetic 仅验证链路 (synthetic fixture: only verifies the chain).
//
// These tests synthesize POCSAG bit streams locally (clean-room frame builder
// in pocsag_decoder.cpp) and feed them through the real decoder to prove:
//   * BCH(31,21) encode/decode round-trips and corrects <=2 bit errors;
//   * a known numeric message decodes back to its address + BCD text;
//   * a known alpha message decodes back to its address + ASCII text;
//   * a couple of in-stream bit errors are corrected, not dropped;
//   * idle-only batches and pure noise yield an honest empty result
//     (no fabricated address / text, no pre-stored pager or message).
//
// No live radio, no recorded air signal; everything is derived in code.
#include <cstdio>
#include <cstdint>
#include <string>
#include <vector>

#include "dsp/pocsag_decoder.h"

using namespace mbdsdr::dsp;

static int failures = 0;
static void check(bool c, const char* m) {
    if (!c) { ++failures; std::printf("FAIL: %s\n", m); }
}

// Deterministic LCG -> pseudo-random bits (fixed seed, reproducible).
static std::vector<int> noiseBits(std::size_t n, unsigned seed) {
    std::vector<int> v;
    v.reserve(n);
    unsigned s = seed;
    for (std::size_t i = 0; i < n; ++i) {
        s = s * 1664525u + 1013904223u;
        v.push_back((s >> 16) & 1);
    }
    return v;
}

int main() {
    // ---- 1. BCH(31,21) codec basics (known reference values) -------------
    check(PocsagDecoder::bchEncode21(0u) == 0x00000000u,
          "bchEncode21(0) == 0");
    check(PocsagDecoder::bchEncode21(1u << 20) == 0x80000769u,
          "bchEncode21(1<<20) == 0x80000769");

    uint32_t aw = PocsagDecoder::buildAddressWord(12345, 0);
    check(aw == 0x00C0E796u, "address word(12345,0) == 0x00C0E796");
    {
        auto [cw, nerr] = PocsagDecoder::bchCorrect(aw);
        check(nerr == 0 && cw == aw, "clean address word: 0 errors");
    }
    {
        uint32_t bad = aw ^ (1u << 5) ^ (1u << 17);   // two bit errors
        auto [cw, nerr] = PocsagDecoder::bchCorrect(bad);
        check(nerr == 2 && cw == aw, "BCH corrects 2 bit errors in address word");
    }

    // ---- 2. Numeric end-to-end (synthetic) -------------------------------
    {
        std::vector<int> bits = PocsagDecoder::buildFrameBits(12345, "08671234");
        PocsagDecoder dec;
        dec.feed(bits);
        std::vector<PocsagMessage> msgs = dec.takeMessages();
        check(msgs.size() == 1, "numeric: exactly one message produced");
        if (!msgs.empty()) {
            const PocsagMessage& m = msgs.front();
            check(m.address == 12345, "numeric: address == 12345");
            check(m.function == 0, "numeric: function == 0 (numeric mode)");
            check(m.type == PocsagMessage::Type::Numeric,
                  "numeric: type tagged Numeric");
            check(m.text == "08671234", "numeric: text round-trips");
            std::printf("  numeric decoded: addr=%u func=%d text=\"%s\"\n",
                        m.address, m.function, m.text.c_str());
        }
    }

    // ---- 3. Alpha end-to-end (synthetic) ---------------------------------
    {
        std::vector<int> bits = PocsagDecoder::buildFrameBits(9876, "Hello");
        PocsagDecoder dec;
        dec.feed(bits);
        std::vector<PocsagMessage> msgs = dec.takeMessages();
        check(msgs.size() == 1, "alpha: exactly one message produced");
        if (!msgs.empty()) {
            const PocsagMessage& m = msgs.front();
            check(m.address == 9876, "alpha: address == 9876");
            check(m.function == 1, "alpha: function == 1 (alpha mode)");
            check(m.type == PocsagMessage::Type::Alpha,
                  "alpha: type tagged Alpha");
            check(m.text == "Hello", "alpha: text round-trips");
            std::printf("  alpha decoded: addr=%u func=%d text=\"%s\"\n",
                        m.address, m.function, m.text.c_str());
        }
    }

    // ---- 4. In-stream bit errors still decoded (BCH correction) ----------
    {
        std::vector<int> bits = PocsagDecoder::buildFrameBits(12345, "08671234");
        // Corrupt two bits inside the first message codeword.
        // Layout: 576 preamble + 32 SC; address slot = (12345&7)*2.
        const int preamble = 576;
        int addrSlot = (12345 & 7) * 2;
        std::size_t wordBit0 = preamble + 32 + (addrSlot + 1) * 32;
        bits[wordBit0 + 3] ^= 1;    // flip bit in message word
        bits[wordBit0 + 20] ^= 1;   // second flip (within t=2 correction)

        PocsagDecoder dec;
        dec.feed(bits);
        std::vector<PocsagMessage> msgs = dec.takeMessages();
        check(msgs.size() == 1, "error-corr: one message despite 2 corrupted bits");
        if (!msgs.empty()) {
            check(msgs.front().address == 12345, "error-corr: address intact");
            check(msgs.front().text == "08671234",
                  "error-corr: text recovered by BCH");
            std::printf("  error-corr decoded text=\"%s\"\n",
                        msgs.front().text.c_str());
        }
    }

    // ---- 5. Idle-only batch: honest no-message ----------------------------
    {
        // Build a batch that is SC + 16 idle words (no address, no message).
        std::vector<int> bits;
        for (int i = 0; i < 576; ++i) bits.push_back((i & 1));   // preamble
        for (int i = 31; i >= 0; --i)
            bits.push_back((int)((kPocsagSyncWord >> i) & 1u));
        for (int w = 0; w < 16; ++w)
            for (int i = 31; i >= 0; --i)
                bits.push_back((int)((kPocsagIdleWord >> i) & 1u));

        PocsagDecoder dec;
        dec.feed(bits);
        std::vector<PocsagMessage> msgs = dec.takeMessages();
        check(msgs.empty(), "idle batch: no message fabricated");
    }

    // ---- 5b. Streaming: feed split into chunks (as FskDemod delivers) ----
    {
        std::vector<int> bits = PocsagDecoder::buildFrameBits(555, "1234567890");
        PocsagDecoder dec;
        std::size_t third = bits.size() / 3;
        dec.feed(std::vector<int>(bits.begin(), bits.begin() + third));
        dec.feed(std::vector<int>(bits.begin() + third,
                                  bits.begin() + 2 * third));
        dec.feed(std::vector<int>(bits.begin() + 2 * third, bits.end()));
        std::vector<PocsagMessage> msgs = dec.takeMessages();
        check(msgs.size() == 1 && msgs.front().address == 555 &&
                  msgs.front().text == "1234567890",
              "streaming chunked feed: long numeric reassembles");
        std::printf("  chunked decoded: addr=%u text=\"%s\"\n",
                    msgs.empty() ? 0u : msgs.front().address,
                    msgs.empty() ? "" : msgs.front().text.c_str());
    }

    // ---- 6. Pure noise: honest empty (no false pager) -------------------
    {
        PocsagDecoder dec;
        dec.feed(noiseBits(1500, 0xC0FFEE));
        std::vector<PocsagMessage> msgs = dec.takeMessages();
        check(msgs.empty(), "pure noise: empty result (no fabricated message)");
        std::printf("  noise sweep: produced %zu messages (expect 0)\n",
                    msgs.size());
    }

    std::printf("pocsag_decoder: %s\n", failures ? "FAILURES" : "all green");
    return failures ? 1 : 0;
}
