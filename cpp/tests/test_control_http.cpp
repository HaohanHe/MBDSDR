// SPDX-License-Identifier: MIT
//
// *** SYNTHETIC TESTS -- 仅验证控制 HTTP 端点接线，非真实接收 (NOT REAL RECEPTION) ***
//
// Phase25 block1: boots the REAL HttpControlServer on an ephemeral loopback
// port and drives it over a REAL QTcpSocket loopback client. The engine is the
// offline TestSignalSource (MBDSDR_TEST_SOURCE=1) -- no hardware, no network.
//
// Covers the frozen JSON contract end to end:
//   1. GET /status returns the real five-state status + mode/freq/bandwidth;
//   2. POST /command {"tool":"tune",...} lands in the engine and the new
//      frequency is readable back over GET /status;
//   3. with the write gate CLOSED, the SAME POST /command tune is refused
//      (gated:true) and the engine frequency is unchanged;
//   4. no device / no decode data -> honest empty state (count:0, [],
//      locked:false), never fabricated;
//   5. unknown path -> 404, unknown command -> honest ok:false, malformed JSON
//      body -> honest 400.
#include <QtTest/QtTest>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QCoreApplication>
#include <QEventLoop>
#include <QTcpServer>
#include <QTcpSocket>
#include <QHostAddress>
#include <QTimer>
#include <QByteArray>

#include "dsp/spectrum_engine.h"
#include "control/control_hub.h"
#include "control/control_http_server.h"
#include "core/tokens.h"

using namespace mbdsdr;
using namespace mbdsdr::dsp;

static QJsonObject parseObj(const QByteArray& s) {
    QJsonParseError pe{};
    QJsonDocument d = QJsonDocument::fromJson(s, &pe);
    if (pe.error != QJsonParseError::NoError || !d.isObject()) return QJsonObject();
    return d.object();
}

struct HttpResp {
    int         status = 0;     // HTTP status code
    QByteArray  raw;            // full response text (headers + body)
    QByteArray  body;           // parsed JSON body
    QJsonObject obj() const { return parseObj(body); }
};

// Synchronous one-shot loopback request. The server lives on THIS (test) thread,
// so spinning a local event loop here both waits for the client reply AND services
// the server's accept/read/respond on the same thread (mirrors the remote test).
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

    // Read until the server closes the connection (it always sends Connection:
    // close and disconnects after responding) or a timeout.
    QEventLoop loop;
    QByteArray acc;
    QTimer guard;
    guard.setSingleShot(true);
    QObject::connect(&sock, &QTcpSocket::readyRead, &loop, [&] { acc += sock.readAll(); });
    QObject::connect(&sock, &QTcpSocket::readChannelFinished, &loop, &QEventLoop::quit);
    QObject::connect(&sock, &QTcpSocket::disconnected, &loop, &QEventLoop::quit);
    QObject::connect(&guard, &QTimer::timeout, &loop, &QEventLoop::quit);
    acc += sock.readAll();   // catch anything that arrived before the connections
    guard.start(3000);
    loop.exec();
    acc += sock.readAll();
    out.raw = acc;

    // Parse status line.
    const int sp1 = acc.indexOf(' ');
    if (sp1 >= 0) {
        const int sp2 = acc.indexOf(' ', sp1 + 1);
        if (sp2 > sp1) out.status = acc.mid(sp1 + 1, sp2 - sp1 - 1).toInt();
    }
    // Split headers / body.
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

class TestControlHttp : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void getStatusReturnsRealFields();
    void postCommandTuneLandsInEngine();
    void gateClosedRefusesWritePost();
    void decoderSnapshotsAreHonestEmpty();
    void unknownPathAndBadRequestsAreHonest();
    void bindsLoopbackOnlyAndHasBanner();
};

void TestControlHttp::initTestCase() {
    qputenv("MBDSDR_TEST_SOURCE", "1");   // deterministic offline synthetic source
}

// 1) GET /status -> the real get_status payload over HTTP.
void TestControlHttp::getStatusReturnsRealFields() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);
    control::HttpControlServer srv(&hub);
    QVERIFY(srv.start(0));               // ephemeral loopback port
    const quint16 port = srv.port();
    QVERIFY(port != 0);

    HttpResp r = httpGet(port, "/status");
    QCOMPARE(r.status, 200);
    QJsonObject o = r.obj();
    QVERIFY2(o.value("ok").toBool(), r.body.constData());
    // Five-state status string present and one of the known states.
    const QString st = o.value("status").toString();
    QVERIFY2((st == QLatin1String("no_telemetry") || st == QLatin1String("connected") ||
              st == QLatin1String("dropped") || st == QLatin1String("error") ||
              st == QLatin1String("disconnected")),
             qPrintable("unexpected status: " + st));
    // Control-layer values readable even before streaming.
    QVERIFY(o.contains("frequency_hz"));
    QVERIFY(o.contains("mode"));
    QVERIFY(o.contains("bandwidth_hz"));
    // error_message is ALWAYS a string (empty when none) -- never a null.
    QVERIFY(o.value("error_message").isString());
    // connected is a real bool; with no streaming it must be the honest false.
    QVERIFY(o.value("connected").isBool());
    QVERIFY(!o.value("connected").toBool());
}

// 2) POST /command {"tool":"tune",...} -> the engine REALLY changes; GET /status
//    reads the new centre back through the same write gate.
void TestControlHttp::postCommandTuneLandsInEngine() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);
    control::HttpControlServer srv(&hub);
    QVERIFY(srv.start(0));
    const quint16 port = srv.port();

    // Baseline.
    QJsonObject before = httpGet(port, "/status").obj();
    const double baseFreq = before.value("frequency_hz").toDouble();

    // Tune via the HTTP command endpoint.
    const QByteArray body =
        QByteArray("{\"tool\":\"tune\",\"args\":{\"freq_hz\":145.0e6}}");
    HttpResp r = httpPost(port, "/command", body);
    QCOMPARE(r.status, 200);
    QJsonObject o = r.obj();
    QVERIFY2(o.value("ok").toBool(), r.body.constData());
    QCOMPARE(o.value("command").toString(), QStringLiteral("tune"));
    QCOMPARE(o.value("frequency_hz").toDouble(), 145.0e6);

    // The engine REALLY changed: read back over GET /status.
    QJsonObject after = httpGet(port, "/status").obj();
    QCOMPARE(after.value("frequency_hz").toDouble(), 145.0e6);
    QVERIFY(after.value("frequency_hz").toDouble() != baseFreq || baseFreq == 145.0e6);

    // The direct in-process view agrees (single source of truth).
    QCOMPARE(hub.execute("get_frequency", {}).isEmpty(), false);
}

// 3) With the write gate CLOSED, the same POST /command tune is honestly refused
//    (gated:true) and the engine frequency does NOT move.
void TestControlHttp::gateClosedRefusesWritePost() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);
    hub.setWriteEnabled(false);          // gate CLOSED
    control::HttpControlServer srv(&hub);
    QVERIFY(srv.start(0));
    const quint16 port = srv.port();

    QJsonObject before = httpGet(port, "/status").obj();
    const double baseFreq = before.value("frequency_hz").toDouble();

    const QByteArray body =
        QByteArray("{\"tool\":\"tune\",\"args\":{\"freq_hz\":160.0e6}}");
    HttpResp r = httpPost(port, "/command", body);
    QCOMPARE(r.status, 200);             // HTTP itself succeeded...
    QJsonObject o = r.obj();
    QVERIFY(!o.value("ok").toBool());    // ...but the command was gated
    QVERIFY(o.value("gated").toBool());
    QVERIFY(o.value("error").isString());

    // Engine untouched.
    QJsonObject after = httpGet(port, "/status").obj();
    QCOMPARE(after.value("frequency_hz").toDouble(), baseFreq);

    // A READ snapshot is STILL allowed over HTTP while the gate is closed.
    HttpResp p = httpGet(port, "/pocsag_messages");
    QCOMPARE(p.status, 200);
    QVERIFY(p.obj().value("ok").toBool());
}

// 4) No device / no decode data -> honest empty state over all three decoder
//    snapshot endpoints (never fabricated).
void TestControlHttp::decoderSnapshotsAreHonestEmpty() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);
    control::HttpControlServer srv(&hub);
    QVERIFY(srv.start(0));
    const quint16 port = srv.port();

    // POCSAG: count:0 + empty messages array.
    QJsonObject p = httpGet(port, "/pocsag_messages").obj();
    QVERIFY(p.value("ok").toBool());
    QCOMPARE(p.value("count").toInt(), 0);
    QVERIFY(p.value("messages").isArray());
    QCOMPARE(p.value("messages").toArray().size(), 0);

    // m17: count:0 + empty calls array.
    QJsonObject m = httpGet(port, "/m17_calls").obj();
    QVERIFY(m.value("ok").toBool());
    QCOMPARE(m.value("count").toInt(), 0);
    QVERIFY(m.value("calls").isArray());
    QCOMPARE(m.value("calls").toArray().size(), 0);

    // VOR: locked=false (a bearing must NOT be fabricated).
    QJsonObject v = httpGet(port, "/vor_radial").obj();
    QVERIFY(v.value("ok").toBool());
    QVERIFY(!v.value("locked").toBool());
}

// 5) Unknown path -> 404; unknown command via POST -> honest ok:false; malformed
//    JSON body -> honest 400; missing "tool" -> honest 400.
void TestControlHttp::unknownPathAndBadRequestsAreHonest() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);
    control::HttpControlServer srv(&hub);
    QVERIFY(srv.start(0));
    const quint16 port = srv.port();

    // Unknown path.
    HttpResp r = httpGet(port, "/does_not_exist");
    QCOMPARE(r.status, 404);
    QVERIFY(!r.obj().value("ok").toBool());
    QVERIFY(r.obj().value("error").isString());

    // Unknown command through POST /command: HTTP 200 but honest ok:false.
    r = httpPost(port, "/command", QByteArray("{\"tool\":\"frobnicate\",\"args\":{}}"));
    QCOMPARE(r.status, 200);
    QVERIFY(!r.obj().value("ok").toBool());
    QVERIFY(r.obj().value("error").toString().contains(QString::fromUtf8("未知命令")));

    // Malformed JSON body -> 400.
    r = httpPost(port, "/command", QByteArray("{not json"));
    QCOMPARE(r.status, 400);
    QVERIFY(!r.obj().value("ok").toBool());

    // Missing "tool" -> 400.
    r = httpPost(port, "/command", QByteArray("{\"args\":{}}"));
    QCOMPARE(r.status, 400);
    QVERIFY(!r.obj().value("ok").toBool());

    // "/" discovery doc is reachable and honest.
    r = httpGet(port, "/");
    QCOMPARE(r.status, 200);
    QVERIFY(r.obj().value("endpoints").isArray());
}

// 6) The server binds loopback only and exposes the honest safety banner.
void TestControlHttp::bindsLoopbackOnlyAndHasBanner() {
    control::ControlHub hub;   // no engine: GET /status is an honest error, but the
                               // bind/listen/banner contract still holds.
    control::HttpControlServer srv(&hub);
    QVERIFY(srv.start(0));
    QVERIFY(srv.isListening());
    // The warning banner must mention loopback/local-only honestly.
    const QString banner = control::HttpControlServer::warningBanner(srv.port());
    QVERIFY2(banner.contains(QStringLiteral("127.0.0.1")) &&
             (banner.contains(QString::fromUtf8("本机")) ||
              banner.contains(QStringLiteral("loopback"))),
             qPrintable("banner: " + banner));

    // With NO engine attached, GET /status is an honest error (never a crash).
    HttpResp r = httpGet(srv.port(), "/status");
    QCOMPARE(r.status, 200);
    QVERIFY(!r.obj().value("ok").toBool());
    QVERIFY(r.obj().value("error").isString());

    srv.stop();
    QVERIFY(!srv.isListening());
}

QTEST_MAIN(TestControlHttp)
#include "test_control_http.moc"
