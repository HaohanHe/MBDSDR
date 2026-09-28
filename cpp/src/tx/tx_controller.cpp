// SPDX-License-Identifier: GPL-3.0-or-later
#include "tx_controller.h"

#include <algorithm>
#include <cmath>

namespace mbdsdr {
namespace tx {

TxController::TxController(ITxBackend& backend, IModulator& modulator,
                           double sampleRate)
    : backend_(backend), modulator_(modulator), rate_(sampleRate) {}

TxController::~TxController() {
    // Defensive: never leave the key held if the controller goes away.
    if (tx_) backend_.setPtt(false);
}

bool TxController::requestPtt(bool on) {
    if (on) {
        if (tripped_) return false;  // must reset watchdog after a trip
        if (!backend_.setPtt(true)) return false;
        tx_ = true;
        segmentStart_ = totalSamples_;
        return true;
    }
    backend_.setPtt(false);
    tx_ = false;
    return true;
}

int TxController::transmitAudio(const std::vector<float>& audio) {
    if (!tx_) return 0;  // no explicit PTT -> no emission at all

    auto iq = modulator_.process(audio);

    // Watchdog: would this block push the segment past the limit?
    const double projected =
        double(totalSamples_ - segmentStart_ + static_cast<long long>(iq.size())) / rate_;
    if (projected > maxPttSeconds_) {
        backend_.setPtt(false);
        tx_ = false;
        tripped_ = true;
        return 0;
    }

    const int accepted = backend_.writeComplex(iq.data(), static_cast<int>(iq.size()));
    totalSamples_ += accepted;
    return accepted;
}

double TxController::segmentElapsedSeconds() const {
    return double(totalSamples_ - segmentStart_) / rate_;
}

double TxController::totalTxSeconds() const {
    return double(totalSamples_) / rate_;
}

QString TxController::status() const {
    if (tripped_)
        return QStringLiteral("PTT watchdog tripped (auto-stopped)");
    if (tx_)
        return QStringLiteral("ON AIR · %1 s").arg(segmentElapsedSeconds(), 0, 'f', 1);
    return QStringLiteral("standby");
}

} // namespace tx
} // namespace mbdsdr
