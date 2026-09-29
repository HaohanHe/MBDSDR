// SPDX-License-Identifier: MIT
#include "frequency_scanner.h"

#include <cmath>
#include <algorithm>

#include "signal_watch.h"

namespace mbdsdr {
namespace dsp {

FrequencyScanner::FrequencyScanner()
    : watch_(new SignalWatch()) {}

FrequencyScanner::~FrequencyScanner() {
    delete watch_;
}

void FrequencyScanner::setConfig(const ScanConfig& c) {
    cfg_ = c;
}

const ScanConfig& FrequencyScanner::config() const {
    return cfg_;
}

void FrequencyScanner::setBookmarkFrequencies(const QList<double>& hz) {
    bookmarks_ = hz;
}

void FrequencyScanner::buildSequence() {
    seq_.clear();
    if (cfg_.source == ScanSource::Bookmarks) {
        seq_ = bookmarks_;
        return;
    }
    // Range mode: synthesise the equally-spaced channel list by an integer step
    // count (small float tolerance for the stop endpoint). The scanner itself
    // never invents a *measurement* -- this is only the list of tunings to walk.
    if (cfg_.stepHz <= 0.0 || cfg_.stopHz < cfg_.startHz) return;
    const long steps = std::lround((cfg_.stopHz - cfg_.startHz) / cfg_.stepHz);
    const long n = std::max<long>(1, steps + 1);
    for (long i = 0; i < n; ++i) {
        seq_.append(cfg_.startHz + static_cast<double>(i) * cfg_.stepHz);
    }
}

void FrequencyScanner::tuneTo(int index) {
    idx_ = index;
    currentFreq_ = seq_[index];
    freqTimerMs_ = 0.0;
    goneTimerMs_ = 0.0;
    holdTimerMs_ = 0.0;
    watch_->reset();          // 清掉上一频率残留，防止残留电平误命中
}

void FrequencyScanner::start() {
    buildSequence();
    hitCount_ = 0;
    hits_.clear();
    watch_->reset();
    goneTimerMs_ = 0.0;
    holdTimerMs_ = 0.0;

    if (seq_.isEmpty()) {
        state_ = ScanState::Idle;
        currentFreq_ = 0.0;
        pendingTune_ = false;
        return;
    }

    // Pick the entry index and the travelling direction delta.
    dirDelta_ = +1;
    int first = 0;
    if (cfg_.direction == ScanDirection::Down) {
        first = seq_.size() - 1;
        dirDelta_ = -1;
    } else if (cfg_.direction == ScanDirection::PingPong) {
        first = 0;             // 从起点出发，向上，到端点再反向
        dirDelta_ = +1;
    }

    state_ = ScanState::Scanning;
    tuneTo(first);
    pendingTune_ = true;       // 首个 tick 发出到起点的调谐请求
}

void FrequencyScanner::pause() {
    if (state_ == ScanState::Scanning || state_ == ScanState::Hit) {
        prePause_ = state_;
        state_ = ScanState::Paused;
    }
}

void FrequencyScanner::resume() {
    if (state_ == ScanState::Paused) state_ = prePause_;
}

void FrequencyScanner::stop() {
    state_ = ScanState::Idle;
    pendingTune_ = false;
    freqTimerMs_ = 0.0;
    holdTimerMs_ = 0.0;
    goneTimerMs_ = 0.0;
}

ScanState FrequencyScanner::state() const { return state_; }
double FrequencyScanner::currentFrequency() const { return currentFreq_; }
double FrequencyScanner::hitFrequency() const { return hitFreq_; }
float  FrequencyScanner::lastLevelDb() const { return lastLevel_; }
int    FrequencyScanner::hitCount() const { return hitCount_; }
QList<ScanHit> FrequencyScanner::hits() const { return hits_; }

bool FrequencyScanner::advance() {
    if (seq_.isEmpty()) { state_ = ScanState::Idle; return false; }

    int next = idx_ + dirDelta_;
    if (cfg_.direction == ScanDirection::PingPong) {
        // 到端点反向：在边界处翻向，再走到相邻频道（永不自发 Idle）。
        if (next < 0) { dirDelta_ = +1; next = idx_ + dirDelta_; }
        else if (next >= seq_.size()) { dirDelta_ = -1; next = idx_ + dirDelta_; }
    } else {
        if (next < 0 || next >= seq_.size()) {
            if (cfg_.loop) {
                next = (next < 0) ? seq_.size() - 1 : 0;
            } else {
                state_ = ScanState::Idle;   // 走到尽头，扫描结束
                return false;
            }
        }
    }
    tuneTo(next);
    // We now walk a fresh channel: leave the Hit dwell and re-enter the
    // settle+evaluate path.
    state_ = ScanState::Scanning;
    // Note: the tick that called advance() emits the tune request itself; we do
    // NOT set pendingTune_ here, otherwise the next tick would re-request.
    return true;
}

double FrequencyScanner::tick(int elapsedMs, float rssiDb, bool* needTune) {
    if (needTune) *needTune = false;
    lastLevel_ = rssiDb;

    if (state_ == ScanState::Idle || state_ == ScanState::Paused) {
        // Paused：冻结一切计时、不调谐、状态不变。
        return currentFreq_;
    }

    // 启动/换频后的首个 tick：只负责发出调谐请求，不计入驻留时间。
    if (pendingTune_) {
        pendingTune_ = false;
        if (needTune) *needTune = true;
        return currentFreq_;
    }

    if (state_ == ScanState::Scanning) {
        freqTimerMs_ += elapsedMs;

        // settle 窗口：检测器已在 tuneTo() 复位，这段时间忽略 RSSI，
        // 避免上一频率残留电平被误判为新频率上的信号。
        if (freqTimerMs_ <= cfg_.settleMs) return currentFreq_;

        // 评估窗：把真实 RSSI 喂给复用的 SignalWatch 门限。
        watch_->setBlockMs(static_cast<double>(elapsedMs));
        watch_->setThresholdDb(cfg_.thresholdDb);
        const bool active = watch_->update(rssiDb);

        if (active) {
            // 命中：记录一次（同一次停留只计一次）。
            state_ = ScanState::Hit;
            hitFreq_ = currentFreq_;
            hitCount_++;
            hits_.append({currentFreq_, rssiDb});
            holdTimerMs_ = 0.0;
            goneTimerMs_ = 0.0;
            return currentFreq_;
        }

        // 驻留满 dwellMs 仍未命中 -> 推进到下一频率（本 tick 即发出新调谐）。
        if (freqTimerMs_ >= cfg_.dwellMs) {
            if (advance() && needTune) *needTune = true;
        }
        return currentFreq_;
    }

    // state_ == Hit
    watch_->setBlockMs(static_cast<double>(elapsedMs));
    watch_->setThresholdDb(cfg_.thresholdDb);
    const bool active = watch_->update(rssiDb);

    if (cfg_.holdMode == HitHoldMode::FixedMs) {
        holdTimerMs_ += elapsedMs;     // 与信号是否仍在无关，固定停留
        if (holdTimerMs_ >= cfg_.holdMs) {
            if (advance() && needTune) *needTune = true;
        }
    } else { // UntilSignalGone
        if (active) {
            goneTimerMs_ = 0.0;        // 信号仍在：持续停留
        } else {
            goneTimerMs_ += elapsedMs;  // 信号已被门限判为消失
            if (goneTimerMs_ >= cfg_.lingerMs) {
                if (advance() && needTune) *needTune = true;
            }
        }
    }
    return currentFreq_;
}

} // namespace dsp
} // namespace mbdsdr
