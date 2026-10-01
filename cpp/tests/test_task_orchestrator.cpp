// SPDX-License-Identifier: MIT
// Task orchestrator tests: run a real SpectrumEngine through the deterministic
// templates and assert steps really execute, step results really chain
// (scan hit frequency lands in bookmark / retune), interrupt halts before the
// next step, max-steps caps the run, single-step failure honours the continue
// policy, and manual-mode gating produces an honest (non-faked) report.
#include <QtTest/QtTest>
#include <QSettings>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QTemporaryDir>
#include <thread>
#include <chrono>

#include "ai/task_orchestrator.h"
#include "ai/llm_worker.h"
#include "dsp/spectrum_engine.h"
#include "ui/bookmark_manager.h"

using namespace mbdsdr;

class TestTaskOrchestrator : public QObject {
    Q_OBJECT
private slots:
    void testResolveRef();
    void testTemplateShapes();
    void testSweepHitChainsToBookmark();   // full loopback chain (soak)
    void testInterruptHaltsNextStep();
    void testMaxStepsCap();
    void testSingleStepFailureContinue();
    void testManualGateHonestReport();      // manual mode blocks the task
};

// Build a fake prior result whose parsed JSON has hits[0].frequencyHz.
static ai::StepResult priorScanResult(double hitHz) {
    ai::StepResult r;
    r.tool = "scan_band";
    QJsonObject hit; hit["frequencyHz"] = hitHz; hit["dbfs"] = -12.5;
    QJsonObject o; o["ok"] = true; o["hits"] = QJsonArray{hit};
    r.resultJson = o;
    return r;
}

void TestTaskOrchestrator::testResolveRef() {
    QList<ai::StepResult> prior;
    prior.append(priorScanResult(123456789.0));

    bool ok = false;
    QString err;
    QJsonValue ref = QJsonObject{{"fromStep", 0}, {"path", "hits[0].frequencyHz"}};
    QJsonValue v = ai::TaskOrchestrator::resolveRef(ref, prior, ok, err);
    QVERIFY2(ok, qPrintable(err));
    QCOMPARE(v.toDouble(), 123456789.0);

    // out-of-range step
    QJsonValue badStep = QJsonObject{{"fromStep", 5}, {"path", "hits[0].x"}};
    (void)ai::TaskOrchestrator::resolveRef(badStep, prior, ok, err);
    QVERIFY2(!ok, "out-of-range fromStep must fail honestly");

    // out-of-range array index
    QJsonValue badIdx = QJsonObject{{"fromStep", 0}, {"path", "hits[9].frequencyHz"}};
    (void)ai::TaskOrchestrator::resolveRef(badIdx, prior, ok, err);
    QVERIFY2(!ok, "array index out of range must fail honestly");

    // literal value passes through unchanged
    QJsonValue lit = 42.0;
    QJsonValue lv = ai::TaskOrchestrator::resolveRef(lit, prior, ok, err);
    QVERIFY(ok);
    QCOMPARE(lv.toDouble(), 42.0);
}

void TestTaskOrchestrator::testTemplateShapes() {
    auto p = ai::planSweepFindAndRecord(100e6, 108e6, 100e3, "NFM", "测试书签");
    QCOMPARE(p.steps.size(), 6);
    QCOMPARE(p.steps[0].tool, QString("scan_band"));
    QCOMPARE(p.steps[1].tool, QString("add_bookmark"));
    // The bookmark freq must reference the scan hit, not a hard-coded number.
    QJsonObject fref = p.steps[1].args.value("freq_hz").toObject();
    QCOMPARE(fref.value("fromStep").toInt(), 0);
    QCOMPARE(fref.value("path").toString(), QString("hits[0].frequencyHz"));

    auto t = ai::planTargetCapture(436.5e6, "WFM");
    QCOMPARE(t.steps.size(), 4);
    QCOMPARE(t.steps[0].tool, QString("tune_frequency"));

    auto d = ai::planFixedFrequencyRecord(98.5e6, "WFM", 200e3);
    QCOMPARE(d.steps.size(), 5);
}

void TestTaskOrchestrator::testSweepHitChainsToBookmark() {
    dsp::SpectrumEngine engine;
    ui::BookmarkManager bm;   // isolated by QSettings temp path in main()
    bm.clear();

    ai::TaskOrchestrator orch(&engine);
    orch.setBookmarkManager(&bm);

    // Narrow, fast scan (4 steps * 20 ms).
    ai::TaskPlan p = ai::planSweepFindAndRecord(100e6, 100.3e6, 100e3, "NFM", "自动命中");
    QString report = orch.run(p);

    // Every step really ran.
    QCOMPARE(orch.results().size(), p.steps.size());
    for (const ai::StepResult& r : orch.results()) {
        QVERIFY2(r.state == ai::StepState::Succeeded,
                 qPrintable(r.tool + " -> " + r.error));
    }

    // Evidence: the scan step produced a real structured hit.
    const QJsonObject scanOut = orch.results()[0].resultJson;
    QVERIFY(scanOut.value("hits").isArray());
    const double hitHz = scanOut.value("hits").toArray().at(0).toObject().value("frequencyHz").toDouble();
    QVERIFY(hitHz > 0.0);

    // The bookmark step persisted EXACTLY the frequency the scan returned.
    QCOMPARE(bm.count(), 1);
    QCOMPARE(bm.list()[0].frequencyHz, hitHz);

    // The retune step tuned to that same hit frequency (real engine effect).
    QVERIFY2(qAbs(engine.centerFreq() - hitHz) < 1.0,
             qPrintable(QString("engine %1 vs hit %2").arg(engine.centerFreq()).arg(hitHz)));

    qDebug() << "REPORT:" << report;
    bm.clear();
}

void TestTaskOrchestrator::testInterruptHaltsNextStep() {
    dsp::SpectrumEngine engine;
    ai::TaskOrchestrator orch(&engine);

    // Slow scan (11 steps * 20 ms = 220 ms) then a marker retune we must NEVER reach.
    ai::TaskPlan p;
    p.name = "中断测试";
    p.abortOnFail = true;
    ai::TaskStep scan; scan.tool = "scan_band"; scan.description = "慢扫描";
    scan.args = QJsonObject{{"low_hz", 100e6}, {"high_hz", 101.0e6}, {"step_hz", 100e3}};
    ai::TaskStep marker; marker.tool = "tune_frequency"; marker.description = "标记调谐";
    marker.args = QJsonObject{{"freq_hz", 999e6}};
    p.steps = {scan, marker};

    std::string report;
    std::thread runner([&]() { report = orch.run(p).toStdString(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(60));  // mid-scan
    orch.requestStop();
    runner.join();

    // Only the (in-flight) scan ran; the marker retune must not have executed.
    QCOMPARE(orch.results().size(), 1);
    QCOMPARE(orch.results()[0].tool, QString("scan_band"));
    QVERIFY2(qAbs(engine.centerFreq() - 999e6) > 1.0,
             "marker retune after stop must NOT have run");
    QVERIFY2(QString::fromStdString(report).contains("中断"),
             qPrintable(QString::fromStdString(report)));
}

void TestTaskOrchestrator::testMaxStepsCap() {
    dsp::SpectrumEngine engine;
    ai::TaskOrchestrator orch(&engine);

    ai::TaskPlan p;
    p.name = "超限";
    p.abortOnFail = false;
    for (int i = 0; i < 20; ++i) {
        ai::TaskStep s; s.tool = "tune_frequency";
        s.args = QJsonObject{{"freq_hz", 100e6 + i * 1e3}};
        p.steps.append(s);
    }
    QString report = orch.run(p);
    QCOMPARE(orch.results().size(), ai::TaskOrchestrator::maxSteps());
    QVERIFY2(report.contains("最大步数"), qPrintable(report));
}

void TestTaskOrchestrator::testSingleStepFailureContinue() {
    dsp::SpectrumEngine engine;
    ai::TaskOrchestrator orch(&engine);

    ai::TaskPlan p;
    p.name = "失败继续";
    p.abortOnFail = false;   // keep going after a failure
    ai::TaskStep bad; bad.tool = "no_such_tool"; bad.args = QJsonObject{};
    ai::TaskStep good; good.tool = "tune_frequency";
    good.args = QJsonObject{{"freq_hz", 98.5e6}};
    p.steps = {bad, good};

    QString report = orch.run(p);
    QCOMPARE(orch.results().size(), 2);
    QCOMPARE(orch.results()[0].state, ai::StepState::Failed);
    QCOMPARE(orch.results()[1].state, ai::StepState::Succeeded);
    QVERIFY2(qAbs(engine.centerFreq() - 98.5e6) < 1.0, "good step must still run");
    qDebug() << "REPORT:" << report;
}

void TestTaskOrchestrator::testManualGateHonestReport() {
    dsp::SpectrumEngine engine;
    const double before = engine.centerFreq();
    ai::TaskOrchestrator orch(&engine);
    orch.setManualMode(true);   // AI 接管 off

    ai::TaskPlan p = ai::planTargetCapture(145.8e6, "NFM");
    QString report = orch.run(p);

    // Every write step is gated: no engine effect, honest gated result.
    QCOMPARE(orch.results().size(), p.steps.size());
    for (const ai::StepResult& r : orch.results()) {
        QCOMPARE(r.state, ai::StepState::Gated);
        QVERIFY2(r.resultText.contains("\"gated\":true"), qPrintable(r.resultText));
    }
    QVERIFY2(qAbs(engine.centerFreq() - before) < 1.0,
             "manual mode must not retune the radio");
    QVERIFY2(report.contains("拦截"), qPrintable(report));
    qDebug() << "REPORT:" << report;
}

#include <QCoreApplication>
int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    // Isolate QSettings (bookmark writes) from the user's real config.
    QTemporaryDir tmp;
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope, tmp.path());
    TestTaskOrchestrator t;
    return QTest::qExec(&t, argc, argv);
}
#include "test_task_orchestrator.moc"
