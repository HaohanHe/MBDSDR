// SPDX-License-Identifier: MIT
#include "task_runner.h"

#include "memory_store.h"         // G1: append the completed run to task_runs
#include "dsp/spectrum_engine.h"
#include "ui/bookmark_manager.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMetaType>

namespace mbdsdr {
namespace ai {

// Register types for queued signal delivery across the worker -> UI boundary.
static const int kRegStep = qRegisterMetaType<mbdsdr::ai::StepResult>(
    "mbdsdr::ai::StepResult");
static const int kRegPlan = qRegisterMetaType<mbdsdr::ai::TaskPlan>(
    "mbdsdr::ai::TaskPlan");
static const int kRegState = qRegisterMetaType<mbdsdr::ai::StepState>(
    "mbdsdr::ai::StepState");

TaskRunner::TaskRunner(dsp::SpectrumEngine* engine,
                       ui::BookmarkManager* bookmarks,
                       QObject* parent)
    : QObject(parent), engine_(engine), bookmarks_(bookmarks) {}

TaskRunner::~TaskRunner() {
    // Orderly shutdown: ask the worker to quit, then join its thread.  We own no
    // timers, so there is nothing else to clean up; this prevents zombies.
    thread_.quit();
    if (!thread_.wait(3000))
        thread_.terminate();
}

void TaskRunner::start() {
    this->moveToThread(&thread_);
    thread_.start();
}

namespace {

const char* stateName(StepState s) {
    switch (s) {
    case StepState::Pending:   return "pending";
    case StepState::Running:   return "running";
    case StepState::Succeeded: return "succeeded";
    case StepState::Failed:    return "failed";
    case StepState::Gated:     return "gated";
    case StepState::Aborted:   return "aborted";
    }
    return "unknown";
}

// G1: after a plan finishes, append one row to the shared memory DB's
// task_runs table so get_task_history can answer "what did this agent do?"
// across restarts. Best-effort: a history write failure must never break the
// run or the finished() signal -- observability is not part of the radio path.
void recordTaskRunHistory(const TaskPlan& plan, const QList<StepResult>& steps,
                          const QString& report) {
    QJsonArray arr;
    bool anyFailed = false, anyAborted = false, anyGated = false, anySucceeded = false;
    for (const StepResult& s : steps) {
        QJsonObject so;
        so["tool"] = s.tool;
        so["description"] = s.description;
        so["state"] = QString::fromUtf8(stateName(s.state));
        so["summary"] = s.summary;
        so["elapsed_ms"] = (double)s.elapsedMs;
        if (!s.error.isEmpty()) so["error"] = s.error;
        arr.append(so);
        switch (s.state) {
        case StepState::Failed:   anyFailed = true; break;
        case StepState::Aborted:   anyAborted = true; break;
        case StepState::Gated:     anyGated = true; break;
        case StepState::Succeeded: anySucceeded = true; break;
        default: break;
        }
    }
    const QString state =
        anyFailed   ? QStringLiteral("failed") :
        anyAborted  ? QStringLiteral("aborted") :
        anyGated    ? QStringLiteral("gated") :
        anySucceeded ? QStringLiteral("succeeded") :
                       QStringLiteral("partial");

    MemoryStore store(defaultMemoryDbPath());
    QString err;
    if (!store.open(&err)) return;   // best-effort: no db, no history row
    store.recordTaskRun(plan.name, report, state, steps.size(),
                        QString::fromUtf8(QJsonDocument(arr).toJson(QJsonDocument::Compact)),
                        &err);
}

} // namespace

void TaskRunner::runPlan(const TaskPlan plan) {
    if (running_.load()) return;          // single-flight; UI disables the button
    running_.store(true);
    orch_.setEngine(engine_);
    orch_.setBookmarkManager(bookmarks_);
    orch_.setManualMode(manualMode_);

    // onStep fires on THIS (worker) thread; the queued signal hops to the UI.
    const QString report = orch_.run(plan,
        [this](const StepResult& s) { emit stepUpdated(s); });

    recordTaskRunHistory(plan, orch_.results(), report);

    running_.store(false);
    emit finished(report);
}

void TaskRunner::requestStop() {
    // Only touches the orchestrator's atomic stop flag -- safe from any thread.
    orch_.requestStop();
}

} // namespace ai
} // namespace mbdsdr
