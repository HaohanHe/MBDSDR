// SPDX-License-Identifier: MIT
//
// HttpControlServer -- a thin, loopback-only JSON/HTTP front-end on top of the
// existing headless ControlHub.
//
// It does NOT re-implement any control logic: every endpoint is a direct call
// into ControlHub::execute(...), so the JSON shapes, the read/write gate, the
// honest errors and the honest empty states are EXACTLY the ones the in-process
// command surface already produces (the GUI and the AI tool loop are the other
// clients of that same one object). This object only speaks HTTP/1.1 + JSON over
// a local TCP socket.
//
// Clean-room design (own naming/structure; the loopback bind and the request
// routing are generic, not copied from any GPL server):
//
//   * LOOPBACK ONLY. The server binds 127.0.0.1 (QHostAddress::LocalHost) and
//     refuses to expose a configurable bind address to a remote interface on
//     purpose: this control channel has NO authentication. Binding to anything
//     but the loopback would let any host on the network drive the receiver.
//     startDefault() prints an explicit warning banner saying so.
//   * NO AUTH, HONEST ABOUT IT. There is no token/basic-auth. The /status and
//     discovery doc say so plainly. It is meant for this machine only (the
//     on-device mobile viewer talks to its own desktop over the loopback, or the
//     user tunnels explicitly).
//   * PORT IS ELASTIC. The default port comes from tokens::kControlHttpDefaultPort,
//     and is overridable by the MBDSDR_CONTROL_HTTP_PORT env var or the
//     "control/httpPort" QSettings key -- never a raw literal scattered around.
//   * HONEST EMPTY STATE. With no device / no decoded data, the endpoints return
//     the engine's real empty state (connected=false, count:0, [], locked=false);
//     nothing is ever fabricated.
//
// Threading: like ControlHub, this object must be CREATED on -- and never moved
// off -- the thread that owns the SpectrumEngine (its home thread). QTcpServer
// delivers newConnection / readyRead on that same thread, so the hub.execute()
// calls below run ON the home thread and dispatch directly (zero-overhead path).
// If a future caller moves this to another thread, ControlHub.execute() already
// marshals the engine touch back onto the engine's home thread with a blocking
// queued call, so the engine is still never touched off its designed thread.
#pragma once

#include <QObject>
#include <QString>
#include <QByteArray>
#include <QHash>

class QTcpServer;
class QTcpSocket;

namespace mbdsdr {
namespace control {

class ControlHub;

class HttpControlServer : public QObject {
    Q_OBJECT
public:
    explicit HttpControlServer(ControlHub* hub, QObject* parent = nullptr);
    ~HttpControlServer() override;

    // Bind to the loopback interface on `port`. Pass 0 for an ephemeral port
    // (used by tests, then read back via port()). Returns false if the bind
    // failed (port in use etc.) -- never throws, never pretends success.
    bool start(quint16 port);

    // Resolve the port from env MBDSDR_CONTROL_HTTP_PORT -> QSettings
    // "control/httpPort" -> tokens::kControlHttpDefaultPort, then start(). On
    // success it prints the local-only warning banner via qWarning.
    bool startDefault();

    void stop();

    quint16 port() const;          // actual bound port (0 if not listening)
    bool isListening() const;
    ControlHub* hub() const { return hub_; }

    // The one-line honest safety notice printed on start(). Also useful for a
    // UI "server on" badge tooltip.
    static QString warningBanner(quint16 port);

signals:
    // Observational log of every handled request (method, path, HTTP status).
    // Purely informational; the server works fine with no one connected.
    void requestHandled(const QString& method, const QString& path, int status);

private slots:
    void onNewConnection();
    void onReadyRead();
    void onDisconnected();

private:
    // One parsed HTTP request line + headers + (partial) body.
    struct Request {
        QString method;
        QString path;        // path only, without query
        QString rawTarget;   // path?query as received
        QByteArray body;
        qint64   contentLength = 0;
        bool     headersComplete = false;
    };

    // Try to complete a request from the socket's accumulated bytes; once a full
    // request is present, route() it and write the response.
    void tryHandle(QTcpSocket* sock);
    // Dispatch a completed request, returning the JSON body + HTTP status code.
    QByteArray route(const Request& req, int& statusOut);

    ControlHub*  hub_;
    QTcpServer*  server_ = nullptr;
    quint16      boundPort_ = 0;
    // Per-socket partial request buffers (owned by the socket's lifetime).
    QHash<QTcpSocket*, QByteArray> buffers_;
};

} // namespace control
} // namespace mbdsdr
