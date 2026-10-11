// SPDX-License-Identifier: MIT
// Headless MCP (Model Context Protocol) JSON-RPC 2.0 server — line-oriented
// over stdio, no GUI, no model/LLM dependency.
//
// Protocol shape mirrors the Python reference (mbdsdr_ai/core/mcp_server.py):
//   * one JSON-RPC request per stdin line; one response JSON object per stdout
//     line (plus '\n'); errors/log go to stderr so the protocol stream on
//     stdout is never polluted.
//   * methods:
//       initialize                 -> handshake result
//       notifications/initialized  -> notification, no reply
//       tools/list                 -> {tools:[{name,description,inputSchema}]}
//       tools/call                 -> {content:[{type:"text",text:<json>}]}
//   * unknown method -> JSON-RPC error {-32601, "未知方法: <m>"}; malformed
//     JSON -> logged to stderr, no reply (aligned with the Python loop).
//
// Tool table single source of truth: ai::toolDefs() (the same declarative
// registry the desktop AI path uses). There is deliberately no second,
// hand-maintained tool list here.
//
// Write-tool manual gate: when tools/call targets a write-class tool
// (ai::isWriteTool(name)), the caller must pass arguments.arm_manual == true
// for the action to really execute; otherwise ai::gatedToolResult(name) is
// returned as the text payload and the tool is NOT executed. arm_manual is
// stripped from arguments before real dispatch so it never leaks into the
// underlying executor.
//
// Zero model dependency: this translation unit only calls the declarative
// tool registry / dispatch (ai::toolDefs / executeTool / isWriteTool /
// gatedToolResult). It never instantiates an LLM client, reads an API key, or
// issues a chat completion. (agent_tools.h transitively includes llm_client.h
// for the ToolDef type — that is a type-only include; no LLM path is invoked.)
#pragma once

#include <QString>

namespace mbdsdr {
namespace dsp { class SpectrumEngine; }
namespace ui  { class BookmarkManager; }

namespace agentapi {

// Pure, testable one-line handler.
//   engine: receiver used for real tool dispatch. nullptr is allowed — tools
//     that need a receiver then return their honest null-engine error envelope
//     through ai::executeTool (never a fabricated value, never a crash).
//   bm: optional injected bookmark store, forwarded verbatim to executeTool.
// Returns the response line to write to stdout (WITHOUT trailing newline),
// or an empty string to mean "do not write stdout" (notifications, malformed
// JSON already logged to stderr by the caller).
QString handleRequestLine(const QString& line,
                          dsp::SpectrumEngine* engine,
                          ui::BookmarkManager* bm = nullptr);

} // namespace agentapi
} // namespace mbdsdr
