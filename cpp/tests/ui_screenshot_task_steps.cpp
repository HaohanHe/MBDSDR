// SPDX-License-Identifier: MIT
// Offscreen screenshot of the AI-panel task step list. Drives the REAL
// TaskOrchestrator against a real SpectrumEngine (sweep -> hit -> bookmark ->
// retune -> record), then feeds the real StepRecords into TaskStepsView.
// Not in ctest. Env: MBD_OUT (png path).
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QSettings>
#include <QTemporaryDir>

#include "core/tokens.h"
#include "ui/task_steps_view.h"
#include "ai/task_orchestrator.h"
#include "dsp/spectrum_engine.h"
#include "ui/bookmark_manager.h"

using namespace mbdsdr;

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QTemporaryDir tmp;
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope, tmp.path());
    app.setStyleSheet(tokens::buildDarkQss());

    const QString out = QString::fromLocal8Bit(qgetenv("MBD_OUT"));

    dsp::SpectrumEngine engine;
    ui::BookmarkManager bm;
    bm.clear();

    ai::TaskOrchestrator orch(&engine);
    orch.setBookmarkManager(&bm);
    ai::TaskPlan plan = ai::planSweepFindAndRecord(100e6, 100.3e6, 100e3, "NFM", "自动命中");
    const QString report = orch.run(plan);

    ui::TaskStepsView view;
    view.setMinimumSize(480, 640);
    view.setRun(orch.results(), report);
    view.resize(480, 640);
    view.show();
    QApplication::processEvents();

    QTimer::singleShot(150, [&]() {
        QPixmap pm = view.grab();
        pm.save(out, "PNG");
        qInfo("task steps screenshot saved to %s (%dx%d, %1 steps)",
              out.toLocal8Bit().constData(), pm.width(), pm.height(), orch.results().size());
        app.quit();
    });
    return app.exec();
}
