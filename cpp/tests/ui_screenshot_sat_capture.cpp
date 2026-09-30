// SPDX-License-Identifier: MIT
// Offscreen visual self-check: the 天空 (satellite) tab shown in the
// one-tap-capture + Doppler-auto-compensation LOCKED state.  NOT in ctest;
// built as ui_shot_satcap and run offscreen.  Saves a PNG to
// cpp/scratch/ui_sat_capture.png.
//
// The live pass table is empty offscreen (no fresh TLE / no station), so the
// harness drives the real capture-control widgets to the exact restrained
// state the 1 Hz loop produces: 捕获 button enabled, 多普勒自动补偿 checked, and
// the monoInfo status line reading "锁定中·多普勒补偿 ±xxx Hz".  State-set and
// grab run synchronously in one callback so the ~1 Hz live timer cannot reset
// them mid-shot.  This checks the rendering (no overlap / clip / sticker feel)
// -- the decision logic itself is unit-tested in tests/test_sat_capture.cpp.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QTabWidget>
#include <QSplitter>
#include <QCheckBox>
#include <QPushButton>
#include <QLabel>
#include <QSettings>
#include <QDir>
#include <QDebug>

#include "core/tokens.h"
#include "ui/main_window.h"

using namespace mbdsdr;

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QString tmpDir = QDir::tempPath() + "/mbdsdr_shot_satcap";
    QDir().mkpath(tmpDir);
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope, tmpDir);
    app.setStyleSheet(tokens::buildDarkQss());

    MainWindow win;
    win.resize(tokens::scaled(1600), tokens::scaled(900));
    win.show();

    QTimer::singleShot(1200, [&]() {
        // Widen the right rail: find the main 3-section splitter and give the
        // right dock enough room for the capture bar (nested splitters ignored).
        for (auto* sp : win.findChildren<QSplitter*>()) {
            qInfo("splitter: %d children, sizes {%d,%d,%d}", sp->count(),
                  sp->sizes().value(0), sp->sizes().value(1), sp->sizes().value(2));
            if (sp->count() == 3)
                sp->setSizes({tokens::scaled(280), tokens::scaled(640),
                              tokens::scaled(680)});
        }
        // Switch to the 天空 tab.
        QTabWidget* rightTabs = nullptr;
        for (auto* t : win.findChildren<QTabWidget*>()) {
            for (int i = 0; i < t->count(); ++i) {
                if (t->tabText(i) == QString::fromUtf8("天空")) {
                    t->setCurrentIndex(i);
                    t->currentWidget()->show();
                    rightTabs = t;
                    break;
                }
            }
        }

        // Drive the real capture-control widgets to the locked state (blocked so
        // the no-station refusal logic doesn't immediately reset them -- this is
        // a rendering check, the preconditions are unit-tested).
        if (auto* btn = win.findChild<QPushButton*>("capturePassBtn"))
            btn->setEnabled(true);
        if (auto* chk = win.findChild<QCheckBox*>("dopplerCompChk")) {
            const QSignalBlocker block(chk);
            chk->setEnabled(true);
            chk->setChecked(true);
        }
        QLabel* statusLbl = nullptr;
        if (auto* lbl = win.findChild<QLabel*>("captureStatusLabel")) {
            lbl->setText(QString::fromUtf8("锁定中·多普勒补偿 +1234 Hz"));
            statusLbl = lbl;
        }

        // Grab JUST the sky page (the right dock tab), widened via the splitter,
        // so the capture bar is legible regardless of the full-window rail width.
        QPixmap pm;
        if (rightTabs && rightTabs->currentWidget()) {
            rightTabs->currentWidget()->resize(tokens::scaled(680),
                                               tokens::scaled(860));
            pm = rightTabs->currentWidget()->grab();
        } else {
            pm = win.grab();
        }
        const QString out =
            "/home/user/Doubao/chats/38438160041798146/cpp/scratch/ui_sat_capture.png";
        pm.save(out, "PNG");
        qInfo("ui_sat_capture saved %dx%d -> %s", pm.width(), pm.height(),
              qPrintable(out));
        app.quit();
    });
    return app.exec();
}
