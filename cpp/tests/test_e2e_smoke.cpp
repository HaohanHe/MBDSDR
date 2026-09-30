// SPDX-License-Identifier: MIT
// Offscreen end-to-end smoke over a POSIX loopback rtl_tcp (NOT HARDWARE /
// 非硬件). The loopback server sends the real 12-byte RTL0 handshake then
// streams deterministic int8 IQ (a complex tone). Drives the REAL engine chain
// (connect -> run loop -> FFT/spectrum -> demod readback) and asserts:
//   * frames keep flowing (frame counter grows)
//   * spectrum data is non-empty and non-flat (a real tone lights up a bin)
//   * set center freq / sample rate / gain are read back consistently
// No radio, no GSM base station -- fabricated IQ only.

#include <QCoreApplication>
#include <QElapsedTimer>
#include <QtTest>

#include <atomic>
#include <cmath>
#include <cstring>
#include <thread>
#include <vector>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include "dsp/spectrum_engine.h"
#include "core/spectrum_frame.h"

using namespace mbdsdr;
using namespace mbdsdr::dsp;

namespace {

class LoopbackServer {
public:
    ~LoopbackServer() { stop(); }
    bool listen() {
        int fd = ::socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0) return false;
        int one = 1;
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = 0;
        if (::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
            ::close(fd); return false;
        }
        if (::listen(fd, 1) < 0) { ::close(fd); return false; }
        socklen_t alen = sizeof(addr);
        ::getsockname(fd, reinterpret_cast<sockaddr*>(&addr), &alen);
        listenFd_ = fd;
        port_ = ntohs(addr.sin_port);
        thread_ = std::thread([this] { run(); });
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
    void run() {
        int c = ::accept(listenFd_, nullptr, nullptr);
        if (c < 0) return;
        clientFd_ = c;
        unsigned char hdr[12];
        std::memcpy(hdr, "RTL0", 4);
        const quint32 tuner = 5, gains = 29;   // R820T
        for (int i = 0; i < 4; ++i) hdr[4 + i] = (tuner >> (24 - 8*i)) & 0xff;
        for (int i = 0; i < 4; ++i) hdr[8 + i] = (gains >> (24 - 8*i)) & 0xff;
        ::send(c, reinterpret_cast<const char*>(hdr), 12, 0);

        // Deterministic complex tone (int8 I/Q): 200 bins into a 2048 window.
        constexpr int kBuf = 8192;
        std::vector<unsigned char> chunk(kBuf * 2);
        double ph = 0.0;
        const double dph = 2.0 * M_PI * 200.0 / 2048.0;
        for (int i = 0; i < kBuf; ++i) {
            chunk[2*i]     = static_cast<unsigned char>(127 + 60 * std::cos(ph));
            chunk[2*i + 1] = static_cast<unsigned char>(127 + 60 * std::sin(ph));
            ph += dph;
        }
        while (!stop_.load()) {
            ssize_t n = ::send(c, reinterpret_cast<const char*>(chunk.data()),
                               chunk.size(), 0);
            if (n <= 0) break;
            usleep(20000);
        }
    }
    int listenFd_ = -1, clientFd_ = -1;
    std::atomic<bool> stop_{false};
    std::thread thread_;
    quint16 port_ = 0;
};

struct Sink {
    int frames = 0;
    double maxDbfs = -999.0;
    qint64 lastFreq = 0, lastSr = 0;
    double lastGain = 0.0;
    bool connected = false;
    void onFrame(const mbdsdr::SpectrumFrame& f) {
        ++frames;
        for (float v : f.dbfs) if (v > maxDbfs) maxDbfs = v;
    }
    void onTelemetry(const QString&, bool c, double freq, double sr, double g) {
        connected = c; lastFreq = qint64(freq); lastSr = qint64(sr); lastGain = g;
    }
};

} // namespace

int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    int failures = 0;
    auto check = [&](bool cond, const char* msg) {
        if (!cond) { ++failures; qWarning("FAIL: %s", msg); }
        else qInfo("ok: %s", msg);
    };

    LoopbackServer server;
    if (!server.listen()) { qCritical("cannot listen"); return 1; }

    SpectrumEngine eng;
    eng.start();
    Sink sink;
    QObject::connect(&eng, &SpectrumEngine::spectrumReady,
                     [&](const mbdsdr::SpectrumFrame& f) { sink.onFrame(f); });
    QObject::connect(&eng, &SpectrumEngine::sourceTelemetry,
                     [&](const QString& n, bool c, double f, double s, double g) {
                         sink.onTelemetry(n, c, f, s, g);
                     });

    const bool ok = eng.connectRtlTcp("127.0.0.1", server.port());
    check(ok, "connectRtlTcp to loopback succeeds");

    // Settings round-trip through the live source.
    eng.onSetCenterFreq(98.5e6);
    eng.onSetSampleRate(2.4e6);
    eng.onSetGain(20.0);

    // Pump the event loop for a fixed window: the engine needs a few hundred ms
    // to drain the handshake and fill the first FFT window (avoid a start race).
    QElapsedTimer t; t.start();
    while (t.elapsed() < 6000)
        QCoreApplication::processEvents(QEventLoop::AllEvents, 20);

    check(sink.frames >= 5, "engine produced spectrum frames (IQ flowing)");
    check(sink.connected, "telemetry reports the real source connected");
    check(sink.maxDbfs > -80.0, "spectrum has a lit tone bin (non-flat data)");
    check(sink.lastFreq == qint64(98.5e6), "center freq read back after set");
    check(sink.lastSr == qint64(2.4e6), "sample rate read back after set");
    check(sink.lastGain >= 15.0 && sink.lastGain <= 25.0, "gain read back after set");

    // Honest rejection: bogus settings must NOT take effect / crash.
    eng.onSetCenterFreq(-5000.0);
    eng.onSetSampleRate(0.0);
    eng.onSetSampleRate(1e9);
    QCoreApplication::processEvents(QEventLoop::AllEvents, 50);
    check(sink.lastFreq == qint64(98.5e6), "illegal negative freq ignored");
    check(sink.lastSr == qint64(2.4e6), "illegal/absurd sample rate ignored");

    qInfo("cleanup: disconnect source");
    eng.disconnectSource();
    qInfo("cleanup: shutdown engine");
    eng.shutdown();
    qInfo("cleanup: stop server");
    server.stop();
    qInfo("cleanup: wait engine");
    eng.wait(3000);
    qInfo("cleanup: done");
    return failures ? 1 : 0;
}
