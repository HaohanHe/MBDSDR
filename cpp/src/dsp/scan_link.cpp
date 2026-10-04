// SPDX-License-Identifier: MIT
#include "scan_link.h"

namespace mbdsdr {
namespace dsp {

ScanActivityLink::ScanActivityLink() = default;
ScanActivityLink::~ScanActivityLink() = default;

void ScanActivityLink::setConfig(const ScanConfig& c) { scanner_.setConfig(c); }

void ScanActivityLink::setBookmarkFrequencies(const QList<double>& hz) {
    scanner_.setBookmarkFrequencies(hz);
}

const FrequencyScanner& ScanActivityLink::scanner() const { return scanner_; }

void ScanActivityLink::setActions(ScanLinkActions a) { actions_ = std::move(a); }

void ScanActivityLink::start() {
    scanner_.start();
    dwellCount_ = 0;
    parkedFreq_ = 0.0;
    retuneLog_.clear();
    wasHit_ = false;
    state_ = (scanner_.state() == ScanState::Idle) ? ScanLinkState::Idle
                                                   : ScanLinkState::Scanning;
}

void ScanActivityLink::stop() {
    // If we were parked on an activity, close it out honestly before going Idle.
    if (wasHit_ && actions_.onDwellEnded) actions_.onDwellEnded(parkedFreq_);
    scanner_.stop();
    state_ = ScanLinkState::Idle;
    wasHit_ = false;
    parkedFreq_ = 0.0;
}

double ScanActivityLink::tick(int elapsedMs, float rssiDb) {
    bool needTune = false;
    const double freq = scanner_.tick(elapsedMs, rssiDb, &needTune);

    // Edge 1: the scanner asked to retune (new channel, incl. the first out of
    // start()). Log it and hand the retune to the caller.
    if (needTune) {
        retuneLog_.append(freq);
        if (actions_.onRetune) actions_.onRetune(freq);
    }

    const bool nowHit = (scanner_.state() == ScanState::Hit);

    // Edge 2: Scanning -> Hit. A NEW activity opened: park, decode, record.
    if (nowHit && !wasHit_) {
        parkedFreq_ = scanner_.hitFrequency();
        ++dwellCount_;
        state_ = ScanLinkState::Dwell;
        if (actions_.onActivityFound)
            actions_.onActivityFound(parkedFreq_, scanner_.lastLevelDb());
    }
    // Edge 3: Hit -> (Scanning | Idle). The dwell ended; finalise the recording.
    else if (!nowHit && wasHit_) {
        if (actions_.onDwellEnded) actions_.onDwellEnded(parkedFreq_);
        parkedFreq_ = 0.0;
        if (scanner_.state() == ScanState::Idle) state_ = ScanLinkState::Idle;
        else                                     state_ = ScanLinkState::Scanning;
    }
    // Idle fall-through (no edge): keep the normalised state in sync.
    else if (scanner_.state() == ScanState::Idle) {
        state_ = ScanLinkState::Idle;
    } else if (state_ == ScanLinkState::Idle) {
        state_ = ScanLinkState::Scanning;
    }

    wasHit_ = nowHit;
    return freq;
}

ScanLinkState ScanActivityLink::state() const { return state_; }
int ScanActivityLink::dwellCount() const { return dwellCount_; }
double ScanActivityLink::parkedFrequency() const { return parkedFreq_; }
QList<double> ScanActivityLink::retuneLog() const { return retuneLog_; }

} // namespace dsp
} // namespace mbdsdr
