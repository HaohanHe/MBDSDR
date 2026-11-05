# COORDINATION.md §6.1 追加草稿：claim 实例与展示字段

> 提出：操作逻辑线/6ae85f56，2026-11-06
> 状态：**待落地**。`docs/COORDINATION.md` 当前有他线在途改动，见文末证据；
> 本次不能写。TTL 到期（07:53）或持有方释放后，用私有 index 只提交本段
> hunk，避免卷走他人的在途改动。

## 一、实例：`desktop/control_panel.py` 的 takeover

- 原记录：`owner=参数治理线-6ae85f56`，`pid=39084`，持有起点 `06:23:16`，
  TTL 90 min。
- 事实：`Get-Process -Id 39084` 查无；`git status -- desktop/control_panel.py`
  为空（无在途改动）；硬编码线三次查证后交接（`claims.json` 无该文件条目 /
  `release` 返回 `RELEASE DENIED` / 只提交 `docs/hardcoded-parameters.md`）。
- 处理：`takeover --owner 操作逻辑线/6ae85f56 --reason "…"` 成功（pid 死 +
  无在途改动，满足 2/3），随后提交 `8f033ca3`，结束即释放。
- 教训：owner 名里的「参数治理线」不是可验证身份，后来的人只能靠 pid 反查。
  **owner 必须带可验证的会话标识**（本线统一用 `线名/会话前 8 位`，如
  `操作逻辑线/6ae85f56`），reason 里写一条可复核证据。

## 二、`file_claim.py status` 的展示字段（30 秒判断法）

`status` 每行给出：

| 字段 | 含义 | 怎么用 |
|---|---|---|
| `owner` | 持有者（应含会话标识） | 没有会话标识 = 不可追责，优先怀疑孤儿 |
| `pid` | **执行 claim 命令的进程** | 注意：CLI 跑完就退出，pid 几乎必然「已死」，**不能单独作为判据** |
| `持有起点` | 首次 claim 时间（= 心跳基准） | 与 TTL 比：`held=14.2/90.0min 剩余 75.8min` |
| `剩余` | TTL 剩余 | 归零即可接管 |

**接管判据（脚本口径，需 1+1）**：

1. `pid` 条件：已死或被复用（`PASS`）；
2. `过期` **或** `无在途改动` 之一：
   - 过期：`now - 心跳 >= TTL`；
   - 无在途改动：`git status --porcelain -- <path>` 为空。

本次 `COORDINATION.md` 的判定：

```
[FAIL] 心跳过期：心跳 2026-11-06 06:23:16，已 14.2min / TTL 90.0min
[PASS] pid 已死/被复用：pid 38116 已不存在
[FAIL] 无在途改动：有在途改动： M docs/COORDINATION.md
通过 1/3 条（需 pid 条件 + 过期/无在途之一）
```

结论：pid 死了也不能抢「有未提交改动」的文件——那些改动可能正是别人写到
一半的成果。要么等 TTL 过期，要么由持有方释放；**绝不 `checkout`/覆盖**。

## 三、可直接粘贴的追加文本（待落地）

```
### 6.1.x claim 记录的三个字段与接管判据（2026-11-06 实例）

- 记录 owner 必须带可验证会话标识（本仓统一 `线名/会话前 8 位`），
  reason 里附一条可复核证据；不要只写组织名。
- `file_claim.py status` 展示 owner / pid / 持有起点 / held 与剩余 TTL。
  pid 是执行 claim 的 CLI 进程，跑完即退，**单看 pid 必然「已死」**；
  真正判据是「TTL 过期」或「该路径无在途改动（git status 为空）」。
- 实例：control_panel.py 原 owner=参数治理线-6ae85f56、pid 39084 已死、
  文件干净 → 按上述规则 takeover，提交 8f033ca3；硬编码线此前三次查证
  （claims.json 无条目 / release 被拒 / 只提交 hardcoded-parameters.md）。
- 反例：COORDINATION.md 在途改动未提交时，即使 pid 已死也拒绝 takeover；
  不得 checkout/覆盖他人的在途改动，应等 TTL 到期或持有方释放。
```
