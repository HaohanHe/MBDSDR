// SPDX-License-Identifier: GPL-3.0-or-later
#include "loopback_backend.h"

namespace mbdsdr {
namespace tx {

bool LoopbackTxBackend::open() {
    open_ = true;
    err_.clear();
    return true;
}

void LoopbackTxBackend::close() {
    ptt_ = false;
    open_ = false;
}

bool LoopbackTxBackend::setFrequencyHz(double hz) {
    if (hz <= 0.0) {
        err_ = QStringLiteral("invalid TX frequency");
        return false;
    }
    freq_ = hz;
    return true;
}

bool LoopbackTxBackend::setSampleRate(double sps) {
    if (sps <= 0.0) {
        err_ = QStringLiteral("invalid sample rate");
        return false;
    }
    rate_ = sps;
    return true;
}

bool LoopbackTxBackend::setGainDb(double db) {
    gain_ = db;
    return true;
}

bool LoopbackTxBackend::setPtt(bool on) {
    if (on) {
        if (!open_) {
            err_ = QStringLiteral("PTT refused: backend not open");
            return false;
        }
        if (freq_ <= 0.0) {
            err_ = QStringLiteral("PTT refused: invalid frequency");
            return false;
        }
    }
    ptt_ = on;
    return true;
}

int LoopbackTxBackend::writeComplex(const std::complex<float>* buf, int n) {
    if (!ptt_ || !open_ || n <= 0) return 0;  // no leakage while not transmitting
    cap_.insert(cap_.end(), buf, buf + n);
    return n;
}

} // namespace tx
} // namespace mbdsdr
