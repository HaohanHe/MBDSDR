// SPDX-License-Identifier: MIT
#include "network_audio_sink.h"

#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cstring>

namespace mbdsdr {
namespace dsp {

namespace {
// Bounded DSP-block transmit wait: a wedged TCP peer may stall the audio thread
// for at most this long, after which the frame is dropped and counted as a send
// error (honest) instead of killing the receive chain.
constexpr int kSendTimeoutMs = 200;
// Accept thread wake / client-detect cadence.
constexpr int kAcceptPollMs = 100;
constexpr int kClientRcvTimeoutMs = 200;

std::string errnoText(int e) {
    return std::strerror(e);
}
} // namespace

NetworkAudioSink::NetworkAudioSink() = default;

NetworkAudioSink::~NetworkAudioSink() {
    stop();
}

void NetworkAudioSink::setLastErrorLocked(const std::string& what) {
    std::lock_guard<std::mutex> lk(errMtx_);
    lastError_ = what;
}

std::string NetworkAudioSink::lastError() const {
    std::lock_guard<std::mutex> lk(const_cast<std::mutex&>(errMtx_));
    return lastError_;
}

bool NetworkAudioSink::start(const std::string& host, uint16_t port,
                             NetAudioProtocol proto, bool stereo) {
    stop();   // idempotent tear-down of any previous session
    std::lock_guard<std::mutex> lk(mtx_);

    proto_ = proto;
    stereo_ = stereo;
    targetHost_ = host;
    targetPort_ = port;
    actualPort_.store(0);

    if (proto_ == NetAudioProtocol::UDP) {
        int fd = ::socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
        if (fd < 0) {
            setLastErrorLocked(std::string("socket: ") + errnoText(errno));
            return false;
        }
        sockaddr_in dst{};
        dst.sin_family = AF_INET;
        dst.sin_port = htons(port);
        if (::inet_pton(AF_INET, host.c_str(), &dst.sin_addr) != 1) {
            ::close(fd);
            setLastErrorLocked("bad destination address: " + host);
            return false;
        }
        // Bounded transmit wait: a wedged peer stalls the DSP thread for at most
        // kSendTimeoutMs, then the frame is dropped + counted honestly.
        struct timeval stv{};
        stv.tv_sec = 0;
        stv.tv_usec = kSendTimeoutMs * 1000;
        ::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &stv, sizeof(stv));
        // Connected UDP: send() instead of sendto(), and ICMP port-unreachables
        // surface back as ECONNREFUSED on send (honest error instead of silently
        // vanishing datagrams).
        if (::connect(fd, reinterpret_cast<sockaddr*>(&dst), sizeof(dst)) < 0) {
            const int e = errno;
            ::close(fd);
            setLastErrorLocked(std::string("connect(udp): ") + errnoText(e));
            return false;
        }
        sendFd_ = fd;
        actualPort_.store(port);
    } else {
        int lfd = ::socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
        if (lfd < 0) {
            setLastErrorLocked(std::string("socket: ") + errnoText(errno));
            return false;
        }
        int one = 1;
        ::setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_ANY);
        addr.sin_port = htons(port);
        if (::bind(lfd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
            const int e = errno;
            ::close(lfd);
            setLastErrorLocked(std::string("bind: ") + errnoText(e));
            return false;
        }
        if (::listen(lfd, 1) < 0) {
            const int e = errno;
            ::close(lfd);
            setLastErrorLocked(std::string("listen: ") + errnoText(e));
            return false;
        }
        listenFd_ = lfd;
        {
            sockaddr_in actual{};
            socklen_t alen = sizeof(actual);
            if (::getsockname(lfd, reinterpret_cast<sockaddr*>(&actual), &alen) == 0)
                actualPort_.store(ntohs(actual.sin_port));
            else
                actualPort_.store(port);
        }
        stopAccept_.store(false);
        acceptThread_ = std::thread(&NetworkAudioSink::acceptLoop, this);
    }

    running_.store(true);
    setLastErrorLocked("");
    return true;
}

void NetworkAudioSink::stop() {
    {
        std::lock_guard<std::mutex> lk(mtx_);
        if (!running_.load() && sendFd_ < 0 && listenFd_ < 0 &&
            clientFd_.load() < 0) {
            return;   // nothing to tear down
        }
        stopAccept_.store(true);
        if (sendFd_ >= 0) { ::close(sendFd_); sendFd_ = -1; }
        if (listenFd_ >= 0) {
            // Wake the poll()ing accept loop.
            ::shutdown(listenFd_, SHUT_RDWR);
            ::close(listenFd_);
            listenFd_ = -1;
        }
        int c = clientFd_.exchange(-1);
        if (c >= 0) {
            ::shutdown(c, SHUT_RDWR);
            ::close(c);
        }
        running_.store(false);
    }
    if (acceptThread_.joinable()) acceptThread_.join();
}

void NetworkAudioSink::acceptLoop() {
    while (!stopAccept_.load()) {
        pollfd pfd{listenFd_, POLLIN, 0};
        int pr = ::poll(&pfd, 1, kAcceptPollMs);
        if (pr < 0) {
            if (errno == EINTR) continue;
            break;
        }
        if (pr == 0) continue;   // timeout: re-check stopAccept_

        sockaddr_in cli{};
        socklen_t clen = sizeof(cli);
        int cfd = ::accept(listenFd_, reinterpret_cast<sockaddr*>(&cli), &clen);
        if (cfd < 0) {
            if (errno == EINTR || errno == EAGAIN) continue;
            if (stopAccept_.load()) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
            continue;
        }

        // Bounded recv wait so a half-dead client is reaped within ~1 s even if
        // no FIN ever arrives; keepalive catches the rest.
        struct timeval tv{};
        tv.tv_sec = 0;
        tv.tv_usec = kClientRcvTimeoutMs * 1000;
        ::setsockopt(cfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        struct timeval stv{};
        stv.tv_sec = 0;
        stv.tv_usec = kSendTimeoutMs * 1000;
        ::setsockopt(cfd, SOL_SOCKET, SO_SNDTIMEO, &stv, sizeof(stv));
        int one = 1;
        ::setsockopt(cfd, SOL_SOCKET, SO_KEEPALIVE, &one, sizeof(one));

        {
            std::lock_guard<std::mutex> lk(mtx_);
            // Single-client policy (like SDR++): a second evicts the first.
            int old = clientFd_.exchange(cfd);
            if (old >= 0) { ::shutdown(old, SHUT_RDWR); ::close(old); }
        }

        // Block here until this client leaves.
        char b;
        while (!stopAccept_.load()) {
            ssize_t r = ::recv(cfd, &b, 1, 0);
            if (r == 0) break;                 // orderly close
            if (r < 0) {
                if (errno == EINTR) continue;
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    // Idle timeout: still ours? then keep waiting.
                    if (clientFd_.load() != cfd) break;
                    continue;
                }
                break;                         // RST / hard error
            }
            // Data from the peer: the wire is push-only (we never expect
            // commands) -- ignore and keep watching for the close.
        }

        {
            std::lock_guard<std::mutex> lk(mtx_);
            if (clientFd_.load() == cfd) clientFd_.store(-1);
        }
        ::close(cfd);
    }
}

bool NetworkAudioSink::sendMono(const float* data, std::size_t n) {
    if (n == 0) return true;
    convBuf_.assign(n, 0);
    const float v = volume_.load();
    const bool muted = muted_.load();
    for (std::size_t i = 0; i < n; ++i) {
        float s = muted ? 0.0f : data[i] * v;
        s = std::clamp(s, -1.0f, 1.0f);
        int q = static_cast<int>(std::lround(s * 32768.0f));
        if (q > 32767) q = 32767;         // -32768 stays representable
        convBuf_[i] = static_cast<int16_t>(q);
    }

    std::lock_guard<std::mutex> lk(mtx_);
    if (!running_.load()) return false;

    int fd = -1;
    if (proto_ == NetAudioProtocol::UDP) fd = sendFd_;
    else fd = clientFd_.load();

    if (fd < 0) {
        // TCP with nobody attached: honest drop, never fabricate audio.
        framesDropped_.fetch_add(1);
        return false;
    }

    const std::size_t total = n * sizeof(int16_t);
    const char* ptr = reinterpret_cast<const char*>(convBuf_.data());
    std::size_t off = 0;
    while (off < total) {
        ssize_t s = ::send(fd, ptr + off, total - off, MSG_NOSIGNAL);
        if (s <= 0) {
            const int e = errno;
            sendErrors_.fetch_add(1);
            setLastErrorLocked(std::string("send: ") + errnoText(e));
            if (proto_ == NetAudioProtocol::TCP &&
                (e == EPIPE || e == ECONNRESET)) {
                // Dead peer: detach so the state reads honestly. The accept
                // thread will reap the fd (its recv watch has already fired).
                clientFd_.compare_exchange_strong(fd, -1);
            }
            return false;
        }
        off += static_cast<std::size_t>(s);
    }
    bytesSent_.fetch_add(total);
    return true;
}

void NetworkAudioSink::write(const std::vector<float>& audio) {
    if (!running_.load() || audio.empty()) return;
    sendMono(audio.data(), audio.size());
}

void NetworkAudioSink::writeStereo(const std::vector<float>& left,
                                    const std::vector<float>& right) {
    if (!running_.load()) return;
    const std::size_t n = std::min(left.size(), right.size());
    if (n == 0) return;
    if (!stereo_) {
        // Downmix exactly like the IAudioSink default contract, then ship mono.
        std::vector<float> mono(n);
        for (std::size_t i = 0; i < n; ++i)
            mono[i] = (left[i] + right[i]) * 0.5f;
        sendMono(mono.data(), n);
        return;
    }
    // Interleaved L/R int16: build a stereo-interleaved buffer, reuse sendMono's
    // wire path by feeding it the interleaved float pairs as one mono block of
    // 2*n samples (frame i = L[i], R[i]).
    std::vector<float> inter(2 * n);
    for (std::size_t i = 0; i < n; ++i) {
        inter[2 * i] = left[i];
        inter[2 * i + 1] = right[i];
    }
    sendMono(inter.data(), 2 * n);
}

void NetworkAudioSink::setVolume(float v) {
    volume_.store(std::clamp(v, 0.0f, 1.0f));
}

void NetworkAudioSink::setMuted(bool m) {
    muted_.store(m);
}

bool NetworkAudioSink::isAvailable() const {
    return running_.load();
}

QStringList NetworkAudioSink::outputDevices() const {
    // Honest empty list: this sink is not a sound card.
    return QStringList();
}

QString NetworkAudioSink::currentDeviceName() const {
    if (!running_.load()) return QStringLiteral("network(idle)");
    const char* p = (proto_ == NetAudioProtocol::UDP) ? "udp" : "tcp";
    return QString(QString::fromStdString(p) + "://" +
                   QString::fromStdString(targetHost_) + ":" +
                   QString::number(targetPort_));
}

bool NetworkAudioSink::clientConnected() const {
    if (!running_.load()) return false;
    if (proto_ == NetAudioProtocol::UDP) return true;
    return clientFd_.load() >= 0;
}

} // namespace dsp
} // namespace mbdsdr
