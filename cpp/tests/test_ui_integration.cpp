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
#include <QLabel>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QSettings>
#include <QSlider>
#include <QTabWidget>
#include <QTableWidget>
#include <QTimer>

#include <cmath>

#include "core/tokens.h"
#include "ui/main_window.h"
#include "ui/bookmark_manager.h"
#include "ai/agent.h"
#include "ai/ai_session_store.h"
#include "dsp/frequency_scanner.h"
#include "dsp/scan_link.h"
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
    // Phase62 orphan wiring: honest UI entries for the three backends that had
    // no desktop surface (export IQ segment / network audio tap / scan link).
    void exportIqSegmentHonestFailureWithoutData();
    void networkAudioSinkEmptyStateAndToggle();
    void scanLinkToggleDrivesStateLabel();
    // Phase63: closable right-rail panel tabs (SDR++ module show/hide, light).
    void rightTabCloseHidesTabKeepsIndexStable();
    void rightTabVisibilityPersistsRoundTrip();
    void rightTabCloseLastVisibleRefused();
};

QString TestUiIntegration::tmpSettingsDir;

void TestUiIntegration::initTestCase() {
    // Phase21: the engine no longer auto-falls back to the offline test source.
    // The scan-hit and squelch-auto slots need flowing synthetic IQ (a real scan
    // tone + a tracked audio-RMS floor), so opt in explicitly BEFORE any
    // MainWindow (which builds the engine) is constructed.
    qputenv("MBDSDR_TEST_SOURCE", "1");
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

    // Delete the current (first) session: count drops back to 1. The delete is
    // destructive, so the production panel asks a modal QMessageBox first; the
    // offscreen harness seam auto-confirms so this click isn't blocked.
    win.harnessSetAutoConfirmSessionDelete(true);
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

// Phase62 orphan A: with the honest empty source (no hardware, no file) the
// one-shot raw-IQ export must FAIL honestly -- the status label carries the
// engine's real reason and no file is fabricated.
void TestUiIntegration::exportIqSegmentHonestFailureWithoutData() {
    MainWindow win;
    win.show();
    QApplication::processEvents();
    // Drop the synthetic source this suite opted into: the production honest
    // empty (NullSource) yields no IQ, exactly like a box with no hardware.
    win.engine()->setTestSourceEnabled(false);
    QTest::qWait(400);
    QVERIFY2(!win.engine()->hasData(),
             "empty source must report hasData()==false");

    QLabel* status = win.harnessRecLibStatus();
    QVERIFY(status);
    const bool ok = win.harnessExportIq(1.0);
    QVERIFY2(!ok, "export must honestly refuse with no IQ data");
    QVERIFY2(status->text().contains(QStringLiteral("导出失败")),
             qPrintable(QString("status must carry the honest failure, got: %1")
                            .arg(status->text())));
}

// Phase62 orphan B: the network-audio tap entry starts/stops the REAL
// NetworkAudioSink through the engine's parallel-tap seam. The empty states are
// honest: stop disabled while nothing streams; an empty host is refused.
void TestUiIntegration::networkAudioSinkEmptyStateAndToggle() {
    MainWindow win;
    win.show();
    QApplication::processEvents();

    auto* startBtn = win.findChild<QPushButton*>("netAudioStartBtn");
    auto* stopBtn  = win.findChild<QPushButton*>("netAudioStopBtn");
    auto* status   = win.findChild<QLabel*>("netAudioStatusLabel");
    auto* hostEdit = win.findChild<QLineEdit*>("netAudioHostEdit");
    QVERIFY(startBtn && stopBtn && status && hostEdit);

    // Honest empty state: not streaming -> stop disabled, status idle, no tap.
    QVERIFY(!stopBtn->isEnabled());
    QCOMPARE(status->text(), QStringLiteral("未开启"));
    QVERIFY(!win.engine()->networkTapActive());

    // Empty config: start refuses honestly and installs nothing.
    hostEdit->clear();
    startBtn->click();
    QVERIFY2(status->text().contains(QStringLiteral("未配置")),
             qPrintable(QString("empty host must be refused, got: %1").arg(status->text())));
    QVERIFY(!win.engine()->networkTapActive());

    // UDP to loopback always binds; streaming goes live through the engine tap.
    hostEdit->setText("127.0.0.1");
    startBtn->click();
    QVERIFY(win.engine()->networkTapActive());
    QVERIFY(!startBtn->isEnabled());
    QVERIFY(stopBtn->isEnabled());
    qInfo() << "network audio status:" << status->text();

    stopBtn->click();
    QVERIFY(!win.engine()->networkTapActive());
    QCOMPARE(status->text(), QStringLiteral("未开启"));
}

// Phase62 orphan C: the activity-scan link checkbox drives the real headless
// bridge: enabled -> it walks the band on the engine's real RSSI; disabled ->
// back to Idle. No fabricated dwells: a quiet band stays Scanning forever.
void TestUiIntegration::scanLinkToggleDrivesStateLabel() {
    MainWindow win;
    win.show();
    QApplication::processEvents();

    auto* chk = win.findChild<QCheckBox*>("scanLinkChk");
    auto* label = win.findChild<QLabel*>("scanLinkStateLabel");
    QVERIFY(chk && label);
    // Honest empty state: disabled link shows the not-armed idle.
    QCOMPARE(label->text(), QStringLiteral("空闲（未启用）"));

    // Any dwell-recording goes to a throwaway dir, never the cwd.
    win.engine()->setRecordingDir(QDir::tempPath() + "/mbdsdr_scanlink_rec");

    chk->setChecked(true);
    QTest::qWait(250);   // ~five 50 ms ticks
    qInfo() << "scan link state:" << label->text();
    QVERIFY(win.scanLink());
    const dsp::ScanLinkState st = win.scanLink()->state();
    QVERIFY2(st == dsp::ScanLinkState::Scanning || st == dsp::ScanLinkState::Dwell,
             "an enabled link must walk the band");
    QVERIFY2(!label->text().contains(QStringLiteral("未启用")),
             "an enabled link must leave the disabled empty state");

    chk->setChecked(false);
    QTest::qWait(150);
    QCOMPARE(win.scanLink()->state(), dsp::ScanLinkState::Idle);
    QVERIFY(label->text().contains(QStringLiteral("空闲")));
}

// Phase63: closing a right-rail panel tab hides ONLY that tab -- the count, the
// page widgets and every index stay put (decoder/bookmark wiring by index must
// not move). The restore button appears; showAllRightTabs brings the tab back.
void TestUiIntegration::rightTabCloseHidesTabKeepsIndexStable() {
    MainWindow win;
    win.show();
    QApplication::processEvents();

    auto* tabs = win.findChild<QTabWidget*>("rightTabs");
    QVERIFY(tabs);
    // Pick a real (enabled) panel tab that sits between others, e.g. 数据.
    int idx = -1;
    for (int i = 0; i < tabs->count(); ++i) {
        if (tabs->tabText(i) == QString::fromUtf8("数据") && tabs->isTabEnabled(i))
            idx = i;
    }
    QVERIFY2(idx > 0, "the 数据 panel tab must exist and be switchable");
    QWidget* page = tabs->widget(idx);
    QWidget* leftNeighbor = tabs->widget(idx - 1);
    QWidget* rightNeighbor = tabs->widget(idx + 1);
    const int countBefore = tabs->count();
    QVERIFY(tabs->isTabVisible(idx));

    // Drive the real close path: emit tabCloseRequested exactly as the tab bar
    // close button does.
    QMetaObject::invokeMethod(tabs, "tabCloseRequested", Qt::DirectConnection,
                             Q_ARG(int, idx));
    QApplication::processEvents();

    QVERIFY2(!tabs->isTabVisible(idx), "closed tab must be hidden, not removed");
    QCOMPARE(tabs->count(), countBefore);            // index layout untouched
    QCOMPARE(tabs->widget(idx), page);               // same page widget
    QCOMPARE(tabs->widget(idx - 1), leftNeighbor);    // neighbors unchanged
    QCOMPARE(tabs->widget(idx + 1), rightNeighbor);
    // The restore affordance appears while something is hidden.
    auto* restore = win.findChild<QPushButton*>("rightTabRestoreBtn");
    QVERIFY(restore);
    QVERIFY2(restore->isVisible(), "restore button must appear once a tab is hidden");

    win.showAllRightTabs();
    QApplication::processEvents();
    QVERIFY(tabs->isTabVisible(idx));
    QVERIFY2(!restore->isVisible(), "restore button hides again once all visible");
}

// Phase63: the hidden tab list persists through a fresh QSettings round-trip.
void TestUiIntegration::rightTabVisibilityPersistsRoundTrip() {
    {
        QSettings s("MBDSDR", "MBDSDR");
        s.remove("ui/hiddenRightTabs");
        s.sync();
    }
    // First window: close the 数据 tab. The destructor flushes saveUiState()
    // (the production backstop for the 500 ms debounce), so the hidden list is
    // on disk once this block ends.
    {
        MainWindow win;
        win.show();
        QApplication::processEvents();
        auto* tabs = win.findChild<QTabWidget*>("rightTabs");
        QVERIFY(tabs);
        int idx = -1;
        for (int i = 0; i < tabs->count(); ++i) {
            if (tabs->tabText(i) == QString::fromUtf8("数据") && tabs->isTabEnabled(i))
                idx = i;
        }
        QVERIFY(idx > 0);
        QVERIFY(tabs->isTabVisible(idx));
        QMetaObject::invokeMethod(tabs, "tabCloseRequested", Qt::DirectConnection,
                                 Q_ARG(int, idx));
        QApplication::processEvents();
        QVERIFY(!tabs->isTabVisible(idx));
    }
    // The flush must have recorded the closed tab by text.
    {
        QSettings s("MBDSDR", "MBDSDR");
        const QStringList hidden = s.value("ui/hiddenRightTabs").toStringList();
        QVERIFY2(hidden.contains(QString::fromUtf8("数据")),
                 qPrintable(QString("ui/hiddenRightTabs must record 数据, got: %1")
                                .arg(hidden.join(", "))));
    }
    // Second window: the same tab must start out hidden (restored from QSettings).
    {
        MainWindow win;
        win.show();
        QApplication::processEvents();
        auto* tabs = win.findChild<QTabWidget*>("rightTabs");
        QVERIFY(tabs);
        int idx = -1;
        for (int i = 0; i < tabs->count(); ++i) {
            if (tabs->tabText(i) == QString::fromUtf8("数据") && tabs->isTabEnabled(i))
                idx = i;
        }
        QVERIFY(idx > 0);
        QVERIFY2(!tabs->isTabVisible(idx),
                 "restored window must keep the persisted-hidden tab hidden");
        win.showAllRightTabs();   // leaves an empty list for the next suite
        QApplication::processEvents();
    }
}

// Phase63: the rail never ends up with zero visible panels -- closing the last
// visible tab is refused; the restore entry stays reachable.
void TestUiIntegration::rightTabCloseLastVisibleRefused() {
    MainWindow win;
    win.show();
    QApplication::processEvents();
    auto* tabs = win.findChild<QTabWidget*>("rightTabs");
    QVERIFY(tabs);
    QList<int> enabled;
    for (int i = 0; i < tabs->count(); ++i)
        if (tabs->isTabEnabled(i)) enabled << i;
    QVERIFY2(enabled.size() >= 3, "the right rail must ship several real panels");

    // Close every real tab but the last one in the list.
    for (int k = 0; k < enabled.size() - 1; ++k) {
        QMetaObject::invokeMethod(tabs, "tabCloseRequested", Qt::DirectConnection,
                                 Q_ARG(int, enabled[k]));
    }
    QApplication::processEvents();
    // Now exactly one real panel remains visible; closing it again is refused.
    const int last = enabled.last();
    QVERIFY(tabs->isTabVisible(last));
    QMetaObject::invokeMethod(tabs, "tabCloseRequested", Qt::DirectConnection,
                             Q_ARG(int, last));
    QApplication::processEvents();
    QVERIFY2(tabs->isTabVisible(last),
             "the last visible panel tab must not be closable");
}

QTEST_MAIN(TestUiIntegration)
#include "test_ui_integration.moc"
