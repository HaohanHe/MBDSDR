// SPDX-License-Identifier: MIT
// Process-observable step list for the AI panel: renders the real StepRecords
// produced by TaskOrchestrator (tool / key args / state / result summary /
// elapsed), clearly separated from the final natural-language report. Pure
// view: it only displays what the orchestrator recorded -- never invents steps.
#pragma once

#include <QWidget>
#include "ai/task_orchestrator.h"

class QVBoxLayout;
class QLabel;

namespace mbdsdr {
namespace ui {

class TaskStepsView : public QWidget {
    Q_OBJECT
public:
    explicit TaskStepsView(QWidget* parent = nullptr);

    // Replace the on-screen list with a run's real records + final report.
    void setRun(const QList<ai::StepResult>& steps, const QString& report);
    // Incremental live update (async run): redraw the cards only, no report.
    void setLiveSteps(const QList<ai::StepResult>& steps);
    void clear();

private:
    QVBoxLayout* root_ = nullptr;
    QWidget* listHost_ = nullptr;
    QVBoxLayout* listLay_ = nullptr;
    QLabel* reportLabel_ = nullptr;

    void clearList();
    void renderCards(const QList<ai::StepResult>& steps);
};

} // namespace ui
} // namespace mbdsdr
