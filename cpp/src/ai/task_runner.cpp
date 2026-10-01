// SPDX-License-Identifier: MIT
#include "task_runner.h"

#include "dsp/spectrum_engine.h"
#include "ui/bookmark_manager.h"

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

void TaskRunner::runPlan(const TaskPlan plan) {
    if (running_.load()) return;          // single-flight; UI disables the button
    running_.store(true);
    orch_.setEngine(engine_);
    orch_.setBookmarkManager(bookmarks_);
    orch_.setManualMode(manualMode_);

    // onStep fires on THIS (worker) thread; the queued signal hops to the UI.
    const QString report = orch_.run(plan,
        [this](const StepResult& s) { emit stepUpdated(s); });

    running_.store(false);
    emit finished(report);
}

void TaskRunner::requestStop() {
    // Only touches the orchestrator's atomic stop flag -- safe from any thread.
    orch_.requestStop();
}

} // namespace ai
} // namespace mbdsdr
