// SPDX-License-Identifier: MIT
// Unit tests for the AI multi-session store (CRUD + JSON persistence) and the
// pure context-compaction function. No network, no GUI.
#include <QtTest/QtTest>
#include <QDir>
#include <QFile>
#include <QFileInfo>

#include "ai/ai_session_store.h"
#include "ai/ai_context.h"

using namespace mbdsdr;

class TestAiSession : public QObject {
    Q_OBJECT
private:
    QString tmpDir();
private slots:
    void init();
    void cleanup();
    void testDefaultOneEmptySession();
    void testCreateRenameDelete();
    void testPersistenceRoundTrip();
    void testDeleteNeverEmpties();
    void testCompactBelowBudgetNoOp();
    void testCompactKeepsRecentAndSummaryRole();
    void testCompactThreshold();
    void testRuleSummaryHonest();
    void testLlmSummaryCallbackUsed();

private:
    QString dir_;
};

QString TestAiSession::tmpDir() {
    return QDir::tempPath() + "/mbdsdr_aisession_" +
           QString::number(QCoreApplication::applicationPid()) + "_" +
           QString::number(reinterpret_cast<quintptr>(this));
}

void TestAiSession::init() {
    dir_ = tmpDir();
    QDir().rmdir(dir_);  // must not pre-exist
}

void TestAiSession::cleanup() {
    QDir(dir_).removeRecursively();
}

void TestAiSession::testDefaultOneEmptySession() {
    ai::AiSessionStore store(dir_);
    auto s = store.sessions();
    QCOMPARE(s.size(), 1);             // exactly one session, no seeded chat
    QVERIFY(!s[0].id.isEmpty());
    QVERIFY(store.messages(store.currentId()).isEmpty());
}

void TestAiSession::testCreateRenameDelete() {
    ai::AiSessionStore store(dir_);
    const QString first = store.currentId();

    const QString s2 = store.createSession(QString::fromUtf8("扫描记录"));
    QCOMPARE(store.sessions().size(), 2);
    QCOMPARE(store.currentId(), s2);

    store.renameSession(s2, QString::fromUtf8("改个名"));
    bool foundRenamed = false;
    for (const auto& i : store.sessions())
        if (i.id == s2 && i.title == QString::fromUtf8("改个名")) foundRenamed = true;
    QVERIFY(foundRenamed);

    store.setCurrent(first);
    QCOMPARE(store.currentId(), first);

    store.deleteSession(s2);
    QCOMPARE(store.sessions().size(), 1);
    QCOMPARE(store.sessions()[0].id, first);
}

void TestAiSession::testPersistenceRoundTrip() {
    QString id;
    {
        ai::AiSessionStore store(dir_);
        id = store.createSession(QString::fromUtf8("持久化"));
        store.appendMessage(id, ai::SessionMessage{"user", "98.5"});
        store.appendMessage(id, ai::SessionMessage{"assistant", "已调谐到 98.500 MHz"});
        store.appendMessage(id, ai::SessionMessage{"summary", "此前 1 轮对话已压缩"});
    }
    // A fresh store on the SAME directory must reload the messages.
    ai::AiSessionStore store(dir_);
    bool foundTitle = false;
    for (const auto& i : store.sessions())
        if (i.id == id && i.title == QString::fromUtf8("持久化")) foundTitle = true;
    QVERIFY(foundTitle);
    auto msgs = store.messages(id);
    QCOMPARE(msgs.size(), 3);
    QCOMPARE(msgs[0].role, QString("user"));
    QCOMPARE(msgs[0].content, QString("98.5"));
    QCOMPARE(msgs[2].role, QString("summary"));

    // On-disk files actually exist.
    QVERIFY(QFileInfo::exists(dir_ + "/index.json"));
    QVERIFY(QFileInfo::exists(dir_ + "/sessions/" + id + ".json"));
}

void TestAiSession::testDeleteNeverEmpties() {
    ai::AiSessionStore store(dir_);
    auto s = store.sessions();
    QCOMPARE(s.size(), 1);
    store.deleteSession(s[0].id);   // deleting the only one must not drop it
    QCOMPARE(store.sessions().size(), 1);
    QVERIFY(!store.currentId().isEmpty());
}

// ---- compactContext -----------------------------------------------------

static QList<ai::ChatMessage> makeHistory(int userTurns) {
    QList<ai::ChatMessage> h;
    for (int i = 0; i < userTurns; ++i) {
        h.append(ai::ChatMessage{"user", QString("question number %1 about tuning").arg(i)});
        h.append(ai::ChatMessage{"assistant", QString("answer %1").arg(i)});
    }
    return h;
}

void TestAiSession::testCompactBelowBudgetNoOp() {
    auto h = makeHistory(2);
    auto out = ai::compactContext(h, "SYS", ai::CompactOptions{100000, 4});
    QVERIFY(!out.didCompact);
    QCOMPARE(out.messages.size(), 1 + h.size());  // system + all history
    QCOMPARE(out.messages[0].role, QString("system"));
    QCOMPARE(out.messages[0].content, QString("SYS"));
}

void TestAiSession::testCompactKeepsRecentAndSummaryRole() {
    auto h = makeHistory(8);
    ai::CompactOptions opt;
    opt.budgetTokens = 40;          // tiny -> force compaction
    opt.keepRecentRounds = 2;
    auto out = ai::compactContext(h, "SYS", opt, nullptr);
    QVERIFY(out.didCompact);
    QCOMPARE(out.messages[0].role, QString("system"));
    // Exactly one summary entry with role == "summary".
    int summaryCount = 0;
    for (const auto& m : out.messages)
        if (m.role == "summary") ++summaryCount;
    QCOMPARE(summaryCount, 1);
    // The last two user turns (questions 6 and 7) must be kept verbatim.
    bool q6 = false, q7 = false;
    for (const auto& m : out.messages) {
        if (m.content == "question number 6 about tuning") q6 = true;
        if (m.content == "question number 7 about tuning") q7 = true;
    }
    QVERIFY(q6);
    QVERIFY(q7);
    // The oldest turns must be gone (folded into the summary).
    bool q0 = false;
    for (const auto& m : out.messages)
        if (m.content == "question number 0 about tuning") q0 = true;
    QVERIFY(!q0);
}

void TestAiSession::testCompactThreshold() {
    // Just under budget -> no compaction.
    auto small = makeHistory(1);
    auto below = ai::compactContext(small, "SYS", ai::CompactOptions{500, 2});
    QVERIFY(!below.didCompact);

    // Just over budget -> compaction fires.
    auto big = makeHistory(20);
    auto above = ai::compactContext(big, "SYS", ai::CompactOptions{30, 2});
    QVERIFY(above.didCompact);
    QVERIFY(above.compressedRounds > 0);
}

void TestAiSession::testRuleSummaryHonest() {
    auto h = makeHistory(5);
    auto out = ai::compactContext(h, "SYS", ai::CompactOptions{30, 1}, nullptr);
    QVERIFY(out.didCompact);
    // No LLM callback -> the honest rule-based count summary, annotated.
    QVERIFY(out.summaryText.contains(QString::fromUtf8("已压缩")));
}

void TestAiSession::testLlmSummaryCallbackUsed() {
    auto h = makeHistory(5);
    auto out = ai::compactContext(h, "SYS", ai::CompactOptions{30, 1},
        [](const QList<ai::ChatMessage>&) { return QString::fromUtf8("LLM 生成的摘要"); });
    QVERIFY(out.didCompact);
    QCOMPARE(out.summaryText, QString::fromUtf8("LLM 生成的摘要"));
}

#include <QCoreApplication>
int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    TestAiSession t;
    return QTest::qExec(&t, argc, argv);
}
#include "test_ai_session.moc"
