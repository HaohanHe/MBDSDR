// SPDX-License-Identifier: MIT
// Pure, widget-free text/format helpers for the NanoVNA panel. Split out so
// they can be unit-tested without a running widget hierarchy.
#pragma once

#include <QString>
#include <QStringList>

namespace mbdsdr {
namespace ui {

// The honest, no-device hint shown under the connection row.
QString vnaPortHintText();

// Status line: disconnected -> the honest "no NanoVNA" line; connected ->
// "model · version · cal items" (cal empty means un-calibrated, stated plainly).
QString vnaStatusText(bool connected, const QString& model,
                      const QString& version, const QStringList& cal);

// One-line measurement readout: lowest VSWR across the sweep and its frequency,
// plus the S21 mid-band gain. Non-finite / empty -> honest "无数据".
QString vnaReadoutText(double minVswr, long freqAtMinHz, double midGainDb);

// Resonance numeric readout: fr/Q/BW/ESR from vna_rf::analyzeResonance.
// valid=false (点数不足/span=0/无极值) -> honest "--" placeholders, never fabricated.
QString vnaAnalysisText(bool valid, double seriesFrHz, double q,
                        double bandwidthHz, double esr);

} // namespace ui
} // namespace mbdsdr
