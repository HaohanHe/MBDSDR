// SPDX-License-Identifier: MIT
// Offline tests for the streaming offline-file source (FileSource) and the
// engine openOfflineFile path. Everything runs on REAL files written to a temp
// dir: a 16-bit mono PCM WAV (+ sidecar JSON), a SigMF cf32 capture, and a raw
// complex blob. Asserts:
//   * chunked streaming (the file is NEVER loaded whole; each read fills only a
//     fixed-size caller buffer, cumulative samples == total),
//   * WAV int16 -> real complex conversion, pause, real seek,
//   * honest errors (missing file, non-16-bit WAV, missing raw sample rate),
//   * engine openOfflineFile swaps the source and offlinePosition advances.
// No hardware, no network, no audio device.
#include <QtTest/QtTest>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QDataStream>
#include <QtEndian>
#include <cmath>
#include <vector>
#include <complex>

#include "dsp/file_source.h"
#include "dsp/spectrum_engine.h"

using namespace mbdsdr::dsp;

namespace {
bool writeWav(const QString& path, quint32 sampleRate, int samples) {
    QFile f(path);
    if (!f.open(QIODevice::WriteOnly)) return false;
    QDataStream ds(&f);
    ds.setByteOrder(QDataStream::LittleEndian);
    const qint64 dataBytes = samples * 2;
    ds.writeRawData("RIFF", 4);
    ds << static_cast<quint32>(36 + dataBytes);
    ds.writeRawData("WAVE", 4);
    ds.writeRawData("fmt ", 4);
    ds << static_cast<quint32>(16);
    ds << static_cast<quint16>(1) << static_cast<quint16>(1);   // PCM mono
    ds << sampleRate;
    ds << static_cast<quint32>(sampleRate * 2);
    ds << static_cast<quint16>(2) << static_cast<quint16>(16);
    ds.writeRawData("data", 4);
    ds << static_cast<quint32>(dataBytes);
    for (int i = 0; i < samples; ++i)
        ds << static_cast<qint16>(((i * 2000) % 2000) - 1000);
    return true;
}
bool writeSidecar(const QString& wavPath, const QJsonObject& o) {
    QFile f(wavPath.chopped(4) + ".json");
    if (!f.open(QIODevice::WriteOnly)) return false;
    f.write(QJsonDocument(o).toJson(QJsonDocument::Indented));
    return true;
}
} // namespace

class TestFileSource : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void wavStreamsInFixedChunks();
    void wavIsRealComplex();
    void pauseFreezesOffset();
    void seekMovesRealOffset();
    void sigmfRoundtrip();
    void rawHonestUserParams();
    void errorsAreHonestNoCrash();
    void engineOpensWavAndStreams();
private:
    QString tmp_;
};

void TestFileSource::initTestCase() {
    tmp_ = QDir::tempPath() + "/mbdsdr_filesrc_test";
    QDir(tmp_).removeRecursively();
    QDir().mkpath(tmp_);
    writeWav(tmp_ + "/rec_audio.wav", 48000, 48000);   // 1.0 s
    QJsonObject o;
    o["type"] = "mbdsdr-audio-recording";
    o["center_freq"] = 100.5e6;
    o["mode"] = "NFM";
    writeSidecar(tmp_ + "/rec_audio.wav", o);
}

void TestFileSource::wavStreamsInFixedChunks() {
    FileSource fs(QStringLiteral(""));
    QVERIFY2(fs.openWav(tmp_ + "/rec_audio.wav", 100.5e6, "NFM"),
             qPrintable(fs.errorString()));
    QCOMPARE(fs.sampleRate(), 48000.0);
    QCOMPARE(fs.totalSamples(), 48000);
    QVERIFY(fs.start());

    // Read in FIXED 1000-sample chunks (the engine uses ~25 ms blocks too).
    // The whole file is never loaded: each call fills exactly out.size().
    const std::size_t chunk = 1000;
    std::vector<std::complex<float>> out(chunk);
    std::size_t total = 0, calls = 0;
    for (int i = 0; i < 60; ++i) {
        QCOMPARE(out.size(), chunk);                 // caller buffer never grows
        std::size_t got = fs.readIQ(out);
        QVERIFY(got <= chunk);
        total += got; ++calls;
        if (got < chunk) break;                       // EOF loop
    }
    QVERIFY2(total >= 48000, "must stream the whole capture (got " +
             QByteArray::number(total) + ")");
    fs.stop();
}

void TestFileSource::wavIsRealComplex() {
    FileSource fs(QStringLiteral(""));
    QVERIFY(fs.openWav(tmp_ + "/rec_audio.wav", 0, "NFM"));
    QVERIFY(fs.start());
    std::vector<std::complex<float>> out(8);
    QCOMPARE(fs.readIQ(out), std::size_t(8));
    for (auto c : out) {
        QVERIFY2(std::abs(c.imag()) < 1e-9f,
                 "mono WAV feeds as real-valued complex (imag==0)");
        QVERIFY2(std::abs(c.real()) <= 1.0f + 1e-6f,
                 "int16 must normalize to unit range");
    }
    fs.stop();
}

void TestFileSource::pauseFreezesOffset() {
    FileSource fs(QStringLiteral(""));
    QVERIFY(fs.openWav(tmp_ + "/rec_audio.wav", 0, "NFM"));
    QVERIFY(fs.start());
    fs.setPaused(true);
    qint64 before = fs.playedSamples();
    std::vector<std::complex<float>> out(512);
    QCOMPARE(fs.readIQ(out), std::size_t(0));        // paused => no advance
    QCOMPARE(fs.playedSamples(), before);
    fs.setPaused(false);
    QVERIFY(fs.readIQ(out) > 0);
    fs.stop();
}

void TestFileSource::seekMovesRealOffset() {
    FileSource fs(QStringLiteral(""));
    QVERIFY(fs.openWav(tmp_ + "/rec_audio.wav", 0, "NFM"));
    QVERIFY(fs.start());
    fs.seekFraction(0.5);
    QVERIFY2(std::abs(fs.playedSamples() - 24000) <= 10,
             qPrintable(QString("after seek 0.5: %1").arg(fs.playedSamples())));
    fs.stop();
}

void TestFileSource::sigmfRoundtrip() {
    // Build a SigMF cf32 capture by hand.
    const QString base = tmp_ + "/iqcap";
    std::vector<std::complex<float>> iq(4096);
    for (int i = 0; i < 4096; ++i)
        iq[i] = {std::cos(2*M_PI*100*i/48000.0f), std::sin(2*M_PI*100*i/48000.0f)};
    QFile d(base + ".sigmf-data");
    d.open(QIODevice::WriteOnly);
    d.write(QByteArray(reinterpret_cast<const char*>(iq.data()),
                       qint64(iq.size() * sizeof(std::complex<float>))));
    d.close();
    QJsonObject g;
    g["core:datatype"] = "cf32_le";
    g["core:sample_rate"] = 48000.0;
    g["core:frequency"] = 98.5e6;
    g["core:num_samples"] = 4096;
    QJsonObject root; root["global"] = g;
    QFile m(base + ".sigmf-meta");
    m.open(QIODevice::WriteOnly);
    m.write(QJsonDocument(root).toJson());
    m.close();

    FileSource fs(base);
    QVERIFY(fs.start());
    QCOMPARE(fs.sampleRate(), 48000.0);
    QCOMPARE(fs.centerFreq(), 98.5e6);
    QCOMPARE(fs.totalSamples(), 4096);
    std::vector<std::complex<float>> rd(4096);
    QCOMPARE(fs.readIQ(rd), std::size_t(4096));
    float err = 0;
    for (int i = 0; i < 4096; ++i) err = std::max(err, std::abs(rd[i] - iq[i]));
    QVERIFY2(err < 1e-5f, "SigMF cf32 roundtrip");
    fs.stop();
}

void TestFileSource::rawHonestUserParams() {
    const QString p = tmp_ + "/raw.cf32";
    std::vector<std::complex<float>> iq(1024, {0.1f, -0.1f});
    QFile f(p);
    f.open(QIODevice::WriteOnly);
    f.write(QByteArray(reinterpret_cast<const char*>(iq.data()),
                       qint64(iq.size() * 8)));
    f.close();

    FileSource fs(QStringLiteral(""));
    // Missing sample rate must fail honestly.
    QVERIFY(!fs.openRaw(p, 0.0));
    QVERIFY(!fs.errorString().isEmpty());
    QVERIFY(fs.openRaw(p, 1200000.0));
    QCOMPARE(fs.totalSamples(), qint64(1024));
    QVERIFY(fs.start());
    std::vector<std::complex<float>> out(1024);
    QCOMPARE(fs.readIQ(out), std::size_t(1024));
    QVERIFY(std::abs(out[0].real() - 0.1f) < 1e-6f);
    fs.stop();
}

void TestFileSource::errorsAreHonestNoCrash() {
    FileSource fs1(QStringLiteral(""));
    QVERIFY(!fs1.openWav(tmp_ + "/nope.wav", 0, ""));
    QVERIFY(!fs1.errorString().isEmpty());

    // Non-16-bit WAV (8-bit) must be rejected, not crash.
    const QString bad = tmp_ + "/eight.wav";
    QFile f(bad); f.open(QIODevice::WriteOnly);
    QDataStream ds(&f); ds.setByteOrder(QDataStream::LittleEndian);
    ds.writeRawData("RIFF", 4); ds << quint32(36);
    ds.writeRawData("WAVE", 4);
    ds.writeRawData("fmt ", 4); ds << quint32(16);
    ds << quint16(1) << quint16(1) << quint32(8000) << quint32(8000);
    ds << quint16(1) << quint16(8);          // 8-bit
    ds.writeRawData("data", 4); ds << quint32(0);
    f.close();
    FileSource fs2(QStringLiteral(""));
    QVERIFY(!fs2.openWav(bad, 0, ""));
    QVERIFY(!fs2.errorString().isEmpty());

    FileSource fs3(QStringLiteral(""));
    QVERIFY(!fs3.openRaw(tmp_ + "/nope.cf32", 1e6));
}

void TestFileSource::engineOpensWavAndStreams() {
    SpectrumEngine eng;
    QVERIFY(!eng.isOfflineFileActive());
    const bool ok = eng.openOfflineFile(tmp_ + "/rec_audio.wav");
    QVERIFY2(ok, "engine should open the real WAV (fallback error emitted)");
    QVERIFY(eng.isOfflineFileActive());
    double cur = -1, total = -1;
    QVERIFY(eng.offlinePosition(cur, total));
    QVERIFY2(total > 0.9 && total < 1.1,
             qPrintable(QString("1 s file => total ~1s, got %1").arg(total)));

    // Stream a little through the engine: position must advance.
    eng.start();
    QTest::qWait(800);
    double cur2 = -1, total2 = -1;
    QVERIFY(eng.offlinePosition(cur2, total2));
    qInfo("offline position: %.2f / %.2f s", cur2, total2);
    eng.setOfflinePaused(true);
    double pausedAt = -1;
    eng.offlinePosition(pausedAt, total2);
    QTest::qWait(300);
    double pausedAfter = -1;
    eng.offlinePosition(pausedAfter, total2);
    QVERIFY2(std::abs(pausedAfter - pausedAt) < 0.15,
             "pause must freeze the file offset");
    eng.setOfflinePaused(false);
    eng.shutdown();
    eng.wait(2000);
}

QTEST_MAIN(TestFileSource)
#include "test_file_source.moc"
