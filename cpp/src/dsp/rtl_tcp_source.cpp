// SPDX-License-Identifier: MIT
#include "rtl_tcp_source.h"

#include <QDateTime>

#include <cerrno>
#include <cstring>

#include <arpa/inet.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

namespace mbdsdr {
namespace dsp {

namespace {
constexpr int kConnectTimeoutMs = 2000;   // mirrors the old waitForConnected(2000)
constexpr int kReadTimeoutMs    = 50;     // mirrors waitForReadyRead(50)
}

RtlTcpSource::RtlTcpSource(QString host, quint16 port)
    : host_(std::move(host)), port_(port) {}

RtlTcpSource::~RtlTcpSource() { stop(); }

void RtlTcpSource::setFdBlocking(bool blocking) {
    const int fd = fd_.load();
    if (fd < 0) return;
    const int flags = ::fcntl(fd, F_GETFL, 0);
    if (flags < 0) return;
    ::fcntl(fd, F_SETFL, blocking ? (flags & ~O_NONBLOCK) : (flags | O_NONBLOCK));
}

bool RtlTcpSource::start() {
    stop();   // idempotent; closes any stale fd

    addrinfo hints;
    std::memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_protocol = IPPROTO_TCP;
    addrinfo* res = nullptr;
    const QString portStr = QString::number(port_);
    if (::getaddrinfo(host_.toUtf8().constData(), portStr.toUtf8().constData(),
                      &hints, &res) != 0) {
        lastError_ = QStringLiteral("无法解析主机 %1").arg(host_);
        return false;   // honest failure
    }

    int fd = -1;
    for (addrinfo* ai = res; ai != nullptr; ai = ai->ai_next) {
        fd = ::socket(ai->ai_family, ai->ai_socktype | SOCK_CLOEXEC, ai->ai_protocol);
        if (fd < 0) continue;
        setFdBlocking(false);
        if (::connect(fd, ai->ai_addr, ai->ai_addrlen) != 0) {
            if (errno != EINPROGRESS) {
                ::close(fd);
                fd = -1;
                continue;
            }
            pollfd p{fd, POLLOUT, 0};
            const int pr = ::poll(&p, 1, kConnectTimeoutMs);
            if (pr <= 0 || !(p.revents & POLLOUT)) {
                ::close(fd);
                fd = -1;
                continue;
            }
            int err = 0;
            socklen_t elen = sizeof(err);
            if (::getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &elen) != 0 || err != 0) {
                lastError_ = QString::fromLocal8Bit(std::strerror(err));
                ::close(fd);
                fd = -1;
                continue;
            }
        }
        setFdBlocking(true);
        break;
    }
    ::freeaddrinfo(res);

    if (fd < 0) {
        if (lastError_.isEmpty()) lastError_ = QStringLiteral("连接超时");
        return false;
    }
    fd_.store(fd);
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
    const int fd = fd_.exchange(-1);
    eof_.store(true);
    if (fd >= 0) ::close(fd);
}

bool RtlTcpSource::isConnected() const {
    return fd_.load() >= 0 && !eof_.load();
}

void RtlTcpSource::readDongleInfo() {
    tunerTypeRaw_ = -1;
    tunerGainCount_ = 0;
    headerKnown_ = false;
    const int fd = fd_.load();
    if (fd < 0) return;

    // librtlsdr rtl_tcp.c:618-629 sends 12 bytes right after accept():
    //   char magic[4] = "RTL0"; uint32 tuner_type (BE); uint32 gain_count (BE);
    unsigned char buf[12];
    std::size_t got = 0;
    // Bound the wait: the real daemon replies instantly, but a mock/legacy
    // server may send no header at all. First poll with no data -> stop (we
    // must not stall connect()); if partial bytes arrived we keep draining up
    // to 12.
    for (int attempt = 0; attempt < 6 && got < sizeof(buf); ++attempt) {
        pollfd p{fd, POLLIN, 0};
        const int pr = ::poll(&p, 1, 50);
        if (pr <= 0) { if (got == 0) break; else continue; }
        if (!(p.revents & POLLIN)) break;
        const ssize_t n = ::recv(fd, reinterpret_cast<char*>(buf) + got,
                                 sizeof(buf) - got, 0);
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
    const int fd = fd_.load();
    if (fd < 0 || eof_.load()) return;
    // rtl_tcp command: 1 byte cmd, 4 bytes big-endian arg.
    unsigned char buf[5] = {cmd,
        static_cast<unsigned char>((arg >> 24) & 0xff),
        static_cast<unsigned char>((arg >> 16) & 0xff),
        static_cast<unsigned char>((arg >>  8) & 0xff),
        static_cast<unsigned char>( arg        & 0xff)};
    ssize_t off = 0;
    while (off < 5) {
        const ssize_t n = ::send(fd, buf + off, 5 - off, MSG_NOSIGNAL);
        if (n > 0) { off += n; continue; }
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            pollfd p{fd, POLLOUT, 0};
            ::poll(&p, 1, kReadTimeoutMs);
            continue;
        }
        eof_.store(true);   // send failed = link dead
        return;
    }
}

std::size_t RtlTcpSource::readIQ(std::vector<std::complex<float>>& out) {
    const int fd = fd_.load();
    if (fd < 0 || eof_.load()) return 0;
    const int want = static_cast<int>(out.size());
    if (want <= 0) return 0;

    const std::size_t wantBytes = static_cast<std::size_t>(want) * 2;
    QByteArray data;
    data.reserve(static_cast<int>(wantBytes));
    while (static_cast<std::size_t>(data.size()) < wantBytes) {
        pollfd p{fd, POLLIN, 0};
        const int pr = ::poll(&p, 1, kReadTimeoutMs);
        if (pr <= 0) break;                       // timeout -> partial / zero
        if (!(p.revents & POLLIN)) {
            if (p.revents & (POLLERR | POLLHUP)) { eof_.store(true); return 0; }
            break;
        }
        char chunk[65536];
        const std::size_t wantHere =
            std::min(wantBytes - static_cast<std::size_t>(data.size()),
                     sizeof(chunk));
        const ssize_t n = ::recv(fd, chunk, wantHere, 0);
        if (n > 0) { data.append(chunk, static_cast<int>(n)); continue; }
        if (n == 0) { eof_.store(true); return 0; }   // peer closed (unplug)
        if (errno == EINTR) continue;
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

} // namespace dsp
} // namespace mbdsdr
