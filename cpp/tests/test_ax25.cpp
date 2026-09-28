// SPDX-License-Identifier: GPL-3.0-or-later
// AX.25 + KISS round-trip self-check (not hardware).
#include "radio/ax25.h"
#include "radio/kiss.h"

#include <QByteArray>
#include <cstdio>

using namespace mbdsdr::radio;

static int g_failures = 0;
#define CHECK(cond, msg) do { \
    if (!(cond)) { std::printf("FAIL: %s\n", msg); ++g_failures; } \
} while (0)

int main() {
    Ax25Frame f;
    CHECK(parseAx25Addr("APZ001", f.dst), "parse dst");
    CHECK(parseAx25Addr("N7LEM-9", f.src), "parse src ssid");
    Ax25Addr p1, p2;
    CHECK(parseAx25Addr("WIDE1-1", p1), "parse WIDE1-1");
    CHECK(parseAx25Addr("WIDE2-2", p2), "parse WIDE2-2");
    f.path = {p1, p2};
    f.info = "!4903.50N/07201.75W-";

    const QByteArray raw = encodeAx25(f);
    CHECK(!raw.isEmpty(), "encode produced bytes");

    // Known first-address bytes for destination APZ001 (ssid 0, not last).
    const unsigned char expectDst[7] = {0x82,0xA0,0xB4,0x60,0x60,0x62,0x80};
    bool dstOk = true;
    for (int i = 0; i < 7; ++i)
        dstOk &= (unsigned char)raw[i] == expectDst[i];
    CHECK(dstOk, "dst APZ001 byte vector");

    // Round-trip decode.
    Ax25Frame back;
    CHECK(decodeAx25(raw, back), "decode frame");
    CHECK(back.dst.call == "APZ001" && back.dst.ssid == 0, "dst restored");
    CHECK(back.src.call == "N7LEM" && back.src.ssid == 9, "src+ssid restored");
    CHECK(back.path.size() == 2 && back.path[0].call == "WIDE1" &&
          back.path[0].ssid == 1 && back.path[1].call == "WIDE2" &&
          back.path[1].ssid == 2, "digipeater path restored");
    CHECK(back.control == 0x03 && back.pid == 0xF0, "UI control/PID");
    CHECK(QString::fromLatin1(back.info) == QString::fromLatin1(f.info),
          "info payload restored");

    // KISS wrap then one-shot decode returns the AX.25 payload.
    const QByteArray wrapped = kissWrap(raw);
    CHECK((unsigned char)wrapped.front() == KISS_FEND &&
          (unsigned char)wrapped.back() == KISS_FEND, "kiss delimiters");
    auto frames = kissDecode(wrapped);
    CHECK(frames.size() == 1 && frames[0] == raw, "kiss one-shot round-trip");

    // KISS escaping: payload containing FEND/FESC survives.
    QByteArray tricky;
    tricky.append(static_cast<char>(0xC0));
    tricky.append(static_cast<char>(0xDB));
    tricky.append("abc");
    const QByteArray w2 = kissWrap(tricky);
    auto f2 = kissDecode(w2);
    CHECK(f2.size() == 1 && f2[0] == tricky, "kiss FEND/FESC escaping");

    // Streaming: feed wrapped bytes in arbitrary 3-byte chunks.
    KissDecoder dec;
    for (int i = 0; i < wrapped.size(); i += 3)
        dec.feed(wrapped.mid(i, 3));
    auto streamed = dec.takeFrames();
    CHECK(streamed.size() == 1 && streamed[0] == raw, "kiss split-chunk decode");

    // Repeated FENDs (idle fill) must not create empty frames.
    QByteArray idle;
    idle.append(static_cast<char>(0xC0));
    idle.append(static_cast<char>(0xC0));
    idle.append(wrapped);
    CHECK(kissDecode(idle).size() == 1, "repeated FEND no phantom frames");

    // Malformed: truncated address field rejected.
    Ax25Frame junk;
    CHECK(!decodeAx25(QByteArray(10, '\x80'), junk), "truncated frame rejected");

    if (g_failures == 0) std::printf("ax25/kiss: all checks passed\n");
    return g_failures ? 1 : 0;
}
