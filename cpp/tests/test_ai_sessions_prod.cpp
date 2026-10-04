// SPDX-License-Identifier: MIT
//
// *** SYNTHETIC/OFFSCREEN TEST -- drives the PRODUCTION MainWindow AI panel.
//     NOT REAL RECEPTION, NOT A REAL LLM. CI has no API key, so the streaming
//     signals can't come from the network; the test calls the SAME handlers the
//     Agent signals are wired to (harnessAiBeginUserTurn / harnessAiSetPartial /
//     harnessAiFinishResponse). The engine lands on the honest empty source. ***
//
// Phase32 block1: incomplete production wiring of the AI session panel. Boots
// the real MainWindow offscreen (env-isolated store dir) and asserts:
//
//   1. SESSION CRUD, REAL JSON ON DISK
//      - first run creates exactly ONE empty session;
//      - 新会话 button adds a second and honestly selects it (index.json
//        "current" + sessions[] written to disk);
//      - the combo switches currentId_ to the picked session;
//      - a user turn lands in sessions/<id>.json on disk;
//      - 删除 the CURRENT session re-points currentId_ at the survivor (never
//        stays on a dead id, never leaves zero sessions) and removes the file.
//
//   2. TRANSIENT -> SETTLE, NO DUPLICATION
//      - a pending turn shows the single transient "思考中…" line;
//      - partial chunks REPLACE that line (exactly one, never appended);
//      - the final reply settles EXACTLY once: the cursor/transient disappears,
//        the final line appears once, and the store holds 1 user + 1 assistant.
//
//   3. INCOMPLETE BADGE DISPLAY + CLEAR
//      - a pending (unsettled) turn badges the session 〔未完成〕 in the combo
//        AND sets incomplete=true in index.json;
//      - settling the reply clears the badge + the on-disk flag.
#include <QApplication>
#include <QComboBox>
#include <QPushButton>
#include <QPlainTextEdit>
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QDateTime>
#include <QRandomGenerator>
#include <QtGlobal>
#include <QTcpServer>
#include <QHostAddress>

#include "core/tokens.h"
#include "ui/main_window.h"
#include "ai/ai_session_store.h"

using namespace mbdsdr;

// A fresh, empty dir under the temp tree (the store creates index.json +
// sessions/ inside it). Unique per block so runs never collide.
static QString freshStoreDir() {
    QString base = QDir::tempPath() + "/mbdsdr_p32_sessions_" +
                   QString::number(QDateTime::currentMSecsSinceEpoch()) + "_" +
                   QString::number(QRandomGenerator::global()->bounded(1'000'000));
    QDir().mkpath(base);
    return base;
}

static QByteArray readFile(const QString& path) {
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) return {};
    return f.readAll();
}
static QJsonObject readJson(const QString& path) {
    return QJsonDocument::fromJson(readFile(path)).object();
}

// Bind an ephemeral loopback port, then close it, so MainWindow's production
// control-HTTP server binds somewhere that can't collide (non-fatal if it
// doesn't, but we keep it tidy and parallel-safe like the e2e_prod test).
static quint16 freeLoopbackPort() {
    QTcpServer probe;
    if (!probe.listen(QHostAddress(QHostAddress::LocalHost), 0)) return 0;
    const quint16 p = probe.serverPort();
    probe.close();
    return p;
}

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());
    qputenv("MBDSDR_TEST_SOURCE", "1");   // synthetic engine source (no real HW)

    int failures = 0;
    auto check = [&](bool cond, const char* msg) {
        if (!cond) { ++failures; qWarning("FAIL: %s", msg); }
        else       { qInfo("ok: %s", msg); }
    };

    // ======================================================================
    // 1. SESSION CRUD -- create / switch / delete, real JSON on disk
    // ======================================================================
    {
        const quint16 port = freeLoopbackPort();
        if (port) qputenv(mbdsdr::tokens::kControlHttpPortEnvVar,
                          QByteArray::number(port));
        const QString dir = freshStoreDir();
        qputenv("MBDSDR_AI_SESSIONS_DIR", dir.toLocal8Bit());

        MainWindow win;
        win.resize(1000, 700);
        win.show();

        auto* store = win.aiSessionStore();
        auto* combo = win.findChild<QComboBox*>("aiSessionCombo");
        auto* newBtn = win.findChild<QPushButton*>("aiNewSessionBtn");
        auto* delBtn = win.findChild<QPushButton*>("aiDeleteSessionBtn");
        check(store != nullptr, "crud: store exposed via harness accessor");
        check(combo && newBtn && delBtn, "crud: session bar widgets found by objectName");

        check(store->sessions().size() == 1, "crud: first run creates exactly 1 empty session");
        const QString s0 = store->currentId();
        check(!s0.isEmpty(), "crud: a session is selected on first run");

        // --- create a second session, honestly selected, written to disk ---
        newBtn->click();
        check(store->sessions().size() == 2, "crud: 新会话 button creates a 2nd session");
        const QString s1 = store->currentId();
        check(s1 != s0, "crud: new session becomes the current one");

        QJsonObject idx = readJson(dir + "/index.json");   // read the real on-disk index
        check(idx.value("current").toString() == s1, "crud: disk index.current == new session");
        check(idx.value("sessions").toArray().size() == 2, "crud: disk index lists 2 sessions");

        // --- switch back to s0 via the combo ---
        combo->setCurrentIndex(0);   // order: s0 (oldest) first, s1 second
        check(store->currentId() == s0, "crud: combo switch moves currentId_ to the picked session");

        // --- a user turn really lands in sessions/<id>.json on disk ---
        win.harnessAiBeginUserTurn(QString::fromUtf8("频率 100.5"));
        win.harnessAiFinishResponse(QString::fromUtf8("好的"));
        QJsonObject s0doc = readJson(dir + "/sessions/" + s0 + ".json");
        bool foundUser = false, foundAsst = false;
        for (const QJsonValue& v : s0doc.value("messages").toArray()) {
            const QJsonObject o = v.toObject();
            if (o.value("role").toString() == "user" &&
                o.value("content").toString().contains(QString::fromUtf8("频率 100.5")))
                foundUser = true;
            if (o.value("role").toString() == "assistant" &&
                o.value("content").toString() == QString::fromUtf8("好的"))
                foundAsst = true;
        }
        check(foundUser,  "crud: user line persisted to sessions/<id>.json");
        check(foundAsst, "crud: assistant line persisted to sessions/<id>.json");

        // --- delete the CURRENT session: honestly re-point to the survivor ---
        win.harnessSetAutoConfirmSessionDelete(true);   // offscreen: skip the modal
        delBtn->click();
        check(store->sessions().size() == 1, "crud: delete leaves exactly 1 session (never zero)");
        check(store->currentId() == s1, "crud: deleting current honestly switches to survivor s1");
        check(!QFile::exists(dir + "/sessions/" + s0 + ".json"),
              "crud: deleted session file removed from disk");
        check(combo->currentData().toString() == s1, "crud: combo now shows the survivor");
    }

    // ======================================================================
    // 2. TRANSIENT -> SETTLE, NO DUPLICATION  (error keeps the partial text)
    // ======================================================================
    {
        const QString dir = freshStoreDir();
        qputenv("MBDSDR_AI_SESSIONS_DIR", dir.toLocal8Bit());
        MainWindow win;
        win.show();
        auto* store = win.aiSessionStore();

        win.harnessAiBeginUserTurn(QString::fromUtf8("调一下频率"));
        const QString tPending = win.harnessAiChatText();
        check(tPending.contains(QString::fromUtf8("You: 调一下频率")),
              "transient: user line rendered after begin-turn");
        check(tPending.contains(QString::fromUtf8("思考中…")),
              "transient: pending transient line shown");

        // Partial chunks REPLACE the single transient line (never append).
        win.harnessAiSetPartial(QString::fromUtf8("正在"));
        win.harnessAiSetPartial(QString::fromUtf8("正在调谐"));
        const QString tPartial = win.harnessAiChatText();
        check(tPartial.contains(QString::fromUtf8("AI: 正在调谐 ▌")),
              "transient: latest partial replaces the line");
        check(tPartial.count(QString::fromUtf8("正在调谐")) == 1,
              "transient: partial shown exactly once (chunks replace, not append)");

        // Settle: the final (possibly error+partial) text lands once, transient gone.
        win.harnessAiFinishResponse(QString::fromUtf8("正在调谐到 100.5 MHz"));
        const QString tFinal = win.harnessAiChatText();
        check(!tFinal.contains("▌"),        "settle: transient cursor gone after final");
        check(!tFinal.contains(QString::fromUtf8("思考中")),
              "settle: pending line cleared after final");
        check(tFinal.count(QString::fromUtf8("正在调谐到 100.5 MHz")) == 1,
              "settle: final line rendered exactly once (dedup)");
        const auto msgs = store->messages(store->currentId());
        check(msgs.size() == 2, "settle: store holds exactly 1 user + 1 assistant (no dup, no transient)");
    }

    // ======================================================================
    // 3. INCOMPLETE BADGE -- shown while pending, cleared on settle
    // ======================================================================
    {
        const QString dir = freshStoreDir();
        qputenv("MBDSDR_AI_SESSIONS_DIR", dir.toLocal8Bit());
        MainWindow win;
        win.show();
        auto* store = win.aiSessionStore();
        auto* combo = win.findChild<QComboBox*>("aiSessionCombo");

        // A turn BEGUN but not settled leaves the session flagged incomplete.
        win.harnessAiBeginUserTurn(QString::fromUtf8("一个没回答完的问题"));
        check(combo->currentText().contains(QString::fromUtf8("未完成")),
              "badge: combo shows 〔未完成〕 while a reply is pending");
        QJsonObject idx = readJson(dir + "/index.json");
        bool incOnDisk = false;
        for (const QJsonValue& v : idx.value("sessions").toArray())
            if (v.toObject().value("id").toString() == store->currentId())
                incOnDisk = v.toObject().value("incomplete").toBool(false);
        check(incOnDisk, "badge: index.json incomplete==true while pending");

        // Settling the reply clears the badge + the on-disk flag.
        win.harnessAiFinishResponse(QString::fromUtf8("答案"));
        check(!combo->currentText().contains(QString::fromUtf8("未完成")),
              "badge: 〔未完成〕 cleared once the reply settles");
        QJsonObject idx2 = readJson(dir + "/index.json");
        bool incCleared = false;
        for (const QJsonValue& v : idx2.value("sessions").toArray())
            if (v.toObject().value("id").toString() == store->currentId())
                incCleared = v.toObject().value("incomplete").toBool(true);
        check(!incCleared, "badge: index.json incomplete==false after settle");
    }

    qInfo(failures == 0 ? "ALL PASS" : "FAILURES PRESENT");
    return failures ? 1 : 0;
}
