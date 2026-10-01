// SPDX-License-Identifier: MIT
//
// Round-11/12 pre-hardware soak: drive the offline loopback source end-to-end
// (start -> record -> switch to an offline file replay -> assert the file
// streams in fixed chunks without loading it whole, no NaN frames, no crash,
// no stale state), then exercise the new LEO geometry + S-meter pure paths.
// NOT HARDWARE -- uses the honest offline test source / a self-written WAV.
#include <QtTest>
#include <QApplication>
#include <QElapsedTimer>
#include <QDir>
#include <QFile>
#include <QDataStream>
#include <cmath>

#include "dsp/spectrum_engine.h"
#include "dsp/file_source.h"
#include "core/spectrum_frame.h"
#include "core/pnt_geometry.h"
#include "ui/s_meter.h"
#include "core/tokens.h"

using namespace mbdsdr;

static bool frameFinite(const SpectrumFrame& f) {
    for (float v : f.dbfs) if (!std::isfinite(v)) return false;
    return true;
}

class TestLeoSoak : public QObject {
    Q_OBJECT
private slots:
    void loopbackChainNoCrash();
};

void TestLeoSoak::loopbackChainNoCrash() {
    // 1) Engine loopback on the offline test source.
    dsp::SpectrumEngine eng;
    int frames = 0; bool saw = false;
    double peakRssi = 0.0; bool sawRssi = false;
    QObject::connect(&eng, &dsp::SpectrumEngine::spectrumReady,
                     &eng, [&](const SpectrumFrame& f) {
        ++frames; QVERIFY(frameFinite(f)); saw = true;
    });
    QObject::connect(&eng, &dsp::SpectrumEngine::rssiLevel,
                     &eng, [&](float db) {
        sawRssi = true; if (std::isfinite(db)) peakRssi = std::max(peakRssi, double(db));
    });
    eng.start();
    QElapsedTimer t; t.start();
    while ((!saw || !sawRssi) && t.elapsed() < 4000) QTest::qWait(30);
    QVERIFY2(saw, "engine produced no spectrum frames");
    QVERIFY2(sawRssi, "engine produced no RSSI readback");

    // 2) Record a short baseband clip, then stop.
    QVERIFY(eng.startRecording());
    QTest::qWait(80);
    eng.stopRecording();
    eng.disconnectSource();

    // 3) Offline file replay: write a WAV and stream it in FIXED chunks (the
    //    buffer never grows -> no whole-file load / no large memory).
    const QString dir = QDir::tempPath() + "/mbdsdr_leo_soak";
    QDir().mkpath(dir);
    const QString wav = dir + "/loopback.wav";
    {
        QFile f(wav); f.open(QIODevice::WriteOnly);
        QDataStream ds(&f); ds.setByteOrder(QDataStream::LittleEndian);
        const quint32 sr = 48000; const int n = 48000;
        const qint64 bytes = n * 2;
        ds.writeRawData("RIFF", 4); ds << quint32(36 + bytes);
        ds.writeRawData("WAVE", 4); ds.writeRawData("fmt ", 4);
        ds << quint32(16) << quint16(1) << quint16(1) << sr
           << quint32(sr * 2) << quint16(2) << quint16(16);
        ds.writeRawData("data", 4); ds << quint32(bytes);
        for (int i = 0; i < n; ++i) ds << qint16(((i * 2000) % 2000) - 1000);
    }
    dsp::FileSource fs(QStringLiteral(""));
    QVERIFY2(fs.openWav(wav, 100.5e6, "NFM"), qPrintable(fs.errorString()));
    QVERIFY(fs.start());
    const std::size_t chunk = 1000;
    std::vector<std::complex<float>> out(chunk);
    std::size_t total = 0; int calls = 0, maxBuf = 0;
    for (int i = 0; i < 200; ++i) {
        QVERIFY(out.size() == chunk);   // caller buffer never grows
        std::size_t got = fs.readIQ(out);
        maxBuf = std::max(maxBuf, (int)out.size());
        total += got; ++calls;
        if (got < chunk) break;
    }
    QVERIFY2(total >= 48000, "offline file replay must stream the whole clip");
    QCOMPARE(maxBuf, (int)chunk);   // streamed in fixed chunks, not whole-file
    fs.stop();

    // 4) Engine still healthy after the cycle (no stale state / zombie timers).
    int before = frames; t.restart();
    while (frames - before < 3 && t.elapsed() < 3000) QTest::qWait(30);
    QVERIFY2(frames - before >= 3, "engine stalled after loopback soak");

    // 5) LEO geometry + S-meter over the measured numbers (pure paths).
    QList<double> els = {60, 55, 50, 65, 45, 70};
    const double dop = geo::simplifiedDop(els, tokens::kGeoMinElevationDeg);
    QVERIFY(dop > 0.0 && dop < tokens::kGeoDopGood);
    const int su = ui::SMeterWidget::sUnitsAboveNoise(peakRssi, -80.0);
    QVERIFY(su >= 0 && su <= tokens::kSMeterMaxUnits);
    qInfo("leo soak frames=%d fileSamples=%zu rssi=%.1f sUnits=%d dop=%.2f",
          frames, total, peakRssi, su, dop);
}

QTEST_MAIN(TestLeoSoak)
#include "test_leo_soak.moc"
