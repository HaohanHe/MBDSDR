// SPDX-License-Identifier: MIT
#include "ai_context.h"

#include <QString>

namespace mbdsdr {
namespace ai {

int estimateTokens(const QString& text) {
    int cjk = 0;
    int other = 0;
    for (QChar c : text) {
        const uint u = c.unicode();
        // CJK Unified Ideographs / Hiragana / Katakana / Hangul + CJK punct.
        const bool isCjk =
            (u >= 0x3000 && u <= 0x303F) ||   // CJK symbols/punct
            (u >= 0x3040 && u <= 0x30FF) ||   // hiragana/katakana
            (u >= 0x3400 && u <= 0x4DBF) ||   // ext A
            (u >= 0x4E00 && u <= 0x9FFF) ||   // unified ideographs
            (u >= 0xAC00 && u <= 0xD7AF) ||   // hangul
            (u >= 0xF900 && u <= 0xFAFF);
        if (isCjk) ++cjk;
        else ++other;
    }
    // ~4 Latin chars per token; CJK ~1 char per token.
    return cjk + (other + 3) / 4;
}

// Honest rule-based summary when no LLM summary is available: count the
// dropped rounds and quote the first few user asks. Never invents content.
static QString ruleBasedSummary(const QList<ChatMessage>& oldMessages,
                                int compressedRounds) {
    QStringList asks;
    for (const auto& m : oldMessages) {
        if (m.role == "user" && asks.size() < 3) {
            QString s = m.content.trimmed();
            if (s.size() > 24) s = s.left(24) + "…";
            asks << s;
        }
    }
    QString body =
        QString::fromUtf8("此前 %1 轮对话已压缩").arg(compressedRounds);
    if (!asks.isEmpty())
        body += QString::fromUtf8("：用户曾询问「%1」").arg(asks.join("」「"));
    body += QString::fromUtf8("；助手已相应回复，细节从略。");
    return body;
}

CompactResult compactContext(const QList<ChatMessage>& history,
                             const QString& systemPrompt,
                             const CompactOptions& opts,
                             SummaryFn summarize) {
    CompactResult out;

    int total = estimateTokens(systemPrompt);
    for (const auto& m : history) total += estimateTokens(m.content);

    out.messages.append(ChatMessage{"system", systemPrompt});

    if (total <= opts.budgetTokens || history.isEmpty()) {
        out.messages.append(history);
        return out;
    }

    // Find the split: keep the last `keepRecentRounds` user turns verbatim.
    int userSeen = 0;
    int boundary = 0;  // default: compact everything (shouldn't normally happen)
    bool found = false;
    for (int i = history.size() - 1; i >= 0; --i) {
        if (history[i].role == "user") {
            ++userSeen;
            if (userSeen >= opts.keepRecentRounds) {
                boundary = i;
                found = true;
                break;
            }
        }
    }
    if (!found) boundary = 0;  // fewer rounds than keep: keep all, nothing drops

    QList<ChatMessage> oldMessages, recent;
    for (int i = 0; i < history.size(); ++i) {
        if (i < boundary) oldMessages.append(history[i]);
        else recent.append(history[i]);
    }

    int compressedRounds = 0;
    for (const auto& m : oldMessages)
        if (m.role == "user") ++compressedRounds;

    if (oldMessages.isEmpty()) {
        out.messages.append(recent);
        return out;
    }

    // Produce the summary: real LLM summary if the injected callback gives
    // one back; otherwise the honest rule-based count summary.
    QString summaryText;
    if (summarize) {
        summaryText = summarize(oldMessages).trimmed();
    }
    if (summaryText.isEmpty())
        summaryText = ruleBasedSummary(oldMessages, compressedRounds);

    ChatMessage sum;
    sum.role = "summary";
    sum.content = summaryText;
    out.messages.append(sum);
    out.messages.append(recent);

    out.didCompact = true;
    out.compressedRounds = compressedRounds;
    out.summaryText = summaryText;
    return out;
}

} // namespace ai
} // namespace mbdsdr
