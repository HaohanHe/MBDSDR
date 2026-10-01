// SPDX-License-Identifier: MIT
// Offscreen screenshot: the TaskStepsView EMBEDDED in the real MainWindow AI
// panel. Opens the AI tab, runs the real autonomous task button (driving the
// real orchestrator + engine + bookmark store), then grabs the AI tab page so
// the step list is shown in panel context. Not in ctest. Env: MBD_OUT.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QSettings>
#include <QTemporaryDir>
#include <QTabWidget>
#include <QPushButton>
#include <QComboBox>

#include "core/tokens.h"
#include "ui/main_window.h"

using namespace mbdsdr;

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QTemporaryDir tmp;
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope, tmp.path());
    app.setStyleSheet(tokens::buildDarkQss());

    const QString out = QString::fromLocal8Bit(qgetenv("MBD_OUT"));
    const QString mode = QString::fromLocal8Bit(qgetenv("MBD_MODE"));  // "sat" or sweep

    MainWindow win;
    win.resize(1280, 800);
    win.show();
    QApplication::processEvents();

    // Open the AI tab.
    auto* tabs = win.findChild<QTabWidget*>("rightTabs");
    QWidget* aiPage = nullptr;
    if (tabs) {
        for (int i = 0; i < tabs->count(); ++i) {
            if (tabs->tabText(i).contains(QString::fromUtf8("AI"))) {
                tabs->setCurrentIndex(i);
                aiPage = tabs->currentWidget();
                break;
            }
        }
    }
    QApplication::processEvents();

    // Optionally select the satellite template (lat/lon/sat-name inputs shown).
    if (mode == QLatin1String("sat")) {
        if (auto* combo = win.findChild<QComboBox*>("aiTemplateCombo"))
            combo->setCurrentIndex(3);   // 卫星过境接收
    }
    QApplication::processEvents();

    // Run the real autonomous task through the wired button.
    if (auto* btn = win.findChild<QPushButton*>("aiRunTaskBtn"))
        btn->click();
    QApplication::processEvents();

    QTimer::singleShot(400, [&]() {
        QWidget* page = aiPage ? aiPage : &win;
        page->resize(460, 860);
        QApplication::processEvents();
        QPixmap pm = page->grab();
        pm.save(out, "PNG");
        qInfo("task screenshot saved to %s (%dx%d)",
              out.toLocal8Bit().constData(), pm.width(), pm.height());
        app.quit();
    });
    return app.exec();
}
