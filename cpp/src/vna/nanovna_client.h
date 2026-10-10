// SPDX-License-Identifier: MIT
// NanoVNA (H / H4) text-protocol client — clean-room, self-written.
//
// Protocol facts (command words / data framing / prompt timing) were learned
// from public upstream (ttrftech/NanoVNA firmware, nanovna-saver) as a
// functional interop interface (like AT/SCPI); the implementation below is
// independently written and copies no upstream code.
//
// Wire format: USB CDC 115200 8N1; commands end in '\r'; reply lines end in
// "\r\n"; one response round ends at the "ch>" prompt; `data N` yields one
// "re im" complex pair per line.
//
// Design mirrors the Python reference (mbdsdr_ai/nanovna_client.py):
//   * pluggable transport so a scripted replay drives deterministic tests with
//     no hardware;
//   * honest empty state: with no open transport, isConnected()==false and all
//     data reads back as empty — nothing is fabricated.
#pragma once

#include <QByteArray>
#include <QString>
#include <QStringList>

#include <complex>
#include <memory>
#include <vector>

namespace mbdsdr {
namespace vna {

// Standard reference impedance (Ohm).
constexpr double kRefImpedanceOhm = 50.0;

// ---- RF conversion pure functions (general engineering formulas) ----------
// All are self-written from the closed forms; nothing device-specific.
double returnLossDb(std::complex<double> s11);            // +inf when |g|==0
double vswr(std::complex<double> s11);                     // +inf when |g|>=1, 1.0 when g==0
std::complex<double> s11ToImpedance(std::complex<double> s11,
                                     double z0 = kRefImpedanceOhm);
double s21GainDb(std::complex<double> s21);               // -inf when |a|==0
double s21PhaseDeg(std::complex<double> s21);              // atan2(im,re) in degrees

// ---- Pluggable transport --------------------------------------------------
class VnaTransport {
public:
    virtual ~VnaTransport() = default;
    virtual bool open() = 0;
    virtual void close() = 0;
    virtual bool isOpen() const = 0;
    virtual QString errorString() const = 0;
    virtual void write(const QByteArray& data) = 0;
    // Read one '\n'-terminated line; trailing '\r' stripped. false on EOF/closed.
    virtual bool readLine(QByteArray& out) = 0;
    virtual void resetInputBuffer() = 0;
};

// Real termios serial backend (115200 8N1). Fail-soft: missing device yields
// open()==false + errorString(), never throws. On non-Unix builds open()
// honestly reports unsupported so the code still compiles/runs.
std::unique_ptr<VnaTransport> createSerialTransport(const QString& device);

// ---- Client ----------------------------------------------------------------
class NanoVnaClient {
public:
    // No transport injected -> honest empty state (isConnected()==false).
    NanoVnaClient();
    explicit NanoVnaClient(std::unique_ptr<VnaTransport> transport);
    ~NanoVnaClient();

    // Open the transport and run the help/version/info handshake. Returns false
    // (and clears state) on any failure; never throws.
    bool connect();
    // Line-B UI entry: open a real serial device (termios 115200 8N1) and run
    // the help/version/info handshake. Honest: returns false on open failure.
    bool connectSerial(const QString& device);
    void close();
    bool isConnected() const { return connected_; }

    // Readback state (honest empty when disconnected).
    QString model() const { return model_; }          // info first line (board name)
    QString version() const { return version_; }      // version reply first line
    QStringList calStatus() const { return cal_; }    // space-split set items
    bool hasSweep() const { return hasSweep_; }
    long sweepStartHz() const { return sStartHz_; }
    long sweepStopHz() const { return sStopHz_; }
    int sweepPoints() const { return sPoints_; }

    // Business commands; each returns false (with *err set) when disconnected or
    // rejected. setSweep validates stop>start and points>0 before sending.
    bool setSweep(long startHz, long stopHz, int points, QString* err);
    std::vector<long> readFrequencies();
    // channel 0 -> S11, channel 1 -> S21.
    std::vector<std::complex<double>> readData(int channel);
    QStringList readCalStatus();

private:
    QList<QString> exec(const QString& command);

    std::unique_ptr<VnaTransport> tr_;
    bool connected_ = false;
    QString version_;
    QString model_;
    QStringList cal_;
    bool hasSweep_ = false;
    long sStartHz_ = 0;
    long sStopHz_ = 0;
    int sPoints_ = 0;
};

} // namespace vna
} // namespace mbdsdr
