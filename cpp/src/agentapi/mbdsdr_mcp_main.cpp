// SPDX-License-Identifier: MIT
// mbdsdr-mcp: headless stdio JSON-RPC 2.0 (MCP) server entry point.
//
// Reads one JSON-RPC request per stdin line, hands it to
// agentapi::handleRequestLine(), and writes one JSON-RPC response line per
// stdout line (flushed). Errors/parse logs go to stderr only.
//
// This binary has NO model/LLM dependency: it only exposes the declarative
// tool registry through real dispatch against a headless SpectrumEngine. It
// never reads an API key or issues a chat request — the model is whatever
// external agent (Claude Desktop / Cursor / generic MCP client) drives this
// process over stdio.
//
// GUI choice: SpectrumEngine is a QThread and its AudioOutput is a plain
// QObject; no widget/event-loop GUI is needed, so we use QCoreApplication.
// We still force offscreen + null-audio BEFORE constructing the engine so a
// headless launch never tries to open a real render device. If a future
// change makes engine construction require a QGuiApplication, switch this to
// QApplication with QT_QPA_PLATFORM=offscreen (already set below).

#include <QCoreApplication>
#include <QTextStream>
#include <QFile>

#include "agentapi/mcp_server.h"
#include "dsp/spectrum_engine.h"

namespace {

void printUsage() {
    QTextStream out(stdout);
    out << "mbdsdr-mcp — headless MCP (JSON-RPC 2.0 over stdio) server\n"
           "\n"
           "Protocol: one JSON-RPC 2.0 request per stdin line; one response\n"
           "JSON object per stdout line (newline-terminated). Errors go to\n"
           "stderr only.\n"
           "\n"
           "Methods:\n"
           "  initialize                 handshake -> serverInfo.name=mbdsdr-core\n"
           "  notifications/initialized   no reply\n"
           "  tools/list                 -> {tools:[{name,description,inputSchema}]}\n"
           "  tools/call                 -> {content:[{type:text,text:<json>}]}\n"
           "\n"
           "Write-tool manual gate: tools/call on a write-class tool requires\n"
           "arguments.arm_manual == true to really execute; otherwise the tool\n"
           "is NOT run and a {gated:true,...} envelope is returned.\n"
           "\n"
           "No model / API key is used by this binary. Environment:\n"
           "  QT_QPA_PLATFORM=offscreen, MBDSDR_NULL_AUDIO=1 are set\n"
           "automatically for headless operation.\n";
}

} // namespace

int main(int argc, char** argv) {
    // Force headless audio/platform BEFORE any Qt object that might probe the
    // platform. QCoreApplication needs no GUI platform plugin, but setting
    // offscreen keeps SpectrumEngine's audio path on NullAudioSink.
    qputenv("QT_QPA_PLATFORM", "offscreen");
    qputenv("MBDSDR_NULL_AUDIO", "1");

    QCoreApplication app(argc, argv);

    for (int i = 1; i < argc; ++i) {
        const QString a = QString::fromLocal8Bit(argv[i]);
        if (a == QLatin1String("--help") || a == QLatin1String("-h")) {
            printUsage();
            return 0;
        }
    }

    // Headless receiver: no hardware -> honest NullSource (no fabricated IQ),
    // null audio sink. nullptr bookmarks keeps bookmark tools honest-empty.
    mbdsdr::dsp::SpectrumEngine engine;

    QTextStream in(stdin);
    QTextStream out(stdout);   // Qt6 default codec is UTF-8

    while (true) {
        const QString line = in.readLine();
        if (line.isNull()) break;   // EOF on stdin -> graceful shutdown
        const QString reply =
            mbdsdr::agentapi::handleRequestLine(line, &engine, nullptr);
        if (!reply.isEmpty()) {
            out << reply << QLatin1Char('\n');
            out.flush();
        }
    }
    return 0;
}
