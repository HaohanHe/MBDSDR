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
#include <QPushButton>
#include <QDoubleSpinBox>
#include <QSpinBox>
#include <QDir>
#include <QSplitter>
#include <QTableWidget>
#include <cstdlib>
#include <cmath>
#include "core/tokens.h"
#include "core/spectrum_frame.h"
#include "ui/main_window.h"
#include "ui/spectrum_widget.h"
#include "ui/spectrum_display.h"
#include "ui/s_meter.h"
#include "ui/rssi_trend.h"
#include "ui/bookmark_manager.h"

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    // Throwaway QSettings so real persisted focus-mode state never hides rails.
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope,
                       QDir::tempPath() + "/mbdsdr_shot_narrow_" +
                           QString::number(QCoreApplication::applicationPid()));
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    const QString out = QString::fromLocal8Bit(qgetenv("MBD_OUT"));

    // Optional: seed the REAL recent-tune list (MBD_TUNEHIST=1) into the
    // throwaway QSettings BEFORE MainWindow restores it, so the "最近" combo in
    // the 频率 group renders its real populated rows instead of the honest empty
    // state. MBD_TUNEHIST=empty seeds nothing (forces the "无调谐记录" item).
    // Pure persisted-state display -- no demo data inside the app itself.
    const QByteArray tuneHist = qgetenv("MBD_TUNEHIST");
    {
        QSettings s("MBDSDR", "MBDSDR");
        if (tuneHist == "empty") {
            s.remove(mbdsdr::tokens::kSettingsKeyTuneHistory);
        } else if (tuneHist.size()) {
            s.setValue(mbdsdr::tokens::kSettingsKeyTuneHistory,
                       QVariantList{100.0e6, 98.5e6, 137.0e6, 121.5e6});
        }
        s.sync();
    }

    // Optional: seed the persisted waterfall ring depth (MBD_WFDEPTH=128/256/512)
    // BEFORE MainWindow restores it, so both the "瀑布" row depth combo AND the
    // canvas ring come up at the chosen depth -- the real persisted round-trip,
    // not a post-hoc poke. The throwaway QSettings path above keeps this off the
    // user's data. A value outside the named set honestly resolves to the default.
    const int wfDepth = qEnvironmentVariableIntValue("MBD_WFDEPTH");
    {
        QSettings s("MBDSDR", "MBDSDR");
        if (wfDepth > 0)
            s.setValue(mbdsdr::tokens::kSettingsKeyWfDepth, wfDepth);
        s.sync();
    }

    // Optional: seed the persisted peak-hold decay tier (MBD_MHDECAY=0.5/1.5/3.0)
    // BEFORE MainWindow restores it, so both the spectrum "衰减" combo AND the
    // canvas maxHold step come up at the chosen 慢/中/快 tier -- the real persisted
    // round-trip, not a post-hoc poke. The throwaway QSettings path above keeps
    // this off the user's data. A value outside the named set honestly resolves
    // to the default (1.5). Off by default so every other shot is unchanged.
    const double mhDecaySeed =
        QString::fromLocal8Bit(qgetenv("MBD_MHDECAY")).toDouble();
    if (mhDecaySeed > 0.0) {
        QSettings s("MBDSDR", "MBDSDR");
        s.setValue(mbdsdr::tokens::kSettingsKeyMaxHoldDecay, mhDecaySeed);
        s.sync();
    }

    // Optional: seed the persisted dB reference gridline density (MBD_DBGRID=10/20/40)
    // BEFORE MainWindow restores it, so both the spectrum "格线" combo AND the canvas
    // horizontal dB grid come up at the chosen density -- the real persisted
    // round-trip, not a post-hoc poke. The throwaway QSettings path above keeps this
    // off the user's data. A value outside the named set honestly resolves to the
    // default (20). Off by default so every other shot is unchanged.
    const int dbGridSeed = qEnvironmentVariableIntValue("MBD_DBGRID");
    if (dbGridSeed > 0) {
        QSettings s("MBDSDR", "MBDSDR");
        s.setValue(mbdsdr::tokens::kSettingsKeyDbGridStep, dbGridSeed);
        s.sync();
    }

    // Optional: seed the persisted waterfall scroll-pause flag (MBD_WFPAUSE=1)
    // BEFORE MainWindow restores it, so both the spectrum "暂停滚动" tool button
    // AND the canvas frozen snapshot come up in the paused state -- the real
    // persisted round-trip, not a post-hoc poke. The throwaway QSettings path
    // above keeps this off the user's data. Off by default so every other shot
    // is unchanged.
    if (qgetenv("MBD_WFPAUSE") == "1") {
        QSettings s("MBDSDR", "MBDSDR");
        s.setValue(mbdsdr::tokens::kSettingsKeyWfScrollPaused, true);
        s.sync();
    }

    // Optional: seed the persisted Rx demod-mode + bandwidth (MBD_MODE=USB
    // MBD_BW=9000) BEFORE MainWindow restores it, so the left "解调" / "带宽"
    // combos render the restored values -- the real persisted round-trip, not a
    // post-hoc poke. The matching vfo/0 row is seeded consistently so the
    // engine's authoritative VFO restore lands on the same mode/bandwidth instead
    // of the NFM/12.5k defaults. Off by default so every other shot is unchanged.
    const QByteArray modeSeed = qgetenv("MBD_MODE");
    bool bwOk = false;
    const double bwSeed = qgetenv("MBD_BW").toDouble(&bwOk);
    if (!modeSeed.isEmpty() && bwOk && bwSeed > 0.0) {
        QSettings s("MBDSDR", "MBDSDR");
        s.setValue("rx/demodMode", QString::fromUtf8(modeSeed));
        s.setValue("rx/bandwidth", bwSeed);
        s.setValue("vfo/count", 1);
        s.setValue("vfo/0/freq", 98.5e6);
        s.setValue("vfo/0/mode", QString::fromUtf8(modeSeed));
        s.setValue("vfo/0/bw", bwSeed);
        s.setValue("vfo/0/color", QString::fromUtf8(mbdsdr::tokens::kAccent));
        s.setValue("vfo/0/selected", true);
        s.sync();
    }

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

    // Optional: place the two dual measurement cursors (MBD_CURSORSHOT=1) so
    // the trace renders the teal/pink dashed cursors AND their quiet lavender
    // center-symmetric mirror dotted lines about the tuned centre f0. Pure
    // display geometry pushed to the canvas -- no storage, no demo data in the
    // app. f0 = 98.5 MHz, fs = 2.4 MHz: cursor A at +300 kHz (98.8) mirrors to
    // 98.2; cursor B at -200 kHz (98.3) mirrors to 98.7. Off by default so every
    // other screenshot is unchanged.
    if (qgetenv("MBD_CURSORSHOT").size()) {
        if (!sw) {   // resolve the spectrum tab if an earlier gate did not
            for (auto* tb : win.findChildren<QTabWidget*>())
                for (int i = 0; i < tb->count(); ++i)
                    if (tb->tabText(i) == QString::fromUtf8("频谱"))
                        sw = qobject_cast<mbdsdr::ui::SpectrumWidget*>(tb->widget(i));
        }
        if (sw && sw->displayCanvas()) {
            mbdsdr::ui::SpectrumDisplay* cv = sw->displayCanvas();
            const int bins = 512;
            auto frame = [&](int peakBin, float peakDb) {
                mbdsdr::SpectrumFrame fr;
                fr.sampleRateHz = 2.4e6;
                fr.centerFreqHz = 98.5e6;
                fr.fftSize = bins;
                fr.dbfs.assign(bins, -100.0f);
                if (peakBin >= 2 && peakBin < bins - 2) {
                    fr.dbfs[peakBin] = peakDb;
                    fr.dbfs[peakBin - 1] = peakDb - 4.0f;
                    fr.dbfs[peakBin + 1] = peakDb - 4.0f;
                    fr.dbfs[peakBin - 2] = peakDb - 10.0f;
                    fr.dbfs[peakBin + 2] = peakDb - 10.0f;
                }
                fr.sourceName = "test";
                fr.isTestSignal = true;
                return fr;
            };
            // A real frame sets the 2.4 MHz visible window; without it the mirror
            // x would collapse (span=1 Hz) and be culled off-canvas.
            for (int i = 0; i < 4; ++i)
                cv->setSpectrum(frame(256, -30.0f));
            cv->placeCursorA(98.8e6);   // teal dashed; mirror -> 98.2
            cv->placeCursorB(98.3e6);   // pink dashed; mirror -> 98.7
        }
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

    // Optional: collapse a named bookmark group (MBD_BMKCOLLAPSE=VHF) by the real
    // cellClicked path on its section header, so the shot shows the ▸ collapsed
    // state -- the header keeps its real "(N)" count while that group's rows are
    // hidden. Pure UI state on top of MBD_BMKGROUP's real store; off by default.
    if (qgetenv("MBD_BMKCOLLAPSE").size()) {
        const QString want = QString::fromUtf8(qgetenv("MBD_BMKCOLLAPSE"));
        if (auto* tbl = win.findChild<QTableWidget*>("bmTable")) {
            for (int v = 0; v < tbl->rowCount(); ++v) {
                auto* it = tbl->item(v, 0);
                if (it && it->data(Qt::UserRole).toInt() == -1 &&
                    it->text().contains(want)) {
                    QMetaObject::invokeMethod(tbl, "cellClicked",
                                              Qt::DirectConnection,
                                              Q_ARG(int, v), Q_ARG(int, 0));
                    // The right rail stacks several group boxes above the table,
                    // so the collapsed header can sit below the table viewport;
                    // scroll it into view for the shot.
                    tbl->scrollToBottom();
                    break;
                }
            }
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

    // Optional: fill the waterfall ring to its (possibly non-default) depth so the
    // shot shows the deep rolling history the chosen MBD_WFDEPTH buys, and proves
    // the ring was actually re-allocated at that depth rather than left at 256.
    // Pure offline test signal; off by default so every other screenshot is
    // unchanged.
    if (wfDepth > 0) {
        if (!sw) {   // resolve the spectrum tab if an earlier gate did not
            for (auto* tb : win.findChildren<QTabWidget*>())
                for (int i = 0; i < tb->count(); ++i)
                    if (tb->tabText(i) == QString::fromUtf8("频谱"))
                        sw = qobject_cast<mbdsdr::ui::SpectrumWidget*>(tb->widget(i));
        }
        if (sw && sw->displayCanvas()) {
            mbdsdr::ui::SpectrumDisplay* cv = sw->displayCanvas();
            const int bins = 512;
            auto frame = [&](int peakBin, float peakDb) {
                mbdsdr::SpectrumFrame fr;
                fr.sampleRateHz = 2.4e6;
                fr.centerFreqHz = 98.5e6;
                fr.fftSize = bins;
                fr.dbfs.assign(bins, -100.0f);
                if (peakBin >= 2 && peakBin < bins - 2) {
                    fr.dbfs[peakBin] = peakDb;
                    fr.dbfs[peakBin - 1] = peakDb - 4.0f;
                    fr.dbfs[peakBin + 1] = peakDb - 4.0f;
                    fr.dbfs[peakBin - 2] = peakDb - 10.0f;
                    fr.dbfs[peakBin + 2] = peakDb - 10.0f;
                }
                fr.sourceName = "test";
                fr.isTestSignal = true;
                return fr;
            };
            // A drifting carrier so the waterfall paints a sloping trace instead of
            // a flat line -- makes the filled depth visually obvious.
            for (int i = 0; i < wfDepth + 16; ++i)
                cv->setSpectrum(frame(180 + (i % 120), -22.0f));
        }
    }

    // Optional: demonstrate the y-axis range freeze (MBD_YFREEZE=1). Feeds strong
    // carriers so auto-range eases the ceiling down from 0 dB, then turns auto off
    // -- the frozen ceiling stays on the dB grid and the spinboxes sync to it.
    // Pure offline test signal; off by default so every other screenshot is unchanged.
    if (qgetenv("MBD_YFREEZE").size()) {
        if (!sw) {   // resolve the spectrum tab if PEAKSHOT did not already
            for (auto* tb : win.findChildren<QTabWidget*>())
                for (int i = 0; i < tb->count(); ++i)
                    if (tb->tabText(i) == QString::fromUtf8("频谱"))
                        sw = qobject_cast<mbdsdr::ui::SpectrumWidget*>(tb->widget(i));
        }
        if (sw && sw->displayCanvas()) {
            mbdsdr::ui::SpectrumDisplay* cv = sw->displayCanvas();
            const int bins = 512;
            auto frame = [&](int peakBin, float peakDb) {
                mbdsdr::SpectrumFrame fr;
                fr.sampleRateHz = 2.4e6;
                fr.centerFreqHz = 98.5e6;
                fr.fftSize = bins;
                fr.dbfs.assign(bins, -100.0f);
                if (peakBin >= 2 && peakBin < bins - 2) {
                    fr.dbfs[peakBin] = peakDb;
                    fr.dbfs[peakBin - 1] = peakDb - 4.0f;
                    fr.dbfs[peakBin + 1] = peakDb - 4.0f;
                    fr.dbfs[peakBin - 2] = peakDb - 10.0f;
                    fr.dbfs[peakBin + 2] = peakDb - 10.0f;
                }
                fr.sourceName = "test";
                fr.isTestSignal = true;
                return fr;
            };
            // Feed strong frames so auto-range eases the ceiling down.
            for (int i = 0; i < 30; ++i)
                cv->setSpectrum(frame(200, -25.0f));
            // Turn off auto range -> freeze the current eased ceiling.
            cv->setAutoRangeOn(false);
            // Sync the spinboxes to the frozen range (mirrors the widget button handler).
            sw->setDbSpinValues(
                static_cast<int>(std::round(cv->currentDbFloor())),
                static_cast<int>(std::round(cv->currentDbCeil())));
        }
    }

    // Optional: populate the mini RSSI/dBfs trend strip (MBD_RSSITREND=1) with a
    // deterministic sequence of REAL-valued dBfs samples pushed straight into the
    // widget -- the same pushDbfs() path the production onRssiLevel slot uses --
    // so the recent-N-sample line renders instead of its honest empty caption.
    // Pure display-harness injection; off by default so every other shot is
    // unchanged. No mock signal is invented inside the app itself.
    if (qgetenv("MBD_RSSITREND").size()) {
        if (auto* trend = win.findChild<mbdsdr::ui::RssiTrendWidget*>()) {
            // A gentle fading/fluctuating envelope so the shape is visible.
            for (int i = 0; i < 60; ++i) {
                const float db = -55.0f
                    - 8.0f * std::sin(i * 0.35f)
                    - (i / 60.0f) * 6.0f;   // slow downward drift (fading)
                trend->pushDbfs(db);
            }
            // Also seed the S-meter so the two adjacent strips line up in state.
            if (auto* meter = win.findChild<mbdsdr::ui::SMeterWidget*>()) {
                meter->setNoiseFloorDbfs(-72.0);
                meter->setSignalDbfs(-55.0);
            }
        }
    }

    // Optional: run a tiny range scan to NATURAL completion so the status label
    // shows "扫描完成，已回到 XX.XXX MHz" (MBD_SCANRETURN=1). Sets a narrow
    // 500 kHz range with 100 ms dwell so the walk finishes within the ~1.2 s
    // pre-grab delay, then retunes back to the pre-scan frequency. Real scanner
    // state machine driven by the real engine RSSI -- no mock. Off by default.
    if (qgetenv("MBD_SCANRETURN").size()) {
        // Jump to the scan/bookmarks tab.
        for (auto* t : win.findChildren<QTabWidget*>()) {
            for (int i = 0; i < t->count(); ++i) {
                if (t->tabText(i) == QString::fromUtf8("扫描/书签")) {
                    t->setCurrentIndex(i);
                    t->currentWidget()->show();
                }
            }
        }
        // Narrow range + short dwell so the walk ends quickly.
        if (auto* sp = win.findChild<QDoubleSpinBox*>("scanStartSpin")) sp->setValue(98.0);
        if (auto* sp = win.findChild<QDoubleSpinBox*>("scanStopSpin"))  sp->setValue(98.5);
        if (auto* sp = win.findChild<QSpinBox*>("scanDwellSpin"))       sp->setValue(100);
        // Click the real start button -> real scanner_->start() + pre-scan freq capture.
        if (auto* btn = win.findChild<QPushButton*>("scanStartBtn"))
            btn->click();
    }

    QTimer::singleShot(1200, [&]() {
        // Drive the receive-link four-state badge (MBD_CONNSTATE=running/error/
        // dropped) through the SAME real slots the engine's source signals reach.
        // Done HERE, right before the grab, so the engine's own startup source
        // emit (run() head) can't overwrite the injected state between injection
        // and capture. Off by default => the honest boot idle state shows.
        const QByteArray connState = qgetenv("MBD_CONNSTATE");
        if (connState == "running") {
            win.harnessSourceChanged(QString::fromUtf8("RTL-SDR"), true);
        } else if (connState == "error") {
            win.harnessSourceError(QString::fromUtf8("连接被拒绝 (refused)"));
        } else if (connState == "dropped") {
            win.harnessSourceDropped();
        } else if (connState == "connecting") {
            win.harnessShowConnecting();
        }
        QApplication::processEvents();
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
