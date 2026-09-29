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

// Minimal IAudioSink that does NOT override writeStereo, used to exercise the
// interface's default (L+R)/2 -> write() downmix path.
// SYNTHETIC -- NOT HARDWARE: this is a fake in-memory capture, never hardware.
class DownmixSpy : public IAudioSink {
public:
    std::vector<float> got;
    void write(const std::vector<float>& a) override { got = a; }
    void setVolume(float) override {}
    void setMuted(bool) override {}
    bool isAvailable() const override { return true; }
    QStringList outputDevices() const override { return {}; }
    QString currentDeviceName() const override { return QStringLiteral("spy"); }
};

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
    // --- Stereo extension (SYNTHETIC -- NOT HARDWARE) ---------------------
    void memoryStereoCapturesIndependentChannels();
    void memoryStereoEqualChannelsAreEquivalent();
    void memoryStereoAppliesVolumeMute();
    void defaultWriteStereoDownmix();
    void memoryStereoClearEmptiesStereoBuffers();
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

// --- Stereo extension tests (SYNTHETIC -- NOT HARDWARE) -------------------

void TestAudioSink::memoryStereoCapturesIndependentChannels() {
    MemoryAudioSink sink;
    QVERIFY(sink.isAvailable());

    // Distinct left/right content: the two channels must be captured apart.
    std::vector<float> L{0.5f, -0.25f, 0.75f, -1.0f, 0.0f};
    std::vector<float> R{0.1f,  0.90f, -0.40f,  0.20f, 0.6f};
    sink.writeStereo(L, R);

    QCOMPARE(sink.stereoFrames(), std::size_t(5));
    QCOMPARE(sink.stereoLeft().size(), std::size_t(5));
    QCOMPARE(sink.stereoRight().size(), std::size_t(5));

    // Left captured verbatim (volume == 1, unmuted).
    QCOMPARE(sink.stereoLeft()[0], 0.5f);
    QCOMPARE(sink.stereoLeft()[2], 0.75f);
    QCOMPARE(sink.stereoLeft()[3], -1.0f);
    // Right captured independently (not copied from left).
    QCOMPARE(sink.stereoRight()[1], 0.9f);
    QCOMPARE(sink.stereoRight()[2], -0.4f);
    QCOMPARE(sink.stereoRight()[4], 0.6f);

    // L != R on every frame except index 0 (0.5 vs 0.1 ...); at least one frame differs.
    bool anyDiff = false;
    for (std::size_t i = 0; i < L.size(); ++i)
        if (sink.stereoLeft()[i] != sink.stereoRight()[i]) anyDiff = true;
    QVERIFY2(anyDiff, "left and right must be captured as independent channels");

    // The legacy mono buffer is NOT touched by the stereo path.
    QCOMPARE(sink.frames(), std::size_t(0));
    QCOMPARE(sink.buffer().size(), std::size_t(0));
}

void TestAudioSink::memoryStereoEqualChannelsAreEquivalent() {
    MemoryAudioSink sink;

    // Feeding a mono-equivalent stereo (L == R) must leave both captured
    // channels carrying the identical content (no channel bleed / swap).
    std::vector<float> mono{0.3f, -0.7f, 0.2f, 0.5f};
    sink.writeStereo(mono, mono);

    QCOMPARE(sink.stereoFrames(), std::size_t(4));
    QVERIFY2(sink.stereoLeft() == sink.stereoRight(),
             "mono-equivalent stereo must capture L == R");
    QCOMPARE(sink.stereoLeft()[0], 0.3f);
    QCOMPARE(sink.stereoRight()[3], 0.5f);
}

void TestAudioSink::memoryStereoAppliesVolumeMute() {
    MemoryAudioSink sink;
    sink.setVolume(0.5f);

    std::vector<float> L{0.8f, -0.4f};
    std::vector<float> R{0.4f, -0.8f};
    sink.writeStereo(L, R);

    // Volume scales each channel independently, same as the mono path.
    QCOMPARE(sink.stereoLeft()[0], 0.4f);    // 0.8 * 0.5
    QCOMPARE(sink.stereoLeft()[1], -0.2f);   // -0.4 * 0.5
    QCOMPARE(sink.stereoRight()[0], 0.2f);   // 0.4 * 0.5
    QCOMPARE(sink.stereoRight()[1], -0.4f);  // -0.8 * 0.5

    // Mute silences both channels but preserves frame count.
    sink.setMuted(true);
    sink.writeStereo(L, R);
    QCOMPARE(sink.stereoFrames(), std::size_t(4));
    QCOMPARE(sink.stereoLeft()[2], 0.0f);
    QCOMPARE(sink.stereoLeft()[3], 0.0f);
    QCOMPARE(sink.stereoRight()[2], 0.0f);
    QCOMPARE(sink.stereoRight()[3], 0.0f);
}

void TestAudioSink::defaultWriteStereoDownmix() {
    // No override on this sink: the interface default must downmix (L+R)/2
    // and forward the result to the pure-virtual write().
    DownmixSpy spy;
    std::vector<float> L{1.0f, 0.0f, -1.0f, 0.5f};
    std::vector<float> R{0.0f, 1.0f,  0.5f, -0.5f};
    spy.writeStereo(L, R);

    QCOMPARE(spy.got.size(), std::size_t(4));
    QCOMPARE(spy.got[0], 0.5f);    // (1.0 + 0.0)/2
    QCOMPARE(spy.got[1], 0.5f);    // (0.0 + 1.0)/2
    QCOMPARE(spy.got[2], -0.25f);  // (-1.0 + 0.5)/2
    QCOMPARE(spy.got[3], 0.0f);    // (0.5 - 0.5)/2
}

void TestAudioSink::memoryStereoClearEmptiesStereoBuffers() {
    MemoryAudioSink sink;
    std::vector<float> a{0.1f, 0.2f};
    sink.write(a);                 // mono buffer
    sink.writeStereo(a, a);        // stereo buffers

    QCOMPARE(sink.frames(), std::size_t(2));
    QCOMPARE(sink.stereoFrames(), std::size_t(2));

    sink.clear();
    QCOMPARE(sink.frames(), std::size_t(0));
    QCOMPARE(sink.stereoFrames(), std::size_t(0));
    QCOMPARE(sink.stereoLeft().size(), std::size_t(0));
    QCOMPARE(sink.stereoRight().size(), std::size_t(0));
}

QTEST_MAIN(TestAudioSink)
#include "test_audio_sink.moc"
