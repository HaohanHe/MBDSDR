# 交接单：`desktop/main_window.py` 三处文案指路（A6 / A7 / A10）

- 日期：2026-11-11
- 目标文件：`desktop/main_window.py`
- 为什么交接：该文件当前由 **启动工单线/W26** 持有（claim 至 23:17），且有 47 行未提交改动。
  按纪律不在别人 claim 的文件上提交，故写成交接单；内容与上下文自足，谁落地谁提交。
- 行号为 2026-11-11 22:1x 工作区快照（含持有线未提交改动），落地前请重新定位字符串。

---

## A6 指路到不存在的设置项（P1）

**现文案**（`desktop/main_window.py:11844-11845`，地点面板创建失败分支）：

```python
"地点面板不可用；可在「设置 → 卫星与多普勒 → 地面站经纬度」"
"手填位置（纬度与经度要成对填写）"
```

**建议**：

```python
"地点面板不可用；可在「设置 → 卫星与多普勒 → 观测地点纬度/经度」"
"手填（两项要成对填写）"
```

**触发条件**：`_open_location_panel()` 里 `panel is None`（地点面板创建失败/不可用）。

**反证**：打开设置 → 卫星与多普勒，搜「地面站」0 结果；实际项名是
「观测地点纬度 / 观测地点经度」（`desktop/config_schema.py:441/449`）。

---

## A7 保存失败只给「请重试」，不给原因和路径（P2）

**现文案**（`desktop/main_window.py:5430`）：

```python
QMessageBox.warning(self, "保存失败", "无法保存布局，请重试。")
```

**建议**（`dlm` 即 `self.dock_layout`，`dlm.store.path` 是布局文件真实路径）：

```python
QMessageBox.warning(
    self, "保存失败",
    f"无法保存布局「{name}」。\n"
    f"布局文件：{dlm.store.path}\n"
    "常见原因：目录只读、磁盘满、文件被占用；处理后重试仍失败请附日志。")
```

**触发条件**：`dlm.save_user_layout(name)` 返回 False（写盘失败/异常）。

**反证**：把 `%APPDATA%/MBDSDR` 设为只读后点「保存」，弹窗仍只说「请重试」，
既没有路径也没有可区分的失败原因；`dock_layout_manager.py:430` 只把原因写进日志。

---

## A10 「自动持久化」只覆盖退出时三栏比例（P2）

**现文案**（`desktop/main_window.py:1636`，布局下拉 tooltip）：

```python
self.layout_combo.setToolTip("切换布局预设（可自由拖动，自动持久化）")
```

**建议**：

```python
self.layout_combo.setToolTip(
    "切换布局预设；拖动只改当前窗口，退出时才记三栏比例，"
    "自定义预设需点「保存」")
```

**触发条件**：悬停布局下拉（无需运行态）。

**反证**：拖动分隔条后直接结束进程再启动，三栏比例会恢复（说明退出时确实记），
但页签顺序/面板可见性不会——同一文件 `:1642` 的「保存」tooltip 自认
「把当前布局（页签顺序/三栏比例/面板可见性）存为自定义预设」，即完整持久化
必须点保存。「自动持久化」把两件事混成一句，属半假。

---

## 落地方式

1. `git log -1 -- desktop/main_window.py` 确认持有线已提交、工作区无他人未提交改动。
2. `python scripts/file_claim.py claim --owner <你的线> --ttl-min 60 desktop/main_window.py`
3. 逐条替换（三条可一笔提交，同一文件）。
4. `python scripts/safe_commit.py --files desktop/main_window.py --message-file <msg>`
5. 完成后把本交接单移到 `docs/pending-patches/archive/` 或删除，并在
   `docs/research/2026-11-11-copy-claims.md` §6 补一笔。
