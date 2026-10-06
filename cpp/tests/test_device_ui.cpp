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
//
// Phase40 note (intermittent / order wall-clock): this test drives a REAL
// MainWindow, which builds the production QtAudioSink. On a headless box with
// no PulseAudio, the engine worker can stall briefly while it probes the audio
// backend, so the Phase-2 "connect -> combo populated" waitFor(6000) window is
// occasionally exceeded under load (observed ~1 in 4 single runs). We do NOT
// widen the timing assertions to paper over this: the window already matches the
// production handshake budget, and a real regression in the handshake would be
// hidden by a bigger timeout. The environmental contributor (no audio backend)
// is documented in docs/learn/phase40; on an audio-equipped box this is stable.
#include <QApplication>
#include <QDir>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QtTest>
#include <QSettings>
#include <functional>

#include <atomic>
#include <chrono>
#include <cstring>
#include <thread>
#ifdef _WIN32
#  include <winsock2.h>
#  include <ws2tcpip.h>
using rawsock = SOCKET;
const rawsock kRawInvalid = INVALID_SOCKET;
inline void rawClose(rawsock s) { ::closesocket(s); }
constexpr int kRawNoSignal = 0;
constexpr int kRawShutBoth = SD_BOTH;
inline void ensureRawStack() {
    static const bool kInit = [] {
        WSADATA data;
        return WSAStartup(MAKEWORD(2, 2), &data) == 0;
    }();
    (void)kInit;
}
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
inline void ensureRawStack() {}
#endif

#include "core/tokens.h"
#include "dsp/spectrum_engine.h"
#include "ui/main_window.h"

using namespace mbdsdr;
using mbdsdr::dsp::SpectrumEngine;

class RawRtlTcp {
public:
    ~RawRtlTcp() { stop(); }
    bool listen() {
        ensureRawStack();
        rawsock fd = ::socket(AF_INET, SOCK_STREAM, 0);
        if (fd == kRawInvalid) return false;
        int one = 1;
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR,
                   reinterpret_cast<const char*>(&one), sizeof(one));
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = 0;
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
private:
    void acceptLoop() {
        rawsock c = ::accept(listenFd_, nullptr, nullptr);
        if (c == kRawInvalid) return;
        clientFd_ = c;
        // 12-byte RTL0 dongle-info header (rtl_tcp.c:618-629). tuner_type=5.
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
            const int n = static_cast<int>(
                ::send(c, chunk, sizeof(chunk), kRawNoSignal));
            if (n <= 0) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(25));
        }
    }
    rawsock listenFd_ = kRawInvalid;
    rawsock clientFd_ = kRawInvalid;
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

    // Phase38: isolate QSettings to a throwaway PID-unique dir so the offscreen
    // MainWindow neither reads the developer's persisted rx/vfo/onboarding state
    // nor writes geometry/UI back into the real user config. onboardingDismissed
    // is pre-seeded so the first-run card does not pop (matches the returning-user
    // state this device-panel test assumes; assertions touch only the device panel).
    {
        const QString cfgDir = QDir::tempPath() + "/mbdsdr_cfg_deviceui_" +
                               QString::number(QCoreApplication::applicationPid());
        QDir().mkpath(cfgDir);
        QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope, cfgDir);
        QSettings s("MBDSDR", "MBDSDR");
        s.setValue(QString::fromUtf8("ui/onboardingDismissed"), true);
        s.sync();
    }

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
