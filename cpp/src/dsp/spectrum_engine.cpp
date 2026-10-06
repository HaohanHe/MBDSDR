// SPDX-License-Identifier: MIT
#include "spectrum_engine.h"
#include "rtl_sdr_source.h"
#include "rtl_tcp_source.h"
#include "test_signal.h"
#include "file_source.h"
#include "power_spectrum.h"
#include "noise_blanker.h"
#include "null_audio_sink.h"
#include "core/tokens.h"

#include <QDebug>
#include <QByteArray>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSettings>
#include <QTimer>
#include <algorithm>
#include <cmath>
#include <chrono>
#include <thread>

namespace mbdsdr {
namespace dsp {

// Capture-band edge roll-off for the in-band IF-offset move. A VFO may ride on
// the channelizer NCO (tuner left parked) only while it sits inside the central
// fraction of the band; once it approaches the tuner edge (where the passband
// rolls off) we genuinely retune the source to the target so the VFO lands back
// near capture center (offset ~0).
namespace {
constexpr double kVfoEdgeFraction = 0.85;

// The synthetic test source is an EXPLICIT debugging opt-in, never automatic.
// Honor the MBDSDR_TEST_SOURCE=1 environment variable (the --test-source CLI
// flag sets this same variable in main.cpp before the engine is constructed,
// so both channels funnel through this single read). Any other value / absence
// leaves the production path on the honest empty state.
bool testSourceEnvEnabled() {
    return qgetenv("MBDSDR_TEST_SOURCE") == "1";
}
}

SpectrumEngine::SpectrumEngine(QObject* parent) : QThread(parent) {
    reconnectClock_.start();
    // Auto-reconnect must touch QTcpSocket, and a QTcpSocket must live in a
    // thread that runs an event loop. The run() loop is not one, so recovery
    // is requested as a queued signal and performed here, on the engine's
    // home thread (the UI thread in the app, the test thread in tests).
    connect(this, &SpectrumEngine::reconnectRequested,
            this, &SpectrumEngine::handleReconnectRequested,
            Qt::QueuedConnection);
    // The synthetic test source is opt-in only. Read the env/CLI opt-in BEFORE
    // picking the startup source so a production launch with no RTL-SDR attached
    // lands on the honest empty NullSource (no fabricated IQ), not on a
    // self-synthesizing test signal.
    testSourceEnabled_.store(testSourceEnvEnabled());
    auto rtl = std::make_unique<RtlSdrSource>();
    if (rtl->start()) {
        source_ = std::move(rtl);
    } else if (testSourceEnabled_.load()) {
        // Explicitly requested (--test-source / MBDSDR_TEST_SOURCE / API):
        // synthetic source, honestly labeled "Test Signal".
        source_ = std::make_unique<TestSignalSource>();
        source_->start();
    } else {
        // No hardware AND no explicit opt-in: honest empty state (NullSource),
        // which produces no IQ until a device connects / a file opens / the
        // caller explicitly enables the test source.
        source_ = std::make_unique<NullSource>();
        source_->start();
    }
    // NOTE: do NOT emit sourceChanged() here. The engine is being constructed
    // before MainWindow's connect() calls exist, so the signal would be lost.
    // The initial state is emitted once in run(), after connections are wired.

    audioOut_ = new AudioOutput(this);
    // Headless/automated runs must never open a real render device: an offscreen
    // platform or an explicit MBDSDR_NULL_AUDIO routes the write path to a sink
    // that discards (the real QtAudioSink is built lazily on first write, so it
    // is never created here). Squelch/demod logic is untouched -- only the
    // render endpoint is muted, so no demod hiss reaches a speaker.
    if (qgetenv("MBDSDR_NULL_AUDIO") == "1" ||
        qgetenv("QT_QPA_PLATFORM").contains("offscreen")) {
        testSink_ = std::make_unique<NullAudioSink>();
    }
    audioSink_ = testSink_ ? testSink_.get()
                           : static_cast<IAudioSink*>(audioOut_);
    // AGC operating point from the named tokens: drive recovered audio toward the
    // target, but CEIL the boost so an idle noise floor is not blasted to
    // listening volume (the real "sandpaper hiss" fault).
    agc_.setTarget(tokens::kAgcTargetLin);
    agc_.setMaxGain(tokens::kAgcMaxGainLin);
    // Shared output directory (user-configurable, program-dir "record").
    {
        QSettings rs("MBDSDR", "MBDSDR");
        recDir_ = rs.value("rec/dir", QStringLiteral("record")).toString();
        if (recDir_.isEmpty()) recDir_ = QStringLiteral("record");
        recMaxSegSecs_ = rs.value("rec/max_seg_s",
                                  tokens::kRecMaxSegmentSecondsDefault).toDouble();
    }
    gatedRec_.setOutputDir(recDir_);
    telemetryClock_.start();
    aptEmitClock_.start();
    stereoEmitClock_.start();
    // Single default VFO at the source center, NFM 12.5 kHz -- identical to
    // the legacy single-channel receiver on first boot.
    vfoManager_.initDefault(source_->sampleRate(), source_->centerFreq(),
                            demodMode_, bandwidth_);
    rebuildDemod();
}

SpectrumEngine::~SpectrumEngine() {
    shutdown();
    wait();
    if (recorder_.isRecording()) recorder_.stop();
    if (wavWriter_.isRecording()) wavWriter_.stop();
    gatedRec_.flush();
    if (source_) source_->stop();
}

void SpectrumEngine::rebuildDemod() {
    // Per-channel channelizer/demod/resampler are owned and rebuilt lazily by
    // vfoManager_ (one channel per VFO, mirroring the legacy single-channel
    // chain). Here we only reset the SHARED downstream blocks and re-sync the
    // cached mode used for the test-signal generator + recording template.
    vfoManager_.sourceRateChanged();   // input rate may have changed -> all channels rebuild

    frontend_.reset();
    squelch_.reset();
    agc_.reset();
    cwDecoder_.reset();
    adsbDecoder_.reset();
    aptDecoder_.reset();
    aptActive_ = false;

    // Keep the cached mode/bandwidth in lock-step with the selected VFO so the
    // legacy demodMode()/bandwidth() getters and expandRecTemplate() are correct.
    if (const VfoChannel* sel = vfoManager_.selected()) {
        demodMode_ = sel->mode;
        bandwidth_ = sel->bandwidthHz;
    }

    if (auto* ts = dynamic_cast<TestSignalSource*>(source_.get())) {
        if (demodMode_ == "AM") ts->setModulation("am");
        else if (demodMode_ == "NFM" || demodMode_ == "WFM") ts->setModulation("fm");
        else ts->setModulation("tone");
    }
}

void SpectrumEngine::setFftSize(int n) {
    if (n == 1024 || n == 2048 || n == 4096) fftSize_.store(n);
}
void SpectrumEngine::shutdown() { running_.store(false); }

double SpectrumEngine::centerFreq() const {
    // Async tuning (mailbox) means the source LO can lag the commanded value by
    // one engine tick. Report a pending command immediately (the dial/source is
    // driven to exactly this value on the next loop); otherwise read the LO.
    double pendingHz = 0.0;
    {
        QMutexLocker cl(&const_cast<QMutex&>(ctrlMutex_));
        if (pending_.dCenterFreq) pendingHz = pending_.centerFreqHz;
    }
    QMutexLocker lk(&const_cast<QMutex&>(sourceMutex_));
    const double actual = source_ ? source_->centerFreq() : 0.0;
    return pendingHz > 0.0 ? pendingHz : actual;
}

DeviceCapabilities SpectrumEngine::sourceCapabilities() const {
    // Read ONLY the independent snapshot: never take sourceMutex_ here, or the
    // UI thread starves against the tight real-source run loop.
    QMutexLocker lk(&capsMutex_);
    return capsSnapshot_;
}

void SpectrumEngine::updateCapsSnapshotLocked() {
    QMutexLocker lk(&capsMutex_);
    capsSnapshot_ = source_ ? source_->capabilities() : noDeviceCapabilities();
    gainTableSnapshot_ = source_ ? source_->availableGainsDb() : std::vector<double>{};
}

std::vector<double> SpectrumEngine::availableGainsDb() const {
    QMutexLocker lk(&capsMutex_);
    return gainTableSnapshot_;
}

void SpectrumEngine::onSetCenterFreq(double f) {
    // Honest guard: a non-positive / non-finite frequency would drive the
    // tuner and every downstream mixer into NaN. Ignore it rather than queue
    // a bogus tune (the caller keeps its previous value).
    if (!(f > 0.0) || !std::isfinite(f)) return;
    // Enqueue only: the engine thread applies the tuning (see
    // applyControlCommandsLocked) so the caller's thread never blocks on USB.
    {
    QMutexLocker lk(&ctrlMutex_);
    pending_.centerFreqHz = f;
    pending_.dCenterFreq = true;
    }
    applyIfIdle();
}
void SpectrumEngine::onSetSampleRate(double r) {
    // Guard against bogus rates (<=0 / NaN / absurdly high) reaching the
    // channelizer and the rtl_tcp setSampleRate command.
    if (!(r > 0.0) || !std::isfinite(r) || r > 32e6) return;
    {
    QMutexLocker lk(&ctrlMutex_);
    pending_.sampleRateHz = r;
    pending_.dSampleRate = true;
    }
    applyIfIdle();
}
void SpectrumEngine::onSetGain(double g) {
    {
    QMutexLocker lk(&ctrlMutex_);
    pending_.gainDb = g;
    pending_.dGain = true;
    }
    applyIfIdle();
}
void SpectrumEngine::setDemodMode(const QString& m) {
    {
    QMutexLocker lk(&ctrlMutex_);
    pending_.demodMode = m;
    pending_.dDemodMode = true;
    }
    applyIfIdle();
}
void SpectrumEngine::setSquelchThreshold(float db) {
    squelchThreshold_ = db;        // cache for read-back (Squelch has no getter)
    squelch_.setThresholdDb(db);
    squelchAuto_.store(false);     // a manual threshold disarms auto
}
void SpectrumEngine::setSquelchEnabled(bool e) { squelch_.setEnabled(e); }

bool SpectrumEngine::squelchEnabled() const {
    return squelch_.mode() == Squelch::Mode::Gate;
}
bool SpectrumEngine::squelchOpen() const { return squelch_.open(); }

double SpectrumEngine::scanBand(double lowHz, double highHz, double stepHz,
                                  double* peakFreqHzOut) {
    double peakDb = -200.0;
    double peakFreq = lowHz;
    for (double f = lowHz; f <= highHz; f += stepHz) {
        std::vector<std::complex<float>> iq(1024);
        {
            QMutexLocker lk(&sourceMutex_);
            source_->setCenterFreq(f);
            source_->readIQ(iq);
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        double rms = 0;
        for (auto c : iq) rms += std::norm(c);
        rms = 10 * std::log10(rms / iq.size() + 1e-10);
        if (rms > peakDb) { peakDb = rms; peakFreq = f; }
    }
    if (peakFreqHzOut) *peakFreqHzOut = peakFreq;
    return peakDb;
}

// One-shot synchronous capture for frequency calibration. Takes the SAME lock
// scanBand() uses so the engine read-loop never interleaves a block into the
// returned window. When tuneHz >= 0 the ACTIVE source is first parked there
// (a no-op on the offline file source, which already streams a fixed capture),
// then readIQ is pulled in chunks until `out` holds ~sampleCount complex
// samples -- or the source honestly stops delivering data. sampleRateHzOut /
// centreHzOut report the source's nominal rate and current centre for the ppm
// denominator; on a test / offline source these are the honestly-labelled
// generated-data values, never a fabricated device capture.
std::size_t SpectrumEngine::captureForCalibration(
        double tuneHz, int sampleCount,
        std::vector<std::complex<float>>& out,
        double& sampleRateHzOut, double& centreHzOut) {
    QMutexLocker lk(&sourceMutex_);
    out.clear();
    sampleRateHzOut = 0.0;
    centreHzOut = 0.0;
    if (!source_) return 0;

    if (tuneHz >= 0.0) source_->setCenterFreq(tuneHz);

    // Chunked pull: readIQ fills exactly out.size() for live sources (blocks
    // until ready) and loops the offline file on EOF, so a short read only
    // happens when the source genuinely stops. 8192 is a small, cache-friendly
    // block that keeps the lock hold-time modest.
    constexpr std::size_t kChunk = 8192;
    const std::size_t target =
        sampleCount > 0 ? static_cast<std::size_t>(sampleCount) : 0;
    std::size_t gotTotal = 0;
    while (gotTotal < target) {
        const std::size_t want = std::min<std::size_t>(kChunk, target - gotTotal);
        std::vector<std::complex<float>> block(want);
        const std::size_t got = source_->readIQ(block);
        if (got == 0) break;   // honest EOF / source has no more data
        if (got < want) block.resize(got);
        out.insert(out.end(), block.begin(), block.end());
        gotTotal += got;
    }

    sampleRateHzOut = source_->sampleRate();
    centreHzOut = source_->centerFreq();
    return gotTotal;
}

// One-shot user-initiated IQ export. Mirrors captureForCalibration's proven
// locked chunked pull, but ALSO reads the source gain/hw readback under the SAME
// lock (so the SigMF sidecar carries real values, never a fabricated 0), then
// writes the captured window to its own local Recorder (the in-flight continuous
// recorder_ is never touched). All file IO happens AFTER the lock is released so
// the DSP read-loop is not blocked on disk.
bool SpectrumEngine::exportIqSegment(int sampleCount, double tuneHz,
                                     QString& pathOut, double& sampleRateHzOut,
                                     double& centerHzOut, qint64& samplesOut,
                                     qint64& sizeBytesOut, QString& errorOut) {
    pathOut.clear();
    samplesOut = 0;
    sizeBytesOut = 0;
    sampleRateHzOut = 0.0;
    centerHzOut = 0.0;

    // Honest request bounds: tiny captures are useless, huge ones would stall
    // the read loop under the lock. Clamp rather than refuse.
    if (sampleCount < 1024) sampleCount = 1024;
    if (sampleCount > 16 * 1024 * 1024) sampleCount = 16 * 1024 * 1024;

    std::vector<std::complex<float>> iq;
    double gainDb = 0.0;
    QString hardware;
    {
        QMutexLocker lk(&sourceMutex_);
        if (!source_ || !hasDataLocked()) {
            errorOut = QString::fromUtf8("无 IQ 数据可导出：源未运行、无硬件且未打开离线文件");
            return false;
        }
        if (tuneHz >= 0.0) source_->setCenterFreq(tuneHz);

        constexpr std::size_t kChunk = 8192;
        const std::size_t target = static_cast<std::size_t>(sampleCount);
        std::size_t gotTotal = 0;
        while (gotTotal < target) {
            const std::size_t want = std::min<std::size_t>(kChunk, target - gotTotal);
            std::vector<std::complex<float>> block(want);
            const std::size_t got = source_->readIQ(block);
            if (got == 0) break;   // honest EOF / source has no more data
            if (got < want) block.resize(got);
            iq.insert(iq.end(), block.begin(), block.end());
            gotTotal += got;
        }
        sampleRateHzOut = source_->sampleRate();
        centerHzOut     = source_->centerFreq();
        gainDb          = source_->gain();
        hardware        = source_->name();
    }

    if (iq.empty()) {
        errorOut = QString::fromUtf8("源未产出任何 IQ 样本（无可导出数据）");
        return false;
    }

    // Build a collision-free base path under the recording dir, mirroring the
    // continuous Recorder's second-resolution disambiguation so an export never
    // truncates a sibling capture.
    QDir().mkpath(recDir_);
    const QString stamp = QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss");
    QString base = QStringLiteral("%1/export_%2_%3Hz")
                       .arg(recDir_, stamp)
                       .arg(static_cast<qint64>(centerHzOut));
    int n = 2;
    while (QFile::exists(base + QStringLiteral(".sigmf-data")) && n < 10000) {
        base = QStringLiteral("%1_%2").arg(base).arg(n++);
    }

    // Local, dedicated recorder: this NEVER disturbs an in-flight recorder_ /
    // wavWriter_ continuous capture. startWithBase opens the file, writeIQ dumps
    // the captured window, stop() patches num_samples and writes the sidecar.
    Recorder rec;
    if (!rec.startWithBase(base, sampleRateHzOut, centerHzOut, gainDb, hardware)) {
        errorOut = QString::fromUtf8("导出文件创建失败（无法打开输出文件）");
        return false;
    }
    rec.writeIQ(iq);
    rec.stop();

    pathOut = rec.currentFilePath();
    samplesOut = static_cast<qint64>(iq.size());
    sizeBytesOut = QFileInfo(pathOut).size();
    return true;
}

bool SpectrumEngine::tryConnectRtl() {
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->stop();
    auto rtl = std::make_unique<RtlSdrSource>();
    // Replay cached front-end options onto the fresh device BEFORE start().
    // start() applies stored members to the hardware in the correct order.
    rtl->setDirectSampling(cachedDirectSampling_);
    rtl->setOffsetTuning(cachedOffsetTuning_);
    rtl->setRtlAgc(cachedRtlAgc_);
    rtl->setTunerAgc(cachedTunerAgc_);
    rtl->setBiasTee(cachedBiasTee_);
    rtl->setPpm(cachedPpm_);
    if (rtl->start()) {
        source_ = std::move(rtl);
        reconnectPending_ = false;   // manual action cancels any retry
        updateCapsSnapshotLocked();
        emit sourceChanged("RTL-SDR", true);
        return true;
    }
    // Honest failure: no automatic synthetic fallback. Install the idle source
    // (synthetic only if the caller explicitly opted in, else empty NullSource).
    installIdleSourceLocked();
    emit sourceError(QStringLiteral("RTL-SDR 设备打开失败：未检测到硬件"));
    emit sourceChanged(source_->name(), false);
    return false;
}

void SpectrumEngine::disconnectSource() {
    QMutexLocker lk(&sourceMutex_);
    // Manual disconnect -> idle source (synthetic only if explicitly opted in,
    // else honest empty). No automatic synthetic fallback.
    installIdleSourceLocked();
    // A manual disconnect is an explicit user action: cancel any pending
    // auto-reconnect so the device does not silently re-attach later.
    tcpHost_.clear();
    tcpPort_ = 0;
    emit sourceChanged(source_->name(), false);
}

bool SpectrumEngine::connectRtlTcp(const QString& host, quint16 port) {
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->stop();
    auto tcp = std::make_unique<RtlTcpSource>(host, port);
    if (tcp->start()) {
        source_ = std::move(tcp);
        tcpHost_ = host;
        tcpPort_ = port;
        reconnectPending_ = false;   // manual action cancels any retry
        realSourceActive_ = true;
        updateCapsSnapshotLocked();
        emit sourceChanged(QString("rtl_tcp %1:%2").arg(host).arg(port), true);
        return true;
    }
    // Honest failure: no automatic synthetic fallback. Report the REAL socket
    // reason (refused / timeout / ...) and install the idle source (synthetic
    // only if explicitly opted in, else honest empty).
    const QString reason = tcp->lastError();
    installIdleSourceLocked();
    emit sourceError(reason.isEmpty()
        ? QStringLiteral("rtl_tcp 连接失败") : reason);
    emit sourceChanged(source_->name(), false);
    return false;
}

// Read a WAV's sibling sidecar .json for center_freq/mode (offline, no ui dep).
static void readWavSidecarMeta(const QString& wavPath, double& centerHz,
                               QString& mode) {
    QString jp = wavPath;
    jp.chop(4);
    jp += ".json";
    QFile jf(jp);
    if (!jf.open(QIODevice::ReadOnly)) return;
    QJsonParseError pe;
    QJsonDocument doc = QJsonDocument::fromJson(jf.readAll(), &pe);
    if (pe.error != QJsonParseError::NoError || !doc.isObject()) return;
    const QJsonObject o = doc.object();
    const double cf = o.value("center_freq").toDouble(-1.0);
    const double ch = o.value("frequency").toDouble(-1.0);
    if (ch > 0.0) centerHz = ch; else if (cf > 0.0) centerHz = cf;
    mode = o.value("mode").toString();
}

bool SpectrumEngine::openOfflineFile(const QString& path, double rawSampleRateHz) {
    QMutexLocker lk(&sourceMutex_);
    if (source_) source_->stop();

    auto fs = std::make_unique<FileSource>(QString());
    bool ok = false;
    QString p = path;
    if (p.endsWith(".sigmf-data", Qt::CaseInsensitive)) {
        p.chop(QString(".sigmf-data").size());
        ok = fs->openSigmf(p);
    } else if (p.endsWith(".sigmf-meta", Qt::CaseInsensitive)) {
        p.chop(QString(".sigmf-meta").size());
        ok = fs->openSigmf(p);
    } else if (p.endsWith(".wav", Qt::CaseInsensitive)) {
        double center = 0.0;
        QString mode;
        readWavSidecarMeta(p, center, mode);
        ok = fs->openWav(p, center, mode);
    } else {
        // Raw cf32_le: sample rate must be supplied by the user.
        ok = fs->openRaw(p, rawSampleRateHz);
    }

    if (ok) ok = fs->start();

    if (ok) {
        source_ = std::move(fs);
        realSourceActive_ = false;     // offline file is NOT hardware
        reconnectPending_ = false;
        updateCapsSnapshotLocked();
        needDemodReset_.store(true);   // reconfigure channelizer/decimator
        emit sourceChanged(source_->name(), false);
        return true;
    }

    // Honest failure: no automatic synthetic fallback. Report the real reason
    // and install the idle source (synthetic only if explicitly opted in, else
    // honest empty).
    const QString why = fs->errorString();
    installIdleSourceLocked();
    emit sourceError(why.isEmpty() ? QStringLiteral("文件打开失败") : why);
    emit sourceChanged(source_->name(), false);
    return false;
}

bool SpectrumEngine::isOfflineFileActive() const {
  return dynamic_cast<FileSource*>(source_.get()) != nullptr;
}

void SpectrumEngine::setOfflinePaused(bool paused) {
    QMutexLocker lk(&sourceMutex_);
    if (auto* fs = dynamic_cast<FileSource*>(source_.get())) fs->setPaused(paused);
}

void SpectrumEngine::seekOfflineFraction(double f01) {
    QMutexLocker lk(&sourceMutex_);
    if (auto* fs = dynamic_cast<FileSource*>(source_.get())) fs->seekFraction(f01);
}

bool SpectrumEngine::offlinePosition(double& curSec, double& totalSec) {
    QMutexLocker lk(&sourceMutex_);
    auto* fs = dynamic_cast<FileSource*>(source_.get());
    if (!fs || fs->sampleRate() <= 0) return false;
    curSec = fs->playedSamples() / fs->sampleRate();
    totalSec = fs->totalSamples() / fs->sampleRate();
    return true;
}


// Runs on the engine's home thread (queued from reconnectRequested, or a
// QTimer retry). Blocking connect (worst case 2 s) is acceptable while the
// device is absent; manual source operations cancel reconnectPending_ so a
// retry already queued just no-ops.
void SpectrumEngine::handleReconnectRequested() {
    if (!reconnectPending_ || !autoReconnect_.load()) return;
    const bool wasPending = reconnectPending_;
    const bool ok = connectRtlTcp(tcpHost_, tcpPort_);
    // connectRtlTcp carries manual semantics and clears reconnectPending_ on
    // failure; as an auto-reconnect we want the retry chain to continue.
    if (!ok && wasPending && autoReconnect_.load())
        reconnectPending_ = true;
    if (reconnectPending_ && autoReconnect_.load()) {
        // Failed (device still absent): try again after the throttle window.
        QTimer::singleShot(kReconnectIntervalMs, this,
                           &SpectrumEngine::handleReconnectRequested);
    }
}

// Called with sourceMutex_ held (from the run() loop). Swaps the dead device
// out for the offline test source and reports the drop as an event. Note: no
// isConnected() guard here -- after an RST the socket reads as unconnected
// while the device is in fact gone, and this is exactly the state we must
// fall back from.
void SpectrumEngine::dropSourceLocked() {
    if (!source_) return;
    // Honest drop: NO automatic synthetic fallback. Install the idle source
    // (synthetic TestSignalSource only if the caller explicitly opted in, else
    // the empty NullSource). Note: no isConnected() guard here -- after an RST
    // the socket reads as unconnected while the device is in fact gone, and
    // this is exactly the state we must recover from.
    installIdleSourceLocked();
    if (autoReconnect_.load() && !tcpHost_.isEmpty() && tcpPort_ != 0) {
        reconnectPending_ = true;
        lastReconnectMs_ = reconnectClock_.elapsed();
    }
    emit sourceDropped();
    emit sourceChanged(source_->name(), false);
}

// Outside sourceMutex_ no longer needed: the retry loop lives in run().
// Kept as a no-op stub is worse than nothing, so it was removed; the
// declaration is gone from the header as well.

bool SpectrumEngine::isTestSignalActive() const {
    QMutexLocker lk(&const_cast<QMutex&>(sourceMutex_));
    // True only when the synthetic TestSignalSource is actually the active
    // source. On real hardware OR the honest empty NullSource this is false --
    // a no-hardware idle launch is NOT mislabeled as "test signal".
    return dynamic_cast<TestSignalSource*>(source_.get()) != nullptr;
}

bool SpectrumEngine::hasRealSource() const {
    QMutexLocker lk(&const_cast<QMutex&>(sourceMutex_));
    return source_ && source_->isConnected();
}

bool SpectrumEngine::isSynthetic() const {
    QMutexLocker lk(&const_cast<QMutex&>(sourceMutex_));
    return dynamic_cast<TestSignalSource*>(source_.get()) != nullptr;
}

void SpectrumEngine::installIdleSourceLocked() {
    // Caller holds sourceMutex_. After a real source is gone (open failed,
    // dropped, or manually disconnected) install the idle source: the explicitly
    // opted-in synthetic TestSignalSource, or -- by default -- the honest empty
    // NullSource that produces no IQ. Never silently synthesizes. Both report the
    // same nominal 2.4 MHz / 98.5 MHz so no channelizer rebuild is needed.
    if (source_) source_->stop();
    if (testSourceEnabled_.load()) {
        source_ = std::make_unique<TestSignalSource>();
    } else {
        source_ = std::make_unique<NullSource>();
    }
    source_->start();
    realSourceActive_ = false;
    reconnectPending_ = false;
    updateCapsSnapshotLocked();
}

void SpectrumEngine::setTestSourceEnabled(bool on) {
    QMutexLocker lk(&sourceMutex_);
    testSourceEnabled_.store(on);
    // Only materialize / tear down the synthetic source while we are on the idle
    // source (NullSource empty <-> TestSignalSource). Enabling must never kick a
    // live real device or an opened offline file; disabling only drops back to
    // the honest empty state.
    if (on && (!source_ || dynamic_cast<NullSource*>(source_.get()))) {
        if (source_) source_->stop();
        auto ts = std::make_unique<TestSignalSource>();
        ts->start();
        source_ = std::move(ts);
        realSourceActive_ = false;
        reconnectPending_ = false;
        updateCapsSnapshotLocked();
        emit sourceChanged(source_->name(), false);
    } else if (!on && dynamic_cast<TestSignalSource*>(source_.get())) {
        source_->stop();
        source_ = std::make_unique<NullSource>();
        source_->start();
        realSourceActive_ = false;
        reconnectPending_ = false;
        updateCapsSnapshotLocked();
        emit sourceChanged(source_->name(), false);
    }
}

void SpectrumEngine::setMuted(bool m) {
    if (audioSink_) audioSink_->setMuted(m);
    if (networkTap_) networkTap_->setMuted(m);
}

void SpectrumEngine::setTestAudioSink(std::unique_ptr<IAudioSink> sink) {
    // Swap the DSP write path. The settings-dialog handle (audioOut_) and its
    // Qt device hot-swap are untouched; only where run() writes demod audio
    // changes. nullptr restores the real device sink.
    testSink_ = std::move(sink);
    audioSink_ = testSink_ ? testSink_.get() : static_cast<IAudioSink*>(audioOut_);
}

void SpectrumEngine::setNetworkAudioSink(std::unique_ptr<IAudioSink> tap) {
    // Parallel branch: the production write path above is untouched -- the tap
    // simply receives the same frames in run(). The DSP loop reads networkTap_
    // with no lock; swap only happens from the UI/test thread between blocks.
    networkTap_ = std::move(tap);
}

void SpectrumEngine::setBandwidth(double hz) {
    {
    QMutexLocker lk(&ctrlMutex_);
    pending_.bandwidthHz = hz;
    pending_.dBandwidth = true;
    }
    applyIfIdle();
}

QString SpectrumEngine::expandRecTemplate() const {
    // Called with sourceMutex_ held (source_/VFO state stable).
    const QString stamp = QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss");
    // Tag the recording with the SELECTED VFO's absolute frequency + mode.
    double freqHz = source_ ? source_->centerFreq() : 0.0;
    QString mode = demodMode_;
    if (const VfoChannel* sel = vfoManager_.selected()) {
        freqHz = sel->freqHz;
        mode = sel->mode;
    }
    const double freqMhz = freqHz / 1e6;
    const QString freqStr = QString::number(freqMhz, 'f', 3);
    return QString(recTemplate_)
        .replace("{time}", stamp)
        .replace("{freq}", freqStr)
        .replace("{mode}", mode);
}

bool SpectrumEngine::hasDataLocked() const {
    // A live producer exists iff source_ yields IQ: real hardware (connected),
    // the explicitly-enabled synthetic test signal, or an opened offline capture
    // file. The honest empty NullSource yields nothing -> false. Called with
    // sourceMutex_ held; the public hasData() takes the lock and calls this.
    if (!source_) return false;
    if (source_->isConnected()) return true;                        // real HW
    if (dynamic_cast<TestSignalSource*>(source_.get())) return true;  // synthetic
    if (dynamic_cast<FileSource*>(source_.get())) return true;        // offline file
    return false;
}

bool SpectrumEngine::hasData() const {
    QMutexLocker lk(&const_cast<QMutex&>(sourceMutex_));
    return hasDataLocked();
}

void SpectrumEngine::setRecMaxSegmentSeconds(double secs) {
    recMaxSegSecs_ = secs;
    QSettings("MBDSDR", "MBDSDR").setValue("rec/max_seg_s", secs);
}

bool SpectrumEngine::startRecording() {
    if (recorder_.isRecording() || wavWriter_.isRecording()) return false;
    QMutexLocker lk(&sourceMutex_);
    if (!hasDataLocked()) return false;

    QDir().mkpath(recDir_);
    const QString base = recDir_ + QLatin1Char('/') + expandRecTemplate();

    if (recTarget_ == RecTarget::BasebandIQ) {
        // Arm auto-segmentation (tokens/settings value, never a hard-coded magic
        // number here) before opening the first capture file.
        recorder_.setMaxSegmentSeconds(recMaxSegSecs_);
        if (!recorder_.startWithBase(base, source_->sampleRate(),
                                     source_->centerFreq(), source_->gain(),
                                     source_->name())) {
            return false;
        }
        recCurrentPath_ = recorder_.currentFilePath();
        emit recordingStateChanged(true, recCurrentPath_);
    } else {
        const int channels = recStereo_ ? 2 : 1;
        if (!wavWriter_.start(base + ".wav", 48000.0, channels)) {
            return false;
        }
        recCurrentPath_ = wavWriter_.currentFilePath();
        // Capture the readback metadata for the sidecar JSON written at stop.
        // center/gain come from the live source (hardware actual values), not the
        // UI requests. mode = selected VFO demod.
        wavSidecarPath_ = base + ".json";
        wavSidecarCenterHz_ = source_->centerFreq();
        wavSidecarGainDb_ = source_->gain();
        wavSidecarHardware_ = source_->name();
        wavSidecarMode_ = demodMode_;
        emit recordingStateChanged(true, recCurrentPath_);
    }
    // Start the wall-clock used for the 1 Hz REC progress tick.
    recClock_.restart();
    lastRecSecond_ = -1;
    return true;
}

void SpectrumEngine::stopRecording() {
    if (recorder_.isRecording()) {
        const QString path = recorder_.currentFilePath();
        recorder_.stop();
        emit recordingStateChanged(false, path);
    } else if (wavWriter_.isRecording()) {
        const QString path = wavWriter_.currentFilePath();
        wavWriter_.stop();
        // Write the sidecar metadata JSON now that the WAV is finalized. Fields
        // mirror the SigMF recorder so demod-audio captures carry the same proof.
        if (!wavSidecarPath_.isEmpty()) {
            QJsonObject meta;
            meta["type"] = "mbdsdr-audio-recording";
            meta["sample_rate"] = 48000.0;
            meta["center_freq"] = wavSidecarCenterHz_;
            meta["gain"] = wavSidecarGainDb_;
            meta["hardware"] = wavSidecarHardware_;
            meta["mode"] = wavSidecarMode_;
            meta["datetime"] = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
            QFile f(wavSidecarPath_);
            if (f.open(QIODevice::WriteOnly)) {
                f.write(QJsonDocument(meta).toJson(QJsonDocument::Indented));
            }
            wavSidecarPath_.clear();
        }
        emit recordingStateChanged(false, path);
    }
}
void SpectrumEngine::setGatedRecordingEnabled(bool e) {
    gatedEnabled_ = e;
    // The single gated recorder is active when either mode is armed.
    gatedRec_.setEnabled(gatedEnabled_ || watchEnabled_.load());
}

void SpectrumEngine::setWatchEnabled(bool e) {
    if (e == watchEnabled_.load()) return;
    watchEnabled_.store(e);
    if (!e) watch_.reset();
    gatedRec_.setEnabled(gatedEnabled_ || e);
    emit watchStateChanged(e, gatedRec_.isRecording(),
                           gatedRec_.segmentCount());
}

void SpectrumEngine::setRecordingDir(const QString& dir) {
    QString d = dir;
    while (d.size() > 1 && d.endsWith(QLatin1Char('/'))) d.chop(1);
    if (d.isEmpty()) d = QStringLiteral("record");
    recDir_ = d;
    gatedRec_.setOutputDir(recDir_);
    QSettings rs("MBDSDR", "MBDSDR");
    rs.setValue("rec/dir", recDir_);
}

// ---- Multi-VFO management --------------------------------------------------
void SpectrumEngine::vfoAdd() {
    QMutexLocker lk(&sourceMutex_);
    const double center = source_ ? source_->centerFreq() : 0.0;
    const int id = vfoManager_.addVfo(center);
    if (const VfoChannel* sel = vfoManager_.selected()) {
        demodMode_ = sel->mode;
        bandwidth_ = sel->bandwidthHz;
    }
    emit vfoListChanged();
    (void)id;
}

void SpectrumEngine::vfoRemove(int id) {
    QMutexLocker lk(&sourceMutex_);
    vfoManager_.removeVfo(id);
    if (const VfoChannel* sel = vfoManager_.selected()) {
        demodMode_ = sel->mode;
        bandwidth_ = sel->bandwidthHz;
    }
    emit vfoListChanged();
}

void SpectrumEngine::vfoSelect(int id) {
    QMutexLocker lk(&sourceMutex_);
    if (vfoManager_.selectVfo(id)) {
        if (const VfoChannel* sel = vfoManager_.selected()) {
            demodMode_ = sel->mode;
            bandwidth_ = sel->bandwidthHz;
        }
        emit vfoListChanged();
    }
}

void SpectrumEngine::vfoSetFreq(int id, double hz) {
    QMutexLocker lk(&sourceMutex_);
    vfoManager_.setFreq(id, hz);
    emit vfoListChanged();
}

bool SpectrumEngine::vfoSetOffset(int id, double targetHz) {
    QMutexLocker lk(&sourceMutex_);
    if (!source_) return false;
    const double center = source_->centerFreq();
    const double sr = source_->sampleRate();
    // Usable half-bandwidth: central fraction of the capture before the tuner's
    // edge roll-off. In-band moves stay inside +/-this and never touch the tuner.
    const double usableHalf = sr * 0.5 * kVfoEdgeFraction;

    bool retuned = false;
    if (std::abs(targetHz - center) > usableHalf) {
        // The target would fall outside the usable capture band. Genuinely move
        // the source tuner to the target so this VFO lands back near offset 0.
        // Every OTHER VFO keeps its absolute freqHz; their channelizer NCO
        // offsets (= freqHz - new center) are recomputed continuously by
        // VfoManager::process() on the next run() frame.
        source_->setCenterFreq(targetHz);
        retuned = true;
    }
    // In-band move (or just after a retune): only record this VFO's absolute
    // frequency. The channelizer NCO offset is applied by process() as
    // (freqHz - sourceCenterHz). This is the SDR++ in-band IF-offset path: the
    // RTL tuner is NOT retuned while the VFO stays inside the capture band.
    vfoManager_.setFreq(id, targetHz);
    emit vfoListChanged();
    return retuned;
}

void SpectrumEngine::vfoSetBandwidth(int id, double hz) {
    QMutexLocker lk(&sourceMutex_);
    vfoManager_.setBandwidth(id, hz);
    if (vfoManager_.selectedId() == id) bandwidth_ = hz;
    emit vfoListChanged();
}

void SpectrumEngine::vfoSetMode(int id, const QString& mode) {
    QMutexLocker lk(&sourceMutex_);
    vfoManager_.setMode(id, mode);
    if (vfoManager_.selectedId() == id) {
        demodMode_ = mode;
        if (const VfoChannel* sel = vfoManager_.selected()) bandwidth_ = sel->bandwidthHz;
    }
    emit vfoListChanged();
}

void SpectrumEngine::vfoSetColor(int id, const QColor& c) {
    QMutexLocker lk(&sourceMutex_);
    vfoManager_.setColor(id, c);
    emit vfoListChanged();
}

bool SpectrumEngine::vfoRename(int id, const QString& name) {
    QMutexLocker lk(&sourceMutex_);
    const bool ok = vfoManager_.renameVfo(id, name);
    if (ok) emit vfoListChanged();
    return ok;
}

void SpectrumEngine::setAnrEnabled(bool on) {
    QMutexLocker lk(&sourceMutex_);
    anr_.setEnabled(on);
}

void SpectrumEngine::setAnrStrength(float s) {
    QMutexLocker lk(&sourceMutex_);
    anr_.setStrength(s);
}

void SpectrumEngine::setForceMono(bool on) {
    QMutexLocker lk(&sourceMutex_);
    forceMono_ = on;
    // Apply immediately to the live WFM channel; the run loop re-applies the
    // flag every block so a (re)built WFM channel inherits it too.
    if (VfoChannel* sel = vfoManager_.selected())
        if (sel->stereo) sel->stereo->setForceMono(on);
}

void SpectrumEngine::setTestFmStereo(bool on) {
    QMutexLocker lk(&sourceMutex_);
    testFmStereo_ = on;
}

void SpectrumEngine::resetAptDecoder() {
    QMutexLocker lk(&sourceMutex_);
    aptDecoder_.reset();
    aptLastRows_ = 0;
    aptLastLocked_ = false;
    // Push a cleared frame so the panel drops the old image immediately.
    emit aptImageReady(aptDecoder_.image(), false, 0, 0.0);
}

QVector<VfoMarker> SpectrumEngine::vfoMarkers() const {
    QMutexLocker lk(&const_cast<QMutex&>(sourceMutex_));
    QVector<VfoMarker> out = vfoManager_.markers();
    // Backfill the IF-offset bookkeeping against the CURRENT source capture
    // center. This is the snapshot the UI draws: referenceHz = capture center,
    // centerOffsetHz = this VFO's signed IF offset (freqHz - capture center).
    const double center = source_ ? source_->centerFreq() : 0.0;
    for (auto& m : out) {
        m.referenceHz = center;
        m.centerOffsetHz = m.freqHz - center;
    }
    return out;
}

int SpectrumEngine::selectedVfoId() const {
    QMutexLocker lk(&const_cast<QMutex&>(sourceMutex_));
    return vfoManager_.selectedId();
}

std::vector<PocsagMessage> SpectrumEngine::pocsagMessages(int channelId) const {
    QMutexLocker lk(&const_cast<QMutex&>(sourceMutex_));
    return vfoManager_.pocsagMessages(channelId);
}

std::vector<M17Call> SpectrumEngine::m17Calls(int channelId) const {
    QMutexLocker lk(&const_cast<QMutex&>(sourceMutex_));
    return vfoManager_.m17Calls(channelId);
}

VorResult SpectrumEngine::vorResult(int channelId) const {
    QMutexLocker lk(&const_cast<QMutex&>(sourceMutex_));
    return vfoManager_.vorResult(channelId);
}

DigitalLockStatus SpectrumEngine::digitalLockStatus() const {
    QMutexLocker lk(&const_cast<QMutex&>(sourceMutex_));
    return vfoManager_.digitalLockStatus();
}

void SpectrumEngine::clearDigitalOutputs(int channelId) {
    QMutexLocker lk(&sourceMutex_);
    vfoManager_.clearDigitalOutputs(channelId);
}

void SpectrumEngine::setDirectSampling(int mode) {
    {
    QMutexLocker lk(&ctrlMutex_);
    pending_.directSampling = mode;
    pending_.dDirectSampling = true;
    }
    applyIfIdle();
}
void SpectrumEngine::setOffsetTuning(bool on) {
    {
    QMutexLocker lk(&ctrlMutex_);
    pending_.offsetTuning = on;
    pending_.dOffsetTuning = true;
    }
    applyIfIdle();
}
void SpectrumEngine::setRtlAgc(bool on) {
    {
    QMutexLocker lk(&ctrlMutex_);
    pending_.rtlAgc = on;
    pending_.dRtlAgc = true;
    }
    applyIfIdle();
}
void SpectrumEngine::setTunerAgc(bool on) {
    {
    QMutexLocker lk(&ctrlMutex_);
    pending_.tunerAgc = on;
    pending_.dTunerAgc = true;
    }
    applyIfIdle();
}
void SpectrumEngine::setBiasTee(bool on) {
    {
    QMutexLocker lk(&ctrlMutex_);
    pending_.biasTee = on;
    pending_.dBiasTee = true;
    }
    applyIfIdle();
}
void SpectrumEngine::setPpm(double ppm) {
    {
    QMutexLocker lk(&ctrlMutex_);
    pending_.ppm = ppm;
    pending_.dPpm = true;
    }
    applyIfIdle();
}

void SpectrumEngine::setWindowType(int w) {
    powerSpectrum_.setWindow(static_cast<PowerSpectrum::Window>(
        std::clamp(w, 0, 2)));
}
void SpectrumEngine::setAverageMode(int a) {
    powerSpectrum_.setAverage(static_cast<PowerSpectrum::Average>(
        std::clamp(a, 0, 2)));
}
void SpectrumEngine::setNoiseBlanker(bool on) {
    noiseBlanker_.setEnabled(on);
}

void SpectrumEngine::setFrontendDecimation(int D) {
    const int d = std::clamp(D, 1, tokens::kDecimMaxFactor);
    frontendDecimation_.store(d);
    frontendDecimConfiguredSr_ = 0.0;   // force reconfigure on next loop
}

bool SpectrumEngine::noiseBlankerEnabled() const { return noiseBlanker_.enabled(); }
int SpectrumEngine::windowType() const { return static_cast<int>(powerSpectrum_.window()); }
int SpectrumEngine::averageMode() const { return static_cast<int>(powerSpectrum_.average()); }

void SpectrumEngine::applyIfIdle() {
    // The loop flag running_ defaults true even before QThread::start() launches
    // run(); gate on the ACTUAL thread state. When the thread is live the run()
    // loop drains asynchronously (keeps USB tuning off the caller's thread);
    // when it has not been started (headless tests) drain on this thread so
    // set-then-readback stays consistent (the legacy synchronous behavior).
    if (QThread::isRunning()) return;
    QMutexLocker lk(&sourceMutex_);
    applyControlCommandsLocked();
}

// Drains the asynchronous control mailbox and applies every command ON THE
// ENGINE THREAD. Called from run() while sourceMutex_ is held, so the device
// and all shared engine state are touched here -- never on a caller's thread.
void SpectrumEngine::applyControlCommandsLocked() {
    PendingControls p;
    {
        QMutexLocker lk(&ctrlMutex_);
        if (!pending_.any()) return;
        p = pending_;
        pending_ = PendingControls{};
    }

    bool vfoChanged = false;

    if (p.dCenterFreq) {
        if (source_) source_->setCenterFreq(p.centerFreqHz);
        // Tuning moves the SELECTED VFO with it (kept at offset 0), like the
        // legacy single-channel receiver; other VFOs keep absolute frequencies.
        if (VfoChannel* sel = vfoManager_.selected()) sel->freqHz = p.centerFreqHz;
        vfoChanged = true;
    }
    if (p.dSampleRate) {
        if (source_) source_->setSampleRate(p.sampleRateHz);
        vfoManager_.sourceRateChanged();
        needDemodReset_.store(true);
    }
    if (p.dGain) {
        if (source_) source_->setGain(p.gainDb);
    }
    if (p.dDemodMode) {
        vfoManager_.setMode(vfoManager_.selectedId(), p.demodMode);
        if (const VfoChannel* sel = vfoManager_.selected()) {
            demodMode_ = sel->mode;
            bandwidth_ = sel->bandwidthHz;
        }
        needDemodReset_.store(true);
        vfoChanged = true;
    }
    if (p.dBandwidth) {
        bandwidth_ = p.bandwidthHz;
        vfoManager_.setBandwidth(vfoManager_.selectedId(), p.bandwidthHz);
        // The channel filter cutoff depends on bandwidth; rebuild on next loop.
        needDemodReset_.store(true);
        vfoChanged = true;
    }
    if (p.dDirectSampling) {
        cachedDirectSampling_ = p.directSampling;
        if (source_) source_->setDirectSampling(p.directSampling);
    }
    if (p.dOffsetTuning) {
        cachedOffsetTuning_ = p.offsetTuning;
        if (source_) source_->setOffsetTuning(p.offsetTuning);
    }
    if (p.dRtlAgc) {
        cachedRtlAgc_ = p.rtlAgc;
        if (source_) source_->setRtlAgc(p.rtlAgc);
    }
    if (p.dTunerAgc) {
        cachedTunerAgc_ = p.tunerAgc;
        if (source_) source_->setTunerAgc(p.tunerAgc);
    }
    if (p.dBiasTee) {
        cachedBiasTee_ = p.biasTee;
        if (source_) source_->setBiasTee(p.biasTee);
    }
    if (p.dPpm) {
        cachedPpm_ = p.ppm;
        if (source_) source_->setPpm(p.ppm);
    }

    if (vfoChanged) emit vfoListChanged();
}

void SpectrumEngine::run() {
    std::vector<std::complex<float>> iq;
    iq.resize(static_cast<std::size_t>(fftSize_.load()));

    // Connections now exist on the UI side; report the real source state.
    {
        QMutexLocker lk(&sourceMutex_);
        emit sourceChanged(source_->name(), source_->isConnected());
    }

    while (running_.load()) {
        int n = fftSize_.load();

        QMutexLocker lk(&sourceMutex_);

        // Apply queued set commands on THIS (engine) thread before touching the
        // device, so GUI/headless callers never block on USB tuning.
        applyControlCommandsLocked();

        if (needDemodReset_.load()) { rebuildDemod(); needDemodReset_.store(false); }

        const bool real = source_->isConnected();
        const double sr = source_->sampleRate();
        const int D = std::max(1, vfoManager_.maxDecimation());

        // Real sources: blocking reads pace to wall-clock. Synthetic sources have
        // no hardware clock, but we still read ~25 ms of simulated time per loop
        // (aligned to a whole number of decimation branches) so the offline
        // demod chain advances at near wall-clock speed and loops like the
        // Costas/timing PLL converge within a few seconds instead of needing
        // tens of seconds of wall-clock for ~1.3 s of simulated audio. The 33 ms
        // sleep below still paces the loop. The spectrum FFT always uses only
        // the first n samples (unchanged).
        std::size_t wantN;
        {
            long want = static_cast<long>(std::round(sr * 0.025));
            want = ((want + D - 1) / D) * D;
            wantN = static_cast<std::size_t>(std::max<long>(want, n));
        }        if (iq.size() != wantN) iq.resize(wantN);

        std::size_t got = source_->readIQ(iq);
        if (got == 0) {
            if (real || realSourceActive_) {
                // A real device that stops delivering IQ for the grace period
                // is dropped (unplugged / link lost). Honest fallback + event.
                if (++zeroReadFrames_ >= kMaxZeroReadBeforeDrop) {
                    dropSourceLocked();
                    zeroReadFrames_ = 0;
                    // Reconnect runs on the engine's home (UI) thread via a
                    // queued signal: QTcpSocket must live in a thread with an
                    // event loop, which the run() loop is not.
                    if (reconnectPending_ && autoReconnect_.load())
                        emit reconnectRequested();
                }
            } else {
                zeroReadFrames_ = 0;
            }
            lk.unlock();
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
            continue;
        }
        zeroReadFrames_ = 0;
        if (got < iq.size()) iq.resize(got);

        // Sanitize: a buggy source / bad block can deliver NaN/Inf which would
        // poison every downstream log-magnitude and emit NaN frames to the UI.
        // Clamp non-finite samples to 0 instead of propagating them.
        for (auto& c : iq) {
            if (!std::isfinite(c.real()) || !std::isfinite(c.imag()))
                c = std::complex<float>(0.0f, 0.0f);
        }

        noiseBlanker_.process(iq);
        frontend_.process(iq);

        // Record raw IQ if recording
        if (recorder_.isRecording()) recorder_.writeIQ(iq);

        // SpyServer read-only tap: re-emit the SAME real block the recorder /
        // downstream chain see, but only while a remote client is streaming.
        // Queued copy to the UI thread (SpyServerServer lives there).
        if (iqTapRequested_.load())
            emit iqTapReady(iq, sr, source_->centerFreq());

        // ---- Frontend software decimation (real anti-alias low-pass + integer D)
        // D=1 leaves iq/sr untouched (zero behavior change). D>1 narrows the band
        // to +/-sr/(2D) and cuts wideband compute; the recorder/iqTap above already
        // captured the RAW block, so recorded IQ stays at native rate.
        double srEff = sr;
        {
            const int D = frontendDecimation_.load();
            if (D > 1) {
                if (frontendDecimConfiguredSr_ != sr) {
                    // Cutoff = decimated-Nyquist * kDecimLpfFrac to stop aliasing.
                    frontendDecim_.configure(sr, sr / D,
                                            (sr / D / 2.0) * tokens::kDecimLpfFrac);
                    frontendDecim_.setVfoOffsetHz(0.0);
                    frontendDecimConfiguredSr_ = sr;
                }
                auto dec = frontendDecim_.process(iq);
                iq.swap(dec);
                srEff = sr / D;
            }
        }

        // Spectrum (computed from an FFT-sized window of the block). A real
        // source may deliver a PARTIAL block at start-up / underflow, so round
        // the window DOWN to the largest power of two -- otherwise a non-pow2
        // size makes PowerSpectrum::process throw and terminate the engine.
        std::size_t specN = std::min<std::size_t>(n, iq.size());
        while (specN > 1 && (specN & (specN - 1)) != 0) --specN;
        SpectrumFrame frame;
        if (specN >= 2) {
            std::vector<std::complex<float>> spec(iq.begin(), iq.begin() + specN);
            frame.dbfs.resize(specN);
            powerSpectrum_.process(spec, frame.dbfs);
            frame.centerFreqHz = source_->centerFreq();
            frame.sampleRateHz = srEff;
            frame.fftSize = static_cast<int>(specN);
            frame.isTestSignal = !real;
            frame.sourceName = source_->name();
            emit spectrumReady(frame);
        }

        // Fan the source IQ out to every VFO channel (each with its own
        // channelizer + demod + resampler state), then take the SELECTED VFO's
        // 48 kHz audio into the shared downstream.
        const double centerNow = source_->centerFreq();
        // If frontend decimation changed the effective rate, rebuild the VFO
        // channelizers for it before fanning out. D=1 keeps srEff==sr so this is
        // a no-op and channels are untouched (zero regression path).
        if (srEff != lastEffSrForVfo_) {
            vfoManager_.sourceRateChanged();
            lastEffSrForVfo_ = srEff;
        }
        const std::vector<float>& raw = vfoManager_.process(iq, srEff, centerNow);
        const VfoChannel* sel = vfoManager_.selected();
        const QString selMode = sel ? sel->mode : demodMode_;
        const bool digital = VfoChannel::modeIsDigital(selMode);

        // ---- POCSAG / m17 / VOR snapshot change-diff (every block) ----------
        // Pull the SELECTED channel's digital read-out and emit ONLY when it
        // changed vs the last push (mirrors rdsUpdated). Reading off a non-
        // matching mode yields an honest empty snapshot, so leaving POCSAG/m17/
        // VOR pushes exactly one cleared edge -- the panel never keeps a stale
        // list / radial from another band.
        {
            const int selId = sel ? sel->id : -1;

            // POCSAG message list (append-only; reset on clear / mode switch).
            const std::vector<PocsagMessage> pmsgs =
                (selMode == "POCSAG" && selId >= 0)
                    ? vfoManager_.pocsagMessages(selId)
                    : std::vector<PocsagMessage>{};
            if (pmsgs.size() != lastPocsagCount_) {
                lastPocsagCount_ = pmsgs.size();
                emit pocsagMessagesChanged(pmsgs);
            }

            // m17 call list (append-only; reset on clear / mode switch).
            const std::vector<M17Call> mcalls =
                (selMode == "m17" && selId >= 0)
                    ? vfoManager_.m17Calls(selId)
                    : std::vector<M17Call>{};
            if (mcalls.size() != lastM17Count_) {
                lastM17Count_ = mcalls.size();
                emit m17CallsChanged(mcalls);
            }

            // VOR radial: emit on lock edge / radial move / Morse-ID change.
            const VorResult vres =
                (selMode == "VOR" && selId >= 0)
                    ? vfoManager_.vorResult(selId)
                    : VorResult{};
            const bool vorChanged =
                vres.locked != lastVorLocked_ ||
                (vres.locked &&
                 std::abs(vres.radialDeg - lastVorRadialDeg_) > 0.5) ||
                vres.morseId != lastVorMorseId_;
            if (vorChanged) {
                lastVorLocked_ = vres.locked;
                lastVorRadialDeg_ = vres.radialDeg;
                lastVorMorseId_ = vres.morseId;
                emit vorRadialChanged(vres);
            }
        }

        // Keep the offline test source emitting the right kind of IQ for the
        // selected VFO (am/fm/tone/bpsk/qpsk). Real hardware ignores this.
        if (auto* ts = dynamic_cast<TestSignalSource*>(source_.get())) {
            QString want;
            if (selMode == "AM") want = "am";
            else if (selMode == "NFM" || selMode == "WFM") want = "fm";
            else if (selMode == "BPSK") want = "bpsk";
            else if (selMode == "QPSK") want = "qpsk";
            else want = "tone";
            if (want != ts->modulation()) ts->setModulation(want);
            // *** TEST ONLY -- NOT HARDWARE *** opt-in synthetic FM-stereo MPX.
            ts->setFmStereo(testFmStereo_);
        }

        // RSSI is always reported from raw capture energy.
        double rssi = 0;
        for (auto c : iq) rssi += std::norm(c);
        rssi = 10 * std::log10(rssi / iq.size() + 1e-10);
        // Snapshot for the decoupled band-scan driver (rssiDbfs()). Only reachable
        // when a real block was read (got>0 above), so the honest empty source
        // leaves it at the -100 dBFS quiet default -- no fabricated busy level.
        rssiDbfs_.store(static_cast<float>(rssi));
        emit rssiLevel(static_cast<float>(rssi));

        // Real-RSSI trigger for the unattended watch. The block duration is
        // the selected VFO's 48 kHz audio block; detection runs on the REAL
        // capture energy, never on fabricated levels.
        watch_.setBlockMs(raw.size() * 1000.0 / 48000.0);
        const bool watchGate = watch_.update(static_cast<float>(rssi));

        // Real measured noise floor -> SNR. The median per-bin level of the power
        // spectrum is the noise density (peaks don't lift the median); Parseval
        // scales it to the total-power domain used by RSSI. A slow exponential
        // average tracks the floor so transients don't move it. All values come
        // from the real frame.dbfs / real IQ -- nothing fabricated.
        if (!frame.dbfs.empty()) {
            std::vector<float> sorted = frame.dbfs;
            std::sort(sorted.begin(), sorted.end());
            const float medDb = sorted[sorted.size() / 2];
            const double medLin = std::pow(10.0, medDb / 10.0);
            // Per-bin scaled power summed over all bins ~= total capture power.
            const double noiseTotalLin = medLin * static_cast<double>(sorted.size());
            const double noiseTotalDb = 10.0 * std::log10(noiseTotalLin + 1e-12);
            if (!noiseFloorInit_) {
                noiseFloorTrackDb_ = noiseTotalDb;
                noiseFloorInit_ = true;
            } else {
                noiseFloorTrackDb_ = 0.98 * noiseFloorTrackDb_ + 0.02 * noiseTotalDb;
            }
            emit snrLevel(static_cast<float>(rssi - noiseFloorTrackDb_));
            emit noiseFloorLevel(static_cast<float>(noiseFloorTrackDb_));
        }

        // ~1 Hz readback of the ACTUAL source state to the status bar. These are
        // the hardware readback values (gain is rounded by the driver), not the
        // UI spinbox requests. The test source reports connected=false and the UI
        // tags it "非硬件" -- never presented as real hardware.
        const qint64 nowMs = telemetryClock_.elapsed();
        if (lastTelemetryMs_ < 0 || nowMs - lastTelemetryMs_ >= 1000) {
            lastTelemetryMs_ = nowMs;
            // Refresh the UI-facing capability snapshot ~1 Hz (we already hold
            // sourceMutex_ here). UI reads capsMutex_, never sourceMutex_.
            updateCapsSnapshotLocked();
            emit sourceTelemetry(source_->name(), source_->isConnected(),
                                 source_->centerFreq(), source_->sampleRate(),
                                 source_->gain());
        }

        if (digital) {
            // ---- Digital VFO: no analog audio; push constellation symbols ----
            if (sel && !sel->recoveredSymbols.empty())
                emit constellationSymbols(sel->recoveredSymbols, source_->isConnected());
            if (!wasDigital_) wasDigital_ = true;   // entering digital (panel setMode in UI)
            std::vector<float> silence;
            silence.resize(raw.size(), 0.0f);
            audioSink_->write(silence);
            emit squelchState(true);
            emit audioLevel(-60.0f);
            // Gated/wav recording: keep the audio stream continuous but silent.
            if (wavWriter_.isRecording()) wavWriter_.write(silence);
            lk.unlock();
            if (!real) std::this_thread::sleep_for(std::chrono::milliseconds(33));
            continue;
        }

        // Leaving digital mode: tell the panel to return to its empty state once.
        if (wasDigital_) {
            wasDigital_ = false;
            emit constellationCleared();
        }

        // ANR sits AFTER the per-VFO resampler and BEFORE squelch/AGC/gated
        // recorder. Disabled by default -> identity (bit-exact legacy chain).
        const std::vector<float> audio = anr_.process(raw);

        // Squelch decision FIRST (updates smoothing/hangover, does not mute),
        // then AGC always sees the REAL audio (it tracks the noise floor while
        // closed instead of decaying away on zero blocks). We mute only the
        // speaker path. The gated recorder receives the real, un-muted audio so
        // its pre-roll captures the true signal onset.
        const float rms = rmsDbfs(audio);
        // Audio-RMS-domain noise floor (SAME domain as the Squelch threshold, so
        // the auto gate = floor + margin is apples-to-apples). Asymmetric
        // follower: track quieter backgrounds quickly, ignore loud transients
        // (a real signal) so it never lifts the floor. Never reads the IQ
        // total-power / per-bin canvas floor here -- that would cross domains.
        if (!audioNfInit_) {
            audioNfDbfs_ = rms;
            audioNfInit_ = true;
        } else if (rms < audioNfDbfs_) {
            audioNfDbfs_ = (1.0 - tokens::kSquelchNfAlphaDown) * audioNfDbfs_
                         + tokens::kSquelchNfAlphaDown * rms;
        } else {
            audioNfDbfs_ = (1.0 - tokens::kSquelchNfAlphaUp) * audioNfDbfs_
                         + tokens::kSquelchNfAlphaUp * rms;
        }
        {
            const qint64 nowNf = audioNfEmitClock_.elapsed();
            if (audioNfLastEmitMs_ < 0) {
                audioNfEmitClock_.start();
                audioNfLastEmitMs_ = 0;
            } else if (nowNf - audioNfLastEmitMs_ >= 200) {  // ~5 Hz
                audioNfLastEmitMs_ = nowNf;
                emit audioRmsNoiseFloor(static_cast<float>(audioNfDbfs_));
            }
        }
        // Auto-threshold latch: when armed, the gate threshold follows the tracked
        // audio-RMS noise floor + margin (same domain), re-derived each block. A
        // manual setSquelchThreshold disarms this (see above).
        if (squelchAuto_.load()) {
            const float autoThr = static_cast<float>(audioNfDbfs_)
                                  + static_cast<float>(tokens::kSquelchAutoMarginDb);
            squelchThreshold_ = autoThr;
            squelch_.setThresholdDb(autoThr);
        }
        const bool gate = squelch_.decide(audio, rms);
        // Detection/ANR/squelch/AGC all run on the legacy mono M. processWithGain
        // exposes the per-sample linear gain so the stereo M/S matrix below reuses
        // the EXACT same envelope (mono and L/R never level-mismatched).
        std::vector<float> leveled, agcGain;
        agc_.processWithGain(audio, &leveled, &agcGain);
        std::vector<float> out = leveled;
        if (!gate) std::fill(out.begin(), out.end(), 0.0f);
        emit squelchState(gate);
        emit audioLevel(agc_.currentLevelDb());

        // Speaker path. WFM with a live stereo decoder: rebuild L/R from the same
        // mono M plus the recovered side S with the smoothed blend, applying the
        // same AGC gain (zeroed when the squelch gate is closed). All other analog
        // modes keep the legacy mono write. Recording below stays mono (`out`).
        const bool wfmSel = (selMode == "WFM");
        if (wfmSel && sel && sel->stereo) {
            sel->stereo->setForceMono(forceMono_);   // survives channel rebuilds
            const float blend = sel->stereoBlend;
            const std::vector<float>& M = sel->stereoM48k;
            const std::vector<float>& S = sel->stereoS48k;
            const std::size_t n =
                std::min({M.size(), S.size(), agcGain.size()});
            std::vector<float> L(n), R(n);
            // Use one block-constant AGC gain for both M and S. The per-sample
            // `agcGain` envelope still ripple at audio beat rates (it tracks the
            // mono envelope sample-by-sample); multiplying that ripple onto the
            // side chain creates intermodulation sidebands that leak between L and
            // R and destroy channel separation. Averaged over the ~20 ms block the
            // gain is flat, so M and S share a single, common gain -- exactly how a
            // real receiver applies AGC to the recovered audio. The legacy mono
            // path / recording below still uses the per-sample `out` unchanged.
            double gblk = 0.0;
            for (std::size_t i = 0; i < n; ++i) gblk += agcGain[i];
            const float g = gate && n > 0 ? static_cast<float>(gblk / n) : 0.0f;
            for (std::size_t i = 0; i < n; ++i) {
                const float m = M[i], s = S[i];
                L[i] = std::clamp(g * (m + blend * s), -1.0f, 1.0f);
                R[i] = std::clamp(g * (m - blend * s), -1.0f, 1.0f);
            }
            audioSink_->writeStereo(L, R);
            // Phase24 network tap: mirror the SAME frames in parallel. The local
            // playback above is never altered; digital-mode silence (the other
            // write site) is deliberately NOT tapped -- digital VFOs carry no
            // demodulated audio, so the stream simply stays empty there.
            if (networkTap_) networkTap_->writeStereo(L, R);
        } else {
            audioSink_->write(out);
            if (networkTap_) networkTap_->write(out);
        }

        // Throttled (~5 Hz) honest stereo readout for the UI badge. Stereo means
        // the real pilot is locked AND the blend has actually come up; anything
        // else (or any non-WFM mode) reports mono.
        {
            bool isStereo = false;
            float bl = 0.0f, pq = 0.0f;
            if (wfmSel && sel && sel->stereo) {
                isStereo = sel->stereoLock && sel->stereoBlend > 0.5f;
                bl = sel->stereoBlend;
                pq = sel->stereoPilot;
            }
            const qint64 nowSt = stereoEmitClock_.elapsed();
            if (stereoLastEmitMs_ < 0 || nowSt - stereoLastEmitMs_ >= 200) {
                stereoLastEmitMs_ = nowSt;
                emit stereoState(isStereo, bl, pq);
            }
        }

        // Gated recording: the single segment writer serves both the
        // squelch-gated mode and the watch mode. It is fed the REAL
        // (un-muted) audio so pre-roll captures the true onset, and a gate
        // that is the OR of whichever modes are armed.
        {
            SegmentContext ctx;
            ctx.mode = selMode;
            ctx.channelFreqHz = sel ? sel->freqHz : centerNow;
            ctx.centerFreqHz = centerNow;
            ctx.gainDb = source_->gain();
            ctx.triggerThresholdDb = watch_.thresholdDb();
            ctx.hardware = source_->name();
            ctx.hardwareConnected = real;
            gatedRec_.setContext(ctx);

            bool recGate = false;
            if (gatedEnabled_) recGate = recGate || gate;
            if (watchEnabled_.load()) recGate = recGate || watchGate;

            const std::vector<QString> saved = gatedRec_.feed(leveled, recGate);
            const bool nowRecording = gatedRec_.isRecording();
            if (!saved.empty())
                emit watchStateChanged(watchEnabled_.load(), nowRecording,
                                       gatedRec_.segmentCount());
            if (nowRecording != gatedWasRecording_) {
                gatedWasRecording_ = nowRecording;
                emit watchStateChanged(watchEnabled_.load(), nowRecording,
                                       gatedRec_.segmentCount());
            }
        }

        // Continuous audio (WAV) recording for the main record button. When
        // recIgnoreSquelch_ is false we normally only capture while the gate is
        // open -- BUT skipping the closed-gate frames would shrink the WAV
        // shorter than the wall-clock recording time. Instead, write a silent
        // frame of the same length as `out` so the 48 kHz sample stream stays
        // continuous and the file duration matches the time spent recording.
        // (Baseband IQ recording above is unaffected: it always writes real IQ.)
        if (wavWriter_.isRecording()) {
            if (recIgnoreSquelch_ || gate) {
                wavWriter_.write(out);
            } else {
                std::vector<float> silence(out.size(), 0.0f);
                wavWriter_.write(silence);
            }
        }

        // 1 Hz REC progress tick: elapsed wall-clock seconds + current file
        // size, so the UI can render "● REC: name (MM:SS, NN KB)".
        if (wavWriter_.isRecording() || recorder_.isRecording()) {
            // Auto-segment rotation: if the IQ recorder opened a fresh file since
            // the last tick, surface the new path honestly.
            if (recorder_.isRecording() && recorder_.takeSegmentRotated()) {
                recCurrentPath_ = recorder_.currentFilePath();
                emit recordingSegmentChanged(recCurrentPath_);
            }
            const int secs = static_cast<int>(recClock_.elapsed() / 1000);
            if (secs != lastRecSecond_) {
                lastRecSecond_ = secs;
                emit recordingProgress(recCurrentPath_, secs,
                                       QFileInfo(recCurrentPath_).size());
            }
        }

        // CW decode (follows the selected VFO mode)
        if (selMode == "CW") {
            cwDecoder_.feed(out);
            QString text = cwDecoder_.takeText();
            if (!text.isEmpty()) emit cwDecoded(text, cwDecoder_.wpm());
        }

        // ADS-B decode (operates on full-rate raw IQ)
        if (selMode == "ADS-B") {
            adsbDecoder_.feed(iq);
            for (const auto& ac : adsbDecoder_.takeNewAircraft())
                emit adsbAircraft(ac);
        }

        // RDS (WFM only): pull the selected channel's accumulated PS/PTY/RadioText.
        // The channel's RdsDecoder is already being fed inside vfoManager_.process();
        // we only diff against the last pushed snapshot so the UI sees an event
        // when the station name / PTY / RadioText changes, not a per-block flood.
        if (selMode == "WFM" && sel && sel->rds) {
            RdsInfo rds = sel->rds->info();
            const bool locked = rds.haveAny;
            const bool changed =
                locked != lastRdsLocked_ ||
                (locked && (rds.programService != lastRdsPs_ ||
                            rds.pty != lastRdsPty_ ||
                            rds.radioText != lastRdsRt_));
            if (changed) {
                lastRdsPs_    = rds.programService;
                lastRdsPty_   = rds.pty;
                lastRdsRt_    = rds.radioText;
                lastRdsLocked_ = locked;
                emit rdsUpdated(rds.programService, rds.pty, rds.radioText, locked);
            }
        }

        // NOAA APT (WFM only): feed the SELECTED VFO's demodulated 48 kHz audio
        // into the streaming image decoder. We feed the pre-squelch `audio` (the
        // APT sync loop wants a continuous stream; the speaker squelch is
        // irrelevant to the decoder). When the user leaves WFM or retunes to a
        // different satellite we reset the accumulator and push one cleared
        // frame -- we never keep showing a stale cloud photo from another band.
        {
            const bool wfm = (selMode == "WFM");
            const double aptFreq = sel ? sel->freqHz : centerNow;
            if (wfm) {
                const bool retuned = std::abs(aptFreq - aptLastFreqHz_) > 1.0;
                if (!aptActive_ || retuned) {
                    aptDecoder_.reset();
                    aptLastRows_ = 0;
                    aptLastLocked_ = false;
                }
                aptActive_ = true;
                aptLastFreqHz_ = aptFreq;

                aptDecoder_.feed(audio);

                const int rows = aptDecoder_.rowCount();
                const bool locked = aptDecoder_.isLocked();
                const bool rowsChanged = (rows != aptLastRows_);
                const bool lockChanged  = (locked != aptLastLocked_);
                const qint64 nowApt = aptEmitClock_.elapsed();
                // Lock-state changes go out immediately; row growth is throttled
                // (~300 ms; APT lines arrive at 2/s, so this is about every line)
                // to avoid flooding the UI thread with QImage copies.
                const bool throttled =
                        aptLastEmitMs_ < 0 || nowApt - aptLastEmitMs_ >= 300;
                if (lockChanged || (rowsChanged && rows > 0 && throttled)) {
                    aptLastRows_ = rows;
                    aptLastLocked_ = locked;
                    aptLastEmitMs_ = nowApt;
                    emit aptImageReady(aptDecoder_.image(), locked, rows,
                                       aptDecoder_.lastSyncCorrelation());
                }
            } else if (aptActive_) {
                // Left WFM: stop feeding, drop the image, tell the panel to
                // return to its honest empty state.
                aptDecoder_.reset();
                aptActive_ = false;
                aptLastRows_ = 0;
                aptLastLocked_ = false;
                aptLastFreqHz_ = 0.0;
                emit aptImageReady(aptDecoder_.image(), false, 0, 0.0);
            }
        }

        lk.unlock();
        // Synthetic sources have no hardware clock; pace them manually. Real
        // blocking reads already run at wall-clock speed and must not sleep.
        if (!real) std::this_thread::sleep_for(std::chrono::milliseconds(33));
    }
}

} // namespace dsp
} // namespace mbdsdr
