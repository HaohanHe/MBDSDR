// SPDX-License-Identifier: MIT
// Bundled waterfall colormaps (B3): every res/colormaps/*.json the build ships
// must parse through the same loadColormapFileForTool gate the runtime menu
// uses -- a bundled file that fails here is a broken menu entry in the product.
// Also pins the bundledColormapFiles directory probe (sorted, absent -> empty).
#include <QtTest/QtTest>
#include <QTemporaryDir>

#include "ui/spectrum_render.h"

#ifndef MBDSDR_COLORMAP_SRC_DIR
#define MBDSDR_COLORMAP_SRC_DIR "res/colormaps"
#endif

using namespace mbdsdr;

class TestBundledColormaps : public QObject {
    Q_OBJECT
private slots:
    void everyBundledFileParses();
    void bundledStopsWellFormed();
    void probeListsSortedJsons();
    void probeAbsentDirIsEmpty();
};

void TestBundledColormaps::everyBundledFileParses() {
    const QDir dir(QStringLiteral(MBDSDR_COLORMAP_SRC_DIR));
    QVERIFY2(dir.exists(), "res/colormaps must exist in the source tree");
    const QStringList files =
        dir.entryList({QStringLiteral("*.json")}, QDir::Files, QDir::Name);
    QVERIFY2(files.size() >= 14,
             qPrintable(QStringLiteral("expected >=14 bundled colormaps, got %1")
                            .arg(files.size())));
    for (const QString& f : files) {
        ui::ParsedColormap cm;
        QString err;
        QVERIFY2(ui::loadColormapFileForTool(dir.absoluteFilePath(f), &cm, &err),
                 qPrintable(f + QStringLiteral(": ") + err));
        QVERIFY2(!QString::fromStdString(cm.name).isEmpty(),
                 qPrintable(f + QStringLiteral(": missing display name")));
    }
}

void TestBundledColormaps::bundledStopsWellFormed() {
    const QDir dir(QStringLiteral(MBDSDR_COLORMAP_SRC_DIR));
    const QStringList files =
        dir.entryList({QStringLiteral("*.json")}, QDir::Files, QDir::Name);
    for (const QString& f : files) {
        ui::ParsedColormap cm;
        QString err;
        QVERIFY2(ui::loadColormapFileForTool(dir.absoluteFilePath(f), &cm, &err),
                 qPrintable(f));
        QVERIFY2(cm.stops.size() >= 2, qPrintable(f));
        QCOMPARE(cm.stops.front().t, 0.0);   // evenly spaced list starts at 0
        QCOMPARE(cm.stops.back().t, 1.0);    // ... and ends at 1
        for (const auto& s : cm.stops)
            QVERIFY2(s.t >= 0.0 && s.t <= 1.0, qPrintable(f));
    }
}

void TestBundledColormaps::probeListsSortedJsons() {
    QTemporaryDir tmp;
    QVERIFY(tmp.isValid());
    QDir(tmp.filePath(QStringLiteral("colormaps"))).mkpath(QStringLiteral("."));
    for (const char* name : {"b.json", "a.json", "c.json"}) {
        QFile f(tmp.filePath(QStringLiteral("colormaps/")) + QString::fromLatin1(name));
        QVERIFY(f.open(QIODevice::WriteOnly));
        f.write("[\"#000000\",\"#ffffff\"]");
    }
    const QStringList got = ui::bundledColormapFiles(tmp.path());
    QCOMPARE(got.size(), 3);
    QVERIFY(got.at(0).endsWith(QStringLiteral("/a.json")));
    QVERIFY(got.at(2).endsWith(QStringLiteral("/c.json")));
}

void TestBundledColormaps::probeAbsentDirIsEmpty() {
    QTemporaryDir tmp;
    QVERIFY(tmp.isValid());
    QCOMPARE(ui::bundledColormapFiles(tmp.path()).size(), 0);
    QCOMPARE(ui::bundledColormapFiles(QString()).size(), 0);
}

QTEST_MAIN(TestBundledColormaps)
#include "test_bundled_colormaps.moc"
