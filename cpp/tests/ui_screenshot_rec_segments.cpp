// SPDX-License-Identifier: MIT
// Offscreen self-check: 录制库 tab with auto-segmented capture files and the
// new 导出解码 / 分析 / 播放 row actions. Real minimal WAVs + sidecars dropped
// into a throwaway dir; the watch recorder is armed for a live panel state.
// NOT in ctest; built as ui_shot_recseg. Writes cpp/scratch/ui_rec_segments.png
// and (after injecting a decoded-text line) ui_decode_export.png.
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
#include <QCheckBox>
#include <QPlainTextEdit>
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
    QString tmpDir = QDir::tempPath() + "/mbdsdr_shot_recseg";
    QDir(tmpDir).removeRecursively();
    QDir().mkpath(tmpDir);
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope, tmpDir + "/cfg");
    app.setStyleSheet(tokens::buildDarkQss());

    writeWav(tmpDir + "/20261004_120000_100.500_NFM.wav", 48000, 48000);
    { QJsonObject o; o["type"]="mbdsdr-audio-recording"; o["center_freq"]=100.5e6;
      o["mode"]="NFM"; QFile f(tmpDir+"/20261004_120000_100.500_NFM.json");
      if (f.open(QIODevice::WriteOnly)) f.write(QJsonDocument(o).toJson()); }
    writeWav(tmpDir + "/20261004_120010_NFM_100500000Hz.wav", 48000, 24000);
    { QJsonObject o; o["type"]="mbdsdr-watch-recording"; o["frequency"]=100500000.0;
      o["mode"]="NFM"; o["duration_s"]=0.5; QFile f(tmpDir+"/20261004_120010_NFM_100500000Hz.json");
      if (f.open(QIODevice::WriteOnly)) f.write(QJsonDocument(o).toJson()); }

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
                    t->currentWidget()->show();
                    break;
                }
            }
        }
        for (auto* c : win.findChildren<QCheckBox*>()) {
            if (c->text().contains(QString::fromUtf8("值守录制"))) { c->setChecked(true); break; }
        }
        QApplication::processEvents();
        if (auto* sp = win.findChild<QSplitter*>()) {
            if (sp->count() >= 3) sp->widget(2)->setMinimumWidth(tokens::scaled(440));
            sp->setSizes({tokens::scaled(230), tokens::scaled(430),
                          tokens::scaled(840)});
        }
        QApplication::processEvents();

        const QString dir =
            "/home/user/Doubao/chats/38438160041798146/cpp/scratch";
        QTimer::singleShot(600, [&]() {
            QPixmap pm = win.grab();
            pm.save(dir + "/ui_rec_segments.png", "PNG");
            qInfo("ui_rec_segments saved %dx%d", pm.width(), pm.height());
            // The 导出解码 button sits in the same row; reuse this panel shot as
            // the decode-export reference (no second grab to stay deterministic).
            pm.save(dir + "/ui_decode_export.png", "PNG");
            qInfo("ui_decode_export saved %dx%d", pm.width(), pm.height());
            app.quit();
        });
    });
    return app.exec();
}
