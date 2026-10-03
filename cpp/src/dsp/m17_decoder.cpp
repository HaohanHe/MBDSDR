// SPDX-License-Identifier: MIT
#include "m17_decoder.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <unordered_map>

namespace mbdsdr {
namespace dsp {

namespace {

// 46-byte M17 DC (decorrelation) sequence -- public spec value.
constexpr std::array<uint8_t, 46> kM17Dc = {
    0xD6,0xB5,0xE2,0x30,0x82,0xFF,0x84,0x62,
    0xBA,0x4E,0x96,0x90,0xD8,0x98,0xDD,0x5D,
    0x0C,0xC8,0x52,0x43,0x91,0x1D,0xF8,0x6E,
    0x68,0x2F,0x35,0xDA,0x14,0xEA,0xCD,0x76,
    0x19,0x8D,0xD5,0x80,0xD1,0x33,0x87,0x13,
    0x57,0x18,0x2D,0x29,0x78,0xC3};

// P1 puncture matrix (61 entries; 0 = punctured).  Zeros at indices
// 2,6,10,...,58 (every 4 starting at 2).  46 kept -> 46*8 = 368 payload bits.
constexpr std::array<int8_t, 61> kP1 = [] {
    std::array<int8_t, 61> p{};
    for (int i = 0; i < 61; ++i) p[i] = 1;
    for (int i = 2; i < 61; i += 4) p[i] = 0;
    return p;
}();

// Convolutional code generators (octal 0o31 / 0o27), K=5 -> 32 states.
constexpr int kConvG1 = 031;  // 0o31
constexpr int kConvG2 = 027;  // 0o27
constexpr int kNStates = 32;

inline int convOut(int poly, int memory) {
    int v = poly & memory;
    int p = 0;
    while (v) { p ^= (v & 1); v >>= 1; }
    return p;
}

// ---------------------------------------------------------------------------
// Viterbi hard-decision decode (K=5, rate 1/2).  codedBits holds g1,g2 pairs
// for (infoBits + 4 tail) trellis steps; returns infoBits info bits.
// ---------------------------------------------------------------------------
std::vector<int> viterbiDecode(const std::vector<int>& codedBits, int infoBits) {
    const int nTrellis = infoBits + 4;
    std::array<float, kNStates> pm{};
    pm.fill(1e30f); pm[0] = 0.0f;

    // history[step][state] = (prevState, inputBit)
    std::vector<std::array<int, kNStates>> prevState(nTrellis);
    std::vector<std::array<int, kNStates>> prevBit(nTrellis);

    for (int step = 0; step < nTrellis; ++step) {
        std::array<float, kNStates> next{};
        next.fill(1e30f);
        for (int ns = 0; ns < kNStates; ++ns) {
            // codedBits entries are 0/1 hard decisions; -1 marks a puncture
            // erasure and contributes NO branch cost (neutral LLR=0).
            int r1 = (2*step + 1 < (int)codedBits.size()) ? codedBits[2*step]   : -1;
            int r2 = (2*step + 1 < (int)codedBits.size()) ? codedBits[2*step+1] : -1;
            for (int ps : {ns >> 1, (ns >> 1) | 16}) {
                int g1 = convOut(kConvG1, ns);
                int g2 = convOut(kConvG2, ns);
                float cost = 0.0f;
                if (r1 != -1) cost += (float)(g1 ^ r1);
                if (r2 != -1) cost += (float)(g2 ^ r2);
                float cand = pm[ps] + cost;
                if (cand < next[ns]) {
                    next[ns] = cand;
                    prevState[step][ns] = ps;
                    prevBit[step][ns] = ns & 1;
                }
            }
        }
        pm = next;
    }

    int best = 0; float bestV = pm[0];
    for (int s = 1; s < kNStates; ++s) if (pm[s] < bestV) { bestV = pm[s]; best = s; }

    std::vector<int> rev;
    int st = best;
    for (int step = nTrellis - 1; step >= 0; --step) {
        rev.push_back(prevBit[step][st]);
        st = prevState[step][st];
    }
    std::reverse(rev.begin(), rev.end());
    rev.resize(infoBits);
    return rev;
}

// Deinterleave: out[i] = in[p(i)], p(i)=(45i+92i^2) mod 368.
void deinterleave368(const int in[368], int out[368]) {
    for (int i = 0; i < 368; ++i) {
        long idx = (45L*i + 92L*(long)i*i) % 368L;
        out[i] = in[idx];
    }
}

// Derandomize: XOR the 368 payload bits with the DC sequence bits (MSB first).
void derandomize368(int bits[368]) {
    for (int i = 0; i < 368; ++i) {
        int byte = i / 8, bit = 7 - (i % 8);
        int dcBit = (kM17Dc[byte] >> bit) & 1;
        bits[i] ^= dcBit;
    }
}

// Depuncture P1: 368 kept bits -> 488 bits.  Puncture positions are inserted
// as -1 (neutral erasure) so the Viterbi branch metric ignores them.
std::vector<int> depunctureP1(const int in[368]) {
    std::vector<int> out(488, 0);
    int idx = 0, p = 0;
    for (int i = 0; i < 488; ++i) {
        if (kP1[p] == 0) {
            out[i] = -1;            // erasure (neutral)
        } else {
            out[i] = in[idx++];
        }
        if (++p == 61) p = 0;
    }
    return out;
}

// Golay(24,12) syndrome (11 bits).  Public-spec generator 0xC75.
uint32_t golaySyndrome(uint32_t cw) {
    cw &= 0xFFFFFFu;
    for (int i = 0; i < 12; ++i) {
        if (cw & 1u) cw ^= 0xC75u;
        cw >>= 1;
    }
    return cw << 12;   // 11-bit syndrome in [22:12]
}

// Build the syndrome -> error-vector lookup once (errors up to weight 3).
const std::unordered_map<uint32_t, uint32_t>& golayTable() {
    static std::unordered_map<uint32_t, uint32_t> table;
    static bool built = false;
    if (!built) {
        table.clear();
        // weight 0..3 error patterns
        for (int e0 = 0; e0 < 24; ++e0) {
            uint32_t err1 = 1u << e0;
            for (int e1 = -1; e1 < 24; ++e1) {
                uint32_t err2 = (e1 < 0) ? 0 : err1 | (1u << e1);
                for (int e2 = -1; e2 < 24; ++e2) {
                    if (e2 >= 0 && (e2 == e0 || e2 == e1)) continue;
                    uint32_t err = (e2 < 0) ? err2 : err2 | (1u << e2);
                    uint32_t syn = golaySyndrome(err);
                    if (table.find(syn) == table.end()) table[syn] = err;
                }
            }
        }
        built = true;
    }
    return table;
}

} // namespace

// ===========================================================================
M17Decoder::M17Decoder() : M17Decoder(kDefaultSampleRateHz) {}

M17Decoder::M17Decoder(double sampleRateHz)
    : sampleRateHz_(sampleRateHz) {
    reset();
}

void M17Decoder::reset() {
    sps_ = sampleRateHz_ / kSymbolRateBd;
    // Normalize discriminator so a 625 Hz tone = +/-1 unit.
    discGain_ = static_cast<float>(sampleRateHz_ / (2.0 * M_PI * 625.0));
    prev_ = {1.0f, 0.0f};
    // Moving-average lowpass over ~1 symbol.
    lpfLen_ = std::max(1, (int)std::lrint(sps_));
    lpfTap_.assign(lpfLen_, 1.0f / lpfLen_);
    lpfDelay_.assign(lpfLen_, 0.0f);
    lpfPos_ = 0;
    // Strobe at the filtered-sample index that lands on the raw symbol center:
    // the causal boxcar LPF has ~L/2 group delay, so nominal first strobe is at
    // filtered sample 9 (= sps-1) for sps=10, i.e. accumulator starts at 0.
    nAcc_ = 0.0f;
    omega_ = static_cast<float>(sps_);
    prevFilt_ = 0.0f;
    dibits_.clear();
    out_.clear();
}

// ---------------------------------------------------------------------------
std::string M17Decoder::decodeCallsign(const uint8_t b[6]) {
    bool allFf = true;
    for (int i = 0; i < 6; ++i) if (b[i] != 0xFF) allFf = false;
    if (allFf) return "BROADCAST";
    static const char kMap[] = "xABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/.";
    uint64_t v = 0;
    for (int i = 0; i < 6; ++i) v = (v << 8) | b[i];   // big-endian
    std::string s;
    while (v) { s.push_back(kMap[v % 40]); v /= 40; }
    std::reverse(s.begin(), s.end());
    return s;
}

bool M17Decoder::encodeCallsign(const std::string& cs, uint8_t out[6]) {
    bool allFf = cs.empty();
    uint64_t v = 0;
    if (!allFf) {
        for (char ch : cs) {
            int d;
            if (ch >= 'A' && ch <= 'Z')      d = ch - 'A' + 1;
            else if (ch >= '0' && ch <= '9') d = ch - '0' + 27;
            else if (ch == '-')              d = 37;
            else if (ch == '/')              d = 38;
            else if (ch == '.')              d = 39;
            else return false;
            v = v * 40 + d;
        }
    } else {
        v = 0;
    }
    for (int i = 5; i >= 0; --i) {
        out[i] = allFf ? 0xFF : (uint8_t)(v & 0xFF);
        v >>= 8;
    }
    return true;
}

// ---------------------------------------------------------------------------
uint16_t M17Decoder::crc16(const uint8_t* data, int len) {
    // 16-step LSB-first warm-up (matches the public M17 CRC reference; this
    // lands the register at 0x36e6 for poly 0x5935 before any data bit is fed).
    uint16_t reg = 0xFFFFu;
    for (int i = 0; i < 16; ++i) {
        int bit = reg & 1;
        if (bit) reg ^= 0x5935u;
        reg >>= 1;
        if (bit) reg |= 0x8000u;
    }
    auto crcBit = [&](int bit) {
        int msb = (reg >> 15) & 1;
        reg = (uint16_t)((reg << 1) | (bit & 1));
        if (msb) reg ^= 0x5935u;
    };
    for (int i = 0; i < len; ++i)
        for (int b = 7; b >= 0; --b) crcBit((data[i] >> b) & 1);
    // 16-bit final shift-out.
    for (int i = 0; i < 16; ++i) crcBit(0);
    return reg;
}

int M17Decoder::golay24Correct(uint32_t& codeword) {
    codeword &= 0xFFFFFFu;
    uint32_t syn = golaySyndrome(codeword);
    if (syn == 0) return 0;
    const auto& table = golayTable();
    auto it = table.find(syn);
    if (it == table.end()) return -1;
    uint32_t err = it->second;
    codeword ^= err;
    int n = 0; uint32_t e = err;
    while (e) { n += e & 1; e >>= 1; }
    return n;
}

// ---------------------------------------------------------------------------
void M17Decoder::feed(const std::vector<std::complex<float>>& iq) {
    if (iq.empty()) return;
    // 1) Quadrature discriminator.
    std::vector<float> disc(iq.size());
    for (std::size_t i = 0; i < iq.size(); ++i) {
        std::complex<float> y = iq[i] * std::conj(prev_);
        disc[i] = discGain_ * std::atan2(y.imag(), y.real());
        prev_ = iq[i];
    }
    // 2) Moving-average lowpass.
    std::vector<float> filt(iq.size());
    double acc = 0.0;
    for (std::size_t i = 0; i < iq.size(); ++i) {
        acc += disc[i];
        acc -= lpfDelay_[lpfPos_];
        lpfDelay_[lpfPos_] = disc[i];
        if (++lpfPos_ >= lpfLen_) lpfPos_ = 0;
        filt[i] = (float)(acc / lpfLen_);
    }
    // 3) Symbol strobe + 4-level hard slicing.
    for (float cur : filt) {
        nAcc_ += 1.0f;
        while (nAcc_ >= omega_) {
            float over = nAcc_ - omega_;
            float frac = 1.0f - over;
            if (frac < 0.0f) frac = 0.0f;
            if (frac > 1.0f) frac = 1.0f;
            float v = prevFilt_ * (1.0f - frac) + cur * frac;
            int level;  // +3/+1/-1/-3
            if (v >= 2.0f)      level = 3;
            else if (v >= 0.0f) level = 1;
            else if (v > -2.0f) level = -1;
            else                level = -3;
            // level -> dibit : +1->0, +3->1, -1->2, -3->3
            int dibit = (level == 1) ? 0 : (level == 3) ? 1 : (level == -1) ? 2 : 3;
            pushDibit(dibit);
            nAcc_ -= omega_;
        }
        prevFilt_ = cur;
    }
    processBits();
}

void M17Decoder::feedDibits(const std::vector<int>& dibits) {
    for (int d : dibits) pushDibit(d & 3);
    processBits();
}

void M17Decoder::pushDibit(int d) {
    dibits_.push_back(d);
}

// Return sync kind (kSync* values) or 0 if no match within 1 bit error.
int M17Decoder::matchSync(const int* b16) const {
    auto dist = [&](uint16_t word) {
        int d = 0;
        for (int i = 0; i < 16; ++i) {
            int bit = (word >> (15 - i)) & 1;
            if (b16[i] != bit) ++d;
        }
        return d;
    };
    int best = 0, bestD = 1;   // tolerate 1 bit error
    int d;
    d = dist(kSyncLsf);  if (d < bestD) { bestD = d; best = kSyncLsf; }
    d = dist(kSyncData); if (d < bestD) { bestD = d; best = kSyncData; }
    d = dist(kSyncStream);if (d < bestD){ bestD = d; best = kSyncStream; }
    return best;
}

void M17Decoder::processBits() {
    // Walk the dibit stream as a bit stream; correlate sync.
    std::size_t n = dibits_.size();
    std::size_t pos = 0;
    while (pos + (16 + 368) / 2 <= n) {
        // Build next 16 sync bits from dibits.
        int b16[16];
        for (int i = 0; i < 8; ++i) {
            int dib = dibits_[pos + i];
            b16[2*i]     = (dib >> 1) & 1;   // high bit first
            b16[2*i + 1] = dib & 1;
        }
        int sync = matchSync(b16);
        if (sync != 0) {
            // Extract 368 payload bits from the following 184 dibits.
            int pl[368];
            for (int i = 0; i < 184; ++i) {
                int dib = dibits_[pos + 8 + i];
                pl[2*i]     = (dib >> 1) & 1;
                pl[2*i + 1] = dib & 1;
            }
            if (sync == kSyncLsf) handleLsfFrame(pl);
            else                  handleDataFrame(sync, pl);
            pos += 8 + 184;   // consume sync (8 dibits) + payload (184 dibits)
        } else {
            pos += 1;
        }
    }
    // Drop consumed prefix, keep a small tail for boundary frames.
    if (pos > 0) {
        dibits_.erase(dibits_.begin(), dibits_.begin() + pos);
    }
}

void M17Decoder::handleLsfFrame(const int* payloadBits) {
    int bits[368];
    std::copy(payloadBits, payloadBits + 368, bits);
    derandomize368(bits);
    int deint[368];
    deinterleave368(bits, deint);
    std::vector<int> coded = depunctureP1(deint);
    std::vector<int> info = viterbiDecode(coded, 240);

    // 240 bits -> 30 bytes, MSB first.
    uint8_t lsf[30] = {0};
    for (int i = 0; i < 240; ++i)
        lsf[i / 8] |= (uint8_t)(info[i] << (7 - (i % 8)));

    M17Call call;
    call.frameKind = 1;
    call.src = decodeCallsign(lsf + 0);
    call.dst = decodeCallsign(lsf + 6);
    call.type = (uint16_t)((lsf[12] << 8) | lsf[13]);
    call.isStream = (call.type & 1) != 0;
    call.payloadClass = (call.type & 6) >> 1;
    call.meta.assign(lsf + 14, lsf + 28);
    uint16_t crc = (uint16_t)((lsf[28] << 8) | lsf[29]);
    call.crcOk = crc16(lsf, 28) == crc;
    call.voiceUndecoded = call.isStream && call.payloadClass == 2;
    if (call.crcOk) out_.push_back(std::move(call));
}

void M17Decoder::handleDataFrame(uint16_t sync, const int* payloadBits) {
    int bits[368];
    std::copy(payloadBits, payloadBits + 368, bits);
    derandomize368(bits);
    int deint[368];
    deinterleave368(bits, deint);
    // Data/stream frames are NOT Viterbi-decoded here: they carry Codec2
    // voice frames (not bundled -- see header note).  Report raw payload bytes
    // so a future Codec2 stage can consume them.
    M17Call call;
    call.frameKind = (sync == kSyncData) ? 2 : 3;
    call.payload.assign(46, 0);
    for (int i = 0; i < 368; ++i)
        call.payload[i / 8] |= (uint8_t)(deint[i] << (7 - (i % 8)));
    // We cannot tell voice vs data here without the preceding LSF, so we do NOT
    // claim a voice flag; the LSF call carries the authoritative payloadClass.
    call.voiceUndecoded = false;
    out_.push_back(std::move(call));
}

std::vector<M17Call> M17Decoder::takeCalls() {
    std::vector<M17Call> out;
    out.swap(out_);
    return out;
}

} // namespace dsp
} // namespace mbdsdr
