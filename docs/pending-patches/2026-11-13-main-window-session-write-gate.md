# `desktop/main_window.py` 在途 hunk 归属（一）：设备会话写门 / PPM 唯一真值

登记日期：2026-11-13
登记人：收尸线（父会话 `6ae85f56a16a76df1598024e`）
触发：`73b75378` 逐段排除 `desktop/main_window.py` 的在途行段后提交；本文件把
**14 组**（`-U3` 合并口径）定性归口。口径与漂移：登记时（HEAD `c17f7011`，
2026-11-13 20:24）该文件 `-U3` 17 组 / `-U0` 31 段；20:35 复测为 `-U3` 20 组 /
`-U0` 34 段，新增 3 组属 S1-S4 结果线，见
`2026-11-13-main-window-tool-success-wiring.md`。焦点链 3 组见
`2026-11-13-keyboard-focus-chain-unlanded.md`。14 + 3 + 3 = 20 组，无遗漏。

行号口径：`-U3` 会把相邻排除段合并成一个 hunk，本文件按**符号**登记，不写行号
（该文件每笔都在漂）。用 `git diff HEAD -- desktop/main_window.py` 对照时，按符号找段落。

## 一、归属与存活判定

- **原持有者：设备会话锁线**。
- claim 台账：`acquired_at=2026-11-13 06:46:40`、`ttl=60min`、`renew=0`；
  `2026-11-13` 晚在 `desktop/device_panel.py` 的接管记录里实测已 `EXPIRED(+675.7min)`，
  本文件登记时仍无续租。
- 进程 `pid=39716` 已退出；最后提交 `e6d5e269`（2026-11-13 07:12）。
- 结论：**线已停手、代码未落库**，这 14 段属于无主在途改动。

## 二、逐段定性（14 段）

判据只有一条：**HEAD 里有没有同样的逻辑**。下表「HEAD」列全是「无」，证据按符号给出，
不凭印象。

| # | 触发符号 | 改动摘要 | HEAD 是否已有 | HEAD 证据 |
|---|---|---|---|---|
| 1 | `_UNSET`（模块级哨兵） | 新增 `_UNSET = object()`，区分「未传参」与「显式传 None」 | 否 | `git show HEAD:desktop/main_window.py` 无 `_UNSET` |
| 2 | `_req_store` 导入 | `import connect_request_store as _req_store` | 否 | HEAD 无 `_req_store` |
| 3 | `_last_ppm_applied` 移除 | 删掉实例字段，PPM 生效值改由 store 提供 | 否 | HEAD:1211 `self._last_ppm_applied: Optional[int] = None` |
| 4 | `_session_store_key` | 新增：从 `device_panel._current_dev_key` 取会话键 | 否 | HEAD 无该符号 |
| 5 | `_panels_set_sdr_connected(..., session_initial=)` | 新增形参，连接时把本次载荷交给面板 | 否 | HEAD 签名只有 `(self, connected: bool)` |
| 6 | `_panels_set_sdr_connected` 调用点 | 传 `session_initial=_req_store.ConnectRequest(...)` | 否 | HEAD 调用只有 `self._panels_set_sdr_connected(True)` |
| 7 | `dp.set_connected(..., initial=)` | 连接载荷作为会话快照起点；并调 `control_panel.set_session_occupied` 置灰 | 否 | HEAD 只有 `dp.set_connected(bool(connected))` |
| 8 | `_on_ppm_changed` / 连接流程 | PPM 只 `_run(...)`，不再写第二状态 | 否 | HEAD:7570 `self._last_ppm_applied = (int(ppm) if ...)` |
| 9 | `_set_gain_intent` | 不再存 `_gain_intent_db`，store 是唯一真值 | 否 | HEAD:7784 `self._gain_intent_db = value` |
| 10 | `_on_gain_changed` | 会话占用时走写门：拒绝 + 可读原因 + 回填生效值 | 否 | HEAD 无 `_req_store.update` 调用 |
| 11 | 增益下发 | 删掉重复的 `request_tool.emit("sdr_set_gain", ...)` | 否 | HEAD:7859 仍有该 emit（同一下发两遍） |
| 12 | `_on_sample_rate_changed` | 会话占用时走写门，下拉回填生效值 | 否 | HEAD 无写门调用 |
| 13 | `_on_ppm_changed` + `_restore_ppm_display(revert=_UNSET)` | 回填改读 `request_for(key).ppm`；失败不再记请求值 | 否 | HEAD:8304 `revert = self._last_ppm_applied` |
| 14 | `_on_bandwidth_changed` / AI 增益入口 | 带宽与 AI 增益同样走写门，拒绝时回显生效值 | 否 | HEAD 无 `_req_store.update` 调用 |

**没有「判不出来」的段**：14 段都能落到同一归属（设备会话锁线）与同一主题（写门 + PPM 唯一真值）。

## 三、与这 14 段绑定的盘上文件（**不能单独提交**）

| 文件 | 状态 | 与 14 段的关系 |
|---|---|---|
| `desktop/control_panel.py` | 已改未暂存 | 新增 `_session_tip(...)`（14 处）与 `set_session_occupied`，供第 7 段置灰 |
| `desktop/connect_request_store.py` | 已暂存（+5 行） | 公开别名 `session_reject_reason()`，UI 显示拒绝原因用 |
| `desktop/device_panel.py` | 已改未暂存（3 hunk） | `set_connected(initial=)`，第 7 段的另一半 |
| `tests/test_main_window_session_gate.py` | 已暂存（新增 +278 行） | 主窗口写门用例 |
| `docs/tooltip-dynamic-baseline.json` | 已暂存（104 行） | 基线改成 `_session_tip(...)` 表达式；**单独落会红**：拿 HEAD 的 `control_panel.py` 跑 `scan_dynamic_tooltips()`，20 条里 8 条 stale（`self._session_tip(tip)` 等），`test_dynamic_baseline_entries_still_exist` 必失败 |
| `tests/test_hw_rollback_shows_device_truth.py` | 已暂存（+24 行） | 用例改成用 `connect_request_store.update(key, ppm=25)` 种「最后生效值」；**单独落会红**：HEAD 的 `_restore_ppm_display` 仍读 `_last_ppm_applied`（HEAD:8304），而 HEAD 版用例原本是 `mw._last_ppm_applied = 25` |

即：`docs/tooltip-dynamic-baseline.json` 与 `tests/test_hw_rollback_shows_device_truth.py`
不是「独立可落的小修」，它们是这 14 段的产品改动的**配套**。单独提交等于让 HEAD
的测试变红，不能按「各算各的」处理。

## 四、接取前提

1. `python scripts/file_claim.py status desktop/main_window.py desktop/control_panel.py desktop/device_panel.py desktop/connect_request_store.py` 全部无活跃持有者；`main_window.py` 现有别的线在改（窄屏 4px 线），**必须先协调提交顺序**，不要整文件 `git add`。
2. `git diff HEAD -- desktop/main_window.py > <本目录>/2026-11-13-session-write-gate.patch` 留底。
3. 按 §6.1.4 写接管记录（≤30 分钟宽限、进程、提交、在途四条必填）。
4. 一次提交只带「这 14 段 + 上表 6 个文件」，`-U3` 之外的他人 hunk 逐段剔除。

## 五、验收判据

沿用 `2026-11-13-orphan-initial-hunks.md` §五的四条（连接零拒绝 / 缺省等价 / 写门不被绕过 / 主窗口接线），
另加：

1. `tests/test_main_window_session_gate.py` 全绿，且**旧行为反证**：临时把写门短路（`_session_store_key()` 恒 `None`）→ 会话占用期间改增益/采样率/带宽/PPM 不再被拒，用例必须红。
2. `docs/tooltip-dynamic-baseline.json` 与 `desktop/control_panel.py` 同笔落地；单独落地时
   `test_dynamic_baseline_entries_still_exist` 必须红（本文件 §三已给出 8 条 stale 证据）。
3. `tests/test_hw_rollback_shows_device_truth.py` 与 `_restore_ppm_display` 改动同笔落地；
   只落测试时 `test_ppm_rejection_restores_last_applied` 必须红。

## 六、不接的合法出口

显式回退这 14 段并说明「不进写门批次」：

```powershell
git diff HEAD -- desktop/main_window.py > "$env:TEMP\session-write-gate.patch"
git checkout -- desktop/main_window.py   # 注意：会连带丢弃他人的 3 段焦点链 hunk，先按 hunk 拆
```

只允许「整批接」或「逐 hunk 明确回退」，不允许既不接也不回退、把半成品继续挂在盘上。
