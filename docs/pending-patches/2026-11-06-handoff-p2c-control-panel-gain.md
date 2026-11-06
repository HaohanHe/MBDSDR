# 对接说明：控制面板"未连接增益显示「—」"（P2-C 后半，交给 `设置架构线`）

> 写给正在改 `desktop/control_panel.py` 的那条线。**不指望你去看我的补丁存档**，
> 规则、行锚点、受影响的既有测试和反证都写在这里，照着并进你手上的文件即可。
> 生成时间：2026-11-06；基线 commit `d1616f9c`。

## 0. 先说冲突面

`desktop/control_panel.py` 工作树已经有**未提交改动**（`git diff --stat` 显示
89 insertions / 13 deletions，内容是 `set_gain_range` / `set_gain` / `set_frequency_hz` /
`set_vfo_bandwidth` 的"配置请求被替换"提示）。该文件 2026-11-06 03:35 取证时
**已无人 claim**，但 `set_gain_applied` 尚未出现（P2-C 未落库）。本单是在**那批
未提交改动之上追加**，不要 `git checkout` / `git stash` 回退那批东西。
行号随你的改动漂移，下面一律给**代码锚点**，行号只是 `d1616f9c` 的参考。

## 1. 问题

- 构造即显示 `0 dB`：`self.gain_val = QLabel("0 dB")`（基线 550 行）。
- `set_sdr_connected(False)`（基线 248 行）只 `setEnabled(False)`，**不清标签**：
  断开后仍挂着上一个数字。
- `set_connected(False)`（基线 292 行）同样不碰 `gain_val`。
- 结果：没插设备时，界面显示"增益 0 dB"，用户读成"当前增益是 0 dB"。
  0 dB 是有物理意义的（最小增益），和"不知道"混在一起就是假读数。

红线（与本仓 `_fmt_dbfs` / `format_doppler_status` 同一口径）：
**未知 → 「—」，绝不渲染成 0**；数字只在"真实施加成功"之后出现。

## 2. 状态口径（先对齐，再改代码）

| 状态 | 滑杆位置 | `gain_val` 文本 | tooltip |
|---|---|---|---|
| 未连接（默认） | 上次配置值（可动不了，禁用） | **「—」** | 「未连接：没有真实增益读数；滑杆位置是上次配置，未施加」 |
| 未连接 + 启动恢复了配置 20 dB | 20 | **「—」** | 「未连接：上次配置 20 dB（未施加）」 |
| 已连接，尚未施加增益 | 保留配置位置 | **「—」** | 「已连接：等待增益下发」 |
| 已连接，`backend.set_gain(20)` 成功 | 20 | **「20 dB」** | 现有设备能力说明（`_gain_base_tip`） |
| 已连接，用户拖到 25 且下发成功 | 25 | **「25 dB」** | 同上 |
| 已连接后断开 | 保留最后位置 | **「—」** | 回到"未连接"说明 |

要点：**滑杆位置是"配置意图"，标签是"读数"**。两者不再强行一致。
（这也是为什么不把滑杆归零——归零会抹掉用户配置。）

## 3. 改动（4 处 + 1 个新 setter）

### P2-C-1 初始文本（锚点 `self.gain_val = QLabel("0 dB")`）

```python
        self.gain_val = QLabel("—")
        self.gain_val.setMinimumWidth(36)
        self.gain_val.setMaximumWidth(80)
        self.gain_val.setToolTip("未连接：没有真实增益读数")
```

### P2-C-2 `set_sdr_connected`（锚点：`for w in widgets: ... w.setEnabled(self._sdr_connected)` 之后）

```python
        if not self._sdr_connected:
            # 断开即清读数：不让上一个数字冒充"当前增益"
            self.gain_val.setText("—")
            self.gain_val.setToolTip("未连接：没有真实增益读数；"
                                     "滑杆位置是上次配置，未施加")
```

连接分支**不要**主动写数字（此时还没有任何真实施加）；数字由
`set_gain_applied` 或用户拖动后写入。

### P2-C-3 `set_connected`（锚点：`self.set_sdr_connected(connected)` 调用之后）

`set_sdr_connected` 已经清了，这里不用重复；但 `elif not self._recording:` 分支
不要顺手写增益。保持"断开→—、连接→等真值"。

### P2-C-4 `set_gain`（基线 1277 行，启动恢复 setter）—— **按连接态分流**

现有实现无条件 `self.gain_val.setText(f"{db} dB")`，这是"未连接也显示配置值"的来源。
改成：

```python
    def set_gain(self, db: float):
        """恢复/回填 LNA 增益。滑杆永远采纳配置意图；标签只在连接态显示读数。

        未连接时标签保持「—」：20 dB 是"上次配置"，不是"当前增益"。
        """
        db = float(db)
        if not math.isfinite(db):
            return
        requested = int(round(db))
        db = int(max(0, min(49, db)))
        self.gain_slider.blockSignals(True)
        self.gain_slider.setValue(db)
        self.gain_slider.blockSignals(False)
        self._gain_requested_db = db
        if self._sdr_connected:
            self.gain_val.setText(f"{db} dB")
            self.gain_val.setToolTip(f"当前增益 {db} dB（后端钳位，实际档位以设备为准）")
        else:
            self.gain_val.setText("—")
            self.gain_val.setToolTip(f"未连接：上次配置 {db} dB（未施加）")
        if requested != db:      # 你现有的"配置请求被替换"提示，原样保留
            ...
```

`__init__` 里补 `self._gain_requested_db: Optional[int] = None`。

### P2-C-5 新增 `set_gain_applied`（给 `main_window` 回填真实读数用）

```python
    def set_gain_applied(self, db: float) -> None:
        """连接后由 main_window 在 backend.set_gain 成功后调用。

        与 set_gain 的区别：这是"已施加"的读数，未连接时**拒绝**渲染数字。
        """
        if not self._sdr_connected:
            return
        db = float(db)
        if not math.isfinite(db):
            return
        self.gain_slider.blockSignals(True)
        self.gain_slider.setValue(int(round(db)))
        self.gain_slider.blockSignals(False)
        self.gain_val.setText(f"{db:g} dB")
```

`main_window` 侧调用点**已由 ux-basis 落库**（2026-11-06，P2-D）：
`_backfill_real_rf_readings()`（连接成功、`backend.set_gain` 之后）与
`_on_gain_changed` 的失败回滚，都通过 `hasattr(cp, "set_gain_applied")` 守卫调用。
你这边只需要把方法暴露出来，落库即自动生效；未落库时控制面板保持「—」，
不会报错也不会编造读数。

### 保持不变（别顺手改）

- `_on_gain_changed`（`self.gain_val.setText(f"{db} dB")`）：只在连接态可达
  （滑杆禁用），语义正确。
- `set_gain_range`：它是"范围收放"，不是读数；现有 `applied != requested` 时
  写标签的行为保留（那是钳位提示，有原因）。
- 你新加的 `_gain_base_tip` 工具链全部保留。

## 4. 受影响的既有测试（必须一起改，否则你就是新的基线红）

| 用例 | 现在断言什么 | 改后为什么会红 | 怎么改（不许放宽比较） |
|---|---|---|---|
| `tests/test_settings_semantics.py:194` | `assert "dB" in c.gain_val.text()`（未连接构造） | 文本变「—」 | 改成断言未连接时 `== "—"`，并另建 `c.set_sdr_connected(True); c.set_gain(20)` 后 `== "20 dB"` |
| `tests/test_ui_stability_resources.py::TestNanIsEmptyNotFabricated::test_control_panel_nan_gain_keeps_previous` | `set_gain(20.0)` 后 `before.replace(" dB", "") == "20"`，NaN 后保持 `before`（已在 `abc52bd8` 按不变式重写，全文件 24 passed） | P2-C 后 `mw` 夹具未连接 → `before` 变成「—」→ 该断言红 | 用例开头补 `mw.control_panel.set_sdr_connected(True)` 再 `set_gain(20.0)`；**断言本身一字不改** |
| `tests/test_desktop_ui_responsiveness.py:105` | `"25" in gain_val.text()`（先 `set_sdr_connected(True)` 再拖） | 不受影响 | 不用动 |
| 新 `tests/test_control_panel_gain_honesty.py` | — | — | 见 §5 |

## 5. 新守卫用例（4 条，落库时一起加）

```python
def test_unconnected_default_is_dash():
    cp = ControlPanel()
    assert cp.gain_val.text() == "—"

def test_restored_gain_not_shown_as_reading():
    cp = ControlPanel()
    cp.set_gain(20.0)                 # 启动恢复路径
    assert cp.gain_slider.value() == 20, "配置意图要保留"
    assert cp.gain_val.text() == "—", "未连接不许把配置值当读数"
    assert "上次配置 20 dB" in cp.gain_val.toolTip()

def test_applied_gain_requires_connection():
    cp = ControlPanel()
    cp.set_gain_applied(20.0)         # 未连接：拒绝
    assert cp.gain_val.text() == "—"

def test_disconnect_clears_last_reading():
    cp = ControlPanel()
    cp.set_sdr_connected(True)
    cp.set_gain_applied(20.0)
    assert cp.gain_val.text() == "20 dB"
    cp.set_sdr_connected(False)
    assert cp.gain_val.text() == "—", "断开后仍显示上次增益=把历史值当读数"
```

## 6. 反证（改回旧行为 → 哪个用例红）

| 变异 | 期望变红 |
|---|---|
| `QLabel("—")` 改回 `QLabel("0 dB")` | `test_unconnected_default_is_dash` |
| `set_gain` 未连接分支去掉，恢复无条件 `setText(f"{db} dB")` | `test_restored_gain_not_shown_as_reading` |
| `set_sdr_connected` 的断开清理两行删掉 | `test_disconnect_clears_last_reading` |
| `set_gain_applied` 去掉 `_sdr_connected` 守卫 | `test_applied_gain_requires_connection` |

四条都必须实测红一次再恢复（仓库规矩：不留绿色假证据）。

## 7. 验收（手测）

1. 启动（未插设备）：增益显示「—」，滑杆可看不可拖，tooltip 写"上次配置"；
2. 插设备连接：仍显示「—」直到 main_window 下发成功 → 变真实数字；
3. 拔设备/断开：立刻回「—」，不留最后数字；
4. 未连接时重启，配置文件里的 `gain_db` 不被本改动改写（只显示，不回写）。
