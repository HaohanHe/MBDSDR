// SPDX-License-Identifier: MIT
//
// *** SYNTHETIC/OFFSCREEN TEST -- drives the PRODUCTION MainWindow wiring.
//     NOT REAL RECEPTION. Cloud VM has no RTL-SDR; the engine lands on the honest
//     empty NullSource. The control channel is exercised over a REAL loopback
//     TCP socket against the REAL HttpControlServer that MainWindow itself starts
//     in its constructor. ***
//
// Phase27 block1: end-to-end integration of the production HTTP control wiring.
// Unlike test_control_http (which builds the hub/server by hand), THIS test boots
// the real MainWindow offscreen and asserts that:
//
//   A. HAPPY PATH
//      1. MainWindow::setupControlHttpServer() actually binds a loopback listener
//         (env MBDSDR_CONTROL_HTTP_PORT -> startDefault()), and reports its real
//         port through the harness accessor; the top-bar chip reads "up".
//      2. GET /status over a real QTcpSocket returns the real get_status payload
//         (ok, one of the five states, frequency_hz/mode/bandwidth_hz present,
//         connected honestly false).
//      3. POST /command {"tool":"tune",...} lands in the REAL engine that
//         MainWindow owns -- the new frequency is readable back over GET /status
//         (single source of truth, production path).
//      4. The three decoder snapshot endpoints are honest empty (count:0 / [] /
//         locked:false) -- nothing fabricated.
//
//   B. FAILURE PATH (honest, non-fatal)
//      5. When the configured port is already occupied, a SECOND MainWindow fails
//         to bind but does NOT crash: harnessControlHttpListening()==false and
//         the chip honestly says "启动失败/端口被占". The rest of the window is
//         still alive.
//
// Port isolation: the happy path does NOT hard-code 50732. It probes a free
// loopback port with a throwaway QTcpServer, points MBDSDR_CONTROL_HTTP_PORT at
// it, then constructs MainWindow -- so this test never collides with a real
// desktop or a parallel run. The honest default-port banner text is asserted
// separately against HttpControlServer::warningBanner.
#include <QApplication>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QtTest>
#include <QTcpServer>
#include <QTcpSocket>
#include <QHostAddress>
#include <QTimer>
#include <QByteArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <functional>

#include "core/tokens.h"
#include "dsp/spectrum_engine.h"
#include "control/control_http_server.h"
#include "ui/main_window.h"

using namespace mbdsdr;

static QJsonObject parseObj(const QByteArray& s) {
    QJsonParseError pe{};
    QJsonDocument d = QJsonDocument::fromJson(s, &pe);
    if (pe.error != QJsonParseError::NoError || !d.isObject()) return QJsonObject();
    return d.object();
}

struct HttpResp {
    int         status = 0;
    QByteArray  body;
    QJsonObject obj() const { return parseObj(body); }
};

// Synchronous one-shot loopback request. The server lives on THIS (test/GUI)
// thread; spinning a local event loop both waits for the client reply AND
// services the server's accept/read/respond on the same thread.
static HttpResp httpRequest(quint16 port, const QString& method,
                            const QString& target, const QByteArray& body = {}) {
    HttpResp out;
    QTcpSocket sock;
    sock.connectToHost(QHostAddress(QHostAddress::LocalHost), port);
    if (!sock.waitForConnected(2000)) return out;

    QByteArray req = method.toUtf8() + " " + target.toUtf8() + " HTTP/1.1\r\n";
    req += "Host: 127.0.0.1\r\n";
    if (!body.isEmpty()) {
        req += "Content-Type: application/json\r\n";
        req += QByteArray("Content-Length: ") + QByteArray::number(body.size()) + "\r\n";
    }
    req += "Connection: close\r\n\r\n";
    if (!body.isEmpty()) req += body;
    sock.write(req);
    sock.flush();

    QEventLoop loop;
    QByteArray acc;
    QTimer guard;
    guard.setSingleShot(true);
    QObject::connect(&sock, &QTcpSocket::readyRead, &loop, [&] { acc += sock.readAll(); });
    QObject::connect(&sock, &QTcpSocket::readChannelFinished, &loop, &QEventLoop::quit);
    QObject::connect(&sock, &QTcpSocket::disconnected, &loop, &QEventLoop::quit);
    QObject::connect(&guard, &QTimer::timeout, &loop, &QEventLoop::quit);
    acc += sock.readAll();
    guard.start(3000);
    loop.exec();
    acc += sock.readAll();

    const int sp1 = acc.indexOf(' ');
    if (sp1 >= 0) {
        const int sp2 = acc.indexOf(' ', sp1 + 1);
        if (sp2 > sp1) out.status = acc.mid(sp1 + 1, sp2 - sp1 - 1).toInt();
    }
    const int hd = acc.indexOf("\r\n\r\n");
    out.body = hd < 0 ? QByteArray() : acc.mid(hd + 4);
    return out;
}

static HttpResp httpGet(quint16 port, const QString& target) {
    return httpRequest(port, "GET", target);
}
static HttpResp httpPost(quint16 port, const QString& target, const QByteArray& body) {
    return httpRequest(port, "POST", target, body);
}

// Bind to an ephemeral loopback port, return the port number, then CLOSE the
// probe so the caller can immediately bind it (single-threaded, no race).
static quint16 freeLoopbackPort() {
    QTcpServer probe;
    if (!probe.listen(QHostAddress(QHostAddress::LocalHost), 0)) return 0;
    const quint16 p = probe.serverPort();
    probe.close();
    return p;
}

static bool waitFor(const std::function<bool()>& pred, int timeoutMs) {
    QElapsedTimer t; t.start();
    while (t.elapsed() < timeoutMs) {
        if (pred()) return true;
        QCoreApplication::processEvents(QEventLoop::AllEvents, 60);
    }
    return pred();
}

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    // Synthetic-only fixture (red line: 合成仅测试夹具): give the production
    // engine a TestSignalSource instead of the tuning-no-op NullSource, so a
    // POST /command tune REALLY changes centerFreq() and can be read back over
    // /status. NullSource ignores setCenterFreq (honest empty state), which would
    // make the "engine really changed" assertion impossible. TestSignalSource is
    // still synthetic: it is NOT a real receiver, and connected stays honest
    // false (same contract test_control_http already exercises).
    qputenv("MBDSDR_TEST_SOURCE", "1");

    int failures = 0;
    auto check = [&](bool cond, const char* msg) {
        if (!cond) { ++failures; qWarning("FAIL: %s", msg); }
        else       { qInfo("ok: %s", msg); }
    };

    // ---- A. HAPPY PATH -----------------------------------------------------
    {
        const quint16 port = freeLoopbackPort();
        check(port != 0, "happy: probed a free loopback port");
        if (port == 0) return 1;
        qputenv(mbdsdr::tokens::kControlHttpPortEnvVar,
                QByteArray::number(port));   // startDefault() resolves this first

        MainWindow win;
        win.resize(1000, 700);
        win.show();

        // Let the engine's deferred startup sourceChanged land; the HTTP listen is
        // synchronous inside the ctor, but pump so no startup event is stuck.
        waitFor([&]() { return false; }, 300);

        // 1. Production wiring actually bound the loopback listener.
        check(win.harnessControlHttpListening(), "happy: control HTTP is listening (production ctor)");
        check(win.harnessControlHttpPort() == port,
              "happy: bound port matches the env-isolated port");
        QLabel* chip = win.harnessControlHttpBanner();
        check(chip != nullptr && !chip->text().isEmpty(), "happy: banner chip exists");
        check(chip && chip->text().contains(QStringLiteral("127.0.0.1")) &&
              !chip->text().contains(QStringLiteral("失败")),
              "happy: chip honestly reads 'up' (loopback, not failure)");

        // 2. GET /status over real loopback: real fields, honest empty connected.
        HttpResp s = httpGet(port, "/status");
        check(s.status == 200, "happy: GET /status -> HTTP 200");
        QJsonObject so = s.obj();
        check(so.value("ok").toBool(), "happy: /status ok:true");
        const QString st = so.value("status").toString();
        check(st == QLatin1String("no_telemetry") || st == QLatin1String("connected") ||
              st == QLatin1String("disconnected") || st == QLatin1String("dropped") ||
              st == QLatin1String("error"),
              "happy: /status.status is one of the five real states");
        check(so.contains("frequency_hz") && so.contains("mode") &&
              so.contains("bandwidth_hz"), "happy: /status exposes freq/mode/bandwidth");
        check(so.value("connected").isBool() && !so.value("connected").toBool(),
              "happy: /status connected honestly false (no hardware)");
        check(so.value("error_message").isString(), "happy: /status error_message is always a string");

        const double baseFreq = so.value("frequency_hz").toDouble();

        // 3. POST /command tune lands in the REAL engine MainWindow owns.
        HttpResp r = httpPost(port, "/command",
                              QByteArray("{\"tool\":\"tune\",\"args\":{\"freq_hz\":145.0e6}}"));
        check(r.status == 200, "happy: POST /command tune -> HTTP 200");
        QJsonObject ro = r.obj();
        check(ro.value("ok").toBool(), "happy: tune ok:true (write gate open by default)");
        check(ro.value("frequency_hz").toDouble() == 145.0e6,
              "happy: tune echoes the effective frequency");

        // Read the new centre back over the SAME production channel.
        QJsonObject after = httpGet(port, "/status").obj();
        check(after.value("frequency_hz").toDouble() == 145.0e6,
              "happy: engine REALLY changed -- /status reads 145 MHz back");
        check(after.value("frequency_hz").toDouble() != baseFreq || baseFreq == 145.0e6,
              "happy: frequency actually moved from baseline");

        // 4. Three decoder snapshots are honest empty over production HTTP.
        QJsonObject p = httpGet(port, "/pocsag_messages").obj();
        check(p.value("ok").toBool() && p.value("count").toInt() == 0 &&
              p.value("messages").isArray() && p.value("messages").toArray().isEmpty(),
              "happy: /pocsag_messages honest empty (count:0, [])");
        QJsonObject m = httpGet(port, "/m17_calls").obj();
        check(m.value("ok").toBool() && m.value("count").toInt() == 0 &&
              m.value("calls").isArray() && m.value("calls").toArray().isEmpty(),
              "happy: /m17_calls honest empty (count:0, [])");
        QJsonObject v = httpGet(port, "/vor_radial").obj();
        check(v.value("ok").toBool() && !v.value("locked").toBool(),
              "happy: /vor_radial honest empty (locked:false, no fabricated bearing)");

        // Discovery doc is reachable and honest.
        HttpResp d = httpGet(port, "/");
        check(d.status == 200 && d.obj().value("endpoints").isArray(),
              "happy: GET / discovery doc lists endpoints");

        // ~win stops the listener gracefully before engine teardown.
    }

    // ---- B. FAILURE PATH (honest, non-fatal) -------------------------------
    {
        // Occupy a loopback port, then point the env var at it. A fresh MainWindow
        // must fail to bind there -- and must NOT crash.
        QTcpServer holder;
        const bool held = holder.listen(QHostAddress(QHostAddress::LocalHost), 0);
        check(held, "failure: reserved a port to force a bind conflict");
        if (!held) return failures ? 1 : 0;
        const quint16 occupied = holder.serverPort();
        qputenv(mbdsdr::tokens::kControlHttpPortEnvVar,
                QByteArray::number(occupied));

        MainWindow win2;
        win2.resize(1000, 700);
        win2.show();
        waitFor([&]() { return false; }, 200);

        check(!win2.harnessControlHttpListening(),
              "failure: second window does NOT listen when the port is occupied");
        check(win2.harnessControlHttpPort() == 0,
              "failure: reported port is 0 (honest -- not listening)");
        QLabel* chip2 = win2.harnessControlHttpBanner();
        check(chip2 != nullptr &&
              chip2->text().contains(QStringLiteral("失败")),
              "failure: banner chip honestly says bind failure");
        check(chip2 && chip2->text().contains(QStringLiteral("其余功能正常")),
              "failure: banner honestly says the rest of the app still works");
        // The window is still alive (engine behind it) -- prove it without touching
        // the (down) control channel: engine() exists.
        check(win2.engine() != nullptr, "failure: window still owns a live engine");

        holder.close();
    }

    // Honest default-port safety banner (the production default, not the env test
    // port) -- loopback-only + no-auth notice the UI surfaces on a normal start.
    const QString banner = control::HttpControlServer::warningBanner(tokens::kControlHttpDefaultPort);
    check(banner.contains(QStringLiteral("127.0.0.1")) &&
          banner.contains(QString::number(tokens::kControlHttpDefaultPort)),
          "default: warningBanner names loopback + the well-known default port");

    qInfo(failures == 0 ? "ALL PASS" : "FAILURES PRESENT");
    return failures ? 1 : 0;
}
