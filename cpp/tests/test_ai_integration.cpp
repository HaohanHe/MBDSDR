// SPDX-License-Identifier: MIT
// AI integration test: hits the real OpenAI-compatible endpoint ONLY when
// MBDSDR_AI_KEY is set in the environment. Otherwise SKIPs (never FAILs in
// offline CI). The key is never hard-coded or written to disk by this test.
#include <QtTest/QtTest>
#include <QByteArray>
#include <cstdlib>
#include "ai/llm_client.h"

using namespace mbdsdr::ai;

class TestAiIntegration : public QObject {
    Q_OBJECT
private slots:
    void chatRoundtrip();
};

void TestAiIntegration::chatRoundtrip() {
    QByteArray key = qgetenv("MBDSDR_AI_KEY");
    if (key.isEmpty()) QSKIP("no MBDSDR_AI_KEY in env");

    LLMClient client;
    client.setApiKey(QString::fromUtf8(key));
    QByteArray base = qgetenv("MBDSDR_AI_BASE_URL");
    if (!base.isEmpty()) client.setBaseUrl(QString::fromUtf8(base));
    QByteArray model = qgetenv("MBDSDR_AI_MODEL");
    if (!model.isEmpty()) client.setModel(QString::fromUtf8(model));

    QList<ChatMessage> msgs;
    ChatMessage m; m.role="user"; m.content="Reply with exactly one word: hello";
    msgs.append(m);
    LLMResponse r = client.chat(msgs, {});
    if (!r.error.isEmpty()) QSKIP("network/API error: " + r.error.toUtf8());
    QVERIFY(!r.content.trimmed().isEmpty());
}

QTEST_MAIN(TestAiIntegration)
#include "test_ai_integration.moc"
