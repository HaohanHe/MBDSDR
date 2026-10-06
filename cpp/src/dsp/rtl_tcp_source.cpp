// SPDX-License-Identifier: MIT
#include "rtl_tcp_source.h"

#include <QDateTime>

#include <cstring>
#include <mutex>

#ifdef _WIN32
#  include <winsock2.h>
#  include <ws2tcpip.h>
// wingdi.h #define's DeviceCapabilities to DeviceCapabilitiesW (print spooler
// API), which collides with our DeviceCapabilities type used below.
#  undef DeviceCapabilities
#else
#  include <cerrno>
#  include <arpa/inet.h>
#  include <fcntl.h>
#  include <netdb.h>
#  include <netinet/in.h>
#  include <poll.h>
#  include <sys/socket.h>
#  include <unistd.h>
#endif

namespace mbdsdr {
namespace dsp {

namespace {
constexpr int kConnectTimeoutMs = 2000;   // mirrors the old waitForConnected(2000)
constexpr int kReadTimeoutMs    = 50;     // mirrors waitForReadyRead(50)

// Thin native-socket abstraction so the transport logic below is identical on
// both platforms. A blocking native socket (not QTcpSocket) is used on purpose:
// IQ is polled from the engine run() thread while connect() may run elsewhere,
// and a plain fd/socket is safe to touch from any thread with no event loop.
#ifdef _WIN32
using socket_t = SOCKET;
using pollfd_t = WSAPOLLFD;
socket_t kInvalidSocket = INVALID_SOCKET;
inline int  lastSocketErr() { return WSAGetLastError(); }
inline void closeSocket(SOCKET s) { ::closesocket(s); }
inline int  doPoll(WSAPOLLFD* fds, int n, int ms) { return ::WSAPoll(fds, n, ms); }
inline bool socketWouldBlock(int e) { return e == WSAEWOULDBLOCK; }
inline bool socketInterrupted(int e) { return e == WSAEINTR; }
constexpr int kSocketCloexec = 0;     // SOCK_CLOEXEC has no Winsock equivalent
constexpr int kMsgNoSignal   = 0;     // Winsock never raises SIGPIPE
inline void ensureSocketStack() {
    static std::once_flag once;
    std::call_once(once, [] { WSADATA d; WSAStartup(MAKEWORD(2, 2), &d); });
}
#else
using socket_t = int;
using pollfd_t = pollfd;
constexpr socket_t kInvalidSocket = -1;
inline int  lastSocketErr() { return errno; }
inline void closeSocket(int s) { ::close(s); }
inline int  doPoll(pollfd* fds, int n, int ms) { return ::poll(fds, n, ms); }
inline bool socketWouldBlock(int e) { return e == EAGAIN || e == EWOULDBLOCK; }
inline bool socketInterrupted(int e) { return e == EINTR; }
constexpr int kSocketCloexec = SOCK_CLOEXEC;
constexpr int kMsgNoSignal   = MSG_NOSIGNAL;
inline void ensureSocketStack() {}
#endif

// Resolve the member fd to a usable native socket, or kInvalidSocket.
socket_t activeSocket(const std::atomic<qintptr>& fd) {
    const qintptr v = fd.load();
    return v < 0 ? kInvalidSocket : static_cast<socket_t>(v);
}
} // namespace

RtlTcpSource::RtlTcpSource(QString host, quint16 port)
    : host_(std::move(host)), port_(port) {}

RtlTcpSource::~RtlTcpSource() { stop(); }

void RtlTcpSource::setFdBlocking(qintptr fdv, bool blocking) {
    if (fdv < 0) return;
    const socket_t fd = static_cast<socket_t>(fdv);
#ifdef _WIN32
    u_long mode = blocking ? 0u : 1u;   // FIONBIO: 1 = non-blocking
    ::ioctlsocket(fd, FIONBIO, &mode);
#else
    const int flags = ::fcntl(fd, F_GETFL, 0);
    if (flags < 0) return;
    ::fcntl(fd, F_SETFL, blocking ? (flags & ~O_NONBLOCK) : (flags | O_NONBLOCK));
#endif
}

bool RtlTcpSource::start() {
    stop();   // idempotent; closes any stale socket
    ensureSocketStack();

    addrinfo hints;
    std::memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_protocol = IPPROTO_TCP;
    addrinfo* res = nullptr;
    const QByteArray hostB = host_.toUtf8();
    const QByteArray portB = QString::number(port_).toUtf8();
    if (::getaddrinfo(hostB.constData(), portB.constData(), &hints, &res) != 0) {
        lastError_ = QStringLiteral("无法解析主机 %1").arg(host_);
        return false;   // honest failure
    }

    socket_t fd = kInvalidSocket;
    for (addrinfo* ai = res; ai != nullptr; ai = ai->ai_next) {
        fd = ::socket(ai->ai_family, ai->ai_socktype | kSocketCloexec, ai->ai_protocol);
        if (fd == kInvalidSocket) continue;
        setFdBlocking(static_cast<qintptr>(fd), false);
        if (::connect(fd, ai->ai_addr, ai->ai_addrlen) != 0) {
            const int e = lastSocketErr();
            if (!socketWouldBlock(e)) {   // POSIX EINPROGRESS / Winsock WSAEWOULDBLOCK
                closeSocket(fd);
                fd = kInvalidSocket;
                continue;
            }
            pollfd_t p;
            std::memset(&p, 0, sizeof(p));
            p.fd = fd;
            p.events = POLLOUT;
            const int pr = doPoll(&p, 1, kConnectTimeoutMs);
            if (pr <= 0 || !(p.revents & POLLOUT)) {
                closeSocket(fd);
                fd = kInvalidSocket;
                continue;
            }
            int err = 0;
#ifdef _WIN32
            int elen = sizeof(err);
#else
            socklen_t elen = sizeof(err);
#endif
            if (::getsockopt(fd, SOL_SOCKET, SO_ERROR,
                             reinterpret_cast<char*>(&err), &elen) != 0 || err != 0) {
                lastError_ = QStringLiteral("socket error %1").arg(err);
                closeSocket(fd);
                fd = kInvalidSocket;
                continue;
            }
        }
        setFdBlocking(static_cast<qintptr>(fd), true);
        break;
    }
    ::freeaddrinfo(res);

    if (fd == kInvalidSocket) {
        if (lastError_.isEmpty()) lastError_ = QStringLiteral("连接超时");
        return false;
    }
    fd_.store(static_cast<qintptr>(fd));
    eof_.store(false);
    lastError_.clear();

    // Read the 12-byte "RTL0" dongle-info header the daemon sends immediately
    // on accept (real tuner identity). Must happen BEFORE we issue commands /
    // read IQ so the header is consumed and the IQ stream stays aligned.
    readDongleInfo();

    sendCmd(0x02, static_cast<quint32>(rateHz_));   // set sample rate
    sendCmd(0x01, static_cast<quint32>(freqHz_));   // set center freq
    return true;
}

void RtlTcpSource::stop() {
    const qintptr fd = fd_.exchange(-1);
    eof_.store(true);
    if (fd >= 0) closeSocket(static_cast<socket_t>(fd));
}

bool RtlTcpSource::isConnected() const {
    return fd_.load() >= 0 && !eof_.load();
}

void RtlTcpSource::readDongleInfo() {
    tunerTypeRaw_ = -1;
    tunerGainCount_ = 0;
    headerKnown_ = false;
    const socket_t fd = activeSocket(fd_);
    if (fd == kInvalidSocket) return;

    // librtlsdr rtl_tcp.c:618-629 sends 12 bytes right after accept():
    //   char magic[4] = "RTL0"; uint32 tuner_type (BE); uint32 gain_count (BE);
    unsigned char buf[12];
    std::size_t got = 0;
    // Bound the wait: the real daemon replies instantly, but a mock/legacy
    // server may send no header at all. First poll with no data -> stop (we
    // must not stall connect()); if partial bytes arrived we keep draining up
    // to 12.
    for (int attempt = 0; attempt < 6 && got < sizeof(buf); ++attempt) {
        pollfd_t p;
        std::memset(&p, 0, sizeof(p));
        p.fd = fd;
        p.events = POLLIN;
        const int pr = doPoll(&p, 1, 50);
        if (pr <= 0) { if (got == 0) break; else continue; }
        if (!(p.revents & POLLIN)) break;
        const int n = static_cast<int>(::recv(
            fd, reinterpret_cast<char*>(buf) + got,
            static_cast<int>(sizeof(buf) - got), 0));
        if (n > 0) { got += static_cast<std::size_t>(n); continue; }
        break;   // n == 0 / error: nothing more to read
    }

    if (got == sizeof(buf) && std::memcmp(buf, "RTL0", 4) == 0) {
        tunerTypeRaw_ = (static_cast<int>(buf[4]) << 24) |
                        (static_cast<int>(buf[5]) << 16) |
                        (static_cast<int>(buf[6]) <<  8) |
                        (static_cast<int>(buf[7]));
        tunerGainCount_ = (static_cast<int>(buf[8])  << 24) |
                          (static_cast<int>(buf[9])  << 16) |
                          (static_cast<int>(buf[10]) <<  8) |
                          (static_cast<int>(buf[11]));
        headerKnown_ = true;
    }
    // else: no / unrecognised header -> tuner stays unknown (honest).
}

DeviceCapabilities RtlTcpSource::capabilities() const {
    if (!isConnected()) return noDeviceCapabilities();
    if (!headerKnown_) {
        // Link up, but the server sent no parseable RTL0 header (mock / legacy).
        // Report the connected link state with honest unknown ranges -- never a
        // guessed tuner range.
        DeviceCapabilities c;
        c.connected = true;
        c.deviceName = name();
        c.provenance = QStringLiteral(
            "rtl_tcp 已连接，但未收到 RTL0 握手（服务端未上报调谐器型号）；"
            "调谐/采样率范围未知");
        return c;
    }
    return rtlCapabilitiesFromHandshake(
        tunerTypeRaw_, tunerGainCount_,
        QString("%1:%2").arg(host_).arg(port_));
}

void RtlTcpSource::sendCmd(quint8 cmd, quint32 arg) {
    commandLogged(cmd, arg);   // test seam (records even when socket is closed)
    const socket_t fd = activeSocket(fd_);
    if (fd == kInvalidSocket || eof_.load()) return;
    // rtl_tcp command: 1 byte cmd, 4 bytes big-endian arg.
    unsigned char buf[5] = {cmd,
        static_cast<unsigned char>((arg >> 24) & 0xff),
        static_cast<unsigned char>((arg >> 16) & 0xff),
        static_cast<unsigned char>((arg >>  8) & 0xff),
        static_cast<unsigned char>( arg        & 0xff)};
    int off = 0;
    while (off < 5) {
        const int n = static_cast<int>(::send(
            fd, reinterpret_cast<char*>(buf + off), 5 - off, kMsgNoSignal));
        if (n > 0) { off += n; continue; }
        const int e = lastSocketErr();
        if (n < 0 && socketInterrupted(e)) continue;
        if (n < 0 && socketWouldBlock(e)) {
            pollfd_t p;
            std::memset(&p, 0, sizeof(p));
            p.fd = fd;
            p.events = POLLOUT;
            doPoll(&p, 1, kReadTimeoutMs);
            continue;
        }
        eof_.store(true);   // send failed = link dead
        return;
    }
}

std::size_t RtlTcpSource::readIQ(std::vector<std::complex<float>>& out) {
    const socket_t fd = activeSocket(fd_);
    if (fd == kInvalidSocket || eof_.load()) return 0;
    const int want = static_cast<int>(out.size());
    if (want <= 0) return 0;

    const int wantBytes = want * 2;
    QByteArray data;
    data.reserve(wantBytes);
    while (data.size() < wantBytes) {
        pollfd_t p;
        std::memset(&p, 0, sizeof(p));
        p.fd = fd;
        p.events = POLLIN;
        const int pr = doPoll(&p, 1, kReadTimeoutMs);
        if (pr <= 0) break;                       // timeout -> partial / zero
        if (!(p.revents & POLLIN)) {
            if (p.revents & (POLLERR | POLLHUP)) { eof_.store(true); return 0; }
            break;
        }
        char chunk[65536];
        const int remaining = wantBytes - static_cast<int>(data.size());
        const int wantHere = std::min(remaining,
                                     static_cast<int>(sizeof(chunk)));
        const int n = static_cast<int>(::recv(fd, chunk, wantHere, 0));
        if (n > 0) { data.append(chunk, n); continue; }
        if (n == 0) { eof_.store(true); return 0; }   // peer closed (unplug)
        if (socketInterrupted(lastSocketErr())) continue;
        eof_.store(true);                             // ECONNRESET etc.
        return 0;
    }

    const int pairs = std::min(want, static_cast<int>(data.size() / 2));
    for (int i = 0; i < pairs; ++i) {
        const float I = (static_cast<unsigned char>(data[2*i])     - 127.0f) / 128.0f;
        const float Q = (static_cast<unsigned char>(data[2*i + 1]) - 127.0f) / 128.0f;
        out[i] = std::complex<float>(I, Q);
    }
    return static_cast<std::size_t>(pairs);
}

void RtlTcpSource::setCenterFreq(double freqHz) {
    freqHz_ = freqHz;
    sendCmd(0x01, static_cast<quint32>(freqHz));
}
void RtlTcpSource::setSampleRate(double rateHz) {
    rateHz_ = rateHz;
    sendCmd(0x02, static_cast<quint32>(rateHz));
}
void RtlTcpSource::setGain(double gainDb) {
    gainDb_ = gainDb;
    sendCmd(0x03, 0);                  // manual gain mode
    sendCmd(0x04, static_cast<quint32>(gainDb * 10));  // 0.1 dB units
}
void RtlTcpSource::setRtlAgc(bool on) {
    // rtl_tcp has one gain-mode command (0x03); route the IF AGC to it.
    sendCmd(0x03, on ? 1 : 0);
}
void RtlTcpSource::setTunerAgc(bool on) {
    // 0x03 = gain mode: 1 = AGC, 0 = manual.
    sendCmd(0x03, on ? 1 : 0);
}

} // namespace dsp
} // namespace mbdsdr
