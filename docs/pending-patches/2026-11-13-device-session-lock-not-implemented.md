# 设备会话锁（gpredict 第三档）：待建单

**状态：§一~§四已落地（会话占用态 + 面板层）；2026-11-13 追加 §五写门与
§六 `main_window.py` 符号级后续清单。**
（历史：本单最初因 `desktop/device_panel.py` 里 5 行零读写的空壳声明而回退重写。）

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

---

## 五、写门（2026-11-13 落地）

### 5.1 契约

| 项 | 结论 |
|---|---|
| 位置 | `desktop/connect_request_store.py::update` —— 全仓唯一写入口，也是唯一写门 |
| 被拒行为 | **拒绝 + 可读原因**；不静默忽略（用户会以为改成功），不排队延后（会话结束时凭空多一次写入，事后无法解释） |
| 返回 | `WriteResult(request, rejected, reason)`；`request` 永远是当前权威值（占用期=冻结快照）。`__bool__` 为 `not rejected`，`if update(...)` 语义不变 |
| 拒绝副作用 | 不改 `_requests`、不下发硬件；但仍 `_notify(key)`——读侧必须回到冻结值，控件不能停在被拒的新值上 |
| 原因文案 | `_session_reject_reason(key)`：`设备会话进行中（{key}）：本次改动未写入，采样率/增益/PPM/带宽保持本次会话的生效值。请先断开设备（结束会话）再修改。` |
| 无绕过标志 | 断开写回由 `device_panel._write_back_session` 先 `end_session(key)` 再走同一个 `update`；调用方随后的 `end_session` 幂等 |

### 5.2 连接流程为什么不会被自己的门拒（裁定②）

`_apply_connect_params` 的调用点（main_window L6720）晚于
`_panels_set_sdr_connected(True)`（L6647），会话在参数下发之前就已建好。
两条路里选了 **`begin_session(key, initial=...)` 收下初始载荷**：

- 理由：另一条「先下发后建会话」要把建会话挪到 `_apply_connect_params` 之后，
  改主窗口两段时序、中间还夹着 UI 通知；让 `begin_session` 收下初始载荷，
  则「起点值」成为会话的一部分，此后门关闭，**连接流程自身零拒绝**。
- 语义：`initial` 只写**会话快照**（实际生效值），**不写意图**；与 `update`
  共用 `_normalize_values` 做同一套校验，且不经过门。
- 意图 vs 快照：意图=用户想设的，快照=本次实际生效的，两者只在断开写回时
  合一（否则「写回」不可观测）。

覆盖用例：`tests/test_session_write_gate.py::test_connect_flow_is_never_rejected`
（intent 30 → initial 35：快照 35、意图仍 30、`request_for` 等于快照）。

### 5.3 被拒的三条断言（裁定③）

`tests/test_session_write_gate.py::test_ai_write_tools_are_blocked_and_never_touch_hardware`
同时断：

1. `backend.calls == []` —— `set_*` 一次都没被调用；
2. `store.intent_for(key)` 保持原值；
3. 原因非空且含「会话」，`error == "device_session_locked"`。

面板侧同名判据在
`tests/test_device_panel_session_lock.py::test_render_stays_on_session_snapshot_while_other_panel_writes`
（`rejected is True` + `reason` 非空 + 意图/回显不变）。

### 5.4 AI 侧判定（`mbdsdr_ai/sdr_tools.py`）

- `_desktop_request_store()`：只用 `sys.modules` 取**已导入**的单例（裸名优先），
  绝不 `import`——包名导入会造第二份空存储，门永远开着；
- `_session_identity_key(backend)`：`driver == "rtltcp"` 或 identity 为空 → 空串
  （不参与）；否则 `device_identity.describe` 出键；
- 加门点：`_set_sample_rate_h` / `_guard_backend_set` /
  `_set_ppm_correction`（在 `clamp_ppm` 之前）→
  `ToolResult(success=False, error="device_session_locked")`；
- 独立进程没有桌面 store → 门不参与，跨进程互斥由 `device_owner` 承担
  （不是本门的职责）。

### 5.5 反证（mutation）记录

| 改坏什么 | 期望 | 实测 |
|---|---|---|
| 摘掉 store 的写门 | 会话中写入被接受 | 10 条红 |
| 摘掉 AI 侧门 | `sdr_set_sample_rate` 变成功 | 红 |
| `begin_session(initial)` 先冻结再走 `update` | 连接流程被自己拒 | 红（30 vs 35） |
| `_write_back_session` 去掉 `end_session` | 写回停在旧意图 | 红（30 vs 33） |

### 5.6 状态切换必须同时换 tooltip（2026-11-13 全量排查）

规矩：**tooltip 要么设成当前状态专属的内容，要么清空**；不许让上一态的
一句话留在控件上。只 `setText` 不碰 tooltip，等于给用户一个指向已隐藏按钮
或已解除限制的残留提示。本笔修掉的站点与判据：

| 站点 | 上一态残留 | 处置 | 断言 |
|---|---|---|---|
| `device_panel._set_owner_state` | unknown 态「点查看诊断」，而 `owner_diag_btn` 已隐藏 | tooltip 换成「等对方断开后点重试」 | `test_owner_unknown_to_occupied_drops_diag_tooltip`：不含「查看诊断」且含「重试」 |
| `device_panel._render_request` | 热插拔 `event.detail` / 「用 Zadig 装 WinUSB 驱动」 | tooltip 换成身份告警专属说明，**并保留**最近 3 条历史告警（`recent_warnings`，`test_recent_warnings_surface_on_device_panel` 依赖它） | `test_render_identity_warning_replaces_stale_tooltip`：不含「Zadig」 |
| `device_panel._on_connect_clicked`（采样率/增益范围未知、身份拿不到三处） | 上一轮身份告警仍在 tooltip | 清空 | `test_connect_precheck_hints_drop_stale_tooltip`：不含「Zadig」 |
| `module_panel.set_sdr_connected` | 上一轮写门拒绝的「会话占用中」 | 进入本方法先清空 | `test_reconnect_clears_stale_session_tooltip`：`toolTip()==""` |
| `module_panel._push` | 把 `result.reason` 逐字复写到 tooltip（无补充信息） | 不再设 tooltip（原因已在标签上） | `test_rejected_write_shows_reason_without_duplicate_tooltip` |

同类新增站点照此办理：状态文案与 tooltip 必须同源切换，补一条「切换后
`toolTip()` 不含上一态关键词」的断言。

## 六、`main_window.py` 释放后的符号级改动清单（一条命令可落）

前提：`desktop/main_window.py` 当前被他人持有，本单不碰。以下按符号列出，
**要删的符号和替代来源写死**，拿到文件后照单执行。

### 6.1 删除的私有状态（裁定④）

| 符号 | 位置 | 处置 | 替代来源 |
|---|---|---|---|
| `self._gain_intent_db` | `_set_gain_intent`（L7844，唯一赋值 L7856，全仓无读取） | **删除** | `connect_request_store.intent_for(key).gain_db`；会话中改读 `request_for(key).gain_db` |
| `self._last_ppm_applied` | `__init__` L1210；`_apply_connect_params` L7552/L7554；`_on_ppm_changed` L8365；`_restore_ppm_display` L8376 | **删除** | 会话中 `request_for(key).ppm`（冻结生效值），会话外 `intent_for(key).ppm` |

两者都是「第二真值」：store 才是会话期唯一真相。`_gain_intent_db` 当前只写
不读，可直接删；`_last_ppm_applied` 只在 `_restore_ppm_display` 读，改读 store。

### 6.2 四个用户改动槽要过门

| 符号 | 位置 | 改动 |
|---|---|---|
| `_on_gain_changed` | L7907 | 碰硬件前先 `store.update(key, gain_db=float(db))`；`rejected` → 滑杆回 `result.request.gain_db`、`statusBar` 显示 `result.reason`、**不调** `backend.set_gain`、**不落盘**。L7922 `backend.set_gain` 与 L7931 `request_tool.emit("sdr_set_gain")` 是同一意图的两条下发路径，收敛为一条（建议保留直接 `set_gain`） |
| `_on_sample_rate_changed` | L8111 | 同上；否则 `self._record_sr` / 流水线 / 硬件三处会在会话中被改。`settings.set("sample_rate_hz")`（L8151 / L8280）只在门通过后执行 |
| `_on_ppm_changed` | L8345 | 同上；失败回填改用 `request_for(key).ppm`（见 6.1） |
| `_on_vfo_bandwidth_changed` | L8437 | 同上；`settings.set("bandwidth_hz")`（L8482）只在门通过后执行 |

门的键：`device_panel._current_dev_key`（连接成功后已记）；未连接（`None`）时
不参与。回滚/读回值一律取 `WriteResult.request`，不要在槽里另存副本。

### 6.3 连接起点

- L6647 `_panels_set_sdr_connected(True)` + L6720
  `_apply_connect_params(backend, sample_rate=..., bandwidth=..., ppm=..., gain=...)`：
  把同一组参数作为 `ConnectRequest` 传给 `DevicePanel.set_connected(..., initial=...)`，
  使会话快照=实际下发值。
- 不要改这两个调用的先后：`begin_session` 已在 `set_connected(True)` 内调用，
  重复调用返回首个快照（幂等），因此传 `initial` 必须与首次调用同批到达。

### 6.4 `control_panel.set_sdr_connected`（L292）

当前实现连接成功后**启用**参数控件，与 gpredict 第三档相反。改动：

- 会话占用期间把 `sample_rate_combo` / `gain_slider` / `ppm_spin` /
  `vfo_bw_combo` / `agc_check` / `agc_speed_combo` 置灰，并给 tooltip 说明
  「会话进行中，断开后可改」；
- 断开后恢复。现有 `connected=False` 分支只表达「未连接」，需拆成「未连接」
  与「会话占用」两种理由，不能共用一句提示。

### 6.5 `settings` 第二真值（裁定④）

- `settings.set("sample_rate_hz"/"gain_db"/"bandwidth_hz")` 只在门内成功写时落盘；
- 会话期间 store 是唯一真相，`settings` 不得领先于 store；
- 现有「仅真实成功后落盘」的注释对硬件成立，但门拒绝时硬件本就没被调，
  必须在写盘前显式 return。

## 七、`mbdsdr_ai/core/controller.py` claim 接管（审计事实）

- 文件：`mbdsdr_ai/core/controller.py`
- 原 owner：`A03第一批线`
- 原 claim 心跳：2026-11-13 02:48:47（TTL 60min）；接管时已过期约 190min
- 原 pid：1960 —— `Get-Process -Id 1960` 查无此进程（pid 仅诊断，不参与判定）
- 在途改动：`git status` 该文件无未提交改动
- 接管判据：`expired=PASS no_inflight=PASS`；审计日志
  `D:\mbdsdr\.locks\claim_takeovers.log` 记 `action=orphan_takeover`（含原 owner、
  过期时长、pid 状态、理由）
- 新持有者：`设备会话锁线`，TTL 60min
- 结论：controller 运行在独立进程，没有桌面 `connect_request_store` 单例，
  本写门不参与，不改其行为；跨进程互斥仍由 `device_owner` 承担。
