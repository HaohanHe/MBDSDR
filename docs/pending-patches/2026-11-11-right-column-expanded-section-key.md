# 交接单：登记 `right_column_expanded_section`（右栏展开态）

> 提出：2026-11-11（D-10 手风琴收口）；**执行方：设置线**（它正持有
> `desktop/config_schema.py` 与设置页分组展开态键 `settings_expanded_groups`，
> 同族键同批登记）。
> 本线（roadmap/D10）**不接管** `config_schema.py`；右栏实现见
> `desktop/right_column.py`（`_EXPANDED_KEY` / `_remember_expanded` /
> `_restore_expanded`）。
>
> **状态：已登记**——`20652e90`（settings_expanded_groups +
> right_column_expanded_section），登记后 `git status --porcelain --
> desktop/config_schema.py` 为空，右栏用例 9/9 绿。本单保留作为键规格与
> 坑位存档。

## 一、键规格（五个字段写死）

| 字段 | 值 |
| --- | --- |
| 键名 | `right_column_expanded_section` |
| 类型 | `str`（`SettingItem.kind="text"`） |
| 默认值 | `""` |
| 合法值域 | 右栏节 id 集合：优先 `tabData` 字符串（面板 id），没有时用节标题；口径唯一来源是 `right_column.RightColumn._section_key()` |
| 空串语义 | 「未设置」：首次启动 / 恢复时只展开首节，不写任何「全部折叠」态 |

建议落点：`desktop/config_schema.py` 的 `ALL_ITEMS` →「显示与主题」组末尾
（`show_waterfall` 之后），`exposed=False`（内部键，不进设置面板）。

```python
    SettingItem(
        key="right_column_expanded_section", group="显示与主题", kind="text",
        label="右栏上次展开的分组", default="",
        help=("内部键：右栏手风琴（同一时刻只展开一节）记住上次展开的是哪一节。"
              "存节的稳定 id（面板 id，不用下标——面板菜单增删节后下标会漂）；"
              "空值 = 首次启动，界面只展开首节；未知 id（面板被关掉/旧版本）"
              "同样回落为只展开首节。"),
        source="desktop/right_column.py:_EXPANDED_KEY"
               "（W48 / D-10 用户裁定；读取点 _restore_expanded）",
        exposed=False,
    ),
```

## 二、两个坑（必须知道）

1. **`DesktopSettings.set()` 对未登记键静默忽略**（`settings.py:485`
   `if key not in DEFAULTS: return`）。所以登记前
   `tests/test_right_column_accordion.py` 的「重启还原」用例是红的
   （8/9 绿），登记后应变 9/9 绿——**这是预期落差，不是回归**，不要拿它
   判右栏实现坏。
2. **坏值/未知 id 回落只展开首节**：空白串、非节 id 字符串、旧版下标式
   值（如 `"0"`）都落到首节；绝不因一个坏字符串把 20 节全展开，也不
   静默全折叠。全部折叠是临时查看动作，不写入本键。

## 三、读取方与写点

- 写：用户点开某一节标题 → `RightColumn._remember_expanded()` →
  `settings.set("right_column_expanded_section", _section_key(index))`
  （`DesktopSettings` 防抖 500ms，退出 `flush()`）。
- 读：`RightColumn.showEvent` 首次显示 → `_restore_expanded()`；
  配置源优先显式注入，否则取宿主窗口的 `self.settings`（主窗口唯一实例）。
- 合法值域会随面板菜单增删节变化；读到未知 id 即按坑 2 回落。

## 四、验收

```powershell
python -m pytest tests/test_right_column_accordion.py -q   # 期望 9 passed
python scripts/file_claim.py status desktop/config_schema.py  # 确认归属
git status --porcelain -- desktop/config_schema.py            # 提交后应为空
```

登记并提交后请回报：提交哈希 + `git status --porcelain -- desktop/config_schema.py`
（应为空）。右栏线据此复跑 9/9 并把 D-10 三档高度重测排进计划。
