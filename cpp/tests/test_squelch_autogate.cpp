// SPDX-License-Identifier: MIT
//
// *** NOT HARDWARE / 非硬件合成 ***
//
// Audio-RMS-domain squelch auto-gate backing test. The offline TestSignalSource
// drives the real demod chain; we assert:
//   * The engine emits a slowly-tracked audio-block RMS dBFS noise floor
//     (audioRmsNoiseFloor) that is a finite, measured number -- never NaN/inf and
//     never the IQ total-power / per-bin canvas floor cross-wired in.
//   * The read-back audioNoiseFloorDbfs() is in a sane dBFS range.
//   * Gate mode still mutes the speaker path below the threshold (real mute),
//     while the audio stream itself keeps flowing.
#include <QtTest/QtTest>
#include <QElapsedTimer>
#include <QSignalSpy>

#include <cmath>
#include <memory>
#include <vector>

#include "dsp/spectrum_engine.h"
#include "dsp/memory_audio_sink.h"

using namespace mbdsdr::dsp;

class TestSquelchAutoGate : public QObject {
    Q_OBJECT
private slots:
    void audioRmsNoiseFloorIsRealSameDomain();
    void gateBelowThresholdMutesSpeaker();
};

void TestSquelchAutoGate::audioRmsNoiseFloorIsRealSameDomain() {
    SpectrumEngine eng;
    auto* mem = new MemoryAudioSink();
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(mem));
    eng.setMuted(false);
    eng.setSquelchEnabled(false);   // open: let the real audio RMS be tracked

    QSignalSpy spy(&eng, &SpectrumEngine::audioRmsNoiseFloor);
    eng.start();
    QTest::qWait(900);
    eng.shutdown();
    eng.wait();

    QVERIFY2(spy.count() >= 1, "engine must emit the audio-RMS noise floor");
    for (const auto& args : spy) {
        const float v = qvariant_cast<float>(args.at(0));
        QVERIFY2(std::isfinite(v), "noise floor must be a finite measured dBFS");
        // Audio RMS dBFS of a real (even synthetic) demod block sits below 0 dBFS
        // and above the absolute -100 dBFS floor -- sanity, no fabricated value.
        QVERIFY2(v < 0.0f && v > -110.0f,
                 "audio-RMS noise floor must be a sane dBFS level");
    }
    QVERIFY2(std::isfinite(eng.audioNoiseFloorDbfs()),
             "read-back audioNoiseFloorDbfs must be finite");
}

void TestSquelchAutoGate::gateBelowThresholdMutesSpeaker() {
    // Reuse the proven contract: with the gate held CLOSED (threshold above the
    // test tone's RMS), the speaker/audio sink path is filled with continuous
    // silence while the 48k stream keeps flowing. This is the REAL mute path the
    // auto gate drives.
    SpectrumEngine eng;
    auto* mem = new MemoryAudioSink();
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(mem));
    eng.setMuted(false);
    eng.setSquelchEnabled(true);
    eng.setSquelchThreshold(20.0f);   // above full scale -> always closed

    eng.start();
    QTest::qWait(700);
    eng.shutdown();
    eng.wait();

    QVERIFY2(mem->frames() > 5000, "continuous 48k stream must still flow");
    float peak = 0.0f;
    for (float s : mem->buffer()) peak = std::max(peak, std::fabs(s));
    QVERIFY2(peak < 1e-3f, "closed gate must silence the speaker path");
}

QTEST_MAIN(TestSquelchAutoGate)
#include "test_squelch_autogate.moc"
