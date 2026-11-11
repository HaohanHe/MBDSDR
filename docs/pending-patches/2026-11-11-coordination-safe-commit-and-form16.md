# COORDINATION.md 两段追加 + 提交事故形态⑯（待落地）

> 提出：机制线（会话 `3eda2b56`），2026-11-11
> 状态：**待落地，禁止直接在工作区副本上改 `docs/COORDINATION.md`**。
> 该副本含他线 49 行在途改动、且**不含**冗余线已提交的 11 行；它的 post-commit 已报
> `STALE_INDEX_SUSPECT`。直接改并提交 = 静默回退那 11 行。
> 落库前提：①`git show HEAD:docs/COORDINATION.md` 与工作区副本的差异只剩本文件要加的段；
> ②没有以 ` M docs/COORDINATION.md` 形式存在的他线在途改动。

## 一、可直接粘贴的追加文本（两段）

### 1.1 提交唯一入口（对应本轮 ⑤）

```md
### 提交必须走 `scripts/safe_commit.py`（2026-11-11 写死）

- **所有线的提交唯一入口**：`python scripts/safe_commit.py --files <清单> --message-file <msg>`
  （或 `--message`）。它做三道校验：私有 index（`read-tree HEAD` + 只 add 清单内文件，不碰他人
  在途改动）；提交前重校验 HEAD 未变（变了就整盘重建，绝不基于旧基线提交）；提交后逐文件比对
  `HEAD:<path>` 的 blob（退出码 3 = 已提交但 blob 不一致，按形态⑩处理）。
- **拒绝入库 ≠ 可绕开**：它拒绝就是「判据不成立」。标准处置是**修判据**——
  补全 `--files`、重建基线、消解撞锁（`--retries`）、把误报的路径从清单里去掉。
  **不得**手工 `git commit`，**不得** `git add -A` 绕过文件集校验：那等于主动放弃
  上面三道校验，且会把他人未提交改动卷进来。
```

### 1.2 `COORDINATION.md:136` 的 P0 条（对应本轮 ②，等 `tool_registry.py` 在途落库后补两行）

落库时按 `docs/evidence-standard.md §7.7` 重新走一遍，不许直接沿用旧坐标：

1. 取 `git show HEAD:mbdsdr_ai/tool_registry.py` 搜 `MODEL_HARD_GATE_PATTERNS` 的真实行号，
   把 `:525` 改成它；
2. 基准取该行 blame：`git blame -L N,N --porcelain HEAD -- mbdsdr_ai/tool_registry.py` 首字段；
3. 补**两行**：
   - 「判据：`git log -S MODEL_HARD_GATE_PATTERNS -- mbdsdr_ai/tool_registry.py` 命中
     `da7eebee`；行号按 HEAD 重定位后基准 = 该行 blame」；
   - 「复核：`python scripts/verify_claims.py --check-lines --strict-lines` 下该条为『含字面量』」。

当前状态：该条在白名单里（`docs/line-check-waivers.md`，owner=机制线，到期 2026-11-14）；
本段落库后应同时删掉那条豁免。

## 二、提交事故形态⑯（追加到 COORDINATION.md 形态表，与 ⑭⑮ 并列）

```md
### 形态⑯ 多线在途文档 + 私有 index 提交 = 工作区副本落后（2026-11-11）

**现象**：A 线用私有 index 只提交了自己那 11 行；工作区副本仍含 B 线 49 行在途改动，
且不含 A 线这 11 行（A 线的 post-commit 报 `STALE_INDEX_SUSPECT`）。若 B 线直接在工作区
副本上改并提交，就会把 A 线那 11 行静默回退——`git status` 只会显示 ` M`，看不出丢内容。

**判据**：`git diff HEAD -- <path>` 里出现「HEAD 已有内容的删除」时，先怀疑副本落后，
而不是对方在删。核对 `git show HEAD:<path>` 与工作区副本，逐行确认差异归属。

**处置**：以 `git show HEAD:<path>` 为底重放——把本轮要加的内容写到 HEAD 版本上；
再决定「等对方在途行落库后一起提交」或「原样带上并逐行确认归属」。提交后用
`git show HEAD:<path>` 逐字确认 A 线那 11 行仍在。**绝不** `git checkout -- <path>`。
```

## 三、`docs/error-code-visibility.md` 的未提交 hunk（本轮 ①）

已按裁定把 `:328` 改成完整四元组：

```
`ff964183` — `desktop/troubleshooting.py:92` — `2.048 MS/s`（基准 `ff964183`）
```

并留了「2026-11-11 单位统一后更新」一行。但该文件工作区有他线 **14 行** 在途改动，
本轮**不入库**（只提交自己的文件）。处置：等他线那批落库后，按 §7.7 复核该条仍为
「含字面量」再一并提交；若被覆盖，按上面的四元组重写即可（`ff964183` 是该行 blame）。
