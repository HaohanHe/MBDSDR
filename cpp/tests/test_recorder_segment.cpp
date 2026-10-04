// SPDX-License-Identifier: MIT
// Real on-disk tests for continuous-capture auto-segmentation: once a Recorder
// exceeds its sample cap it must finalise the current SigMF capture and open a
// FRESH collision-avoided file, without truncating the previous segment. No
// hardware, no wall-clock dependency (the cap is in samples).
#include <QtTest/QtTest>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <vector>
#include <complex>

#include "dsp/recorder.h"

using namespace mbdsdr::dsp;

class TestRecorderSegment : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void splitsAtSampleCap();
    void collisionAvoidedNaming();
    void unlimitedStaysSingleFile();
private:
    QString dir_;
};

void TestRecorderSegment::initTestCase() {
    dir_ = QDir::tempPath() + "/mbdsdr_recseg_test";
    QDir(dir_).removeRecursively();
    QDir().mkpath(dir_);
}

static std::vector<std::complex<float>> block(std::size_t n) {
    return std::vector<std::complex<float>>(n, {0.1f, 0.0f});
}

void TestRecorderSegment::splitsAtSampleCap() {
    Recorder rec;
    // 0.1 s @ 48 kHz cap = 4800 samples.
    rec.setSegmentContext(dir_, 100e6, 10.0, "test");
    rec.setMaxSegmentSeconds(0.1);
    QVERIFY(rec.start(dir_, 48000.0, 100e6, 10.0, "test"));
    const QString firstPath = rec.currentFilePath();
    QCOMPARE(rec.segmentCount(), 1);

    // Push 0.25 s worth of samples -> must rotate at least once (>=2 segments).
    for (int i = 0; i < 25; ++i) rec.writeIQ(block(480));   // 10 ms each
    QVERIFY(rec.takeSegmentRotated());
    QVERIFY2(rec.segmentCount() >= 2,
             qPrintable(QString("expected rotation, segments=%1").arg(rec.segmentCount())));
    rec.stop();

    // On-disk: at least two .sigmf-data files, each with a valid meta whose
    // num_samples matches the real bytes.
    QStringList dataFiles;
    for (const auto& fi : QDir(dir_).entryInfoList({"*.sigmf-data"}))
        dataFiles << fi.absoluteFilePath();
    QVERIFY2(dataFiles.size() >= 2,
             qPrintable(QString("expected >=2 segment files, got %1").arg(dataFiles.size())));

    qint64 totalBytes = 0;
    for (const QString& df : dataFiles) {
        QFile data(df); data.open(QIODevice::ReadOnly);
        totalBytes += data.size();
        const QString meta = df;   // strip .sigmf-data -> .sigmf-meta
        QFile mf(meta.chopped(QString(".sigmf-data").size()) + ".sigmf-meta");
        QVERIFY(mf.open(QIODevice::ReadOnly));
        QJsonParseError pe;
        auto g = QJsonDocument::fromJson(mf.readAll(), &pe).object().value("global").toObject();
        QCOMPARE(pe.error, QJsonParseError::NoError);
        const qint64 n = g.value("core:num_samples").toVariant().toLongLong();
        QVERIFY2(n > 0, "every segment meta must carry a real num_samples");
        QCOMPARE(n * 8, data.size());   // cf32_le = 8 bytes/frame
    }
    QVERIFY(totalBytes > 4800 * 8);      // actually wrote > one cap
}

void TestRecorderSegment::collisionAvoidedNaming() {
    // Two separate start/stop bursts in the SAME second on the SAME frequency
    // must NOT truncate each other (the existing _2/_3 disambiguation).
    Recorder a;
    a.setSegmentContext(dir_, 200e6, 10.0, "test");
    QVERIFY(a.start(dir_, 48000.0, 200e6, 10.0, "test"));
    a.writeIQ(block(1000));
    const QString pathA = a.currentFilePath();
    a.stop();

    Recorder b;
    b.setSegmentContext(dir_, 200e6, 10.0, "test");
    QVERIFY(b.start(dir_, 48000.0, 200e6, 10.0, "test"));
    const QString pathB = b.currentFilePath();
    b.writeIQ(block(1000));
    b.stop();

    QVERIFY2(pathA != pathB, "same-second same-freq recordings must get distinct names");
    QVERIFY(QFileInfo::exists(pathA));
    QVERIFY(QFileInfo::exists(pathB));
}

void TestRecorderSegment::unlimitedStaysSingleFile() {
    Recorder rec;
    rec.setSegmentContext(dir_, 300e6, 10.0, "test");
    rec.setMaxSegmentSeconds(0.0);     // disabled
    QVERIFY(rec.start(dir_, 48000.0, 300e6, 10.0, "test"));
    const QString p = rec.currentFilePath();
    for (int i = 0; i < 50; ++i) rec.writeIQ(block(480));
    rec.stop();
    QVERIFY(!rec.takeSegmentRotated());
    QCOMPARE(rec.segmentCount(), 1);
    QVERIFY(QFileInfo::exists(p));
}

QTEST_MAIN(TestRecorderSegment)
#include "test_recorder_segment.moc"
