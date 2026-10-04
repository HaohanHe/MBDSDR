// SPDX-License-Identifier: MIT
//
// NetworkAudioSink: stream the demodulated 48 kHz audio out to the network.
// Benchmarked on the SDR++ "network_sink" module (GPLv3, mechanism only, never
// copied): raw 16-bit signed little-endian PCM frames over either
//
//   * UDP -- a connected datagram socket sending to host:port (the default in
//     SDR++); or
//   * TCP -- a listening server socket that serves ONE client at a time and
//     re-listens as soon as the client leaves.
//
// The float32 [-1,1] stream is converted to int16 by scale 32768.0 (same as
// SDR++ volk_32f_s32f_convert_16i), clamp-rounded to [-32768, 32767]. No
// header/wrapping -- a listener sees a bare PCM stream (e.g. feed it into
// `sox -t raw -r 48000 -e signed -b 16 -c 1 -` or any VLC raw-PCM input).
//
// Honest-state contract (no fake audio):
//   * Before start()/after stop(): write() is a no-op and nothing is ever sent.
//   * UDP with nobody listening: datagrams are still emitted (connectionless);
//     a hard ICMP rejection surfaces in lastError() / sendErrors_.
//   * TCP with no client attached: frames are counted as dropped, never
//     buffered or fabricated; the state reads back "listening, no client".
//   * A broken TCP client is detected by the accept thread (recv <= 0 / keepalive
//     timeout), the socket is reaped, and clientConnected() goes false again;
//     send errors are counted honestly instead of being retried into the void.
//
// Threading: write() MUST be called from the DSP worker thread (IAudioSink
// contract). start()/stop() are called from the UI/test thread. The socket
// critical section is tiny and mutex-guarded; the DSP thread is never blocked
// on a stuck peer longer than the SO_SNDTIMEO below.
#pragma once

#include "iaudio_sink.h"

#include <atomic>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace mbdsdr {
namespace dsp {

enum class NetAudioProtocol { UDP, TCP };

class NetworkAudioSink : public IAudioSink {
public:
    NetworkAudioSink();
    ~NetworkAudioSink() override;

    NetworkAudioSink(const NetworkAudioSink&) = delete;
    NetworkAudioSink& operator=(const NetworkAudioSink&) = delete;

    // Open the network endpoint and start streaming.
    //   UDP: `host` is the dotted-quad IPv4 target, `port` the destination port.
    //   TCP: listens on all interfaces on `port`; the first connecting client
    //        owns the stream until it disconnects.
    // `stereo` selects interleaved L/R int16 framing in writeStereo(); otherwise
    // the default (L+R)/2 downmix ships mono.
    // Returns false on any socket/bind failure and fills lastError() with the
    // real reason (errno string) -- never silently pretends to be streaming.
    bool start(const std::string& host, uint16_t port,
               NetAudioProtocol proto, bool stereo = false);
    // Close every socket and stop the accept thread. Safe to call twice.
    void stop();

    // ---- IAudioSink -------------------------------------------------------
    void write(const std::vector<float>& audio) override;
    void writeStereo(const std::vector<float>& left,
                      const std::vector<float>& right) override;
    void setVolume(float v) override;
    void setMuted(bool m) override;
    // True once start() succeeded and stop() has not been called -- even on TCP
    // before any client connects (we ARE serving; we just have nobody).
    bool isAvailable() const override;
    QStringList outputDevices() const override;   // empty: not hardware.
    QString currentDeviceName() const override;

    // ---- Honest state read-back ------------------------------------------
    bool running() const { return running_.load(); }
    // UDP: true whenever running. TCP: true only while a client is attached.
    bool clientConnected() const;
    // Bytes handed to the kernel (sent on UDP, handed off on TCP).
    uint64_t bytesSent() const { return bytesSent_.load(); }
    // Frames dropped because nothing was attached (TCP: no client yet).
    uint64_t framesDropped() const { return framesDropped_.load(); }
    // Frames rejected by the kernel on send (broken pipe / ICMP reject).
    uint64_t sendErrors() const { return sendErrors_.load(); }
    // Last honest failure reason (open, send or accept-side). Empty = none.
    std::string lastError() const;
    NetAudioProtocol protocol() const { return proto_; }
    // TCP: the port actually bound after listen (honest read-back, 0 before
    // start). UDP: echoes the destination port the stream was told to use.
    uint16_t actualPort() const { return actualPort_.load(); }

private:
    void acceptLoop();
    // Convert one mono block and hand it to the socket layer. Returns false when
    // the frame could not be sent (caller already counted it).
    bool sendMono(const float* data, std::size_t n);
    void setLastErrorLocked(const std::string& what);

    NetAudioProtocol proto_ = NetAudioProtocol::UDP;
    bool stereo_ = false;
    std::atomic<bool> running_{false};
    std::atomic<bool> stopAccept_{false};

    int sendFd_ = -1;          // UDP: connected datagram socket
    int listenFd_ = -1;        // TCP: listening socket
    std::atomic<int> clientFd_{-1};  // TCP: currently attached client
    std::thread acceptThread_;

    // Guards fd lifecycle in start()/stop() against the DSP write() path.
    std::mutex mtx_;
    std::string targetHost_;
    uint16_t targetPort_ = 0;
    std::atomic<uint16_t> actualPort_{0};

    std::atomic<float> volume_{1.0f};
    std::atomic<bool> muted_{false};

    std::atomic<uint64_t> bytesSent_{0};
    std::atomic<uint64_t> framesDropped_{0};
    std::atomic<uint64_t> sendErrors_{0};
    std::string lastError_;
    std::mutex errMtx_;

    // Conversion scratch. write() is the ONLY caller (DSP thread contract), so
    // this needs no lock of its own.
    std::vector<int16_t> convBuf_;
};

} // namespace dsp
} // namespace mbdsdr
