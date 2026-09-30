// SPDX-License-Identifier: MIT
// Offscreen self-connect test for the clean-room SpyServer server:
//   start server -> a real local TCP client socket speaks the wire protocol
//   from docs/learn/spyserver.md: HELLO -> DEVICE_INFO+CLIENT_SYNC handshake,
//   SET_SETTING (IQ_FORMAT / DECIMATION / FREQUENCY / STREAMING_MODE / ENABLED),
//   then real 20B-header interleaved-I/Q frames flow, and ENABLED=0 stops the
//   stream without dropping the TCP connection.
//
// IQ source: the REAL SpectrumEngine with its offline TestSignalSource (an
// honestly-labelled synthetic, non-hardware producer) -- feedIQ() is fed the
// same block the engine's recorder/downstream chain see, never fabricated here.
#include <QtTest/QtTest>
#include <QTcpSocket>
#include <QTcpServer>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QtEndian>
#include <vector>
#include <complex>
#include <cmath>

#include "dsp/spectrum_engine.h"
#include "dsp/spyserver_server.h"

using namespace mbdsdr::dsp;

static inline void putU32(QByteArray& b, quint32 v) {
    uchar t[4]; qToLittleEndian<quint32>(v, t); b.append(reinterpret_cast<char*>(t), 4);
}
static inline quint32 getU32(const QByteArray& b, int off) {
    return qFromLittleEndian<quint32>(reinterpret_cast<const uchar*>(b.constData() + off));
}

class TestSpyServer : public QObject {
    Q_OBJECT
private slots:
    void handshakeAndStreaming();
};

// Pump the event loop until at least `n` bytes are readable (or timeout).
static bool waitBytes(QTcpSocket* s, qint64 n, int timeoutMs) {
    QElapsedTimer t; t.start();
    while (s->bytesAvailable() < n && t.elapsed() < timeoutMs) {
        qApp->processEvents(QEventLoop::AllEvents, 30);
        QTest::qWait(5);
    }
    return s->bytesAvailable() >= n;
}
static QByteArray readExact(QTcpSocket* s, int n, int timeoutMs) {
    QByteArray out;
    out.reserve(n);
    QElapsedTimer t; t.start();
    while (out.size() < n && t.elapsed() < timeoutMs) {
        const qint64 need = n - out.size();
        if (s->bytesAvailable() >= need) { out += s->read(need); break; }
        qApp->processEvents(QEventLoop::AllEvents, 30);
        QTest::qWait(5);
    }
    return out;
}

void TestSpyServer::handshakeAndStreaming() {
    SpectrumEngine eng;
    SpyServerServer server;

    // Wire the server's tuner straight to the real engine (frequency/gain).
    SpyServerTuner tuner;
    tuner.setCenterFreq = [&eng](double hz) { eng.onSetCenterFreq(hz); };
    tuner.setGain       = [&eng](double db) { eng.onSetGain(db); };
    tuner.queryInfo = [&eng](double& maxSr, double& minHz, double& maxHz,
                             double& centerHz, double& gainDb) {
        maxSr = 2.4e6;                 // engine native rate (RTL default)
        minHz = 24e6; maxHz = 1700e6;
        centerHz = eng.centerFreq();
        gainDb = 0.0;
    };
    server.setTuner(tuner);
    // Real IQ path: engine block -> server (queued across threads).
    connect(&eng, &SpectrumEngine::iqTapReady,
            &server, &SpyServerServer::feedIQ);
    connect(&server, &SpyServerServer::iqTapRequired,
            &eng, &SpectrumEngine::setSpyServerTapRequested);

    eng.start();
    QTest::qWait(150);                 // let the source settle + first blocks flow

    QVERIFY(server.start(0));          // OS-assigned free port
    const quint16 port = server.port();
    QVERIFY(port > 0);

    QTcpSocket cli;
    cli.connectToHost(QHostAddress::LocalHost, port);
    QVERIFY2(cli.waitForConnected(2000), "client connect");

    // ---- HELLO: cmd=0, body = version(0x020006A4) + name (no NUL) ----
    {
        QByteArray pkt;
        putU32(pkt, 0);                          // CommandType = HELLO
        putU32(pkt, 4u + 11u);                   // BodySize
        putU32(pkt, 0x020006A4u);                // version
        pkt.append("MBDSDRTest", 11);
        cli.write(pkt);
    }

    // ---- Expect DEVICE_INFO: 20B header + 48B body, msgType == 0 ----
    QByteArray hdr = readExact(&cli, 20, 3000);
    QCOMPARE(hdr.size(), 20);
    const quint32 diMsgType = getU32(hdr, 4);
    const quint32 diBodySize = getU32(hdr, 16);
    QCOMPARE(diMsgType, 0u);            // DEVICE_INFO
    QCOMPARE(diBodySize, 48u);          // 12 x u32
    QByteArray di = readExact(&cli, 48, 1000);
    QCOMPARE(di.size(), 48);
    const quint32 maxSampleRate = getU32(di, 8);   // slot 2 = MaximumSampleRate
    QCOMPARE(maxSampleRate, 2400000u);
    const quint32 decimStages = getU32(di, 16);    // slot 4
    QVERIFY(decimStages >= 1);

    // ---- Expect CLIENT_SYNC: 20B header + 36B body, msgType == 1 ----
    QByteArray hdr2 = readExact(&cli, 20, 1000);
    QCOMPARE(hdr2.size(), 20);
    QCOMPARE(getU32(hdr2, 4), 1u);      // CLIENT_SYNC
    QCOMPARE(getU32(hdr2, 16), 36u);   // 9 x u32
    QByteArray sync = readExact(&cli, 36, 1000);
    QCOMPARE(sync.size(), 36);

    // ---- SET_SETTING helper ----
    auto setSetting = [&cli](quint32 setting, quint32 value) {
        QByteArray pkt;
        putU32(pkt, 2);                 // CommandType = SET_SETTING
        putU32(pkt, 8);                 // BodySize
        putU32(pkt, setting);
        putU32(pkt, value);
        cli.write(pkt);
    };

    // Agree: Int16 IQ (format enum 2), decimation stage 1, tune to 100 MHz,
    // IQ-only streaming mode, then ENABLE streaming.
    const quint32 kDecim = 1;
    setSetting(100, 2);                 // IQ_FORMAT = Int16
    setSetting(102, kDecim);            // IQ_DECIMATION = 1
    setSetting(101, 100000000u);         // IQ_FREQUENCY = 100 MHz
    setSetting(0, 1);                    // STREAMING_MODE = IQ
    setSetting(1, 1);                    // STREAMING_ENABLED = on

    // ---- Expect real IQ frames. Parameter SET_SETTINGs also trigger the
    // server to re-push CLIENT_SYNC (msgType=1, note §7.2), so skip any sync
    // messages until an INT16_IQ (low16=101) frame arrives. ----
    quint32 iqBodySize = 0;
    bool gotIq = false;
    for (int guard = 0; guard < 50; ++guard) {
        QByteArray ih = readExact(&cli, 20, 3000);
        if (ih.size() != 20) break;
        const quint32 mtype = getU32(ih, 4);
        const quint32 low = mtype & 0xFFFFu;
        const quint32 streamType = getU32(ih, 8);
        iqBodySize = getU32(ih, 16);
        QByteArray body = readExact(&cli, static_cast<int>(iqBodySize), 2000);
        QCOMPARE(body.size(), static_cast<int>(iqBodySize));
        if (low == 1u) continue;                 // CLIENT_SYNC resync: skip
        QCOMPARE(low, 101u);                     // INT16_IQ
        QCOMPARE(streamType, 1u);                // IQ stream
        QVERIFY(iqBodySize > 0);                 // non-empty real IQ body
        QVERIFY(iqBodySize % 4u == 0u);         // Int16 = 4 bytes per I/Q pair
        gotIq = true;
        break;
    }
    QVERIFY2(gotIq, "an INT16_IQ frame must arrive after ENABLED=1");

    // Frequency must have reached the real engine tuner.
    QTest::qWait(60);
    QVERIFY2(std::fabs(eng.centerFreq() - 100000000.0) < 1.0,
             "client-set center frequency must land in the engine");

    // Derived output sample rate contract: MaxSampleRate >> decimation.
    const quint32 expectedRate = maxSampleRate >> kDecim;
    QCOMPARE(expectedRate, 1200000u);  // 2.4 MHz >> 1

    // ---- ENABLED=0: stop pushing, but keep the TCP connection alive ----
    setSetting(1, 0);
    // Drain whatever was already in flight (buffered IQ frames + one last
    // queued feedIQ) before checking the stream has truly stopped.
    for (int i = 0; i < 60; ++i) {
        qApp->processEvents(QEventLoop::AllEvents, 20);
        if (cli.bytesAvailable()) cli.readAll();
        QTest::qWait(10);
    }
    // Now the stream must be silent for a grace window (and the socket stays up).
    const bool gotLate = waitBytes(&cli, 1, 400);
    QVERIFY2(!gotLate, "ENABLED=0 must stop pushing IQ frames");
    QCOMPARE(cli.state(), QAbstractSocket::ConnectedState);   // connection kept

    cli.disconnectFromHost();
    eng.shutdown();
    eng.wait(2000);
    server.stop();
}

QTEST_MAIN(TestSpyServer)
#include "test_spyserver_server.moc"
