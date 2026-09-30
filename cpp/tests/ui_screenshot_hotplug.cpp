// SPDX-License-Identifier: MIT
// Offscreen screenshot harness for the device hotplug event-channel UI.
//
// Real chain, no mocks: a loopback rtl_tcp server streams IQ, the engine
// connects (through the same API the 连接 button calls), the server dies
// (device pulled), the engine detects the drop and emits sourceDropped (UI:
// 「设备断开，等待重插」), the server restarts on the same port and the
// auto-reconnect recovers (UI: back to connected). Three captures, one per
// phase:
//   1. connected      -> MBD_OUT   (default scratch/hotplug_connected.png)
//   2. dropped        -> MBD_OUT2  (default scratch/hotplug_dropped.png)
//   3. auto-recovered -> MBD_OUT3  (default scratch/hotplug_recovered.png)
//
// Registered as `ui_shot_hotplug` by CMakeLists (integration stage).
// Env: MBD_W, MBD_H (window size), MBD_OUT, MBD_OUT2, MBD_OUT3.
#include <QApplication>
#include <QTcpServer>
#include <QTcpSocket>
#include <QTimer>
#include <QHostAddress>
#include <QPixmap>
#include <QByteArray>
#include <cstdlib>
#include "core/tokens.h"
#include "dsp/spectrum_engine.h"
#include "ui/main_window.h"

using mbdsdr::dsp::SpectrumEngine;
using mbdsdr::SpectrumFrame;

// Minimal rtl_tcp streamer: accepts one client, reads the two 5-byte setup
// commands, then emits inert IQ (offset-127 8-bit zeros) on a 25 ms timer.
// stop() tears the connection down (like unplugging), restart() re-listens on
// the SAME port (like re-plugging the device).
class TcpStreamServer : public QObject {
    Q_OBJECT
public:
    explicit TcpStreamServer(QObject* parent = nullptr) : QObject(parent) {
        streamTimer_.setInterval(25);
        connect(&streamTimer_, &QTimer::timeout, this, &TcpStreamServer::emitPacket);
    }
    bool listen() {
        if (!server_.listen(QHostAddress::LocalHost, 0)) return false;
        lastPort_ = server_.serverPort();
        connect(&server_, &QTcpServer::newConnection, this, &TcpStreamServer::accept);
        return true;
    }
    bool hasClient() const { return sock_ != nullptr; }
    quint16 port() const { return lastPort_; }
    void stop() {
        streamTimer_.stop();
        if (sock_) {
            // abort() = hard reset (RST): like a device unplugged / the
            // rtl_tcp daemon dying, not a graceful FIN. The engine sees
            // POLLERR immediately regardless of in-flight TCP buffers.
            sock_->abort();
            sock_->deleteLater();
            sock_ = nullptr;
        }
        server_.close();
    }
    bool restart() {
        const quint16 p = lastPort_;
        server_.close();
        if (!server_.listen(QHostAddress::LocalHost, p)) return false;
        setupBytes_ = 0;   // a fresh client must re-send the setup commands
        connect(&server_, &QTcpServer::newConnection, this, &TcpStreamServer::accept);
        return true;
    }
private slots:
    void accept() {
        QTcpSocket* s = server_.nextPendingConnection();
        // May be invoked twice (listen + restart both connect the signal);
        // the second call has no pending connection -- keep the live one.
        if (!s) return;
        if (sock_) { sock_->disconnectFromHost(); sock_->deleteLater(); }
        sock_ = s;
        connect(sock_, &QTcpSocket::readyRead, this, &TcpStreamServer::readSetup);
    }
    void readSetup() {
        if (!sock_ || setupBytes_ >= 10) return;
        setupBytes_ += static_cast<int>(sock_->bytesAvailable());
        if (setupBytes_ >= 10) streamTimer_.start();
    }
    void emitPacket() {
        if (!sock_ || sock_->state() != QAbstractSocket::ConnectedState) return;
        // ~2.4 MB/s -- close to the engine's consumption rate, so the TCP
        // buffers do not balloon (a real rtl_tcp daemon paces to the device).
        static const QByteArray chunk(60000, char(0x80));
        sock_->write(chunk);
    }
private:
    QTcpServer server_;
    QTcpSocket* sock_ = nullptr;
    QTimer streamTimer_;
    quint16 lastPort_ = 0;
    int setupBytes_ = 0;
};

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    const int w = std::atoi(qgetenv("MBD_W").constData());
    const int h = std::atoi(qgetenv("MBD_H").constData());
    const QString out1 = QString::fromLocal8Bit(
        qgetenv("MBD_OUT").isEmpty() ? "scratch/hotplug_connected.png" : qgetenv("MBD_OUT"));
    const QString out2 = QString::fromLocal8Bit(
        qgetenv("MBD_OUT2").isEmpty() ? "scratch/hotplug_dropped.png" : qgetenv("MBD_OUT2"));
    const QString out3 = QString::fromLocal8Bit(
        qgetenv("MBD_OUT3").isEmpty() ? "scratch/hotplug_recovered.png" : qgetenv("MBD_OUT3"));

    TcpStreamServer server;
    if (!server.listen()) {
        qCritical("cannot listen; abort");
        return 1;
    }

    mbdsdr::MainWindow win;
    win.resize(w > 0 ? w : 1280, h > 0 ? h : 800);
    win.show();

    SpectrumEngine* eng = win.engine();
    qInfo("engine running=%d started", eng->isRunning());
    int frameCount = 0;
    QObject::connect(eng, &SpectrumEngine::spectrumReady,
                     [&](const SpectrumFrame&) { ++frameCount; });
    int droppedCount = 0;
    int errorCount = 0;
    QObject::connect(eng, &SpectrumEngine::sourceDropped,
                     [&]() { ++droppedCount; });
    QObject::connect(eng, &SpectrumEngine::sourceError,
                     [&]() { ++errorCount; });
    QObject::connect(eng, &SpectrumEngine::sourceChanged,
                     [](const QString& n, bool c) {
                         qInfo("sourceChanged: %s connected=%d",
                               n.toUtf8().constData(), c);
                     });

    // Phase 1: connect through the real engine API (same path as the button).
    QTimer::singleShot(1500, [&]() {
        const bool ok = eng->connectRtlTcp("127.0.0.1", server.port());
        if (!ok) qWarning("initial connect failed");
        // Let some IQ flow so the UI shows the connected source state.
        QTimer::singleShot(2000, [&]() {
            QPixmap pm1 = win.grab();
            pm1.save(out1, "PNG");
            qInfo("phase1 connected -> %s dropped=%d", out1.toLocal8Bit().constData(),
                  droppedCount);

            // Phase 2: pull the device.
            qInfo("phase2 pre-stop: hasClient=%d frames=%d", server.hasClient(), frameCount);
            server.stop();
            qInfo("phase2 post-stop: hasClient=%d", server.hasClient());
            // The engine keeps draining in-flight TCP buffers before the
            // zero-read grace (~0.4 s) kicks in; give it generous time.
            // Phase 2: pull the device. In-flight TCP data drains within a
            // few hundred ms at paced rates; the drop grace (~0.4 s) plus
            // event delivery follows quickly after.
            QTimer::singleShot(2500, [&]() {
                qInfo("phase2 pre-grab: testSig=%d dropped=%d frames=%d",
                      eng->isTestSignalActive(), droppedCount, frameCount);
                QPixmap pm2 = win.grab();
                pm2.save(out2, "PNG");
                qInfo("phase2 dropped   -> %s dropped=%d errors=%d", out2.toLocal8Bit().constData(),
                      droppedCount, errorCount);

                // Phase 3: re-plug; auto-reconnect throttles at 2 s.
                server.restart();
                QTimer::singleShot(7000, [&]() {
                    QPixmap pm3 = win.grab();
                    pm3.save(out3, "PNG");
                    qInfo("phase3 recovered -> %s (%dx%d)", out3.toLocal8Bit().constData(),
                          pm3.width(), pm3.height());
                    app.quit();
                });
            });
        });
    });
    return app.exec();
}
#include "ui_screenshot_hotplug.moc"
