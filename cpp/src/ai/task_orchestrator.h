// SPDX-License-Identifier: MIT
// Autonomous multi-step task orchestration for the desktop Agent.
//
// A TaskPlan is a deterministic step sequence; every step drives the radio
// through the SAME single execution point the LLM tool-loop uses
// (LLMWorker::dispatchToolCall -> executeTool -> engine). There is no second
// execution path. Step arguments may reference a previous step's *real* result
// with a JSON path ({"fromStep":0,"path":"hits[0].frequencyHz"}) -- no {{var}}
// templating. The orchestrator is plain synchronous, reentrant-ish logic with
// an atomic stop flag, so it can be unit-tested against a real SpectrumEngine.
#pragma once

#include "llm_client.h"
#include <QString>
#include <QJsonObject>
#include <QJsonValue>
#include <QList>
#include <atomic>
#include <functional>

namespace mbdsdr {
namespace dsp { class SpectrumEngine; }
namespace ui  { struct Bookmark; class BookmarkManager; }

namespace ai {

// Lifecycle of one planned step.
enum class StepState { Pending, Running, Succeeded, Failed, Gated, Aborted };

// One planned action. `args` may carry reference values (see resolveRef).
struct TaskStep {
    QString tool;            // tool name, e.g. "scan_band" / "tune_frequency"
    QJsonObject args;        // literal args OR {"fromStep":n,"path":"..."} refs
    QString description;     // short human label for the step list UI
};

// Record of one executed step, fed to the process-observable UI.
struct StepResult {
    QString tool;
    QString description;
    QJsonObject argsResolved;   // args after reference resolution (what really ran)
    StepState state = StepState::Pending;
    QString resultText;         // raw tool result string (may be JSON)
    QJsonObject resultJson;     // parsed when the result is a JSON object
    QString summary;            // trimmed one-line summary for the step list
    qint64 elapsedMs = 0;
    bool gated = false;         // blocked by manual mode
    QString error;              // honest failure / gate reason
};

struct TaskPlan {
    QString name;
    QList<TaskStep> steps;
    bool abortOnFail = true;    // stop on first failed step vs. keep going
};

class TaskOrchestrator {
public:
    explicit TaskOrchestrator(dsp::SpectrumEngine* engine = nullptr);

    void setEngine(dsp::SpectrumEngine* e) { engine_ = e; }
    // Mirror the Agent manual-mode gate: write tools get gated results.
    void setManualMode(bool on) { manualMode_ = on; }
    // Optional bookmark store; when set, "add_bookmark" steps persist there.
    void setBookmarkManager(ui::BookmarkManager* bm) { bookmarks_ = bm; }

    // Run the whole plan synchronously, step by step. Honors max-steps token,
    // abortOnFail and requestStop(). Returns a final honest natural-language
    // summary (distinct from the per-step records in results()).
    // Step callback invoked on the RUN thread as each step completes; used by the
    // async TaskRunner to re-emit progress to the UI thread (queued). May be null.
    using StepCallback = std::function<void(const StepResult&)>;
    QString run(const TaskPlan& plan, StepCallback onStep = nullptr);

    // Atomic interrupt: no further step starts after this returns. Safe to call
    // from another thread while run() is in flight. No timers are owned, so
    // there is nothing to leak; joining the run() call site cleans up.
    void requestStop() { stop_.store(true); }
    bool isStopRequested() const { return stop_.load(); }

    const QList<StepResult>& results() const { return results_; }
    static int maxSteps();   // from tokens::kTaskMaxSteps

    // Resolve one arg value against prior step results. A value that is a JSON
    // object with BOTH "fromStep" (int) and "path" (string) keys is a reference
    // and is extracted from that prior step's parsed result. Anything else is a
    // literal and returned unchanged. Honest: sets ok=false + err on failure.
    static QJsonValue resolveRef(const QJsonValue& v,
                                 const QList<StepResult>& prior,
                                 bool& ok, QString& err);

private:
    dsp::SpectrumEngine* engine_ = nullptr;
    ui::BookmarkManager* bookmarks_ = nullptr;
    bool manualMode_ = false;
    std::atomic<bool> stop_{false};
    QList<StepResult> results_;
};

// ---- Deterministic task templates (no LLM key required) --------------------
// All parameters are call-supplied; no station names / locations are baked in.
/// Sweep a band, take the peak hit, save it as a bookmark, retune + record.
TaskPlan planSweepFindAndRecord(double lowHz, double highHz, double stepHz,
                                 const QString& mode, const QString& bookName);
/// Tune to a target frequency, set mode, then record (satellite-pass capture;
/// live Doppler compensation is the engine's own job).
TaskPlan planTargetCapture(double targetHz, const QString& mode);
/// Tune to a fixed frequency, set bandwidth + mode, then record for decode.
TaskPlan planFixedFrequencyRecord(double freqHz, const QString& mode,
                                  double bandwidthHz);

} // namespace ai
} // namespace mbdsdr
