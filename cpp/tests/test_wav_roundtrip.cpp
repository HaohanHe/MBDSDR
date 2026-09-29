// SPDX-License-Identifier: MIT
// WAV roundtrip: start engine with TestSignalSource, record demod audio to a
// temp WAV, stop, read back header. Verifies sample rate=48000, channels,
// and data length>0. No hardware, no network.
#include <QtTest/QtTest>
#include <QFile>
#include <QDir>
#include <QDataStream>
#include <QJsonDocument>
#include <QJsonObject>
#include "dsp/spectrum_engine.h"

using namespace mbdsdr::dsp;

class TestWavRoundtrip : public QObject {
    Q_OBJECT
private slots:
    void writesValidWav();
    void writesSidecarJson();
};

static quint16 rd16(const uchar* p) { return quint16(p[0]) | (quint16(p[1])<<8); }
static quint32 rd32(const uchar* p) {
    return quint32(p[0]) | (quint32(p[1])<<8) | (quint32(p[2])<<16) | (quint32(p[3])<<24);
}

void TestWavRoundtrip::writesValidWav() {
    QDir().mkpath("recordings");
    SpectrumEngine eng;
    eng.setRecTarget(RecTarget::DemodAudio);
    eng.setDemodMode("NFM");
    eng.start();
    QTest::qWait(400);  // let the run loop fill audio buffers

    QVERIFY(eng.startRecording());
    QString path = eng.recordingPath();
    QTest::qWait(400);  // accumulate audio
    eng.stopRecording();
    eng.shutdown();
    eng.wait(2000);

    QFile f(path);
    QVERIFY(f.exists());
    QVERIFY(f.size() > 44);
    QVERIFY(f.open(QIODevice::ReadOnly));
    QByteArray h = f.read(44);
    QCOMPARE(rd32(reinterpret_cast<const uchar*>(h.data())+0), 0x46464952u);  // "RIFF"
    QCOMPARE(rd32(reinterpret_cast<const uchar*>(h.data())+8),  0x45564157u);  // "WAVE"
    QCOMPARE(rd32(reinterpret_cast<const uchar*>(h.data())+12), 0x20746d66u);  // "fmt "
    QCOMPARE(rd16(reinterpret_cast<const uchar*>(h.data())+20), 1u);            // PCM
    quint16 ch = rd16(reinterpret_cast<const uchar*>(h.data())+22);
    QVERIFY(ch==1 || ch==2);
    quint32 sr = rd32(reinterpret_cast<const uchar*>(h.data())+24);
    QCOMPARE(sr, 48000u);
    quint32 dataLen = rd32(reinterpret_cast<const uchar*>(h.data())+40);
    QVERIFY(dataLen > 0);
    f.remove();
}

void TestWavRoundtrip::writesSidecarJson() {
    // Demod-audio recording must drop a sibling .json proof carrying the readback
    // sample_rate / center_freq / gain (and hardware/mode). No hardware: the
    // values are the honest test-source readbacks.
    QDir().mkpath("recordings");
    SpectrumEngine eng;
    eng.setRecTarget(RecTarget::DemodAudio);
    eng.setDemodMode("NFM");
    eng.start();
    QTest::qWait(400);

    QVERIFY(eng.startRecording());
    const QString wavPath = eng.recordingPath();
    QTest::qWait(400);
    eng.stopRecording();

    QString jsonPath = wavPath;
    QVERIFY(jsonPath.endsWith(".wav"));
    jsonPath.chop(4);
    jsonPath += ".json";

    QFile jf(jsonPath);
    QVERIFY2(jf.exists(), "a sidecar .json must sit next to the recorded WAV");
    QVERIFY(jf.open(QIODevice::ReadOnly));
    QJsonParseError perr{};
    QJsonDocument doc = QJsonDocument::fromJson(jf.readAll(), &perr);
    QCOMPARE(perr.error, QJsonParseError::NoError);
    QVERIFY(doc.isObject());
    const QJsonObject o = doc.object();

    // The three required readback fields must be present and numeric.
    QVERIFY(o.contains("sample_rate"));
    QVERIFY(o.contains("center_freq"));
    QVERIFY(o.contains("gain"));
    QCOMPARE(o.value("sample_rate").toDouble(), 48000.0);
    QVERIFY(o.value("center_freq").toDouble() > 0.0);
    QVERIFY(o.value("gain").toDouble() >= 0.0);
    // Hardware + mode provenance.
    QVERIFY(o.contains("hardware"));
    QCOMPARE(o.value("mode").toString(), QStringLiteral("NFM"));

    eng.shutdown();
    eng.wait(2000);
    QFile::remove(wavPath);
    QFile::remove(jsonPath);
}

QTEST_MAIN(TestWavRoundtrip)
#include "test_wav_roundtrip.moc"
