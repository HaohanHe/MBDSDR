// SPDX-License-Identifier: MIT
// Offscreen screenshot: AI multi-session panel with the 〔已摘要〕 annotation
// and a streaming transient line. Not in ctest. Env: MBD_OUT (png path).
// No network: the messages are written through the real AiSessionStore API
// (the same path the app uses), and the transient line is driven by the real
// Agent::partialReady signal.
#include <QApplication>
#include <QTimer>
#include <QPixmap>
#include <QSettings>
#include <QDir>
#include <QTabWidget>
#include <QSplitter>
#include <QPlainTextEdit>
#include <QScrollBar>
#include <cstdlib>

#include "core/tokens.h"
#include "ui/main_window.h"
#include "ai/agent.h"
#include "ai/ai_session_store.h"

using namespace mbdsdr;

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QSettings::setPath(QSettings::NativeFormat, QSettings::UserScope,
                       QDir::tempPath() + "/mbdsdr_shot_ai_" +
                           QString::number(QCoreApplication::applicationPid()));
    // Throwaway session dir so the user's real sessions are never touched.
    const QString sessDir = QString::fromLocal8Bit(qgetenv("MBDSDR_SCRATCH_DIR"))
                                .append("/ai_sessions");
    QDir(sessDir).removeRecursively();   // deterministic: start from one empty session
    qputenv("MBDSDR_AI_SESSIONS_DIR", sessDir.toLocal8Bit());
    app.setStyleSheet(tokens::buildDarkQss());

    const QString out = QString::fromLocal8Bit(qgetenv("MBD_OUT"));

    MainWindow win;
    win.resize(1280, 800);
    win.show();
    QApplication::processEvents();

    // Give the right rail (AI panel) enough width so the session switcher and
    // chat lines are not clipped; the spectrum keeps its minimum.
    if (auto* split = win.findChild<QSplitter*>())
        split->setSizes({260, 560, 460});
    QApplication::processEvents();

    auto* store = win.aiSessionStore();
    // Populate the default (first) session through the real store API.
    const QString cur = store->currentId();
    store->appendMessage(cur, ai::SessionMessage{"user", "98.5"});
    store->appendMessage(cur, ai::SessionMessage{"assistant", "已调谐到 98.500 MHz（本地指令）"});
    store->appendMessage(cur, ai::SessionMessage{"summary",
        "此前 2 轮对话已压缩：用户曾询问「扫描 88–108 MHz」；助手已相应回复，细节从略。"});
    store->appendMessage(cur, ai::SessionMessage{"user", "切到 WFM"});
    store->appendMessage(cur, ai::SessionMessage{"assistant", "模式切换为 WFM（本地指令）"});
    // A second session in the switcher.
    store->createSession(QString::fromUtf8("扫描计划"));
    store->appendMessage(store->currentId(), ai::SessionMessage{"user", "记录今天的扫描结果"});

    // Switch back to the first session so the 〔已摘要〕 marker is on screen.
    store->setCurrent(cur);

    // Show the AI tab.
    auto* tabs = win.findChild<QTabWidget*>("rightTabs");
    if (tabs) {
        for (int i = 0; i < tabs->count(); ++i) {
            if (tabs->tabText(i).contains(QString::fromUtf8("AI"))) {
                tabs->setCurrentIndex(i);
                break;
            }
        }
    }
    QApplication::processEvents();

    // Drive a streaming transient line through the real partialReady signal so
    // the "AI: … ▌" state is visible.
    if (auto* agent = win.findChild<ai::Agent*>()) {
        QMetaObject::invokeMethod(agent, "partialReady", Qt::DirectConnection,
            Q_ARG(QString, QString::fromUtf8("正在分析当前频段，列出可用解调模式…")));
    }
    QApplication::processEvents();

    QTimer::singleShot(300, [&]() {
        // Grab the AI tab page itself at a comfortable width so the session
        // switcher + chat are never clipped (the main window's splitter keeps
        // the right rail narrow for the live layout; this is a panel shot).
        QWidget* page = tabs ? tabs->currentWidget() : nullptr;
        if (!page) page = &win;
        page->resize(460, 820);
        QApplication::processEvents();
        QPlainTextEdit* chat = page->findChild<QPlainTextEdit*>("aiChat");
        if (chat) chat->verticalScrollBar()->setValue(chat->verticalScrollBar()->minimum());
        QApplication::processEvents();
        QPixmap pm = page->grab();
        pm.save(out, "PNG");
        qInfo("ai sessions screenshot saved to %s (%dx%d)",
              out.toLocal8Bit().constData(), pm.width(), pm.height());
        app.quit();
    });
    return app.exec();
}
