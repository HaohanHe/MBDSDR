# 配置损坏「错误有了但界面看不到」接线草稿（2026-11-12）

> **状态：只写草稿，不应用。** `desktop/main_window.py` 由卫星线独占，本目录一行未改。
> 生成基线：`e908937d`（`fix(command-palette): …`），生成时 `desktop/main_window.py` 无本地改动。
> 校验：三份 patch 均通过 `git apply --check`（对生成时工作区）。

## 为什么现在写

设置三环线这批修复（settings / 书签 / 布局 / 站址同步 / memory / scheduler /
guardian / version_store / workflow_engine / command_palette）都做到「备份 +
`last_error` + 拒写」为止，**错误文本有了，界面还没消费**。等锁放开那一下
如果没有现成 patch，又会拖一轮；写草稿还能提前暴露「到底有没有可挂的位置」。

## 索引

| # | patch | 文件 | 落点（生成时行号） | 状态 |
|---|-------|------|------------------|------|
| 01 | `01-bookmark-panel-status.patch` | `desktop/bookmark_panel.py` | 136-138、163-164 | 可直接 apply（不属卫星线文件） |
| 02 | `02-main-window-freq-mgr-status.patch` | `desktop/main_window.py` | 2097-2099 | **等卫星线锁** |
| 03 | `03-capability-drawer-agent-stores.patch` | `desktop/capability_drawer.py` | 35、90、153-162、178-182 | 可直接 apply |

数据源字段（草稿依赖，均已存在）：

- `BookmarkManager.last_error` / `.corrupt_backup`
- `FrequencyManager.load_error`（透传 `BookmarkManager.last_error`）
- `Scheduler.last_error` / `Guardian.last_error` / `MemoryStore.last_error`
- `WorkflowEngine.last_error`（顺带被 03 覆盖，因为它在同一张能力位表里）

---

## 01 书签面板：把 `BookmarkManager.last_error` 放进既有状态行

- **落点**：`_build_ui` 的 `self.status_label`（136-138）；`_init_manager` 末尾
  `self.refresh_list()` 之后（163）。
- **插入内容**：`status_label.setWordWrap(True)`；读 `self._mgr.last_error` 非空时
  `setText` + `setToolTip`。**不新建控件、不弹窗。**
- **落完用户会看到**：书签面板原有提示行（「hintLabel」）显示
  「书签文件损坏（…）：路径；原件已备份为 `bookmarks.json.corrupt-…`，本次按空列表启动」，
  悬停可看全文。不再是「列表空了但一句话没有」。
- **不做的**：不禁用编辑/删除。`_save_blocked` 为真时写操作会失败并返回
  `False`，但这条路径该不该升级成禁用按钮是产品判断，不在接线草稿里替它决定。
- **同一行被改动后怎么判断**：
  1. `_init_manager` 是否还在、`self.refresh_list()` 是否仍是末尾动作；
  2. `BookmarkManager` 是否仍有 `last_error`（`grep -n "last_error" mbdsdr_ai/bookmark_manager.py`）；
  3. 若面板已自行消费 `last_error`（grep 到 `last_error` 在 `bookmark_panel.py` 出现），
     **放弃本 patch**，不要重复显示；
  4. 若只是行号漂移、锚点文本仍在 → 重新生成 patch，不手改行号。

## 02 主窗口：频率管理器加载失败贴一次状态栏

- **落点**：`main_window.py` 2086-2098 的 `try/except Exception: pass` 之后、
  `_add_right_tab("控制", …)` 之前。
- **插入内容**：`self._freq_mgr.load_error` 非空时调**既有错误出口**
  `self._show_error_status(f"书签索引异常：{…}")`（不带 `code`，不硬编错误码）。
- **落完用户会看到**：启动后状态栏出现一条「书签索引异常：…已备份为 …」，
  8 秒后自动消失；期间不做弹窗、不阻塞启动。
- **诚实说明**：这是**一次性、会消失**的提示，不是常驻诊断位。真正常驻的位置
  需要 main_window 新建一个「数据健康」面板，那超出本批范围，也超出本线权限。
- **与 01 的关系**：同一份 `bookmarks.json` 会从两个入口各报一次（书签面板内 + 全局状态栏）。
  如果嫌吵，只保留 01；02 的价值是**用户没打开书签面板时也能知道**。
- **同一行被改动后怎么判断**：
  1. `git log -L 2086,2099:desktop/main_window.py` 看这段最近一次提交；
  2. `git diff <那次提交> HEAD -- desktop/main_window.py` 判断是「搬家」还是「改语义」；
  3. **该重做**：加载块还在、`_freq_mgr.load_error` 仍无 UI 消费（`grep -n "load_error" desktop/` 只有赋值）；
  4. **不该重做**：卫星线已在同一位置或统一错误出口里显示了书签索引错误；
     或加载逻辑被搬进 helper 且 helper 已有错误出口 —— 此时把 patch 改到新位置，
     而不是在旧位置硬贴；
  5. **禁止** `git apply -3`、禁止按行号盲贴。`--check` 失败先看冲突类型。

## 03 能力矩阵：`memory` / `scheduler`（外加 `workflow_engine`）与新增 `guardian` 行

- **落点**：`capability_drawer.py` 的 `__init__`（`self._host = host` 之后）、
  `_collect()` 的 `rows = []` 之后、能力位元组（153-162）、渲染循环的
  `setToolTip`（182）。
- **插入内容**：
  - `_collect` 里 `self._error_notes = {}`；每行读 `obj.last_error`，
    非空则副说明追加「；持久化异常（悬停看详情）」，全文存进 `_error_notes`；
  - 能力位元组补 `("guardian", "自进化守护")`；
  - 渲染时把 `_error_notes[name]` 拼进 tooltip。
- **落完用户会看到**：打开「◧ 能力矩阵」后，「长期记忆 / 调度器 / 工作流引擎 /
  自进化守护」四格中出错的那格写「常驻 · MemoryStore；持久化异常（悬停看详情）」，
  悬停显示完整人话（含备份路径与「本次不写入」）。
- **为什么选这里**：这是桌面端**唯一已有**的「内核对象真实状态」常驻面板，
  memory / scheduler / workflow_engine 本来就在表里；它默认收起，所以不算主动打扰，
  但也**不是主动提示** —— 这一点如实记下，别把它当成「用户一定会看到」。
- **留白（未覆盖）**：
  - `version_store`：桌面无入口、能力位表里也没有位置，**本次不硬塞**。可落的路只有
    一条——启动日志。`default_scan_directories` 已把 `~/.mbdsdr/evolution`
    （以及 `guardian_snapshots`、`workflows`）纳入启动清点，索引损坏的
    `versions.json.corrupt-*` 会出现在启动日志的「配置/数据文件损坏并已备份」那一行。
    **代价**：只在日志文件里，启动时不由界面展示；用户得自己找到
    `<程序目录>/logs/*.log`（无菜单入口，见下），且日志只报数量与最近一条路径，
    不会主动解释「objects/ 还在但无法找回」。所以它仍**不是**用户可见的恢复入口。
  - **缺口登记（本仓当前不具备面向用户的诊断出口）**：`corrupt_evidence` 旧文案
    写着「见帮助 › 诊断」，但 `main_window.py` 的「帮助」菜单只有三步上手 / 能力目录 /
    快捷键一览 / 命令面板，**没有诊断页**；启动日志也没有任何菜单可打开。本批顺手删掉了
    这句指向不存在位置的提示。要真正让 version_store 的损坏可见，需要一个「数据健康」
    面板或「打开日志」菜单项 —— 二者都落在 `main_window.py`，等卫星线，不在本目录。
  - `command_palette.recent_error()` 同理：命令面板的语义是「输入即过滤可执行动作」，
    把错误塞进结果列表会污染可执行项，**本次不接线**，等 main_window 统一诊断位。
  - `memory.get_status_text()` 已在 `1db672b3` 补上 `last_error`（与 scheduler/guardian
    同前缀「持久化错误:」）。03 仍直接读属性：那对没有该文本通道的对象也成立，
    且不依赖文本格式；要改用文本口径也可以，但别两种混用。
- **同一行被改动后怎么判断**：
  1. 能力位元组是否仍是 `(attr, title)` 二元组、渲染是否仍用 `name + "\n" + note`；
  2. 若 `_collect` 被改成返回 3 元组/数据类 → 本 patch 放弃，按新结构重写（不要硬改）；
  3. 若抽屉已改为读 `get_status_text()` 展示 → 优先用它，但仍要先确认
     `memory.get_status_text()` 已包含错误（当前不含）；
  4. `git log -L 153,164:desktop/capability_drawer.py` 确认这段没有他线改动。

---

## 通用重做判定（三问）

1. **对象还在吗？** 目标对象是否仍有 `last_error` / `load_error` 字段（grep 到赋值与清空）。
2. **界面已消费吗？** 目标文件里是否已经出现对该字段的读取（若有，放弃，不重复）。
3. **语义还成立吗？** 插入点是否仍是「数据加载完成之后」这一时序位置；
   搬家可以跟过去，语义变了就重写。

三问都通过才重做；否则只更新落点说明，不贴 patch。

## 生成与校验记录

- 生成脚本：临时目录 `gen_wiring_patches.py`（不进仓库，只读源文件）。
- 校验：`git apply --check <patch>` 三份均通过（生成时工作区）。
- 本目录只含 `.patch` 与本文，**没有任何源文件改动**。
