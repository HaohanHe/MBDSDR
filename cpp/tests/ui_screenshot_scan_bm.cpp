// SPDX-License-Identifier: MIT
// Offscreen visual self-check: 扫描/书签 panel shown in a Hit state with the
// "存入书签" button enabled and a bookmark row in the table. NOT in ctest;
// built as ui_shot_scanbm and run offscreen. Saves a PNG to
// cpp/scratch/ui_scan_savebookmark.png. The headless scanner is driven
// deterministically (synthetic RSSI feed -- NOT HARDWARE); no fake station is
// invented -- the bookmark frequency is the scanner's real hit frequency.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QPainter>
#include <QColor>
#include <QTabWidget>
#include <QSplitter>
#include <QSettings>
#include <QDir>
#include <QDebug>

#include "core/tokens.h"
#include "ui/main_window.h"
#include "ui/bookmark_manager.h"
#include "dsp/frequency_scanner.h"

using namespace mbdsdr;

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    // Isolate from the real user layout / bookmarks so the shot is deterministic.
    QString tmpDir = QDir::tempPath() + "/mbdsdr_shot_scanbm";
    QDir().mkpath(tmpDir);
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope, tmpDir);
    app.setStyleSheet(tokens::buildDarkQss());

    MainWindow win;
    win.resize(tokens::scaled(1500), tokens::scaled(900));
    win.show();

    QTimer::singleShot(600, [&]() {
        // Widen the right dock so all five bookmark columns are visible.
        if (auto* sp = win.findChild<QSplitter*>())
            sp->setSizes({tokens::scaled(260), tokens::scaled(520),
                          tokens::scaled(660)});
        for (auto* t : win.findChildren<QTabWidget*>()) {
            for (int i = 0; i < t->count(); ++i) {
                if (t->tabText(i) == QString::fromUtf8("扫描/书签")) {
                    t->setCurrentIndex(i);
                    t->currentWidget()->show();
                    break;
                }
            }
        }

        // Drive the headless scanner deterministically to a Hit (synthetic RSSI
        // feed -- NOT HARDWARE). Same state machine the real scan timer drives.
        dsp::ScanConfig cfg;
        cfg.startHz = 88.0e6;
        cfg.stopHz  = 88.2e6;
        cfg.stepHz  = 100e3;
        cfg.dwellMs  = 300;
        cfg.thresholdDb = -80.0f;
        cfg.holdMode = dsp::HitHoldMode::UntilSignalGone;
        win.scanner()->setConfig(cfg);
        win.scanner()->start();
        bool needTune = false;
        for (int i = 0; i < 60; ++i)
            win.scanner()->tick(50, -30.0f, &needTune);   // strong signal -> hit

        // Capture the real hit frequency into a bookmark (mode/bandwidth from the
        // live widgets, exactly as the 存入书签 button would).
        ui::Bookmark bm;
        bm.frequencyHz = win.scanner()->hitFrequency();
        bm.mode = "NFM";
        bm.bandwidthHz = 12500.0;
        bm.name = QString("%1 MHz").arg(bm.frequencyHz / 1e6, 0, 'f', 3);
        win.bookmarkManager()->add(bm);

        // Re-sync button enables (Hit -> save enabled) and the table, then grab.
        win.refreshScanBookmarksUi();
        QApplication::processEvents();

        QTimer::singleShot(300, [&]() {
            const QString out =
                "/home/user/Doubao/chats/38438160041798146/cpp/scratch/ui_scan_savebookmark.png";
            QPixmap pm = win.grab();
            pm.save(out, "PNG");
            qInfo("ui_scan_savebookmark saved %dx%d -> %s", pm.width(), pm.height(),
                  qPrintable(out));
            app.quit();
        });
    });
    return app.exec();
}
