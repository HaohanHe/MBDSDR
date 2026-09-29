// SPDX-License-Identifier: MIT
//
// *** NOT HARDWARE / 非硬件合成 ***
//
// Unattended signal-triggered (watch) recording test. Every sample here is
// synthesized in software -- Gaussian floor noise + a sine burst added on top;
// no radio, no real signal. We verify:
//   * SignalWatch opens only while the REAL measured block level is above the
//     configured threshold (with confirm), and closes after the burst leaves;
//   * GatedRecorder writes exactly one WAV (+ JSON sidecar) per burst, never
//     during the below-threshold floor;
//   * the saved file's pre-roll genuinely contains the signal onset (onset
//     sits inside the pre-roll window, with floor audio before it);
//   * filename = yyyyMMdd_HHmmss_MODE_FREQHz.wav; duration is plausible;
//   * the sidecar JSON carries sample rate / centre + channel frequency / gain
//     / mode / start + end time / duration / pre-roll / end-delay / threshold,
//     and is marked "非硬件 / NOT HARDWARE";
//   * a second burst produces a second, distinct file;
//   * floor-only input produces no file at all.
#include <QtTest/QtTest>
#include <QDir>
#include <QFileInfo>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <QDebug>
#include <cmath>
#include <vector>

#include "dsp/squelch.h"
#include "dsp/signal_watch.h"
#include "dsp/gated_recorder.h"

using namespace mbdsdr::dsp;

namespace {
constexpr int kSr = 48000;
constexpr int kBlk = 960;          // 20 ms @ 48 kHz
constexpr double kFloorAmp = 0.01;   // floor RMS ~= -40 dBFS
constexpr double kToneAmp = 0.5;     // tone RMS ~= -9 dBFS
constexpr float  kThreshold = -35.0f; // floor below, tone above

// *** SYNTHETIC -- NOT HARDWARE ***
struct Synth {
    double phase = 0.0;
    unsigned long rng = 0x12345678;
    float gaussian() {
        // xorshift32 -> roughly uniform, Box-Muller.
        rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
        const double u1 = (rng & 0xffffff) / static_cast<double>(0x1000000);
        rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5;
        const double u2 = (rng & 0xffffff) / static_cast<double>(0x1000000);
        const double r = std::sqrt(-2.0 * std::log(u1 + 1e-12));
        return static_cast<float>(r * std::cos(2.0 * M_PI * u2));
    }
    std::vector<float> floorBlock() {
        std::vector<float> v(kBlk);
        for (int i = 0; i < kBlk; ++i) v[i] = static_cast<float>(kFloorAmp) * gaussian();
        return v;
    }
    std::vector<float> burstBlock() {
        std::vector<float> v = floorBlock();
        for (int i = 0; i < kBlk; ++i) {
            phase += 2.0 * M_PI * 1000.0 / kSr;
            v[i] += static_cast<float>(kToneAmp * std::sin(phase));
        }
        return v;
    }
};

// Read a mono int16 WAV into float [-1,1].
std::vector<float> readWav(const QString& path) {
    FILE* fp = fopen(path.toLocal8Bit().constData(), "rb");
    if (!fp) return {};
    fseek(fp, 44, SEEK_SET);
    std::vector<float> out;
    for (;;) {
        unsigned char b[2];
        if (fread(b, 1, 2, fp) != 2) break;
        int16_t s = static_cast<int16_t>(b[0] | (b[1] << 8));
        out.push_back(s / 32768.0f);
    }
    fclose(fp);
    return out;
}

QStringList wavList(const QString& dir) {
    return QDir(dir).entryList(QStringList() << "*.wav", QDir::Files);
}
} // namespace

class TestWatchRecord : public QObject {
    Q_OBJECT
private slots:
    void detectorOpensOnBurstClosesOnFloor();
    void recordsPerBurstWithPrerollAndSidecar();
    void floorOnlyProducesNothing();
    void cleanupTestCase();
};

static QString scratchDir;

void TestWatchRecord::detectorOpensOnBurstClosesOnFloor() {
    SignalWatch watch(20.0);
    watch.setThresholdDb(kThreshold);
    watch.reset();

    Synth sy;
    // Floor: must stay closed.
    for (int i = 0; i < 50; ++i) {
        auto b = sy.floorBlock();
        QVERIFY(!watch.update(rmsDbfs(b)));
    }
    // Burst: opens within a few blocks (confirm = 50 ms).
    bool opened = false;
    int openAt = -1;
    for (int i = 0; i < 20; ++i) {
        auto b = sy.burstBlock();
        if (watch.update(rmsDbfs(b))) { opened = true; openAt = i; break; }
    }
    QVERIFY2(opened, "watch trigger must open on the above-threshold burst");
    // 50 ms confirm / 20 ms blocks -> opens on the 3rd burst block.
    QVERIFY2(openAt >= 2 && openAt <= 5, "trigger opens after the confirm time");

    // Floor again: closes (decay smoothing bridges a few blocks).
    bool closed = false;
    for (int i = 0; i < 60; ++i) {
        auto b = sy.floorBlock();
        if (!watch.update(rmsDbfs(b))) { closed = true; break; }
    }
    QVERIFY2(closed, "watch trigger must close after the burst leaves");
}

void TestWatchRecord::recordsPerBurstWithPrerollAndSidecar() {
    scratchDir = QDir::tempPath() + "/mbdsdr_watch_test_" +
                 QString::number(QCoreApplication::applicationPid());
    QDir().mkpath(scratchDir);

    GatedRecorder rec(kSr);
    rec.setOutputDir(scratchDir);
    rec.setPreRollMs(400.0);
    rec.setHangMs(1500.0);

    SignalWatch watch(20.0);
    watch.setThresholdDb(kThreshold);
    watch.reset();

    // Labelling context (the engine normally supplies this every loop; in the
    // DSP-level test we provide it once, including the real trigger threshold).
    SegmentContext ctx;
    ctx.mode = "NFM";
    ctx.channelFreqHz = 98.5e6;
    ctx.centerFreqHz = 98.5e6;
    ctx.gainDb = 20.0;
    ctx.triggerThresholdDb = kThreshold;
    ctx.hardware = "Test Signal";
    ctx.hardwareConnected = false;
    rec.setContext(ctx);

    auto step = [&](const std::vector<float>& b) {
        const bool gate = watch.update(rmsDbfs(b));
        return rec.feed(b, gate);
    };

    Synth sy;

    // Phase A: 1.0 s floor -> no files.
    for (int i = 0; i < 50; ++i) {
        auto saved = step(sy.floorBlock());
        QVERIFY2(saved.empty(), "floor must not be recorded");
    }
    QVERIFY2(wavList(scratchDir).isEmpty(), "no WAV before the burst");

    // Phase B: 1.5 s burst.
    bool gateOpened = false;
    for (int i = 0; i < 75; ++i) {
        auto saved = step(sy.burstBlock());
        QVERIFY(saved.empty());
        if (watch.active()) gateOpened = true;
    }
    QVERIFY2(gateOpened, "detector opened during the burst");
    QVERIFY2(rec.isRecording(), "recorder is recording during the burst");

    // Phase C: 2.5 s floor -> segment finalises after the 1.5 s end-delay.
    QString firstFile;
    for (int i = 0; i < 125; ++i) {
        auto saved = step(sy.floorBlock());
        if (!saved.empty()) { QCOMPARE(firstFile, QString()); firstFile = saved.front(); }
    }
    QVERIFY2(!firstFile.isEmpty(), "first burst must produce one WAV after end-delay");
    QCOMPARE(wavList(scratchDir).size(), 1);

    // Filename pattern.
    const QString name = QFileInfo(firstFile).fileName();
    qInfo() << "saved segment:" << name;
    QRegularExpression re(R"(^\d{8}_\d{6}_[A-Za-z]+_\d+Hz(_\d+)?\.wav$)");
    QVERIFY2(re.match(name).hasMatch(),
             qPrintable("filename must be yyyyMMdd_HHmmss_MODE_FREQHz.wav, got " + name));

    // Read back: pre-roll + duration.
    std::vector<float> pcm = readWav(firstFile);
    QVERIFY2(!pcm.empty(), "saved WAV must contain samples");
    const double dur = pcm.size() / static_cast<double>(kSr);
    qInfo() << "segment duration:" << dur << "s";
    // 1.5 s burst + up to 0.4 s pre-roll + a short shaped tail, well under the
    // 1.5 s end-delay (tail silence is trimmed on save).
    QVERIFY2(dur >= 1.5 && dur <= 2.3,
             qPrintable(QString("duration in [1.5,2.3]s, got %1").arg(dur)));

    // Signal onset: first clearly-tone sample. Must sit INSIDE the pre-roll
    // window (0.4 s = 19200 samples), with floor audio captured before it.
    int onset = -1;
    for (std::size_t i = 0; i < pcm.size(); ++i) {
        if (std::abs(pcm[i]) > 0.2f) { onset = static_cast<int>(i); break; }
    }
    qInfo() << "onset sample:" << onset;
    QVERIFY2(onset > 2400, "pre-roll must capture floor before the onset (>50ms)");
    QVERIFY2(onset <= 19200 + 2880, "onset must be within the 0.4s pre-roll (+1 block)");

    // Sidecar JSON.
    QString jsonPath = firstFile;
    jsonPath.chop(4); jsonPath += ".json";
    QFile jf(jsonPath);
    QVERIFY2(jf.exists(), "a sidecar .json must sit next to the WAV");
    QVERIFY(jf.open(QIODevice::ReadOnly));
    QJsonParseError perr{};
    QJsonDocument doc = QJsonDocument::fromJson(jf.readAll(), &perr);
    QCOMPARE(perr.error, QJsonParseError::NoError);
    QVERIFY(doc.isObject());
    const QJsonObject o = doc.object();

    QVERIFY(o.contains("type"));
    QVERIFY(o.contains("sample_rate"));
    QVERIFY(o.contains("center_freq"));
    QVERIFY(o.contains("frequency"));
    QVERIFY(o.contains("gain_db"));
    QVERIFY(o.contains("mode"));
    QVERIFY(o.contains("start_time"));
    QVERIFY(o.contains("end_time"));
    QVERIFY(o.contains("duration_s"));
    QVERIFY(o.contains("preroll_ms"));
    QVERIFY(o.contains("end_delay_ms"));
    QVERIFY(o.contains("trigger_threshold_db"));
    QVERIFY(o.contains("hardware"));
    QVERIFY(o.contains("is_hardware"));
    QVERIFY(o.contains("note"));

    QCOMPARE(o.value("sample_rate").toDouble(), static_cast<double>(kSr));
    QCOMPARE(o.value("preroll_ms").toDouble(), 400.0);
    QCOMPARE(o.value("end_delay_ms").toDouble(), 1500.0);
    QCOMPARE(o.value("trigger_threshold_db").toDouble(), static_cast<double>(kThreshold));
    QCOMPARE(o.value("is_hardware").toBool(), false);
    QVERIFY(o.value("duration_s").toDouble() > 0.0);
    QVERIFY(o.value("center_freq").toDouble() > 0.0);
    QVERIFY(o.value("frequency").toDouble() > 0.0);
    QVERIFY2(o.value("note").toString().contains("NOT HARDWARE"),
             "offline sidecar must be marked NOT HARDWARE");
    // Measured duration must match the written sample count.
    QVERIFY(std::abs(o.value("duration_s").toDouble() - dur) < 0.05);

    // Phase D/E: a second burst -> second, distinct file.
    for (int i = 0; i < 50; ++i) step(sy.burstBlock());
    QString secondFile;
    for (int i = 0; i < 125; ++i) {
        auto saved = step(sy.floorBlock());
        if (!saved.empty()) { QCOMPARE(secondFile, QString()); secondFile = saved.front(); }
    }
    QVERIFY2(!secondFile.isEmpty(), "second burst must produce a second WAV");
    QVERIFY2(secondFile != firstFile, "the two files must be distinct");
    QCOMPARE(wavList(scratchDir).size(), 2);
    QCOMPARE(rec.segmentCount(), 2);
}

void TestWatchRecord::floorOnlyProducesNothing() {
    const QString dir = QDir::tempPath() + "/mbdsdr_watch_quiet_" +
                        QString::number(QCoreApplication::applicationPid());
    QDir().mkpath(dir);

    GatedRecorder rec(kSr);
    rec.setOutputDir(dir);
    rec.setPreRollMs(400.0);
    rec.setHangMs(1500.0);
    SignalWatch watch(20.0);
    watch.setThresholdDb(kThreshold);
    watch.reset();

    Synth sy;
    // 3 s of floor only.
    for (int i = 0; i < 150; ++i) {
        auto b = sy.floorBlock();
        const bool gate = watch.update(rmsDbfs(b));
        auto saved = rec.feed(b, gate);
        QVERIFY(saved.empty());
    }
    auto tail = rec.flush();
    QVERIFY2(tail.empty(), "floor-only watch must not finalise a segment");
    QVERIFY2(wavList(dir).isEmpty(), "no WAV may be written below threshold");
    QDir(dir).removeRecursively();
}

void TestWatchRecord::cleanupTestCase() {
    if (!scratchDir.isEmpty()) QDir(scratchDir).removeRecursively();
}

QTEST_MAIN(TestWatchRecord)
#include "test_watch_record.moc"
