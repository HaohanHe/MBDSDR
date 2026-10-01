// SPDX-License-Identifier: MIT
// Async bridge that runs a TaskPlan on a dedicated worker QThread so the UI
// thread never blocks on a long sweep / record.
//
// Thread model (mirrors SpectrumEngine's own QThread + queued-signal pattern):
//   * TaskRunner is a QObject created on the UI thread, then moveToThread()'d
//     onto a private QThread.  Its runPlan slot executes on that worker thread.
//   * runPlan(plan) is invoked from the UI via a queued connection.
//   * requestStop() touches ONLY the orchestrator's ATOMIC stop flag, so it is
//     safe to call from the UI thread while runPlan is in flight -- no data race,
//     no mutex.  No further step starts after it returns.
//   * stepUpdated(StepResult) / finished(QString) are emitted on the worker thread
//     and delivered to the UI through queued connections (metatypes registered).
//   * We own NO timers: the engine's own sample timer stays on the engine
//     thread; joining the worker thread in the destructor guarantees no zombie.
#pragma once

#include <QObject>
#include <QThread>
#include <atomic>

#include "task_orchestrator.h"
namespace mbdsdr {
namespace dsp { class SpectrumEngine; }
namespace ui  { class BookmarkManager; }

namespace ai {

class TaskRunner : public QObject {
    Q_OBJECT
public:
    explicit TaskRunner(dsp::SpectrumEngine* engine,
                        ui::BookmarkManager* bookmarks,
                        QObject* parent = nullptr);
    ~TaskRunner() override;

    // Start the worker thread. Call once on the UI thread.
    void start();
    bool isRunning() const { return running_.load(); }

    // Set manual-mode gate; takes effect for the next queued runPlan.
    void setManualMode(bool on) { manualMode_ = on; }

public slots:
    // Queued: run a plan on the worker thread. Guards against overlap.
    void runPlan(const mbdsdr::ai::TaskPlan plan);
    // Atomic stop, safe from any thread.
    void requestStop();

signals:
    void stepUpdated(const mbdsdr::ai::StepResult step);
    void finished(const QString report);

private:
    dsp::SpectrumEngine* engine_ = nullptr;
    ui::BookmarkManager* bookmarks_ = nullptr;
    QThread thread_;
    TaskOrchestrator orch_{nullptr};
    std::atomic<bool> running_{false};
    bool manualMode_ = false;
};

} // namespace ai
} // namespace mbdsdr
