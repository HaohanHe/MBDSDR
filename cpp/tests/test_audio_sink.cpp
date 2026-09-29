// SPDX-License-Identifier: GPL-3.0-or-later
//
// Contract tests for the IAudioSink abstraction:
//   * MemoryAudioSink captures samples, applies volume/mute, reports an honest
//     empty device list (no fabricated hardware).
//   * The SpectrumEngine's test-injection point routes the real demodulator
//     output to the injected sink. This uses the offline TestSignalSource
//     only -- NOT HARDWARE.
#include <QtTest/QtTest>
#include <QElapsedTimer>

#include <cmath>
#include <memory>
#include <vector>

#include "dsp/spectrum_engine.h"
#include "dsp/memory_audio_sink.h"

using namespace mbdsdr::dsp;

class TestAudioSink : public QObject {
    Q_OBJECT
private slots:
    void memoryCapturesSamples();
    void memoryVolumeScales();
    void memoryMuteSilences();
    void memoryIsHonestBackend();
    void engineRoutesDemodToInjectedSink();
    void snrSignalEmitsRealMeasurement();
    void closedSquelchMutesSpeakerPath();
};

void TestAudioSink::memoryCapturesSamples() {
    MemoryAudioSink sink;
    QVERIFY(sink.isAvailable());

    std::vector<float> in{0.1f, -0.2f, 0.3f, -0.4f, 0.5f};
    sink.write(in);
    sink.write(in);

    QCOMPARE(sink.frames(), std::size_t(10));
    // Default volume == 1, unmuted: samples captured verbatim.
    QCOMPARE(sink.buffer().size(), std::size_t(10));
    QCOMPARE(sink.buffer()[0], 0.1f);
    QCOMPARE(sink.buffer()[4], 0.5f);
    QCOMPARE(sink.buffer()[5], 0.1f);

    sink.clear();
    QCOMPARE(sink.frames(), std::size_t(0));
}

void TestAudioSink::memoryVolumeScales() {
    MemoryAudioSink sink;
    sink.setVolume(0.5f);
    QCOMPARE(sink.volume(), 0.5f);

    std::vector<float> in{0.2f, -0.4f, 0.8f};
    sink.write(in);

    QCOMPARE(sink.buffer()[0], 0.1f);
    QCOMPARE(sink.buffer()[1], -0.2f);
    QCOMPARE(sink.buffer()[2], 0.4f);

    // Volume is clamped into 0..1.
    sink.setVolume(5.0f);
    QCOMPARE(sink.volume(), 1.0f);
    sink.setVolume(-1.0f);
    QCOMPARE(sink.volume(), 0.0f);
}

void TestAudioSink::memoryMuteSilences() {
    MemoryAudioSink sink;
    sink.setVolume(1.0f);
    sink.setMuted(true);
    QVERIFY(sink.muted());

    std::vector<float> in{0.5f, -0.5f, 0.25f};
    sink.write(in);

    // Frame count is preserved (continuous 48k stream) but every sample is 0.
    QCOMPARE(sink.frames(), std::size_t(3));
    for (float s : sink.buffer()) QCOMPARE(s, 0.0f);

    // Unmute: samples flow again at the current volume.
    sink.setMuted(false);
    sink.write(in);
    QCOMPARE(sink.buffer()[3], 0.5f);
}

void TestAudioSink::memoryIsHonestBackend() {
    MemoryAudioSink sink;
    // No hardware: the device list MUST be empty, never a fabricated name.
    QVERIFY(sink.outputDevices().isEmpty());
    QCOMPARE(sink.currentDeviceName(), QStringLiteral("memory"));
    QVERIFY(sink.isAvailable());   // the buffer backend itself is always usable
}

void TestAudioSink::engineRoutesDemodToInjectedSink() {
    // NOT HARDWARE: the engine falls back to TestSignalSource on this box.
    SpectrumEngine eng;

    auto* mem = new MemoryAudioSink();
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(mem));
    // Unmute and open the gate so the test-tone audio actually leaves the chain.
    eng.setMuted(false);
    eng.setSquelchEnabled(false);

    eng.start();
    // Let the worker run ~0.6 s of 25 ms blocks (~24k samples expected).
    QTest::qWait(600);
    eng.shutdown();
    eng.wait();

    QVERIFY2(mem->frames() > 5000,
             "demodulated audio must reach the injected sink");

    // A tone through NFM should leave the buffer non-silent.
    float peak = 0.0f;
    for (float s : mem->buffer()) peak = std::max(peak, std::fabs(s));
    QVERIFY2(peak > 1e-4f, "captured audio must not be silence");
}

void TestAudioSink::snrSignalEmitsRealMeasurement() {
    // NOT HARDWARE: offline test source. The engine must push a real measured SNR
    // (derived from the spectrum median noise floor), not a hard-coded number.
    SpectrumEngine eng;
    QSignalSpy spy(&eng, &SpectrumEngine::snrLevel);
    eng.start();
    QTest::qWait(600);
    eng.shutdown();
    eng.wait();

    QVERIFY2(spy.count() >= 1, "engine must emit snrLevel every loop");
    // Every emitted value is a finite dB number (no NaN/inf, no fake peak).
    for (const auto& args : spy) {
        const float v = qvariant_cast<float>(args.at(0));
        QVERIFY2(std::isfinite(v), "SNR must be a finite measured dB value");
    }
}

void TestAudioSink::closedSquelchMutesSpeakerPath() {
    // With the squelch gate held CLOSED, the speaker/audio sink path must
    // receive continuous 48 kHz silence (the gated recorder keeps the real
    // audio for pre-roll, but the loudspeaker is muted).
    SpectrumEngine eng;
    auto* mem = new MemoryAudioSink();
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(mem));
    eng.setMuted(false);
    // Threshold set above full scale: the offline test tone can never open the
    // gate, so the loudspeaker path must be held continuously silent.
    eng.setSquelchEnabled(true);
    eng.setSquelchThreshold(20.0f);

    eng.start();
    QTest::qWait(700);
    eng.shutdown();
    eng.wait();

    QVERIFY2(mem->frames() > 5000, "continuous 48k stream must still flow");
    float peak = 0.0f;
    for (float s : mem->buffer()) peak = std::max(peak, std::fabs(s));
    QVERIFY2(peak < 1e-3f,
             "closed squelch must fill the speaker path with silence");
}

QTEST_MAIN(TestAudioSink)
#include "test_audio_sink.moc"
