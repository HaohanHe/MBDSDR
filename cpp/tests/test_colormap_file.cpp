// SPDX-License-Identifier: MIT
// Tool-side colormap file loader tests: read + parse + honest errors for the
// headless channels (Agent executor / ControlHub). Covers valid JSON stops,
// hex-array form, name capture, and every rejection path with a human-readable
// reason -- the set_color_map tool must never "succeed" on a broken file.
#include <QtTest/QtTest>
#include <QTemporaryDir>

#include "ui/spectrum_render.h"

using namespace mbdsdr;

class TestColormapFile : public QObject {
    Q_OBJECT
private slots:
    void stopsObjectParsesWithName();
    void hexArrayParses();
    void missingFileFailsHonest();
    void malformedJsonFailsHonest();
    void singleStopRejected();
};

void TestColormapFile::stopsObjectParsesWithName() {
    QTemporaryDir dir;
    QFile f(dir.filePath(QStringLiteral("ramp.json")));
    QVERIFY(f.open(QIODevice::WriteOnly));
    f.write("{\"name\":\"sunset\",\"stops\":[{\"t\":0,\"c\":\"#000000\"},"
            "{\"t\":1,\"c\":\"#ffffff\"}]}");
    f.close();
    ui::ParsedColormap parsed;
    QString err;
    QVERIFY2(ui::loadColormapFileForTool(f.fileName(), &parsed, &err),
             qPrintable(err));
    QCOMPARE(QString::fromStdString(parsed.name), QStringLiteral("sunset"));
    QCOMPARE(static_cast<int>(parsed.stops.size()), 2);
    QCOMPARE(parsed.stops.at(0).t, 0.0);
    QCOMPARE(parsed.stops.at(1).t, 1.0);
}

void TestColormapFile::hexArrayParses() {
    QTemporaryDir dir;
    QFile f(dir.filePath(QStringLiteral("arr.json")));
    QVERIFY(f.open(QIODevice::WriteOnly));
    f.write("[\"#ff0000\",\"#00ff00\",\"#0000ff\"]");
    f.close();
    ui::ParsedColormap parsed;
    QString err;
    QVERIFY2(ui::loadColormapFileForTool(f.fileName(), &parsed, &err),
             qPrintable(err));
    QCOMPARE(static_cast<int>(parsed.stops.size()), 3);
}

void TestColormapFile::missingFileFailsHonest() {
    ui::ParsedColormap parsed;
    QString err;
    QVERIFY(!ui::loadColormapFileForTool(QStringLiteral("/nonexistent/ramp.json"),
                                         &parsed, &err));
    QVERIFY2(err.contains(QStringLiteral("无法打开")), qPrintable(err));
}

void TestColormapFile::malformedJsonFailsHonest() {
    QTemporaryDir dir;
    QFile f(dir.filePath(QStringLiteral("bad.json")));
    QVERIFY(f.open(QIODevice::WriteOnly));
    f.write("{ not json !!");
    f.close();
    ui::ParsedColormap parsed;
    QString err;
    QVERIFY(!ui::loadColormapFileForTool(f.fileName(), &parsed, &err));
    QVERIFY2(err.contains(QStringLiteral("JSON")), qPrintable(err));
}

void TestColormapFile::singleStopRejected() {
    QTemporaryDir dir;
    QFile f(dir.filePath(QStringLiteral("one.json")));
    QVERIFY(f.open(QIODevice::WriteOnly));
    f.write("[\"#000000\"]");
    f.close();
    ui::ParsedColormap parsed;
    QString err;
    QVERIFY(!ui::loadColormapFileForTool(f.fileName(), &parsed, &err));
    QVERIFY2(err.contains(QStringLiteral("2 个")), qPrintable(err));
}

QTEST_MAIN(TestColormapFile)
#include "test_colormap_file.moc"
