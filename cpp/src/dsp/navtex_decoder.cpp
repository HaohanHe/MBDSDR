// SPDX-License-Identifier: MIT
#include "navtex_decoder.h"

#include "fsk_demod.h"

#include <utility>

namespace mbdsdr {
namespace dsp {

// -- ITA-2 / CCITT-2 tables (clean-room, public alphabet) --------------------
// 5-bit code (0..31) -> ASCII.  Shift codes (27 figures / 31 letters) and the
// null (0) map to 0 (control); the state machine treats them specially.
char ita2Letters(int code) {
    static const char tab[32] = {
        /* 0*/ 0,       // null
        /* 1*/ 'E',
        /* 2*/ '\n',    // line feed
        /* 3*/ 'A',
        /* 4*/ ' ',
        /* 5*/ 'S',
        /* 6*/ 'I',
        /* 7*/ 'U',
        /* 8*/ '\r',    // carriage return
        /* 9*/ 'D',
        /*10*/ 'R',
        /*11*/ 'J',
        /*12*/ 'N',
        /*13*/ 'F',
        /*14*/ 'C',
        /*15*/ 'K',
        /*16*/ 'T',
        /*17*/ 'Z',
        /*18*/ 'L',
        /*19*/ 'W',
        /*20*/ 'H',
        /*21*/ 'Y',
        /*22*/ 'P',
        /*23*/ 'Q',
        /*24*/ 'O',
        /*25*/ 'B',
        /*26*/ 'G',
        /*27*/ 0,       // figures shift
        /*28*/ 'M',
        /*29*/ 'X',
        /*30*/ 'V',
        /*31*/ 0,       // letters shift
    };
    if (code < 0 || code > 31) return 0;
    return tab[code];
}

char ita2Figures(int code) {
    static const char tab[32] = {
        /* 0*/ 0,       // null
        /* 1*/ '3',
        /* 2*/ '\n',
        /* 3*/ '-',
        /* 4*/ ' ',
        /* 5*/ '\a',    // bell
        /* 6*/ '8',
        /* 7*/ '7',
        /* 8*/ '\r',
        /* 9*/ '$',
        /*10*/ '4',
        /*11*/ '\'',
        /*12*/ ',',
        /*13*/ '!',
        /*14*/ ':',
        /*15*/ '(',
        /*16*/ '5',
        /*17*/ '"',
        /*18*/ ')',
        /*19*/ '2',
        /*20*/ '#',
        /*21*/ '6',
        /*22*/ '0',
        /*23*/ '1',
        /*24*/ '9',
        /*25*/ '?',
        /*26*/ '@',
        /*27*/ 0,       // figures shift
        /*28*/ '.',
        /*29*/ '/',
        /*30*/ ';',
        /*31*/ 0,       // letters shift
    };
    if (code < 0 || code > 31) return 0;
    return tab[code];
}

namespace {
// Decode one 5-bit code using the current shift state; returns 0 for shifts.
int decodeShifted(int code, bool figures) {
    return figures ? static_cast<int>(ita2Figures(code))
                   : static_cast<int>(ita2Letters(code));
}
} // namespace

// -- Lifecycle ---------------------------------------------------------------
NavtexDecoder::NavtexDecoder() = default;

NavtexDecoder::~NavtexDecoder() {
    delete static_cast<FskDemod*>(demod_);
}

void NavtexDecoder::setSampleRate(double hz) {
    delete static_cast<FskDemod*>(demod_);
    FskDemodConfig cfg;
    cfg.sampleRateHz = hz;
    cfg.symbolRateBd = kNavtexSymbolRateBd;
    cfg.deviationHz  = kNavtexDeviationHz;
    demod_ = static_cast<void*>(new FskDemod(cfg));
    sampleRateHz_ = hz;
    reset();
}

void NavtexDecoder::resetFrameState() {
    fstate_ = FState::kIdle;
    phaseCount_ = 0;
    phaseExpect_ = kNavtexPhasingA;
    preambleGot_ = 0;
    headerGot_ = 0;
    headerBuf_.clear();
    nRun_ = 0;
    shiftFigures_ = false;
    cur_ = NavtexMessage{};
    phasingOk_ = false;
}

void NavtexDecoder::reset() {
    if (demod_) static_cast<FskDemod*>(demod_)->reset();
    bits_.clear();
    alignBits_ = -1;
    charConsumed_ = 0;
    wireIdx_ = 0;
    msgChars_.clear();
    diversityErrors_ = 0;
    out_.clear();
    resetFrameState();
}

// Read 5-bit code for char index charIdx under a given bit alignment offset.
// char j occupies bits [alignOffset + 5*j .. alignOffset + 5*j + 4].
// Returns -1 if not enough bits yet.
int NavtexDecoder::charAt(int alignOffset, long charIdx) const {
    const long start = alignOffset + 5 * charIdx;
    if (start < 0 || start + 5 > static_cast<long>(bits_.size())) return -1;
    int code = 0;
    for (int b = 0; b < 5; ++b) code |= bits_[start + b] << b;
    return code;
}

// Scan buffered bits for a phasing run.  On the WIRE, SITOR-B interleaving
// turns the message-channel ABAB phasing into a periodic mix of A and B
// (warm-up c0,c1,c2 then alternating copy/fresh pairs), so the robust
// indicator is a run of wire chars that are ALL in {phasingA, phasingB}.
// We pick the 5-bit alignment offset that yields the longest such run.
// Once locked, we start consuming 3 wire positions before the last phasing
// char (the lagging copy of the final phasing char sits 3 wire slots after
// the message channel's first Z), and enter kPreamble which tolerates any
// trailing phasing chars.
void NavtexDecoder::tryAlign() {
    if (alignBits_ >= 0) return;
    const long n = static_cast<long>(bits_.size());
    if (n < kNavtexBitsPerChar * (kNavtexPhasingMinChars + 6)) return;

    int bestAlign = -1;
    long bestRun = 0;
    for (int a = 0; a < 5; ++a) {
        long jMax = (n - 1 - a) / 5;
        if (jMax < kNavtexPhasingMinChars) continue;
        // Scan forward: find the longest run of chars in {A, B} anywhere in
        // the buffer.  The phasing preamble sits at the START of a burst; the
        // tail (text/NNNN) is non-phasing, so walking back from jMax finds
        // nothing.
        long run = 0;
        for (long j = 0; j <= jMax; ++j) {
            int c = charAt(a, j);
            if (c == kNavtexPhasingA || c == kNavtexPhasingB) {
                ++run;
            } else {
                if (run > bestRun) { bestRun = run; bestAlign = a; }
                run = 0;
            }
        }
        if (run > bestRun) { bestRun = run; bestAlign = a; }
    }
    if (bestRun >= kNavtexPhasingMinChars && bestAlign >= 0) {
        alignBits_ = bestAlign;
        // Replay from char 0: the state machine is already in kPreamble and
        // ignores trailing phasing chars, so the long phasing preamble is
        // skipped cheaply while we still build msgChars_ for diversity.
        charConsumed_ = 0;
        wireIdx_ = 0;
        msgChars_.clear();
        phasingOk_ = true;
        fstate_ = FState::kPreamble;
        preambleGot_ = 0;
    }
}

// -- Streaming --------------------------------------------------------------
void NavtexDecoder::feed(const std::vector<std::complex<float>>& baseband) {
    if (!demod_ || baseband.empty()) return;
    FskDemod* d = static_cast<FskDemod*>(demod_);
    d->process(baseband);
    std::vector<int> obits = d->takeBits();
    bits_.insert(bits_.end(), obits.begin(), obits.end());

    // Try to acquire char alignment on the phasing pattern.
    if (alignBits_ < 0) {
        tryAlign();
    }

    // Once aligned, drain any complete chars we have not yet processed.
    if (alignBits_ >= 0) {
        for (;;) {
            int c = charAt(alignBits_, charConsumed_);
            if (c < 0) break;
            processChar(c);
            ++charConsumed_;
        }
        // Trim consumed bits to keep memory bounded.
        const long keepFrom = alignBits_ + 5 * charConsumed_;
        if (keepFrom > 200 && keepFrom < static_cast<long>(bits_.size())) {
            bits_.erase(bits_.begin(), bits_.begin() + keepFrom);
            alignBits_ = 0;
            charConsumed_ = 0;
        }
    }
}

std::vector<NavtexMessage> NavtexDecoder::takeNewMessages() {
    std::vector<NavtexMessage> out;
    out.swap(out_);
    return out;
}

// -- Wire de-interleave + diversity ------------------------------------------
// SITOR-B wire layout (per SPEC):
//   w = c0 c1 c2 c0' c3 c1' c4 c2' c5 c3' ...
// i.e. warm-up c0,c1,c2 on the message channel; then alternating
//   copy-of-c_k at w[3+2k]  and  fresh c_{k+3} at w[4+2k].
// The message channel (odd 1-indexed positions) carries c0,c1,c2,c3,c4...
// The delayed-copy channel (even 1-indexed) carries c0',c1',c2'...
// Diversity: at each copy position, compare against the already-recovered
// c_k; count mismatches HONESTLY (never silently correct).
void NavtexDecoder::processChar(int code) {
    if (wireIdx_ < 3) {
        // Warm-up: every char is a fresh message char.
        msgChars_.push_back(code);
        handleMessageChar(code);
    } else {
        const int d = static_cast<int>(wireIdx_) - 3;   // 0,1,2,3,...
        if (d % 2 == 0) {
            // Delayed copy channel: copy of c_{d/2}.
            const int k = d / 2;
            if (k < static_cast<int>(msgChars_.size()) &&
                code != msgChars_[k]) {
                ++diversityErrors_;
            }
        } else {
            // Fresh message channel: c_{3 + d/2}.
            msgChars_.push_back(code);
            handleMessageChar(code);
        }
    }
    ++wireIdx_;
}

// -- Frame state machine -----------------------------------------------------
void NavtexDecoder::handleMessageChar(int code) {
    switch (fstate_) {
    case FState::kIdle: {
        // Scan for an alternating A/B phasing run.
        if (code == phaseExpect_) {
            ++phaseCount_;
            phaseExpect_ = (phaseExpect_ == kNavtexPhasingA)
                               ? kNavtexPhasingB
                               : kNavtexPhasingA;
        } else if (code == kNavtexPhasingA || code == kNavtexPhasingB) {
            // A phasing char arrived but not the expected one -> restart run.
            phaseCount_ = 1;
            phaseExpect_ = (code == kNavtexPhasingA)
                               ? kNavtexPhasingB
                               : kNavtexPhasingA;
        } else {
            phaseCount_ = 0;
            phaseExpect_ = kNavtexPhasingA;
        }
        if (phaseCount_ >= kNavtexPhasingMinChars) {
            phasingOk_ = true;
            fstate_ = FState::kPreamble;
            preambleGot_ = 0;
        }
        break;
    }
    case FState::kPreamble: {
        // Expect Z C Z C in order.  Trailing phasing chars (lagging copies
        // of the final phasing tone) may arrive here after alignment; ignore
        // them so they don't reset the preamble match.
        static const int kExpect[4] = { kIta2Z, kIta2C, kIta2Z, kIta2C };
        if (code == kNavtexPhasingA || code == kNavtexPhasingB) {
            break;   // trailing phasing copy; stay in preamble
        }
        if (code == kExpect[preambleGot_]) {
            ++preambleGot_;
            if (preambleGot_ == 4) {
                fstate_ = FState::kExpectSpace;
            }
        } else {
            // Preamble lost: restart. This char might start a new phasing run.
            resetFrameState();
            handleMessageChar(code);
        }
        break;
    }
    case FState::kExpectSpace: {
        if (code == kIta2Space) {
            fstate_ = FState::kHeader;
            headerGot_ = 0;
            diversityErrors_ = 0;   // per-message diversity window starts here
            headerBuf_.clear();
        } else {
            resetFrameState();
            handleMessageChar(code);
        }
        break;
    }
    case FState::kHeader: {
        // B1..B4 decoded with current shift; shift codes may appear here
        // (sender drops to figures for the B3B4 number).
        if (code == kIta2Ltrs) {
            shiftFigures_ = false;
        } else if (code == kIta2Figs) {
            shiftFigures_ = true;
        } else {
            int ch = decodeShifted(code, shiftFigures_);
            if (ch != 0) headerBuf_.push_back(static_cast<char>(ch));
            ++headerGot_;
            if (headerGot_ == 4) {
                cur_.stationB1 = headerBuf_.size() > 0
                                    ? std::string(1, headerBuf_[0])
                                    : std::string();
                cur_.typeB2 = headerBuf_.size() > 1
                                  ? std::string(1, headerBuf_[1])
                                  : std::string();
                cur_.numberB3B4 = headerBuf_.size() > 2
                                      ? headerBuf_.substr(2)
                                      : std::string();
                fstate_ = FState::kText;
            }
        }
        break;
    }
    case FState::kText: {
        if (code == kIta2Ltrs) {
            shiftFigures_ = false;
            nRun_ = 0;
        } else if (code == kIta2Figs) {
            shiftFigures_ = true;
            nRun_ = 0;
        } else if (code == kIta2N && !shiftFigures_) {
            // Consecutive N's (letters case). 4 in a row = end-of-message.
            ++nRun_;
            cur_.text.push_back('N');
            if (nRun_ == 4) {
                // Strip the trailing "NNNN" we just appended.
                if (cur_.text.size() >= 4)
                    cur_.text.erase(cur_.text.size() - 4);
                cur_.phasingOk = phasingOk_;
                cur_.diversityErrors = diversityErrors_;
                cur_.diversityOk = (diversityErrors_ == 0);
                out_.push_back(cur_);
                resetFrameState();
            }
        } else if (code == kIta2Null) {
            nRun_ = 0;
        } else {
            nRun_ = 0;
            int ch = decodeShifted(code, shiftFigures_);
            if (ch != 0) cur_.text.push_back(static_cast<char>(ch));
        }
        break;
    }
    }
}

} // namespace dsp
} // namespace mbdsdr
