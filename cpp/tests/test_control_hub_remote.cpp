// SPDX-License-Identifier: MIT
//
// *** REMOTE / THREADING HARDENING TESTS -- synthetic + real TCP loopback, NOT
// *** HARDWARE. No QWidget. Deterministic offscreen.
//
// Phase22-A: ControlHub is the GUI-decoupled headless control layer, and the new
// failure mode is a REMOTE/network thread calling execute(). These cases prove:
//
//   1. CROSS-THREAD SAFETY. Several worker threads hammer execute() concurrently
//      (writes AND reads). execute() must marshal the engine touch onto the
//      engine's home thread (BlockingQueued) instead of calling engine slots on
//      the caller's thread. Result: no crash, no data race, every call returns a
//      well-formed JSON object. (Synthetic test source; the run loop is NOT
//      started so the only concurrency being exercised is the marshal itself.)
//
//   2. get_status HONEST ON A REAL (loopback) LINK. Using the same inert
//      RtlTcpServer fixture as test_engine_hotplug, we drive the three liveness
//      scenarios -- connect, device pulled out (drop), device re-plugged
//      (auto-reconnect) -- plus a connect FAILURE, and assert that the
//      connected / status / error_message fields flip correctly and are never
//      stale. MBDSDR_TEST_SOURCE=1 makes the offline fallback deterministic.
#include <QtTest/QtTest>
#include <QJsonDocument>
#include <QJsonObject>
#include <QCoreApplication>
#include <QEventLoop>
#include <atomic>
#include <thread>
#include <vector>

#include <QTcpServer>
#include <QTcpSocket>
#include <QTimer>
#include <QHostAddress>
#include <QByteArray>

#include "dsp/spectrum_engine.h"
#include "control/control_hub.h"

using namespace mbdsdr;
using namespace mbdsdr::dsp;

static QJsonObject parseObj(const QString& s) {
    QJsonParseError pe{};
    QJsonDocument d = QJsonDocument::fromJson(s.toUtf8(), &pe);
    if (pe.error != QJsonParseError::NoError || !d.isObject()) return QJsonObject();
    return d.object();
}

// Inert rtl_tcp loopback source (mirrors test_engine_hotplug): accepts one
// client, reads the two 5-byte setup commands, then streams zero-padded IQ.
// The engine only needs the byte stream; the payload is deliberately inert.
class RtlTcpServer : public QObject {
    Q_OBJECT
public:
    explicit RtlTcpServer(QObject* parent = nullptr) : QObject(parent) {
        streamTimer_.setInterval(25);
        connect(&streamTimer_, &QTimer::timeout, this, &RtlTcpServer::emitPacket);
    }
    bool listen() {
        if (!server_.listen(QHostAddress::LocalHost, 0)) return false;
        lastPort_ = server_.serverPort();
        connect(&server_, &QTcpServer::newConnection, this, &RtlTcpServer::accept);
        return true;
    }
    quint16 port() const { return lastPort_; }
    bool hasClient() const { return sock_ != nullptr; }
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
        if (!s) return;
        if (sock_) { sock_->disconnectFromHost(); sock_->deleteLater(); }
        sock_ = s;
        connect(sock_, &QTcpSocket::readyRead, this, &RtlTcpServer::readSetup);
    }
    void readSetup() {
        if (!sock_) return;
        cmdBuf_ += sock_->readAll();
        if (cmdBuf_.size() >= 10) {
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
    quint16 lastPort_ = 0;
    QByteArray cmdBuf_;
    QTimer streamTimer_;
    QByteArray streamChunk_{120000, static_cast<char>(0x80)};
};

class TestControlHubRemote : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void multithreadedWritesAreThreadSafe();
    void statusHonestAcrossDropAndReconnect();
    void connectFailureReportsRealError();
};

void TestControlHubRemote::initTestCase() {
    qputenv("MBDSDR_TEST_SOURCE", "1");   // explicit deterministic offline fallback
}

// 1) Several foreign threads hammer execute() concurrently. The engine's home
//    thread here IS the test thread (it created the engine + hub), so every call
//    from a std::thread worker takes the BlockingQueued marshal path. The home
//    thread pumps its event loop to service those queued calls; if execute()
//    instead called engine slots directly on the worker threads, the non-locked
//    setters (squelch/mute/watch) would race. We assert every result is
//    well-formed and the hub stays functional afterwards.
void TestControlHubRemote::multithreadedWritesAreThreadSafe() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);

    constexpr int kThreads = 4;
    constexpr int kPerThread = 400;
    std::atomic<int> done{0};

    auto worker = [&hub, &done]() {
        for (int i = 0; i < kPerThread; ++i) {
            // Mix reads and writes across several handlers.
            QJsonObject r1 = parseObj(hub.execute("tune", {{"freq_hz", 100.0e6 + (i % 50) * 1.0e6}}));
            QJsonObject r2 = parseObj(hub.execute("get_status", {}));
            QJsonObject r3 = parseObj(hub.execute("set_gain", {{"gain_db", static_cast<double>(i % 40)}}));
            QJsonObject r4 = parseObj(hub.execute("set_squelch_threshold", {{"threshold_db", -60.0}}));
            // Every call must return a well-formed object (no empty parse = no crash
            // on the wire). `ok` may be true/gated/error but the JSON must exist.
            if (r1.isEmpty() || r2.isEmpty() || r3.isEmpty() || r4.isEmpty())
                return;   // surfaces as a short `done` count below
        }
        done.fetch_add(1);
    };

    std::vector<std::thread> pool;
    pool.reserve(kThreads);
    for (int t = 0; t < kThreads; ++t) pool.emplace_back(worker);

    // The workers BlockingQueue onto THIS (home) thread, so we MUST pump the
    // event loop while they run -- joining them directly here would deadlock.
    while (done.load() < kThreads)
        QCoreApplication::processEvents(QEventLoop::AllEvents, 50);
    for (auto& th : pool) th.join();

    QCOMPARE(done.load(), kThreads);

    // The hub still works correctly afterwards (the last tune landed on home).
    QJsonObject st = parseObj(hub.execute("get_status", {}));
    QVERIFY2(st.value("ok").toBool(), st.value("error").toString().toUtf8().constData());
    QVERIFY(st.value("status").isString());
}

// 2) connect -> drop -> auto-reconnect over a real TCP loopback. Assert the
//    get_status fields flip honestly and never go stale.
void TestControlHubRemote::statusHonestAcrossDropAndReconnect() {
    RtlTcpServer server;
    QVERIFY(server.listen());
    const quint16 port = server.port();

    SpectrumEngine eng;
    eng.start();
    control::ControlHub hub;
    hub.setEngine(&eng);

    auto status = [&]() { return parseObj(hub.execute("get_status", {})); };

    // --- Connect a real loopback source: must reach connected=true.
    QVERIFY(eng.connectRtlTcp("127.0.0.1", port));
    QTRY_VERIFY_WITH_TIMEOUT(server.hasClient(), 2000);
    QSignalSpy frames(&eng, &SpectrumEngine::spectrumReady);
    QTRY_VERIFY_WITH_TIMEOUT(frames.count() >= 3, 3000);
    // ControlHub sees the ~1 Hz telemetry as a queued update on this thread.
    QTRY_VERIFY_WITH_TIMEOUT(status().value("connected").toBool(), 4000);
    QCOMPARE(status().value("status").toString(), QStringLiteral("connected"));
    QVERIFY(status().value("error_message").toString().isEmpty());

    // --- Device pulled out: connected must flip FALSE (not stale-true) and the
    //     status must reflect a live-device drop.
    server.stop();
    QTRY_VERIFY_WITH_TIMEOUT(!status().value("connected").toBool(), 8000);
    QCOMPARE(status().value("status").toString(), QStringLiteral("dropped"));

    // --- Device re-plugged on the same port: the 2 s auto-reconnect recovers and
    //     connected goes back true, status back to connected, any error cleared.
    QVERIFY(server.restart());
    QTRY_VERIFY_WITH_TIMEOUT(status().value("connected").toBool(), 12000);
    QCOMPARE(status().value("status").toString(), QStringLiteral("connected"));
    QVERIFY(status().value("error_message").toString().isEmpty());

    eng.shutdown();
    eng.wait(2000);
}

// 3) Connect to a closed port: sourceError carries a REAL reason and get_status
//    reports status=error with that reason (never an empty/fabricated cause).
void TestControlHubRemote::connectFailureReportsRealError() {
    RtlTcpServer server;
    QVERIFY(server.listen());
    const quint16 port = server.port();
    server.stop();   // close it immediately -> connecting here fails

    SpectrumEngine eng;
    eng.start();
    control::ControlHub hub;
    hub.setEngine(&eng);

    // Wait until the idle source has produced at least one telemetry tick so the
    // empty/error distinction is meaningful (not the no-telemetry empty state).
    QTRY_VERIFY_WITH_TIMEOUT(hub.telemetry().available, 3000);

    QVERIFY(!eng.connectRtlTcp("127.0.0.1", port));
    // sourceError -> onSourceError (direct, same home thread). Poll through the
    // snapshot + the get_status view for honesty.
    QTRY_VERIFY_WITH_TIMEOUT(!hub.telemetry().lastError.isEmpty(), 3000);

    QJsonObject st = parseObj(hub.execute("get_status", {}));
    QVERIFY(st.value("ok").toBool());
    QVERIFY(!st.value("connected").toBool());
    QCOMPARE(st.value("status").toString(), QStringLiteral("error"));
    QVERIFY2(!st.value("error_message").toString().isEmpty(),
             "error_message must carry the real connect-failure reason");

    eng.shutdown();
    eng.wait(2000);
}

QTEST_MAIN(TestControlHubRemote)
#include "test_control_hub_remote.moc"
