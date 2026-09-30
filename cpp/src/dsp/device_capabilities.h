// SPDX-License-Identifier: MIT
// Device capability readback for the UI device-info panel and the dynamic
// sample-rate combo.
//
// HONESTY MODEL
// -------------
// Nothing here is fabricated. For an rtl_tcp source the only device identity
// the wire actually carries is the 12-byte "RTL0" dongle-info handshake that
// librtlsdr's rtl_tcp daemon sends immediately on accept
// (repos/librtlsdr/src/rtl_tcp.c:618-629): magic "RTL0" + big-endian tuner
// type (enum rtlsdr_tuner, rtl-sdr.h:172-180) + big-endian gain count.
//
//   * Device name / tuner identity  <- parsed from that real handshake.
//   * Tunable range                 <- the per-tuner constants the C driver
//                                      itself uses (tuner_e4k.c / tuner_r82xx.c),
//                                      mirrored in docs/learn/librtlsdr.md.
//                                      A tuner with no driver-known range
//                                      (Unknown / FC2580) reports 0 = "未知"
//                                      rather than a guessed number.
//   * Sample-rate range             <- the real RTL2832U accept rule from
//                                      librtlsdr.c:1100-1102: valid iff
//                                      rate>225k && rate<=3.2M &&
//                                      NOT(300k<rate<=900k) (the dead band).
//
// There is no SoapySDR RX backend in this tree (only a TX mirror in
// src/tx/soapy_tx_backend.cpp), so enumerate()/probeDevice() are not used;
// the rtl_tcp handshake above is the only real identity source. When no real
// device is connected, the struct is the honest empty state (connected=false,
// ranges 0) -- the UI then shows "RTL-SDR 未连接" and disables the combo.
#pragma once

#include <QString>
#include <QList>

namespace mbdsdr {
namespace dsp {

// Mirrors enum rtlsdr_tuner (repos/librtlsdr/include/rtl-sdr.h:172-180). The
// numeric values are exactly what the rtl_tcp handshake puts on the wire.
enum class RtlTuner {
    Unknown = 0,
    E4000   = 1,
    FC0012  = 2,
    FC0013  = 3,
    FC2580  = 4,
    R820T   = 5,
    R828D   = 6,
};

// Real, read-back device capabilities. Zero/empty fields mean "unknown /
// not reported" -- never a placeholder number.
struct DeviceCapabilities {
    bool    connected = false;      // real hardware actually streaming
    QString deviceName;             // e.g. "RTL-SDR · R820T"
    double  tunableMinHz = 0.0;    // 0 when the driver table has no entry
    double  tunableMaxHz = 0.0;
    double  sampleRateMinHz = 0.0;  // real RTL2832U bounds
    double  sampleRateMaxHz = 0.0;
    QString provenance;             // where these numbers came from (honest note)
};

// Human tuner name for the handshake enum. Unknown -> "未知调谐器".
QString rtlTunerName(RtlTuner t);

// Build capabilities from the raw rtl_tcp handshake fields.
//   tunerTypeRaw  = the big-endian-decoded uint32 tuner type from the wire
//   gainCount     = the decoded uint32 tuner gain count (0 = not reported)
//   endpoint      = "host:port" purely for the provenance note
// If tunerTypeRaw is not a known enum value the tunable range stays 0 (未知).
DeviceCapabilities rtlCapabilitiesFromHandshake(int tunerTypeRaw, int gainCount,
                                                 const QString& endpoint);

// Honest empty state (no real device). connected=false, all ranges 0.
DeviceCapabilities noDeviceCapabilities();

// Sample-rate combo options DERIVED from the connected device's real range.
// Returns an EMPTY list when caps.connected is false (UI then disables the
// combo). The options are the RTL2832U rates the driver actually accepts
// (librtlsdr.c:1100 rule), FILTERED to the device's [sampleRateMinHz,
// sampleRateMaxHz] -- i.e. rebuilding on connect/disconnect, never a fixed
// device-independent menu. Rates that fall in the (300k, 900k] dead band are
// excluded automatically.
QList<double> buildSampleRateOptions(const DeviceCapabilities& caps);

} // namespace dsp
} // namespace mbdsdr
