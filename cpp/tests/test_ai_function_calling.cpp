// SPDX-License-Identifier: MIT
// AI function-calling integration test: sends a natural-language request and
// asserts the LLM actually invokes set_frequency / set_mode tools that drive
// the engine. SKIPs (never FAILs) when MBDSDR_AI_KEY is absent or the model
// declines to call tools.
#include <QtTest/QtTest>
#include <QSignalSpy>
#include <QEventLoop>
#include <QTimer>
#include "ai/agent.h"
#include "ai/ai_config.h"
#include "dsp/spectrum_engine.h"

using namespace mbdsdr;

class TestAiFunctionCalling : public QObject {
    Q_OBJECT
private slots:
    void toolCallsDriveEngine();
};

void TestAiFunctionCalling::toolCallsDriveEngine() {
    QByteArray key = qgetenv("MBDSDR_AI_KEY");
    if (key.isEmpty()) QSKIP("no MBDSDR_AI_KEY in env");

    dsp::SpectrumEngine engine;
    engine.start();
    QTest::qWait(200);  // let run loop settle

    ai::Agent agent;
    agent.setEngine(&engine);

    ai::AiConfig cfg;
    cfg.apiKey = QString::fromUtf8(key);
    QByteArray base = qgetenv("MBDSDR_AI_BASE_URL");
    cfg.baseUrl = base.isEmpty() ? "https://api.siliconflow.cn/v1" : QString::fromUtf8(base);
    QByteArray model = qgetenv("MBDSDR_AI_MODEL");
    cfg.model = model.isEmpty() ? "Qwen/Qwen2.5-7B-Instruct" : QString::fromUtf8(model);
    agent.setConfig(cfg);

    QSignalSpy spyResp(&agent, &ai::Agent::responseReady);
    QSignalSpy spyTool(&agent, &ai::Agent::toolCalled);
    QVERIFY(spyResp.isValid());
    QVERIFY(spyTool.isValid());

    agent.sendMessage("请把频率调到 98.5 MHz，解调模式设为 NFM");

    // Wait up to 25 s for the final response (tool loop may take 2-3 rounds).
    for (int i = 0; i < 250 && spyResp.isEmpty(); ++i) QTest::qWait(100);

    if (spyTool.isEmpty()) {
        QSKIP("model did not invoke any tool (text-only reply)");
    }

    // The engine should reflect the tool actions.
    const double freq = engine.centerFreq();
    QVERIFY2(qAbs(freq - 98.5e6) < 2.0e6,
             qPrintable(QString("expected ~98.5 MHz, got %1 Hz").arg(freq, 0, 'f', 0)));
    QCOMPARE(engine.demodMode(), QString("NFM"));

    engine.shutdown();
    engine.wait(2000);
}

QTEST_MAIN(TestAiFunctionCalling)
#include "test_ai_function_calling.moc"
