# Phase50 —— Agent 层最后收尾：手动压缩入口 + 两端会话一致性核对

范围：只写 `mobile/lib/` + `mobile/test/` + 本目录；`cpp/` 只读、不改不重编。
基线：flutter **377** 全过 + analyze **0**。纯 Dart，不重编 cpp，不触发 pytest/ctest。

---

## 块1：移动端手动压缩入口

- 会话栏新增「压缩上下文」按钮（`Icons.unfold_less_outlined`，IconButton 默认 48×48 ≥ `AppTokens.touchMin=44`）。
  位置：`mobile/lib/pages/chat_page.dart` `_buildSessionBar()`（菜单后、工具清单前）。
- 可用态诚实：`_compactPlan` 真实跑一次 `AiClient.compactHistory(_history)`，
  只有 `didCompact==true` 才可点；**无历史 / 未达预算门槛 / user 轮次不足 → 禁用**，
  tooltip 诚实说明原因（`_compactTooltip`），并给真实「约 X / 预算 6000 字符」。
- 点击 `_onManualCompact()`：**真实调用 `compactHistory`（G1 规则法折叠，不二次 LLM、不 mock）**，
  把 `_history`（上送层）换成折叠版（占位 system + 最近 N 轮原文），并在折叠边界插入
  一行居中占位「〔已折叠 N 轮早期对话为占位；原文仍可在上下回看，未删除〕」（`_UiMessage.foldMarker`）。
- **不删落盘原文**：`ChatSessionStore` 里的完整会话记录与气泡原样保留（G1 语义），折叠只作用上送/展示层。
- 边界定位规则抽到 `AiClient.findCompactBoundary`，`compactHistory` 与 UI 占位定位共用，避免漂移。

## 块3：压缩触发可观测（自动 + 手动统一）

- `complete()` 真实折叠时 yield 新事件 `ContextCompactedEvent`（真实条数 + 折叠前占用 `charsUsed` + 预算）。
- 自动压缩（send 阈值触发）与手动压缩共用**同一纯函数** `compactionStatusText(...)`：
  「已折叠 N 轮早期对话为占位（占用约 X / 预算 Y 字符；完整原文仍在本会话记录中，可回看，未删除）」。
- 自动路径 → 一次性 SnackBar（`AppTokens.kAiCompactionNotice`）；手动路径 → 持久占位气泡。
- 阈值/预算全部用具名常量：`kAiContextBudgetChars=6000`、`kAiKeepRecentUserTurns=6`、
  `kAiFoldAskPreviewChars=24`、`kAiCompactionNotice`；业务里无裸数。

---

## 块2：两端会话一致性对照表（桌面 cpp 只读核对）

| 项 | 桌面 `cpp/src/ai/*` | 移动端 `mobile/lib/*` | 判定 |
|---|---|---|---|
| 新建会话 | `createSession()`，首跑恰好一个空会话 | `store.createSession()`，同 | ✅ 一致 |
| 重命名 | `renameSession` trim 空忽略 | `renameSession` trim 空忽略、无变化不写盘 | ✅ 一致 |
| 删除会话 | 永不零会话：最后一个清空而非删 | `deleteSession` 同 | ✅ 一致 |
| 切会话 | `setCurrent`，切后清 transient | `_switchSession`，忙时禁止切换 | ✅ 一致（移动多忙守卫，更严） |
| 删当前后重指 | `currentId=order.last` | 同 | ✅ 一致 |
| index JSON | `{version:1,current,sessions:[{id,title,updatedAt,incomplete}]}` | 逐字段同形（`_persistIndex`） | ✅ 形状互读 |
| 会话 JSON | `{id,title,incomplete,messages:[{role,content,kind,ts?}]}` | 逐字段同形（`toJson/fromJson`） | ✅ 形状互读 |
| kind 缺失回退 | 空 → 用 role | 空 → 用 role | ✅ 一致 |
| incomplete 角标 | combo 标题追加「 〔未完成〕」 | 抽屉 subtitle「上次未完成」 | ⚠️ 数据一致、呈现不同（移动只在抽屉，桌面在切换下拉）。低成本但属 UI 呈现差异，保持现状不动 |
| 新 user 行清 incomplete | 是 | 是（`appendLine`） | ✅ 一致 |
| 流式去重 | 单条 transient 替换不追加、只固化一次 | `_UiMessage` 累加、`onDone` 只固化一次 | ✅ 一致 |
| 错误保 partial | 保留 partial 并固化 | `_finalizeAssistantOnError` 保留 partial 固化 | ✅ 一致 |
| **手动压缩是否写盘** | `onAiCompactContext` 用 `setMessages` **把 summary 行写回磁盘**（role="summary"，重载后保留） | **G1：不写盘**，只折内存 `_history` 上送层；重载后从磁盘重建全量原文 | 🏗️ **架构性差异，按红线保留**：移动红线明确「折叠只作用上送/展示层，不删落盘原文（沿用 G1 语义）」，故不改成桌面那样持久化 summary |
| 摘要来源 | 可注入 LLM summarize 回调（worker 线程真摘要），缺省回退规则法 | 纯规则法占位，**不二次调 LLM** | 🏗️ 架构性：G1 显式规则法、不 mock 不二次 LLM，保留 |
| 占用估算 | `estimateTokens`（CJK≈1、Latin≈1/4 token） | 字符数粗代理（无分词器） | 🏗️ 平台能力差异（移动无 tiktoken），如实记录 |
| 大 tool 输出预截 | `truncateLargeToolOutputs` 截 tool 行 | 无（移动不持久化 tool 瞬态行） | 🏗️ 边界差异，store 注释已声明 |
| 会话 id 生成 | QUuid | 时间微秒+随机 base36（无 uuid 依赖） | 🏗️ 实现差异，id 不互拼、各自容器 |
| 容器 | 真实文件 `index.json`+`sessions/<id>.json` | SharedPreferences/KvStore 同形 JSON 字符串 | 🏗️ 容器差异（store 头注释已声明，字段互读） |

**结论**：CRUD / JSON 形状 / incomplete / 流式去重 / 错误保 partial 全部一致，无需在移动改任何行为。
唯一功能性新增是块1手动压缩入口 + 块3事件可观测。其余差异均为架构/平台边界，按红线如实记录、不在本期改 cpp 也不把移动改成桌面那种持久化 summary。
