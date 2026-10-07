// SPDX-License-Identifier: MIT
// Offscreen screenshot harness for the device-info panel + dynamic sample-rate
// combo. Raw POSIX-socket loopback server (accept -> immediately send the 12-byte
// "RTL0" handshake, tuner type 5 = R820T, then stream inert IQ).
//   1. No device -> honest empty state (scratch/ui_devices_empty.png).
//   2. Connected -> real name/ranges + device-derived sample-rate combo
//      (scratch/ui_devices.png).
// Registered as `ui_shot_devices`. Env: MBD_W, MBD_H, MBD_OUT, MBD_OUT_EMPTY.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <cstdlib>
#include <cstring>
#include <atomic>
#include <chrono>
#include <thread>
#ifdef _WIN32
#  include <winsock2.h>
#  include <ws2tcpip.h>
using rawsock = SOCKET;
const rawsock kRawInvalid = INVALID_SOCKET;
inline void rawClose(rawsock s) { ::closesocket(s); }
constexpr int kRawNoSignal = 0;
constexpr int kRawShutBoth = SD_BOTH;
#else
#  include <arpa/inet.h>
#  include <netinet/in.h>
#  include <sys/socket.h>
#  include <unistd.h>
using rawsock = int;
constexpr rawsock kRawInvalid = -1;
inline void rawClose(rawsock s) { ::close(s); }
constexpr int kRawNoSignal = MSG_NOSIGNAL;
constexpr int kRawShutBoth = SHUT_RDWR;
#endif

#include "core/tokens.h"
#include "dsp/spectrum_engine.h"
#include "ui/main_window.h"

using mbdsdr::dsp::SpectrumEngine;

class RawRtlTcp {
public:
    ~RawRtlTcp() { stop(); }
    bool listen() {
        rawsock fd = ::socket(AF_INET, SOCK_STREAM, 0);
        if (fd == kRawInvalid) return false;
        int one = 1;
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR,
                   reinterpret_cast<const char*>(&one), sizeof(one));
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
            rawClose(fd); return false;
        }
        if (::listen(fd, 1) != 0) { rawClose(fd); return false; }
#ifdef _WIN32
        int alen = sizeof(addr);
#else
        socklen_t alen = sizeof(addr);
#endif
        ::getsockname(fd, reinterpret_cast<sockaddr*>(&addr), &alen);
        listenFd_ = fd;
        port_ = ntohs(addr.sin_port);
        thread_ = std::thread([this] { acceptLoop(); });
        return true;
    }
    quint16 port() const { return port_; }
private:
    void acceptLoop() {
        rawsock c = ::accept(listenFd_, nullptr, nullptr);
        if (c == kRawInvalid) return;
        clientFd_ = c;
        unsigned char hdr[12];
        std::memcpy(hdr, "RTL0", 4);
        const quint32 tuner = 5;   // R820T
        const quint32 gains = 29;
        for (int i = 0; i < 4; ++i) hdr[4 + i] = (tuner >> (24 - 8*i)) & 0xff;
        for (int i = 0; i < 4; ++i) hdr[8 + i] = (gains >> (24 - 8*i)) & 0xff;
        ::send(c, reinterpret_cast<const char*>(hdr), 12, kRawNoSignal);
        char chunk[60000];
        std::memset(chunk, 0x80, sizeof(chunk));
        while (!stop_.load()) {
            if (::send(c, chunk, sizeof(chunk), kRawNoSignal) <= 0) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(25));
        }
    }
    void stop() {
        stop_ = true;
        if (listenFd_ != kRawInvalid) {
            ::shutdown(listenFd_, kRawShutBoth); rawClose(listenFd_);
            listenFd_ = kRawInvalid;
        }
        if (clientFd_ != kRawInvalid) {
            ::shutdown(clientFd_, kRawShutBoth); rawClose(clientFd_);
            clientFd_ = kRawInvalid;
        }
        if (thread_.joinable()) thread_.join();
    }
    rawsock listenFd_ = kRawInvalid;
    rawsock clientFd_ = kRawInvalid;
    std::atomic<bool> stop_{false};
    std::thread thread_;
    quint16 port_ = 0;
};

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    const int w = std::atoi(qgetenv("MBD_W").constData());
    const int h = std::atoi(qgetenv("MBD_H").constData());
    const QString out = QString::fromLocal8Bit(
        qgetenv("MBD_OUT").isEmpty() ? "scratch/ui_devices.png" : qgetenv("MBD_OUT"));
    const QString outEmpty = QString::fromLocal8Bit(
        qgetenv("MBD_OUT_EMPTY").isEmpty() ? "scratch/ui_devices_empty.png" : qgetenv("MBD_OUT_EMPTY"));

    RawRtlTcp server;
    if (!server.listen()) { qCritical("cannot listen"); return 1; }

    mbdsdr::MainWindow win;
    win.resize(w > 0 ? w : 1100, h > 0 ? h : 780);
    win.show();

    QTimer::singleShot(600, [&]() {
        win.grab().save(outEmpty, "PNG");
        qInfo("empty -> %s", outEmpty.toLocal8Bit().constData());

        SpectrumEngine* eng = win.engine();
        eng->connectRtlTcp("127.0.0.1", server.port());
        QTimer::singleShot(3000, [&]() {
            win.grab().save(out, "PNG");
            qInfo("connected -> %s (device='%s', sr options=%d)",
                  out.toLocal8Bit().constData(),
                  win.harnessDeviceName().toLocal8Bit().constData(),
                  static_cast<int>(win.harnessSampleRateOptions().size()));
            app.quit();
        });
    });
    return app.exec();
}
