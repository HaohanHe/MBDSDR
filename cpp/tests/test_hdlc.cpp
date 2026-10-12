// SPDX-License-Identifier: MIT
// HDLC bit-level tests for the APRS/AX.25 receiver chain: NRZI decode,
// flag hunt, zero-deletion, CRC-16/X.25 check, and the encode->deframe
// round trip. Synthetic frames use placeholder callsigns (TEST-1/TEST-2)
// -- honest test fixtures, never preset real callsigns.
#include <QtTest/QtTest>

#include "radio/hdlc.h"
#include "radio/ax25.h"

using namespace mbdsdr;

class TestHdlc : public QObject {
    Q_OBJECT
private slots:
    void nrziDecodeInvertsTransitions();
    void roundTripEncodedFrameDecodes();
    void badCrcFrameIsRejected();
    void garbageStreamYieldsEmpty();
    void bitStuffingRoundTrip();
};

void TestHdlc::nrziDecodeInvertsTransitions() {
    // NRZI: no transition = 1, transition = 0.
    // Raw bits 1100 -> transitions: 1(no) 1(no) 0(trans) 1(no, level 0)
    // With implicit low reference: 1 -> level 1 (no transition) -> 1,
    // 1 -> 1, 0 -> flip to 0 -> decoded 0, 0 -> no transition -> 1.
    QByteArray raw(4, 0);
    raw[0] = 1; raw[1] = 1; raw[2] = 0; raw[3] = 0;
    const QByteArray dec = radio::nrziDecode(raw);
    QCOMPARE(dec.size(), 4);
    // 1 (rise from low ref) -> 0, 1 -> 1, 0 -> 0, 0 -> 1  => 0101
    QCOMPARE(static_cast<int>(dec[0]), 0);
    QCOMPARE(static_cast<int>(dec[1]), 1);
    QCOMPARE(static_cast<int>(dec[2]), 0);
    QCOMPARE(static_cast<int>(dec[3]), 1);
}

void TestHdlc::roundTripEncodedFrameDecodes() {
    radio::Ax25Frame f;
    QVERIFY(radio::parseAx25Addr(QStringLiteral("TEST-1"), f.dst));
    QVERIFY(radio::parseAx25Addr(QStringLiteral("TEST-2"), f.src));
    f.control = 0x03;
    f.pid = 0xF0;
    f.info = QByteArray("HELLO 73");
    const QByteArray payload = radio::encodeAx25(f);
    QVERIFY2(!payload.isEmpty(), "encodeAx25 must succeed for valid addresses");

    const QByteArray stream = radio::hdlcEncode(payload);
    const QByteArray nrzi = radio::nrziDecode(stream);
    const QList<QByteArray> frames = radio::hdlcDeframe(nrzi);
    QCOMPARE(frames.size(), 1);

    radio::Ax25Frame out;
    QVERIFY(radio::decodeAx25(frames.first(), out));
    QCOMPARE(out.src.toString(), QStringLiteral("TEST-2"));
    QCOMPARE(out.dst.toString(), QStringLiteral("TEST-1"));
    QCOMPARE(out.info, QByteArray("HELLO 73"));
}

void TestHdlc::badCrcFrameIsRejected() {
    radio::Ax25Frame f;
    QVERIFY(radio::parseAx25Addr(QStringLiteral("TEST-1"), f.dst));
    QVERIFY(radio::parseAx25Addr(QStringLiteral("TEST-2"), f.src));
    f.info = QByteArray("payload");
    const QByteArray payload = radio::encodeAx25(f);
    QByteArray stream = radio::hdlcEncode(payload);
    // Corrupt one payload bit (flip a data bit inside the frame body, not the
    // flag region).
    stream[40] = stream[40] == 0 ? 1 : 0;
    const QByteArray nrzi = radio::nrziDecode(stream);
    const QList<QByteArray> frames = radio::hdlcDeframe(nrzi);
    QVERIFY2(frames.isEmpty(), "corrupted frame must fail the CRC check");
}

void TestHdlc::garbageStreamYieldsEmpty() {
    QByteArray garbage(400, 0);
    for (int i = 0; i < garbage.size(); ++i)
        garbage[i] = (i * 7 + i / 3) % 2;
    const QList<QByteArray> frames = radio::hdlcDeframe(radio::nrziDecode(garbage));
    QVERIFY2(frames.isEmpty(), "random bit stream must decode to no valid frames");
}

void TestHdlc::bitStuffingRoundTrip() {
    // A payload containing 5+ consecutive 1s exercises zero-deletion both ways.
    radio::Ax25Frame f;
    QVERIFY(radio::parseAx25Addr(QStringLiteral("TEST-1"), f.dst));
    QVERIFY(radio::parseAx25Addr(QStringLiteral("TEST-2"), f.src));
    f.info = QByteArray("\x1F\x1F\x1F\x1F", 4);   // 00011111 repeated: runs of 5 ones
    const QByteArray payload = radio::encodeAx25(f);
    const QByteArray stream = radio::hdlcEncode(payload);
    const QList<QByteArray> frames = radio::hdlcDeframe(radio::nrziDecode(stream));
    QCOMPARE(frames.size(), 1);
    QCOMPARE(frames.first(), payload);
}

QTEST_MAIN(TestHdlc)
#include "test_hdlc.moc"
