// SPDX-License-Identifier: MIT
#include <QtTest/QtTest>
#include <QRegularExpression>
#include <QSettings>

#include "ai/agent_tools.h"
#include "ai/agent.h"
#include "ai/llm_worker.h"
#include "ai/ai_config.h"
#include "dsp/spectrum_engine.h"

using namespace mbdsdr;

class TestAgent : public QObject {
    Q_OBJECT
private slots:
    void testToolParse();
    void testTuneTool();
    void testModeTool();
    void testConfigFields();
    void testLocalFrequency();
    void testLocalMode();
    void testLocalUnknown();
    // --- Manual-mode write-tool gate (desktop AI takeover / handoff) ---
    void testWriteToolClassification();
    void testManualModeGatesWriteTool();
    void testManualModeAllowsReadTool();
    void testAiTakeoverRunsWriteTool();
    void testManualModePersistence();
};

void TestAgent::testToolParse() {
    auto tools = ai::toolDefs();
    QCOMPARE(tools.size(), 8);
    QCOMPARE(tools[0].name, "tune_frequency");
    QCOMPARE(tools[1].name, "set_mode");
}

void TestAgent::testTuneTool() {
    dsp::SpectrumEngine engine;
    QJsonObject args;
    args["freq_hz"] = 103300000;
    QString result = ai::executeTool("tune_frequency", args, &engine);
    QVERIFY2(result.contains("103.3"), qPrintable(result));
}

void TestAgent::testModeTool() {
    dsp::SpectrumEngine engine;
    QJsonObject args;
    args["mode"] = "AM";
    QString result = ai::executeTool("set_mode", args, &engine);
    QVERIFY2(result.contains("AM"), qPrintable(result));
}

void TestAgent::testConfigFields() {
    ai::AiConfig cfg;
    cfg.apiKey = "test-key-123";
    cfg.baseUrl = "https://test.example.com/v1";
    cfg.model = "test-model";
    QVERIFY(cfg.isConfigured());
    QCOMPARE(cfg.baseUrl, QString("https://test.example.com/v1"));
}

void TestAgent::testLocalFrequency() {
    QRegularExpression freqRe(
        R"(^\s*(\d+(\.\d+)?)\s*([mM]?[Hh]?[Zz]?)\s*$)");
    auto m = freqRe.match("98.5");
    QVERIFY(m.hasMatch());
    QCOMPARE(m.captured(1).toDouble(), 98.5);

    m = freqRe.match("100M");
    QVERIFY(m.hasMatch());
    QCOMPARE(m.captured(1).toDouble(), 100.0);

    m = freqRe.match("439.850");
    QVERIFY(m.hasMatch());
    QCOMPARE(m.captured(1).toDouble(), 439.850);
}

void TestAgent::testLocalMode() {
    QString low = "am";
    QCOMPARE(low.toUpper(), QString("AM"));
    low = "nfm";
    QCOMPARE(low.toUpper(), QString("NFM"));
    low = "ssb";
    QCOMPARE(low.toUpper(), QString("SSB"));
}

void TestAgent::testLocalUnknown() {
    QRegularExpression freqRe(
        R"(^\s*(\d+(\.\d+)?)\s*([mM]?[Hh]?[Zz]?)\s*$)");
    QVERIFY(!freqRe.match("hello world").hasMatch());
}

// The write/read split must match the registered tool set in agent_tools.cpp.
void TestAgent::testWriteToolClassification() {
    QVERIFY(ai::isWriteTool("tune_frequency"));
    QVERIFY(ai::isWriteTool("set_mode"));
    QVERIFY(ai::isWriteTool("set_bandwidth"));
    QVERIFY(ai::isWriteTool("start_recording"));
    QVERIFY(ai::isWriteTool("stop_recording"));
    QVERIFY(ai::isWriteTool("scan_band"));
    // read-only / unknown are never gated
    QVERIFY(!ai::isWriteTool("get_status"));
    QVERIFY(!ai::isWriteTool("some_future_read_tool"));
    QVERIFY(!ai::isWriteTool("totally_unknown"));
}

// Manual mode: a write tool returns the gated JSON and does NOT touch the engine.
void TestAgent::testManualModeGatesWriteTool() {
    dsp::SpectrumEngine engine;
    const double freqBefore = engine.centerFreq();
    const QString modeBefore = engine.demodMode();
    const double bwBefore = engine.bandwidth();

    QJsonObject tune; tune["freq_hz"] = 98500000;
    QString r1 = ai::LLMWorker::dispatchToolCall("tune_frequency", tune, &engine, /*manualMode=*/true);
    QVERIFY2(r1.contains("\"gated\":true"), qPrintable(r1));
    QVERIFY2(r1.contains("\"ok\":false"), qPrintable(r1));
    QVERIFY2(r1.contains("手动模式：未执行 tune_frequency"), qPrintable(r1));

    QJsonObject mode; mode["mode"] = "AM";
    QString r2 = ai::LLMWorker::dispatchToolCall("set_mode", mode, &engine, /*manualMode=*/true);
    QVERIFY2(r2.contains("\"gated\":true"), qPrintable(r2));
    QVERIFY2(r2.contains("手动模式：未执行 set_mode"), qPrintable(r2));

    // No engine state may have changed.
    QCOMPARE(engine.centerFreq(), freqBefore);
    QCOMPARE(engine.demodMode(), modeBefore);
    QCOMPARE(engine.bandwidth(), bwBefore);
}

// Manual mode: the read-only get_status still executes against the live engine.
void TestAgent::testManualModeAllowsReadTool() {
    dsp::SpectrumEngine engine;
    // Put the engine into a known state via a real (AI-takeover) write call.
    QJsonObject m; m["mode"] = "WFM";
    ai::LLMWorker::dispatchToolCall("set_mode", m, &engine, /*manualMode=*/false);

    // In manual mode the read tool must run (not be gated).
    QString status = ai::LLMWorker::dispatchToolCall("get_status", QJsonObject{}, &engine, /*manualMode=*/true);
    QVERIFY2(!status.contains("gated"), qPrintable(status));
    QVERIFY2(status.contains("频率"), qPrintable(status));
    QVERIFY2(status.contains("WFM"), qPrintable(status));
}

// AI takeover (manualMode=false): write tool really executes and changes engine.
void TestAgent::testAiTakeoverRunsWriteTool() {
    dsp::SpectrumEngine engine;
    QJsonObject tune; tune["freq_hz"] = 98500000;
    QString r = ai::LLMWorker::dispatchToolCall("tune_frequency", tune, &engine, /*manualMode=*/false);
    QVERIFY2(!r.contains("gated"), qPrintable(r));
    QVERIFY2(r.contains("98.5"), qPrintable(r));
    QCOMPARE(engine.centerFreq(), 98500000.0);
}

// Manual mode persists through QSettings and is re-read on a fresh Agent.
void TestAgent::testManualModePersistence() {
    QSettings s("MBDSDR", "MBDSDR");
    const QVariant prev = s.value("aiManualMode");  // preserve user value
    s.remove("aiManualMode");
    s.sync();

    {
        ai::Agent a;
        QCOMPARE(a.manualMode(), false);            // default = AI takeover
        a.setManualMode(true);
        QCOMPARE(a.manualMode(), true);
        QCOMPARE(QSettings("MBDSDR", "MBDSDR").value("aiManualMode").toBool(), true);
    }
    {
        ai::Agent a;
        QCOMPARE(a.manualMode(), true);             // re-read from QSettings
        a.setManualMode(false);
    }
    {
        ai::Agent a;
        QCOMPARE(a.manualMode(), false);
    }

    if (prev.isValid()) s.setValue("aiManualMode", prev);
    else s.remove("aiManualMode");
    s.sync();
}

#include <QCoreApplication>
int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    TestAgent t;
    return QTest::qExec(&t, argc, argv);
}
#include "test_agent.moc"
