// SPDX-License-Identifier: MIT
// rtl_tcp network source: IQ from an rtl_tcp server (rtl-sdr over TCP).
// Protocol: 8-byte requests (1-byte command + 4-byte big-endian arg),
// stream of 8-bit unsigned IQ (offset 127). Honest failure: if connect()
// fails, isConnected() stays false and readIQ returns 0 -- no fake data.
//
// Transport is a native (POSIX) socket, NOT QTcpSocket: IQ is read from the
// DSP engine's run() thread while connect happens on the UI thread, and
// QTcpSocket must not be touched from a thread other than its creator
// (QSocketNotifier is wired to the creator's event loop). A plain blocking
// fd is safe to poll/recv from any thread and keeps the engine thread free
// of Qt event-loop requirements.
#pragma once

#include "dsp/source.h"
#include <QString>
#include <atomic>

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
    // rtl_tcp command 0x03 selects gain mode: 0 = manual, 1 = AGC.  The daemon
    // exposes no separate RTL2832 IF-AGC command, so both map to 0x03.
    void setRtlAgc(bool on) override;
    void setTunerAgc(bool on) override;
    double centerFreq() const override { return freqHz_; }
    double sampleRate() const override { return rateHz_; }
    double gain() const override { return gainDb_; }
    QString name() const override { return QString("rtl_tcp %1:%2").arg(host_).arg(port_); }
    // True while the native fd is open and the peer has not closed/reset.
    // After an EOF/RST it flips false; the engine treats sustained zero
    // reads as a drop (see realSourceActive_ in SpectrumEngine).
    bool isConnected() const override;
    // Real device identity read from the 12-byte "RTL0" handshake the daemon
    // sends on accept (tuner type + gain count). No handshake parsed -> the
    // ranges stay 0/未知 rather than guessed.
    DeviceCapabilities capabilities() const override;
    // Human-readable reason of the last failed start() (socket error string,
    // "Connection refused", "timed out", ...). Empty when start() succeeded.
    QString lastError() const { return lastError_; }

private:
    void sendCmd(quint8 cmd, quint32 arg);

protected:
    // Test seam: override to observe the (cmd, arg) frames that WOULD be sent
    // (before the fd write).  The base implementation does nothing.
    virtual void commandLogged(quint8 /*cmd*/, quint32 /*arg*/) {}
    // Read (and parse, if present) the 12-byte RTL0 dongle-info header the
    // daemon sends immediately on accept. Tolerates a missing header (mock /
    // legacy server) without stalling connect().
    void readDongleInfo();
    void setFdBlocking(qintptr fd, bool blocking);
    QString host_;
    quint16 port_;
    std::atomic<qintptr> fd_{-1};   // native socket; -1 = closed (qintptr fits SOCKET)
    std::atomic<bool> eof_{false};  // peer closed / reset detected
    QString lastError_;
    double freqHz_ = 98.5e6;
    double rateHz_ = 2.4e6;
    double gainDb_ = 0.0;
    int  tunerTypeRaw_ = -1;        // decoded from handshake; -1 = unknown
    int  tunerGainCount_ = 0;       // decoded from handshake; 0 = not reported
    bool headerKnown_ = false;      // parsed a real "RTL0" magic header
};

} // namespace dsp
} // namespace mbdsdr
