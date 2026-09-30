// SPDX-License-Identifier: MIT
//
// Offscreen MainWindow test: the sky "现在/预览" time scrubber.
//   * slider range comes from tokens (center = live now, ±kSkyPreviewRangeMin).
//   * dragging sets the sky clock to wall-now + offset (throttled <=10 Hz),
//     proven by SkyView::currentTimeUtc() landing at the preview moment.
//   * releasing returns to live (currentTime == wall-now again).
// No station / TLE is required: updateLiveSatellite() always stamps the sky
// clock from the effective moment, independent of propagation.
#include <QtTest>
#include <QApplication>
#include <QSlider>
#include <QDateTime>
#include <QTimer>

#include "core/tokens.h"
#include "ui/main_window.h"
#include "ui/sky_view.h"

using namespace mbdsdr;

class TestSkyTimeSlider : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void sliderRangeFromTokens();
    void dragMovesSkyClockToPreviewMoment();
    void releaseReturnsToLiveNow();
};

void TestSkyTimeSlider::initTestCase() {
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope,
                       QDir::tempPath() + "/mbdsdr_skyslider_" +
                           QString::number(QCoreApplication::applicationPid()));
}

void TestSkyTimeSlider::sliderRangeFromTokens() {
    MainWindow win;
    win.show();
    QApplication::processEvents();
    auto* slider = win.findChild<QSlider*>("skyTimeSlider");
    QVERIFY2(slider, "skyTimeSlider must exist on the sky tab");
    QCOMPARE(slider->minimum(), -tokens::kSkyPreviewRangeMin);
    QCOMPARE(slider->maximum(),  tokens::kSkyPreviewRangeMin);
    QCOMPARE(slider->value(), 0);   // center = live now
}

void TestSkyTimeSlider::dragMovesSkyClockToPreviewMoment() {
    MainWindow win;
    win.show();
    QApplication::processEvents();
    auto* slider = win.findChild<QSlider*>("skyTimeSlider");
    auto* sky = win.findChild<ui::SkyView*>();
    QVERIFY(slider && sky);

    const QDateTime t0 = QDateTime::currentDateTimeUtc();
    slider->setValue(10);   // +10 minutes preview
    QTest::qWait(tokens::kSkyPreviewThrottleMs * 3 + 200);  // let the throttle fire
    QApplication::processEvents();

    const QDateTime shown = sky->currentTimeUtc();
    QVERIFY2(shown.isValid(), "preview must stamp the sky clock");
    // shown ≈ t0 + 10 min (a few seconds of slack for timer scheduling).
    const qint64 deltaMs = t0.msecsTo(shown);
    const qint64 expectMs = qint64(10) * 60 * 1000;
    QVERIFY2(std::abs(deltaMs - expectMs) < 8000,
             "dragging +10 min must move the sky clock to wall-now + 10 min");
    QVERIFY2(std::abs(deltaMs) > 60 * 1000,
             "preview clock must NOT equal live now");
}

void TestSkyTimeSlider::releaseReturnsToLiveNow() {
    MainWindow win;
    win.show();
    QApplication::processEvents();
    auto* slider = win.findChild<QSlider*>("skyTimeSlider");
    auto* sky = win.findChild<ui::SkyView*>();
    QVERIFY(slider && sky);

    slider->setValue(-15);
    QTest::qWait(tokens::kSkyPreviewThrottleMs * 3 + 200);
    QApplication::processEvents();
    // While previewing, the shown clock is offset from wall-now by ~15 min.
    QVERIFY2(std::abs(sky->currentTimeUtc().msecsTo(QDateTime::currentDateTimeUtc())) >
                 10 * 60 * 1000,
             "preview clock must be offset from live now before release");

    // Release == return to live. Emit sliderReleased (the real host signal).
    QMetaObject::invokeMethod(slider, "sliderReleased", Qt::DirectConnection);
    QApplication::processEvents();
    QTest::qWait(50);

    const qint64 drift = sky->currentTimeUtc().msecsTo(QDateTime::currentDateTimeUtc());
    QVERIFY2(std::abs(drift) < 3000,
             "after release the sky clock must track live wall-now again");
    QCOMPARE(slider->value(), 0);   // recentred
}

QTEST_MAIN(TestSkyTimeSlider)
#include "test_sky_time_slider.moc"
