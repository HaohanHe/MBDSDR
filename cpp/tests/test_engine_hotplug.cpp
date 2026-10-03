// SPDX-License-Identifier: MIT
// Device hotplug event-channel tests (real TCP loopback, no mocks):
//   1) an rtl_tcp server feeds IQ -> the engine connects (sourceChanged=true)
//   2) the server closes -> the engine detects the drop (sourceDropped) and
//      falls back to the offline test source (isTestSignalActive()==true)
//   3) the same server restarts on the same port -> the 2 s auto-reconnect
//      recovers without any user action (sourceChanged=true again)
//   4) connecting to a closed port fails and sourceError carries a real reason
#include <QtTest/QtTest>
#include <QSignalSpy>
#include <QTcpServer>
#include <QTcpSocket>
#include <QTimer>
#include <QHostAddress>
#include <QByteArray>

#include "dsp/spectrum_engine.h"

using namespace mbdsdr::dsp;

// Minimal rtl_tcp server: accepts one client, reads the two 5-byte setup
// commands (cmd + big-endian dword), then streams IQ at ~25 ms cadence until
// stop(). IQ samples are offset-127 8-bit (I/Q = 0); the engine only needs the
// byte count, so the payload is deliberately inert.
class RtlTcpServer : public QObject {
    Q_OBJECT
public:
    explicit RtlTcpServer(QObject* parent = nullptr) : QObject(parent) {
        streamTimer_.setInterval(25);
        connect(&streamTimer_, &QTimer::timeout, this, &RtlTcpServer::emitPacket);
    }
private:
    quint16 lastPort_ = 0;
public:
    bool listen() {
        if (!server_.listen(QHostAddress::LocalHost, 0)) return false;
        lastPort_ = server_.serverPort();
        connect(&server_, &QTcpServer::newConnection, this, &RtlTcpServer::accept);
        return true;
    }
    quint16 port() const { return lastPort_; }
    bool hasClient() const { return sock_ != nullptr; }
    // Restart on the SAME port (simulates the device being re-plugged).
    // The port must be remembered: QTcpServer::serverPort() returns 0 while
    // the server is not listening (it is already closed here).
    bool restart() {
        const quint16 p = lastPort_;
        server_.close();
        if (!server_.listen(QHostAddress::LocalHost, p)) return false;
        connect(&server_, &QTcpServer::newConnection, this, &RtlTcpServer::accept);
        return true;
    }
    void stop() {
        streamTimer_.stop();
        if (sock_) {
            sock_->disconnectFromHost();
            sock_->deleteLater();
            sock_ = nullptr;
        }
        server_.close();
    }
private slots:
    void accept() {
        QTcpSocket* s = server_.nextPendingConnection();
        // listen() and restart() both connect this slot, so it can fire
        // twice for one client; the second call returns null -- keep the
        // live socket (a null here used to kill the stream and make the
        // auto-reconnect flaky).
        if (!s) return;
        if (sock_) { sock_->disconnectFromHost(); sock_->deleteLater(); }
        sock_ = s;
        connect(sock_, &QTcpSocket::readyRead, this, &RtlTcpServer::readSetup);
    }
    void readSetup() {
        if (!sock_) return;
        cmdBuf_ += sock_->readAll();
        if (cmdBuf_.size() >= 10) {          // two 5-byte rtl_tcp commands
            cmdBuf_.clear();
            disconnect(sock_, &QTcpSocket::readyRead, this, &RtlTcpServer::readSetup);
            streamTimer_.start();
        }
    }
    void emitPacket() {
        if (!sock_ || sock_->state() != QAbstractSocket::ConnectedState) return;
        sock_->write(streamChunk_);
    }
private:
    QTcpServer server_;
    QTcpSocket* sock_ = nullptr;
    QByteArray cmdBuf_;
    QTimer streamTimer_;
    // ~25 ms of 2.4 MS/s IQ = 60 000 complex samples = 120 000 bytes.
    QByteArray streamChunk_{120000, static_cast<char>(0x80)};
};

class TestEngineHotplug : public QObject {
    Q_OBJECT
private slots:
    // Phase21: the idle/offline source is now the honest NullSource by default.
    // These cases expect the device drop / connect-failure to land on the offline
    // synthetic source (isTestSignalActive()==true), so opt in explicitly BEFORE
    // any engine is constructed (the flag is read in the ctor and reused by
    // installIdleSourceLocked on every drop/failure).
    void initTestCase();
    void dropDetectsAndFallsBack();
    void autoReconnectRecovers();
    void connectFailureReportsReason();
};

void TestEngineHotplug::initTestCase() {
    qputenv("MBDSDR_TEST_SOURCE", "1");   // explicit synthetic offline source
}

void TestEngineHotplug::dropDetectsAndFallsBack() {
    RtlTcpServer server;
    QVERIFY(server.listen());
    const quint16 port = server.port();

    SpectrumEngine eng;
    eng.start();                              // initial sourceChanged (test signal)
    QSignalSpy sc(&eng, &SpectrumEngine::sourceChanged);
    QSignalSpy sd(&eng, &SpectrumEngine::sourceDropped);

    QVERIFY(eng.connectRtlTcp("127.0.0.1", port));
    // Sync point: the server must really accept and the engine must really
    // stream IQ (spectrumReady advancing) before we simulate the unplug --
    // otherwise the "connected" state is just the TCP handshake.
    QTRY_VERIFY_WITH_TIMEOUT(server.hasClient(), 2000);
    QSignalSpy frames(&eng, &SpectrumEngine::spectrumReady);
    QTRY_VERIFY_WITH_TIMEOUT(frames.count() >= 5, 3000);
    QVERIFY(sc.last().at(1).toBool());
    QVERIFY(!eng.isTestSignalActive());

    server.stop();                            // device pulled out
    QVERIFY(sd.wait(4000));                   // sourceDropped fires once
    QTRY_VERIFY_WITH_TIMEOUT(eng.isTestSignalActive(), 2000);
    // The honest fallback is reported to the UI as disconnected.
    QTRY_VERIFY_WITH_TIMEOUT(
        sc.count() >= 2 && !sc.last().at(1).toBool(), 2000);

    eng.shutdown();
    eng.wait(2000);
}

void TestEngineHotplug::autoReconnectRecovers() {
    RtlTcpServer server;
    QVERIFY(server.listen());
    const quint16 port = server.port();

    SpectrumEngine eng;
    eng.start();
    QSignalSpy sc(&eng, &SpectrumEngine::sourceChanged);

    QVERIFY(eng.connectRtlTcp("127.0.0.1", port));
    QTRY_VERIFY_WITH_TIMEOUT(sc.count() >= 1 && sc.last().at(1).toBool(), 2000);

    server.stop();                            // drop
    QTRY_VERIFY_WITH_TIMEOUT(eng.isTestSignalActive(), 4000);

    QVERIFY(server.restart());                // device re-plugged
    // Auto-reconnect throttles at 2 s; recovery must happen on its own.
    QTRY_VERIFY_WITH_TIMEOUT(!eng.isTestSignalActive(), 8000);
    const int n = sc.count();
    QVERIFY(n >= 2);
    QVERIFY(sc.last().at(1).toBool());        // final state = connected

    eng.shutdown();
    eng.wait(2000);
}

void TestEngineHotplug::connectFailureReportsReason() {
    RtlTcpServer server;
    QVERIFY(server.listen());
    const quint16 port = server.port();
    server.stop();                            // port closed again

    SpectrumEngine eng;
    eng.start();
    QSignalSpy se(&eng, &SpectrumEngine::sourceError);

    QVERIFY(!eng.connectRtlTcp("127.0.0.1", port));
    QTRY_VERIFY_WITH_TIMEOUT(se.count() >= 1, 2000);
    const QString reason = se.first().at(0).toString();
    QVERIFY2(!reason.isEmpty(), "sourceError must carry a real reason");
    qInfo("connect failure reason: %s", qPrintable(reason));
    QVERIFY(eng.isTestSignalActive());

    eng.shutdown();
    eng.wait(2000);
}

QTEST_MAIN(TestEngineHotplug)
#include "test_engine_hotplug.moc"
