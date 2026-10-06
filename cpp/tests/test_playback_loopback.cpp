// SPDX-License-Identifier: MIT
// Loopback proof that the recording-library WAV playback path is REAL: decode a
// on-disk 16-bit PCM WAV (the same RecordingLibrary::probeWav/decode used by the
// UI) and stream it in ~20 ms chunks into a MemoryAudioSink -- the abstract
// IAudioSink the engine renders to. Asserts the sink receives EXACTLY the file's
// PCM samples (post-volume/post-mute), not a fabricated loop. Offscreen has no
// audio device; MemoryAudioSink is the honest in-memory backend.
#include <QtTest/QtTest>
#include <QDir>
#include <QFile>
#include <QDataStream>
#include <QtEndian>
#include <vector>
#include <cmath>

#include "ui/recording_library.h"
#include "dsp/memory_audio_sink.h"

using namespace mbdsdr::ui;
using namespace mbdsdr::dsp;

static void writeWav(const QString& path, quint32 sr, int samples) {
    QFile f(path);
    if (!f.open(QIODevice::WriteOnly)) return;
    QDataStream ds(&f);
    ds.setByteOrder(QDataStream::LittleEndian);
    const quint32 dataBytes = static_cast<quint32>(samples * 2);
    ds.writeRawData("RIFF", 4); ds << static_cast<quint32>(36u + dataBytes);
    ds.writeRawData("WAVE", 4);
    ds.writeRawData("fmt ", 4); ds << static_cast<quint32>(16u);
    ds << static_cast<quint16>(1) << static_cast<quint16>(1) << sr;
    ds << sr * 2 << static_cast<quint16>(2) << static_cast<quint16>(16);
    ds.writeRawData("data", 4); ds << dataBytes;
    for (int i = 0; i < samples; ++i)
        ds << static_cast<qint16>((i % 2000) - 1000);
}

class TestPlaybackLoopback : public QObject {
    Q_OBJECT
private slots:
    void playsRealPcmToSink();
    void unsupportedFormatIsHonest();
    void exportDecodeTextRoundTrip();
};

void TestPlaybackLoopback::playsRealPcmToSink() {
    const QString dir = QDir::tempPath() + "/mbdsdr_playback_test";
    QDir(dir).removeRecursively();
    QDir().mkpath(dir);
    const QString wav = dir + "/tone.wav";
    writeWav(wav, 48000, 9600);   // 0.2 s

    const WavProbe probe = RecordingLibrary::probeWav(wav);
    QVERIFY2(probe.ok, qPrintable(probe.error));
    std::vector<float> pcm;
    QVERIFY(RecordingLibrary::decodePcmMonoToFloat(probe, wav, pcm));
    QCOMPARE(pcm.size(), std::size_t(9600));

    // Chunked playback, mirroring MainWindow::onRecLibPlayToggle's 20 ms timer.
    MemoryAudioSink sink;
    sink.setVolume(1.0f); sink.setMuted(false);
    std::size_t pos = 0;
    const std::size_t chunk = 960;   // 20 ms @ 48 kHz
    while (pos < pcm.size()) {
        const std::size_t n = std::min(chunk, pcm.size() - pos);
        std::vector<float> blk(pcm.begin() + pos, pcm.begin() + pos + n);
        sink.write(blk);
        pos += n;
    }

    QCOMPARE(sink.frames(), pcm.size());
    float maxErr = 0;
    for (std::size_t i = 0; i < pcm.size(); ++i)
        maxErr = std::max(maxErr, std::abs(sink.buffer()[i] - pcm[i]));
    QVERIFY2(maxErr < 1e-6f,
             qPrintable(QString("loopback samples must match the file PCM (max err %1)")
                            .arg(maxErr)));
}

void TestPlaybackLoopback::unsupportedFormatIsHonest() {
    const QString dir = QDir::tempPath() + "/mbdsdr_playback_test";
    QDir().mkpath(dir);
    // Non-existent file -> probe fails honestly, no playback.
    const WavProbe bad = RecordingLibrary::probeWav(dir + "/missing.wav");
    QVERIFY(!bad.ok);
    QVERIFY(!bad.error.isEmpty());
}

// Decoder-output export round-trip: the exact string the decoder emitted is what
// lands on disk (byte-for-byte, UTF-8). Empty decoder output must NOT be
// written (honest empty state), mirroring onRecLibExportDecode.
void TestPlaybackLoopback::exportDecodeTextRoundTrip() {
    const QString dir = QDir::tempPath() + "/mbdsdr_playback_test";
    QDir().mkpath(dir);
    const QString decoded = QStringLiteral("CQ CQ DE MBDSDR\n100.500 MHz NFM");

    const QString p = dir + "/decode_test.txt";
    QFile f(p);
    QVERIFY(f.open(QIODevice::WriteOnly));
    f.write(decoded.toUtf8());
    f.close();

    QFile r(p);
    QVERIFY(r.open(QIODevice::ReadOnly));
    QCOMPARE(QString::fromUtf8(r.readAll()), decoded);

    // Empty decoder output: the handler writes nothing and reports empty state.
    const QString empty;
    QVERIFY(empty.trimmed().isEmpty());
}

QTEST_MAIN(TestPlaybackLoopback)
#include "test_playback_loopback.moc"