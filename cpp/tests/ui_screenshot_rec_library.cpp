// SPDX-License-Identifier: MIT
// Offscreen visual self-check: 录制库 tab listing REAL .wav captures + their
// sidecar proof, plus the live watch-recorder status block. NOT in ctest; built
// as ui_shot_reclib and run offscreen. Saves cpp/scratch/ui_rec_library.png.
// The listed files are real minimal 16-bit PCM WAVs + real sidecar JSONs written
// to a throwaway dir; the watch state comes from arming the real watch checkbox
// (honest "监听中（等待触发）"), never a fabricated status.
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
    QString tmpDir = QDir::tempPath() + "/mbdsdr_shot_reclib";
    QDir(tmpDir).removeRecursively();
    QDir().mkpath(tmpDir);
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope, tmpDir + "/cfg");
    app.setStyleSheet(tokens::buildDarkQss());

    // Drop two real captures into the throwaway record dir.
    writeWav(tmpDir + "/20260930_120000_100.500_NFM.wav", 48000, 48000);
    {
        QJsonObject o;
        o["type"] = "mbdsdr-audio-recording"; o["center_freq"] = 100.5e6;
        o["mode"] = "NFM"; o["datetime"] = "2026-09-30T12:00:00Z";
        QFile f(tmpDir + "/20260930_120000_100.500_NFM.json");
        if (f.open(QIODevice::WriteOnly)) f.write(QJsonDocument(o).toJson());
    }
    writeWav(tmpDir + "/20260930_120010_NFM_100500000Hz.wav", 48000, 24000);
    {
        QJsonObject o;
        o["type"] = "mbdsdr-watch-recording"; o["frequency"] = 100500000.0;
        o["mode"] = "NFM"; o["duration_s"] = 0.5;
        o["trigger_threshold_db"] = -50.0; o["start_time"] = "2026-09-30T12:00:10Z";
        o["is_hardware"] = false;
        QFile f(tmpDir + "/20260930_120010_NFM_100500000Hz.json");
        if (f.open(QIODevice::WriteOnly)) f.write(QJsonDocument(o).toJson());
    }

    MainWindow win;
    win.resize(tokens::scaled(1500), tokens::scaled(900));
    win.show();

    QTimer::singleShot(600, [&]() {
        // Point the engine at our throwaway dir, then rescan.
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
        // Arm the real watch recorder so the panel shows a live state.
        for (auto* c : win.findChildren<QCheckBox*>()) {
            if (c->text().contains(QString::fromUtf8("值守录制"))) { c->setChecked(true); break; }
        }
        QApplication::processEvents();
        // Force the right dock wide enough to show the list + button row.
        if (auto* sp = win.findChild<QSplitter*>()) {
            if (sp->count() >= 3) sp->widget(2)->setMinimumWidth(tokens::scaled(420));
            sp->setSizes({tokens::scaled(230), tokens::scaled(430),
                          tokens::scaled(840)});
        }
        QApplication::processEvents();

        QTimer::singleShot(600, [&]() {
            const QString out =
                "/home/user/Doubao/chats/38438160041798146/cpp/scratch/ui_rec_library.png";
            QPixmap pm = win.grab();
            pm.save(out, "PNG");
            qInfo("ui_rec_library saved %dx%d -> %s", pm.width(), pm.height(),
                  qPrintable(out));
            app.quit();
        });
    });
    return app.exec();
}
