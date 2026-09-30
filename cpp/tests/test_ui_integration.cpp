// SPDX-License-Identifier: MIT
//
// *** NOT HARDWARE / 非硬件 *** -- offscreen MainWindow integration tests.
//
// Drives the real MainWindow (engine runs on the offline TestSignalSource
// fallback, never fabricated hardware) and asserts:
//   1. AI manual-mode checkbox is wired to Agent::setManualMode (initial state
//      round-trips QSettings, toggling flips the backend) and a gated write
//      tool result is annotated in the chat as "[已拦截·手动模式: ...]" while an
//      executed tool keeps the original "[调用工具: ...]" wording.
//   2. The scan "存入书签" button is disabled unless the scanner is in Hit; on a
//      real hit it captures hitFrequency + live mode/bandwidth into a bookmark,
//      refreshes the table, and double-clicking the row tunes the engine there.
//   3. The squelch "自动门限" checkable sets the slider to the REAL same-domain
//      audio-RMS noise floor + margin (tokens::kSquelchAutoMarginDb), clamped to
//      the token slider range.
//
// QSettings is redirected to a throwaway dir in initTestCase so these tests
// never touch the user's persisted bookmarks / AI / squelch state.
#include <QtTest/QtTest>
#include <QApplication>
#include <QCheckBox>
#include <QComboBox>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QDoubleSpinBox>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLineEdit>
#include <QListWidget>
#include <QListWidgetItem>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QSettings>
#include <QSlider>
#include <QTableWidget>
#include <QTimer>

#include <cmath>

#include "core/tokens.h"
#include "ui/main_window.h"
#include "ui/bookmark_manager.h"
#include "ai/agent.h"
#include "ai/ai_session_store.h"
#include "dsp/frequency_scanner.h"
#include "dsp/spectrum_engine.h"

using namespace mbdsdr;

class TestUiIntegration : public QObject {
    Q_OBJECT
private:
    static QString tmpSettingsDir;
private slots:
    void initTestCase();
    void aiManualToggleWiresAgentAndAnnotatesGated();
    void aiStreamingPartialReplacesTransientNoDup();
    void aiSessionSwitcherCrud();
    void scanHitSaveBookmarkThenJump();
    void squelchAutoFollowsSameDomainFloor();
    void vfoCopyDuplicatesSourceParams();
    void vfoDoubleClickSwitchesActive();
    void vfoNamingPersistsRoundTrip();
    void vfoRemoveRefreshesList();
};

QString TestUiIntegration::tmpSettingsDir;

void TestUiIntegration::initTestCase() {
    tmpSettingsDir = QDir::tempPath() + "/mbdsdr_uitest_" +
                     QString::number(QCoreApplication::applicationPid());
    QDir().mkpath(tmpSettingsDir);
    // Redirect all QSettings("MBDSDR","MBDSDR") storage to the throwaway dir so
    // the tests never pollute the real user config / bookmarks.
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope, tmpSettingsDir);
    // Redirect the AI session store to a throwaway dir too.
    qputenv("MBDSDR_AI_SESSIONS_DIR",
            (tmpSettingsDir + "/ai_sessions").toUtf8());
}

void TestUiIntegration::aiManualToggleWiresAgentAndAnnotatesGated() {
    MainWindow win;
    win.show();
    QApplication::processEvents();

    auto* chk = win.findChild<QCheckBox*>("aiManualModeCheck");
    auto* agent = win.findChild<ai::Agent*>();
    auto* chat = win.findChild<QPlainTextEdit*>("aiChat");
    QVERIFY(chk);
    QVERIFY(agent);
    QVERIFY(chat);

    // Initial state round-trips the persisted backend value.
    QCOMPARE(chk->isChecked(), agent->manualMode());

    // Toggling the checkbox must drive the backend (and persist it).
    chk->setChecked(true);
    QVERIFY(agent->manualMode());
    chk->setChecked(false);
    QVERIFY(!agent->manualMode());

    // A gated write tool result JSON must be annotated, not shown as executed.
    const QString gatedJson =
        "{\"ok\":false,\"gated\":true,\"error\":\"手动模式：未执行 tune_frequency\"}";
    QMetaObject::invokeMethod(agent, "toolCalled", Qt::DirectConnection,
                              Q_ARG(QString, "tune_frequency"),
                              Q_ARG(QString, gatedJson));
    QVERIFY2(chat->toPlainText().contains("[已拦截·手动模式: tune_frequency]"),
             "gated write tool must carry the restrained manual-mode annotation");

    // An executed (non-gated) tool keeps the original wording.
    QMetaObject::invokeMethod(agent, "toolCalled", Qt::DirectConnection,
                              Q_ARG(QString, "get_status"),
                              Q_ARG(QString, "频率=100MHz 模式=NFM"));
    QVERIFY2(chat->toPlainText().contains("[调用工具: get_status"),
             "executed tool keeps the original 调用工具 wording");
}

// Streaming: partial updates must REPLACE the single transient line; the final
// reply must appear exactly once and the partial text must not linger.
void TestUiIntegration::aiStreamingPartialReplacesTransientNoDup() {
    MainWindow win;
    win.show();
    QApplication::processEvents();

    auto* agent = win.findChild<ai::Agent*>();
    auto* chat = win.findChild<QPlainTextEdit*>("aiChat");
    QVERIFY(agent);
    QVERIFY(chat);

    // First partial: transient line shows it.
    QMetaObject::invokeMethod(agent, "partialReady", Qt::DirectConnection,
                              Q_ARG(QString, QString::fromUtf8("部分思考")));
    QVERIFY2(chat->toPlainText().contains(QString::fromUtf8("部分思考")),
             "first partial must render in the transient line");
    // Second partial REPLACES the transient (the shorter earlier text is gone).
    QMetaObject::invokeMethod(agent, "partialReady", Qt::DirectConnection,
                              Q_ARG(QString, QString::fromUtf8("部分思考：已经调谐到 98.5")));
    QString mid = chat->toPlainText();
    QVERIFY2(mid.contains(QString::fromUtf8("已经调谐到 98.5")),
             "second partial must be rendered");
    QVERIFY2(!mid.contains(QString::fromUtf8("AI: 部分思考\n")),
             "the earlier partial must have been replaced, not appended");

    // Final reply: transient cleared, final persisted exactly once.
    QMetaObject::invokeMethod(agent, "responseReady", Qt::DirectConnection,
                              Q_ARG(QString, QString::fromUtf8("最终回复完整内容XYZ")));
    QString text = chat->toPlainText();
    int count = 0, idx = 0;
    while ((idx = text.indexOf(QString::fromUtf8("最终回复完整内容XYZ"), idx)) >= 0) {
        ++count;
        idx += 1;
    }
    QCOMPARE(count, 1);
    QVERIFY2(!text.contains(QString::fromUtf8("部分思考")),
             "transient partial text must be gone after the final reply");
}

// Session switcher: new / rename / delete round-trips through the store and
// the combo, switching sessions shows the right persisted messages.
void TestUiIntegration::aiSessionSwitcherCrud() {
    MainWindow win;
    win.show();
    QApplication::processEvents();

    auto* combo = win.findChild<QComboBox*>("aiSessionCombo");
    auto* newBtn = win.findChild<QPushButton*>("aiNewSessionBtn");
    auto* delBtn = win.findChild<QPushButton*>("aiDeleteSessionBtn");
    auto* chat = win.findChild<QPlainTextEdit*>("aiChat");
    auto* store = win.aiSessionStore();
    QVERIFY(combo && newBtn && delBtn && chat && store);

    QCOMPARE(combo->count(), 1);                 // default one empty session

    // New session -> combo grows, current switches to it.
    newBtn->click();
    QCOMPARE(combo->count(), 2);
    QCOMPARE(store->currentId(), combo->currentData().toString());

    // Drop a message into the current (new) session, then switch back to the
    // first: the chat must show the FIRST session's (empty) view, not the new
    // session's message.
    store->appendMessage(store->currentId(),
                         ai::SessionMessage{"user", QString::fromUtf8("在新会话里")});
    QVERIFY(chat->toPlainText().contains(QString::fromUtf8("在新会话里")));

    combo->setCurrentIndex(0);
    QApplication::processEvents();
    QVERIFY2(!chat->toPlainText().contains(QString::fromUtf8("在新会话里")),
             "switching sessions must show the selected session's messages only");

    // Delete the current (first) session: count drops back to 1.
    delBtn->click();
    QCOMPARE(combo->count(), 1);
}

void TestUiIntegration::scanHitSaveBookmarkThenJump() {
    MainWindow win;
    win.show();
    QApplication::processEvents();

    auto* saveBtn = win.findChild<QPushButton*>("scanSaveBmBtn");
    auto* scanStart = win.findChild<QPushButton*>("scanStartBtn");
    auto* scanThr = win.findChild<QDoubleSpinBox*>("scanThrSpin");
    auto* bmTable = win.findChild<QTableWidget*>("bmTable");
    QVERIFY(saveBtn);
    QVERIFY(scanStart);
    QVERIFY(scanThr);
    QVERIFY(bmTable);
    QVERIFY(win.scanner());
    QVERIFY(win.bookmarkManager());

    // Idle: the one-shot save button must be disabled.
    QVERIFY2(!saveBtn->isEnabled(), "save-bookmark button starts disabled");

    // Very low hit threshold so the offline test tone counts as a hit.
    scanThr->setValue(-120.0);
    const int beforeCount = win.bookmarkManager()->count();

    scanStart->click();
    // Let the 50 ms scan timer drive the headless scanner into a Hit.
    QTRY_VERIFY_WITH_TIMEOUT(win.scanner()->state() == dsp::ScanState::Hit, 3000);
    QVERIFY2(saveBtn->isEnabled(), "save-bookmark button enabled only on Hit");

    const double hitF = win.scanner()->hitFrequency();
    QVERIFY(hitF > 0.0);

    // Accept the modal add-bookmark dialog shortly after it opens.
    QTimer::singleShot(300, [&]() {
        auto* dlg = qobject_cast<QDialog*>(QApplication::activeModalWidget());
        if (!dlg) return;
        auto* bb = dlg->findChild<QDialogButtonBox*>();
        if (bb && bb->button(QDialogButtonBox::Ok))
            bb->button(QDialogButtonBox::Ok)->click();
    });
    saveBtn->click();   // blocks until the dialog is accepted

    // A new bookmark carrying the hit frequency must exist, sorted into the list.
    QVERIFY2(win.bookmarkManager()->count() == beforeCount + 1,
             "accepting the dialog must add exactly one bookmark");
    int row = -1;
    for (int i = 0; i < win.bookmarkManager()->list().size(); ++i) {
        if (std::abs(win.bookmarkManager()->list().at(i).frequencyHz - hitF) < 1000.0) {
            row = i;
            break;
        }
    }
    QVERIFY2(row >= 0, "the saved bookmark must carry the real hit frequency");
    // Live mode / bandwidth must have been copied in.
    const ui::Bookmark& saved = win.bookmarkManager()->list().at(row);
    QVERIFY2(!saved.mode.isEmpty(), "bookmark mode must be captured live");

    // Stop the scan so it stops retuning, then double-click the row -> tune here.
    win.scanner()->stop();
    QMetaObject::invokeMethod(bmTable, "cellDoubleClicked", Qt::DirectConnection,
                             Q_ARG(int, row), Q_ARG(int, 0));
    QVERIFY2(std::abs(win.engine()->centerFreq() - hitF) < 5000.0,
             "double-clicking the bookmark row must tune the engine to hit freq");
}

void TestUiIntegration::squelchAutoFollowsSameDomainFloor() {
    MainWindow win;
    win.show();
    QApplication::processEvents();
    QTest::qWait(500);   // let the engine track the real audio-RMS floor

    auto* slider = win.findChild<QSlider*>("squelchSlider");
    auto* autoBtn = win.findChild<QPushButton*>("squelchAutoBtn");
    QVERIFY(slider);
    QVERIFY(autoBtn);
    QVERIFY(autoBtn->isCheckable());

    // Slider range must come from the tokens (no bare numbers).
    QCOMPARE(slider->minimum(), tokens::kSquelchMinDb);
    QCOMPARE(slider->maximum(), tokens::kSquelchMaxDb);

    const double floorDb = win.engine()->audioNoiseFloorDbfs();
    autoBtn->setChecked(true);   // applies floor + margin immediately
    const int v = slider->value();

    const double expected = std::clamp(
        floorDb + tokens::kSquelchAutoMarginDb,
        double(tokens::kSquelchMinDb), double(tokens::kSquelchMaxDb));
    QVERIFY2(std::abs(v - int(std::round(expected))) <= 2,
             "auto gate must set slider to floor(real audio-RMS domain) + margin");
}

// Wait until the engine's VFO snapshot has `n` channels. The UI refresh runs on
// a queued vfoListChanged, so pump the event loop until markers stabilize.
static void waitVfoCount(MainWindow& win, int n) {
    QTRY_VERIFY_WITH_TIMEOUT(win.engine()->vfoMarkers().size() == n, 3000);
    QTRY_VERIFY_WITH_TIMEOUT(
        win.findChild<QListWidget*>("vfoList")->count() == n, 3000);
}

// Each MainWindow restores the persisted vfo/* set on launch. Tests share one
// redirected QSettings dir, so wipe the VFO group (count + per-channel + names)
// before each VFO test so a fresh window starts from the engine's default 1 VFO.
static void clearVfoSettings() {
    QSettings s("MBDSDR", "MBDSDR");
    s.remove("vfo");
    s.remove("ui/vfoNames");
    s.sync();
}

void TestUiIntegration::vfoCopyDuplicatesSourceParams() {
    clearVfoSettings();
    MainWindow win;
    win.show();
    QApplication::processEvents();

    auto* copyBtn = win.findChild<QPushButton*>("vfoCopyBtn");
    auto* list = win.findChild<QListWidget*>("vfoList");
    QVERIFY(copyBtn);
    QVERIFY(list);
    auto* eng = win.engine();
    QVERIFY(eng);

    // Build a source VFO (id = selected after vfoAdd) with distinctive params.
    eng->vfoAdd();
    waitVfoCount(win, 2);
    const int srcId = eng->selectedVfoId();
    QVERIFY(srcId > 0);
    eng->vfoSetFreq(srcId, 100.7e6);
    eng->vfoSetMode(srcId, "AM");
    eng->vfoSetBandwidth(srcId, 8000.0);
    waitVfoCount(win, 2);

    const int beforeCount = eng->vfoMarkers().size();
    // The source must be the active VFO (vfoCopyUi copies the active one).
    QCOMPARE(eng->selectedVfoId(), srcId);

    copyBtn->click();
    waitVfoCount(win, beforeCount + 1);

    // The new (now-active) VFO must carry an exact copy of source params.
    const int newId = eng->selectedVfoId();
    QVERIFY2(newId != srcId, "copy must create a NEW vfo id");
    auto markers = eng->vfoMarkers();
    bool foundNew = false;
    for (const auto& m : markers) {
        if (m.id == newId) {
            foundNew = true;
            QVERIFY2(std::abs(m.freqHz - 100.7e6) < 1.0,
                     "copied VFO frequency must equal source");
            QCOMPARE(m.mode, QString("AM"));
            QVERIFY2(std::abs(m.bandwidthHz - 8000.0) < 1.0,
                     "copied VFO bandwidth must equal source");
        }
    }
    QVERIFY2(foundNew, "the copied VFO must appear in the engine markers");
}

void TestUiIntegration::vfoDoubleClickSwitchesActive() {
    clearVfoSettings();
    MainWindow win;
    win.show();
    QApplication::processEvents();
    auto* eng = win.engine();
    auto* list = win.findChild<QListWidget*>("vfoList");
    QVERIFY(eng && list);

    // Three VFOs; remember the ids.
    eng->vfoAdd();
    eng->vfoAdd();
    waitVfoCount(win, 3);
    auto markers = eng->vfoMarkers();
    QCOMPARE(markers.size(), 3);
    // Target = the FIRST (oldest) channel; active after adds is the newest.
    const int targetId = markers[0].id;
    QVERIFY(eng->selectedVfoId() != targetId);

    // Find the row item carrying targetId and emit itemDoubleClicked on it.
    QListWidgetItem* targetItem = nullptr;
    for (int i = 0; i < list->count(); ++i) {
        auto* it = list->item(i);
        if (it->data(Qt::UserRole).toInt() == targetId) targetItem = it;
    }
    QVERIFY(targetItem);
    QMetaObject::invokeMethod(list, "itemDoubleClicked", Qt::DirectConnection,
                              Q_ARG(QListWidgetItem*, targetItem));
    QTRY_VERIFY_WITH_TIMEOUT(eng->selectedVfoId() == targetId, 2000);
    QCOMPARE(eng->selectedVfoId(), targetId);
}

void TestUiIntegration::vfoNamingPersistsRoundTrip() {
    clearVfoSettings();
    // Fresh window in the redirected QSettings dir: no names preset.
    {
        MainWindow win;
        win.show();
        QApplication::processEvents();
        auto* eng = win.engine();
        auto* list = win.findChild<QListWidget*>("vfoList");
        QVERIFY(eng && list);
        waitVfoCount(win, 1);
        const int id = eng->selectedVfoId();

        QSettings s("MBDSDR", "MBDSDR");
        QVERIFY2(s.value("ui/vfoNames").toByteArray().isEmpty(),
                 "ui/vfoNames must default to empty (no preset names)");

        // Commit an inline rename for the only VFO via the real itemChanged path.
        // setData(EditRole) fires onVfoItemEdited synchronously (persist + reformat).
        QListWidgetItem* it = list->item(0);
        it->setData(Qt::EditRole, QString::fromUtf8("气象预警"));
        QApplication::processEvents();

        // Persisted JSON must map this id -> the name.
        QSettings s2("MBDSDR", "MBDSDR");
        const QByteArray raw = s2.value("ui/vfoNames").toByteArray();
        QVERIFY2(!raw.isEmpty(), "renaming must persist ui/vfoNames");
        const QJsonObject obj = QJsonDocument::fromJson(raw).object();
        QCOMPARE(obj.value(QString::number(id)).toString(),
                 QString::fromUtf8("气象预警"));

        // The list row now shows the name.
        QVERIFY2(list->item(0)->text().contains(QString::fromUtf8("气象预警")),
                 "row must render the user-assigned name");
    }
    // A SECOND window must load the persisted name back (round-trip).
    {
        MainWindow win;
        win.show();
        QApplication::processEvents();
        auto* list = win.findChild<QListWidget*>("vfoList");
        QVERIFY(list);
        waitVfoCount(win, 1);
        QVERIFY2(list->item(0)->text().contains(QString::fromUtf8("气象预警")),
                 "restored window must show the persisted VFO name");
    }
}

void TestUiIntegration::vfoRemoveRefreshesList() {
    clearVfoSettings();
    MainWindow win;
    win.show();
    QApplication::processEvents();
    auto* eng = win.engine();
    auto* list = win.findChild<QListWidget*>("vfoList");
    auto* delBtn = win.findChild<QPushButton*>("vfoDelBtn");
    QVERIFY(eng && list && delBtn);

    eng->vfoAdd();
    waitVfoCount(win, 2);
    const int activeId = eng->selectedVfoId();
    QVERIFY(activeId > 0);
    QCOMPARE(list->count(), 2);

    delBtn->click();   // removes the active VFO (engine keeps >=1)
    waitVfoCount(win, 1);
    QCOMPARE(eng->vfoMarkers().size(), 1);
    QCOMPARE(list->count(), 1);
    // The removed (active) channel id must no longer be present.
    bool stillThere = false;
    for (const auto& m : eng->vfoMarkers()) if (m.id == activeId) stillThere = true;
    QVERIFY2(!stillThere, "the removed VFO id must be gone after delete");
}

QTEST_MAIN(TestUiIntegration)
#include "test_ui_integration.moc"
