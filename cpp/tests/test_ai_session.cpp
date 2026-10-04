// SPDX-License-Identifier: MIT
// Unit tests for the AI multi-session store (CRUD + JSON persistence) and the
// pure context-compaction function. No network, no GUI.
#include <QtTest/QtTest>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>

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

    // ---- Phase31 Wave2: A1 compaction ------------------------------------
    void toolOutputPreTrimmedBeforeBudgetCheck();
    void budgetIsWindowRatioNotHardcoded();
    void allUserAsksPreservedInRuleSummary();

    // ---- Phase31 Wave2: A2 session metadata ------------------------------
    void legacySessionLoadsWithKindFallback();
    void kindAndTsRoundTrip();
    void indexWritesVersion();
    void incompleteMarkerSurvivesAndClears();

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

// A1: a single oversized tool result is pre-trimmed (head+tail) BEFORE the whole
// history budget check, so one verbose tool output cannot by itself force a full
// LLM summary. Non-tool messages pass through untouched.
void TestAiSession::toolOutputPreTrimmedBeforeBudgetCheck() {
    QList<ai::ChatMessage> h;
    h.append(ai::ChatMessage{"user", "test"});
    QString huge = QString("IQ-SNAPSHOT-DATA-").repeated(200);   // ~3400 chars
    h.append(ai::ChatMessage{"tool", huge});
    QCOMPARE(huge.size() > 1200, true);

    auto trimmed = ai::truncateLargeToolOutputs(h);
    QCOMPARE(trimmed.size(), 2);
    QCOMPARE(trimmed[0].content, QString("test"));      // user untouched
    QVERIFY2(trimmed[1].content.contains(QString::fromUtf8("[已截断")),
             qPrintable(trimmed[1].content));
    QVERIFY(trimmed[1].content.size() < huge.size());    // actually shrunk

    // A normal (<=1200 char) tool result is NOT trimmed.
    QList<ai::ChatMessage> smallTool;
    smallTool.append(ai::ChatMessage{"tool", QString("short result")});
    auto s2 = ai::truncateLargeToolOutputs(smallTool);
    QCOMPARE(s2[0].content, QString("short result"));
}

// A1: the budget is a fraction of the model window, not a hardcoded 8192.
void TestAiSession::budgetIsWindowRatioNotHardcoded() {
    // Default budget = default window (32768) * 0.75.
    QCOMPARE(ai::defaultContextBudgetTokens(), 24576);

    // Auto budget (budgetTokens=-1) derived from an explicit small window:
    // window=100 -> budget=75, so a 5-turn history (~160 tokens) compacts.
    ai::CompactOptions opt;            // budgetTokens = -1 (auto)
    opt.contextWindowTokens = 40;      // budget = 40 * 0.75 = 30 tokens
    opt.keepRecentRounds = 1;
    auto out = ai::compactContext(makeHistory(5), "SYS", opt, nullptr);
    QVERIFY(out.didCompact);
}

// A1: the honest rule-based summary preserves EVERY dropped user ask (not just
// the first 3) -- the OpenAI-SDK-excluded user asks must survive compaction.
void TestAiSession::allUserAsksPreservedInRuleSummary() {
    auto h = makeHistory(8);   // 16 messages; keep 1 recent round
    auto out = ai::compactContext(h, "SYS", ai::CompactOptions{30, 1}, nullptr);
    QVERIFY(out.didCompact);
    // Dropped asks are questions 0..6; the one-liners keep them all.
    for (int i = 0; i <= 6; ++i)
        QVERIFY2(out.summaryText.contains(QString("question number %1").arg(i)),
                 qPrintable(QString("missing ask %1").arg(i)));
    // Kept recent turn (question 7) stays verbatim in the message list.
    bool q7 = false;
    for (const auto& m : out.messages)
        if (m.content == "question number 7 about tuning") q7 = true;
    QVERIFY(q7);
}

// A2: a legacy session file (no kind/ts) loads with kind == role, ts == 0.
void TestAiSession::legacySessionLoadsWithKindFallback() {
    // Write a legacy index (no version) and a legacy session (no kind/ts).
    QDir().mkpath(dir_ + "/sessions");
    QFile idx(dir_ + "/index.json");
    idx.open(QIODevice::WriteOnly);
    idx.write(R"({"current":"s1","sessions":[{"id":"s1","title":"旧","updatedAt":1}]})");
    idx.close();
    QFile sf(dir_ + "/sessions/s1.json");
    sf.open(QIODevice::WriteOnly);
    sf.write(R"({"id":"s1","title":"旧","messages":[{"role":"user","content":"hi"},{"role":"assistant","content":"yo"}]})");
    sf.close();

    ai::AiSessionStore store(dir_);
    auto ms = store.messages("s1");
    QCOMPARE(ms.size(), 2);
    QCOMPARE(ms[0].role, QString("user"));
    QCOMPARE(ms[0].kind, QString("user"));   // fallback: kind == role
    QCOMPARE(ms[0].ts, qint64(0));
    QCOMPARE(ms[1].kind, QString("assistant"));
}

// A2: explicit kind/ts round-trips through the JSON layer.
void TestAiSession::kindAndTsRoundTrip() {
    ai::AiSessionStore store(dir_);
    const QString id = store.currentId();
    store.appendMessage(id, ai::SessionMessage{"tool", R"({"ok":true})",
                                               "tool_result", 1700000000123LL});
    QVERIFY(store.messages(id)[0].kind.isEmpty() == false);

    ai::AiSessionStore reloaded(dir_);
    auto ms = reloaded.messages(id);
    QCOMPARE(ms.size(), 1);
    QCOMPARE(ms[0].kind, QString("tool_result"));
    QCOMPARE(ms[0].ts, 1700000000123LL);
}

// A2: index.json carries the on-disk schema version (migration hook).
void TestAiSession::indexWritesVersion() {
    ai::AiSessionStore store(dir_);
    store.createSession("x");
    QFile f(dir_ + "/index.json");
    f.open(QIODevice::ReadOnly);
    QByteArray raw = f.readAll();
    QVERIFY(QJsonDocument::fromJson(raw).object().value("version").toInt() ==
            ai::AiSessionStore::kIndexVersion);
}

// A2: incomplete (cut-off) marker survives a reload and is cleared by a new user
// turn (a crashed half-reply is honestly badged, then superseded).
void TestAiSession::incompleteMarkerSurvivesAndClears() {
    ai::AiSessionStore store(dir_);
    const QString id = store.currentId();
    store.setIncomplete(id, true);
    QVERIFY(store.isIncomplete(id));

    // Survives a reload (crash honesty).
    ai::AiSessionStore reloaded(dir_);
    QVERIFY(reloaded.isIncomplete(id));

    // A brand-new user message clears the stale incomplete flag.
    reloaded.appendMessage(id, ai::SessionMessage{"user", "next"});
    QCOMPARE(reloaded.isIncomplete(id), false);
}

#include <QCoreApplication>
int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    TestAiSession t;
    return QTest::qExec(&t, argc, argv);
}
#include "test_ai_session.moc"
