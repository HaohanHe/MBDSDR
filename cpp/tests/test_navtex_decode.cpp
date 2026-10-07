// SPDX-License-Identifier: MIT
//
// NAVTEX / SITOR-B decoder unit tests (QtTest).
//
// SYNTHETIC FIXTURE -- the encoder below is a test-only, clean-room model of
// SITOR-B over 2-FSK (100 baud, +/-85 Hz). It drives the REAL NavtexDecoder
// (which owns a real FskDemod through a quadrature discriminator + Mueller-
// Muller timing recovery) and checks:
//   * a clean injected message decodes station/type/number/text exactly,
//     phasingOk=1, diversityOk=1, diversityErrors=0,
//   * an AWGN SNR gradient (12..0 dB, 20 trials/point) prints the honest
//     3-column table (decoded / diversity-pass / phasing-ok) and the honest
//     failure threshold,
//   * pure Gaussian noise (8 s) and total silence (8 s) yield ZERO messages
//     (the decoder must not fabricate a frame).
//
// No GPL source; no RF frequency constants (518/490 kHz never appear here).
#include <QtTest/QtTest>

#include "dsp/navtex_decoder.h"

#include <cmath>
#include <complex>
#include <cstdint>
#include <random>
#include <string>
#include <vector>

using namespace mbdsdr::dsp;

namespace {

constexpr double kFs = 8000.0;
constexpr double kTwoPi = 6.28318530717958647692;
constexpr int    kSps = static_cast<int>(kFs / kNavtexSymbolRateBd); // 80

// -- ITA-2 reverse lookup (test-only encoder) --------------------------------
// Letters case: upper-case char -> 5-bit code.
int ita2LetterCode(char c) {
    switch (c) {
    case 'A': return 3;  case 'B': return 25; case 'C': return 14;
    case 'D': return 9;  case 'E': return 1;  case 'F': return 13;
    case 'G': return 26; case 'H': return 20; case 'I': return 6;
    case 'J': return 11; case 'K': return 15; case 'L': return 18;
    case 'M': return 28; case 'N': return 12; case 'O': return 24;
    case 'P': return 22; case 'Q': return 23; case 'R': return 10;
    case 'S': return 5;  case 'T': return 16; case 'U': return 7;
    case 'V': return 30; case 'W': return 19; case 'X': return 29;
    case 'Y': return 21; case 'Z': return 17;
    }
    return -1;
}
// Figures case: digit/punctuation -> 5-bit code.
int ita2FigureCode(char c) {
    switch (c) {
    case '1': return 23; case '2': return 19; case '3': return 1;
    case '4': return 10; case '5': return 16; case '6': return 21;
    case '7': return 7;  case '8': return 6;  case '9': return 24;
    case '0': return 22; case '-': return 3;  case '$': return 9;
    case '\'': return 11; case ',': return 12; case '!': return 13;
    case ':': return 14; case '(': return 15; case '"': return 17;
    case ')': return 18; case '#': return 20; case '?': return 25;
    case '@': return 26; case '.': return 28; case '/': return 29;
    case ';': return 30;
    }
    return -1;
}
// Is this char a figure (digit/punct) rather than a letter?
bool isFigure(char c) {
    if (c >= '0' && c <= '9') return true;
    switch (c) {
    case '-': case '$': case '\'': case ',': case '!': case ':':
    case '(': case '"': case ')': case '#': case '?': case '@':
    case '.': case '/': case ';':
        return true;
    }
    return false;
}

// Encode a human-readable string into ITA-2 5-bit codes, inserting shift
// codes as needed. Starts in letters mode. Space (code 4) works in either.
std::vector<int> encodeText(const std::string& s) {
    std::vector<int> out;
    bool figures = false;
    for (char c : s) {
        if (c == ' ') {
            out.push_back(kIta2Space);
        } else if (c == '\n' || c == '\r') {
            out.push_back(c == '\n' ? kIta2Lf : kIta2Cr);
        } else if (isFigure(c)) {
            if (!figures) { out.push_back(kIta2Figs); figures = true; }
            int code = ita2FigureCode(c);
            if (code >= 0) out.push_back(code);
        } else {
            if (figures) { out.push_back(kIta2Ltrs); figures = false; }
            int code = ita2LetterCode(c);
            if (code >= 0) out.push_back(code);
        }
    }
    return out;
}

// Build the message-channel 5-bit code sequence for a NAVTEX frame.
// phasingChars: number of alternating A/B phasing chars (>=20 required).
std::vector<int> buildMessageCodes(const std::string& stationB1,
                                   const std::string& typeB2,
                                   const std::string& numberB3B4,
                                   const std::string& text,
                                   int phasingChars) {
    std::vector<int> m;
    // 1) Phasing run: A B A B ...
    for (int i = 0; i < phasingChars; ++i)
        m.push_back((i % 2 == 0) ? kNavtexPhasingA : kNavtexPhasingB);
    // 2) ZCZC
    m.push_back(kIta2Z); m.push_back(kIta2C);
    m.push_back(kIta2Z); m.push_back(kIta2C);
    // 3) Space
    m.push_back(kIta2Space);
    // 4) B1 (letter), B2 (letter), then FIGS, B3, B4 (digits), LTRS.
    m.push_back(ita2LetterCode(stationB1.empty() ? 'A' : stationB1[0]));
    m.push_back(ita2LetterCode(typeB2.empty()    ? 'B' : typeB2[0]));
    m.push_back(kIta2Figs);
    m.push_back(numberB3B4.size() > 0 ? ita2FigureCode(numberB3B4[0]) : 22);
    m.push_back(numberB3B4.size() > 1 ? ita2FigureCode(numberB3B4[1]) : 23);
    m.push_back(kIta2Ltrs);
    // 5) Body text
    std::vector<int> body = encodeText(text);
    m.insert(m.end(), body.begin(), body.end());
    // 6) NNNN end
    m.push_back(kIta2N); m.push_back(kIta2N);
    m.push_back(kIta2N); m.push_back(kIta2N);
    // 7) Trailing null padding: the 5-bit char alignment offset means the
    //    last wire char's bits may otherwise be truncated at the buffer end.
    for (int i = 0; i < 6; ++i) m.push_back(kIta2Null);
    return m;
}

// SITOR-B wire interleaver (mirrors the decoder's de-interleave rule).
//   w = c0 c1 c2 c0' c3 c1' c4 c2' c5 c3' ...
std::vector<int> interleaveWire(const std::vector<int>& msg) {
    std::vector<int> w;
    if (msg.size() < 3) return msg;
    w.push_back(msg[0]);
    w.push_back(msg[1]);
    w.push_back(msg[2]);
    const int M = static_cast<int>(msg.size());
    for (int k = 0; k + 3 < M; ++k) {
        w.push_back(msg[k]);       // delayed copy of c_k
        w.push_back(msg[3 + k]);    // fresh c_{k+3}
    }
    return w;
}

// 5-bit ITA-2 codes -> bit stream (LSB first, synchronous).
std::vector<int> codesToBits(const std::vector<int>& codes) {
    std::vector<int> bits;
    bits.reserve(codes.size() * kNavtexBitsPerChar);
    for (int c : codes) {
        for (int b = 0; b < kNavtexBitsPerChar; ++b)
            bits.push_back((c >> b) & 1);
    }
    return bits;
}

// 2-FSK modulate bits -> complex baseband IQ (bit 1 = +deviation,
// bit 0 = -deviation; continuous phase across bit boundaries).
std::vector<std::complex<float>> modulate(const std::vector<int>& bits) {
    std::vector<std::complex<float>> iq;
    iq.reserve(bits.size() * kSps);
    double phase = 0.0;
    for (int b : bits) {
        const double f = (b != 0) ? kNavtexDeviationHz : -kNavtexDeviationHz;
        const double dphi = kTwoPi * f / kFs;
        for (int i = 0; i < kSps; ++i) {
            iq.push_back(std::complex<float>(static_cast<float>(std::cos(phase)),
                                             static_cast<float>(std::sin(phase))));
            phase += dphi;
        }
    }
    return iq;
}

// Add complex AWGN at SNR_dB (signal power = 1 on the unit-modulus tone).
void addAwgn(std::vector<std::complex<float>>& iq, double snrDb,
             std::mt19937& rng) {
    const double noisePow = std::pow(10.0, -snrDb / 10.0); // signal=1
    const double sigma = std::sqrt(noisePow / 2.0);        // per I/Q
    std::normal_distribution<double> ng(0.0, sigma);
    for (auto& s : iq) {
        s += std::complex<float>(static_cast<float>(ng(rng)),
                                static_cast<float>(ng(rng)));
    }
}

} // namespace

class TestNavtexDecode : public QObject {
    Q_OBJECT
private slots:
    void cleanMessageDecodesExactly();
    void snrGradientTable();
    void pureNoiseIsHonestEmpty();
    void silenceIsHonestEmpty();
};

void TestNavtexDecode::cleanMessageDecodesExactly() {
    const std::string b1 = "A", b2 = "B", num = "01",
                      text = "HELLO NAVTEX 123 TEST";
    std::vector<int> m = buildMessageCodes(b1, b2, num, text, 40);
    std::vector<int> w = interleaveWire(m);
    std::vector<int> bits = codesToBits(w);
    std::vector<std::complex<float>> iq = modulate(bits);

    NavtexDecoder dec;
    dec.setSampleRate(kFs);
    dec.feed(iq);
    auto msgs = dec.takeNewMessages();

    QCOMPARE(msgs.size(), std::size_t(1));
    const NavtexMessage& msg = msgs.front();
    QVERIFY2(msg.phasingOk, "phasing should lock on clean signal");
    QVERIFY2(msg.diversityOk, "clean signal must have 0 diversity errors");
    QCOMPARE(msg.diversityErrors, 0);
    QCOMPARE(msg.stationB1, QString::fromStdString(b1));
    QCOMPARE(msg.typeB2, QString::fromStdString(b2));
    QCOMPARE(msg.numberB3B4, QString::fromStdString(num));
    QCOMPARE(QString::fromStdString(msg.text),
             QString::fromStdString(text));
}

void TestNavtexDecode::snrGradientTable() {
    const std::string b1 = "A", b2 = "B", num = "01",
                      text = "HELLO NAVTEX 123 TEST";
    const int trials = 20;

    // Build the signal once; per-trial noise uses a fresh seed.
    std::vector<int> m = buildMessageCodes(b1, b2, num, text, 40);
    std::vector<int> w = interleaveWire(m);
    std::vector<int> bits = codesToBits(w);

    QTextStream out(stdout);
    out << "SNR(dB)  decoded  diversity-pass  phasing-ok" << Qt::endl;
    out << "--------------------------------------------" << Qt::endl;

    int failThreshold = -1; // first SNR (dB) where decoded/trials < 50%

    for (int snr = 12; snr >= 0; --snr) {
        int decoded = 0, divPass = 0, phaseOk = 0;
        for (int t = 0; t < trials; ++t) {
            std::mt19937 rng(1000u + snr * 100u + t);
            std::vector<std::complex<float>> iq = modulate(bits);
            addAwgn(iq, snr, rng);

            NavtexDecoder dec;
            dec.setSampleRate(kFs);
            dec.feed(iq);
            auto msgs = dec.takeNewMessages();

            bool gotOne = false, dp = false, po = false;
            for (const auto& msg : msgs) {
                if (msg.stationB1 == b1 && msg.typeB2 == b2 &&
                    msg.numberB3B4 == num && msg.text == text) {
                    gotOne = true;
                    dp = msg.diversityOk;
                    po = msg.phasingOk;
                }
            }
            if (gotOne) ++decoded;
            if (dp) ++divPass;
            if (po) ++phaseOk;
        }
        out << QString("%1      %2/%3     %4/%5           %6/%7")
                  .arg(snr, 2)
                  .arg(decoded).arg(trials)
                  .arg(divPass).arg(trials)
                  .arg(phaseOk).arg(trials)
            << Qt::endl;
        if (failThreshold < 0 && decoded * 2 < trials)
            failThreshold = snr;
    }
    out << "--------------------------------------------" << Qt::endl;
    out << "Honest failure threshold (decoded < 50%): "
        << (failThreshold >= 0 ? QString("%1 dB").arg(failThreshold)
                               : QString("> 0 dB (still >=50% at 0 dB)"))
        << Qt::endl;
}

void TestNavtexDecode::pureNoiseIsHonestEmpty() {
    // 8 seconds of Gaussian noise, no signal. Must yield 0 messages.
    const int n = static_cast<int>(8.0 * kFs);
    std::mt19937 rng(42);
    std::normal_distribution<double> ng(0.0, 0.5);
    std::vector<std::complex<float>> iq(n);
    for (int i = 0; i < n; ++i)
        iq[i] = std::complex<float>(static_cast<float>(ng(rng)),
                                    static_cast<float>(ng(rng)));

    NavtexDecoder dec;
    dec.setSampleRate(kFs);
    dec.feed(iq);
    auto msgs = dec.takeNewMessages();
    QVERIFY2(msgs.empty(), "pure noise must NOT fabricate a NAVTEX message");
}

void TestNavtexDecode::silenceIsHonestEmpty() {
    // 8 seconds of total silence (zeros). Must yield 0 messages.
    const int n = static_cast<int>(8.0 * kFs);
    std::vector<std::complex<float>> iq(n, std::complex<float>(0, 0));

    NavtexDecoder dec;
    dec.setSampleRate(kFs);
    dec.feed(iq);
    auto msgs = dec.takeNewMessages();
    QVERIFY2(msgs.empty(), "silence must NOT fabricate a NAVTEX message");
}

QTEST_MAIN(TestNavtexDecode)
#include "test_navtex_decode.moc"
