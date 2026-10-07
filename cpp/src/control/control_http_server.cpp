// SPDX-License-Identifier: MIT
#include "control/control_http_server.h"
#include "control/control_hub.h"
#include "core/tokens.h"

#include <QTcpServer>
#include <QTcpSocket>
#include <QHostAddress>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QSettings>
#include <QByteArray>
#include <QStringList>
#include <QUrl>

#include <cstdio>

namespace mbdsdr {
namespace control {

namespace {

// Compact JSON string -> a HTTP JSON error body with the given message. Keeps the
// same {ok:false,error:...} shape as ControlHub so a client never has to learn a
// second error vocabulary.
QJsonObject errBody(const QString& error) {
    QJsonObject o;
    o[QStringLiteral("ok")] = false;
    o[QStringLiteral("error")] = error;
    return o;
}

QByteArray toCompact(const QJsonObject& o) {
    return QJsonDocument(o).toJson(QJsonDocument::Compact);
}

// Parse a "key=value" query string (after '?') into a JSON object. Only a small
// flat set of params is understood; unknown keys are ignored honestly. Currently
// just "channel" (the optional VFO channel id for the decoder snapshot reads).
QJsonObject queryToArgs(const QString& query) {
    QJsonObject args;
    if (query.isEmpty()) return args;
    for (const QString& pair : query.split(QLatin1Char('&'))) {
        const int eq = pair.indexOf(QLatin1Char('='));
        const QString key = (eq < 0 ? pair : pair.left(eq));
        const QString val = (eq < 0 ? QString() : pair.mid(eq + 1));
        if (key == QLatin1String("channel")) {
            bool ok = false;
            const int ch = val.toInt(&ok);
            if (ok) args[QStringLiteral("channel")] = ch;
            // a non-numeric channel is ignored (the snapshot then uses the
            // selected VFO) rather than fabricating one.
        }
    }
    return args;
}

} // namespace

HttpControlServer::HttpControlServer(ControlHub* hub, QObject* parent)
    : QObject(parent), hub_(hub) {
}

HttpControlServer::~HttpControlServer() {
    stop();
}

QString HttpControlServer::warningBanner(quint16 port) {
    return QString::fromUtf8(
        "MBDSDR 控制 HTTP 端点仅绑定本机回环 127.0.0.1:%1（仅限本进程/本机访问）。"
        "该端点无鉴权，请勿将端口暴露到局域网或公网；如需远程访问请自行建立隧道。")
        .arg(port);
}

bool HttpControlServer::start(quint16 port) {
    if (isListening()) return true;
    server_ = new QTcpServer(this);
    // Loopback ONLY: deliberately LocalHost, never Any/AnyIPv4.
    if (!server_->listen(QHostAddress(QHostAddress::LocalHost), port)) {
        delete server_;
        server_ = nullptr;
        return false;
    }
    boundPort_ = server_->serverPort();
    connect(server_, &QTcpServer::newConnection,
            this, &HttpControlServer::onNewConnection);
    qWarning().noquote() << warningBanner(boundPort_);
    return true;
}

bool HttpControlServer::startDefault() {
    quint16 port = tokens::kControlHttpDefaultPort;
    // 1) env override (highest precedence; used by tests / ops).
    const QByteArray env = qgetenv(tokens::kControlHttpPortEnvVar);
    if (!env.isEmpty()) {
        bool ok = false;
        const int p = env.toInt(&ok);
        if (ok && p > 0 && p < 65536) port = static_cast<quint16>(p);
    } else {
        // 2) QSettings override.
        QSettings s;
        const QVariant v = s.value(QString::fromUtf8(tokens::kControlHttpPortSettingsKey));
        if (v.isValid()) {
            bool ok = false;
            const int p = v.toInt(&ok);
            if (ok && p > 0 && p < 65536) port = static_cast<quint16>(p);
        }
    }
    return start(port);
}

void HttpControlServer::stop() {
    if (server_) {
        server_->close();
        server_->deleteLater();
        server_ = nullptr;
    }
    boundPort_ = 0;
    // Drop any in-flight sockets (they are children of this via connection).
    for (QTcpSocket* sock : buffers_.keys()) {
        if (sock) {
            sock->disconnectFromHost();
            sock->deleteLater();
        }
    }
    buffers_.clear();
}

quint16 HttpControlServer::port() const { return boundPort_; }
bool HttpControlServer::isListening() const {
    return server_ && server_->isListening();
}

void HttpControlServer::onNewConnection() {
    while (server_ && server_->hasPendingConnections()) {
        QTcpSocket* sock = server_->nextPendingConnection();
        if (!sock) continue;
        buffers_.insert(sock, QByteArray());
        connect(sock, &QTcpSocket::readyRead,
                this, &HttpControlServer::onReadyRead);
        connect(sock, &QTcpSocket::disconnected,
                this, &HttpControlServer::onDisconnected);
    }
}

void HttpControlServer::onReadyRead() {
    QTcpSocket* sock = qobject_cast<QTcpSocket*>(sender());
    if (!sock) return;
    if (!buffers_.contains(sock)) buffers_.insert(sock, QByteArray());
    buffers_[sock] += sock->readAll();
    tryHandle(sock);
}

void HttpControlServer::onDisconnected() {
    QTcpSocket* sock = qobject_cast<QTcpSocket*>(sender());
    if (!sock) return;
    buffers_.remove(sock);
    sock->deleteLater();
}

void HttpControlServer::tryHandle(QTcpSocket* sock) {
    QByteArray& buf = buffers_[sock];

    // Locate the end of the header block.
    const int headerEnd = buf.indexOf("\r\n\r\n");
    if (headerEnd < 0) return;   // headers not complete yet

    const QByteArray headerBlock = buf.left(headerEnd);
    const QByteArray rest = buf.mid(headerEnd + 4);

    Request req;
    const QList<QByteArray> lines = headerBlock.split('\r');
    if (lines.isEmpty()) return;

    // Request line: "METHOD PATH HTTP/1.1"
    const QByteArray reqLine = lines.first().trimmed();
    const QList<QByteArray> parts = reqLine.split(' ');
    if (parts.size() < 3) {
        // Malformed request line: answer once and close.
        int st = 400;
        const QByteArray body = toCompact(errBody(
            QString::fromUtf8("无法解析的请求行: %1").arg(QString::fromUtf8(reqLine))));
        sock->write("HTTP/1.1 400 Bad Request\r\n");
        sock->write("Content-Type: application/json; charset=utf-8\r\n");
        sock->write(QByteArray("Content-Length: ") + QByteArray::number(body.size()) + "\r\n");
        sock->write("Access-Control-Allow-Origin: *\r\n");
        sock->write("Connection: close\r\n\r\n");
        sock->write(body);
        emit requestHandled(QStringLiteral("?"), QStringLiteral("?"), st);
        sock->disconnectFromHost();
        return;
    }
    req.method = QString::fromUtf8(parts[0]);
    req.rawTarget = QString::fromUtf8(parts[1]);

    // Split path and query.
    const int q = req.rawTarget.indexOf(QLatin1Char('?'));
    if (q < 0) req.path = req.rawTarget;
    else { req.path = req.rawTarget.left(q); }

    // Content-Length header.
    for (int i = 1; i < lines.size(); ++i) {
        const QByteArray& line = lines[i];
        const int colon = line.indexOf(':');
        if (colon < 0) continue;
        const QByteArray name = line.left(colon).trimmed().toLower();
        const QByteArray value = line.mid(colon + 1).trimmed();
        if (name == "content-length") req.contentLength = value.toLongLong();
    }

    // Need the full body before routing.
    if (rest.size() < req.contentLength) return;   // wait for more
    req.body = rest.left(static_cast<int>(req.contentLength));

    const int status = [&]() {
        int st = 200;
        const QByteArray body = route(req, st);
        sock->write(QByteArray("HTTP/1.1 ") + QByteArray::number(st) + " " +
                    (st == 200 ? "OK" : (st == 204 ? "No Content" :
                     (st == 404 ? "Not Found" : "Bad Request"))) + "\r\n");
        sock->write("Content-Type: application/json; charset=utf-8\r\n");
        sock->write(QByteArray("Content-Length: ") + QByteArray::number(body.size()) + "\r\n");
        sock->write("Access-Control-Allow-Origin: *\r\n");
        sock->write("Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n");
        sock->write("Access-Control-Allow-Headers: Content-Type\r\n");
        sock->write("Connection: close\r\n\r\n");
        sock->write(body);
        emit requestHandled(req.method, req.path, st);
        return st;
    }();
    (void)status;

    // Consume the handled request; close after the response flushes.
    buf.clear();
    sock->disconnectFromHost();
}

QByteArray HttpControlServer::route(const Request& req, int& statusOut) {
    statusOut = 200;

    // CORS preflight (browser-based mock/devtools). No body.
    if (req.method == QLatin1String("OPTIONS")) {
        statusOut = 204;
        return QByteArray();
    }

    // Discovery doc at "/": honest endpoint listing, no fake data.
    if (req.method == QLatin1String("GET") && req.path == QLatin1String("/")) {
        QJsonObject o;
        o[QStringLiteral("ok")] = true;
        o[QStringLiteral("service")] = QStringLiteral("mbdsdr-control-http");
        o[QStringLiteral("auth")] = QStringLiteral("none -- loopback only, do not expose");
        QJsonArray eps;
        eps.append(QJsonObject{{QStringLiteral("method"), QStringLiteral("GET")},
                               {QStringLiteral("path"), QStringLiteral("/status")}});
        eps.append(QJsonObject{{QStringLiteral("method"), QStringLiteral("GET")},
                               {QStringLiteral("path"), QStringLiteral("/pocsag_messages?channel=N")}});
        eps.append(QJsonObject{{QStringLiteral("method"), QStringLiteral("GET")},
                               {QStringLiteral("path"), QStringLiteral("/m17_calls?channel=N")}});
        eps.append(QJsonObject{{QStringLiteral("method"), QStringLiteral("GET")},
                               {QStringLiteral("path"), QStringLiteral("/vor_radial?channel=N")}});
        eps.append(QJsonObject{{QStringLiteral("method"), QStringLiteral("GET")},
                               {QStringLiteral("path"), QStringLiteral("/acars_packets?channel=N")}});
        eps.append(QJsonObject{{QStringLiteral("method"), QStringLiteral("GET")},
                               {QStringLiteral("path"), QStringLiteral("/navtex_messages?channel=N")}});
        eps.append(QJsonObject{{QStringLiteral("method"), QStringLiteral("POST")},
                               {QStringLiteral("path"), QStringLiteral("/command")},
                               {QStringLiteral("body"), QStringLiteral("{\"tool\":\"...\",\"args\":{...}}")}});
        o[QStringLiteral("endpoints")] = eps;
        return toCompact(o);
    }

    // ---- Read-only snapshots: each maps 1:1 onto a ControlHub read command --
    //      SAME JSON, SAME honest empty state, always allowed (never gated). ----
    auto readSnapshot = [&](const char* command) -> QByteArray {
        // Optional ?channel=N query -> args; otherwise the selected VFO.
        QJsonObject args;
        const int q = req.rawTarget.indexOf(QLatin1Char('?'));
        if (q >= 0) args = queryToArgs(req.rawTarget.mid(q + 1));
        return hub_->execute(QString::fromUtf8(command), args).toUtf8();
    };

    if (req.method == QLatin1String("GET") && req.path == QLatin1String("/status")) {
        // Full get_status: five-state + error_message + mode/freq/bandwidth.
        return hub_->execute(QStringLiteral("get_status"), QJsonObject()).toUtf8();
    }
    if (req.method == QLatin1String("GET") && req.path == QLatin1String("/pocsag_messages")) {
        return readSnapshot("get_pocsag_messages");
    }
    if (req.method == QLatin1String("GET") && req.path == QLatin1String("/m17_calls")) {
        return readSnapshot("get_m17_calls");
    }
    if (req.method == QLatin1String("GET") && req.path == QLatin1String("/vor_radial")) {
        return readSnapshot("get_vor_radial");
    }
    // Phase60 packet-text snapshots (?channel=N optional).
    if (req.method == QLatin1String("GET") && req.path == QLatin1String("/acars_packets")) {
        return readSnapshot("get_acars_packets");
    }
    if (req.method == QLatin1String("GET") && req.path == QLatin1String("/navtex_messages")) {
        return readSnapshot("get_navtex_messages");
    }

    // ---- Write / unified command: one honest body -> execute() same write gate.
    if (req.method == QLatin1String("POST") && req.path == QLatin1String("/command")) {
        QJsonParseError pe{};
        const QJsonDocument doc = QJsonDocument::fromJson(req.body, &pe);
        if (pe.error != QJsonParseError::NoError || !doc.isObject()) {
            statusOut = 400;
            return toCompact(errBody(QString::fromUtf8(
                "POST /command 需要 JSON 请求体: %1").arg(pe.errorString())));
        }
        const QJsonObject body = doc.object();
        const QJsonValue tool = body.value(QStringLiteral("tool"));
        if (!tool.isString()) {
            statusOut = 400;
            return toCompact(errBody(QString::fromUtf8(
                "缺少字符串字段 \"tool\"")));
        }
        QJsonObject args;
        const QJsonValue av = body.value(QStringLiteral("args"));
        if (av.isObject()) args = av.toObject();
        else if (!av.isUndefined() && !av.isNull()) {
            statusOut = 400;
            return toCompact(errBody(QString::fromUtf8(
                "字段 \"args\" 必须是对象（或省略）")));
        }
        // Unknown command / bad args / gate-closed are ALL honest results from
        // execute() itself (ok:false, gated:true, error:...); we just relay them.
        return hub_->execute(tool.toString(), args).toUtf8();
    }

    // Anything else: honest 404, never a silent fake.
    statusOut = 404;
    return toCompact(errBody(QString::fromUtf8("未知路径: %1 %2")
                                .arg(req.method, req.path)));
}

} // namespace control
} // namespace mbdsdr
