// SPDX-License-Identifier: MIT
#include "ai_context.h"

#include "core/tokens.h"   // kAiContextBudgetRatio / window / tool-output trim sizes

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

int defaultContextBudgetTokens() {
    return static_cast<int>(tokens::kAiDefaultContextWindowTokens *
                            tokens::kAiContextBudgetRatio);
}

QList<ChatMessage> truncateLargeToolOutputs(const QList<ChatMessage>& history) {
    QList<ChatMessage> out = history;
    const int maxChars   = tokens::kAiToolOutputMaxChars;
    const int previewLen = tokens::kAiToolOutputPreviewChars;
    for (auto& m : out) {
        if (m.role != QLatin1String("tool")) continue;   // only tool results
        if (m.content.size() <= maxChars) continue;
        const int total = m.content.size();
        const QString head = m.content.left(previewLen);
        const QString tail = m.content.right(previewLen);
        m.content = QString::fromUtf8(
                       "[已截断 tool 输出：原 %1 字符，保留头尾预览]\n").arg(total)
                   + head + QLatin1String("\n...\n") + tail;
    }
    return out;
}

// Honest rule-based summary when no LLM summary is available: count the dropped
// rounds and quote EVERY user ask one-line each. Per context-compaction.md §6.3
// the OpenAI SDK explicitly EXCLUDES user messages from what it compacts; the
// folded summary must therefore preserve the user's asks (not just the first 3).
// Never invents content.
static QString ruleBasedSummary(const QList<ChatMessage>& oldMessages,
                                int compressedRounds) {
    QStringList asks;
    for (const auto& m : oldMessages) {
        if (m.role == "user") {
            QString s = m.content.trimmed();
            if (s.size() > 24) s = s.left(24) + QString::fromUtf8("…");
            asks << s;
        }
    }
    QString body =
        QString::fromUtf8("此前 %1 轮对话已压缩").arg(compressedRounds);
    if (!asks.isEmpty())
        body += QString::fromUtf8("：用户曾依次询问「%1」").arg(asks.join("」「"));
    body += QString::fromUtf8("；助手已相应回复，细节从略。");
    return body;
}

CompactResult compactContext(const QList<ChatMessage>& history,
                             const QString& systemPrompt,
                             const CompactOptions& opts,
                             SummaryFn summarize) {
    CompactResult out;

    // Resolve the effective budget: explicit override, else window * ratio.
    int budget = opts.budgetTokens;
    if (budget <= 0) {
        const int window = opts.contextWindowTokens > 0
                               ? opts.contextWindowTokens
                               : tokens::kAiDefaultContextWindowTokens;
        budget = static_cast<int>(window * tokens::kAiContextBudgetRatio);
    }

    // Pre-trim oversized tool results BEFORE the budget check (orthogonal light
    // trim). The history actually sent is the trimmed copy, not the raw one.
    const QList<ChatMessage> work = truncateLargeToolOutputs(history);

    int total = estimateTokens(systemPrompt);
    for (const auto& m : work) total += estimateTokens(m.content);

    out.messages.append(ChatMessage{"system", systemPrompt});

    if (total <= budget || work.isEmpty()) {
        out.messages.append(work);
        return out;
    }

    // Find the split: keep the last `keepRecentRounds` user turns verbatim.
    int userSeen = 0;
    int boundary = 0;  // default: compact everything (shouldn't normally happen)
    bool found = false;
    for (int i = work.size() - 1; i >= 0; --i) {
        if (work[i].role == "user") {
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
    for (int i = 0; i < work.size(); ++i) {
        if (i < boundary) oldMessages.append(work[i]);
        else recent.append(work[i]);
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
