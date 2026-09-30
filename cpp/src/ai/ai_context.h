// SPDX-License-Identifier: MIT
#pragma once

#include "llm_client.h"
#include <QString>
#include <QList>
#include <functional>

namespace mbdsdr {
namespace ai {

// =====================================================================
// Context-window budgeting. Named constants -- no magic numbers at the
// call sites. These are APPROXIMATE (no tokenizer); the goal is to keep
// the wire payload bounded, not to match a specific model's exact count.
// =====================================================================
// Hard ceiling for the assembled prompt (system + history). Once the
// approximate count crosses this, early turns are folded into one summary.
inline constexpr int kAiContextBudgetTokens = 8192;
// How many most-recent user/assistant rounds are kept verbatim (a round =
// one user turn and the assistant reply / tool turns that follow it).
inline constexpr int kAiContextKeepRecentRounds = 4;

// Approximate token count of a piece of text. CJK runs ~1 token/char;
// Latin/digit runs ~4 chars/token. Good enough to trigger compaction.
int estimateTokens(const QString& text);

struct CompactOptions {
    int budgetTokens = kAiContextBudgetTokens;
    int keepRecentRounds = kAiContextKeepRecentRounds;
};

struct CompactResult {
    // Ready-to-send messages: [system] [optional summary] [recent...].
    // The summary entry has role == "summary" (the LLM wire layer maps it to a
    // system note; the UI renders it as the restrained 〔已摘要〕 marker).
    QList<ChatMessage> messages;
    bool didCompact = false;
    int compressedRounds = 0;
    QString summaryText;
};

// Produce a summary for the messages that are about to be dropped. Returning
// an empty string means "no LLM summary available" and compactContext then
// falls back to an honest rule-based count summary.
using SummaryFn = std::function<QString(const QList<ChatMessage>& oldMessages)>;

// Pure function: decide whether `history` (WITHOUT the system prompt) fits
// inside `opts.budgetTokens` once `systemPrompt` is prepended. If it fits,
// returns it verbatim. If not, keeps the last `keepRecentRounds` user turns
// verbatim and folds everything earlier into one summary entry.
//
// The summary is produced by `summarize` when provided and non-empty;
// otherwise an honest rule-based summary is generated (count + first asks).
CompactResult compactContext(const QList<ChatMessage>& history,
                             const QString& systemPrompt,
                             const CompactOptions& opts = {},
                             SummaryFn summarize = nullptr);

} // namespace ai
} // namespace mbdsdr
