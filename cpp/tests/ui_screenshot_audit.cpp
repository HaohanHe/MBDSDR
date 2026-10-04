// SPDX-License-Identifier: MIT
// Offscreen audit harness (Figma checklist self-check).
// Captures the post-audit UI: main window with a visible keyboard FOCUS ring on
// the frequency spinbox, then switches the right tab group through 录制库 /
// AI 助手 / 天空 and opens the settings dialog. Every grab is saved under
// scratch/ and Read back to confirm no overlap / clipping / sticker feel.
//
// Registered as ui_shot_audit in CMakeLists (SHOT_SRCS, not part of mbdsdr).
// Env: MBD_W, MBD_H, MBD_OUTDIR.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QTabWidget>
#include <QDoubleSpinBox>
#include <QComboBox>
#include <QMouseEvent>
#include <QDialog>
#include <QList>
#include <cstdlib>
#include <cmath>
#include "core/tokens.h"
#include "ui/main_window.h"
#include "ui/settings_dialog.h"
#include "ui/spectrum_display.h"

namespace {
void grabLater(QWidget* w, const QString& path, int delayMs, QApplication& app) {
    QTimer::singleShot(delayMs, [w, path, &app]() {
        QPixmap pm = w->grab();
        pm.save(path, "PNG");
        qInfo("audit -> %s (%dx%d)", path.toLocal8Bit().constData(),
              pm.width(), pm.height());
    });
}
} // namespace

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    const int w = std::atoi(qgetenv("MBD_W").constData());
    const int h = std::atoi(qgetenv("MBD_H").constData());
    const QString dir = QString::fromLocal8Bit(
        qgetenv("MBD_OUTDIR").isEmpty() ? "scratch" : qgetenv("MBD_OUTDIR"));

    mbdsdr::MainWindow win;
    win.resize(w > 0 ? w : 1280, h > 0 ? h : 800);
    win.show();

    // Collect the right tab widget by its object name (center tabs have a
    // different objectName; findChildren order is not guaranteed).
    QTabWidget* right = win.findChild<QTabWidget*>("rightTabs");

    // 1. Main window with focus ring on the frequency spinbox.
    if (QDoubleSpinBox* f = win.findChild<QDoubleSpinBox*>("freqSpin"))
        f->setFocus();
    grabLater(&win, dir + "/ui_audit_main.png", 1200, app);

    // --- Persistence / fixed markers / decimation audit shots --------------
    if (mbdsdr::ui::SpectrumDisplay* cv = win.findChild<mbdsdr::ui::SpectrumDisplay*>()) {
        QTimer::singleShot(1500, [cv]() {
            cv->setPersistenceMode(2);   // high: ghost trail under live trace
            cv->addFixedMarker(cv->viewCenterHz() - 200e3, "M1");
            cv->addFixedMarker(cv->viewCenterHz() + 350e3, "M2");
            // Select M1 so its amber dashed line + handle dot shows.
            const int mx = cv->xForFrequency(cv->viewCenterHz() - 200e3);
            QMouseEvent press(QEvent::MouseButtonPress,
                              QPoint(mx, cv->height() / 3),
                              Qt::LeftButton, Qt::LeftButton, Qt::NoModifier);
            QApplication::sendEvent(cv, &press);
        });
        grabLater(&win, dir + "/ui_persistence.png", 2600, app);
        grabLater(&win, dir + "/ui_markers.png", 2900, app);
        grabLater(&win, dir + "/ui_markers_interact.png", 3100, app);
        // Dual measurement cursors: place A/B and show the Δf read-out.
        QTimer::singleShot(1600, [cv]() {
            cv->placeCursorA(cv->viewCenterHz() - 150e3);
            cv->placeCursorB(cv->viewCenterHz() + 220e3);
        });
        grabLater(&win, dir + "/ui_dual_cursor.png", 3400, app);
        // Dual cursors with a synthetic signal frame so the dashed A/B lines and
        // the Δf read-out box are actually visible (Phase 28 leftover).
        QTimer::singleShot(1700, [cv]() {
            mbdsdr::SpectrumFrame fr;
            fr.centerFreqHz = cv->viewCenterHz();
            fr.sampleRateHz = 1000000.0;
            fr.fftSize = 1024;
            fr.dbfs.assign(1024, -90.0f);
            for (int i = 0; i < 1024; ++i) {
                const double off = (i - 512) / 512.0 * 500e3;
                if (std::abs(off) < 40e3) fr.dbfs[i] = -30.0f;
            }
            cv->setSpectrum(fr);
            cv->placeCursorA(cv->viewCenterHz() - 150e3);
            cv->placeCursorB(cv->viewCenterHz() + 220e3);
        });
        grabLater(&win, dir + "/ui_dual_cursor_sig.png", 3600, app);
    }
    if (QComboBox* d = win.findChild<QComboBox*>("decimCombo")) {
        QTimer::singleShot(1500, [d]() { d->setCurrentIndex(2); });  // x4
        grabLater(&win, dir + "/ui_decimation.png", 3300, app);
    }

    // 2. Right tab: 录制库.
    if (right) {
        // Grab the QTabBar directly (it is the visible strip of group headers +
        // tabs). Force a sensible size so offscreen layout does not collapse it.
        QTabBar* bar = right->findChild<QTabBar*>();
        if (bar) {
            bar->setEnabled(true);
            bar->resize(520, bar->sizeHint().height());
            bar->show();
            QCoreApplication::processEvents();
            QTimer::singleShot(1800, [bar, dir]() {
                QPixmap pm = bar->grab();
                pm.save(dir + "/ui_ux_tabs.png", "PNG");
                qInfo("tabs -> %s (%dx%d)", qPrintable(dir + "/ui_ux_tabs.png"),
                      pm.width(), pm.height());
            });
        }

        for (int i = 0; i < right->count(); ++i)
            if (right->tabText(i).contains("录制")) right->setCurrentIndex(i);
        grabLater(&win, dir + "/ui_audit_reclib.png", 2100, app);

        // 3. Right tab: AI 助手.
        for (int i = 0; i < right->count(); ++i)
            if (right->tabText(i).contains("AI")) right->setCurrentIndex(i);
        grabLater(&win, dir + "/ui_audit_ai.png", 2600, app);

        // 4. Right tab: 天空.
        for (int i = 0; i < right->count(); ++i)
            if (right->tabText(i).contains("天空")) right->setCurrentIndex(i);
        grabLater(&win, dir + "/ui_audit_sky.png", 3300, app);
    }

    // 5. Settings dialog (modal-ish; show non-blocking and grab).
    QTimer::singleShot(3600, [&]() {
        mbdsdr::ui::SettingsDialog dlg(&win);
        dlg.show();
        QTimer::singleShot(500, [&]() {
            QPixmap pm = dlg.grab();
            pm.save(dir + "/ui_audit_settings.png", "PNG");
            qInfo("audit -> %s (%dx%d)",
                  qPrintable(dir + "/ui_audit_settings.png"),
                  pm.width(), pm.height());
            app.quit();
        });
    });

    return app.exec();
}
