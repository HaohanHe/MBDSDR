// SPDX-License-Identifier: MIT
// LLM plan-parser tests: trustworthy parsing (known tools accepted, args pass
// through), honest rejection (unknown tool / empty steps / garbage => ok=false,
// no guessing), and the fallback contract the UI relies on.
#include <QtTest/QtTest>
#include "ai/plan_parser.h"

using namespace mbdsdr;

class TestPlanParser : public QObject {
    Q_OBJECT
private slots:
    void parsesValidPlan();
    void rejectsUnknownTool();
    void rejectsGarbage();
    void rejectsEmptySteps();
};

void TestPlanParser::parsesValidPlan() {
    const QString reply =
        QString::fromUtf8("好的，计划如下：\n```json\n"
            "{\"name\":\"调谐\",\"steps\":["
            "{\"tool\":\"tune_frequency\",\"args\":{\"freq_hz\":100100000},\"description\":\"调谐\"},"
            "{\"tool\":\"get_status\",\"args\":{},\"description\":\"读状态\"}"
            "]}\n```\n完成。");
    ai::ParsedPlan pp = ai::parsePlanFromLlm(reply);
    QVERIFY2(pp.ok, qPrintable(pp.error));
    QCOMPARE(pp.plan.name, QString::fromUtf8("调谐"));
    QCOMPARE(pp.plan.steps.size(), 2);
    QCOMPARE(pp.plan.steps[0].tool, QString::fromLatin1("tune_frequency"));
    QCOMPARE(pp.plan.steps[0].args.value("freq_hz").toDouble(), 100100000.0);
    QCOMPARE(pp.plan.steps[1].tool, QString::fromLatin1("get_status"));
}

void TestPlanParser::rejectsUnknownTool() {
    const QString reply =
        QString::fromUtf8("{\"steps\":["
            "{\"tool\":\"rm -rf\",\"args\":{},\"description\":\"坏工具\"}]}");
    ai::ParsedPlan pp = ai::parsePlanFromLlm(reply);
    QVERIFY2(!pp.ok, "unknown tool must be rejected, not guessed");
    QVERIFY(!pp.error.isEmpty());
}

void TestPlanParser::rejectsGarbage() {
    ai::ParsedPlan pp = ai::parsePlanFromLlm(QString::fromUtf8("随便聊聊，没有 JSON。"));
    QVERIFY(!pp.ok);
}

void TestPlanParser::rejectsEmptySteps() {
    ai::ParsedPlan pp = ai::parsePlanFromLlm(
        QString::fromUtf8("{\"steps\":[]}"));
    QVERIFY(!pp.ok);
}

QTEST_MAIN(TestPlanParser)
#include "test_plan_parser.moc"
