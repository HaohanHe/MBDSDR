// SPDX-License-Identifier: MIT
// Offscreen visual self-check: 录制库 tab + the 离线分析 group opened on a REAL
// 16-bit PCM WAV (written to a throwaway dir, sidecar JSON included). The file is
// streamed through the real engine DSP chain; the panel shows the real path,
// pause/seek controls and a live 0.x s / 1.0 s position. NOT in ctest; built as
// ui_shot_fileanalysis, run offscreen. Saves cpp/scratch/ui_file_analysis.png.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QTabWidget>
#include <QSplitter>
#include <QSettings>
#include <QDir>
#include <QFile>
#include <QDataStream>
#include <QJsonDocument>
#include <QJsonObject>
#include <QtEndian>
#include <QDebug>

#include "core/tokens.h"
#include "ui/main_window.h"
#include "dsp/spectrum_engine.h"

using namespace mbdsdr;

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
    for (int i = 0; i < samples; ++i) ds << static_cast<qint16>((i % 2000) - 1000);
}

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QString tmpDir = QDir::tempPath() + "/mbdsdr_shot_fileana";
    QDir(tmpDir).removeRecursively();
    QDir().mkpath(tmpDir);
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope, tmpDir + "/cfg");
    app.setStyleSheet(tokens::buildDarkQss());

    const QString wav = tmpDir + "/20261001_093000_100.500_NFM.wav";
    writeWav(wav, 48000, 48000);
    {
        QJsonObject o;
        o["type"] = "mbdsdr-audio-recording"; o["center_freq"] = 100.5e6;
        o["mode"] = "NFM"; o["datetime"] = "2026-10-01T09:30:00Z";
        QFile f(tmpDir + "/20261001_093000_100.500_NFM.json");
        if (f.open(QIODevice::WriteOnly)) f.write(QJsonDocument(o).toJson());
    }

    MainWindow win;
    win.resize(tokens::scaled(1500), tokens::scaled(900));
    win.show();

    QTimer::singleShot(600, [&]() {
        win.engine()->setRecordingDir(tmpDir);
        win.refreshRecordingLibrary();

        for (auto* t : win.findChildren<QTabWidget*>()) {
            for (int i = 0; i < t->count(); ++i) {
                if (t->tabText(i) == QString::fromUtf8("录制库")) {
                    t->setCurrentIndex(i);
                    break;
                }
            }
        }
        // Open the real WAV through the engine offline-analysis path.
        win.engine()->openOfflineFile(wav);
        if (auto* sp = win.findChild<QSplitter*>()) {
            if (sp->count() >= 3) sp->widget(2)->setMinimumWidth(tokens::scaled(440));
            sp->setSizes({tokens::scaled(230), tokens::scaled(430),
                          tokens::scaled(840)});
        }
        QApplication::processEvents();

        QTimer::singleShot(500, [&]() {
            const QString out =
                "/home/user/Doubao/chats/38438160041798146/cpp/scratch/ui_file_analysis.png";
            QPixmap pm = win.grab();
            pm.save(out, "PNG");
            qInfo("ui_file_analysis saved %dx%d -> %s", pm.width(), pm.height(),
                  qPrintable(out));
            app.quit();
        });
    });
    return app.exec();
}
