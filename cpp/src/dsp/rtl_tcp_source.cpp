// SPDX-License-Identifier: MIT
#include "rtl_tcp_source.h"

#include <QHostAddress>
#include <QDateTime>
#include <cstring>

namespace mbdsdr {
namespace dsp {

RtlTcpSource::RtlTcpSource(QString host, quint16 port)
    : host_(std::move(host)), port_(port) {}

RtlTcpSource::~RtlTcpSource() { stop(); }

bool RtlTcpSource::start() {
    sock_ = new QTcpSocket();
    sock_->connectToHost(QHostAddress(host_), port_);
    if (!sock_->waitForConnected(2000)) {
        sock_->deleteLater();
        sock_ = nullptr;
        return false;   // honest failure -- caller shows a status message
    }
    sendCmd(0x02, static_cast<quint32>(rateHz_));   // set sample rate
    sendCmd(0x01, static_cast<quint32>(freqHz_));   // set center freq
    return true;
}

void RtlTcpSource::stop() {
    if (sock_) {
        sock_->disconnectFromHost();
        sock_->deleteLater();
        sock_ = nullptr;
    }
}

bool RtlTcpSource::isConnected() const {
    return sock_ && sock_->state() == QAbstractSocket::ConnectedState;
}

void RtlTcpSource::sendCmd(quint8 cmd, quint32 arg) {
    if (!isConnected()) return;
    // rtl_tcp command: 1 byte cmd, 4 bytes big-endian arg.
    unsigned char buf[5] = {cmd,
        static_cast<unsigned char>((arg >> 24) & 0xff),
        static_cast<unsigned char>((arg >> 16) & 0xff),
        static_cast<unsigned char>((arg >>  8) & 0xff),
        static_cast<unsigned char>( arg        & 0xff)};
    sock_->write(reinterpret_cast<const char*>(buf), 5);
}

std::size_t RtlTcpSource::readIQ(std::vector<std::complex<float>>& out) {
    if (!isConnected()) return 0;
    const int want = static_cast<int>(out.size());
    if (want <= 0) return 0;
    QByteArray data;
    while (data.size() < want * 2) {
        if (!sock_->waitForReadyRead(50)) break;
        data += sock_->readAll();
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
