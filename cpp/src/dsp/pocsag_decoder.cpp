// SPDX-License-Identifier: MIT
#include "dsp/pocsag_decoder.h"

namespace mbdsdr {
namespace dsp {

namespace {

// ---------------------------------------------------------------------------
// BCH(31,21) tables. The division/remainder recurrence and the single/
// double-error syndrome->pattern lookup are the textbook decoder for the
// degree-10 BCH(31,21) code (corrects t=2). Tables are built once, lazily.
// ---------------------------------------------------------------------------
struct BchTables {
    uint16_t parity[kPocsagInfoBits];   // parity for a single set data bit
    uint16_t syn[32];                   // syndrome for a single 31-bit error
    uint32_t err[2048];                 // 11-bit syndrome -> 32-bit pattern
};

const BchTables& tables() {
    static const BchTables t = [] {
        BchTables t{};
        // parity[databit]: remainder when only data bit `databit` is set.
        for (int db = 0; db < kPocsagInfoBits; ++db) {
            uint32_t shreg = 1u << (db + kPocsagParityBits);
            for (int i = kPocsagInfoBits - 1; i >= 0; --i) {
                if (shreg & (1u << (i + kPocsagParityBits)))
                    shreg ^= (kPocsagBchPoly << i);
            }
            t.parity[db] = static_cast<uint16_t>(shreg & 0x3FFu);
        }
        // syn[bit]: syndrome of a single error at 31-domain position `bit`.
        for (int bit = 0; bit < 31; ++bit) {
            uint32_t shreg = 1u << bit;
            for (int i = kPocsagInfoBits - 1; i >= 0; --i) {
                if (shreg & (1u << (i + kPocsagParityBits)))
                    shreg ^= (kPocsagBchPoly << i);
            }
            t.syn[bit] = static_cast<uint16_t>(shreg & 0x3FFu);
        }
        // Single errors (word bits 1..31; bit0 is the overall parity bit, so a
        // single data error flips it too -> set the 0x400 syndrome bit).
        for (int i = 1; i <= 31; ++i) {
            uint32_t syn = static_cast<uint32_t>(t.syn[i - 1]) | 0x400u;
            t.err[syn] = 1u << i;
        }
        // Double errors: syndromes XOR; the two parity flips cancel.
        for (int i = 1; i <= 31; ++i) {
            for (int j = i + 1; j <= 31; ++j) {
                uint32_t syn =
                    static_cast<uint32_t>(t.syn[i - 1]) ^
                    static_cast<uint32_t>(t.syn[j - 1]);
                if (t.err[syn] == 0)
                    t.err[syn] = (1u << i) | (1u << j);
            }
        }
        return t;
    }();
    return t;
}

// Overall even-parity bit (0/1) of a 32-bit word.
int evenParity32(uint32_t x) {
    x ^= x >> 16;
    x ^= x >> 8;
    x ^= x >> 4;
    x ^= x >> 2;
    x ^= x >> 1;
    return static_cast<int>(x & 1u);
}

// POCSAG numeric code table: nibble -> character.
const char* kNumericTable = "084 2.6]195-3U7[";

// Numeric character -> BCD nibble (mirrors the public numeric paging table).
int numericEncode(char ch) {
    switch (ch) {
        case '0': return 0;  case '1': return 8;  case '2': return 4;
        case '3': return 12; case '4': return 2;  case '5': return 10;
        case '6': return 6;  case '7': return 14; case '8': return 1;
        case '9': return 9;  case 'U': return 13; case ' ': return 3;
        case '-': return 11; case '.': return 5;  case '[': return 15;
        case ']': return 7;
        default:  return 3;   // unknown -> space pad
    }
}

// Reverse the bits of a 7-bit value.
int rev7(int b) {
    b &= 0x7F;
    return (((b << 6) & 0x40) | ((b >> 6) & 0x01) |
            ((b << 4) & 0x20) | ((b >> 4) & 0x02) |
            ((b << 2) & 0x10) | ((b >> 2) & 0x04) |
            (b & 0x08));
}

} // namespace

// ===========================================================================
// BCH codec
// ===========================================================================
uint32_t PocsagDecoder::bchEncode21(uint32_t data21) {
    const BchTables& t = tables();
    uint32_t d = data21 & 0x1FFFFFu;
    uint32_t parity = 0;
    for (uint32_t tmp = d; tmp; tmp &= tmp - 1)
        parity ^= t.parity[__builtin_ctz(tmp)];
    uint32_t cw = (d << (kPocsagParityBits + 1)) | (parity << 1);
    cw |= static_cast<uint32_t>(evenParity32(cw));
    return cw;
}

std::pair<uint32_t, int> PocsagDecoder::bchCorrect(uint32_t cw) {
    const BchTables& t = tables();
    uint32_t bits = cw >> 1;                 // drop the overall parity bit
    uint32_t syn = 0;
    for (uint32_t tmp = bits; tmp; tmp &= tmp - 1)
        syn ^= t.syn[__builtin_ctz(tmp)];
    if (evenParity32(cw)) syn |= 0x400u;     // odd overall parity
    if (syn == 0) return {cw, 0};
    uint32_t err = t.err[syn];
    if (err == 0) return {cw, -1};
    return {cw ^ err, __builtin_popcount(err)};
}

// ===========================================================================
// Ctor / streaming plumbing
// ===========================================================================
PocsagDecoder::PocsagDecoder() = default;

void PocsagDecoder::reset() {
    buf_.clear();
    pos_ = 0;
    state_ = State::Hunt;
    polarity_ = 0;
    wordIdx_ = 0;
    havePending_ = false;
    pendingAddr_ = 0;
    pendingFunc_ = 0;
    nibBuf_.clear();
    numNibbles_ = 0;
    out_.clear();
}

void PocsagDecoder::feed(const std::vector<int>& bits) {
    if (bits.empty()) return;
    buf_.insert(buf_.end(), bits.begin(), bits.end());
    pump();
    // Drop already-consumed prefix to bound memory on long streams.
    if (pos_ > 8192) {
        buf_.erase(buf_.begin(), buf_.begin() + static_cast<std::ptrdiff_t>(pos_));
        pos_ = 0;
    }
}

std::vector<PocsagMessage> PocsagDecoder::takeMessages() {
    std::vector<PocsagMessage> out;
    out.swap(out_);
    return out;
}

uint32_t PocsagDecoder::readWord(std::size_t at) const {
    uint32_t w = 0;
    for (int k = 0; k < kPocsagBitsPerWord; ++k)
        w = (w << 1) | static_cast<uint32_t>(buf_[at + k] & 1);
    return w;
}

// ===========================================================================
// Word classification + message assembly
// ===========================================================================
void PocsagDecoder::flushPending() {
    if (havePending_ && numNibbles_ > 0) {
        PocsagMessage m;
        m.address  = pendingAddr_;
        m.function = pendingFunc_;
        if (pendingFunc_ == 0) {
            m.type = PocsagMessage::Type::Numeric;
            m.text = decodeNumeric(nibBuf_, numNibbles_);
        } else {
            m.type = PocsagMessage::Type::Alpha;
            m.text = decodeAlpha(nibBuf_, numNibbles_);
        }
        out_.push_back(std::move(m));
    }
    havePending_ = false;
    nibBuf_.clear();
    numNibbles_ = 0;
}

void PocsagDecoder::handleWord(uint32_t cw, int nerr, int wordIdx) {
    if (nerr < 0) {                 // uncorrectable -> abort current message
        flushPending();
        return;
    }
    if (cw == kPocsagIdleWord) {    // idle -> end current message
        if (havePending_) flushPending();
        return;
    }

    if (cw & kPocsagMessageFlag) {
        // 20-bit payload appended to the continuous nibble stream.
        uint32_t data = (cw >> 11) & 0xFFFFFu;
        if (!havePending_) {        // stray message word: default to alpha
            havePending_ = true;
            pendingAddr_ = 0;
            pendingFunc_ = 1;
        }
        if (numNibbles_ & 1) {      // pending half-byte -> complete it first
            nibBuf_.back() =
                static_cast<uint8_t>((nibBuf_.back() & 0xF0u) |
                                     ((data >> 16) & 0xFu));
            nibBuf_.push_back(static_cast<uint8_t>((data >> 8) & 0xFFu));
            nibBuf_.push_back(static_cast<uint8_t>(data & 0xFFu));
        } else {                    // byte-aligned
            nibBuf_.push_back(static_cast<uint8_t>((data >> 12) & 0xFFu));
            nibBuf_.push_back(static_cast<uint8_t>((data >> 4) & 0xFFu));
            nibBuf_.push_back(static_cast<uint8_t>((data << 4) & 0xFFu));
        }
        numNibbles_ += 5;
    } else {
        // Address word: close any open message, then open a new one.
        if (havePending_) flushPending();
        uint32_t addr = ((cw >> 10) & 0x1FFFF8u) |
                        static_cast<uint32_t>((wordIdx >> 1) & 7);
        int func = static_cast<int>((cw >> 11) & 3u);
        havePending_ = true;
        pendingAddr_ = addr;
        pendingFunc_ = func;
    }
}

// ===========================================================================
// Stream sync state machine
// ===========================================================================
void PocsagDecoder::pump() {
    for (;;) {
        if (state_ == State::Hunt) {
            if (buf_.size() - pos_ < kPocsagBitsPerWord) return;
            uint32_t w = readWord(pos_);
            if (bchCorrect(w).first == kPocsagSyncWord) {
                polarity_ = 0;
                state_ = State::Sync;
                wordIdx_ = 0;
                pos_ += kPocsagBitsPerWord;
                continue;
            }
            if (bchCorrect(~w).first == kPocsagSyncWord) {
                polarity_ = 1;
                state_ = State::Sync;
                wordIdx_ = 0;
                pos_ += kPocsagBitsPerWord;
                continue;
            }
            pos_ += 1;              // no sync here; slide one bit
        } else {
            if (buf_.size() - pos_ < kPocsagBitsPerWord) return;
            uint32_t w = readWord(pos_);
            pos_ += kPocsagBitsPerWord;
            if (polarity_) w = ~w;
            auto [cw, nerr] = bchCorrect(w);
            handleWord(cw, nerr, wordIdx_);
            ++wordIdx_;
            if (wordIdx_ == kPocsagWordsPerBatch) {
                wordIdx_ = 0;
                state_ = State::Hunt;   // next word re-tested as batch sync
            }
        }
    }
}

// ===========================================================================
// Payload decoding
// ===========================================================================
std::string PocsagDecoder::decodeNumeric(const std::vector<uint8_t>& buf,
                                         int nibbles) {
    std::string out;
    for (int i = 0; i < nibbles; ++i) {
        int bi = i / 2;
        if (bi >= static_cast<int>(buf.size())) break;
        int nib = (i & 1) == 0 ? ((buf[bi] >> 4) & 0xF) : (buf[bi] & 0xF);
        out += kNumericTable[nib];
    }
    while (!out.empty() && out.back() == ' ') out.pop_back();
    return out;
}

std::string PocsagDecoder::decodeAlpha(const std::vector<uint8_t>& buf,
                                      int nibbles) {
    int nChars = nibbles * 4 / 7;
    std::string out;
    for (int i = 0; i < nChars; ++i) {
        int b0 = buf[(i * 7) / 8];
        int b1 = (((i * 7 + 6) / 8) < static_cast<int>(buf.size()))
                     ? buf[(i * 7 + 6) / 8]
                     : 0;
        int c = rev7(((b0 << 8 | b1) >> ((i + 1) % 8)) & 0x7F);
        if (c >= 32 && c < 127) out += static_cast<char>(c);
    }
    while (!out.empty() && out.back() == ' ') out.pop_back();
    return out;
}

// ===========================================================================
// Test synthesis (clean-room frame builder)
// ===========================================================================
int PocsagDecoder::detectFunction(const std::string& message) {
    if (message.empty()) return 1;
    for (char ch : message) {
        bool numeric = (ch >= '0' && ch <= '9') || ch == 'U' || ch == ' ' ||
                       ch == '.' || ch == '-' || ch == '[' || ch == ']';
        if (!numeric) return 1;
    }
    return 0;
}

uint32_t PocsagDecoder::buildAddressWord(uint32_t address, int function) {
    uint32_t data = ((address >> 3) << 2) | static_cast<uint32_t>(function & 3);
    return bchEncode21(data);
}

std::vector<uint32_t> PocsagDecoder::encodeMessageWords(const std::string& msg,
                                                        int function) {
    std::vector<uint32_t> cws;
    if (function == 0) {
        // Numeric: 5 BCD nibbles per word.
        std::size_t i = 0;
        const std::size_t n = msg.size();
        while (i < n) {
            uint32_t data = 0;
            for (int k = 0; k < 5; ++k) {
                uint32_t nib = (i < n) ? static_cast<uint32_t>(numericEncode(msg[i++]))
                                       : 3u;  // space pad
                data = (data << 4) | nib;
            }
            cws.push_back(bchEncode21(0x100000u | (data & 0xFFFFFu)));
        }
    } else {
        // Alpha: 7-bit reversed ASCII, packed MSB-first, nibble-aligned.
        std::vector<int> bits;
        for (char ch : msg) {
            int c = rev7(static_cast<int>(ch) & 0x7F);
            for (int b = 6; b >= 0; --b) bits.push_back((c >> b) & 1);
        }
        while (bits.size() % 4 != 0) bits.push_back(0);
        for (std::size_t off = 0; off < bits.size(); off += 20) {
            uint32_t data = 0;
            int cnt = 0;
            for (std::size_t j = off; j < off + 20 && j < bits.size(); ++j) {
                data = (data << 1) | static_cast<uint32_t>(bits[j]);
                ++cnt;
            }
            while (cnt < 20) { data <<= 1; ++cnt; }
            cws.push_back(bchEncode21(0x100000u | (data & 0xFFFFFu)));
        }
    }
    return cws;
}

std::vector<int> PocsagDecoder::buildFrameBits(uint32_t address,
                                               const std::string& message) {
    int func = detectFunction(message);
    std::vector<uint32_t> msgCws = encodeMessageWords(message, func);

    std::vector<int> bits;
    bits.reserve(kPocsagPreambleBits + kPocsagBitsPerWord +
                 kPocsagWordsPerBatch * kPocsagBitsPerWord);

    // 1010... preamble.
    for (int i = 0; i < kPocsagPreambleBits; ++i)
        bits.push_back((i & 1) == 0 ? 1 : 0);

    // Batch sync word, MSB first.
    for (int i = 31; i >= 0; --i)
        bits.push_back(static_cast<int>((kPocsagSyncWord >> i) & 1u));

    // 16 slots: address word in its frame, then message words, then idle.
    int framePos = static_cast<int>(address & 7);
    bool addressSent = false;
    std::size_t msgIdx = 0;
    for (int frame = 0; frame < 8; ++frame) {
        for (int c = 0; c < 2; ++c) {
            uint32_t cw;
            if (!addressSent && frame == framePos && c == 0) {
                cw = buildAddressWord(address, func);
                addressSent = true;
            } else if (addressSent && msgIdx < msgCws.size()) {
                cw = msgCws[msgIdx++];
            } else {
                cw = kPocsagIdleWord;
            }
            for (int i = 31; i >= 0; --i)
                bits.push_back(static_cast<int>((cw >> i) & 1u));
        }
    }
    return bits;
}

} // namespace dsp
} // namespace mbdsdr
