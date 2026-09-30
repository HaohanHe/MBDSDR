// SPDX-License-Identifier: MIT
// Offscreen tests for the recording-library core: scanning a real directory of
// .wav + sidecar .json, the honest empty state, deletion of real files, minimal
// RIFF/WAV header probing (sample rate / frames), and the filename-template
// fallback. No hardware, no audio device, no network.
#include <QtTest/QtTest>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QDataStream>
#include <QtEndian>

#include "ui/recording_library.h"

using namespace mbdsdr::ui;

namespace {
// Write a minimal uncompressed 16-bit PCM WAV (mono, arbitrary rate) with n
// samples, mirroring the exact layout WavWriter/GatedRecorder produce.
bool writeWav(const QString& path, quint32 sampleRate, int samples) {
    QFile f(path);
    if (!f.open(QIODevice::WriteOnly)) return false;
    QDataStream ds(&f);
    ds.setByteOrder(QDataStream::LittleEndian);
    const qint64 dataBytes = samples * 2;   // mono int16
    ds.writeRawData("RIFF", 4);
    ds << static_cast<quint32>(36 + dataBytes);
    ds.writeRawData("WAVE", 4);
    ds.writeRawData("fmt ", 4);
    ds << static_cast<quint32>(16);
    ds << static_cast<quint16>(1);          // PCM
    ds << static_cast<quint16>(1);          // mono
    ds << sampleRate;
    ds << static_cast<quint32>(sampleRate * 2);
    ds << static_cast<quint16>(2);
    ds << static_cast<quint16>(16);
    ds.writeRawData("data", 4);
    ds << static_cast<quint32>(dataBytes);
    for (int i = 0; i < samples; ++i)
        ds << static_cast<qint16>((i % 1000) - 500);
    return true;
}

bool writeSidecar(const QString& wavPath, const QJsonObject& o) {
    QFile f(wavPath.chopped(4) + ".json");
    if (!f.open(QIODevice::WriteOnly)) return false;
    f.write(QJsonDocument(o).toJson(QJsonDocument::Indented));
    return true;
}
} // namespace

class TestRecordingLibrary : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void scansSidecarMetadata();
    void emptyDirIsHonest();
    void deletesWavAndSidecar();
    void probesWavHeader();
    void fallsBackToFilename();
    void rejectsNonPcm();
private:
    QString tmpDir_;
};

void TestRecordingLibrary::initTestCase() {
    tmpDir_ = QDir::tempPath() + "/mbdsdr_reclib_test";
    QDir(tmpDir_).removeRecursively();
    QDir().mkpath(tmpDir_);

    // 1) continuous demod-audio recording: <stamp>_<freqMHz>_<mode>.wav + proof
    writeWav(tmpDir_ + "/20260930_120000_100.500_NFM.wav", 48000, 48000);
    QJsonObject audio;
    audio["type"] = "mbdsdr-audio-recording";
    audio["sample_rate"] = 48000.0;
    audio["center_freq"] = 100.5e6;
    audio["mode"] = "NFM";
    audio["datetime"] = "2026-09-30T12:00:00Z";
    writeSidecar(tmpDir_ + "/20260930_120000_100.500_NFM.wav", audio);

    // 2) gated watch segment: <stamp>_<mode>_<freq>Hz.wav + watch proof
    writeWav(tmpDir_ + "/20260930_120010_NFM_100500000Hz.wav", 48000, 24000);
    QJsonObject watch;
    watch["type"] = "mbdsdr-watch-recording";
    watch["duration_s"] = 0.5;
    watch["frequency"] = 100500000.0;
    watch["mode"] = "NFM";
    watch["trigger_threshold_db"] = -50.0;
    watch["start_time"] = "2026-09-30T12:00:10Z";
    watch["is_hardware"] = false;
    writeSidecar(tmpDir_ + "/20260930_120010_NFM_100500000Hz.wav", watch);
}

void TestRecordingLibrary::scansSidecarMetadata() {
    const QVector<RecordingEntry> entries = RecordingLibrary::scan(tmpDir_);
    QCOMPARE(entries.size(), 2);

    // Find the watch segment (newest) by name rather than assuming order.
    const RecordingEntry* watch = nullptr;
    const RecordingEntry* audio = nullptr;
    for (const RecordingEntry& e : entries) {
        if (e.wavPath.contains("120010")) watch = &e;
        else if (e.wavPath.contains("120000")) audio = &e;
    }
    QVERIFY(watch);
    QCOMPARE(watch->meta.sidecarType, QString("mbdsdr-watch-recording"));
    QCOMPARE(watch->meta.frequencyHz, 100500000.0);
    QCOMPARE(watch->meta.mode, QString("NFM"));
    QCOMPARE(watch->meta.triggerThresholdDb, -50.0);
    QVERIFY(watch->meta.time.startsWith("2026-09-30"));
    QVERIFY(QFileInfo::exists(watch->jsonPath));

    // Older audio recording carries center_freq from its proof.
    QVERIFY(audio);
    QCOMPARE(audio->meta.sidecarType, QString("mbdsdr-audio-recording"));
    QCOMPARE(audio->meta.frequencyHz, 100.5e6);
    QCOMPARE(audio->meta.mode, QString("NFM"));
}

void TestRecordingLibrary::emptyDirIsHonest() {
    const QString emptyDir = tmpDir_ + "/empty";
    QDir().mkpath(emptyDir);
    QVERIFY(RecordingLibrary::scan(emptyDir).isEmpty());      // no rows
    QVERIFY(RecordingLibrary::scan(emptyDir + "/does_not_exist").isEmpty());
}

void TestRecordingLibrary::deletesWavAndSidecar() {
    const QString dir = tmpDir_ + "/del";
    QDir().mkpath(dir);
    const QString wav = dir + "/20260930_130000_100.000_AM.wav";
    writeWav(wav, 48000, 100);
    QJsonObject o; o["type"] = "mbdsdr-audio-recording";
    writeSidecar(wav, o);
    QVERIFY(QFileInfo::exists(wav));
    QVERIFY(QFileInfo::exists(wav.chopped(4) + ".json"));

    QVector<RecordingEntry> entries = RecordingLibrary::scan(dir);
    QCOMPARE(entries.size(), 1);
    QVERIFY(RecordingLibrary::removeEntry(entries.first()));
    QVERIFY(!QFileInfo::exists(wav));
    QVERIFY(!QFileInfo::exists(wav.chopped(4) + ".json"));    // sidecar gone too
    QVERIFY(RecordingLibrary::scan(dir).isEmpty());
}

void TestRecordingLibrary::probesWavHeader() {
    const QString wav = tmpDir_ + "/20260930_120000_100.500_NFM.wav";
    const WavProbe p = RecordingLibrary::probeWav(wav);
    QVERIFY2(p.ok, qPrintable(p.error));
    QCOMPARE(p.audioFormat, 1);          // PCM
    QCOMPARE(p.channels, 1);
    QCOMPARE(p.sampleRate, 48000u);
    QCOMPARE(p.bitsPerSample, 16);
    QCOMPARE(p.frames, 48000u);          // 48000 samples mono int16

    // Decode the payload into unit-range float.
    std::vector<float> out;
    QVERIFY(RecordingLibrary::decodePcmMonoToFloat(p, wav, out));
    QCOMPARE(out.size(), static_cast<std::size_t>(48000));
    QVERIFY(out.front() >= -1.0f && out.front() <= 1.0f);
}

void TestRecordingLibrary::fallsBackToFilename() {
    // No sidecar: parse the audio template "<stamp>_<freqMHz>_<mode>".
    RecordingMeta m1 = RecordingLibrary::parseFileName("20260930_143000_100.500_WFM");
    QVERIFY(m1.time.startsWith("2026-09-30"));
    QCOMPARE(m1.frequencyHz, 100.5e6);
    QCOMPARE(m1.mode, QString("WFM"));

    // Watch template "<stamp>_<mode>_<freq>Hz".
    RecordingMeta m2 = RecordingLibrary::parseFileName("20260930_143005_NFM_100500000Hz");
    QVERIFY(m2.time.startsWith("2026-09-30"));
    QCOMPARE(m2.frequencyHz, 100500000.0);
    QCOMPARE(m2.mode, QString("NFM"));

    // Unparseable name -> honest empty fields (UI then shows the raw filename).
    RecordingMeta m3 = RecordingLibrary::parseFileName("random_capture_xyz");
    QVERIFY(m3.time.isEmpty());
    QCOMPARE(m3.frequencyHz, 0.0);
    QVERIFY(m3.mode.isEmpty());
}

void TestRecordingLibrary::rejectsNonPcm() {
    // Craft a WAV with audioFormat=3 (IEEE float) -- must be rejected honestly.
    const QString bad = tmpDir_ + "/float.wav";
    QFile f(bad);
    f.open(QIODevice::WriteOnly);
    QDataStream ds(&f);
    ds.setByteOrder(QDataStream::LittleEndian);
    ds.writeRawData("RIFF", 4); ds << static_cast<quint32>(44);
    ds.writeRawData("WAVE", 4);
    ds.writeRawData("fmt ", 4); ds << static_cast<quint32>(16);
    ds << static_cast<quint16>(3);          // IEEE float, NOT PCM
    ds << static_cast<quint16>(1); ds << static_cast<quint32>(48000);
    ds << static_cast<quint32>(48000 * 4); ds << static_cast<quint16>(4);
    ds << static_cast<quint16>(32);
    ds.writeRawData("data", 4); ds << static_cast<quint32>(16);
    f.close();

    const WavProbe p = RecordingLibrary::probeWav(bad);
    QVERIFY(!p.ok);
    QVERIFY(!p.error.isEmpty());
}

QTEST_MAIN(TestRecordingLibrary)
#include "test_recording_library.moc"
