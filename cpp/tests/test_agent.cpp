// SPDX-License-Identifier: MIT
#include <QtTest/QtTest>
#include <QRegularExpression>

#include "ai/agent_tools.h"
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
};

void TestAgent::testToolParse() {
    auto tools = ai::toolDefs();
    QCOMPARE(tools.size(), 7);
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

#include <QCoreApplication>
int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    TestAgent t;
    return QTest::qExec(&t, argc, argv);
}
#include "test_agent.moc"
