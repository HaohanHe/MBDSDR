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
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QDoubleSpinBox>
#include <QLineEdit>
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
    void scanHitSaveBookmarkThenJump();
    void squelchAutoFollowsSameDomainFloor();
};

QString TestUiIntegration::tmpSettingsDir;

void TestUiIntegration::initTestCase() {
    tmpSettingsDir = QDir::tempPath() + "/mbdsdr_uitest_" +
                     QString::number(QCoreApplication::applicationPid());
    QDir().mkpath(tmpSettingsDir);
    // Redirect all QSettings("MBDSDR","MBDSDR") storage to the throwaway dir so
    // the tests never pollute the real user config / bookmarks.
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope, tmpSettingsDir);
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

QTEST_MAIN(TestUiIntegration)
#include "test_ui_integration.moc"
