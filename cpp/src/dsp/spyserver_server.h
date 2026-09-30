// SPDX-License-Identifier: MIT
// SpyServer (Airspy / SDR++ compatible) *server*, clean-room re-implemented
// strictly from the wire layout reverse-engineered in
// docs/learn/spyserver.md (two independent open-source *client* implementations
// cross-checked). No GPL source was copied; every byte layout below is either
// marked double-client corroborated in the note, or honestly annotated as an
// unverified server-side guess that clients do not depend on.
//
// Model: one TCP listener (default 5555), currently a SINGLE client. Extra
// incoming connections are refused (multi-client arbitration via CLIENT_SYNC
// CanControl is explicitly unimplemented). All multi-byte integers are
// little-endian u32 on the wire (SDRangel's explicit encodeUInt32 + sdrpp's
// host-order memcpy on little-endian x86 -- double corroborated, note §2).
//
// Threading: this object lives in the GUI thread, which runs a Qt event loop.
// accept() and all socket reads/writes happen there. Real IQ is *pushed in*
// via feedIQ() -- the engine delivers it through a queued connection from its
// own thread, so no socket write ever blocks the engine's read loop.
#pragma once

#include <QObject>
#include <QString>
#include <vector>
#include <complex>
#include <functional>
#include <atomic>

class QTcpServer;
class QTcpSocket;

namespace mbdsdr {
namespace dsp {

// Where the server learns the current tuner state and forwards client-set
// parameters to the real engine. The UI wires these to SpectrumEngine slots.
// All callbacks are invoked on the GUI thread.
struct SpyServerTuner {
    // Client asked to retune the hardware center frequency (Hz).
    std::function<void(double hz)> setCenterFreq;
    // Client asked for a gain index 0..MaximumGainIndex (we map to dB).
    std::function<void(double db)> setGain;
    // Pull the current honest device state. Called at handshake time to build
    // DEVICE_INFO / CLIENT_SYNC, and again when re-syncing after a parameter
    // change. The server never invents a sample rate / frequency -- it reads
    // whatever the live source reports.
    std::function<void(double& maxSampleRateHz,
                       double& minFreqHz, double& maxFreqHz,
                       double& centerHz, double& gainDb)> queryInfo;
};

class SpyServerServer : public QObject {
    Q_OBJECT
public:
    explicit SpyServerServer(QObject* parent = nullptr);
    ~SpyServerServer() override;

    // Bind + listen. Pass port 0 to let the OS choose (then read port()).
    // Returns false (and leaves isListening() false) on bind failure -- e.g.
    // the port is already taken; the UI must surface that honestly, never fake
    // a "listening" state.
    bool start(quint16 port);
    void stop();
    bool isListening() const;
    // Actual bound port (0 before start / after stop).
    quint16 port() const;
    // Number of currently-connected command clients (0 or 1 in this build).
    int clientCount() const;

    void setTuner(SpyServerTuner tuner) { tuner_ = std::move(tuner); }

    // Deliver one real IQ block from the engine. Called on the GUI thread
    // (queued from the engine thread). If a client is connected, has completed
    // handshake, and enabled streaming, the block is decimated per the agreed
    // decimation stage, converted to the agreed IQ format, and pushed as a
    // 20-byte message header + interleaved I/Q body. No-op otherwise.
    // The data is the engine's REAL baseband (rtl_tcp source, or the honestly
    // labelled offline test signal) -- never synthesized here.
    void feedIQ(const std::vector<std::complex<float>>& iq,
                double sampleRateHz, double centerHz);

signals:
    // Listening state / bound port changed (UI status line).
    void listeningChanged(bool on, quint16 port);
    // Connected-client count changed (0/1 here).
    void clientCountChanged(int count);
    // The server asks the engine to (start/stop) copying its real IQ blocks
    // into feedIQ(). Off when no client is streaming so the idle path costs
    // nothing. The UI wires this to SpectrumEngine::setSpyServerTapRequested.
    void iqTapRequired(bool on);

private slots:
    void onNewConnection();
    void onClientReadyRead();
    void onClientDisconnected();

private:
    void resetClientState();
    void sendDeviceInfo();
    void sendClientSync();
    void dispatchCommand(quint32 cmd, const QByteArray& body);
    void onSetSetting(quint32 setting, quint32 value);
    // Write one message frame: 20B header + body. Little-endian u32 fields.
    void writeMessage(quint32 msgType, quint32 streamType,
                      const QByteArray& body);
    // Serialize `iq` (already at the agreed output rate after decimation) into
    // the current wire format (UInt8 offset-binary or Int16), interleaved I/Q.
    QByteArray serializeIQ(const std::vector<std::complex<float>>& iq) const;
    // Block-average-decimate by 2^k in place semantics -> out.
    static std::vector<std::complex<float>>
    decimateBy(const std::vector<std::complex<float>>& in, int powerOfTwo);

    QTcpServer* server_ = nullptr;
    QTcpSocket* client_  = nullptr;
    SpyServerTuner tuner_;

    // Per-connection protocol state.
    QByteArray recvBuf_;
    bool helloDone_ = false;
    bool streamingEnabled_ = false;
    // IQ wire format selector, decoded from SET_SETTING(IQ_FORMAT). We support
    // the two formats real clients actually use.
    enum class IqFmt { UInt8, Int16 } iqFmt_ = IqFmt::Int16;
    int decimation_ = 0;             // k: output rate = maxSampleRate >> k
    quint32 sequence_ = 0;          // message body sequence number
    double currentCenterHz_ = 0.0;
    double currentGainDb_ = 0.0;
    // Honest device descriptors snapshotted at handshake from queryInfo().
    double maxSampleRateHz_ = 0.0;
    double minFreqHz_ = 0.0;
    double maxFreqHz_ = 0.0;
    std::atomic<quint16> boundPort_{0};
};

} // namespace dsp
} // namespace mbdsdr
