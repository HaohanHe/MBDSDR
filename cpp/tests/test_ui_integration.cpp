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
#include "ui/spectrum_widget.h"
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
    // Phase63: grouped bookmark table -- section headers per group, honest empty
    // state, UserRole keeps visual-row -> store-index mapping, retune on the
    // grouped layout, and a group re-assign rebuilds the partitions.
    void bmGroupedViewSectionsEmptyStateAndIndexMapping();
    // Phase63: bookmark group collapse/expand -- clicking a section header hides
    // that group's rows (store untouched, "(N)" stays the real total), clicking
    // again restores them, and adding a bookmark into a collapsed group expands
    // it so the fresh row is honestly visible (other groups keep their state).
    void bmGroupCollapseToggleHidesRowsKeepsCountAndNewRowVisible();
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
    // Phase63: recent-tune list -- honest empty state, persisted-list load, and a
    // picked entry driving the real spinbox tune path.
    void tuneHistoryEmptyStateLoadsAndJumps();
    // Phase63: receive-link four-state badge (Idle/Connecting/Running/Error)
    // driven by the REAL engine source signals; the follow-on fallback
    // sourceChanged(false) must not erase an Error badge.
    void receiveLinkBadgeFourStatesDrivenByRealSignals();
    // Phase63: Rx demod-mode + bandwidth persist through the real saveUiState()
    // (rx/demodMode, rx/bandwidth) and restore on the next launch, with the
    // bandwidth combo snapping to the nearest preset. Drives the real MainWindow
    // offscreen -- no mock.
    void demodBandwidthPersistsAndRestoresRoundTrip();
    // Phase63: a corrupted/illegal persisted mode + absurd bandwidth must snap to
    // honest defaults (NFM + widest preset) instead of presenting a bogus entry.
    void demodBandwidthIllegalPersistedFallsBack();
    // Phase63: spectrum FFT tier (1024/2048/4096) persists through the real
    // saveUiState() ("rx/fftSize") and restores on the next launch, dispatched
    // into the running engine. Drives the real MainWindow offscreen -- no mock.
    void fftSizePersistsAndRestoresRoundTrip();
    // Phase63: an absurd persisted FFT size (8192) must honestly fall back to the
    // 2048 default instead of presenting a bogus combo entry.
    void fftSizeIllegalPersistedFallsBack();
    // Phase63: CTCSS UI face -- the 亚音 checkbox arms the real engine detector
    // (setCtcssEnabled read-back), the 音调 spinbox dispatches the target Hz
    // (setCtcssFreqHz read-back, range == the legal PL domain tokens), and both
    // persist through the real saveUiState() and restore on the next launch. The
    // honest badge never claims 检测到 without a real ctcssPresent().
    void ctcssUiWiresEngineAndPersistsRoundTrip();
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
    // Grouped layout: the visual row no longer equals the store index; resolve
    // the table row that carries this bookmark's store index in UserRole.
    win.scanner()->stop();
    int visualRow = -1;
    for (int v = 0; v < bmTable->rowCount(); ++v) {
        auto* it = bmTable->item(v, 0);
        if (it && it->data(Qt::UserRole).toInt() == row) { visualRow = v; break; }
    }
    QVERIFY2(visualRow >= 0, "the saved bookmark must have a selectable table row");
    QMetaObject::invokeMethod(bmTable, "cellDoubleClicked", Qt::DirectConnection,
                             Q_ARG(int, visualRow), Q_ARG(int, 0));
    QVERIFY2(std::abs(win.engine()->centerFreq() - hitF) < 5000.0,
             "double-clicking the bookmark row must tune the engine to hit freq");
}

// Phase63 grouped bookmark view: data comes only from the real BookmarkManager.
// Empty store -> one honest 暂无书签 row. Seeded store -> one section header row
// per group ("默认 (N)" for the empty group, "组名 (N)" otherwise) plus that
// group's bookmark rows; data rows carry their store index in UserRole so the
// grouped layout can't drift the index-keyed wiring; a group re-assign rebuilds
// the partitions with fresh indices.
void TestUiIntegration::bmGroupedViewSectionsEmptyStateAndIndexMapping() {
    MainWindow win;
    win.show();
    QApplication::processEvents();
    auto* bmTable = win.findChild<QTableWidget*>("bmTable");
    auto* bm = win.bookmarkManager();
    QVERIFY(bmTable && bm);

    // Honest empty state: cleared store -> exactly one non-interactive hint row.
    bm->clear();
    win.refreshScanBookmarksUi();
    QCOMPARE(bm->count(), 0);
    QCOMPARE(bmTable->rowCount(), 1);
    QVERIFY2(bmTable->item(0, 0)->text().contains(QString::fromUtf8("暂无书签")),
             "empty store must show the honest 暂无书签 row");
    QCOMPARE(bmTable->item(0, 0)->data(Qt::UserRole).toInt(), -2);

    // Seed three groups (default "" + AIR + VHF) through the real manager.
    bm->add(ui::Bookmark{"默认台",  98.5e6,   "WFM", 120000.0, ""    });
    bm->add(ui::Bookmark{"航空",    127.6e6,  "AM",  8000.0,   "AIR" });
    bm->add(ui::Bookmark{"Simplex", 144.8e6,  "NFM", 12500.0,  "VHF" });
    bm->add(ui::Bookmark{"中继",    145.05e6, "NFM", 12500.0,  "VHF" });
    win.refreshScanBookmarksUi();

    // Layout: 3 section headers + 4 data rows = 7 visual rows.
    QCOMPARE(bm->count(), 4);
    QCOMPARE(bmTable->rowCount(), 7);
    // Section headers at visual rows 0 (默认), 2 (AIR), 4 (VHF); UserRole -1.
    QVERIFY2(bmTable->item(0, 0)->text().contains(QString::fromUtf8("默认 (1)")),
             qPrintable(QString("default group header must read 默认 (1), got: %1")
                            .arg(bmTable->item(0, 0)->text())));
    QCOMPARE(bmTable->item(2, 0)->text(), QStringLiteral("▾ AIR (1)"));
    QCOMPARE(bmTable->item(4, 0)->text(), QStringLiteral("▾ VHF (2)"));
    QCOMPARE(bmTable->item(0, 0)->data(Qt::UserRole).toInt(), -1);
    QCOMPARE(bmTable->item(2, 0)->data(Qt::UserRole).toInt(), -1);
    QCOMPARE(bmTable->item(4, 0)->data(Qt::UserRole).toInt(), -1);
    // Data rows carry their store index; visual row != store index now.
    // list() order: 默认98.5=0, AIR127.6=1, VHF144.8=2, VHF145.05=3.
    QCOMPARE(bmTable->item(1, 0)->data(Qt::UserRole).toInt(), 0);
    QCOMPARE(bmTable->item(3, 0)->data(Qt::UserRole).toInt(), 1);
    QCOMPARE(bmTable->item(5, 0)->data(Qt::UserRole).toInt(), 2);
    QCOMPARE(bmTable->item(6, 0)->data(Qt::UserRole).toInt(), 3);
    QCOMPARE(bmTable->item(5, 1)->text(), QStringLiteral("144.800"));

    // Grouped-view double-click on visual row 5 (store idx 2) retunes really.
    win.scanner()->stop();
    QMetaObject::invokeMethod(bmTable, "cellDoubleClicked", Qt::DirectConnection,
                             Q_ARG(int, 5), Q_ARG(int, 0));
    QVERIFY2(std::abs(win.engine()->centerFreq() - 144.8e6) < 5000.0,
             "grouped-view double-click must resolve the store index and retune");
    // Double-clicking a section header row (visual 4) is a honest no-op.
    const double beforeHz = win.engine()->centerFreq();
    QMetaObject::invokeMethod(bmTable, "cellDoubleClicked", Qt::DirectConnection,
                             Q_ARG(int, 4), Q_ARG(int, 0));
    QVERIFY2(std::abs(win.engine()->centerFreq() - beforeHz) < 1.0,
             "double-clicking a section header must not retune");

    // Re-assign store index 2 (144.8e6) into the default group -> partitions
    // rebuild: 默认 (2), AIR (1), VHF (1) = still 3 headers + 4 rows.
    ui::Bookmark edited = bm->list().at(2);
    edited.group = "";
    bm->update(2, edited);
    win.refreshScanBookmarksUi();
    QCOMPARE(bmTable->rowCount(), 7);
    QVERIFY2(bmTable->item(0, 0)->text().contains(QString::fromUtf8("默认 (2)")),
             qPrintable(QString("re-group must rebuild 默认 (2), got: %1")
                            .arg(bmTable->item(0, 0)->text())));
    QCOMPARE(bmTable->item(3, 0)->text(), QStringLiteral("▾ AIR (1)"));
    QCOMPARE(bmTable->item(5, 0)->text(), QStringLiteral("▾ VHF (1)"));
    // Fresh list order: 默认98.5=0, 默认144.8=1, AIR127.6=2, VHF145.05=3.
    QCOMPARE(bmTable->item(1, 0)->data(Qt::UserRole).toInt(), 0);
    QCOMPARE(bmTable->item(2, 0)->data(Qt::UserRole).toInt(), 1);
    QCOMPARE(bmTable->item(4, 0)->data(Qt::UserRole).toInt(), 2);
    QCOMPARE(bmTable->item(6, 0)->data(Qt::UserRole).toInt(), 3);
}

// Find the visual row of a group's section header by its rendered "组名 (" text
// prefix (section rows carry UserRole == -1). Returns -1 if not found.
static int findBmHeaderRow(QTableWidget* t, const QString& groupDisp) {
    for (int v = 0; v < t->rowCount(); ++v) {
        auto* it = t->item(v, 0);
        if (it && it->data(Qt::UserRole).toInt() == -1 &&
            it->text().contains(groupDisp))
            return v;
    }
    return -1;
}

void TestUiIntegration::bmGroupCollapseToggleHidesRowsKeepsCountAndNewRowVisible() {
    MainWindow win;
    win.show();
    QApplication::processEvents();
    auto* bmTable = win.findChild<QTableWidget*>("bmTable");
    auto* bm = win.bookmarkManager();
    QVERIFY(bmTable && bm);

    bm->clear();
    win.refreshScanBookmarksUi();
    // Seed three groups: 默认(1) AIR(1) VHF(2) -> 3 headers + 4 data = 7 rows.
    bm->add(ui::Bookmark{"默认台",  98.5e6,   "WFM", 120000.0, ""    });
    bm->add(ui::Bookmark{"航空",    127.6e6,  "AM",  8000.0,   "AIR" });
    bm->add(ui::Bookmark{"Simplex", 144.8e6,  "NFM", 12500.0,  "VHF" });
    bm->add(ui::Bookmark{"中继",    145.05e6, "NFM", 12500.0,  "VHF" });
    win.refreshScanBookmarksUi();
    QCOMPARE(bm->count(), 4);
    QCOMPARE(bmTable->rowCount(), 7);

    // --- Collapse the VHF group by the real cellClicked path on its header. ---
    int vhfHead = findBmHeaderRow(bmTable, QStringLiteral("VHF ("));
    QVERIFY2(vhfHead >= 0, "VHF section header must exist before collapse");
    QMetaObject::invokeMethod(bmTable, "cellClicked", Qt::DirectConnection,
                              Q_ARG(int, vhfHead), Q_ARG(int, 0));
    QApplication::processEvents();

    // 3 headers remain; the 2 VHF data rows are gone -> 5 visual rows.
    QCOMPARE(bmTable->rowCount(), 5);
    // The header flips to the ▸ collapsed glyph but keeps the REAL total (2).
    QVERIFY2(bmTable->item(vhfHead, 0)->text().startsWith(QString::fromUtf8("▸")),
             qPrintable(QString("collapsed VHF header must start with ▸, got: %1")
                            .arg(bmTable->item(vhfHead, 0)->text())));
    QVERIFY2(bmTable->item(vhfHead, 0)->text().endsWith(QStringLiteral("(2)")),
             qPrintable(QString("collapsed count must stay the real (2), got: %1")
                            .arg(bmTable->item(vhfHead, 0)->text())));
    // Store is untouched: collapse hides rows only.
    QCOMPARE(bm->count(), 4);
    // The VHF data rows (store idx 2 = 144.8, idx 3 = 145.05) are no longer in
    // the table; the other two groups' rows are still there.
    bool saw144 = false, saw145 = false;
    for (int v = 0; v < bmTable->rowCount(); ++v) {
        const int idx = bmTable->item(v, 0)->data(Qt::UserRole).toInt();
        if (idx == 2) saw144 = true;
        if (idx == 3) saw145 = true;
    }
    QVERIFY2(!saw144 && !saw145, "collapsed VHF rows must be hidden from the table");
    QCOMPARE(bmTable->item(1, 0)->data(Qt::UserRole).toInt(), 0);  // 默认 row kept
    QCOMPARE(bmTable->item(3, 0)->data(Qt::UserRole).toInt(), 1);  // AIR row kept

    // --- Click the same header again -> expand restores the rows exactly. ---
    QMetaObject::invokeMethod(bmTable, "cellClicked", Qt::DirectConnection,
                              Q_ARG(int, vhfHead), Q_ARG(int, 0));
    QApplication::processEvents();
    QCOMPARE(bmTable->rowCount(), 7);
    QVERIFY2(bmTable->item(vhfHead, 0)->text().startsWith(QString::fromUtf8("▾")),
             qPrintable(QString("expanded VHF header must start with ▾, got: %1")
                            .arg(bmTable->item(vhfHead, 0)->text())));
    QCOMPARE(bmTable->item(5, 0)->data(Qt::UserRole).toInt(), 2);
    QCOMPARE(bmTable->item(6, 0)->data(Qt::UserRole).toInt(), 3);

    // --- Collapse VHF again, then add a bookmark INTO VHF via the real add
    // button: the target group must auto-expand so the fresh row is visible. ---
    QMetaObject::invokeMethod(bmTable, "cellClicked", Qt::DirectConnection,
                              Q_ARG(int, vhfHead), Q_ARG(int, 0));
    QApplication::processEvents();
    QCOMPARE(bmTable->rowCount(), 5);

    // Auto-fill the modal add dialog: name + group=VHF, then accept.
    QTimer::singleShot(300, [&]() {
        auto* dlg = qobject_cast<QDialog*>(QApplication::activeModalWidget());
        if (!dlg) return;
        // The form's own QLineEdits are direct children of the dialog (the
        // QDoubleSpinBoxes embed their own private line edits, which must not be
        // touched). Of the two direct fields, 名称 carries the seeded-MHz
        // placeholder and 分组 is blank -- pick them by that honest signature.
        QLineEdit* nameEdit = nullptr;
        QLineEdit* groupEdit = nullptr;
        for (QLineEdit* le : dlg->findChildren<QLineEdit*>()) {
            if (le->parent() != dlg) continue;   // skip spinbox-embedded editors
            if (!le->placeholderText().isEmpty()) nameEdit = le;
            else                                  groupEdit = le;
        }
        if (nameEdit)  nameEdit->setText(QString::fromUtf8("折叠组新行"));
        if (groupEdit) groupEdit->setText(QStringLiteral("VHF"));
        auto* bb = dlg->findChild<QDialogButtonBox*>();
        if (bb && bb->button(QDialogButtonBox::Ok))
            bb->button(QDialogButtonBox::Ok)->click();
    });
    bmTable->clearSelection();
    win.findChild<QPushButton*>("bmAddBtn")->click();   // blocks until dialog accepted
    QApplication::processEvents();

    QCOMPARE(bm->count(), 5);
    // VHF auto-expanded: header back to ▾ and now reads the real total (3).
    vhfHead = findBmHeaderRow(bmTable, QStringLiteral("VHF ("));
    QVERIFY2(vhfHead >= 0, "VHF header must be present after auto-expand");
    QVERIFY2(bmTable->item(vhfHead, 0)->text().startsWith(QString::fromUtf8("▾")),
             qPrintable(QString("new-row group must expand to ▾, got: %1")
                            .arg(bmTable->item(vhfHead, 0)->text())));
    QVERIFY2(bmTable->item(vhfHead, 0)->text().endsWith(QStringLiteral("(3)")),
             qPrintable(QString("new-row group count must be the real (3), got: %1")
                            .arg(bmTable->item(vhfHead, 0)->text())));
    // The fresh bookmark's row is on screen (find its store index in UserRole).
    int newIdx = -1;
    for (int i = 0; i < bm->list().size(); ++i)
        if (bm->list().at(i).name == QString::fromUtf8("折叠组新行")) newIdx = i;
    QVERIFY2(newIdx >= 0, "the added bookmark must be in the real store");
    bool newRowVisible = false;
    for (int v = 0; v < bmTable->rowCount(); ++v)
        if (bmTable->item(v, 0)->data(Qt::UserRole).toInt() == newIdx) newRowVisible = true;
    QVERIFY2(newRowVisible, "a bookmark added into a collapsed group must render visible");
    // The default + AIR groups were untouched by the VHF auto-expand.
    QVERIFY2(findBmHeaderRow(bmTable, QStringLiteral("默认 (")) >= 0,
             "default group header must survive");
    QVERIFY2(findBmHeaderRow(bmTable, QStringLiteral("AIR (")) >= 0,
             "AIR group header must survive");
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

void TestUiIntegration::tuneHistoryEmptyStateLoadsAndJumps() {
    // (1) Honest empty state: no persisted history -> a single disabled
    //     "无调谐记录" item, never a fabricated seed.
    {
        QSettings s("MBDSDR", "MBDSDR");
        s.remove(tokens::kSettingsKeyTuneHistory);
        s.sync();
    }
    {
        MainWindow win;
        auto* combo = win.findChild<QComboBox*>("tuneHistCombo");
        QVERIFY2(combo, "tuneHistCombo must exist in the 频率 group");
        QCOMPARE(combo->count(), 1);
        QCOMPARE(combo->itemText(0), QStringLiteral("无调谐记录"));
        QVERIFY2(!combo->isEnabled(),
                 "empty recent-tune list must be disabled (nothing to jump to)");
    }

    // (2) Persisted history loads most-recent-first, and picking an entry drives
    //     the real spinbox tune path.
    {
        QSettings s("MBDSDR", "MBDSDR");
        s.setValue(tokens::kSettingsKeyTuneHistory,
                   QVariantList{100.0e6, 98.5e6});
        s.sync();
    }
    MainWindow win;
    auto* combo = win.findChild<QComboBox*>("tuneHistCombo");
    QVERIFY(combo);
    QCOMPARE(combo->count(), 2);
    QCOMPARE(combo->itemText(0), QString("100 MHz"));
    QCOMPARE(combo->itemText(1), QString("98.5 MHz"));
    QVERIFY(combo->isEnabled());

    auto* spin = win.findChild<QDoubleSpinBox*>("freqSpin");
    QVERIFY(spin);
    // Restored centre defaults to 98.5 MHz (no rx/centerFreq persisted here).
    // Picking the first history entry must retune to 100.0 MHz.
    Q_EMIT combo->activated(0);
    QCOMPARE(spin->value(), 100.0);
}

// Phase63: the receive-link four-state badge is the single explicit Idle /
// Connecting / Running / Error indicator next to the connect button. It is
// driven ONLY by the real engine source signals (the harness forwards the SAME
// private slots the queued engine signals reach). Error shows the real reason
// verbatim, and the engine's fallback sourceChanged(false) after a failed
// connect must not silently erase it.
void TestUiIntegration::receiveLinkBadgeFourStatesDrivenByRealSignals() {
    MainWindow win;
    win.show();
    QApplication::processEvents();
    QLabel* badge = win.harnessConnStateBadge();
    QVERIFY2(badge, "the four-state receive-link badge must exist");

    // Boot: no real hardware -> honest idle empty state.
    QVERIFY2(badge->text().contains(QStringLiteral("空闲")),
             qPrintable(QString("boot badge should read idle, got: %1").arg(badge->text())));

    // Real hardware connects -> Running carries the real source name.
    win.harnessSourceChanged(QStringLiteral("RTL-SDR"), true);
    QCOMPARE(badge->text(), QStringLiteral("已连接 · RTL-SDR"));

    // A connect attempt fails with a REAL reason -> Error shows it verbatim.
    win.harnessSourceError(QStringLiteral("连接被拒绝 (refused)"));
    QVERIFY2(badge->text().contains(QStringLiteral("错误")) &&
             badge->text().contains(QStringLiteral("连接被拒绝")),
             qPrintable(QString("error badge must carry the real reason, got: %1")
                            .arg(badge->text())));

    // The engine follows every failed connect with a fallback sourceChanged(false);
    // it must NOT reset the Error badge to Idle (honest retention).
    win.harnessSourceChanged(QStringLiteral("No Source"), false);
    QVERIFY2(badge->text().contains(QStringLiteral("错误")),
             qPrintable(QString("fallback sourceChanged(false) must keep Error, got: %1")
                            .arg(badge->text())));

    // A successful reconnect clears the flags and shows Running again.
    win.harnessSourceChanged(QStringLiteral("rtl_tcp 127.0.0.1:1234"), true);
    QCOMPARE(badge->text(), QStringLiteral("已连接 · rtl_tcp 127.0.0.1:1234"));

    // Manual disconnect -> honest idle empty state.
    win.harnessSourceChanged(QStringLiteral("No Source"), false);
    QVERIFY2(badge->text().contains(QStringLiteral("空闲")),
             qPrintable(QString("disconnect should return to idle, got: %1").arg(badge->text())));

    // The opt-in synthetic test source does NOT claim Running (no real link);
    // its provenance pill covers data origin, so the link badge stays idle.
    win.harnessSourceChanged(QStringLiteral("Test Signal"), false);
    QVERIFY2(badge->text().contains(QStringLiteral("空闲")),
             qPrintable(QString("synthetic source must keep the link badge idle, got: %1")
                            .arg(badge->text())));
}

// Phase63: the Rx demod-mode combo and bandwidth combo persist through the real
// saveUiState() (keys rx/demodMode + rx/bandwidth, written on the debounced save
// timer) and restore on the next launch. The bandwidth is stored as the live Hz
// value and snapped to the nearest preset for the combo. Two real windows share
// the redirected QSettings dir; no mock, no hand-seeded keys -- the first window
// drives the actual combo handlers and the second reads them back.
void TestUiIntegration::demodBandwidthPersistsAndRestoresRoundTrip() {
    clearVfoSettings();
    const QString targetMode = QStringLiteral("USB");
    const int bwPresetIdx = 2;          // bwCombo_ preset: 9000 Hz
    const double targetBwHz = 9000.0;

    // Window 1: switch to USB, then move the bandwidth combo off the mode default
    // to the 9 kHz preset. The real currentIndexChanged handlers run end-to-end.
    {
        MainWindow win;
        win.show();
        QApplication::processEvents();
        auto* eng = win.engine();
        auto* demod = win.findChild<QComboBox*>("demodCombo");
        auto* bw = win.findChild<QComboBox*>("bwCombo");
        QVERIFY(eng && demod && bw);

        demod->setCurrentText(targetMode);
        QApplication::processEvents();
        bw->setCurrentIndex(bwPresetIdx);
        QApplication::processEvents();

        // The synthetic source keeps the engine thread live, so setDemodMode /
        // setBandwidth drain asynchronously on that thread -- wait for readback.
        QTRY_VERIFY_WITH_TIMEOUT(eng->demodMode() == targetMode, 2000);
        QTRY_VERIFY_WITH_TIMEOUT(eng->bandwidth() == targetBwHz, 2000);

        // Let the 500 ms debounced save timer arm; then close the window, whose
        // destructor flushes saveUiState() to disk (same path the app uses on
        // exit). We read the keys AFTER destruction to avoid fighting Qt's
        // in-process QSettings cache.
        QTest::qWait(700);
    }

    // Fresh QSettings on disk (window 1 already destroyed + flushed).
    {
        QSettings s("MBDSDR", "MBDSDR");
        s.sync();
        QCOMPARE(s.value("rx/demodMode").toString(), targetMode);
        QCOMPARE(s.value("rx/bandwidth").toDouble(), targetBwHz);
    }

    // Window 2: a fresh launch must restore the mode text, snap the bandwidth
    // combo to the 9 kHz preset, and dispatch both into the running engine.
    {
        MainWindow win;
        win.show();
        QApplication::processEvents();
        auto* eng = win.engine();
        auto* demod = win.findChild<QComboBox*>("demodCombo");
        auto* bw = win.findChild<QComboBox*>("bwCombo");
        QVERIFY(eng && demod && bw);

        QCOMPARE(demod->currentText(), targetMode);
        QCOMPARE(bw->currentIndex(), bwPresetIdx);
        QTRY_VERIFY_WITH_TIMEOUT(eng->demodMode() == targetMode, 2000);
        QTRY_VERIFY_WITH_TIMEOUT(eng->bandwidth() == targetBwHz, 2000);
    }
}

// Phase63: a corrupted settings file (illegal mode text + an absurd bandwidth
// far outside the [1k, 2M] presets) must honestly fall back instead of presenting
// a bogus combo entry. The illegal mode misses findText -> NFM (combo index 1);
// the absurd bandwidth snaps to the widest preset (2 MHz, index 6).
void TestUiIntegration::demodBandwidthIllegalPersistedFallsBack() {
    clearVfoSettings();
    {
        QSettings s("MBDSDR", "MBDSDR");
        s.setValue("rx/demodMode", "NOT_A_REAL_MODE");
        s.setValue("rx/bandwidth", 5.0e9);   // far outside every preset
        s.sync();
    }
    MainWindow win;
    win.show();
    QApplication::processEvents();
    auto* demod = win.findChild<QComboBox*>("demodCombo");
    auto* bw = win.findChild<QComboBox*>("bwCombo");
    QVERIFY(demod && bw);

    QCOMPARE(demod->currentIndex(), 1);
    QCOMPARE(demod->currentText(), QStringLiteral("NFM"));
    // The absurd bandwidth cannot survive: with no persisted VFO row the default
    // 12.5 kHz is authoritative and the combo snaps to its preset (index 4). The
    // point is an honest, valid default -- never a bogus combo entry.
    QTRY_VERIFY_WITH_TIMEOUT(bw->currentIndex() == 4, 2000);
}

// Phase63: FFT tier (1024/2048/4096) round-trips through the real saveUiState()
// path. Window 1 picks 1024; the debounced save flush + window destructor write
// "rx/fftSize"; window 2 (fresh launch) must restore the combo AND dispatch the
// tier into the running engine (read back fftSize()). No mock -- the offline
// TestSignalSource keeps the engine thread live.
void TestUiIntegration::fftSizePersistsAndRestoresRoundTrip() {
    clearVfoSettings();
    const int targetFft = 1024;

    // Window 1: default is 2048; switch the spectrum combo to the 1024 tier.
    {
        MainWindow win;
        win.show();
        QApplication::processEvents();
        auto* eng = win.engine();
        auto* spec = win.findChild<ui::SpectrumWidget*>();
        QVERIFY(eng && spec);

        QCOMPARE(spec->fftSizeValue(), 2048);
        spec->setFftSizeValue(targetFft);
        QApplication::processEvents();

        // The combo's currentIndexChanged -> fftSizeRequested -> engine setFftSize
        // (atomic store; queued delivery only if the engine lives on its thread).
        QTRY_VERIFY_WITH_TIMEOUT(eng->fftSize() == targetFft, 2000);

        QTest::qWait(700);   // let the debounced save timer arm; dtor flushes it
    }

    // Fresh QSettings on disk (window 1 destroyed + flushed).
    {
        QSettings s("MBDSDR", "MBDSDR");
        s.sync();
        QCOMPARE(s.value("rx/fftSize").toInt(), targetFft);
    }

    // Window 2: a fresh launch must restore the combo tier and dispatch it into
    // the running engine.
    {
        MainWindow win;
        win.show();
        QApplication::processEvents();
        auto* eng = win.engine();
        auto* spec = win.findChild<ui::SpectrumWidget*>();
        QVERIFY(eng && spec);

        QCOMPARE(spec->fftSizeValue(), targetFft);
        QTRY_VERIFY_WITH_TIMEOUT(eng->fftSize() == targetFft, 2000);
    }
}

// Phase63: an absurd persisted FFT size (8192, no such tier) must honestly fall
// back to the 2048 default in BOTH the spectrum combo and the engine -- never a
// bogus combo entry, never a half-applied tier.
void TestUiIntegration::fftSizeIllegalPersistedFallsBack() {
    clearVfoSettings();
    {
        QSettings s("MBDSDR", "MBDSDR");
        s.setValue("rx/fftSize", 8192);   // outside {1024,2048,4096}
        s.sync();
    }
    MainWindow win;
    win.show();
    QApplication::processEvents();
    auto* eng = win.engine();
    auto* spec = win.findChild<ui::SpectrumWidget*>();
    QVERIFY(eng && spec);

    // setFftSizeValue(8192): no tier matches -> combo stays index 1 (2048).
    QCOMPARE(spec->fftSizeValue(), 2048);
    QTRY_VERIFY_WITH_TIMEOUT(eng->fftSize() == 2048, 2000);
}

// Phase63: CTCSS UI face. Drives the real MainWindow offscreen (offline test
// source) and asserts the 亚音 checkbox + 音调 spinbox wire into the REAL
// SpectrumEngine setters (read-back), the spinbox range IS the legal PL domain,
// the armed-but-no-tone badge honestly reads 未检测到 (never a fabricated 检测到),
// and the preference persists through saveUiState() + restore on the next launch.
void TestUiIntegration::ctcssUiWiresEngineAndPersistsRoundTrip() {
    clearVfoSettings();
    const double targetHz = 100.0;

    // Window 1: wire the controls into the engine, then persist.
    {
        MainWindow win;
        win.show();
        QApplication::processEvents();
        auto* eng = win.engine();
        auto* chk = win.findChild<QCheckBox*>("ctcssCheck");
        auto* spin = win.findChild<QDoubleSpinBox*>("ctcssFreqSpin");
        auto* badge = win.findChild<QLabel*>("ctcssBadge");
        auto* gate = win.findChild<QCheckBox*>("ctcssGateCheck");
        QVERIFY(eng && chk && spin && badge && gate);

        // Spinbox range IS the legal PL domain (tokens) -- no out-of-domain input.
        QCOMPARE(spin->minimum(), tokens::kCtcssToneHzMin);
        QCOMPARE(spin->maximum(), tokens::kCtcssToneHzMax);
        QCOMPARE(spin->singleStep(), 0.1);

        // Default: off, engine detector disabled, speaker gate off.
        QVERIFY2(!chk->isChecked(), "CTCSS must default to off");
        QVERIFY2(!gate->isChecked(), "speaker gate must default to off");
        QTRY_VERIFY_WITH_TIMEOUT(!eng->ctcssEnabled(), 2000);
        QTRY_VERIFY_WITH_TIMEOUT(!eng->ctcssGateAudio(), 2000);

        // Arming the checkbox must dispatch setCtcssEnabled(true) into the engine.
        chk->setChecked(true);
        QApplication::processEvents();
        QTRY_VERIFY_WITH_TIMEOUT(eng->ctcssEnabled(), 2000);

        // Changing the tone must dispatch setCtcssFreqHz and read back equal.
        spin->setValue(targetHz);
        QApplication::processEvents();
        QTRY_VERIFY_WITH_TIMEOUT(std::abs(eng->ctcssFreqHz() - targetHz) < 0.05, 2000);

        // Honest badge: armed but no real tone on the offline source -> 未检测到,
        // never a fabricated 检测到. The 250 ms poll must paint it.
        QTest::qWait(350);
        QVERIFY2(!eng->ctcssPresent(), "offline source must not present a tone");
        QCOMPARE(badge->text(), QStringLiteral("未检测到"));

        // Arming the speaker gate must dispatch setCtcssGateAudio(true), and with
        // no matching tone the badge honestly flips to the muted state 静音.
        gate->setChecked(true);
        QApplication::processEvents();
        QTRY_VERIFY_WITH_TIMEOUT(eng->ctcssGateAudio(), 2000);
        QTest::qWait(350);
        QCOMPARE(badge->text(), QStringLiteral("静音"));

        QTest::qWait(700);   // debounced save timer arms; dtor flushes it
    }

    // Fresh QSettings on disk (window 1 destroyed + flushed).
    {
        QSettings s("MBDSDR", "MBDSDR");
        s.sync();
        QCOMPARE(s.value(tokens::kSettingsKeyCtcssEnabled).toBool(), true);
        QCOMPARE(s.value(tokens::kSettingsKeyCtcssToneHz).toDouble(), targetHz);
        QCOMPARE(s.value(tokens::kSettingsKeyCtcssGate).toBool(), true);
    }

    // Window 2: a fresh launch must restore the arming + tone + gate and dispatch
    // them into the running engine.
    {
        MainWindow win;
        win.show();
        QApplication::processEvents();
        auto* eng = win.engine();
        auto* chk = win.findChild<QCheckBox*>("ctcssCheck");
        auto* spin = win.findChild<QDoubleSpinBox*>("ctcssFreqSpin");
        auto* gate = win.findChild<QCheckBox*>("ctcssGateCheck");
        QVERIFY(eng && chk && spin && gate);

        QCOMPARE(chk->isChecked(), true);
        QCOMPARE(spin->value(), targetHz);
        QCOMPARE(gate->isChecked(), true);
        QTRY_VERIFY_WITH_TIMEOUT(eng->ctcssEnabled(), 2000);
        QTRY_VERIFY_WITH_TIMEOUT(std::abs(eng->ctcssFreqHz() - targetHz) < 0.05, 2000);
        QTRY_VERIFY_WITH_TIMEOUT(eng->ctcssGateAudio(), 2000);
    }
}

QTEST_MAIN(TestUiIntegration)
#include "test_ui_integration.moc"