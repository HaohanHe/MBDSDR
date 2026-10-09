// SPDX-License-Identifier: MIT
//
// *** SYNTHETIC TEST FIXTURE, NOT REAL RECEPTION ***
//
// End-to-end "demodulate -> sound" correctness over the REAL SpectrumEngine:
//
//   synthetic cf32_le raw IQ  ->  FileSource.openRaw  ->  SpectrumEngine thread
//   (channelizer -> demod -> resampler -> ANR -> squelch -> AGC -> gate)
//   ->  MemoryAudioSink capture.
//
// This is NOT a self-assembled chain (unlike test_demod_e2e.cpp): the engine
// run() loop, source swap, VFO manager and shared downstream blocks are the
// production ones. We assert the recovered audio pitch/level for a KNOWN 1 kHz
// tone in NFM / WFM / AM, prove the pure-noise background stays quiet with no
// dominant tone, prove rational resampling does not drift pitch, and exercise
// the squelch OPEN/CLOSED behaviour. Objective numbers (peak error Hz, SNR dB)
// are printed to stdout.
#include <QtTest/QtTest>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <cmath>
#include <vector>
#include <complex>
#include <algorithm>

#include "dsp/spectrum_engine.h"
#include "dsp/memory_audio_sink.h"
#include "dsp/fft.h"
#include "synthetic_iq_fixture.h"

using namespace mbdsdr::dsp;

namespace {

constexpr double kAudioFs = 48000.0;   // engine always renders at 48 kHz
constexpr double kSrcFs   = 2.048e6;   // typical RTL-SDR capture rate

struct Capture {
    std::vector<float> mono;
    bool gateSeenOpen = false;
    bool gateSeenClosed = false;
    float levelDb = -120.0f;   // final AGC env (currentLevelDb)
};

// Drive one scenario end-to-end through the real engine and return the captured
// post-volume 48 kHz mono buffer. `rawPath`/`sr` is the synthetic capture;
// `mode` is NFM/WFM/AM. squelchOn enables the RMS gate.
Capture runScenario(const QString& rawPath, double sr, const QString& mode,
                    bool squelchOn, float squelchDb,
                    double warmupMs, double captureMs,
                    double carrierOffsetHz = 50000.0) {
    SpectrumEngine eng;
    auto* mem = new MemoryAudioSink();
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(mem));

    QSignalSpy gateSpy(&eng, &SpectrumEngine::squelchState);
    QSignalSpy levelSpy(&eng, &SpectrumEngine::audioLevel);

    if (!eng.openOfflineFile(rawPath, sr)) return Capture{};
    eng.setDemodMode(mode);
    // Retune the selected VFO to the synthetic carrier offset so the channelizer
    // NCO down-mixes it to baseband (off the DC notch).
    eng.vfoSetFreq(eng.selectedVfoId(), carrierOffsetHz);
    eng.setSquelchThreshold(squelchDb);
    eng.setSquelchEnabled(squelchOn);
    eng.setMuted(false);

    eng.start();
    QTest::qWait(static_cast<int>(warmupMs));
    mem->clear();
    gateSpy.clear();
    QTest::qWait(static_cast<int>(captureMs));

    Capture cap;
    cap.mono = mem->buffer();
    for (const auto& sig : levelSpy) cap.levelDb = sig.value(0).toFloat();
    for (const auto& sig : gateSpy) {
        if (sig.value(0).toBool()) cap.gateSeenOpen = true;
        else                       cap.gateSeenClosed = true;
    }
    eng.shutdown();
    eng.wait(3000);
    return cap;
}

double rms(const std::vector<float>& x) {
    if (x.empty()) return 0.0;
    double s = 0; for (float v : x) s += v * v;
    return std::sqrt(s / x.size());
}

// Radix-2 FFT magnitude spectrum of the tail of `x` (skip transients).
static std::vector<float> spectrumPower(const std::vector<float>& x) {
    std::size_t N = 1;
    while (N < x.size()) N <<= 1;
    if (N > 65536) N = 65536;
    if (N < 1024) N = 1024;
    std::vector<std::complex<float>> X(N, {0.0f, 0.0f});
    const std::size_t start = (x.size() >= N) ? x.size() - N : 0;
    for (std::size_t i = 0; i < N && start + i < x.size(); ++i)
        X[i] = {x[start + i], 0.0f};
    fft(X);
    std::vector<float> pw(N / 2, 0.0f);
    for (std::size_t b = 0; b < N / 2; ++b) pw[b] = std::norm(X[b]);
    return pw;
}

// Peak bin frequency (Hz) in 50 Hz..20 kHz and its power.
double dominantHz(const std::vector<float>& x, double fs, double* peakPowerOut) {
    if (x.size() < 1024) { if (peakPowerOut) *peakPowerOut = 0; return 0; }
    const std::vector<float> pw = spectrumPower(x);
    const std::size_t N = pw.size() * 2;
    const std::size_t lo = std::max<std::size_t>(2, (std::size_t)(50.0 / fs * N));
    const std::size_t hi = std::min<std::size_t>(pw.size() - 1, (std::size_t)(20000.0 / fs * N));
    std::size_t best = lo; double bp = -1.0;
    for (std::size_t b = lo; b <= hi; ++b)
        if (pw[b] > bp) { bp = pw[b]; best = b; }
    if (peakPowerOut) *peakPowerOut = bp;
    return static_cast<double>(best) / N * fs;
}

// SNR: power in +/- symHz around target vs total in the scan band (dB).
double snrDb(const std::vector<float>& x, double fs, double targetHz,
             double symHz) {
    if (x.size() < 1024) return -999;
    const std::vector<float> pw = spectrumPower(x);
    const std::size_t N = pw.size() * 2;
    const std::size_t lo = std::max<std::size_t>(2, (std::size_t)(50.0 / fs * N));
    const std::size_t hi = std::min<std::size_t>(pw.size() - 1, (std::size_t)(20000.0 / fs * N));
    double sig = 0, tot = 0;
    for (std::size_t b = lo; b <= hi; ++b) {
        tot += pw[b];
        const double f = static_cast<double>(b) / N * fs;
        if (std::fabs(f - targetHz) <= symHz) sig += pw[b];
    }
    return 10.0 * std::log10((sig + 1e-12) / (tot - sig + 1e-12));
}

// CTCSS sub-audio gate scenario: drive the REAL NFM chain with a synthetic
// carrier and return the captured post-gate SPEAKER RMS plus the honest
// detection latch. Squelch is ON with a low threshold so the carrier opens it
// (the variable under test is the CTCSS speaker gate, not the RMS squelch).
struct CtcssGateCap { double spkRms = 0.0; bool present = false; };
CtcssGateCap runCtcssGateScenario(const QString& rawPath, double sr,
                                  bool detectorOn, double tuneHz, bool gateOn) {
    SpectrumEngine eng;
    auto* mem = new MemoryAudioSink();
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(mem));
    if (!eng.openOfflineFile(rawPath, sr)) return {};
    eng.setDemodMode("NFM");
    eng.vfoSetFreq(eng.selectedVfoId(), 50000.0);
    eng.setSquelchThreshold(-45.0f);
    eng.setSquelchEnabled(true);
    eng.setMuted(false);
    eng.setCtcssEnabled(detectorOn);
    eng.setCtcssFreqHz(tuneHz);
    eng.setCtcssGateAudio(gateOn);

    eng.start();
    QTest::qWait(2200);          // warmup: squelch opens + CTCSS latch
    mem->clear();
    QTest::qWait(1200);          // capture steady state
    CtcssGateCap cap;
    cap.spkRms = rms(mem->buffer());
    cap.present = eng.ctcssPresent();
    eng.shutdown();
    eng.wait(3000);
    return cap;
}

} // namespace

class TestEngineAudioE2E : public QObject {
    Q_OBJECT
private slots:
    void nfmRecovers1kHzTone();
    void amRecovers1kHzTone();
    void wfmRecovers1kHzToneWithPilot();
    void pureNoiseIsQuietAndToneFree();
    void squelchOpensOnSignalClosesOnNoise();
    void rationalResampleNoPitchDrift();
    void ctcssGateMutesSpeakerWithoutMatchingTone();
    void ctcssGateOpensSpeakerWithMatchingTone();
    void ctcssGateOffIsLegacySquelchPassthrough();
    void rawDirectListenProducesStereoPassthrough();
};

// ---- NFM: 1 kHz tone, +/-3 kHz deviation, clean carrier -------------------
void TestEngineAudioE2E::nfmRecovers1kHzTone() {
    QTemporaryDir dir;
    const QString path = dir.filePath("nfm.raw");
    auto iq = fixture::makeNfmIq(kSrcFs, 1.5, 1000.0, 5000.0, 0.0);
    QVERIFY(fixture::writeRawCf32(path, iq));

    Capture cap = runScenario(path, kSrcFs, "NFM", false, -60.0f, 2500, 2000);
    QVERIFY2(cap.mono.size() > 8000, "must have captured 48 kHz audio");

    double peakPow = 0;
    const double got = dominantHz(cap.mono, kAudioFs, &peakPow);
    const double arms = rms(cap.mono);
    const double snr  = snrDb(cap.mono, kAudioFs, 1000.0, 120.0);
    qInfo("NFM: dominant=%.1f Hz  RMS=%.3f  SNR@1k=%.1f dB  agcEnv=%.1f dB",
          got, arms, snr, cap.levelDb);

    QVERIFY2(std::fabs(got - 1000.0) < 150.0,
             "NFM must recover the 1 kHz modulating tone");
    QVERIFY2(arms > 0.02 && arms < 0.8, "NFM audio level must be reasonable");
    QVERIFY2(snr > 8.0, "NFM tone must dominate the recovered audio (SNR)");
}

// ---- AM: 1 kHz tone on the envelope ---------------------------------------
void TestEngineAudioE2E::amRecovers1kHzTone() {
    QTemporaryDir dir;
    const QString path = dir.filePath("am.raw");
    auto iq = fixture::makeAmIq(kSrcFs, 1.5, 1000.0, 0.8, 0.0);
    QVERIFY(fixture::writeRawCf32(path, iq));

    Capture cap = runScenario(path, kSrcFs, "AM", false, -60.0f, 2500, 2000);
    QVERIFY2(cap.mono.size() > 8000, "must have captured 48 kHz audio");

    double peakPow = 0;
    const double got = dominantHz(cap.mono, kAudioFs, &peakPow);
    const double arms = rms(cap.mono);
    const double snr  = snrDb(cap.mono, kAudioFs, 1000.0, 120.0);
    qInfo("AM: dominant=%.1f Hz  RMS=%.3f  SNR@1k=%.1f dB", got, arms, snr);

    QVERIFY2(std::fabs(got - 1000.0) < 150.0,
             "AM must recover the 1 kHz modulating tone");
    QVERIFY2(arms > 0.01, "AM must produce audible level");
    QVERIFY2(snr > 4.0, "AM tone must dominate the recovered audio (SNR)");
}

// ---- WFM: 1 kHz tone + 19 kHz pilot; mono audio recovers the tone ----------
void TestEngineAudioE2E::wfmRecovers1kHzToneWithPilot() {
    QTemporaryDir dir;
    const QString path = dir.filePath("wfm.raw");
    // pilotAmp 0.15 -> a real 19 kHz pilot inside the deviation budget.
    auto iq = fixture::makeWfmIq(kSrcFs, 1.5, 1000.0, 75000.0, 0.15, 0.0);
    QVERIFY(fixture::writeRawCf32(path, iq));

    SpectrumEngine eng;
    auto* mem = new MemoryAudioSink();
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(mem));
    QSignalSpy stereoSpy(&eng, &SpectrumEngine::stereoState);

    QVERIFY(eng.openOfflineFile(path, kSrcFs));
    eng.setDemodMode("WFM");
    eng.vfoSetFreq(eng.selectedVfoId(), 50000.0);
    eng.setSquelchEnabled(false);
    eng.setMuted(false);
    eng.start();
    QTest::qWait(3500);   // let the 19 kHz pilot PLL lock + blend pump up

    bool sawStereo = false;
    for (const auto& sig : stereoSpy)
        if (sig.value(0).toBool()) sawStereo = true;

    mem->clear();
    QTest::qWait(2000);
    // WFM with a live stereo decoder routes to writeStereo; downmix L/R to mono.
    const std::vector<float>& L = mem->stereoLeft();
    const std::vector<float>& R = mem->stereoRight();
    std::vector<float> audio;
    audio.reserve(L.size());
    for (std::size_t i = 0; i < L.size() && i < R.size(); ++i)
        audio.push_back(0.5f * (L[i] + R[i]));
    QVERIFY2(audio.size() > 8000, "WFM must capture 48 kHz audio");
    double peakPow = 0;
    const double got = dominantHz(audio, kAudioFs, &peakPow);
    const double arms = rms(audio);
    qInfo("WFM: dominant=%.1f Hz  RMS=%.3f  stereoLocked=%d",
          got, arms, sawStereo);
    QVERIFY2(std::fabs(got - 1000.0) < 200.0,
             "WFM must recover the 1 kHz modulating tone");
    QVERIFY2(arms > 0.01, "WFM must produce audible level");
    QVERIFY2(sawStereo, "a clean 19 kHz pilot must lock WFM stereo");
    eng.shutdown();
    eng.wait(3000);
}

// ---- Pure noise control: quiet, no dominant tone --------------------------
void TestEngineAudioE2E::pureNoiseIsQuietAndToneFree() {
    QTemporaryDir dir;
    const QString path = dir.filePath("noise.raw");
    // Noise-only capture: a QUIET idle background (per-I/Q std-dev small, as an
    // empty RTL-SDR channel looks -- well below a received voice).
    auto iq = fixture::makeNoiseIq(kSrcFs, 4.0, 0.001);
    QVERIFY(fixture::writeRawCf32(path, iq));

    // Squelch OFF (default): the idle channel must not be cranked up to full
    // listening volume (that is the real "沙啦" fault, fixed in iq_frontend + agc).
    Capture cap = runScenario(path, kSrcFs, "NFM", false, -60.0f, 2500, 2000);
    QVERIFY2(cap.mono.size() > 8000, "must have captured audio");
    const double arms = rms(cap.mono);
    double peakPow = 0;
    const double got = dominantHz(cap.mono, kAudioFs, &peakPow);
    const double snr = snrDb(cap.mono, kAudioFs, 1000.0, 120.0);
    qInfo("NOISE: RMS=%.3f  dominant=%.1f Hz  SNR@1k=%.1f dB  agcEnv=%.1f dB",
          arms, got, snr, cap.levelDb);

    // No signal -> no 1 kHz peak (tone-free).
    QVERIFY2(snr < 6.0, "pure noise must NOT show a 1 kHz tone");
    // The whitener/AGC fixes keep the idle floor from being blasted to near
    // clipping; the natural discriminator hiss lands well below listening peak.
    QVERIFY2(arms < 0.4, "idle background must not be blasted to listening volume");
}

// ---- Squelch: ON opens on the 1 kHz tone, CLOSES on pure noise -------------
void TestEngineAudioE2E::squelchOpensOnSignalClosesOnNoise() {
    QTemporaryDir dir;
    const QString sigPath = dir.filePath("nfm.raw");
    auto sig = fixture::makeNfmIq(kSrcFs, 1.5, 1000.0, 5000.0, 0.0);
    QVERIFY(fixture::writeRawCf32(sigPath, sig));

    const QString noisePath = dir.filePath("noise.raw");
    // An empty frequency: all-zero IQ (no carrier, no noise). The discriminator
    // outputs nothing -> RMS below threshold -> gate stays closed. The broadband
    // noise floor (no dominant tone) is covered by pureNoiseIsQuietAndToneFree.
    auto nz = fixture::makeNoiseIq(kSrcFs, 4.0, 0.0);
    QVERIFY(fixture::writeRawCf32(noisePath, nz));

    // Received 1 kHz signal above threshold -> gate must OPEN at least once.
    Capture open = runScenario(sigPath, kSrcFs, "NFM", true, -10.0f, 2500, 2000);
    QVERIFY2(open.gateSeenOpen, "1 kHz signal must open the squelch gate");

    // Empty channel below threshold -> gate must stay CLOSED (audio muted).
    Capture closed = runScenario(noisePath, kSrcFs, "NFM", true, -10.0f, 2500, 2000);
    QVERIFY2(!closed.gateSeenOpen, "a below-threshold channel must keep the squelch gate CLOSED");
    // When closed the speaker path is zeroed -> captured buffer ~silent.
    QVERIFY2(rms(closed.mono) < 0.01, "closed squelch must mute the audio");
}

// ---- Rational resampling must not drift the recovered pitch ---------------
// 2.048 MHz / 48 kHz = 42.67 (fractional) -> exercises the rational polyphase
// resampler. A second, different fractional rate (250 kHz) must land on the
// SAME 1 kHz tone (no pitch shift from the rate converter).
void TestEngineAudioE2E::rationalResampleNoPitchDrift() {
    QTemporaryDir dir;
    const QString path = dir.filePath("nfm250k.raw");
    const double sr2 = 250000.0;
    auto iq = fixture::makeNfmIq(sr2, 1.5, 1000.0, 5000.0, 0.0);
    QVERIFY(fixture::writeRawCf32(path, iq));

    Capture cap = runScenario(path, sr2, "NFM", false, -60.0f, 2500, 2000);
    QVERIFY2(cap.mono.size() > 8000, "must have captured audio at 250 ksps");
    double peakPow = 0;
    const double got = dominantHz(cap.mono, kAudioFs, &peakPow);
    qInfo("NFM@250k: dominant=%.1f Hz  RMS=%.3f", got, rms(cap.mono));
    QVERIFY2(std::fabs(got - 1000.0) < 150.0,
             "rational resampling must recover exactly 1 kHz (no pitch drift)");
}

// ---- CTCSS speaker gate: no matching tone -> speaker muted (recorder kept) --
// A 1 kHz voice carrier (no sub-audible tone) opens the squelch, but the armed
// CTCSS gate tuned to 88.5 Hz must hold the SPEAKER silent. The honest latch
// must read false (no fabricated tone), and the captured speaker buffer ~silent.
void TestEngineAudioE2E::ctcssGateMutesSpeakerWithoutMatchingTone() {
    QTemporaryDir dir;
    const QString path = dir.filePath("nfm_voice.raw");
    auto iq = fixture::makeNfmCtcssIq(kSrcFs, 2.0, 1000.0, 2500.0, 0.0, 0.0, 0.0);
    QVERIFY(fixture::writeRawCf32(path, iq));

    CtcssGateCap cap = runCtcssGateScenario(path, kSrcFs, true, 88.5, true);
    qInfo("CTCSS gate no-tone: spkRms=%.4f present=%d", cap.spkRms, (int)cap.present);
    QVERIFY2(!cap.present, "a voice-only carrier must NOT latch a sub-audible tone");
    QVERIFY2(cap.spkRms < 0.01,
             "armed CTCSS gate must mute the speaker when no matching tone is present");
}

// ---- CTCSS speaker gate: matching 88.5 Hz tone -> speaker opens ------------
// A quiet 1 kHz voice + a strong embedded 88.5 Hz PL. The armed gate tuned to
// 88.5 must latch present AND open the speaker (real audio out).
void TestEngineAudioE2E::ctcssGateOpensSpeakerWithMatchingTone() {
    QTemporaryDir dir;
    const QString path = dir.filePath("nfm_pl.raw");
    auto iq = fixture::makeNfmCtcssIq(kSrcFs, 2.0, 1000.0, 800.0, 88.5, 2000.0, 0.0);
    QVERIFY(fixture::writeRawCf32(path, iq));

    CtcssGateCap cap = runCtcssGateScenario(path, kSrcFs, true, 88.5, true);
    qInfo("CTCSS gate matched: spkRms=%.4f present=%d", cap.spkRms, (int)cap.present);
    QVERIFY2(cap.present, "the embedded 88.5 Hz tone must latch present");
    QVERIFY2(cap.spkRms > 0.02,
             "armed CTCSS gate must open the speaker on a matching tone");
}

// ---- CTCSS gate OFF: legacy squelch-only passthrough -----------------------
// Detector on but the speaker gate OFF: the speaker must follow the squelch
// open (real audio out), exactly the pre-gate legacy behavior.
void TestEngineAudioE2E::ctcssGateOffIsLegacySquelchPassthrough() {
    QTemporaryDir dir;
    const QString path = dir.filePath("nfm_voice2.raw");
    auto iq = fixture::makeNfmCtcssIq(kSrcFs, 2.0, 1000.0, 2500.0, 0.0, 0.0, 0.0);
    QVERIFY(fixture::writeRawCf32(path, iq));

    CtcssGateCap cap = runCtcssGateScenario(path, kSrcFs, true, 88.5, false);
    qInfo("CTCSS gate off: spkRms=%.4f present=%d", cap.spkRms, (int)cap.present);
    QVERIFY2(cap.spkRms > 0.02,
             "with the gate off the speaker must follow squelch (legacy passthrough)");
}

// ---- RAW direct-listen: channelized IQ reaches writeStereo as L=I / R=Q -----
// Drive a real carrier through the engine in mode=RAW with the squelch gate OFF
// so the passthrough is never muted. The speaker path must be the stereo write
// (Left=even=I, Right=odd=Q) -- both channels must exist, be frame-aligned, and
// carry non-zero samples (the channelized IQ really reached the sink). ANR/AGC
// are bypassed for RAW; this test only pins that the e2e path exists and is
// non-empty, not the exact DSP values (those are asserted in test_demod).
void TestEngineAudioE2E::rawDirectListenProducesStereoPassthrough() {
    QTemporaryDir dir;
    const QString path = dir.filePath("raw.carrier.raw");
    auto iq = fixture::makeAmIq(kSrcFs, 1.5, 1000.0, 0.8, 0.0);
    QVERIFY(fixture::writeRawCf32(path, iq));

    SpectrumEngine eng;
    auto* mem = new MemoryAudioSink();
    eng.setTestAudioSink(std::unique_ptr<IAudioSink>(mem));
    QVERIFY(eng.openOfflineFile(path, kSrcFs));
    eng.setDemodMode("RAW");
    QCOMPARE(eng.demodMode(), QString("RAW"));
    eng.vfoSetFreq(eng.selectedVfoId(), 50000.0);
    eng.setSquelchEnabled(false);     // keep the passthrough unmuted
    eng.setMuted(false);

    eng.start();
    QTest::qWait(2500);
    mem->clear();
    QTest::qWait(1500);

    const std::vector<float>& L = mem->stereoLeft();
    const std::vector<float>& R = mem->stereoRight();
    qInfo("RAW: L frames=%zu R frames=%zu  Lrms=%.4f Rrms=%.4f",
          L.size(), R.size(), rms(L), rms(R));
    QVERIFY2(L.size() > 8000, "RAW must produce 48 kHz stereo Left frames");
    QCOMPARE(L.size(), R.size());
    QVERIFY2(rms(L) > 0.005, "RAW Left (=I) must carry non-zero passthrough samples");
    QVERIFY2(rms(R) > 0.005, "RAW Right (=Q) must carry non-zero passthrough samples");
    eng.shutdown();
    eng.wait(3000);
}

QTEST_MAIN(TestEngineAudioE2E)
#include "test_engine_audio_e2e.moc"
