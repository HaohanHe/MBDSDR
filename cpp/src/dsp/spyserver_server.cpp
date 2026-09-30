// SPDX-License-Identifier: MIT
#include "dsp/spyserver_server.h"

#include <QTcpServer>
#include <QTcpSocket>
#include <QHostAddress>
#include <QtEndian>
#include <cmath>
#include <algorithm>

namespace mbdsdr {
namespace dsp {

// ---- Wire constants (docs/learn/spyserver.md, §3/§4/§7/§10) --------------
// Command frame (client->server): [u32 CommandType][u32 BodySize][body].
static constexpr quint32 kCmdHello        = 0;
static constexpr quint32 kCmdSetSetting   = 2;
// static constexpr quint32 kCmdPing      = 3; // enumerated but never sent by
//                                             // either client; body unknown,
//                                             // intentionally unimplemented.

// SET_SETTING setting sub-numbers (§4.2).
static constexpr quint32 kSetStreamingMode = 0;
static constexpr quint32 kSetStreamingEn   = 1;
static constexpr quint32 kSetGain          = 2;
static constexpr quint32 kSetIqFormat      = 100; // 0x64
static constexpr quint32 kSetIqFrequency   = 101; // 0x65
static constexpr quint32 kSetIqDecimation  = 102; // 0x66

// Message frame (server->client): 20B header = 5 x u32 LE:
//   [0]ProtocolID [1]MessageType [2]StreamType [3]SequenceNumber [4]BodySize
static constexpr quint32 kMsgDeviceInfo = 0;
static constexpr quint32 kMsgClientSync = 1;
static constexpr quint32 kMsgUInt8Iq   = 100; // 0x64
static constexpr quint32 kMsgInt16Iq    = 101; // 0x65

// HELLO body carries this protocol version (note §5). Clients do not actually
// check the server's reply ProtocolID, but we echo it here for the header.
static constexpr quint32 kProtocolVersion = 0x020006A4u;

// DEVICE_INFO (§7.1): 12 x u32 = 48 bytes. Indices are aligned to the sdrpp
// struct field order that the SDR++ client parses in (SDRangel independently
// confirms the *count* = 12 u32 but marks several fields unused). Where the
// precise meaning of a slot is not double-corroborated we still keep the slot
// so offsets line up, and comment the choice honestly.
static constexpr int kDevInfoWords = 12;
static constexpr int kDevInfoBytes = kDevInfoWords * 4;
// Field slot indices (u32 index into the 12-word body).
static constexpr int kDiDeviceType      = 0;
static constexpr int kDiDeviceSerial   = 1;
static constexpr int kDiMaxSampleRate   = 2;
static constexpr int kDiMaxBandwidth   = 3;
static constexpr int kDiDecimStages     = 4;
static constexpr int kDiGainStages      = 5;
static constexpr int kDiMaxGainIndex   = 6;
static constexpr int kDiMinFreq        = 7;
static constexpr int kDiMaxFreq         = 8;
static constexpr int kDiResolution     = 9;
static constexpr int kDiMinIqDecim      = 10;
static constexpr int kDiForcedIqFormat = 11;

// CLIENT_SYNC (§7.2): 9 x u32 = 36 bytes.
static constexpr int kClientSyncWords = 9;
static constexpr int kClientSyncBytes = kClientSyncWords * 4;

namespace {
inline void putU32le(QByteArray& b, quint32 v) {
    uchar tmp[4];
    qToLittleEndian<quint32>(v, tmp);
    b.append(reinterpret_cast<const char*>(tmp), 4);
}
inline quint32 getU32le(const QByteArray& b, int off) {
    if (off + 4 > b.size()) return 0;
    return qFromLittleEndian<quint32>(reinterpret_cast<const uchar*>(b.constData() + off));
}
} // namespace

SpyServerServer::SpyServerServer(QObject* parent) : QObject(parent) {}

SpyServerServer::~SpyServerServer() {
    stop();
}

bool SpyServerServer::start(quint16 port) {
    if (!server_) {
        server_ = new QTcpServer(this);
        connect(server_, &QTcpServer::newConnection,
                this, &SpyServerServer::onNewConnection);
    }
    if (server_->isListening()) stop();

    if (!server_->listen(QHostAddress::Any, port)) {
        // Honest failure: bind failed (port taken / permission). Report 0.
        boundPort_.store(0);
        emit listeningChanged(false, 0);
        return false;
    }
    boundPort_.store(static_cast<quint16>(server_->serverPort()));
    emit listeningChanged(true, boundPort_.load());
    return true;
}

void SpyServerServer::stop() {
    if (client_) {
        // Tear down the single client without emitting a second tap signal.
        disconnect(client_, nullptr, this, nullptr);
        client_->disconnectFromHost();
        client_->deleteLater();
        client_ = nullptr;
        recvBuf_.clear();
        streamingEnabled_ = false;
        emit iqTapRequired(false);
        emit clientCountChanged(0);
    }
    if (server_) {
        server_->close();
    }
    boundPort_.store(0);
    emit listeningChanged(false, 0);
}

bool SpyServerServer::isListening() const {
    return server_ && server_->isListening();
}

quint16 SpyServerServer::port() const { return boundPort_.load(); }
int SpyServerServer::clientCount() const { return client_ ? 1 : 0; }

void SpyServerServer::resetClientState() {
    recvBuf_.clear();
    helloDone_ = false;
    streamingEnabled_ = false;
    iqFmt_ = IqFmt::Int16;
    decimation_ = 0;
    sequence_ = 0;
}

void SpyServerServer::onNewConnection() {
    while (server_->hasPendingConnections()) {
        QTcpSocket* next = server_->nextPendingConnection();
        if (client_) {
            // Single-client build: multi-client arbitration (one tuner +
            // CanControl) is explicitly unimplemented. Refuse the extra peer.
            next->disconnectFromHost();
            next->deleteLater();
            continue;
        }
        client_ = next;
        resetClientState();
        connect(client_, &QTcpSocket::readyRead,
                this, &SpyServerServer::onClientReadyRead);
        connect(client_, &QTcpSocket::disconnected,
                this, &SpyServerServer::onClientDisconnected);
        emit clientCountChanged(1);
    }
}

void SpyServerServer::onClientDisconnected() {
    if (client_) {
        client_->deleteLater();
        client_ = nullptr;
    }
    recvBuf_.clear();
    streamingEnabled_ = false;
    emit iqTapRequired(false);
    emit clientCountChanged(0);
}

void SpyServerServer::onClientReadyRead() {
    if (!client_) return;
    recvBuf_.append(client_->readAll());

    // Frame = 8B header [cmd][bodySize] + body. Parse all complete frames.
    while (recvBuf_.size() >= 8) {
        const quint32 cmd = getU32le(recvBuf_, 0);
        const quint32 bodySize = getU32le(recvBuf_, 4);
        // Guard against a bogus/huge bodySize so a malformed peer can't grow
        // the buffer without bound (clients use <= ~1 MiB anyway).
        if (bodySize > (1u << 20)) { client_->abort(); return; }
        if (static_cast<quint32>(recvBuf_.size()) < 8u + bodySize) break;
        const QByteArray body = recvBuf_.mid(8, bodySize);
        recvBuf_.remove(0, static_cast<int>(8u + bodySize));
        dispatchCommand(cmd, body);
    }
}

void SpyServerServer::dispatchCommand(quint32 cmd, const QByteArray& body) {
    if (cmd == kCmdHello) {
        // body = [u32 version][app name, no NUL]. We accept any version/name
        // (clients don't renegotiate); on first hello we snapshot device state
        // and answer D1=DEVICE_INFO + D2=CLIENT_SYNC (note §9).
        if (!helloDone_) {
            helloDone_ = true;
            if (tuner_.queryInfo) {
                tuner_.queryInfo(maxSampleRateHz_, minFreqHz_, maxFreqHz_,
                                 currentCenterHz_, currentGainDb_);
            }
            sendDeviceInfo();
            sendClientSync();
        }
        return;
    }
    if (cmd == kCmdSetSetting) {
        if (body.size() >= 8) {
            const quint32 setting = getU32le(body, 0);
            const quint32 value   = getU32le(body, 4);
            onSetSetting(setting, value);
        }
        return;
    }
    // Unknown / PING(3): unimplemented, ignore quietly (note §10.2).
}

void SpyServerServer::onSetSetting(quint32 setting, quint32 value) {
    switch (setting) {
    case kSetStreamingMode:
        // Bitmask IQ=1/AF=2/FFT=4. We only implement IQ; accept IQ-only(1)
        // and ignore anything else (note §11.1: FFT/AF out of scope).
        break;

    case kSetStreamingEn:
        streamingEnabled_ = (value == 1u);
        // Tell the engine whether to bother copying real IQ frames. When off
        // we stop pushing but keep the TCP connection (note §9).
        emit iqTapRequired(streamingEnabled_);
        break;

    case kSetGain: {
        // value is a gain *index* 0..MaximumGainIndex. Map to our dB range.
        const double db = static_cast<double>(value) / 29.0 * 49.6;
        currentGainDb_ = db;
        if (tuner_.setGain) tuner_.setGain(db);
        sendClientSync();
        break;
    }

    case kSetIqFormat: {
        // Two number spaces collide here: sdrpp sends the *format enum*
        // 1=UInt8/2=Int16/4=Float (note §4.2). We also tolerate the wire
        // message-type numbers 100/101/103 in case a client uses them, and
        // keep the current format for anything we don't implement (Float).
        if (value == 100u || value == 1u)      iqFmt_ = IqFmt::UInt8;
        else if (value == 101u || value == 2u) iqFmt_ = IqFmt::Int16;
        // 103/4 = Float: intentionally unsupported in this minimal loop.
        break;
    }

    case kSetIqFrequency:
        currentCenterHz_ = static_cast<double>(value);
        if (tuner_.setCenterFreq) tuner_.setCenterFreq(currentCenterHz_);
        sendClientSync();   // push new state (note §7.2)
        break;

    case kSetIqDecimation:
        decimation_ = static_cast<int>(std::clamp<quint32>(value, 0u, 16u));
        // Output rate = MaxSampleRate >> decimation (note §6); we actually
        // block-average-decimate the live IQ in feedIQ(), so the on-wire rate
        // really is derived from the real engine rate -- never faked.
        sendClientSync();
        break;

    default:
        // FFT_* (200..205), IQ_DIGITAL_GAIN(103): out of the minimal closed
        // loop; ignored (note §11.2).
        break;
    }
}

void SpyServerServer::sendDeviceInfo() {
    QByteArray body;
    body.reserve(kDevInfoBytes);
    // Zero the whole 48B first, then patch known fields by slot index so any
    // un-corroborated slot stays 0 rather than a guessed value.
    for (int i = 0; i < kDevInfoWords; ++i) putU32le(body, 0);
    auto setW = [&](int idx, quint32 v) {
        qToLittleEndian<quint32>(v, reinterpret_cast<uchar*>(body.data() + idx * 4));
    };
    // DeviceType=3 (RTLSDR) is the closest documented generic-IQ descriptor;
    // serial=0 / Resolution=16 are honest placeholders. No upstream byte-level
    // spec exists (note §1), so we align to the sdrpp client parse order.
    setW(kDiDeviceType,      3);
    setW(kDiDeviceSerial,    0);
    setW(kDiMaxSampleRate,   static_cast<quint32>(std::max(0.0, maxSampleRateHz_)));
    setW(kDiMaxBandwidth,    static_cast<quint32>(std::max(0.0, maxSampleRateHz_)));
    setW(kDiDecimStages,     9);   // RTL-SDR-class stage count (note §7.1)
    setW(kDiGainStages,      1);
    setW(kDiMaxGainIndex,    29);  // matches our GAIN index mapping
    setW(kDiMinFreq,         static_cast<quint32>(std::max(0.0, minFreqHz_)));
    setW(kDiMaxFreq,         static_cast<quint32>(std::max(0.0, maxFreqHz_)));
    setW(kDiResolution,      16);  // we offer Int16/IQ
    setW(kDiMinIqDecim,      0);
    setW(kDiForcedIqFormat,  0);  // 0 = not forced
    writeMessage(kMsgDeviceInfo, /*streamType=*/0, body);
}

void SpyServerServer::sendClientSync() {
    QByteArray body;
    body.reserve(kClientSyncBytes);
    for (int i = 0; i < kClientSyncWords; ++i) putU32le(body, 0);
    auto setW = [&](int idx, quint32 v) {
        qToLittleEndian<quint32>(v, reinterpret_cast<uchar*>(body.data() + idx * 4));
    };
    const quint32 gainIndex =
        static_cast<quint32>(std::lround(currentGainDb_ / 49.6 * 29.0));
    setW(0, 1);                              // CanControl
    setW(1, gainIndex);                      // current gain index
    setW(2, static_cast<quint32>(currentCenterHz_)); // DeviceCenterFrequency
    setW(3, static_cast<quint32>(currentCenterHz_)); // IQCenterFrequency
    setW(4, static_cast<quint32>(currentCenterHz_)); // FFTCenterFrequency
    setW(5, static_cast<quint32>(std::max(0.0, minFreqHz_)));
    setW(6, static_cast<quint32>(std::max(0.0, maxFreqHz_)));
    setW(7, static_cast<quint32>(std::max(0.0, minFreqHz_)));
    setW(8, static_cast<quint32>(std::max(0.0, maxFreqHz_)));
    writeMessage(kMsgClientSync, /*streamType=*/0, body);
}

void SpyServerServer::writeMessage(quint32 msgType, quint32 streamType,
                                   const QByteArray& body) {
    if (!client_) return;
    QByteArray hdr;
    hdr.reserve(20);
    putU32le(hdr, kProtocolVersion); // ProtocolID: clients don't check (note §8)
    putU32le(hdr, msgType);
    putU32le(hdr, streamType);
    putU32le(hdr, sequence_++);
    putU32le(hdr, static_cast<quint32>(body.size()));
    client_->write(hdr);
    if (!body.isEmpty()) client_->write(body);
}

std::vector<std::complex<float>>
SpyServerServer::decimateBy(const std::vector<std::complex<float>>& in, int k) {
    if (k <= 0 || in.empty()) return in;
    const std::size_t block = static_cast<std::size_t>(1) << k;
    std::vector<std::complex<float>> out;
    out.reserve(in.size() / block);
    for (std::size_t base = 0; base + block <= in.size(); base += block) {
        std::complex<double> acc(0, 0);
        for (std::size_t j = 0; j < block; ++j) acc += in[base + j];
        out.push_back(static_cast<std::complex<float>>(acc / static_cast<double>(block)));
    }
    return out;
}

QByteArray
SpyServerServer::serializeIQ(const std::vector<std::complex<float>>& iq) const {
    QByteArray out;
    if (iqFmt_ == IqFmt::UInt8) {
        // Offset-binary unsigned: (clamp(sample)*127) + 128. Decoding client
        // subtracts 128 (note §8.2, client.cpp:141).
        out.resize(static_cast<int>(iq.size() * 2));
        uchar* p = reinterpret_cast<uchar*>(out.data());
        for (const auto& c : iq) {
            auto enc = [](float v) -> uchar {
                v = std::clamp(v, -1.0f, 1.0f);
                return static_cast<uchar>(std::lround(v * 127.0f) + 128);
            };
            *p++ = enc(c.real());
            *p++ = enc(c.imag());
        }
    } else {
        // Int16, little-endian signed interleaved I/Q (note §8.2).
        out.resize(static_cast<int>(iq.size() * 4));
        uchar* p = reinterpret_cast<uchar*>(out.data());
        for (const auto& c : iq) {
            auto enc = [](float v) -> qint16 {
                v = std::clamp(v, -1.0f, 1.0f);
                return static_cast<qint16>(std::lround(v * 32767.0f));
            };
            const qint16 i = enc(c.real());
            const qint16 q = enc(c.imag());
            qToLittleEndian<qint16>(i, p); p += 2;
            qToLittleEndian<qint16>(q, p); p += 2;
        }
    }
    return out;
}

void SpyServerServer::feedIQ(const std::vector<std::complex<float>>& iq,
                             double /*sampleRateHz*/, double /*centerHz*/) {
    if (!client_ || !helloDone_ || !streamingEnabled_ || iq.empty()) return;
    // Real decimation: the pushed rate really is maxSampleRate >> decimation.
    const std::vector<std::complex<float>> out = decimateBy(iq, decimation_);
    if (out.empty()) return;
    const QByteArray body = serializeIQ(out);
    // High 16 bits of MessageType carry gain dB (only sdrpp reads it; SDRangel
    // ignores -- note §8.3). We fill it honestly from the current gain.
    const quint32 lowType = (iqFmt_ == IqFmt::UInt8) ? kMsgUInt8Iq : kMsgInt16Iq;
    const quint32 gainDb  = static_cast<quint32>(std::lround(currentGainDb_));
    const quint32 msgType = (gainDb << 16) | lowType;
    writeMessage(msgType, /*streamType=*/1, body);
}

} // namespace dsp
} // namespace mbdsdr
