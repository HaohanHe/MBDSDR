// SPDX-License-Identifier: MIT
#include <QtTest/QtTest>
#include <complex>
#include <vector>
#include <cmath>
#include <QDir>
#include <QFileInfo>

#include "dsp/recorder.h"
#include "dsp/file_source.h"
#include "dsp/gated_recorder.h"
#include "dsp/demod.h"

using namespace mbdsdr::dsp;

class TestRecorder : public QObject {
    Q_OBJECT
private slots:
    void sigmfRoundtrip();
    void gatedSegmentation();
    void bandwidthSwitch();
};

void TestRecorder::sigmfRoundtrip() {
    const QString dir = "test_tmp_rec";
    QDir().rmdir(dir);
    QDir().mkpath(dir);

    // Generate known sine
    const int N = 4096;
    std::vector<std::complex<float>> iq(N);
    for (int i = 0; i < N; ++i) {
        iq[i] = {static_cast<float>(std::cos(2*M_PI*100*i/48000.0)),
                 static_cast<float>(std::sin(2*M_PI*100*i/48000.0))};
    }

    Recorder rec;
    QVERIFY(rec.start(dir, 48000, 98.5e6, 20.0, "Test"));
    rec.writeIQ(iq);
    rec.stop();

    // Read back via FileSource
    QString dataPath = rec.currentFilePath();
    QString base = dataPath;
    if (base.endsWith(".sigmf-data")) base.chop(QString(".sigmf-data").size());
    FileSource fs(base);
    QVERIFY(fs.start());
    QCOMPARE(fs.sampleRate(), 48000.0);
    QCOMPARE(fs.centerFreq(), 98.5e6);

    std::vector<std::complex<float>> read(N);
    std::size_t got = fs.readIQ(read);
    QCOMPARE(got, static_cast<std::size_t>(N));

    float maxErr = 0;
    for (int i = 0; i < N; ++i) {
        float e = std::abs(read[i] - iq[i]);
        if (e > maxErr) maxErr = e;
    }
    QVERIFY2(maxErr < 1e-5f, qPrintable("roundtrip max err"));
    fs.stop();
}

void TestRecorder::gatedSegmentation() {
    GatedRecorder gr(48000);
    gr.setOutputDir("test_tmp_wav");

    // 0.5s silence, 0.3s signal, 2.0s silence, 0.4s signal
    const int block = 480; // 10ms blocks
    std::vector<QString> saved;

    // 50 blocks silence (gate=false)
    for (int i = 0; i < 50; ++i) {
        std::vector<float> b(block, 0.001f);
        saved = gr.feed(b, false);
    }
    QVERIFY(saved.empty());

    // 30 blocks signal (gate=true)
    for (int i = 0; i < 30; ++i) {
        std::vector<float> b(block, 0.5f);
        saved = gr.feed(b, true);
    }
    // Hang 1.5s = 150 blocks of silence
    std::vector<QString> allSaved;
    for (int i = 0; i < 160; ++i) {
        std::vector<float> b(block, 0.001f);
        auto s = gr.feed(b, false);
        allSaved.insert(allSaved.end(), s.begin(), s.end());
    }
    QVERIFY2(!allSaved.empty(), "first segment should have been saved");

    // Second signal burst
    for (int i = 0; i < 40; ++i) {
        std::vector<float> b(block, 0.5f);
        gr.feed(b, true);
    }
    for (int i = 0; i < 160; ++i) {
        std::vector<float> b(block, 0.001f);
        auto s = gr.feed(b, false);
        allSaved.insert(allSaved.end(), s.begin(), s.end());
    }
    QVERIFY2(allSaved.size() >= 2, "two segments saved");
    gr.flush();
}

void TestRecorder::bandwidthSwitch() {
    DemodAM am(48000, 8000);
    am.setBandwidth(10000);
    am.setBandwidth(8000);
    // Just verify no crash and reset works
    am.reset();
    QVERIFY(true);
}

QTEST_MAIN(TestRecorder)
#include "test_recorder.moc"
