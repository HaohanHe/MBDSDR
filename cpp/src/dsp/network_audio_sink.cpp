// SPDX-License-Identifier: MIT
#include "network_audio_sink.h"

#ifdef _WIN32
#  include <winsock2.h>
#  include <ws2tcpip.h>
// wingdi.h #define's DeviceCapabilities to DeviceCapabilitiesW; undef to avoid
// collisions with any same-named type pulled via Qt headers.
#  undef DeviceCapabilities
#else
#  include <arpa/inet.h>
#  include <errno.h>
#  include <fcntl.h>
#  include <netinet/in.h>
#  include <poll.h>
#  include <sys/socket.h>
#  include <unistd.h>
#endif

#include <algorithm>
#include <chrono>
#include <cstring>
#include <mutex>

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

// Thin native-socket abstraction so the transport logic is identical on both
// platforms (same style as rtl_tcp_source.cpp).
#ifdef _WIN32
using socket_t = SOCKET;
using pollfd_t = WSAPOLLFD;
const socket_t kInvalidSocket = INVALID_SOCKET;
inline int  lastSocketErr() { return WSAGetLastError(); }
inline void closeSocket(SOCKET s) { ::closesocket(s); }
inline int  doPoll(WSAPOLLFD* fds, int n, int ms) { return ::WSAPoll(fds, n, ms); }
inline bool socketWouldBlock(int e) { return e == WSAEWOULDBLOCK; }
inline bool socketInterrupted(int e) { return e == WSAEINTR; }
// A blocking recv/send bounded by SO_RCVTIMEO/SO_SNDTIMEO reports WSAETIMEDOUT
// on Winsock (POSIX reports EAGAIN, already covered by socketWouldBlock). For
// the accept-loop idle watch this is benign: the client is simply quiet.
inline bool socketTimedOut(int e) { return e == WSAETIMEDOUT; }
inline bool socketBrokenPipe(int e) {
    // Winsock has no EPIPE/SIGPIPE; a locally-aborted connection is the closest
    // semantic (hard peer-reset is WSAECONNRESET, handled separately).
    return e == WSAECONNABORTED;
}
inline bool socketConnReset(int e) { return e == WSAECONNRESET; }
constexpr int kSocketCloexec = 0;    // no SOCK_CLOEXEC on Winsock
constexpr int kMsgNoSignal   = 0;    // Winsock never raises SIGPIPE
constexpr int kShutBoth      = SD_BOTH;   // POSIX kShutBoth
inline void ensureSocketStack() {
    static std::once_flag once;
    std::call_once(once, [] { WSADATA d; WSAStartup(MAKEWORD(2, 2), &d); });
}

// Winsock SO_RCVTIMEO/SO_SNDTIMEO take a DWORD of milliseconds (POSIX takes a
// struct timeval), so the timeout setsockopt is platform-specific.
inline void setSocketRcvTimeout(SOCKET s, int ms) {
    DWORD v = static_cast<DWORD>(ms);
    ::setsockopt(s, SOL_SOCKET, SO_RCVTIMEO,
                 reinterpret_cast<const char*>(&v), sizeof(v));
}
inline void setSocketSendTimeout(SOCKET s, int ms) {
    DWORD v = static_cast<DWORD>(ms);
    ::setsockopt(s, SOL_SOCKET, SO_SNDTIMEO,
                 reinterpret_cast<const char*>(&v), sizeof(v));
}

std::string socketErrText(int e) {
    LPWSTR buf = nullptr;
    const DWORD n = ::FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
            FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr, static_cast<DWORD>(e),
        MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT),
        reinterpret_cast<LPWSTR>(&buf), 0, nullptr);
    std::string out;
    if (n && buf) {
        const int len = ::WideCharToMultiByte(
            CP_UTF8, 0, buf, static_cast<int>(n), nullptr, 0, nullptr, nullptr);
        out.resize(len);
        ::WideCharToMultiByte(CP_UTF8, 0, buf, static_cast<int>(n),
                              out.data(), len, nullptr, nullptr);
        while (!out.empty() && (out.back() == '\r' || out.back() == '\n' ||
                                out.back() == ' '))
            out.pop_back();
        LocalFree(buf);
    }
    if (out.empty()) out = "socket error " + std::to_string(e);
    return out;
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
inline bool socketTimedOut(int) { return false; }  // POSIX timeout -> EAGAIN
inline bool socketBrokenPipe(int e) { return e == EPIPE; }
inline bool socketConnReset(int e) { return e == ECONNRESET; }
constexpr int kSocketCloexec = SOCK_CLOEXEC;
constexpr int kMsgNoSignal   = MSG_NOSIGNAL;
constexpr int kShutBoth      = SHUT_RDWR;
inline void ensureSocketStack() {}

inline void setSocketRcvTimeout(int s, int ms) {
    struct timeval tv {};
    tv.tv_sec = ms / 1000;
    tv.tv_usec = (ms % 1000) * 1000;
    ::setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
}
inline void setSocketSendTimeout(int s, int ms) {
    struct timeval tv {};
    tv.tv_sec = ms / 1000;
    tv.tv_usec = (ms % 1000) * 1000;
    ::setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
}

std::string socketErrText(int e) { return std::strerror(e); }
#endif

socket_t asSocket(qintptr v) { return static_cast<socket_t>(v); }
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

    ensureSocketStack();
    if (proto_ == NetAudioProtocol::UDP) {
        socket_t fd = ::socket(AF_INET, SOCK_DGRAM | kSocketCloexec, 0);
        if (fd == kInvalidSocket) {
            setLastErrorLocked(std::string("socket: ") +
                               socketErrText(lastSocketErr()));
            return false;
        }
        sockaddr_in dst{};
        dst.sin_family = AF_INET;
        dst.sin_port = htons(port);
        if (::inet_pton(AF_INET, host.c_str(), &dst.sin_addr) != 1) {
            closeSocket(fd);
            setLastErrorLocked("bad destination address: " + host);
            return false;
        }
        // Bounded transmit wait: a wedged peer stalls the DSP thread for at most
        // kSendTimeoutMs, then the frame is dropped + counted honestly.
        setSocketSendTimeout(fd, kSendTimeoutMs);
        // Connected UDP: send() instead of sendto(), and ICMP port-unreachables
        // surface back as ECONNREFUSED on send (honest error instead of silently
        // vanishing datagrams).
        if (::connect(fd, reinterpret_cast<sockaddr*>(&dst), sizeof(dst)) != 0) {
            const int e = lastSocketErr();
            closeSocket(fd);
            setLastErrorLocked(std::string("connect(udp): ") + socketErrText(e));
            return false;
        }
        sendFd_ = static_cast<qintptr>(fd);
        actualPort_.store(port);
    } else {
        socket_t lfd = ::socket(AF_INET, SOCK_STREAM | kSocketCloexec, 0);
        if (lfd == kInvalidSocket) {
            setLastErrorLocked(std::string("socket: ") +
                               socketErrText(lastSocketErr()));
            return false;
        }
        int one = 1;
#ifdef _WIN32
        // Winsock SO_REUSEADDR would let a second ACTIVE listener bind the same
        // port (and enables port hijacking). Demand exclusive use so a duplicate
        // bind fails honestly (WSAEADDRINUSE). Must be set before bind().
        ::setsockopt(lfd, SOL_SOCKET, SO_EXCLUSIVEADDRUSE,
                     reinterpret_cast<const char*>(&one), sizeof(one));
#else
        ::setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR,
                     reinterpret_cast<const char*>(&one), sizeof(one));
#endif
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_ANY);
        addr.sin_port = htons(port);
        if (::bind(lfd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
            const int e = lastSocketErr();
            closeSocket(lfd);
            setLastErrorLocked(std::string("bind: ") + socketErrText(e));
            return false;
        }
        if (::listen(lfd, 1) != 0) {
            const int e = lastSocketErr();
            closeSocket(lfd);
            setLastErrorLocked(std::string("listen: ") + socketErrText(e));
            return false;
        }
        listenFd_ = static_cast<qintptr>(lfd);
        {
            sockaddr_in actual{};
#ifdef _WIN32
            int alen = sizeof(actual);
#else
            socklen_t alen = sizeof(actual);
#endif
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
        if (sendFd_ >= 0) {
            closeSocket(asSocket(sendFd_));
            sendFd_ = -1;
        }
        if (listenFd_ >= 0) {
            // Wake the poll()ing accept loop.
            ::shutdown(asSocket(listenFd_), kShutBoth);
            closeSocket(asSocket(listenFd_));
            listenFd_ = -1;
        }
        const qintptr c = clientFd_.exchange(-1);
        if (c >= 0) {
            ::shutdown(asSocket(c), kShutBoth);
            closeSocket(asSocket(c));
        }
        running_.store(false);
    }
    if (acceptThread_.joinable()) acceptThread_.join();
}

void NetworkAudioSink::acceptLoop() {
    while (!stopAccept_.load()) {
        pollfd_t pfd;
        std::memset(&pfd, 0, sizeof(pfd));
        pfd.fd = asSocket(listenFd_);
        pfd.events = POLLIN;
        const int pr = doPoll(&pfd, 1, kAcceptPollMs);
        if (pr < 0) {
            if (socketInterrupted(lastSocketErr())) continue;
            break;
        }
        if (pr == 0) continue;   // timeout: re-check stopAccept_

        sockaddr_in cli{};
#ifdef _WIN32
        int clen = sizeof(cli);
#else
        socklen_t clen = sizeof(cli);
#endif
        socket_t cfd = ::accept(asSocket(listenFd_),
                                reinterpret_cast<sockaddr*>(&cli), &clen);
        if (cfd == kInvalidSocket) {
            const int e = lastSocketErr();
            if (socketInterrupted(e) || socketWouldBlock(e)) continue;
            if (stopAccept_.load()) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
            continue;
        }

        // Bounded recv wait so a half-dead client is reaped within ~1 s even if
        // no FIN ever arrives; keepalive catches the rest.
        setSocketRcvTimeout(cfd, kClientRcvTimeoutMs);
        setSocketSendTimeout(cfd, kSendTimeoutMs);
        const char one = 1;
        ::setsockopt(cfd, SOL_SOCKET, SO_KEEPALIVE, &one, sizeof(one));

        {
            std::lock_guard<std::mutex> lk(mtx_);
            // Single-client policy (like SDR++): a second evicts the first.
            const qintptr cfdq = static_cast<qintptr>(cfd);
            const qintptr old = clientFd_.exchange(cfdq);
            if (old >= 0) {
                ::shutdown(asSocket(old), kShutBoth);
                closeSocket(asSocket(old));
            }
        }

        // Block here until this client leaves.
        char b;
        while (!stopAccept_.load()) {
            const int r = static_cast<int>(::recv(cfd, &b, 1, 0));
            if (r == 0) break;                 // orderly close
            if (r < 0) {
                const int e = lastSocketErr();
                if (socketInterrupted(e)) continue;
                if (socketWouldBlock(e) || socketTimedOut(e)) {
                    // Idle timeout (POSIX EAGAIN / Winsock WSAETIMEDOUT): still
                    // ours? then keep waiting for the client to close.
                    if (clientFd_.load() != static_cast<qintptr>(cfd)) break;
                    continue;
                }
                break;                         // RST / hard error
            }
            // Data from the peer: the wire is push-only (we never expect
            // commands) -- ignore and keep watching for the close.
        }

        {
            std::lock_guard<std::mutex> lk(mtx_);
            if (clientFd_.load() == static_cast<qintptr>(cfd))
                clientFd_.store(-1);
        }
        closeSocket(cfd);
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

    qintptr fdq = -1;
    if (proto_ == NetAudioProtocol::UDP) fdq = sendFd_;
    else fdq = clientFd_.load();

    if (fdq < 0) {
        // TCP with nobody attached: honest drop, never fabricate audio.
        framesDropped_.fetch_add(1);
        return false;
    }
    const socket_t fd = asSocket(fdq);

    const std::size_t total = n * sizeof(int16_t);
    const char* ptr = reinterpret_cast<const char*>(convBuf_.data());
    std::size_t off = 0;
    while (off < total) {
        const int s = static_cast<int>(
            ::send(fd, ptr + off, static_cast<int>(total - off), kMsgNoSignal));
        if (s <= 0) {
            const int e = lastSocketErr();
            sendErrors_.fetch_add(1);
            setLastErrorLocked(std::string("send: ") + socketErrText(e));
            if (proto_ == NetAudioProtocol::TCP &&
                (socketBrokenPipe(e) || socketConnReset(e))) {
                // Dead peer: detach so the state reads honestly. The accept
                // thread will reap the fd (its recv watch has already fired).
                qintptr expected = fdq;
                clientFd_.compare_exchange_strong(expected, -1);
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
