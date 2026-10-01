// SPDX-License-Identifier: MIT
// Deterministic unit tests for M3 dual-protocol layer. No network, no GUI.
#include <QtTest/QtTest>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>

#include "ai/llm_protocol.h"

using namespace mbdsdr::ai;

class TestLlmProtocol : public QObject {
    Q_OBJECT
private slots:
    // --- OpenAI non-streaming response parsing ---
    void testOpenAiParseContentReasoningToolCalls();
    void testOpenAiParseArgumentsStringParsedToObject();
    void testOpenAiFinishReasonPassthrough();

    // --- OpenAI streaming ---
    void testOpenAiStreamReasoningAndToolArgumentsFragments();

    // --- OpenAI request shape ---
    void testOpenAiToolChoiceAutoWhenToolsPresent();
    void testOpenAiThinkingFieldsOnlyWhenEnabled();

    // --- Anthropic request shape ---
    void testAnthropicSystemTopLevelAndInputSchema();
    void testAnthropicMessageContentBlockization();

    // --- Anthropic response parsing ---
    void testAnthropicToolUseThinkingAndToolResultShape();

    // --- tool_call_id pairing ---
    void testToolCallIdPairedOnRequest();
};

static QJsonObject parseObj(const QByteArray& s) {
    return QJsonDocument::fromJson(s).object();
}

void TestLlmProtocol::testOpenAiParseContentReasoningToolCalls() {
    QByteArray body = R"({
      "choices": [{
        "finish_reason": "tool_calls",
        "message": {
          "role": "assistant",
          "content": "",
          "reasoning_content": "I should call the tool.",
          "tool_calls": [{
            "id": "call_abc",
            "type": "function",
            "function": {"name": "tune_frequency", "arguments": "{\"freq_hz\": 100000000}"}
          }]
        }
      }]
    })";
    LLMResponse r;
    QString err;
    QVERIFY(parseChatResponse(body, Protocol::OpenAI, &r, &err));
    QCOMPARE(r.reasoningContent, QString("I should call the tool."));
    QCOMPARE(r.finishReason, QString("tool_calls"));
    QCOMPARE(r.toolCalls.size(), 1);
    QCOMPARE(r.toolCalls[0].id, QString("call_abc"));
    QCOMPARE(r.toolCalls[0].name, QString("tune_frequency"));
    QCOMPARE(r.toolCalls[0].arguments.value("freq_hz").toInt(), 100000000);
}

void TestLlmProtocol::testOpenAiParseArgumentsStringParsedToObject() {
    QByteArray body = R"({
      "choices": [{"finish_reason": "stop",
        "message": {"role":"assistant","content":"done",
          "tool_calls":[{"id":"c1","type":"function",
            "function":{"name":"get_time","arguments":"{\"timezone\":\"Asia/Shanghai\"}"}}]}}]
    })";
    LLMResponse r;
    QVERIFY(parseChatResponse(body, Protocol::OpenAI, &r, nullptr));
    QCOMPARE(r.toolCalls.size(), 1);
    QCOMPARE(r.toolCalls[0].arguments.value("timezone").toString(),
             QString("Asia/Shanghai"));
}

void TestLlmProtocol::testOpenAiFinishReasonPassthrough() {
    QByteArray body = R"({"choices":[{"finish_reason":"stop",
        "message":{"role":"assistant","content":"hi"}}]})";
    LLMResponse r;
    QVERIFY(parseChatResponse(body, Protocol::OpenAI, &r, nullptr));
    QCOMPARE(r.finishReason, QString("stop"));
    QCOMPARE(r.content, QString("hi"));
}

void TestLlmProtocol::testOpenAiStreamReasoningAndToolArgumentsFragments() {
    // Two SSE data lines: first carries reasoning + first tool args fragment,
    // second carries finish_reason + second tool args fragment on same index.
    QByteArray line1 = R"(data: {"choices":[{"delta":{
        "reasoning_content":"think ",
        "tool_calls":[{"index":0,"id":"call_1","type":"function",
          "function":{"name":"scan_band","arguments":"{\"low_hz\":"}}]
    }}]})";
    QByteArray line2 = R"(data: {"choices":[{"delta":{
        "tool_calls":[{"index":0,"function":{"arguments":"24000000}"}}]
      },"finish_reason":"tool_calls"}]})";

    StreamChunk c1 = parseStreamChunk(line1, Protocol::OpenAI);
    QCOMPARE(c1.reasoningDelta, QString("think "));
    QCOMPARE(c1.toolDeltas.size(), 1);
    QCOMPARE(c1.toolDeltas[0].index, 0);
    QCOMPARE(c1.toolDeltas[0].id, QString("call_1"));
    QCOMPARE(c1.toolDeltas[0].name, QString("scan_band"));
    QCOMPARE(c1.toolDeltas[0].argumentsFragment, QString("{\"low_hz\":"));

    StreamChunk c2 = parseStreamChunk(line2, Protocol::OpenAI);
    QCOMPARE(c2.finishReason, QString("tool_calls"));
    QCOMPARE(c2.toolDeltas.size(), 1);
    QCOMPARE(c2.toolDeltas[0].argumentsFragment, QString("24000000}"));
}

void TestLlmProtocol::testOpenAiToolChoiceAutoWhenToolsPresent() {
    QList<ToolDef> tools;
    ToolDef d; d.name = "f"; d.description = "d";
    d.parameters = QJsonObject{{"type", "object"}};
    tools.append(d);

    RequestOptions opts;
    opts.protocol = Protocol::OpenAI;
    opts.model = "m";
    QJsonObject body = buildChatRequest({}, tools, opts);
    QVERIFY(body.contains("tool_choice"));
    QCOMPARE(body.value("tool_choice").toString(), QString("auto"));
    QVERIFY(body.value("tools").isArray());

    // No tools -> no tool_choice.
    QJsonObject bare = buildChatRequest({}, {}, opts);
    QVERIFY(!bare.contains("tool_choice"));
}

void TestLlmProtocol::testOpenAiThinkingFieldsOnlyWhenEnabled() {
    RequestOptions on;
    on.protocol = Protocol::OpenAI;
    on.model = "m";
    on.thinkingEnabled = true;
    on.thinkingBudget = 4096;
    QJsonObject bodyOn = buildChatRequest({}, {}, on);
    QCOMPARE(bodyOn.value("enable_thinking").toBool(), true);
    QCOMPARE(bodyOn.value("thinking_budget").toInt(), 4096);

    RequestOptions off;
    off.protocol = Protocol::OpenAI;
    off.model = "m";
    QJsonObject bodyOff = buildChatRequest({}, {}, off);
    QVERIFY(!bodyOff.contains("enable_thinking"));
    QVERIFY(!bodyOff.contains("thinking_budget"));
}

void TestLlmProtocol::testAnthropicSystemTopLevelAndInputSchema() {
    QList<ChatMessage> msgs;
    ChatMessage sys; sys.role = "system"; sys.content = "You are MiMo.";
    msgs.append(sys);
    ChatMessage u; u.role = "user"; u.content = "hi";
    msgs.append(u);

    QList<ToolDef> tools;
    ToolDef d; d.name = "set_mode"; d.description = "set radio mode";
    d.parameters = QJsonObject{{"type", "object"},
        {"properties", QJsonObject{{"mode", QJsonObject{{"type", "string"}}}}}};
    tools.append(d);

    RequestOptions opts;
    opts.protocol = Protocol::Anthropic;
    opts.model = "mimo-v2.6-pro";
    QJsonObject body = buildChatRequest(msgs, tools, opts);

    // system is top-level, NOT in messages.
    QCOMPARE(body.value("system").toString(), QString("You are MiMo."));
    QJsonArray m = body.value("messages").toArray();
    QCOMPARE(m.size(), 1);
    QCOMPARE(m[0].toObject().value("role").toString(), QString("user"));

    // tools use {type:custom, name, description, input_schema}.
    QJsonArray t = body.value("tools").toArray();
    QCOMPARE(t.size(), 1);
    QJsonObject td = t[0].toObject();
    QCOMPARE(td.value("type").toString(), QString("custom"));
    QCOMPARE(td.value("name").toString(), QString("set_mode"));
    QVERIFY(td.contains("input_schema"));
    QVERIFY(!td.contains("function"));
}

void TestLlmProtocol::testAnthropicMessageContentBlockization() {
    QList<ChatMessage> msgs;
    ChatMessage u; u.role = "user"; u.content = "hello";
    msgs.append(u);
    ChatMessage a; a.role = "assistant"; a.content = "world";
    a.reasoningContent = "thinking out loud";
    msgs.append(a);

    RequestOptions opts;
    opts.protocol = Protocol::Anthropic;
    opts.model = "m";
    QJsonObject body = buildChatRequest(msgs, {}, opts);
    QJsonArray m = body.value("messages").toArray();
    QCOMPARE(m.size(), 2);

    QJsonObject u0 = m[0].toObject();
    QJsonArray u0c = u0.value("content").toArray();
    QCOMPARE(u0c[0].toObject().value("type").toString(), QString("text"));

    QJsonObject a1 = m[1].toObject();
    QJsonArray a1c = a1.value("content").toArray();
    // thinking block first, then text block.
    QCOMPARE(a1c[0].toObject().value("type").toString(), QString("thinking"));
    QCOMPARE(a1c[1].toObject().value("type").toString(), QString("text"));
}

void TestLlmProtocol::testAnthropicToolUseThinkingAndToolResultShape() {
    // Assistant response with thinking + tool_use block.
    QByteArray body = R"({
      "role":"assistant","stop_reason":"tool_use",
      "content":[
        {"type":"thinking","thinking":"let me think"},
        {"type":"tool_use","id":"tu_1","name":"tune_frequency",
         "input":{"freq_hz":100000000}}
      ]
    })";
    LLMResponse r;
    QVERIFY(parseChatResponse(body, Protocol::Anthropic, &r, nullptr));
    QCOMPARE(r.reasoningContent, QString("let me think"));
    QCOMPARE(r.finishReason, QString("tool_calls"));
    QCOMPARE(r.toolCalls.size(), 1);
    QCOMPARE(r.toolCalls[0].id, QString("tu_1"));
    QCOMPARE(r.toolCalls[0].name, QString("tune_frequency"));
    // Anthropic input is an object -> straight into arguments.
    QCOMPARE(r.toolCalls[0].arguments.value("freq_hz").toInt(), 100000000);

    // Now build the follow-up request carrying the tool_result back:
    // assistant message (with toolCalls) + tool result message.
    QList<ChatMessage> hist;
    ChatMessage asst; asst.role = "assistant"; asst.content = "";
    asst.reasoningContent = "let me think";
    ToolCall tc; tc.id = "tu_1"; tc.name = "tune_frequency";
    tc.arguments = QJsonObject{{"freq_hz", 100000000}};
    asst.toolCalls.append(tc);
    hist.append(asst);

    ChatMessage res; res.role = "tool"; res.toolCallId = "tu_1";
    res.content = "tuned to 100 MHz";
    hist.append(res);

    RequestOptions opts;
    opts.protocol = Protocol::Anthropic;
    opts.model = "m";
    QJsonObject req = buildChatRequest(hist, {}, opts);
    QJsonArray m = req.value("messages").toArray();
    // assistant(tool_use) + user(tool_result block).
    QCOMPARE(m.size(), 2);
    QJsonObject a = m[0].toObject();
    QJsonArray ab = a.value("content").toArray();
    QCOMPARE(ab[ab.size()-1].toObject().value("type").toString(),
             QString("tool_use"));

    QJsonObject u = m[1].toObject();
    QCOMPARE(u.value("role").toString(), QString("user"));
    QJsonArray ub = u.value("content").toArray();
    QCOMPARE(ub[0].toObject().value("type").toString(), QString("tool_result"));
    QCOMPARE(ub[0].toObject().value("tool_use_id").toString(),
             QString("tu_1"));
}

void TestLlmProtocol::testToolCallIdPairedOnRequest() {
    QList<ChatMessage> msgs;
    ChatMessage asst; asst.role = "assistant"; asst.content = "";
    ToolCall tc; tc.id = "call_x"; tc.name = "get_status";
    tc.arguments = QJsonObject{};
    asst.toolCalls.append(tc);
    msgs.append(asst);

    ChatMessage res; res.role = "tool"; res.toolCallId = "call_x";
    res.content = "ok";
    msgs.append(res);

    RequestOptions opts;
    opts.protocol = Protocol::OpenAI;
    opts.model = "m";
    QJsonObject body = buildChatRequest(msgs, {}, opts);
    QJsonArray m = body.value("messages").toArray();
    QCOMPARE(m.size(), 2);

    QJsonObject ao = m[0].toObject();
    QJsonArray tcs = ao.value("tool_calls").toArray();
    QCOMPARE(tcs[0].toObject().value("id").toString(), QString("call_x"));

    QJsonObject to = m[1].toObject();
    QCOMPARE(to.value("role").toString(), QString("tool"));
    QCOMPARE(to.value("tool_call_id").toString(), QString("call_x"));
}

QTEST_MAIN(TestLlmProtocol)
#include "test_llm_protocol.moc"
