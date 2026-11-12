# 工程治理总纲（八维 + 13 条公理）

> 这份文件不是"读一遍就算"的规范。每一条规则都由 `scripts/axioms.py` 在仓库上真跑，
> 违规会报出文件:行号、为什么违反、怎么修。规则本身也在反证自检里被验证"不是空过"。
>
> 输入口径：**只用仓库自身内容**——源码 AST、测试里的 skip/xfail 元数据、文档里的
> 路径与提交号、`git cat-file`/`git rev-list`。不 import 项目模块、不启动 Qt、不连硬件。

## 一条命令

```bash
python scripts/axioms.py --prioritize \
  --json docs/axioms-first-run.json \
  --report docs/axioms-first-run.md
```

| 用法 | 作用 |
|---|---|
| `python scripts/axioms.py` | 八维合规报告（含 21 条反证自检） |
| `--prioritize` | 追加按派活规则排序的清单 |
| `--self-test-only` | 只验"反证是否空过" |
| `--rule A03` | 只看某条规则 / 某个维度 |
| `--fail-on must-fix` | 接入门禁：存在应修违规即退出 1 |
| `--max-per-dim N` | 每维最多打印的明细条数（JSON 始终是全量） |

退出码：`0` 测绘完成（默认不因违规阻塞）；`1` 反证自检失败或 `--fail-on` 命中；`2` 工具错误。

## 结果怎么读

- **应修**：检查器能直接指认的缺陷，默认最高优先。
- **复核**：事实已成立，但需要人判断是"改代码"还是"改口径"（例如同名常量是否同一语义）。
- **豁免**：命中规则但已按三要素登记（reason / review_by / approver），到期自动重新计入违规。

## 八维与对应检查

| 维度 | 可判定规范 | 检查名 |
|---|---|---|
| D1 分层与依赖 | `mbdsdr_ai` 不得 import `desktop`/Qt；`tests` 反向依赖 `desktop` 只允许在显式 UI 用例 | `D1-reverse` `D1-qt-in-core` `D1-test-reverse` |
| D2 真值源 | 同一事实只允许一个产生点；同名常量不得多值；消费点必须走 `desktop/readouts.py` | `D2-multi-producer` `D2-dual-constant` + A01–A04/A12/A13 |
| D3 接口与命名 | 公开函数 ≥8 的模块必须有 `__all__`；同名不得不同职；同一概念不得两名 | `D3-no-contract` `D3-name-role` `D3-concept-alias` |
| D4 门禁与验收 | 守卫必须接入某个入口；skip 只能表环境缺失；断言必须可成立；xfail 必须 strict | `D4-silent-gate` + A05–A09 |
| D5 流程与协作 | 已知红必须带转绿条件/复跑命令；文档提交号必须 `cat-file` 可见；绿必须绑定当前树 | `D5-known-red-incomplete` + A10/A11 |
| D6 目录与文档 | 文档引用的仓内路径必须存在；行号必须落在文件内；主题冻结声明必须与代码一致 | `D6-missing-path` `D6-stale-line` `D6-color-claim` |
| D7 任务分派 | 按固定公式算优先级，不由"谁最后汇报"决定 | `priority_score` |
| D8 豁免与债务 | 任何"暂时不做"必须有理由、到期、批准人 | `D8-debt-no-expiry` |

## 13 条公理

每条都能指回本仓真实案例；"反证位置"是自检里重建该违规片段的位置，
自检要求**正例必须变红、负例必须变绿**，且删除被测逻辑后正例必须真红。

| ID | 陈述 | 可判定形式 | 本仓案例 | 检查名 | 反证位置 |
|---|---|---|---|---|---|
| A01 | 用户可见事实只有一个产生点 | `desktop/` 下非 `readouts.py`/`constants.py` 的含单位 f-string 即违规 | main_window 多处就地拼 MHz/kHz | `readout_single_source` | main_window 状态条 |
| A02 | 读数保真，不得把值变成另一个值 | MHz 频率 <6 位小数 / kHz 带宽 0 位小数即违规 | 2400 Hz 显示成 2 kHz；bookmark `.4f} MHz` | `lossy_readout` | `bookmark_panel.py:342` |
| A03 | 降级必须可见且可归因 | 持久化读取外层的宽泛 except 只 pass/return None/default 即违规 | `conversation.py` 读损坏会话静默回空 | `silent_degrade` | `conversation.py` except 块 |
| A04 | 不可读的输入不得被静默覆盖 | 同模块「静默读」+ `json.dump(` 覆写且无 corrupt/backup 即违规 | 会话/配置读损坏后继续整文件覆写 | `corrupt_no_clobber` | `conversation.py` |
| A05 | 每个守卫必须有可成立的通过态 | 顶层 `assert False`、消息自述不可能、永久阻塞后断言线程已退出 | `test_network_failure_modes.py:407`（需 307s） | `impossible_assertion` | 同左 |
| A06 | 守卫必须可反证 | 非 strict xfail、吞 `AssertionError`、宽泛 except 内 skip、死断言 | K06 静默吞噬 113 处；空过的探针 patch | `vacuous_guard` | `tests/` 去掉 `strict=True` |
| A07 | 约束必须放在必经的最窄处 | 同一动作的调用方里「有数值守卫」与「无守卫且丢弃返回值」并存即违规 | 采样率范围只在 device_panel；增益四条路径 | `constraint_at_chokepoint` | `set_sample_rate` 的多条路径 |
| A08 | 守卫必须判对对象且可被触发 | `if`/`while` 条件完全由字面量构成即违规（不做变量传播避免误报） | `decoders.py:435 if False:`；无设备降级判错对象从未触发 | `guard_cannot_fire` | `decoders.py:435` |
| A09 | skip 只能表示环境缺失 | skip 理由含"不可构造/依赖缺失/not available"且无可核对锚点即违规 | `test_web_bridge_mcp.py` 6 处「MCPToolSet 在此环境不可构造」 | `skip_means_env_only` | 同左 |
| A10 | 证据必须可复核 | 文档反引号中的 7–40 位十六进制串必须 `git cat-file -t` 为 commit | `推进报告:141` 的 `5d09bb2`；`COORDINATION:3430` 的 `0d7e433e`（两个幽灵号） | `evidence_commit_exists` | `推进报告:141` |
| A11 | 绿必须绑定到被断言的那棵树 | 绿色声明无提交号，或提交距 HEAD >50 且未标历史/已修 | `learn/phase40/ci-notes.md` 的 752 提交前绿 | `green_binds_tree` | `known-red-register.md` |
| A12 | 同名实现必须行为一致 | 跨模块同名读数口径函数 AST 不同即违规 | `main_window._fmt_dbfs` vs `readouts.fmt_dbfs` | `duplicate_impl_divergence` | main_window 的 `_fmt_dbfs` |
| A13 | 身份必须唯一且不可复用 | owner/holder 字符串比较（函数内无 pid）；serial 由枚举序号拼接 | `file_claim.py:540` 只比名字；`device_manager.py:422/451` 用序号兜底 | `identity_not_reusable` | 同左 |

判同与判活是两件事：同 pid + 进程启动时间才叫"同一个进程"；同序列号才叫"同一台设备"。
名字、枚举序号、可改路径字符串都只是标签。

## 派活优先级（D7）

```
score = loss*100 + freq*10 + evidence*3
        - 50（复核级）
        - 60（冻结文件，如 desktop/main_window.py）
        - 1000（已豁免）
```

| 输入 | 取值 | 含义 |
|---|---|---|
| `loss` | 1–3 | 用户可感知损失：数据丢失/显示错数=3；功能不可用=2；内部整洁=1 |
| `freq` | 1–3 | 触发频率：每条操作路径=3；常见场景=2；边角=1 |
| `evidence` | 1–3 | 证据可得性：无硬件即可静态/单测复现=3；需模拟=2；需真机=1 |

输出是按分数降序的清单（同分按路径稳定排序）。**损失永远压过整洁**，冻结件自动降权，
已豁免项排到最后但仍可见。

## 豁免规则（D8）

`docs/axioms-waivers.json` 的每一项必须同时具备：

```json
{
  "rule": "A02",
  "path": "desktop/main_window.py",
  "reason": "视觉冻结期，改动需先上报排期",
  "review_by": "2027-06-30",
  "approver": "用户（2026-11-12 决定）"
}
```

缺任一字段 → `D8-debt-no-expiry` 报违规；`review_by` 过期 → 该条自动重新计入"应修"，不再算豁免。
没有到期/批准人的"暂时不做"等于没登记。

## 首次全量跑（2026-11-13，HEAD `ab4d36df`）

```
违规 272 条：应修 119，复核 110，豁免 43；反证自检 21 条均通过
D1 5 / D2 69 / D3 33 / D4 42 / D5 40 / D6 39 / D7 0 / D8 1
```

### 立刻修（119）

| 类别 | 条数 | 为什么立刻 |
|---|---:|---|
| A03 静默降级 | 26 | 读失败与"本来就是空"不可区分，下一步就会覆盖或误判 |
| A02 读数失真 | 16 | 用户看到的数不是真实值（3–4 位小数 MHz = 1 kHz 量化） |
| A07 约束只在入口 | 15 | CLI/MCP/重连路径绕过同一条约束，后端夹值后无人提示 |
| D6 文档指向不存在的路径 | 18 | 照文档操作会直接失败（活跃文档，非历史快照） |
| A09 假 skip | 9 | 本仓依赖导不进来被记成"环境不凑巧"，长期假绿 |
| D5 已知红缺字段 | 8 | 没有转绿条件的"已知红"永远不会被销账 |
| A01 读数就地拼装 | 6 | 同一值换面板就换字符串 |
| A04 损坏后覆写 | 5 | 下一次保存永久丢数据，且无备份 |
| A06 空过守卫 | 5 | 删掉被测逻辑仍然绿 |
| D1 Qt 反向依赖 / 反向 import | 4 | 纯后端无法脱离 GUI 测试与复用 |
| A13 身份用名字/序号 | 3 | 同名进程可同时占同一设备；换 USB 口设备变两台 |
| A10 幽灵提交 | 2 | 后续排查建在不存在的提交上 |
| A05 / D2-multi-producer | 2 | 不可能通过的断言；同一读数多产生点 |

### 长期豁免（43，已三要素登记）

全部是 `desktop/main_window.py` 的 A01/A02/A12：该文件处于视觉冻结期，改动需先上报排期，
`review_by=2027-06-30`。到期后这些条目会自动回到"应修"。

### 不立即修、保留复核（110）

- **A11 绿未绑定当前树（30）**：多数是历史状态记录，需重跑后补提交号，或明确标注"历史快照"。
- **D3 命名/契约债（33）**：同名不同职、同概念两名，需改名而不是改逻辑。
- **D2 同名常量多值（12）**：先判定"同一事实"还是"同名不同职"。
- **D4 未接入入口的门禁脚本（11）**、**D6 历史快照文档的失效路径（12）**、
  **D6 颜色声明与代码不一致（7）** 等。

这些**没有**做批量豁免：豁免需要理由、到期、批准人三要素，批量豁免会把真实漂移藏起来。
它们保留在报告里，按季度复核。

## 反证自检（21 条，必须全部 PASS）

`python scripts/axioms.py --self-test-only` 会为每条规则在临时目录重建：

1. **正例**：真实违规代码片段 → 该规则必须命中 ≥1 条；
2. **负例**：修正后的等价片段 → 该规则必须命中 0 条；
3. 正例片段来自本仓历史/现存代码（见 `tools/axioms/selftest.py` 的 `provenance`）。

任一条正例 0 命中（检查失效）或负例仍红（误报）都会让命令退出 1。
这就是公理 A06 对检查器自己的约束：**守卫的反证不得空过**。
