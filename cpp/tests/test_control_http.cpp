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
    // Phase51 block2: three-channel alignment gaps that were previously untested.
    void postCommandArgsMustBeObject();
    void optionsPreflightReturns204();
    void channelQueryPassthroughIsHonest();
    // Phase60+: armed VFO monitoring over HTTP POST /command -> same engine
    // state the Agent tool / ControlHub / UI checkbox drive.
    void postCommandSetVfoArmedLandsInEngine();
    // Read-only capability / recording-state snapshots reach the engine through
    // the generic POST /command -> ControlHub.execute() delegation (no per-route
    // handler needed) and return honest empty fields.
    void postCommandCapabilitiesAndRecordingStateRoute();
    // Noise blanker toggle + read-back ride the same generic POST /command ->
    // ControlHub.execute() delegation (HTTP needs no per-route handler).
    void postCommandNoiseBlankerRoute();
    // CTCSS set + read-back ride the same generic POST /command delegation.
    void postCommandCtcssRoute();
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

    // No fake-success payload leaked across the HTTP boundary: a gated write must
    // carry NONE of the fields a real tune() reports on success (frequency_hz /
    // command / clamped). The compact relayed body is exactly gatedResult().
    QVERIFY2(!o.contains("frequency_hz"), qPrintable(QString::fromUtf8(
        "gated tune must not leak frequency_hz: ") + QString::fromUtf8(r.body)));
    QVERIFY2(!o.contains("command"), qPrintable(QString::fromUtf8(
        "gated tune must not leak command: ") + QString::fromUtf8(r.body)));
    QVERIFY2(!o.contains("clamped"), qPrintable(QString::fromUtf8(
        "gated tune must not leak clamped: ") + QString::fromUtf8(r.body)));

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

// 7) POST /command: the "args" field, when present, MUST be a JSON object. A
//    string / array / number is an honest 400 (never silently coerced, never
//    silently ignored). This pins the same input contract the ControlHub layer
//    documents so HTTP and in-process clients cannot diverge.
void TestControlHttp::postCommandArgsMustBeObject() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);
    control::HttpControlServer srv(&hub);
    QVERIFY(srv.start(0));
    const quint16 port = srv.port();

    // args as a string -> 400.
    HttpResp r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"tune\",\"args\":\"98.5e6\"}"));
    QCOMPARE(r.status, 400);
    QVERIFY(!r.obj().value("ok").toBool());
    QVERIFY(r.obj().value("error").isString());

    // args as an array -> 400.
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"tune\",\"args\":[1,2,3]}"));
    QCOMPARE(r.status, 400);
    QVERIFY(!r.obj().value("ok").toBool());

    // args as a number -> 400.
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"tune\",\"args\":145000000}"));
    QCOMPARE(r.status, 400);
    QVERIFY(!r.obj().value("ok").toBool());

    // Omitting args entirely is ALLOWED (defaults to {}).
    r = httpPost(port, "/command", QByteArray("{\"tool\":\"get_status\"}"));
    QCOMPARE(r.status, 200);
    QVERIFY(r.obj().value("ok").toBool());
}

// 8) CORS preflight: a browser-based dev client sends OPTIONS first. It must get
//    a 204 No Content with NO body and NO engine touch (read-only, idempotent).
void TestControlHttp::optionsPreflightReturns204() {
    control::ControlHub hub;   // no engine
    control::HttpControlServer srv(&hub);
    QVERIFY(srv.start(0));
    const quint16 port = srv.port();

    HttpResp r = httpRequest(port, "OPTIONS", "/command");
    QCOMPARE(r.status, 204);
    QVERIFY(r.body.isEmpty());   // No Content: empty body
}

// 9) The ?channel=N query on the decoder snapshot endpoints must pass through to
//    the ControlHub read command as args.channel and MUST NOT crash / fabricate
//    when there is no data yet. A non-numeric channel is ignored (honest).
void TestControlHttp::channelQueryPassthroughIsHonest() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);
    control::HttpControlServer srv(&hub);
    QVERIFY(srv.start(0));
    const quint16 port = srv.port();

    // Numeric channel -> same honest empty shape (count:0 / empty array).
    HttpResp r = httpGet(port, "/pocsag_messages?channel=0");
    QCOMPARE(r.status, 200);
    QJsonObject o = r.obj();
    QVERIFY(o.value("ok").toBool());
    QCOMPARE(o.value("count").toInt(), 0);
    QVERIFY(o.value("messages").isArray());

    // Non-numeric channel is ignored (falls back to selected VFO) -- still honest,
    // never a fabricated channel id.
    r = httpGet(port, "/pocsag_messages?channel=abc");
    QCOMPARE(r.status, 200);
    QVERIFY(r.obj().value("ok").toBool());
    QCOMPARE(r.obj().value("count").toInt(), 0);

    // Same for m17 / vor with a numeric channel -- no crash, honest empty.
    r = httpGet(port, "/m17_calls?channel=1");
    QCOMPARE(r.status, 200);
    QVERIFY(r.obj().value("ok").toBool());
    r = httpGet(port, "/vor_radial?channel=1");
    QCOMPARE(r.status, 200);
    QVERIFY(!r.obj().value("locked").toBool());
}

// POST /command {"tool":"set_vfo_armed","args":{index,enabled}} -> the engine
// really flips the VFO's armed state; GET /status readback agrees (same state
// the Agent tool / ControlHub / UI checkbox drive).
void TestControlHttp::postCommandSetVfoArmedLandsInEngine() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);
    control::HttpControlServer srv(&hub);
    QVERIFY(srv.start(0));
    const quint16 port = srv.port();
    QVERIFY(port != 0);

    // Create a VFO over the same HTTP surface.
    HttpResp r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"vfo_add\",\"args\":{}}"));
    QCOMPARE(r.status, 200);
    QVERIFY(r.obj().value("ok").toBool());
    // Index 0 is the pre-existing default VFO (vfo_add appends and selects the
    // new one); take index 0's real id from list_vfos.
    r = httpPost(port, "/command", QByteArray("{\"tool\":\"list_vfos\",\"args\":{}}"));
    QVERIFY(r.obj().value("vfos").toArray().size() >= 1);
    const int id = r.obj().value("vfos").toArray().at(0).toObject().value("id").toInt();

    // Arm it via HTTP.
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"set_vfo_armed\",\"args\":{\"index\":0,\"enabled\":true}}"));
    QCOMPARE(r.status, 200);
    QJsonObject o = r.obj();
    QVERIFY2(o.value("ok").toBool(), o.value("error").toString().toUtf8().constData());
    QCOMPARE(o.value("vfo_id").toInt(), id);
    QCOMPARE(o.value("armed").toBool(), true);

    // Readback over HTTP: the same engine VFO reports armed=true.
    r = httpPost(port, "/command", QByteArray("{\"tool\":\"list_vfos\",\"args\":{}}"));
    QCOMPARE(r.status, 200);
    bool found = false;
    for (const auto& v : r.obj().value("vfos").toArray()) {
        QJsonObject vv = v.toObject();
        if (vv.value("id").toInt() == id) {
            found = true;
            QCOMPARE(vv.value("armed").toBool(), true);
        }
    }
    QVERIFY(found);

    // Bad args over HTTP are honest errors, not crashes.
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"set_vfo_armed\",\"args\":{\"index\":0,\"enabled\":\"yes\"}}"));
    QCOMPARE(r.status, 200);
    QVERIFY(!r.obj().value("ok").toBool());
}

// The two new read commands ride the SAME generic POST /command ->
// ControlHub.execute() delegation (HTTP needs no per-route handler). They reach
// the engine and return honest empty fields over the wire.
void TestControlHttp::postCommandCapabilitiesAndRecordingStateRoute() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);
    control::HttpControlServer srv(&hub);
    QVERIFY(srv.start(0));
    const quint16 port = srv.port();
    QVERIFY(port != 0);

    // get_capabilities over POST /command.
    HttpResp r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"get_capabilities\",\"args\":{}}"));
    QCOMPARE(r.status, 200);
    QJsonObject c = r.obj();
    QVERIFY2(c.value("ok").toBool(), c.value("error").toString().toUtf8().constData());
    QCOMPARE(c.value("command").toString(), QStringLiteral("get_capabilities"));
    QVERIFY(c.value("gains_db").isArray());
    QCOMPARE(c.value("gains_db").toArray().size(), 0);   // honest empty on test source
    QVERIFY(!c.value("connected").toBool());

    // get_recording_state over POST /command.
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"get_recording_state\",\"args\":{}}"));
    QCOMPARE(r.status, 200);
    QJsonObject rs = r.obj();
    QVERIFY2(rs.value("ok").toBool(), rs.value("error").toString().toUtf8().constData());
    QCOMPARE(rs.value("command").toString(), QStringLiteral("get_recording_state"));
    QVERIFY(!rs.value("recording").toBool());
    QVERIFY(rs.value("recording_path").toString().isEmpty());
    QVERIFY(rs.value("watch_enabled").isBool());
    QVERIFY(rs.value("recording_dir").isString());
}

// set_noise_blanker / get_noise_blanker_status reach the engine through the
// generic POST /command delegation: the write flips the real switch, the read
// returns it, and the write gate still blocks the write when closed.
void TestControlHttp::postCommandNoiseBlankerRoute() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);
    control::HttpControlServer srv(&hub);
    QVERIFY(srv.start(0));
    const quint16 port = srv.port();
    QVERIFY(port != 0);

    // Write the toggle over POST /command -> engine flips.
    HttpResp r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"set_noise_blanker\",\"args\":{\"on\":true}}"));
    QCOMPARE(r.status, 200);
    QJsonObject w = r.obj();
    QVERIFY2(w.value("ok").toBool(), w.value("error").toString().toUtf8().constData());
    QCOMPARE(w.value("command").toString(), QStringLiteral("set_noise_blanker"));
    QCOMPARE(w.value("enabled").toBool(), true);
    QCOMPARE(eng.noiseBlankerEnabled(), true);

    // Read it back over the wire.
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"get_noise_blanker_status\",\"args\":{}}"));
    QCOMPARE(r.status, 200);
    QJsonObject rd = r.obj();
    QVERIFY2(rd.value("ok").toBool(), rd.value("error").toString().toUtf8().constData());
    QCOMPARE(rd.value("command").toString(), QStringLiteral("get_noise_blanker_status"));
    QCOMPARE(rd.value("enabled").toBool(), true);

    // Gate closed: the same write POST is honestly refused, engine untouched.
    hub.setWriteEnabled(false);
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"set_noise_blanker\",\"args\":{\"on\":false}}"));
    QCOMPARE(r.status, 200);
    QJsonObject g = r.obj();
    QVERIFY(!g.value("ok").toBool());
    QVERIFY(g.value("gated").toBool());
    QCOMPARE(eng.noiseBlankerEnabled(), true);   // still true: gated write did not land
    hub.setWriteEnabled(true);
}

// CTCSS set + read-back ride the SAME generic POST /command -> ControlHub.execute()
// delegation (HTTP needs no per-route handler): the write flips the real engine
// switch + tuning, the read returns them over the wire, an out-of-domain tone is
// honestly rejected, and the write gate still blocks the write when closed.
void TestControlHttp::postCommandCtcssRoute() {
    SpectrumEngine eng;
    control::ControlHub hub;
    hub.setEngine(&eng);
    control::HttpControlServer srv(&hub);
    QVERIFY(srv.start(0));
    const quint16 port = srv.port();
    QVERIFY(port != 0);

    // Write enabled + a legal tone over POST /command -> engine lands.
    HttpResp r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"set_ctcss\",\"args\":{\"enabled\":true,\"frequency_hz\":100.0}}"));
    QCOMPARE(r.status, 200);
    QJsonObject w = r.obj();
    QVERIFY2(w.value("ok").toBool(), w.value("error").toString().toUtf8().constData());
    QCOMPARE(w.value("command").toString(), QStringLiteral("set_ctcss"));
    QCOMPARE(w.value("enabled").toBool(), true);
    QCOMPARE(w.value("frequency_hz").toDouble(), 100.0);
    QCOMPARE(eng.ctcssEnabled(), true);
    QCOMPARE(eng.ctcssFreqHz(), 100.0);

    // Read it back over the wire.
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"get_ctcss_status\",\"args\":{}}"));
    QCOMPARE(r.status, 200);
    QJsonObject rd = r.obj();
    QVERIFY2(rd.value("ok").toBool(), rd.value("error").toString().toUtf8().constData());
    QCOMPARE(rd.value("command").toString(), QStringLiteral("get_ctcss_status"));
    QCOMPARE(rd.value("enabled").toBool(), true);
    QCOMPARE(rd.value("frequency_hz").toDouble(), 100.0);
    QVERIFY(rd.value("active").isBool());   // honest detection latch, false w/o tone
    QCOMPARE(rd.value("gate_audio").toBool(), false);  // speaker gate defaults off

    // Optional speaker gate over the wire: arm it, engine lands, read back.
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"set_ctcss\",\"args\":{\"enabled\":true,\"gate_audio\":true}}"));
    QCOMPARE(r.status, 200);
    QVERIFY2(r.obj().value("ok").toBool(), r.obj().value("error").toString().toUtf8().constData());
    QVERIFY(eng.ctcssGateAudio());
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"get_ctcss_status\",\"args\":{}}"));
    QCOMPARE(r.obj().value("gate_audio").toBool(), true);

    // Out-of-domain tone over the wire is honestly rejected, engine untouched.
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"set_ctcss\",\"args\":{\"enabled\":true,\"frequency_hz\":300.0}}"));
    QCOMPARE(r.status, 200);
    QVERIFY(!r.obj().value("ok").toBool());
    QCOMPARE(eng.ctcssFreqHz(), 100.0);

    // Gate closed: the same write POST is honestly refused, engine untouched.
    hub.setWriteEnabled(false);
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"set_ctcss\",\"args\":{\"enabled\":false}}"));
    QCOMPARE(r.status, 200);
    QJsonObject g = r.obj();
    QVERIFY(!g.value("ok").toBool());
    QVERIFY(g.value("gated").toBool());
    QCOMPARE(eng.ctcssEnabled(), true);   // still true: gated write did not land
    hub.setWriteEnabled(true);

    // CDCSS/DCS over POST /command (透传, no new route): write lands + reads back.
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"set_cdcss\",\"args\":{\"enabled\":true,\"code\":\"023\"}}"));
    QCOMPARE(r.status, 200);
    QVERIFY2(r.obj().value("ok").toBool(), r.obj().value("error").toString().toUtf8().constData());
    QCOMPARE(eng.cdcssEnabled(), true);
    QCOMPARE(eng.cdcssCode(), 023);
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"get_cdcss_status\",\"args\":{}}"));
    QCOMPARE(r.status, 200);
    QJsonObject rd2 = r.obj();
    QVERIFY2(rd2.value("ok").toBool(), rd2.value("error").toString().toUtf8().constData());
    QCOMPARE(rd2.value("code").toString(), QStringLiteral("023"));
    QVERIFY(rd2.value("active").isBool());
    // Illegal DCS code over the wire is honestly rejected.
    r = httpPost(port, "/command",
        QByteArray("{\"tool\":\"set_cdcss\",\"args\":{\"enabled\":true,\"code\":\"777\"}}"));
    QCOMPARE(r.status, 200);
    QVERIFY(!r.obj().value("ok").toBool());
    QCOMPARE(eng.cdcssCode(), 023);
}

QTEST_MAIN(TestControlHttp)
#include "test_control_http.moc"
