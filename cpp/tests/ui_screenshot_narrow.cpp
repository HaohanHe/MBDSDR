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
#include "core/spectrum_frame.h"
#include "ui/main_window.h"
#include "ui/spectrum_widget.h"
#include "ui/spectrum_display.h"
#include "ui/bookmark_manager.h"

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
    // Harness escape hatch: the production floor tokens::kMainMinW keeps the app
    // readable, but this narrow-adaptation harness exists to check layouts BELOW
    // that floor. An explicit MBD_W under the floor lifts the minimum here
    // (screenshot harness only; production keeps its tokenized floor).
    if (w > 0 && w < mbdsdr::tokens::kMainMinW)
        win.setMinimumSize(0, 0);
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

    // Optional: close (hide) one named right-rail panel tab (MBD_HIDETAB=数据)
    // via the real tabCloseRequested path, so the shot shows the closable-tab
    // affordance + the "显示全部面板" restore button honestly render. Off by
    // default so every other screenshot is unchanged.
    const QByteArray hideName = qgetenv("MBD_HIDETAB");
    if (!hideName.isEmpty()) {
        for (auto* t : win.findChildren<QTabWidget*>()) {
            for (int i = 0; i < t->count(); ++i) {
                if (t->tabText(i) == QString::fromUtf8(hideName) && t->isTabEnabled(i))
                    QMetaObject::invokeMethod(t, "tabCloseRequested",
                                              Qt::DirectConnection, Q_ARG(int, i));
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

    // Optional: feed deterministic synthetic carriers straight into the spectrum
    // canvas (MBD_PEAKSHOT=1) so the auto peak table renders real detected rows
    // -- frequency / power / signed-Δ -- instead of sitting on the no-source
    // blank. Pure offline test signal, clearly labelled by the existing banner;
    // off by default so every other screenshot is unchanged.
    mbdsdr::ui::SpectrumWidget* sw = nullptr;
    if (qgetenv("MBD_PEAKSHOT").size()) {
        for (auto* tb : win.findChildren<QTabWidget*>()) {
            for (int i = 0; i < tb->count(); ++i)
                if (tb->tabText(i) == QString::fromUtf8("频谱"))
                    sw = qobject_cast<mbdsdr::ui::SpectrumWidget*>(tb->widget(i));
        }
        if (sw) {
            const int bins = 512;
            const double fs = 2.4e6, f0 = 98.5e6;
            auto frame = [&](int c1, float d1, int c2, float d2) {
                mbdsdr::SpectrumFrame fr;
                fr.sampleRateHz = fs;
                fr.centerFreqHz = f0;
                fr.fftSize = bins;
                fr.dbfs.assign(bins, -100.0f);
                for (int c = 0; c < 2; ++c) {
                    const int b = (c == 0) ? c1 : c2;
                    const float db = (c == 0) ? d1 : d2;
                    if (b < 2 || b >= bins - 2) continue;
                    fr.dbfs[b] = db;
                    fr.dbfs[b - 1] = db - 4.0f;
                    fr.dbfs[b + 1] = db - 4.0f;
                    fr.dbfs[b - 2] = db - 10.0f;
                    fr.dbfs[b + 2] = db - 10.0f;
                }
                fr.sourceName = "test";
                fr.isTestSignal = true;
                return fr;
            };
            // Repeat >= kPeakMinSeenFrames so the carriers mature into rows.
            for (int i = 0; i < 6; ++i)
                sw->setSpectrum(frame(180, -22.0f, 340, -38.0f));
        }
    }

    // Optional: inject two deterministic bookmark frequencies (MBD_BMKSHOT=1) so
    // the spectrum bookmark overlay renders its real dotted reference lines on
    // the trace. Pure display geometry pushed straight to the canvas -- no
    // storage, no demo data in the app itself. Both frequencies sit inside the
    // ~98.5 MHz startup view (default tuned centre, fs 2.4 MHz). Off by default
    // so every other screenshot is unchanged.
    if (qgetenv("MBD_BMKSHOT").size()) {
        if (!sw) {   // resolve the spectrum tab if PEAKSHOT did not already
            for (auto* tb : win.findChildren<QTabWidget*>()) {
                for (int i = 0; i < tb->count(); ++i)
                    if (tb->tabText(i) == QString::fromUtf8("频谱"))
                        sw = qobject_cast<mbdsdr::ui::SpectrumWidget*>(tb->widget(i));
            }
        }
        if (sw && sw->displayCanvas())
            sw->displayCanvas()->setBookmarkHz({98.0e6, 99.0e6});
    }

    // Optional: seed three groups of bookmarks (MBD_BMKGROUP=1) straight into the
    // real BookmarkManager so the grouped bookmark table renders its section
    // headers ("默认 (N)" / "组名 (N)") on the 扫描/书签 tab. Real store + the
    // real refresh path; the throwaway QSettings path at the top keeps this off
    // the user's persisted data. Off by default so every other screenshot is
    // unchanged.
    if (qgetenv("MBD_BMKGROUP").size()) {
        if (auto* bm = win.bookmarkManager()) {
            bm->clear();
            const mbdsdr::ui::Bookmark rows[] = {
                {"本地调频", 98.5e6,   "WFM", 120000.0, ""    },
                {"航空警戒", 121.5e6,  "AM",  8000.0,   "AIR" },
                {"航空导航", 118.0e6,  "AM",  8000.0,   "AIR" },
                {"VHF 直频", 144.8e6,  "NFM", 12500.0,  "VHF" },
                {"VHF 中继", 145.6e6,  "NFM", 12500.0,  "VHF" },
            };
            for (const auto& r : rows) bm->add(r);
            win.refreshScanBookmarksUi();
        }
    }

    // Optional: fill the waterfall history and pin a known seconds-per-row so the
    // vertical time axis ("now" at the top, "-Ns" counting seconds down) renders
    // deterministically for the layout shot. Offscreen frames fed in a tight loop
    // measure ~0 inter-frame gap, so the production EWMA (honestly) stays silent;
    // this gate pins the same row period the live engine would converge to. Pure
    // display demo data -- off by default so every other screenshot is unchanged.
    if (qgetenv("MBD_WATERTICK").size()) {
        if (!sw) {   // resolve the spectrum tab if PEAKSHOT did not already
            for (auto* tb : win.findChildren<QTabWidget*>())
                for (int i = 0; i < tb->count(); ++i)
                    if (tb->tabText(i) == QString::fromUtf8("频谱"))
                        sw = qobject_cast<mbdsdr::ui::SpectrumWidget*>(tb->widget(i));
        }
        if (sw && sw->displayCanvas()) {
            mbdsdr::ui::SpectrumDisplay* cv = sw->displayCanvas();
            const int bins = 512;
            auto frame = [&](int c1, float d1) {
                mbdsdr::SpectrumFrame fr;
                fr.sampleRateHz = 2.4e6;
                fr.centerFreqHz = 98.5e6;
                fr.fftSize = bins;
                fr.dbfs.assign(bins, -100.0f);
                if (c1 >= 2 && c1 < bins - 2) {
                    fr.dbfs[c1] = d1;
                    fr.dbfs[c1 - 1] = d1 - 4.0f;
                    fr.dbfs[c1 + 1] = d1 - 4.0f;
                    fr.dbfs[c1 - 2] = d1 - 10.0f;
                    fr.dbfs[c1 + 2] = d1 - 10.0f;
                }
                fr.sourceName = "test";
                fr.isTestSignal = true;
                return fr;
            };
            // Fill the whole rolling ring so the time axis spans its full depth.
            for (int i = 0; i < 256; ++i)
                cv->setSpectrum(frame(180, -22.0f));
            cv->setSecondsPerRowForTest(0.05);   // 256 rows => ~12.8 s history
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
