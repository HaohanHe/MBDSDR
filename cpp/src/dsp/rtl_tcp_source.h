// SPDX-License-Identifier: MIT
// rtl_tcp network source: IQ from an rtl_tcp server (rtl-sdr over TCP).
// Protocol: 8-byte requests (1-byte command + 4-byte big-endian arg),
// stream of 8-bit unsigned IQ (offset 127). Honest failure: if connect()
// fails, isConnected() stays false and readIQ returns 0 -- no fake data.
#pragma once

#include "dsp/source.h"
#include <QTcpSocket>
#include <QString>

namespace mbdsdr {
namespace dsp {

class RtlTcpSource : public ISource {
public:
    RtlTcpSource(QString host = "127.0.0.1", quint16 port = 1234);
    ~RtlTcpSource() override;

    bool start() override;
    void stop() override;
    std::size_t readIQ(std::vector<std::complex<float>>& out) override;
    void setCenterFreq(double freqHz) override;
    void setSampleRate(double rateHz) override;
    void setGain(double gainDb) override;
    double centerFreq() const override { return freqHz_; }
    double sampleRate() const override { return rateHz_; }
    double gain() const override { return gainDb_; }
    QString name() const override { return QString("rtl_tcp %1:%2").arg(host_).arg(port_); }
    bool isConnected() const override;

private:
    void sendCmd(quint8 cmd, quint32 arg);
    QString host_;
    quint16 port_;
    QTcpSocket* sock_ = nullptr;
    double freqHz_ = 98.5e6;
    double rateHz_ = 2.4e6;
    double gainDb_ = 0.0;
};

} // namespace dsp
} // namespace mbdsdr
