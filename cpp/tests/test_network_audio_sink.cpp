// SPDX-License-Identifier: MIT
//
// *** SYNTHETIC TEST FIXTURE, NOT REAL RECEPTION ***
//
// Phase24 block1: demodulated-audio network streaming (SDR++ network_sink
// benchmark, mechanism only). Covers:
//
//   * Honest idle/empty state: nothing fabricated before start(), nothing sent
//     when nobody listens, no fake silence.
//   * UDP loopback: write a known float block, receive the raw int16 PCM
//     datagram, assert byte content matches float*32768 quantisation exactly.
//   * TCP loopback: the sink's single-client server stream carries the same PCM.
//   * TCP no-client honesty: frames are dropped (counted), never buffered.
//   * TCP disconnect honesty: after the client leaves the state reads back
//     "listening, no client", sends stop erroring-honestly, and a reconnect is
//     accepted (re-listen, like SDR++).
//   * start() failure honesty: bad address / second listener on the same port
//     returns false + a real errno-based reason.
//   * Engine tap e2e: a synthetic offline IQ file through the REAL
//     SpectrumEngine -- the parallel network tap must carry EXACTLY the same
//     bytes as the MemoryAudioSink capture (same demodulated `out` block).
#include <QtTest/QtTest>
#include <QTemporaryDir>

#ifdef _WIN32
#  include <winsock2.h>
#  include <ws2tcpip.h>
using rawsock = SOCKET;
const rawsock kRawInvalid = INVALID_SOCKET;
inline int  rawErr() { return WSAGetLastError(); }
inline void rawClose(rawsock s) { ::closesocket(s); }
constexpr int kRawCloexec = 0;
constexpr int kRawNoSignal = 0;
// One-time Winsock init for in-test mock peers (production source self-inits).
inline void ensureRawStack() {
    static const bool kInit = [] {
        WSADATA data;
        return WSAStartup(MAKEWORD(2, 2), &data) == 0;
    }();
    (void)kInit;
}
// Winsock SO_RCVTIMEO takes a DWORD of milliseconds.
inline void setRawRcvTimeout(rawsock s, int ms) {
    DWORD v = static_cast<DWORD>(ms);
    ::setsockopt(s, SOL_SOCKET, SO_RCVTIMEO,
                 reinterpret_cast<const char*>(&v), sizeof(v));
}
#else
#  include <arpa/inet.h>
#  include <netinet/in.h>
#  include <poll.h>
#  include <sys/socket.h>
#  include <unistd.h>
using rawsock = int;
constexpr rawsock kRawInvalid = -1;
inline int  rawErr() { return errno; }
inline void rawClose(rawsock s) { ::close(s); }
constexpr int kRawCloexec = SOCK_CLOEXEC;
constexpr int kRawNoSignal = MSG_NOSIGNAL;
inline void ensureRawStack() {}
inline void setRawRcvTimeout(rawsock s, int ms) {
    timeval tv{};
    tv.tv_sec = ms / 1000;
    tv.tv_usec = (ms % 1000) * 1000;
    ::setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
}
#endif

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "dsp/spectrum_engine.h"
#include "dsp/memory_audio_sink.h"
#include "dsp/network_audio_sink.h"
#include "synthetic_iq_fixture.h"

using namespace mbdsdr::dsp;

namespace {

// Sink's wire conversion (network_audio_sink.cpp): lround(s*32768), clamped.
int16_t toPcm16(float s) {
    int q = static_cast<int>(std::lround(s * 32768.0f));
    if (q > 32767) q = 32767;
    if (q < -32768) q = -32768;
    return static_cast<int16_t>(q);
}

// --- tiny raw-socket loopback helpers (mirror the sink's own sockets) -------

rawsock openUdpReceiver() {
    ensureRawStack();
    rawsock fd = ::socket(AF_INET, SOCK_DGRAM | kRawCloexec, 0);
    if (fd == kRawInvalid) return kRawInvalid;
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    a.sin_port = 0;
    if (::bind(fd, reinterpret_cast<sockaddr*>(&a), sizeof(a)) != 0) {
        rawClose(fd);
        return kRawInvalid;
    }
    setRawRcvTimeout(fd, 300);
    return fd;
}

uint16_t portOf(rawsock fd) {
    sockaddr_in a{};
#ifdef _WIN32
    int al = sizeof(a);
#else
    socklen_t al = sizeof(a);
#endif
    if (::getsockname(fd, reinterpret_cast<sockaddr*>(&a), &al) != 0) return 0;
    return ntohs(a.sin_port);
}

// Blocking recv until `wantBytes` collected or read times out.
std::vector<uint8_t> recvUntilBytes(rawsock fd, std::size_t wantBytes, int timeoutMs) {
    std::vector<uint8_t> out;
    setRawRcvTimeout(fd, timeoutMs);
    uint8_t buf[65536];
    while (out.size() < wantBytes) {
        const int n = static_cast<int>(::recv(fd, reinterpret_cast<char*>(buf), sizeof(buf), 0));
        if (n > 0) out.insert(out.end(), buf, buf + n);
        else break;   // timeout or EOF
    }
    return out;
}

rawsock connectTcp(uint16_t port) {
    ensureRawStack();
    rawsock fd = ::socket(AF_INET, SOCK_STREAM | kRawCloexec, 0);
    if (fd == kRawInvalid) return kRawInvalid;
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (::connect(fd, reinterpret_cast<sockaddr*>(&a), sizeof(a)) != 0) {
        rawClose(fd);
        return kRawInvalid;
    }
    return fd;
}

// Drain a TCP client socket in the background while the engine runs, so the
// receive buffer never backs up the sink's send path.
class TcpDrain {
public:
    rawsock fd = kRawInvalid;
    std::atomic<bool> stop{false};
    std::vector<uint8_t> data;
    std::thread th;
    TcpDrain() = default;
    ~TcpDrain() { stop.store(true); if (th.joinable()) th.join(); }
    void start() {
        setRawRcvTimeout(fd, 100);
        th = std::thread([this] {
            uint8_t buf[65536];
            while (!stop.load()) {
                const int n = static_cast<int>(::recv(fd, reinterpret_cast<char*>(buf), sizeof(buf), 0));
                if (n > 0) {
                    std::lock_guard<std::mutex> lk(mtx);
                    data.insert(data.end(), buf, buf + n);
                } else if (n == 0) {
                    break;   // orderly close
                }
                // timeout: loop and re-check stop
            }
        });
    }
    std::vector<uint8_t> snapshot() {
        std::lock_guard<std::mutex> lk(mtx);
        return data;
    }
    std::size_t size() {
        std::lock_guard<std::mutex> lk(mtx);
        return data.size();
    }
private:
    std::mutex mtx;
};

} // namespace

class TestNetworkAudioSink : public QObject {
    Q_OBJECT
private slots:
    void idleStateHonest();
    void udpLoopbackPcmExact();
    void udpSilentWhenNoData();
    void tcpLoopbackPcmExact();
    void tcpNoClientDropsHonest();
    void tcpDisconnectAndReconnect();
    void startFailureHonest();
    void engineTapE2eMatchesDemod();
};

// ---------------------------------------------------------------------------

void TestNetworkAudioSink::idleStateHonest() {
    NetworkAudioSink sink;
    QVERIFY(!sink.running());
    QVERIFY(!sink.clientConnected());
    QVERIFY(!sink.isAvailable());
    QVERIFY(sink.outputDevices().isEmpty());   // honest: not hardware
    QCOMPARE(sink.bytesSent(), 0ULL);
    QVERIFY(sink.lastError().empty());
    QCOMPARE(sink.currentDeviceName(), QStringLiteral("network(idle)"));
    // A write before start() must be a strict no-op (no fake audio anywhere).
    const std::vector<float> blk(256, 0.5f);
    sink.write(blk);
    QCOMPARE(sink.bytesSent(), 0ULL);
}

void TestNetworkAudioSink::udpLoopbackPcmExact() {
    rawsock rx = openUdpReceiver();
    QVERIFY(rx != kRawInvalid);
    const uint16_t port = portOf(rx);
    QVERIFY(port > 0);

    NetworkAudioSink sink;
    QVERIFY(sink.start("127.0.0.1", port, NetAudioProtocol::UDP));
    QVERIFY(sink.running());
    QVERIFY(sink.clientConnected());   // connectionless: always "attached"
    QVERIFY(sink.isAvailable());

    std::vector<float> blk(512);
    for (std::size_t i = 0; i < blk.size(); ++i) {
        const int step = static_cast<int>(i % 7) - 3;   // signed: -3..+3
        blk[i] = static_cast<float>(step) / 6.0f;        // [-0.5, +0.5)
    }
    sink.write(blk);

    auto got = recvUntilBytes(rx, blk.size() * sizeof(int16_t), 2000);
    QCOMPARE(got.size(), blk.size() * sizeof(int16_t));
    QCOMPARE(sink.bytesSent(), blk.size() * sizeof(int16_t));
    for (std::size_t i = 0; i < blk.size(); ++i) {
        const int16_t exp = toPcm16(blk[i]);
        const int16_t act = static_cast<int16_t>(got[2 * i]) |
                            (static_cast<int16_t>(got[2 * i + 1]) << 8);
        QCOMPARE(act, exp);
    }
    rawClose(rx);
}

void TestNetworkAudioSink::udpSilentWhenNoData() {
    rawsock rx = openUdpReceiver();
    QVERIFY(rx != kRawInvalid);
    const uint16_t port = portOf(rx);

    NetworkAudioSink sink;
    QVERIFY(sink.start("127.0.0.1", port, NetAudioProtocol::UDP));
    QTest::qWait(350);   // nothing written at all
    uint8_t b;
    const int n = static_cast<int>(::recv(rx, reinterpret_cast<char*>(&b), 1, 0));
    QVERIFY(n <= 0);                       // no datagram, ever
    QCOMPARE(sink.bytesSent(), 0ULL);      // no fabricated audio
    rawClose(rx);
}

void TestNetworkAudioSink::tcpLoopbackPcmExact() {
    NetworkAudioSink sink;
    QVERIFY(sink.start("127.0.0.1", 0, NetAudioProtocol::TCP));
    const uint16_t port = sink.actualPort();
    QVERIFY(port > 0);
    // Listening but no client: honest state, not "connected".
    QVERIFY(sink.running());
    QVERIFY(!sink.clientConnected());

    rawsock cli = connectTcp(port);
    QVERIFY(cli != kRawInvalid);
    QTRY_VERIFY_WITH_TIMEOUT(sink.clientConnected(), 2000);

    std::vector<float> blk(1024);
    for (std::size_t i = 0; i < blk.size(); ++i)
        blk[i] = static_cast<float>(std::sin(i * 0.05)) * 0.8f;
    sink.write(blk);
    sink.write(blk);

    auto got = recvUntilBytes(cli, blk.size() * 2 * sizeof(int16_t), 3000);
    QCOMPARE(got.size(), blk.size() * 2 * sizeof(int16_t));
    QCOMPARE(sink.bytesSent(), blk.size() * 2 * sizeof(int16_t));
    for (std::size_t i = 0; i < blk.size(); ++i) {
        const int16_t exp = toPcm16(blk[i]);
        const int16_t a0 = static_cast<int16_t>(got[2*i]) | (static_cast<int16_t>(got[2*i+1]) << 8);
        const int16_t a1 = static_cast<int16_t>(got[2*(blk.size()+i)]) |
                           (static_cast<int16_t>(got[2*(blk.size()+i)+1]) << 8);
        QCOMPARE(a0, exp);
        QCOMPARE(a1, exp);
    }
    rawClose(cli);
}

void TestNetworkAudioSink::tcpNoClientDropsHonest() {
    NetworkAudioSink sink;
    QVERIFY(sink.start("127.0.0.1", 0, NetAudioProtocol::TCP));
    QVERIFY(sink.running());
    QVERIFY(!sink.clientConnected());

    const std::vector<float> blk(512, 0.5f);
    sink.write(blk);   // nobody attached: honest drop, no crash
    QCOMPARE(sink.framesDropped(), 1ULL);
    QCOMPARE(sink.bytesSent(), 0ULL);
}

void TestNetworkAudioSink::tcpDisconnectAndReconnect() {
    NetworkAudioSink sink;
    QVERIFY(sink.start("127.0.0.1", 0, NetAudioProtocol::TCP));
    const uint16_t port = sink.actualPort();

    rawsock cli = connectTcp(port);
    QVERIFY(cli != kRawInvalid);
    QTRY_VERIFY_WITH_TIMEOUT(sink.clientConnected(), 2000);

    const std::vector<float> blk(256, 0.5f);
    sink.write(blk);
    QCOMPARE(sink.bytesSent(), 256 * sizeof(int16_t));

    // Peer leaves: the accept thread must notice and the state must go honest.
    rawClose(cli);
    QTRY_VERIFY_WITH_TIMEOUT(!sink.clientConnected(), 4000);

    // Sending with no client: counted as a drop, no crash, no fake buffering.
    const uint64_t dropsBefore = sink.framesDropped();
    sink.write(blk);
    QCOMPARE(sink.framesDropped(), dropsBefore + 1);

    // Re-listen: a new client connects and streaming resumes (SDR++ semantics).
    rawsock cli2 = connectTcp(port);
    QVERIFY(cli2 != kRawInvalid);
    QTRY_VERIFY_WITH_TIMEOUT(sink.clientConnected(), 2000);
    sink.write(blk);
    auto got = recvUntilBytes(cli2, blk.size() * sizeof(int16_t), 2000);
    QCOMPARE(got.size(), blk.size() * sizeof(int16_t));
    rawClose(cli2);
}

void TestNetworkAudioSink::startFailureHonest() {
    // Bad destination address must fail honestly, not fabricate a socket.
    {
        NetworkAudioSink sink;
        QVERIFY(!sink.start("not-a-valid-ip", 9999, NetAudioProtocol::UDP));
        QVERIFY(!sink.lastError().empty());
        QVERIFY(!sink.running());
    }
    // Two listeners on the SAME port: the second bind must fail.
    NetworkAudioSink a;
    QVERIFY(a.start("127.0.0.1", 0, NetAudioProtocol::TCP));
    const uint16_t port = a.actualPort();
    NetworkAudioSink b;
    QVERIFY(!b.start("127.0.0.1", port, NetAudioProtocol::TCP));
    QVERIFY(!b.lastError().empty());
    QVERIFY(!b.running());
}

void TestNetworkAudioSink::engineTapE2eMatchesDemod() {
    // *** SYNTHETIC FIXTURE: offline cf32 file, NOT real reception ***
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString rawPath = dir.filePath("nfm.raw");
    auto iq = fixture::makeNfmIq(2.048e6, 1.5, 1000.0, 5000.0, 0.05, 50000.0);
    QVERIFY(fixture::writeRawCf32(rawPath, iq));

    SpectrumEngine eng;
    auto* mem = new MemoryAudioSink();
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(mem));

    auto net = std::make_unique<NetworkAudioSink>();
    QVERIFY(net->start("127.0.0.1", 0, NetAudioProtocol::TCP));
    const uint16_t port = net->actualPort();
    eng.setNetworkAudioSink(std::move(net));

    TcpDrain drain;
    drain.fd = connectTcp(port);
    QVERIFY(drain.fd != kRawInvalid);
    drain.start();

    QVERIFY(eng.openOfflineFile(rawPath, 2.048e6));
    eng.setDemodMode("NFM");
    eng.vfoSetFreq(eng.selectedVfoId(), 50000.0);
    eng.setSquelchEnabled(false);
    eng.setMuted(false);

    eng.start();
    QTest::qWait(800);                 // warmup through the chain
    mem->clear();
    const std::size_t warmBytes = drain.size();   // skip warmup frames on the wire

    QTest::qWait(1200);
    eng.shutdown();
    eng.wait(3000);
    QTest::qWait(300);                 // let the loopback stream drain out
    drain.stop = true;
    if (drain.th.joinable()) drain.th.join();

    // The MemoryAudioSink capture IS the demodulated `out` block; the tap must
    // have shipped the same samples as raw int16 PCM over the TCP stream.
    const std::vector<float>& mono = mem->buffer();
    QVERIFY(mono.size() > 1000);                       // real demod audio flowed
    const std::vector<uint8_t> wire = drain.snapshot();
    QVERIFY(wire.size() >= warmBytes + mono.size() * sizeof(int16_t));
    for (std::size_t i = 0; i < mono.size(); ++i) {
        const std::size_t off = warmBytes + 2 * i;
        const int16_t exp = toPcm16(mono[i]);
        const int16_t act = static_cast<int16_t>(wire[off]) |
                            (static_cast<int16_t>(wire[off + 1]) << 8);
        if (act != exp) {
            qWarning("sample %zu: wire=%d expected=%d (src=%.6f)", i, act, exp, mono[i]);
            QFAIL("network tap bytes diverge from the demodulated audio");
        }
    }
}

QTEST_MAIN(TestNetworkAudioSink)
#include "test_network_audio_sink.moc"
