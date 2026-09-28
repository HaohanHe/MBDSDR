// SPDX-License-Identifier: MIT
//
// QtTest (offscreen) for ConstellationView.
// NOTE: the symbols fed here are hand-built test vectors, NOT live hardware.
// When we feed with isHardware=false the panel must show the
// "非硬件 NOT HARDWARE" tag; when we feed with isHardware=true it must not.

#include "ui/constellation_view.h"

#include <QtTest>
#include <QApplication>
#include <QImage>
#include <complex>
#include <vector>

static int g_argc = 1;
static char g_arg0[] = "test_constellation_view";
static char* g_argv = g_arg0;

using mbdsdr::ui::ConstellationView;
using mbdsdr::dsp::DigMode;

class TestConstellationView : public QObject {
    Q_OBJECT
private slots:
    void initTestCase() {
        // offscreen platform is forced by the runner; just make a widget.
        view_ = new ConstellationView();
        view_->resize(320, 320);
        view_->show();
        QTest::qWait(20);
    }

    void emptyStateNoCrash() {
        view_->clear();
        QVERIFY(view_->isEmpty());
        QCOMPARE(view_->evmText(), QString::fromUtf8("—"));
        // Render must not crash on the empty state.
        QImage img = view_->grab().toImage();
        QVERIFY(!img.isNull());
    }

    void bpskPointsPaintAtMappedPixels() {
        view_->clear();
        view_->setMode(DigMode::BPSK);
        std::vector<std::complex<float>> syms = {{1.f, 0.f}, {-1.f, 0.f}};
        view_->feedSymbols(syms, /*isHardware=*/true);
        QVERIFY(!view_->isEmpty());

        QImage img = view_->grab().toImage();
        QVERIFY(!img.isNull());

        int x1 = view_->xOfI(1.0f), y1 = view_->yOfQ(0.0f);
        int x0 = view_->xOfI(-1.0f), y0 = view_->yOfQ(0.0f);

        // Dot color is kSuccess (#5fd08a): G should dominate R and B.
        QRgb c1 = img.pixel(x1, y1);
        QRgb c0 = img.pixel(x0, y0);
        QVERIFY2(qGreen(c1) > qRed(c1) + 30, "BPSK +1 point not green");
        QVERIFY2(qGreen(c0) > qRed(c0) + 30, "BPSK -1 point not green");
    }

    void qpskFourPointsPaint() {
        view_->clear();
        view_->setMode(DigMode::QPSK);
        float u = 1.0f / std::sqrt(2.0f);
        std::vector<std::complex<float>> syms = {
            { u,  u}, {-u,  u}, {-u, -u}, { u, -u}
        };
        view_->feedSymbols(syms, /*isHardware=*/true);
        QImage img = view_->grab().toImage();

        for (auto s : syms) {
            int x = view_->xOfI(s.real());
            int y = view_->yOfQ(s.imag());
            QRgb c = img.pixel(x, y);
            QVERIFY2(qGreen(c) > qRed(c) + 20, "QPSK point not painted green");
        }
    }

    void nonHardwareTagAppears() {
        view_->clear();
        view_->setMode(DigMode::BPSK);
        QVERIFY(!view_->nonHardwareTagVisible());
        std::vector<std::complex<float>> syms = {{1.f, 0.f}};
        view_->feedSymbols(syms, /*isHardware=*/false);
        QVERIFY(view_->nonHardwareTagVisible());
    }

    void evmZeroForIdealPoints() {
        view_->clear();
        view_->setMode(DigMode::BPSK);
        std::vector<std::complex<float>> syms;
        for (int i = 0; i < 200; ++i) syms.push_back({(i % 2 ? 1.f : -1.f), 0.f});
        view_->feedSymbols(syms, true);
        // ideal points -> EVM ~ 0 (small numerical tolerance)
        QVERIFY2(view_->evmPercent() < 1.0f,
                 QByteArray("EVM should be ~0 for ideal points, got " +
                            QByteArray::number(view_->evmPercent())).constData());
    }

    void evmVisionForOffsetPoints() {
        view_->clear();
        view_->setMode(DigMode::BPSK);
        std::vector<std::complex<float>> syms;
        for (int i = 0; i < 300; ++i) syms.push_back({1.3f, 0.2f});
        view_->feedSymbols(syms, true);
        QVERIFY(view_->evmPercent() > 5.0f);
    }

    void cleanupTestCase() {
        delete view_;
    }

private:
    ConstellationView* view_ = nullptr;
};

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    TestConstellationView tc;
    return QTest::qExec(&tc, argc, argv);
}
#include "test_constellation_view.moc"
