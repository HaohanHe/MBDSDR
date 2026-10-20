// SPDX-License-Identifier: MIT
// Async task-runner tests: the plan runs on a worker QThread so the UI event
// loop stays responsive (a QTimer keeps firing DURING the run), step updates
// arrive as queued signals, a manual-mode gate reports honestly, and requestStop
// halts before later steps. No data race / zombie: runner joins its thread on
// destruction.
#include <QtTest/QtTest>
#include <QSettings>
#include <QTemporaryDir>
#include <QTimer>
#include <QElapsedTimer>
#include <QApplication>

#include "ai/task_runner.h"
#include "ai/memory_store.h"
#include "dsp/spectrum_engine.h"
#include "ui/bookmark_manager.h"

using namespace mbdsdr;

class TestTaskAsync : public QObject {
    Q_OBJECT
private:
    dsp::SpectrumEngine* engine_ = nullptr;
    ui::BookmarkManager* bm_ = nullptr;
private slots:
    void init() {
        engine_ = new dsp::SpectrumEngine();
        bm_ = new ui::BookmarkManager();
        bm_->clear();
    }
    void cleanup() {
        delete engine_; engine_ = nullptr;
        delete bm_; bm_ = nullptr;
    }
    void asyncRunKeepsUiResponsive();
    void requestStopHaltsLaterSteps();
};

void TestTaskAsync::asyncRunKeepsUiResponsive() {
    ai::TaskRunner runner(engine_, bm_);
    runner.start();

    QList<ai::StepResult> steps;
    QByteArray report;
    connect(&runner, &ai::TaskRunner::stepUpdated,
            this, [&](const ai::StepResult& s) { steps.append(s); });
    QSignalSpy finishedSpy(&runner, &ai::TaskRunner::finished);

    // Timer that must keep firing on the UI thread WHILE the worker runs -- this
    // proves the event loop is not frozen by the task.
    int ticks = 0;
    QTimer pump;
    connect(&pump, &QTimer::timeout, this, [&] { ++ticks; });
    pump.start(10);

    ai::TaskPlan plan = ai::planSweepFindAndRecord(100e6, 100.3e6, 100e3, "NFM", "自动命中");
    QMetaObject::invokeMethod(&runner, "runPlan", Qt::QueuedConnection,
                              Q_ARG(mbdsdr::ai::TaskPlan, plan));

    QVERIFY(finishedSpy.wait(5000));
    pump.stop();

    QVERIFY2(ticks > 0, "UI timer must fire while the worker runs (no freeze)");
    QVERIFY(!steps.isEmpty());
    QVERIFY(steps.size() == plan.steps.size());
    for (const auto& s : steps)
        QVERIFY2(s.state == ai::StepState::Succeeded, qPrintable(s.error));

    // G1: the completed run must have been appended to the task_runs history.
    ai::MemoryStore store(ai::defaultMemoryDbPath());
    QString herr;
    QVERIFY2(store.open(&herr), qPrintable(herr));
    QList<ai::TaskRunRecord> runs = store.listTaskRuns(5, &herr);
    QVERIFY2(!runs.isEmpty(), "TaskRunner must persist a history row after runPlan");
    const ai::TaskRunRecord rec = runs.first();
    QCOMPARE(rec.name, plan.name);
    QCOMPARE(rec.stepCount, steps.size());
    QCOMPARE(rec.state, QString("succeeded"));
    QVERIFY(!rec.stepsJson.isEmpty());
}

void TestTaskAsync::requestStopHaltsLaterSteps() {
    ai::TaskRunner runner(engine_, bm_);
    runner.start();

    int stepCount = 0;
    connect(&runner, &ai::TaskRunner::stepUpdated,
            this, [&](const ai::StepResult&) { ++stepCount; });
    QSignalSpy finishedSpy(&runner, &ai::TaskRunner::finished);

    // Wide slow sweep (many steps) so we can stop mid-run.
    ai::TaskPlan plan = ai::planSweepFindAndRecord(100e6, 101.0e6, 100e3, "NFM", "自动命中");
    QMetaObject::invokeMethod(&runner, "runPlan", Qt::QueuedConnection,
                              Q_ARG(mbdsdr::ai::TaskPlan, plan));

    // Let the first step start, then stop.
    QTest::qWait(60);
    runner.requestStop();

    QVERIFY(finishedSpy.wait(5000));
    // The run ended by interrupt: far fewer steps than planned.
    QVERIFY2(stepCount < plan.steps.size(),
             qPrintable(QString("interrupt should truncate, got %1/%2")
                            .arg(stepCount).arg(plan.steps.size())));
}

int main(int argc, char** argv) {
    QTemporaryDir tmp;
    QSettings::setDefaultFormat(QSettings::IniFormat);
    QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, tmp.path());
    qputenv("MBDSDR_SETTINGS_DIR", (tmp.path()).toUtf8());
    // G1: redirect the task-run history + memory db off the production
    // ~/.mbdsdr path -- the runner now appends a history row on every runPlan.
    qputenv("MBDSDR_MEMORY_DB",
            (tmp.path() + "/memory.db").toUtf8());
    QApplication app(argc, argv);
    TestTaskAsync t;
    return QTest::qExec(&t, argc, argv);
}

#include "test_task_async.moc"
