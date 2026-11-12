# A1 接线草稿（2026-11-12）

> **状态：草稿。追认（FZ-04 解冻单）未批，本目录任何一片都不得应用。**
> 用途：把 11 个 A1 单元的接线 diff 先固化下来——只把取值路径从直读全局
> `COLORS[...]` 换成 `tokens().status_color(...)`，不产生任何新的配色决定。
> 每片的应用前提都是同一句：**等 A2 拍板后才应用；拍板前只做 `git apply --check`。**
> 现状基线：A1 = 11 个单元 / 12 行，棘轮 `_BASELINE_CEILING = 11`。
> 本轮只写补丁与文档，未改 `themes.py` / `tokens.py` / 任何产品代码。

## 0. 为什么叫「草稿」而不是「待落地」

- A1 只换取值路径，但换完之后**渲染出来的颜色会变**（`danger` 从全局 `#B85C5C`
  变成各主题的 `status_danger`），这仍属视觉改动，必须等追认。
- 第 11 片（`control` 无效输入红底）还要等解冻单第 2 条选底色令牌，本目录只留
  草案说明，不提供可直接应用的 patch。
- 目录名带 `-wiring-draft` 就是为了让后来的人一眼看出：**这里的东西不能直接打。**

## 1. 目录

| 文件 | 内容 |
|---|---|
| `00-all.patch` | 10 片（除 `control`）合并 + 棘轮同步，**一次 `git apply` 即绿** |
| `01-status-360.patch` | `desktop/status_panel.py:360`（16pt OFF 字） |
| `02-status-867.patch` | `desktop/status_panel.py:867`（RSSI 弱） |
| `03-status-965.patch` | `desktop/status_panel.py:965`（OFF 12pt 600） |
| `04-status-1019.patch` | `desktop/status_panel.py:1019`（RSSI 弱） |
| `05-sat-453.patch` | `desktop/sat_track_panel.py:453`（TLE 档位色） |
| `06-sat-518.patch` | `desktop/sat_track_panel.py:518`（仰角图填充，非文本） |
| `07-sat-522.patch` | `desktop/sat_track_panel.py:522`（「跟踪已停止」字） |
| `08-vfo-232.patch` | `desktop/vfo_panel.py:232`（「删」按钮字） |
| `09-ntrip-48.patch` | `desktop/ntrip_panel.py:48`（未连接/未配置圆点） |
| `10-ntrip-49.patch` | `desktop/ntrip_panel.py:49`（连接中圆点） |
| `11-control-invalid.DRAFT.md` | `control_panel:999/1007`，**阻塞**，等解冻单第 2 条 |

> 上表 12 个文件的共同状态：**草案 · 等 A2 拍板**。01–10 是可直接 `--check` 的
> 纯产品代码 diff；`00-all` 是合并版（含棘轮同步）；11 只有说明、没有 patch。

## 2. 应用方式

单片（每片都要先验后打）：

```powershell
git apply --check docs/pending-patches/2026-11-12-a1-wiring-draft/01-status-360.patch
# 拍板后才允许：
git apply        docs/pending-patches/2026-11-12-a1-wiring-draft/01-status-360.patch
# 然后按 §3 表删掉对应基线行，并把 _BASELINE_CEILING 减 1
$env:MBDSDR_TEST_LOCK="0"; python -m pytest tests/test_ui_contrast.py -q
```

全部批准（含棘轮同步，落地即绿，不用手工改测试）：

```powershell
git apply docs/pending-patches/2026-11-12-a1-wiring-draft/00-all.patch
$env:MBDSDR_TEST_LOCK="0"; python -m pytest tests/test_ui_contrast.py tests/test_theme_contrast_gate.py -q
```

**`00-all.patch` 的应用前提**：它要改的 **5 个文件**（`desktop/status_panel.py`、
`desktop/sat_track_panel.py`、`desktop/vfo_panel.py`、`desktop/ntrip_panel.py`、
`tests/test_ui_contrast.py`；10 指片数，不是文件数）必须仍是当前 HEAD 的样子。
批下来时若期间有人动过其中任何一个：**先 `git apply --check`，失败就停下来报告，
不要手工合并**——手工合会绕过逐片计数，棘轮与上限容易对不上。

**单片只含产品代码，故意不带测试改动**：两片合用时，棘轮基线行与上限会被各改一次，
diff 必冲突；产品代码本身不冲突（不同文件 / 同一文件里互不重叠的行）。所以：
要么用 `00-all`，要么单片 + 手工同步。

**2026-11-12 复核（本目录移动后实测，正好撞上前提条款）**：

- `01`–`10`：对当前工作树 `git apply --check` **全部 OK**；4 个产品文件未被他人改动。
- `00-all`：对工作树 **FAIL** —— `tests/test_ui_contrast.py` 有他线未提交改动
  （+126 行，棘轮扩面到全部 token），补丁上下文对不上；
  对索引 `git apply --check --cached` **OK**（即对 HEAD 仍然有效）。
- 处理方式按本节前提执行：**不手工合并、不改补丁**，等该文件落定后重跑
  `git apply --check`；届时仍失败就把冲突报给 W92 派发方。
- 这是「5 个文件必须都是 HEAD 原样」条款的真实样例：产品文件干净、测试文件被改，
  合并版就会先坏。

## 3. 逐片期望（应用后必须成立）

| 片 | 改动（旧 → 新） | 现值 → 后值（default，card/bg） | 棘轮 |
|---|---|---|---|
| 01 | `COLORS["danger"]` → `status_color("danger")` | 4.45 / 4.01 → **5.02 / 4.53** | 删 `status:360` 行，11→10 |
| 02 | 同上 | 同上 | 删 `status:867`，11→10 |
| 03 | 同上 | 同上 | 删 `status:965`，11→10 |
| 04 | 同上 | 同上 | 删 `status:1019`，11→10 |
| 05 | 同上 | 同上 | 删 `sat:453`，11→10 |
| 06 | 同上（非文本，阈值 3:1） | 4.45 / 4.01 → 5.02 / 4.53 | 删 `sat:518`，11→10 |
| 07 | 同上 | 同上 | 删 `sat:522`，11→10 |
| 08 | 同上 | 同上 | 删 `vfo:232`，11→10 |
| 09 | 删模块常量 `_COLOR_IDLE`，两处调用点改 `status_color("danger")` | 圆点 4.45 / 4.01 → 5.02 / 4.53；**并修掉「导入时求值一次、切主题不刷新」** | 删 `ntrip:48`，11→10 |
| 10 | 删 `_COLOR_BUSY`，调用点改 `status_color("warning")` | 圆点 **2.03 / 1.83 → 5.04 / 4.55**；同样修求值时机 | 删 `ntrip:49`，11→10 |
| 11 | 见 `11-control-invalid.BLOCKED.md` | 1.27（default）/ 2.88（dark）→ ≥4.5 | 删 `control:invalid` 两行，11→10 |

各主题的后值由主题 `status_danger` / `status_warning` 提供，逐主题 ≥4.5 由
`tests/test_ui_contrast.py::test_status_colors_readable_on_theme` 保证，本补丁不改该门禁。

**门禁红绿**：应用前棘轮是绿的（它是允许清单，不是缺陷计数器）；应用后仍绿。
唯一的红是「改了代码没删基线行」——`stale` 断言会报
`基线里登记了已不存在的直读：desktop/status_panel.py danger -1`，按 §3 删行+降数后转绿。
`00-all` 已把这一步做进去。

## 4. 顺序与冲突

- 单片任意顺序都可单独应用到当前 HEAD；`git apply --check` 已全部通过。
- 多片合并：只有 `tests/test_ui_contrast.py` 会冲突；规则是「删掉所有已应用片对应的
  基线行，`_BASELINE_CEILING = 11 − 已应用片数`」。不想手工就用 `00-all`。
- `09`/`10` 单片应用后，`ntrip_panel.py` 顶部注释（原「这三个值」）会暂时失真；
  `00-all` 已一并改成：
  `# 指示灯颜色：IDLE/BUSY 在调用点用 tokens().status_color(...) 求值，随主题刷新；`
  单独应用 09 或 10 时请顺手把这两行改成同样的措辞（只改注释，不影响门禁）。

## 5. 本轮验证记录（现场已还原，无残留改动）

- 11 个 patch：`git apply --check` 全部 OK。
- `00-all` 应用后：`tests/test_ui_contrast.py + tests/test_theme_contrast_gate.py` **77 passed**
  （棘轮 1 个单元 / 上限 1）。
- `01` 单片应用 + 手工同步：**67 passed**；只应用不同步 → 红，
  报 `基线里登记了已不存在的直读：desktop/status_panel.py danger -1`。
- 验证后 `git checkout --` 还原 5 个文件，`git status` 无残留。

## 6. 回退

未提交时：`git checkout -- desktop/status_panel.py desktop/sat_track_panel.py desktop/vfo_panel.py desktop/ntrip_panel.py tests/test_ui_contrast.py`。
已提交：按片 `git revert`（每片一个提交时最省事）。

## 7. 明确不做

- 不碰 `themes.py` / `tokens.py`；不新增任何令牌。
- 不调危险底色、不碰 A2 的 `success` / `accent` 正文与 `vfo` 行底色——那些是解冻单第 2、3 条的决策。
