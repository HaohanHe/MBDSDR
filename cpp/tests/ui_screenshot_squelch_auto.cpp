// SPDX-License-Identifier: MIT
// Offscreen visual self-check: the 静噪 group with the "自动门限" button armed.
// NOT part of ctest; built as ui_shot_sqlauto and run offscreen. Saves a PNG to
// cpp/scratch/ui_squelch_auto.png. The engine runs on the offline test source
// (NOT HARDWARE); the auto threshold follows the real tracked audio-RMS floor.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QPushButton>
#include <QSlider>
#include <QLabel>
#include <QCheckBox>
#include <QScrollArea>
#include <QSplitter>
#include <QSettings>
#include <QDir>
#include <QDebug>

#include "core/tokens.h"
#include "ui/main_window.h"
#include "dsp/spectrum_engine.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    // Isolate from the real user layout (which may have focus mode / rails
    // collapsed) so the shot shows a clean default instrument layout.
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope,
                       QDir::tempPath() + "/mbdsdr_shot_sqlauto");
    QDir().mkpath(QDir::tempPath() + "/mbdsdr_shot_sqlauto");
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    mbdsdr::MainWindow win;
    win.resize(mbdsdr::tokens::scaled(1500), mbdsdr::tokens::scaled(900));
    win.show();

    QTimer::singleShot(900, [&]() {
        // Widen the left rail so the 静噪 group is comfortably readable.
        if (auto* sp = win.findChild<QSplitter*>())
            sp->setSizes({mbdsdr::tokens::scaled(420),
                          mbdsdr::tokens::scaled(820),
                          mbdsdr::tokens::scaled(260)});
        // Let the engine track a real audio-RMS floor, then arm auto gate.
        auto* autoBtn = win.findChild<QPushButton*>("squelchAutoBtn");
        auto* sqlCheck = win.findChild<QCheckBox*>("squelchCheck");
        auto* slider = win.findChild<QSlider*>("squelchSlider");
        if (sqlCheck) sqlCheck->setChecked(true);   // show OPEN/CLOSED state
        if (autoBtn) autoBtn->setChecked(true);
        if (slider) slider->repaint();
        QApplication::processEvents();

        QTimer::singleShot(400, [&]() {
            auto* grp = win.findChild<QWidget*>("squelchGroup");
            // Scroll the left rail so the 静噪 group is in the visible fold.
            for (auto* sa : win.findChildren<QScrollArea*>())
                sa->ensureWidgetVisible(grp);
            QApplication::processEvents();
            const QString out =
                "/home/user/Doubao/chats/38438160041798146/cpp/scratch/ui_squelch_auto.png";
            QPixmap pm = win.grab();
            pm.save(out, "PNG");
            qInfo("ui_squelch_auto saved %dx%d -> %s", pm.width(), pm.height(),
                  qPrintable(out));
            app.quit();
        });
    });
    return app.exec();
}
