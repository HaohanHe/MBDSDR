# 设备会话锁（gpredict 第三档）：待建单

**状态：未开工。2026-11-13 从 `desktop/device_panel.py` 回退了 5 行空壳声明。**

回退的是这三行加一行注释：

```python
# 会话锁（gpredict 第三档）：连接期间参数控件置灰、值原样保留，
# 断开时把本次会话实际生效的值写回唯一请求存储。见 set_connected()。
self._session_key: Optional[str] = None
self._session_values: Dict[str, object] = {}
self._session_texts: Dict[str, str] = {}
```

回退理由：全仓零读写点，`set_connected()` 里没有任何对应实现，配套的
`tests/test_device_panel_session_lock.py` 在台账里有 claim 但**实体从未创建**。
「声明了但不存在」比不写更难查——它让后来的人以为功能已在。

---

## 一、要做的功能（意图）

gpredict 的第三档占用行为：**一台设备连着的时候，参数控件不许改**（置灰），
界面上显示的必须是「本次连接实际生效的值」，不是别人刚写进 store 的新意图；
**断开那一刻**才把本次会话真正生效的值一次性写回唯一请求存储。

现状：**数据层已经全部就位，缺的只有面板这一层。**

| 已有 | 位置 | 说明 |
|---|---|---|
| `begin_session(key)` | `desktop/connect_request_store.py::begin_session` | 冻结首次快照，重复调用不覆盖 |
| `end_session(key)` | `desktop/connect_request_store.py::end_session` | 只清占用态，不写回 |
| `session_values(key)` / `is_occupied(key)` | 同上 | 读「本次生效值」与占用态 |
| `request_for(key)` | 同上 | 占用期间**读侧自动返回冻结快照**，另一面板改不动 |
| 连接/断开时已调 | `desktop/device_panel.py::set_connected` | 已 `begin_session` / `end_session` |

也就是说：数据层判定已经接上，**但面板没有把 `_sessions` 的占用态反映到控件上**，
所以用户现在仍然能在连接期间改参数，改了也不生效（要等下次连接），界面上看不出来。

## 二、缺哪些件（照单开工）

1. **置灰范围与时机**
   - 控件：`sr_edit` / `gain_edit` / `ppm_edit`（带宽仍未在面板上暴露，别顺手加）。
   - 时机：`set_connected(True)` 且**身份确认成功**之后置灰；身份确认失败那条分支
     （`_connected_device is None`）不置灰——那台设备根本没进会话。
   - 断开：`set_connected(False)` 里 `end_session` 之后恢复可编辑。
   - 不要在 `connect_btn` 点击时就置灰：连接可能失败，失败要还回可编辑。

2. **回显用哪个值**
   - 连接期间 `_render_request()` 必须走 `_req_store.request_for(key)`（它已冻结），
     **不要**再另存一份 `_session_values` 副本——回退掉的那三行就是这个思路，
     等于在面板里造第二份真值，一定会和 store 不一致。
   - 断开后 `request_for` 自动恢复最新意图，面板重新渲染即可。

3. **写回时机与内容**
   - 只在 `set_connected(False)` 且此前确实 `is_occupied(key)` 时写回一次，
     走 `_req_store.fill_payload(dev, key)`（唯一写入口），不直接写 `_requests`。
   - 「写回」到底是把会话生效值**写回意图**，还是**丢弃意图回滚到生效值**，
     属产品决策，先看 `docs/research/2026-11-12-gpredict-occupancy-decision.md`；
     数据层刻意不做这个决定（`end_session` 只清占用态）。

4. **与 `parse_sample_rate()` 的关系**
   - 本文件已有 `parse_sample_rate()`（采样率输入宽容：`2048000` / `2.048M` /
     `2.048 MS/s` 都收），是采样率唯一的解析入口。
   - 写回时用它解析 `sr_edit.text()`；解析失败不要写回、也不要把 0 Hz 写进 store
     （`update()` 会拒绝非法值并记日志，但面板应在提交前先挡，给出可读提示）。
   - 连接期间置灰意味着 `parse_sample_rate()` 只在断开写回路径被调用一次，
     不要在两处各解析一遍。

5. **测试要验什么**（`tests/test_device_panel_session_lock.py`，需新建）
   - 连接成功 → 三个控件 `isEnabled() is False`；断开 → 恢复 `True`。
   - 连接期间另一面板改 store → 本面板回显**不变**（验 `request_for` 冻结被真正用上）。
   - 连接期间非法输入（`abc` / `0`）→ 控件不可编辑，不会写进 store。
   - 断开 → 写回一次，且 `is_occupied(key)` 变 False；重复 `set_connected(False)` 幂等。
   - 连接失败（身份确认不了）→ 控件保持可编辑，不产生会话。

## 三、不要做的事

- 不要为了「先占位」再往 `__init__` 里加字段：字段必须和消费点同一笔提交。
- 不要新加全局开关或环境变量来切这套行为；它是产品行为，不是实验特性。
- 不要在这里补跨进程占用登记：后端 `connect()` 已按同一身份键 claim。

## 四、已知边界（2026-11-13 实现时确认）

- **快照里没有的字段不写回。** 会话快照的 `sample_rate_hz` / `gain_db` / `ppm`
  为 `None` 时，断开写回跳过该字段，不拿界面预填默认值顶替——预填值不是本次
  实际下发值，写回去等于伪造意图。快照里有的字段仍按生效值写回，只跳过缺失
  字段，不整笔放弃。覆盖用例：
  `tests/test_device_panel_session_lock.py::test_write_back_skips_fields_missing_from_snapshot`。
- **模块面板没有连接态。** `desktop/module_panel.py` 收不到 `set_connected`，
  读侧只靠 `connect_request_store.request_for()` 在占用期间返回冻结快照来保护
  （其回调渲染不会把会话生效值拉走）。要在模块面板也置灰，得先给它新增连接态
  与 `set_connected` 通道，属另一笔，不在本单范围。

### 离屏实跑记录（2026-11-13，非单测复述）

环境：`QT_QPA_PLATFORM=offscreen`，临时 HOME/APPDATA 与 QSettings/布局目录（与
`tests/conftest.py::isolated_profile` 同口径）；起真实 `DevicePanel`，走真实的
`_on_connect_clicked → connect_requested → set_connected(True/False)` 路径。
两处替身：设备枚举置空（无真机），`connect_requested` 由本地处理器按主窗口语义
回填 `set_connected(True, label, dev)`（身份经 pending 设备确认）。设备：
`rtlsdr|serial:A-0001`，采样率 2.048M、增益 30、PPM 0。

1. 连接后 `isEnabled()`：`sr_edit=false, gain_edit=false, ppm_edit=false`，
   `is_occupied(key)=true`。
2. 会话中面板采样率文本 `"2.048e+06"` = 2 048 000 Hz，与
   `request_for(key).sample_rate_hz` 一致；期间另一处把意图改成 1 536 000 Hz，
   面板文本不变（仍 `"2.048e+06"`），`request_for` 仍为 2 048 000，意图为
   1 536 000——显示读的是冻结值，不是新意图。
3. 断开后 store 落值：`sample_rate_hz=2 048 000`、`gain_db=30`、`ppm=0`、
   `is_occupied=false`，三个控件恢复 `true`。
4. 第二次 `set_connected(False)`：`update` 调用 0 次、`fill_payload` 调用 0 次，
   store 落值不变——断开写回只发生一次。
