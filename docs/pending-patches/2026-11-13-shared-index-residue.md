# 共享 index 残留 4 条：单子与处置责任

- 记录时间：2026-11-13 21:09（Asia/Shanghai）
- 注入证据：`.locks/git_hook_bypass.log` 第 338 行
  `{"type": "INDEX_RESIDUE", "ts": "2026-11-13T20:36:42", "commit": "052828d1…",
   "count": 4, "paths": [...], "reason": "提交后共享 index 仍有暂存条目：
   …只记录不修复"}`
- 现状：4 条仍在共享 index 里；本单**不由任何一条线代清**（见 §3）

## 1. 四条路径与凭据

| 路径 | 台账 owner 凭据 | 暂存 blob | 工作区 blob |
| --- | --- | --- | --- |
| `desktop/connect_request_store.py` | `设备会话锁线`：current 条目（pid 38776，acquired 2026-11-13 06:46:40，`claim_reason=会话写门…`，**未释放**，TTL 60 分钟已过但台账未收） | `220775749d47` | `220775749d47` |
| `docs/tooltip-dynamic-baseline.json` | `K台账再检线`：history（acquired 2026-11-13 04:25:08，released 06:46:15） | `51ff9a48f3b4` | `51ff9a48f3b4` |
| `tests/test_hw_rollback_shows_device_truth.py` | `设备缺口修复线`：history（acquired 2026-11-11 21:22:28，released 21:23:40） | `c090d3466df9` | `c090d3466df9` |
| `tests/test_main_window_session_gate.py` | **无 claim 记录**（`claims.json` 里该路径无 current、无 history） | `7218b80b42d1` | `7218b80b42d1` |

Blob 号取自 2026-11-13 21:09 的实测：`git rev-parse :<path>` 与
`git hash-object <path>` 逐条相等 → **暂存内容 == 工作区当前内容**。

## 2. 清理责任人与时机

| 路径 | 责任人 | 时机 |
| --- | --- | --- |
| `desktop/connect_request_store.py` | `设备会话锁线` 本人 | 该线确认自己的改动已落库、或明确不再需要暂存之后；先 `file_claim.py release`，再清暂存 |
| `docs/tooltip-dynamic-baseline.json` | `K台账再检线` 本人 | 同上：自己那笔提交完成或确认放弃之后 |
| `tests/test_hw_rollback_shows_device_truth.py` | `设备缺口修复线` 本人 | 同上 |
| `tests/test_main_window_session_gate.py` | 责任人不明（无 claim 凭据） | 由仓库维护者在确认无人在途后处理；**任何线都不得以「看着没用」为由代清** |

清理动作只允许一条，且必须由责任人自己执行：

```
git restore --staged -- <path>      # 只撤暂存登记，不动工作区文件
```

## 3. 为什么别的线不许代清

1. `git_sync_index.py` 与任何 `git add` 都会**覆盖别人的暂存意图**。共享 index
   不是「垃圾堆」，是别人正要提交的那份选择；代清等于替别人决定「这些改动
   不再需要被提交」。
2. 本工具链的隔离是**私有 index**：别人的 `safe_commit` 根本读不到共享 index，
   代清对别人是静默破坏——对方下一笔按自己的声明提交时，暂存区已被清空，
   表现成「我明明 add 过」。
3. 这 4 条里有一条连 claim 凭据都没有（`tests/test_main_window_session_gate.py`）。
   没有凭据就只能承认「不知道是谁的」，不能用清理动作把「不知道」抹成「已处理」。

## 4. 清掉不丢东西，但也不是「无代价」

四条路径的暂存 blob 与工作区当前 blob 逐一相等（见 §1 表）：清暂存只把
index 条目退回 HEAD，工作区文件原样保留，**内容不会丢**。代价只有一个：
owner 需要重新 `git add` 一次自己的改动。这也是为什么必须由 owner 自己清——
只有他知道这笔重加要不要做、什么时候做。

## 5. 本单不做的事

- 不执行任何 `git restore --staged` / `git reset` / `git add` 到共享 index；
- 不改这四条路径的内容；
- 不把本单当成「已清理」的凭据：清完由责任人各自回报，本单只记录现状与责任。
