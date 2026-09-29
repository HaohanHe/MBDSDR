// SPDX-License-Identifier: MIT
//
// SDR++-style frequency scanner -- a PURE LOGIC state machine.
//
// This class owns NO radio, opens NO device, sleeps on NO wall clock, and
// fabricates neither a frequency nor an RSSI level. It only answers two
// questions, deterministically, from the caller-supplied elapsed time and the
// caller-supplied REAL measured RSSI (dBFS):
//
//   * "When should I retune, and to which frequency?" (needTune out-param)
//   * "Is the signal on the current frequency strong enough to count as a
//     hit?" -- delegated entirely to the reused SignalWatch gate, which applies
//     a fast-attack / slow-decay smooth plus a short above-threshold confirm.
//
// A "hit" is therefore always based on the REAL RSSI fed in by the caller (the
// engine derives RSSI dBFS from raw capture energy, see spectrum_engine). The
// scanner never invents a level. The offline unit tests drive this state machine
// with a synthetic per-frequency RSSI table (FIXTURE / SYNTHETIC -- NOT
// HARDWARE); the scanner itself performs no measurement.
//
// Ordinary C++ class (no QObject / no moc): it is a headless controller that
// the engine or a UI layer drives with tick().
#pragma once

#include <QList>

namespace mbdsdr {
namespace dsp {

class SignalWatch;

enum class ScanState { Idle, Scanning, Paused, Hit };
enum class ScanDirection { Up, Down, PingPong };    // 向上 / 向下 / 来回
enum class ScanSource { Range, Bookmarks };        // 范围步进 / 书签列表
enum class HitHoldMode { UntilSignalGone, FixedMs }; // 命中后停留

// One recorded hit: the frequency that was tuned and the measured level that
// triggered the gate. The level is the real RSSI the caller fed in at the
// moment the gate opened -- never synthesised here.
struct ScanHit {
    double freqHz = 0.0;
    float  levelDb = -200.0f;
};

struct ScanConfig {
    ScanSource source = ScanSource::Range;
    double startHz = 88e6;
    double stopHz  = 108e6;
    double stepHz  = 100e3;     // 步进 100 kHz
    int    dwellMs    = 300;    // 每步总驻留（0.2–0.5 s），默认 300 ms
    int    settleMs   = 80;     // 调谐后 settle 窗口：复位检测器、忽略 RSSI
    float  thresholdDb = -50.0f;// 命中门限 dBFS（复用 SignalWatch 默认）
    ScanDirection direction = ScanDirection::Up;
    HitHoldMode holdMode = HitHoldMode::UntilSignalGone;
    int    lingerMs = 1000;     // UntilSignalGone：信号连续消失这么久后继续
    int    holdMs   = 2000;     // FixedMs：命中固定停留这么久后继续
    bool   loop    = false;     // Up/Down 到尽头是否回绕；PingPong 始终来回
};

class FrequencyScanner {
public:
    FrequencyScanner();
    ~FrequencyScanner();

    void setConfig(const ScanConfig& c);
    const ScanConfig& config() const;

    // Bookmarks 模式：仅在这些频率（Hz）间依次跳转，不访问任何中间频率。
    void setBookmarkFrequencies(const QList<double>& hz);

    // start：复位到起点（Up=start/书签首项；Down=stop/书签末项），首个 tick
    // 请求调谐到起点。pause/resume 冻结/恢复全部计时；stop 立即回 Idle。
    void start();
    void pause();
    void resume();
    void stop();

    ScanState state() const;
    double currentFrequency() const; // 请求调谐时该值已更新为新目标
    double hitFrequency() const;     // Hit 时有效
    float  lastLevelDb() const;
    int    hitCount() const;         // 本次 start 以来不同命中数
    QList<ScanHit> hits() const;

    // 驱动状态机：elapsedMs = 距上一 tick 的毫秒，rssiDb = 该时段真实测得
    // RSSI(dBFS)。内部把 RSSI 喂给复用的 SignalWatch（setBlockMs(elapsedMs)、
    // setThresholdDb(config.thresholdDb)）。需要重新调谐时置 *needTune=true 并
    // 返回目标频率；无需调谐时返回当前频率、*needTune=false。
    double tick(int elapsedMs, float rssiDb, bool* needTune = nullptr);

private:
    ScanConfig cfg_;
    QList<double> bookmarks_;

    QList<double> seq_;      // 解析出的待调谐频率序列
    int idx_ = 0;            // 当前在 seq_ 中的下标
    int dirDelta_ = +1;      // PingPong 当前步进方向（+1/-1）

    ScanState state_ = ScanState::Idle;
    ScanState prePause_ = ScanState::Scanning; // pause 前的活动状态

    bool pendingTune_ = false; // 下一 tick 必须先发出一次调谐请求
    double freqTimerMs_ = 0.0; // 当前频率驻留计时（settle+评估）
    double holdTimerMs_ = 0.0; // Hit：FixedMs 停留计时
    double goneTimerMs_ = 0.0; // Hit：UntilSignalGone 信号消失计时

    double currentFreq_ = 0.0;
    double hitFreq_ = 0.0;
    float  lastLevel_ = -200.0f;
    int    hitCount_ = 0;
    QList<ScanHit> hits_;

    SignalWatch* watch_;

    void buildSequence();
    void tuneTo(int index);      // 切到 seq_[index]，复位驻留/检测器计时
    bool advance();              // 按方向/loop 推进；返回 true=已调谐新频率
};

} // namespace dsp
} // namespace mbdsdr
