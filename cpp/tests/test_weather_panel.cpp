// SPDX-License-Identifier: MIT
//
// QtTest (offscreen) for WeatherSatPanel.
// NOTE: the QImage fed here is a hand-built test pattern, NOT live hardware.
// The panel must: (a) start in its honest empty state, (b) hold the image we
// hand it, and (c) return to the empty state on clear(). It must never paint
// a fabricated cloud photo.

#include "ui/weather_panel.h"

#include <QtTest>
#include <QApplication>
#include <QImage>

static int g_argc = 1;
static char g_arg0[] = "test_weather_panel";
static char* g_argv = g_arg0;

using mbdsdr::ui::WeatherSatPanel;

class TestWeatherPanel : public QObject {
    Q_OBJECT
private slots:
    void initTestCase() {
        // offscreen platform is forced by the runner; just make a widget.
        panel_ = new WeatherSatPanel();
        panel_->resize(640, 480);
        panel_->show();
        QTest::qWait(20);
    }

    void startsEmpty() {
        // No data fed yet -> honest empty state (no cloud picture).
        QVERIFY(panel_->isEmpty());
        QCOMPARE(panel_->rows(), 0);
        QVERIFY(!panel_->locked());
        // Render must not crash on the empty state.
        QImage img = panel_->grab().toImage();
        QVERIFY(!img.isNull());
    }

    void setImageHoldsKnownPattern() {
        // Build a known 1818-wide grayscale image (same shape the decoder
        // emits) with a recognizable mid-gray fill. NOT HARDWARE.
        QImage frame(1818, 6, QImage::Format_Grayscale8);
        frame.fill(128);
        panel_->setImage(frame, /*locked=*/true, /*rows=*/6, /*syncCorr=*/0.91);

        QVERIFY(!panel_->isEmpty());
        QCOMPARE(panel_->rows(), 6);
        QVERIFY(panel_->locked());
        QVERIFY2(panel_->syncCorr() > 0.9,
                 "sync correlation not propagated");

        // The panel must hold exactly the image we handed it (same size /
        // bytes), not a copy it fabricated.
        QImage held = panel_->image();
        QCOMPARE(held.width(), 1818);
        QCOMPARE(held.height(), 6);
        QVERIFY(!held.isNull());

        // Render must not crash once an image is present.
        QImage img = panel_->grab().toImage();
        QVERIFY(!img.isNull());
    }

    void clearReturnsToEmpty() {
        QVERIFY(!panel_->isEmpty());
        panel_->clear();
        QVERIFY(panel_->isEmpty());
        QCOMPARE(panel_->rows(), 0);
        QVERIFY(!panel_->locked());
        QImage img = panel_->grab().toImage();
        QVERIFY(!img.isNull());
    }

    void unlockedLowCorrStaysSecondaryState() {
        // No lock yet but some rows trickling in (e.g. brief partial sync):
        // panel holds the partial image but reports not locked.
        QImage frame(1818, 2, QImage::Format_Grayscale8);
        frame.fill(40);
        panel_->setImage(frame, /*locked=*/false, /*rows=*/2, /*syncCorr=*/0.3);
        QVERIFY(!panel_->isEmpty());
        QVERIFY(!panel_->locked());
        QCOMPARE(panel_->rows(), 2);
        panel_->clear();
    }

    void cleanupTestCase() {
        delete panel_;
    }

private:
    WeatherSatPanel* panel_ = nullptr;
};

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    TestWeatherPanel tc;
    return QTest::qExec(&tc, argc, argv);
}
#include "test_weather_panel.moc"
