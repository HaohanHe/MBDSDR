// SPDX-License-Identifier: MIT
// Offscreen screenshot: narrow-window adaptation check.
// Not in ctest. Env: MBD_OUT (png path). Shrinks the main window to a small
// usable width and grabs it so the left control rail (already a flick-scroll
// QScrollArea), the center spectrum/waterfall, and the right tab rail must not
// clip or overlap text. No demo data -- the offline test-signal engine feeds it.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QSettings>
#include <QTabWidget>
#include <QScrollArea>
#include <QScrollBar>
#include <QGroupBox>
#include <QDir>
#include <QSplitter>
#include <cstdlib>
#include "core/tokens.h"
#include "ui/main_window.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    // Throwaway QSettings so real persisted focus-mode state never hides rails.
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope,
                       QDir::tempPath() + "/mbdsdr_shot_narrow_" +
                           QString::number(QCoreApplication::applicationPid()));
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    const QString out = QString::fromLocal8Bit(qgetenv("MBD_OUT"));

    mbdsdr::MainWindow win;
    // Shrink to a narrow but usable width. The left rail is a QScrollArea so
    // its controls scroll rather than clip; the spectrum keeps its minimum.
    // Width/height are parameterized via env (MBD_W/MBD_H) so the same target
    // can reproduce a 640 narrow shot and a 1920 full-width shot for
    // flexibility checks; defaults preserve the original 820x640.
    const int w = qEnvironmentVariableIntValue("MBD_W");
    const int h = qEnvironmentVariableIntValue("MBD_H");
    win.resize(w > 0 ? w : 820, h > 0 ? h : 640);
    win.show();

    // Optional: jump to a named right-rail tab (e.g. MBD_TAB=扫描/书签 or
    // 录制库) so the same harness can verify every rail at every width.
    const QByteArray tabName = qgetenv("MBD_TAB");
    if (!tabName.isEmpty()) {
        for (auto* t : win.findChildren<QTabWidget*>()) {
            for (int i = 0; i < t->count(); ++i) {
                if (t->tabText(i) == QString::fromUtf8(tabName)) {
                    t->setCurrentIndex(i);
                    t->currentWidget()->show();
                }
            }
        }
    }

    // Optional: scroll the left control rail to its BOTTOM so groups below the
    // fold (e.g. the network-audio tap) land in the viewport, without the
    // horizontal-offset artifact ensureWidgetVisible can produce.
    if (qgetenv("MBD_SCROLL") == "bottom") {
        for (auto* sa : win.findChildren<QScrollArea*>())
            sa->verticalScrollBar()->setValue(sa->verticalScrollBar()->maximum());
    } else if (!qgetenv("MBD_SCROLL").isEmpty()) {
        if (auto* w = win.findChild<QWidget*>(QString::fromUtf8(qgetenv("MBD_SCROLL")))) {
            for (auto* sa : win.findChildren<QScrollArea*>()) {
                sa->ensureWidgetVisible(w);
                sa->horizontalScrollBar()->setValue(0);   // no left-offset artifact
            }
        }
    }

    QTimer::singleShot(1200, [&]() {
        if (qEnvironmentVariableIntValue("MBD_DUMP") > 0) {
            for (const char* nm : {"netAudioHostEdit", "netAudioPortSpin",
                                   "netAudioStartBtn", "netAudioStopBtn",
                                   "netAudioStatusLabel", "spyserverStatusLabel",
                                   "scanLinkChk", "scanLinkStateLabel", "recLibIqBtn"}) {
                if (auto* w = win.findChild<QWidget*>(QString::fromLatin1(nm)))
                    qInfo("GEOM %-20s x=%d y=%d w=%d h=%d vis=%d",
                          nm, w->x(), w->y(), w->width(), w->height(), w->isVisible());
            }
            for (auto* sa : win.findChildren<QScrollArea*>()) {
                qInfo("SCROLLAREA vp=%d widget=%d wr=%d",
                      sa->viewport()->width(), sa->widget() ? sa->widget()->width() : -1,
                      sa->widgetResizable());
            }
            for (auto* gb : win.findChildren<QGroupBox*>()) {
                if (gb->isVisible())
                    qInfo("GBOX %-16s w=%d min=%d", gb->title().toUtf8().constData(),
                          gb->width(), gb->minimumSizeHint().width());
            }
        }
        qInfo("scaleFactor=%.3f window=%dx%d", mbdsdr::tokens::scaleFactor(),
              win.width(), win.height());
        const auto splits = win.findChildren<QSplitter*>();
        for (auto* s : splits) {
            QStringList sz;
            for (int i = 0; i < s->count(); ++i) sz << QString::number(s->widget(i)->width());
            qInfo("splitter widths: %s", sz.join(' ').toLocal8Bit().constData());
        }
        QPixmap pm = win.grab();
        pm.save(out, "PNG");
        qInfo("narrow screenshot saved to %s (%dx%d)",
              out.toLocal8Bit().constData(), pm.width(), pm.height());
        app.quit();
    });
    return app.exec();
}
