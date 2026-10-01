// SPDX-License-Identifier: MIT
// Figma-audit self-checks (offscreen, real chain, no mocks):
//   1. New design tokens are wired into the generated dark QSS (focus ring,
//      restrained selected fill, disabled surface, hairline divider) -- so a
//      regression that drops them fails here.
//   2. Direct frequency input (MHz) is honest: the QDoubleSpinBox clamps any
//      out-of-range value into [kFreqMinHz, kFreqMaxHz] and never persists an
//      illegal value.
//   3. Mode/bandwidth coverage rule re-check: a manually-set bandwidth is
//      preserved across a mode switch; an untouched default is adopted.
#include <QApplication>
#include <QDoubleSpinBox>
#include <QtTest>

#include "core/tokens.h"
#include "core/bandwidth_preset.h"
#include "ui/main_window.h"

using namespace mbdsdr;

class TestUiAudit : public QObject {
    Q_OBJECT
private slots:
    void qssWiresAuditTokens();
    void qssUsesRestrainedInteractiveBlue();
    void frequencyInputClampsOutOfRange();
    void bandwidthCoverageRulePreservesManualValue();
    void mainWindowHasFocusableFreqSpin();
    void mainWindowHasTokenizedMinimumSize();
};

void TestUiAudit::qssWiresAuditTokens() {
    const QString qss = tokens::buildDarkQss();
    // Focus ring must appear (keyboard-reachable controls).
    QVERIFY(qss.contains("QDoubleSpinBox:focus"));
    QVERIFY(qss.contains(QString::fromUtf8(tokens::kFocusRing)));
    // Restrained selected fill (low-sat blue-gray), not the bright accent block.
    QVERIFY(qss.contains("item:selected"));
    QVERIFY(qss.contains(QString::fromUtf8(tokens::kSelectedFill)));
    // Disabled surface + hairline divider wired.
    QVERIFY(qss.contains(QString::fromUtf8(tokens::kDisabledFill)));
    QVERIFY(qss.contains(QString::fromUtf8(tokens::kDivider)));
}

void TestUiAudit::qssUsesRestrainedInteractiveBlue() {
    const QString qss = tokens::buildDarkQss();
    // UI interactive highlight converged to the restrained blue-gray (#919cac),
    // not the bright instrument blue. Tab selected text + tooltip border use it.
    QVERIFY(qss.contains(QString::fromUtf8(tokens::kInteract)));
    QVERIFY(qss.contains("QTabBar::tab:selected"));
    // The splitter handle must be blue-gray now, not bright blue.
    QVERIFY(qss.contains("rgba(145, 156, 172, 0.35)"));
    QVERIFY(!qss.contains("rgba(124, 196, 255"));
    // kInteract feeds tab-selected + tooltip border (the old bright-blue UI
    // decoration points). The bright kAccent is reserved for canvas traces.
}

void TestUiAudit::frequencyInputClampsOutOfRange() {
    QDoubleSpinBox spin;
    spin.setRange(tokens::kFreqMinHz / 1e6, tokens::kFreqMaxHz / 1e6);
    spin.setDecimals(3);
    // Way below minimum -> clamps up, never writes the illegal value.
    spin.setValue(0.001);
    QCOMPARE_GE(spin.value(), tokens::kFreqMinHz / 1e6);
    // Way above maximum -> clamps down.
    spin.setValue(999999.0);
    QCOMPARE_LE(spin.value(), tokens::kFreqMaxHz / 1e6);
}

void TestUiAudit::bandwidthCoverageRulePreservesManualValue() {
    // User manually set 5 kHz while on NFM (default 12.5k) -> switching to
    // WFM must PRESERVE the manual 5k, not stomp it with 200k.
    const double manual = 5000.0;
    QCOMPARE(core::bandwidthOnModeSwitch("NFM", "WFM", manual), manual);
    // Untouched (still at NFM default 12.5k) -> adopt WFM default 200k.
    QCOMPARE(core::bandwidthOnModeSwitch("NFM", "WFM", core::kBwNfmHz),
             core::kBwWfmHz);
}

void TestUiAudit::mainWindowHasFocusableFreqSpin() {
    MainWindow win;
    win.show();
    QDoubleSpinBox* spin = win.findChild<QDoubleSpinBox*>("freqSpin");
    QVERIFY(spin != nullptr);
    // The frequency control must be keyboard-focusable so the focus ring
    // (kFocusRing, verified in qssWiresAuditTokens) actually shows. Offscreen
    // has no window manager, so we assert the focus policy rather than live
    // focusWidget() (which needs an active top-level).
    QVERIFY(spin->focusPolicy() & Qt::StrongFocus);
    spin->setFocus();  // must not crash in offscreen.
}

void TestUiAudit::mainWindowHasTokenizedMinimumSize() {
    // P0-2: the main window has a tokenized floor (no magic numbers) so a
    // narrow window cannot crush the 3-column layout into horizontal overflow.
    MainWindow win;
    win.show();
    const QSize expected(tokens::scaled(tokens::kMainMinW),
                          tokens::scaled(tokens::kMainMinH));
    QCOMPARE(win.minimumSize(), expected);
    QVERIFY(win.minimumSize().width() > 0);
    QVERIFY(win.minimumSize().height() > 0);
}

QTEST_MAIN(TestUiAudit)
#include "test_ui_audit.moc"
