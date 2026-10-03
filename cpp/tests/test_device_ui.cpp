// SPDX-License-Identifier: MIT
// Offscreen UI test for the device-info panel + dynamic sample-rate combo.
//
// Real chain, no mocks. The loopback rtl_tcp server is a raw POSIX-socket
// thread (accept -> immediately send the 12-byte "RTL0" handshake, then stream
// inert IQ). Using a raw thread avoids the Qt event-loop race where the header
// would otherwise arrive as IQ samples.
//   1. No device -> honest empty state (combo disabled/empty, "RTL-SDR 未连接").
//   2. Connect (server sends RTL0, tuner type 5 = R820T) -> combo populated from
//      the real range and device labelled R820T.
//   3. Pull the device -> combo back to empty/disabled.
#include <QApplication>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QtTest>
#include <functional>

#include <atomic>
#include <cstring>
#include <thread>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include "core/tokens.h"
#include "dsp/spectrum_engine.h"
#include "ui/main_window.h"

using namespace mbdsdr;
using mbdsdr::dsp::SpectrumEngine;

class RawRtlTcp {
public:
    ~RawRtlTcp() { stop(); }
    bool listen() {
        int fd = ::socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0) return false;
        int one = 1;
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = 0;
        if (::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) { ::close(fd); return false; }
        if (::listen(fd, 1) < 0) { ::close(fd); return false; }
        socklen_t alen = sizeof(addr);
        ::getsockname(fd, reinterpret_cast<sockaddr*>(&addr), &alen);
        listenFd_ = fd;
        port_ = ntohs(addr.sin_port);
        thread_ = std::thread([this] { acceptLoop(); });
        return true;
    }
    quint16 port() const { return port_; }
    void stop() {
        stop_ = true;
        if (listenFd_ >= 0) { ::shutdown(listenFd_, SHUT_RDWR); ::close(listenFd_); listenFd_ = -1; }
        if (clientFd_ >= 0) { ::shutdown(clientFd_, SHUT_RDWR); ::close(clientFd_); clientFd_ = -1; }
        if (thread_.joinable()) thread_.join();
    }
private:
    void acceptLoop() {
        int c = ::accept(listenFd_, nullptr, nullptr);
        if (c < 0) return;
        clientFd_ = c;
        // 12-byte RTL0 dongle-info header (rtl_tcp.c:618-629). tuner_type=5.
        unsigned char hdr[12];
        std::memcpy(hdr, "RTL0", 4);
        const quint32 tuner = 5;   // R820T
        const quint32 gains = 29;
        for (int i = 0; i < 4; ++i) hdr[4 + i] = (tuner >> (24 - 8*i)) & 0xff;
        for (int i = 0; i < 4; ++i) hdr[8 + i] = (gains >> (24 - 8*i)) & 0xff;
        ::send(c, reinterpret_cast<const char*>(hdr), 12, 0);
        char chunk[60000];
        std::memset(chunk, 0x80, sizeof(chunk));
        while (!stop_.load()) {
            ssize_t n = ::send(c, chunk, sizeof(chunk), 0);
            if (n <= 0) break;
            usleep(25000);
        }
    }
    int listenFd_ = -1;
    int clientFd_ = -1;
    std::atomic<bool> stop_{false};
    std::thread thread_;
    quint16 port_ = 0;
};

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

    // Phase21: the engine no longer auto-falls back to the offline test source.
    // Without it the idle NullSource never feeds the run loop, which left the
    // default empty-state teardown hanging on exit. Opt in explicitly to the
    // synthetic source BEFORE the MainWindow (which builds the engine) exists;
    // Phase 1 still asserts the honest "未连接" empty state because a synthetic
    // source reports connected=false and an empty device-capability table.
    qputenv("MBDSDR_TEST_SOURCE", "1");

    int failures = 0;
    auto check = [&](bool cond, const char* msg) {
        if (!cond) { ++failures; qWarning("FAIL: %s", msg); }
        else { qInfo("ok: %s", msg); }
    };

    RawRtlTcp server;
    if (!server.listen()) { qCritical("cannot listen"); return 1; }

    MainWindow win;
    win.resize(1100, 760);
    win.show();

    // Phase 1: no device.
    waitFor([&](){ return win.harnessDeviceName().contains(QStringLiteral("未连接")); }, 2000);
    check(win.harnessDeviceName().contains(QStringLiteral("未连接")),
          "no device: panel shows RTL-SDR 未连接");
    check(win.harnessSampleRateOptions().isEmpty(), "no device: sample-rate combo empty");
    check(!win.harnessSampleRateEnabled(), "no device: sample-rate combo disabled");

    // Phase 2: connect (raw server sends the RTL0 handshake immediately).
    SpectrumEngine* eng = win.engine();
    eng->connectRtlTcp("127.0.0.1", server.port());
    const bool connected = waitFor([&]() {
        return !win.harnessSampleRateOptions().isEmpty() && win.harnessSampleRateEnabled();
    }, 6000);
    check(connected, "connect: combo populated + enabled after real RTL0 handshake");
    check(win.harnessDeviceName().contains(QStringLiteral("R820T")),
          "connect: device name parsed from handshake tuner_type=5");
    qInfo("connected device name: %s", win.harnessDeviceName().toUtf8().constData());
    qInfo("sample-rate options: %d", static_cast<int>(win.harnessSampleRateOptions().size()));

    // Phase 3: pull the device.
    server.stop();
    const bool cleared = waitFor([&]() {
        return win.harnessSampleRateOptions().isEmpty() && !win.harnessSampleRateEnabled();
    }, 8000);
    check(cleared, "drop: combo back to empty + disabled after device pull");

    return failures ? 1 : 0;
}

#include "test_device_ui.moc"
