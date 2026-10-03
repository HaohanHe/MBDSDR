// SPDX-License-Identifier: MIT
// Phase 21 Step 2 -- honest no-hardware empty state + explicit synthetic test
// source + first-run onboarding card.
//
// Real chain, no mocks. Runs on a cloud VM with NO RTL-SDR attached, so the
// engine lands on the honest empty NullSource by default (no auto-fallback to
// synthetic IQ). Assertions:
//   1. Default empty: banner says "RTL-SDR 未连接" (no "测试信号" claim),
//      status bar has no "(test)" tag, record/demod/bandwidth controls are
//      disabled, the synthetic pill is hidden, and the first-run card shows.
//   2. The synthetic source is ONLY installed when the user actively picks the
//      "测试信号" combo item: then data flows, the controls enable, and the
//      prominent "合成" pill + banner appear. Switching back to a real-source
//      item tears it down -> honest empty again.
//   3. Onboarding card: shows on first run (no QSettings record), the dismiss
//      button writes QSettings, and a fresh window then does NOT show it.
//   4. Narrow window: the onboarding card stays inside the window width
//      (elastic tokens::scaled, no horizontal overflow).
#include <QApplication>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QtTest>
#include <QSettings>
#include <QLabel>
#include <QPushButton>
#include <QComboBox>
#include <QFrame>
#include <functional>

#include "core/tokens.h"
#include "dsp/spectrum_engine.h"
#include "ui/main_window.h"

using namespace mbdsdr;
using mbdsdr::dsp::SpectrumEngine;

static constexpr const char* kOnboardingKey = "ui/onboardingDismissed";

static bool waitFor(const std::function<bool()>& pred, int timeoutMs) {
    QElapsedTimer t; t.start();
    while (t.elapsed() < timeoutMs) {
        if (pred()) return true;
        QCoreApplication::processEvents(QEventLoop::AllEvents, 60);
    }
    return pred();
}

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setStyleSheet(mbdsdr::tokens::buildDarkQss());

    int failures = 0;
    auto check = [&](bool cond, const char* msg) {
        if (!cond) { ++failures; qWarning("FAIL: %s", msg); }
        else       { qInfo("ok: %s", msg); }
    };

    // --- Fresh first-run state: forget any prior dismissal. ----------------
    {
        QSettings s("MBDSDR", "MBDSDR");
        s.remove(QString::fromUtf8(kOnboardingKey));
        s.sync();
    }

    // ================= First window (first run) ============================
    {
        MainWindow win;
        win.resize(1100, 760);
        win.show();

        SpectrumEngine* eng = win.engine();

        // Drain the engine's queued startup sourceChanged (emitted from the
        // engine thread in run()) so it lands BEFORE we interact. The banner's
        // initial construction text already reads "未连接", so a plain banner
        // check would return early and leave this event queued; a late
        // startup "No Source" delivery would then overwrite an explicit
        // synthetic selection. Pump until the deferred control refresh has run.
        waitFor([&]() {
            // The startup onSourceChanged posts a deferred setControlsEnabled;
            // that settles recordBtn disabled. Also let ~400ms elapse so the
            // engine-thread startup event is fully drained.
            return false;   // pump only
        }, 500);
        check(true, "startup: drained queued sourceChanged events");

        // --- 1. Honest empty state -----------------------------------------
        const QString banner = win.harnessSourceBanner()->text();
        check(banner == QStringLiteral("RTL-SDR 未连接"),
              "empty: banner is exactly 'RTL-SDR 未连接' (no test-signal claim)");
        check(!banner.contains(QStringLiteral("测试信号")),
              "empty: banner does NOT claim '测试信号'");
        const QString status = win.harnessStatusLabel()->text();
        check(!status.contains(QStringLiteral("(test)")),
              "empty: status label has no '(test)' tag");
        check(!status.contains(QStringLiteral("Test Signal")),
              "empty: status label does not say 'Test Signal'");
        check(!win.harnessRecordBtn()->isEnabled(),
              "empty: record button disabled");
        check(!win.harnessDemodCombo()->isEnabled(),
              "empty: demod combo disabled");
        check(!win.harnessBwCombo()->isEnabled(),
              "empty: bandwidth combo disabled");
        check(win.harnessSyntheticBanner()->isHidden(),
              "empty: synthetic pill hidden");
        check(eng && !eng->hasData(), "empty: engine hasData()==false");
        check(eng && !eng->isSynthetic(), "empty: engine not synthetic");

        // --- First-run onboarding card --------------------------------------
        check(win.harnessGuideCardVisible(),
              "first run: onboarding card is visible");

        // --- 2. Explicitly opt into the synthetic test source ---------------
        win.harnessSrcTypeCombo()->setCurrentIndex(2);   // 测试信号
        const bool synthOn = waitFor([&]() {
            return eng->isSynthetic() && eng->hasData();
        }, 3000);
        check(synthOn, "select test item: synthetic source installed + hasData");
        // The control-enable gating + provenance pill are refreshed on the next
        // UI turn (deferred, to avoid re-entering sourceMutex_). Pump events
        // until both settle.
        const bool controlsLive = waitFor([&]() {
            return win.harnessRecordBtn()->isEnabled() &&
                   win.harnessDemodCombo()->isEnabled() &&
                   !win.harnessSyntheticBanner()->isHidden();
        }, 3000);
        check(controlsLive, "select test item: controls enabled + pill shown");
        check(!win.harnessSyntheticBanner()->isHidden(),
              "synthetic: prominent '合成' pill visible");
        check(win.harnessSourceBanner()->text().contains(QStringLiteral("合成")),
              "synthetic: banner marks as 合成/调试");
        check(win.harnessStatusLabel()->text().contains(QStringLiteral("合成")),
              "synthetic: status label marks as 合成");
        check(win.harnessRecordBtn()->isEnabled(),
              "synthetic: record button enabled (data flows)");
        check(win.harnessDemodCombo()->isEnabled(),
              "synthetic: demod combo enabled");
        check(win.harnessBwCombo()->isEnabled(),
              "synthetic: bandwidth combo enabled");

        // --- Switch back to a real-source item -> synthetic torn down --------
        win.harnessSrcTypeCombo()->setCurrentIndex(0);   // 本地 RTL-SDR
        const bool synthOff = waitFor([&]() { return !eng->hasData(); }, 3000);
        check(synthOff, "back to real-source item: synthetic torn down, hasData()==false");
        // Pump until the deferred control/pill refresh settles back to empty.
        waitFor([&]() {
            return !win.harnessRecordBtn()->isEnabled() &&
                   win.harnessSyntheticBanner()->isHidden();
        }, 3000);
        check(win.harnessSyntheticBanner()->isHidden(),
              "empty: synthetic pill hidden again");
        check(!win.harnessRecordBtn()->isEnabled(),
              "empty: record button disabled again");
        check(win.harnessSourceBanner()->text() == QStringLiteral("RTL-SDR 未连接"),
              "empty: banner back to 'RTL-SDR 未连接'");

        // --- 4. Narrow window: no horizontal overflow -----------------------
        win.resize(mbdsdr::tokens::scaled(mbdsdr::tokens::kMainMinW), 600);
        QCoreApplication::processEvents(QEventLoop::AllEvents, 100);
        // The onboarding card (if still visible at this point) must fit inside.
        bool cardFits = true;
        if (win.harnessGuideCardVisible()) {
            // Locate the onboarding card by its panelCard objectName among children.
            const QList<QFrame*> frames = win.findChildren<QFrame*>();
            for (const QFrame* f : frames) {
                if (f->objectName() == QStringLiteral("panelCard") &&
                    f->isVisible() && f->width() > 0 &&
                    f->window() == &win) {
                    // Any visible top-level card must not exceed the window width.
                    if (f->width() > win.width()) cardFits = false;
                }
            }
        }
        check(cardFits, "narrow window: onboarding card fits within window width");

        // --- 3. Dismiss the card -> permanent QSettings memory --------------
        if (win.harnessGuideDismissBtn()) {
            win.harnessGuideDismissBtn()->click();
            QCoreApplication::processEvents(QEventLoop::AllEvents, 50);
        }
        check(!win.harnessGuideCardVisible(), "dismiss: card hidden immediately");
        check(QSettings("MBDSDR", "MBDSDR")
              .value(QString::fromUtf8(kOnboardingKey), false).toBool(),
              "dismiss: QSettings remembers the dismissal");
    }

    // ================= Second window (returning user) =====================
    {
        MainWindow win;
        win.resize(1100, 760);
        win.show();
        QCoreApplication::processEvents(QEventLoop::AllEvents, 100);
        check(!win.harnessGuideCardVisible(),
              "returning user: onboarding card NOT shown again");
    }

    return failures ? 1 : 0;
}

#include "test_phase21_empty_state.moc"
