# 代码守卫：catch-all 静默吞噬（P13 补充）

- 结论：全仓 catch-all 处理器 **1884** 处，其中「无记录」静默吞噬 **1399** 处，白名单 **1** 处，存量 **1398 处**（`ERROR` 573 / `WARN` 825）。
- 策略：**棘轮 + 定点清除**。存量不逐处修；基线只许降不许升，新代码必须干净；只清用户能感知“点了没反应/功能凭空消失”的点。
- 基线：`docs/code-guards-baseline.json`（按文件记 `ERROR`/`WARN`，快照 2026-11-05）。任何文件超基线、新文件出现静默 catch-all、总量上升 → 红。
- 扫描快照：1061 个 Python 文件（提交树口径；工作区另含未跟踪/在途文件时以脚本实时输出为准）。
- 事故首例：`desktop/rf_sky_view.py::RFSkyView._find_body_at` 的 `float(None)` 抛 `TypeError` 被 `try/except Exception: pass` 吞掉，深空天体既不可见也不可点，无日志无报错。该函数快照内 6 处（2 ERROR + 4 WARN）。
- 本轮只报不修；`docs/limitations-register.md` 未触碰，`desktop/main_window.py` 未触碰。

## 判据

扫描器 `scripts/lint_swallow_all.py` 用 AST（不是正则）识别 catch-all：

- catch-all = `except:` / `except Exception:` / `except BaseException:`，或元组里含 Exception/BaseException；
- 静默 = 处理器体内没有「记录原因」的动作（日志、print、warning/error 状态字段、错误容器 append），也没有 `raise`：
  - `EMPTY`：只有 `pass`、`...` 或注释（AST 里注释不可见，等同 Pass）→ **ERROR**
  - `SILENT_CONTROL`：只 `return None` / `return <常量>` / `continue` / `break` → **WARN**
  - `SILENT_FALLBACK`：有语句但无记录（塞默认值后继续）→ **WARN**
- 有记录（RECORDED）或真正重新抛出（RERAISE）→ 只计数，不入缺陷；
- **禁令**：`except Exception: raise` 这种单条裸 `raise`（`FAKE_RERAISE`）判 ERROR；`contextlib.suppress(Exception)` / `suppress(Exception)`（`FORBIDDEN_SUPPRESS`）判 ERROR。两者都不可进白名单，防止绕过扫描器。
- 裸 `except:` 当前为 **0** 处。

每处白名单必须同时回答三问 + 复核期限，缺一即不合格：

1. `reason`：为什么允许降级（必须是外部边界：第三方库/网络/硬件/平台）；
2. `user_visible`：降级后用户看到什么（不能是「什么都没有」）；
3. `test`：哪条测试覆盖这条降级路径（文件必须存在）；
4. `review_by`：`YYYY-MM`，到期即判不合格，必须复核后再续期。

## 白名单（1 处）

| 位置 | ① 为什么允许降级 | ② 用户看到什么 | ③ 覆盖测试 | ④ 复核 |
| --- | --- | --- | --- | --- |
| `mbdsdr_ai/audio_out.py:37` | 外部边界：`sounddevice`/PortAudio 是可选依赖，导入失败（库缺失、平台无 PortAudio、后端初始化失败）必须整体降级，不能拖垮整个应用。 | `_SD_AVAILABLE=False`，`AudioPlayer.available=False`；`start()` 返回 False 并保持静音；`write()` 返回 0；UI 音频入口显示不可用，而不是崩溃或假装播放。 | `tests/test_swallow_guard.py::test_audio_player_degrades_without_sounddevice`（拦截 `import sounddevice`，断言 `available/start/write/stop` 的降级行为）。 | 2027-01 |

其余 1398 处**没有一处能同时回答三问**：绝大多数连日志都没有，更谈不上覆盖降级路径的测试。因此不把「可选依赖 = 自动豁免」当作批量白名单——豁免必须逐条给出三问，否则就是静默丢功能。

## 分布

按目录：`mbdsdr_ai` 692 / `desktop` 574 / `tests` 62 / `experiments` 32 / `scripts` 26 / `tools` 8 / 根脚本 3 / `docs` 1。

按类别：`ERROR/EMPTY` 574、`WARN/SILENT_CONTROL` 523、`WARN/SILENT_FALLBACK` 301。

高风险前 10（完整 256 个文件见附录 A）：

| 文件 | 合计 | ERROR | WARN |
| --- | --- | --- | --- |
| `desktop/main_window.py` | 341 | 239 | 102 |
| `mbdsdr_ai/sdr_backend.py` | 112 | 50 | 62 |
| `desktop/rf_sky_view.py` | 71 | 21 | 50 |
| `mbdsdr_ai/agent.py` | 51 | 11 | 40 |
| `mbdsdr_ai/sdr_tools.py` | 49 | 17 | 32 |
| `desktop/receive_pipeline.py` | 21 | 18 | 3 |
| `desktop/dock_layout_manager.py` | 20 | 11 | 9 |
| `mbdsdr_ai/device_manager.py` | 16 | 8 | 8 |
| `mbdsdr_ai/core/controller.py` | 15 | 1 | 14 |
| `mbdsdr_ai/orbit_tools.py` | 13 | 0 | 13 |

典型危险形态（都与「用户看不见的功能丢失」同构）：

- 设备打开/调谐失败后 `pass`：状态栏仍显示「已连接」，实际无数据（`sdr_backend.py` 多处）。
- 面板/控件构建异常后 `pass`：界面缺块且无提示（`main_window.py::_build_central_widget`、`desktop/*_panel.py`）。
- 天体/星表查找失败后 `continue` / 置 `None`：图层绘制与点击命中整段失效（`rf_sky_view.py::_find_body_at`）。
- `float(None)`、`KeyError`、`AttributeError` 等**编程错误**被 catch-all 吞掉：本类一律算缺陷，不能以「兜底」名义保留。

## 反证（棘轮，不再靠全量缺陷判红）

`tests/test_swallow_guard.py` 分四组：

- 扫描器行为组：DSO 事故同构（`float(None)` 被 `except Exception: pass` → `ERROR/EMPTY`）；静默 return → WARN；窄异常不误报；有记录/真重抛不误报；**假重抛**判 ERROR；**`suppress(Exception)`** 判 ERROR 且不可白名单；白名单三问缺项/缺 `review_by`/过期均判不合格；三问齐全才豁免。
- 降级路径组：`audio_out` 无 `sounddevice` 时整链降级（覆盖唯一白名单）。
- 棘轮组：新文件出现静默 catch-all → 越线；存量下降 → 不越线；单文件 ERROR 增长 → 越线；仓库当前扫描 `check_ratchet` 必须为空。
- 现状组：白名单有效且未失效；禁令构造为 0；扫描确定性用固定夹具验证（仓库在途文件会被其他线并发改写，不能当夹具）。

修前必红的证据保留在扫描器本身：旧版 `test_repo_has_no_unwhitelisted_silent_catch_all` 曾以「须修 1398 处」失败；现在改为基线棘轮——存量不阻塞，新增立刻红。

复跑：

```
python scripts/lint_swallow_all.py                        # 棘轮判定（越线才退 1）
python scripts/lint_swallow_all.py --list                 # 打印每一处 catch-all
python scripts/lint_swallow_all.py --json out.json        # 完整明细
python scripts/lint_swallow_all.py --write-baseline docs/code-guards-baseline.json
python -m pytest tests/test_swallow_guard.py -q            # 全绿（棘轮不越线）
```

## 标准修法（修一处就是这三步）

1. 能收窄异常类型的直接收窄（`except ValueError` / `except (OSError, RuntimeError)`），不吞编程错误；
2. 确属外部边界：补日志或错误状态字段 + 一条断言「降级后用户看到什么」的测试 + 登记白名单三问 + `review_by`；
3. 禁止 `contextlib.suppress(Exception)`、`except Exception: raise`、以及任何等价绕过写法；扫描器已把前两者判红。

## 定点清除清单（按用户会被伤到的顺序）

### A1. `desktop/main_window.py`：连接/录制/设备路径的 EMPTY（48 处）

`main_window.py` 本轮不碰，交桌面/操作逻辑线。快照行号：

| 方法（用户动作） | ERROR 行号 |
| --- | --- |
| `_connect_backend`（连接后端） | 2792, 2799, 2806, 2822, 2829, 2834, 2875, 2879, 2883, 2888 |
| `_connect_real`（连接真实设备） | 3270 |
| `_panels_set_sdr_connected`（连接后启用面板） | 3299, 3308, 3315, 3328, 3334, 3340, 3353, 3360, 3366, 3373, 3392, 3397, 3405 |
| `_teardown_connection`（断开/清理） | 3424, 3432, 3438, 3446, 3459, 3464, 3470 |
| `_on_connection_for_ui` / `_on_module_device_selected` | 3511, 3517, 5596 |
| `_on_device_plugged` / `_on_panel_device_connect` | 5656, 5753 |
| `_on_record_toggled` / `_record_write_chunk` / `_on_pipeline_record_iq` | 3696, 4603, 4613 |
| `_start_recording` / `_stop_recording`（录音） | 5209, 5219, 5229, 5283, 5292, 5298 |
| `_replay_recording`（回放） | 6384, 6388 |
| `_disconnect_gnss` | 6970 |

判定：这些吞一次就是「点了没反应」；修的时候必须断言用户看到的状态（状态栏、按钮、提示），不能只补日志。

### A2. `desktop/rf_sky_view.py::_find_body_at`（6 处，含 DSO 事故）

| 行号 | 类别 | 现状 |
| --- | --- | --- |
| 5318 | ERROR/EMPTY | `float(None)` 类失败被 `pass`（事故点） |
| 5332 | ERROR/EMPTY | `pass` |
| 5378 | WARN/SILENT_CONTROL | `continue`（该天体不画） |
| 5438 | WARN/SILENT_FALLBACK | `px_arr = py_arr = alt_draw_arr = None` |
| 5456 | WARN/SILENT_CONTROL | `continue`（该天体不画） |
| 5473 | WARN/SILENT_FALLBACK | `hyg = 0` |

要求：每处修复都要有「天体出现在图层 / 点击命中返回该天体」的断言；`_find_body_at` 至少要区分「星表无此目标」与「解析失败」。

### A3. `mbdsdr_ai/sdr_tools.py`：AI 工具面（49 处，17 ERROR + 32 WARN）

ERROR（`pass`，模型会当成成功继续编解释）行号：661, 3117, 3185, 3191, 3375, 3471, 3797, 3986, 4055, 4118, 4527, 4930, 5451, 7873, 7891, 7925, 7943。

WARN 多数是 `return ToolResult(success=False, content=...)`（用户/模型能看到失败文本，但没有日志与错误码）。这 32 处至少要补错误码/日志，不能让模型只拿到一句自由文本。

### 其余

`desktop/` 其余 526 处、`mbdsdr_ai/` 其余 643 处按兵不动，靠棘轮只降不升。测试线 62 处（多为 teardown）与 `experiments/` 32 处排在最后。

### D. 三条长输入挂死：实测曲线与处置

**这不只是「慢」，还是内存炸弹。** 2026-11-05 崩溃取证：`scripts/tool_param_fuzz.py`
的 `long_string` 用例把 `feldhell_decode` / `psk31_decode` / `rtty_decode` 的进程吃到
**17.2 GB** commit，叠加打满系统 38.1 GB 上限后进程被拖死（WER 2004）。所以这三个
工具的挂死阈值和内存阈值必须一起定。

测量方法：每个长度独立子进程，父进程每 30ms 采样 RSS/Private，超过 2.5 GB 立即强杀
（保护测量机）；两张表都走完整 handler（注册表 → 调制 → 解调 → 字符匹配），口径一致。
输入为 `A` 重复 n 次。

**优化前（Python `list` 存波形；括注失败 = 往返不匹配返回 `handler_error`）**

| 字符数 | feldhell 耗时 / RSS | psk31 耗时 / RSS | rtty 耗时 / RSS |
| --- | --- | --- | --- |
| 500 | 1.10s / 365MB | 0.37s / 164MB | 0.38s / 255MB |
| 1,000 | 2.19s / 611MB | 0.75s / 209MB | 0.76s / 389MB（失败） |
| 2,000 | 4.52s / 1,102MB | 1.48s / 297MB | 1.49s / 659MB（失败） |
| 4,000 | 9.10s / 2,153MB | 2.95s / 504MB | 2.99s / 1,198MB（失败） |
| 8,000 | **被 2.5GB 闸强杀** | 5.91s / 915MB | 5.98s / 2,274MB（失败） |
| 100,000 | 事故现场 17.2GB | 超 60s | 超 60s |

**优化后（`array('d')` 存波形 + 包络/FSK 能量流式累加）**

| 字符数 | feldhell 耗时 / RSS | psk31 耗时 / RSS | rtty 耗时 / RSS |
| --- | --- | --- | --- |
| 500 | 1.32s / 146MB | 0.40s / 130MB | 0.32s / 125MB |
| 1,000 | 2.60s / 208MB | 0.81s / 138MB | 0.65s / 131MB |
| 2,000 | 5.26s / 250MB | 1.65s / 155MB | 1.29s / 141MB |
| 4,000 | 10.55s / 498MB | 3.24s / 228MB | 2.64s / 179MB |
| 8,000（超上限） | 0.00006s / 118MB（结构化拒绝） | 同左 | 同左 |

同长度对比（4,000 字符）：RSS feldhell **2,153 → 498MB**、psk31 **504 → 228MB**、
rtty **1,198 → 179MB**；耗时 feldhell +16%、psk31 +10%，rtty 反而 -12%

诊断（含优化前数据）：

- **耗时按音频样本数线性，不是 O(n²)**：feldhell 每字符 14 列 × 约 457 采样
  （8kHz / 17.5 列/秒）；psk31 每字符约 11 bit × 256 采样；rtty 每字符约 8.5 bit ×
  176 采样。48000 与 8000 采样率下斜率随样本数同比变化。
- **内存曾经也是线性但常数极大**：每个采样是一个独立 Python `float` 对象
  （约 24 字节对象 + 8 字节指针 ≈ 32 字节/采样），而 `array('d')` 是 8 字节/采样。
  feldhell 每字符 6,400 采样，10 万字符就是 6.4 亿个 float —— 这正是 17.2GB 的来源。
- 优化前「2 万以后每字符耗时变高」是内存/GC 抬升，不是算法复杂度变化。
- 优化后 feldhell 仍是三者最慢（每字符约 2.6ms）；因为它每字符要生成 6,400 个
  采样点，且逐样本 `math.cos`。

处置：

1. **先优化，再限流**。波形从 `list[float]` 改为 `array('d')`；FeldHell 的包络检波
   去掉整段 `[abs(s)]` 列表，改为同一次扫描求峰值、分列求和；RTTY 的 4 条
   正交基带能量数组改为按 bit 窗口流式累加（每个 bit 只需 4 个累加器）。
   RSS 降到原来的 1/2～1/7（feldhell 4.3×、psk31 2.2×、rtty 6.7×）；耗时
   feldhell/psk31 增加 10～16%（`array` 逐项 append 比 `list` 慢），
   rtty 因去掉 4 条与波形等长的数组反而快 12%。这是刻意的取舍：用一点
   线性常数换掉 GB 级内存峰值。
2. **上限 3,000 字符**，写死在 `MAX_ROUNDTRIP_TEXT`，并**写入三个工具的 schema
   描述**（模型和用户调用前就能看到「单次上限 3000 字符，超出直接结构化拒绝」）。
   依据：最慢的 feldhell 3,000 字符约 7.9s，仍在单次 10s 闸内；3,000 字符在
   RTTY 45.45 baud 下约 9 分钟连续发射、FeldHell 约 40 分钟，远超真实业余报文
   （通常几百字符）。没有为了迁就某个测试用例去卡阈值。
3. 超限在 handler 入口秒回 `param_out_of_range`（带 `param/length/limit`），
   不做调制解调、不截断。
4. **模糊测试自身加内存闸**：每个用例独立监控本进程 commit 增长，超过
   `DEFAULT_MEM_CAP_MB = 1536` 立即注入异步异常中止并判 `MEMORY`，证据里带
   case 名、输入规模、峰值与增量；`--mem-cap-mb` 可改。验证方式：
   `tests/test_tool_param_fuzz_guard.py::test_fuzz_memory_cap_aborts_case`
   用「分配 64MB 后睡 0.6s」的 handler 在 16MB 闸下必须判 `MEMORY`。
5. **长输入分档串行**：`long_string` 按 2,000 / 20,000 / 100,000 三档，
   `array_long` 按 5,000 / 50,000 两档，逐档跑、逐档打印
   `输入 / rss_peak / commit_peak / 增量 / 耗时 / 结论`，不再一次性把最大档喂进去。

回归锁：

- `tests/test_tool_param_fuzz_guard.py::test_long_text_roundtrip_is_bounded`
  （10 万字符必须 <5s 返回结构化错误）。
- `tests/test_tool_param_fuzz_guard.py::test_text_roundtrip_limit_is_declared_in_schema`
  （上限必须出现在工具 schema 描述里，不能偷偷截断）。
- `tests/test_tool_param_fuzz_guard.py::test_over_limit_boundary_is_rejected_instantly`
  （`limit + 1` 字符即拒绝，`data.limit` 等于常量）。
- `tests/fldigi_roundtrip.py`（优化前后往返结果仍然正确）。

遗留（属解码器线，与本轮内存收口无关）：`rtty_decode` 从 1,000 字符起往返结果
不匹配，注册表最终回 `handler_error`（非具体错误码）。实测 500 通过、1,000/2,000/
3,000 均失败；疑为 `spb` 取整与实际 baud 的长期相位/帧同步漂移，需解码器线按
`rtty` 专项复核。模糊测试把这类「handler 明确返回失败」归为 OK，不会掩盖该缺陷
——它已单列在此。

## 附录 A：按文件分布（256 个文件）

| 文件 | 合计 | ERROR | WARN |
| --- | --- | --- | --- |
| `desktop/main_window.py` | 341 | 239 | 102 |
| `mbdsdr_ai/sdr_backend.py` | 112 | 50 | 62 |
| `desktop/rf_sky_view.py` | 71 | 21 | 50 |
| `mbdsdr_ai/agent.py` | 51 | 11 | 40 |
| `mbdsdr_ai/sdr_tools.py` | 49 | 17 | 32 |
| `desktop/receive_pipeline.py` | 21 | 18 | 3 |
| `desktop/dock_layout_manager.py` | 20 | 11 | 9 |
| `mbdsdr_ai/device_manager.py` | 16 | 8 | 8 |
| `mbdsdr_ai/core/controller.py` | 15 | 1 | 14 |
| `mbdsdr_ai/orbit_tools.py` | 13 | 0 | 13 |
| `mbdsdr_ai/serial_gnss.py` | 13 | 7 | 6 |
| `desktop/spectrum_widget.py` | 12 | 8 | 4 |
| `desktop/settings_panel.py` | 11 | 6 | 5 |
| `mbdsdr_ai/startup_sequence.py` | 11 | 4 | 7 |
| `mbdsdr_ai/decoders.py` | 10 | 0 | 10 |
| `mbdsdr_ai/solar_system.py` | 10 | 3 | 7 |
| `mbdsdr_ai/orbit_determination.py` | 9 | 3 | 6 |
| `mbdsdr_ai/subagents.py` | 9 | 2 | 7 |
| `experiments/satellite_rx/scripts/stack_up.py` | 8 | 4 | 4 |
| `mbdsdr_ai/acquisition.py` | 8 | 0 | 8 |
| `mbdsdr_ai/gnss_monitor.py` | 8 | 1 | 7 |
| `mbdsdr_ai/hal.py` | 8 | 2 | 6 |
| `mbdsdr_ai/sat_passes.py` | 8 | 0 | 8 |
| `desktop/ai_panel.py` | 7 | 6 | 1 |
| `mbdsdr_ai/code_editor.py` | 7 | 0 | 7 |
| `mbdsdr_ai/modulation.py` | 7 | 0 | 7 |
| `mbdsdr_ai/rtklib_adapter.py` | 7 | 5 | 2 |
| `desktop/ntrip_panel.py` | 6 | 3 | 3 |
| `desktop/touch_helpers.py` | 6 | 4 | 2 |
| `mbdsdr_ai/audio_out.py` | 6 | 2 | 4 |
| `mbdsdr_ai/context_manager.py` | 6 | 1 | 5 |
| `mbdsdr_ai/hal_enhanced.py` | 6 | 4 | 2 |
| `mbdsdr_ai/sat_tracker.py` | 6 | 0 | 6 |
| `mbdsdr_ai/sync_tools.py` | 6 | 0 | 6 |
| `mbdsdr_ai/tx_guard.py` | 6 | 1 | 5 |
| `scripts/ui_interaction_audit.py` | 6 | 3 | 3 |
| `tools/hw_selfcheck/selfcheck.py` | 6 | 1 | 5 |
| `desktop/adsb_map_panel.py` | 5 | 4 | 1 |
| `desktop/bookmark_panel.py` | 5 | 0 | 5 |
| `desktop/control_panel.py` | 5 | 2 | 3 |
| `desktop/device_panel.py` | 5 | 0 | 5 |
| `desktop/satellite_image_panel.py` | 5 | 1 | 4 |
| `desktop/scanner_panel.py` | 5 | 0 | 5 |
| `mbdsdr_ai/audio_device_manager.py` | 5 | 0 | 5 |
| `mbdsdr_ai/mcp_client.py` | 5 | 4 | 1 |
| `mbdsdr_ai/reconnection.py` | 5 | 3 | 2 |
| `mbdsdr_ai/spyserver_client.py` | 5 | 4 | 1 |
| `scripts/soak_receive_chain.py` | 5 | 1 | 4 |
| `tests/test_lrpt_meteor_real_golden.py` | 5 | 0 | 5 |
| `desktop/aprs_panel.py` | 4 | 0 | 4 |
| `desktop/gain_panel.py` | 4 | 2 | 2 |
| `desktop/main.py` | 4 | 2 | 2 |
| `desktop/status_panel.py` | 4 | 0 | 4 |
| `mbdsdr_ai/ccsds_tools.py` | 4 | 0 | 4 |
| `mbdsdr_ai/fengyun_sat.py` | 4 | 1 | 3 |
| `mbdsdr_ai/frame_layout_tool.py` | 4 | 0 | 4 |
| `mbdsdr_ai/gimbal.py` | 4 | 2 | 2 |
| `mbdsdr_ai/m17_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/op25_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/openapi_integration.py` | 4 | 0 | 4 |
| `mbdsdr_ai/openwebrx_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/orbit.py` | 4 | 0 | 4 |
| `mbdsdr_ai/pat_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/radio_control.py` | 4 | 3 | 1 |
| `mbdsdr_ai/rtltcp_client.py` | 4 | 3 | 1 |
| `mbdsdr_ai/sandbox.py` | 4 | 3 | 1 |
| `mbdsdr_ai/sat_consistency.py` | 4 | 0 | 4 |
| `mbdsdr_ai/sdrpp_decoders.py` | 4 | 0 | 4 |
| `mbdsdr_ai/sdrsharp_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/sdrtrunk_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/sstv_decoder.py` | 4 | 1 | 3 |
| `mbdsdr_ai/urh_adapter.py` | 4 | 0 | 4 |
| `tests/test_ui_hard_defects.py` | 4 | 4 | 0 |
| `desktop/adsb_panel.py` | 3 | 1 | 2 |
| `desktop/ai_steps.py` | 3 | 0 | 3 |
| `desktop/layout_store.py` | 3 | 0 | 3 |
| `desktop/mcp_worker.py` | 3 | 1 | 2 |
| `desktop/themes.py` | 3 | 1 | 2 |
| `desktop/vfo_panel.py` | 3 | 0 | 3 |
| `experiments/exp_ota_run.py` | 3 | 0 | 3 |
| `experiments/exp_sstv_identification.py` | 3 | 0 | 3 |
| `mbdsdr_ai/ardop_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/ax25.py` | 3 | 0 | 3 |
| `mbdsdr_ai/bch_codecs.py` | 3 | 0 | 3 |
| `mbdsdr_ai/blind_param_est.py` | 3 | 0 | 3 |
| `mbdsdr_ai/core/mcp_server.py` | 3 | 1 | 2 |
| `mbdsdr_ai/cubicsdr_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/gps_bit_sync.py` | 3 | 0 | 3 |
| `mbdsdr_ai/gps_nav.py` | 3 | 0 | 3 |
| `mbdsdr_ai/gqrx_receiver.py` | 3 | 0 | 3 |
| `mbdsdr_ai/hdsdr_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/inspectrum_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/js8call_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/lrpt_vcdu.py` | 3 | 0 | 3 |
| `mbdsdr_ai/model_manager.py` | 3 | 0 | 3 |
| `mbdsdr_ai/module_graph.py` | 3 | 2 | 1 |
| `mbdsdr_ai/new_spacetime.py` | 3 | 2 | 1 |
| `mbdsdr_ai/new_spacetime_timeline.py` | 3 | 0 | 3 |
| `mbdsdr_ai/protocol_stack.py` | 3 | 0 | 3 |
| `mbdsdr_ai/self_evolution.py` | 3 | 1 | 2 |
| `mbdsdr_ai/sigdigger_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/skill_registry.py` | 3 | 1 | 2 |
| `mbdsdr_ai/skyengine/satellites.py` | 3 | 0 | 3 |
| `mbdsdr_ai/tc_uplink.py` | 3 | 0 | 3 |
| `mbdsdr_ai/tool_registry.py` | 3 | 0 | 3 |
| `mbdsdr_ai/windows_setup.py` | 3 | 3 | 0 |
| `mbdsdr_ai/xastir_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_mcp_client.py` | 3 | 1 | 2 |
| `scripts/tool_availability_audit.py` | 3 | 1 | 2 |
| `tests/test_gui_real_hardware.py` | 3 | 2 | 1 |
| `tests/test_ui_layout_actions.py` | 3 | 3 | 0 |
| `tests/test_ui_layout_locks.py` | 3 | 3 | 0 |
| `desktop/anr_panel.py` | 2 | 0 | 2 |
| `desktop/modulation_panel.py` | 2 | 0 | 2 |
| `desktop/module_panel.py` | 2 | 0 | 2 |
| `desktop/settings.py` | 2 | 1 | 1 |
| `desktop/touch_manager.py` | 2 | 0 | 2 |
| `desktop/weather_panel.py` | 2 | 0 | 2 |
| `experiments/exp_llm_baseline.py` | 2 | 0 | 2 |
| `experiments/nanovna_rh770/scripts/vna_capture2.py` | 2 | 2 | 0 |
| `experiments/satellite_rx/scripts/mbdsdr_ctl.py` | 2 | 0 | 2 |
| `experiments/satellite_rx/scripts/pass_recorder.py` | 2 | 1 | 1 |
| `experiments/satellite_rx/scripts/passes_leo.py` | 2 | 2 | 0 |
| `experiments/satellite_rx/scripts/sat_radar.py` | 2 | 2 | 0 |
| `mbdsdr_ai/aprs_parser.py` | 2 | 1 | 1 |
| `mbdsdr_ai/astronomy.py` | 2 | 0 | 2 |
| `mbdsdr_ai/builtin_tle_meta.py` | 2 | 2 | 0 |
| `mbdsdr_ai/conversation.py` | 2 | 1 | 1 |
| `mbdsdr_ai/df_position.py` | 2 | 0 | 2 |
| `mbdsdr_ai/digital_modes.py` | 2 | 0 | 2 |
| `mbdsdr_ai/doppler_link.py` | 2 | 0 | 2 |
| `mbdsdr_ai/fhss_dehop.py` | 2 | 0 | 2 |
| `mbdsdr_ai/fm_scan.py` | 2 | 0 | 2 |
| `mbdsdr_ai/gnss_timestamps.py` | 2 | 0 | 2 |
| `mbdsdr_ai/gnuradio_blocks.py` | 2 | 0 | 2 |
| `mbdsdr_ai/gpredict_adapter.py` | 2 | 0 | 2 |
| `mbdsdr_ai/gps_ca.py` | 2 | 0 | 2 |
| `mbdsdr_ai/guardian.py` | 2 | 1 | 1 |
| `mbdsdr_ai/hackrf_params.py` | 2 | 1 | 1 |
| `mbdsdr_ai/interference_eval.py` | 2 | 0 | 2 |
| `mbdsdr_ai/interferometry.py` | 2 | 0 | 2 |
| `mbdsdr_ai/limesuite_params.py` | 2 | 2 | 0 |
| `mbdsdr_ai/llm_judge.py` | 2 | 0 | 2 |
| `mbdsdr_ai/mobile_server.py` | 2 | 0 | 2 |
| `mbdsdr_ai/multi_user.py` | 2 | 2 | 0 |
| `mbdsdr_ai/nanovna_dump.py` | 2 | 1 | 1 |
| `mbdsdr_ai/orbit_perturbation.py` | 2 | 0 | 2 |
| `mbdsdr_ai/osmosdr_source.py` | 2 | 0 | 2 |
| `mbdsdr_ai/playback.py` | 2 | 2 | 0 |
| `mbdsdr_ai/r2cloud_adapter.py` | 2 | 0 | 2 |
| `mbdsdr_ai/sat_links.py` | 2 | 0 | 2 |
| `mbdsdr_ai/sat_pipeline_runner.py` | 2 | 1 | 1 |
| `mbdsdr_ai/sat_profile.py` | 2 | 0 | 2 |
| `mbdsdr_ai/satellite/tracker.py` | 2 | 1 | 1 |
| `mbdsdr_ai/self_learning.py` | 2 | 2 | 0 |
| `mbdsdr_ai/tle_fetch.py` | 2 | 0 | 2 |
| `mbdsdr_ai/ui_actions.py` | 2 | 0 | 2 |
| `mbdsdr_ai/web/streamer.py` | 2 | 1 | 1 |
| `scripts/ai_tool_consistency.py` | 2 | 1 | 1 |
| `scripts/bench_dsp_throughput.py` | 2 | 2 | 0 |
| `scripts/decode_sensitivity.py` | 2 | 0 | 2 |
| `scripts/real_hw_acceptance.py` | 2 | 0 | 2 |
| `scripts/rtl_selfcheck.py` | 2 | 0 | 2 |
| `tests/test_interaction_wiring.py` | 2 | 2 | 0 |
| `tests/test_no_sim_regression.py` | 2 | 0 | 2 |
| `tests/test_sdr_backend_audio.py` | 2 | 1 | 1 |
| `tests/test_ui_interaction_guard.py` | 2 | 2 | 0 |
| `tests/test_ui_vfo_constellation.py` | 2 | 2 | 0 |
| `tests/tool_param_smoke.py` | 2 | 1 | 1 |
| `tests/tool_selftest.py` | 2 | 0 | 2 |
| `tools/onboarding/onboard.py` | 2 | 0 | 2 |
| `desktop/constants.py` | 1 | 0 | 1 |
| `desktop/mode_registry.py` | 1 | 0 | 1 |
| `desktop/tokens.py` | 1 | 0 | 1 |
| `docs/learn/phase14/pass_predict.py` | 1 | 0 | 1 |
| `experiments/exp_digital_modes.py` | 1 | 0 | 1 |
| `experiments/exp_fhss_detection.py` | 1 | 0 | 1 |
| `experiments/exp_weak_model_toolcall.py` | 1 | 0 | 1 |
| `experiments/nanovna_rh770/scripts/vna_capture3.py` | 1 | 1 | 0 |
| `experiments/satellite_rx/data/check_tles.py` | 1 | 0 | 1 |
| `experiments/satellite_rx/scripts/sat_catch.py` | 1 | 0 | 1 |
| `mbdsdr_ai/__init__.py` | 1 | 1 | 0 |
| `mbdsdr_ai/atmosphere.py` | 1 | 0 | 1 |
| `mbdsdr_ai/audio_resampler.py` | 1 | 0 | 1 |
| `mbdsdr_ai/background_decoder.py` | 1 | 0 | 1 |
| `mbdsdr_ai/bladerf_params.py` | 1 | 1 | 0 |
| `mbdsdr_ai/channelizer.py` | 1 | 0 | 1 |
| `mbdsdr_ai/cma_equalizer.py` | 1 | 0 | 1 |
| `mbdsdr_ai/df_amplitude.py` | 1 | 0 | 1 |
| `mbdsdr_ai/doppler_compensation.py` | 1 | 0 | 1 |
| `mbdsdr_ai/dsp.py` | 1 | 0 | 1 |
| `mbdsdr_ai/feature_manager.py` | 1 | 0 | 1 |
| `mbdsdr_ai/fec.py` | 1 | 0 | 1 |
| `mbdsdr_ai/gk2a_lrit.py` | 1 | 0 | 1 |
| `mbdsdr_ai/golden_regression.py` | 1 | 0 | 1 |
| `mbdsdr_ai/hooks.py` | 1 | 1 | 0 |
| `mbdsdr_ai/kismet_adapter.py` | 1 | 0 | 1 |
| `mbdsdr_ai/lrpt_image.py` | 1 | 0 | 1 |
| `mbdsdr_ai/lrpt_pipeline.py` | 1 | 0 | 1 |
| `mbdsdr_ai/mimo_v2.py` | 1 | 0 | 1 |
| `mbdsdr_ai/orbit_dynamics.py` | 1 | 0 | 1 |
| `mbdsdr_ai/plugin_system.py` | 1 | 1 | 0 |
| `mbdsdr_ai/position_service.py` | 1 | 1 | 0 |
| `mbdsdr_ai/remote_control.py` | 1 | 0 | 1 |
| `mbdsdr_ai/rtcm3_decoder.py` | 1 | 0 | 1 |
| `mbdsdr_ai/rtk_solver.py` | 1 | 0 | 1 |
| `mbdsdr_ai/sat_image_processing.py` | 1 | 0 | 1 |
| `mbdsdr_ai/satnogs_adapter.py` | 1 | 0 | 1 |
| `mbdsdr_ai/scheduler.py` | 1 | 0 | 1 |
| `mbdsdr_ai/sdrangel_plugins.py` | 1 | 0 | 1 |
| `mbdsdr_ai/sky_actions.py` | 1 | 0 | 1 |
| `mbdsdr_ai/spacetime_predict.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/exp_phase58_multisat.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/test_mobile_server.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/test_optional_deps.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/test_phase58_multisat.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/test_sdr_rtltcp_registration.py` | 1 | 0 | 1 |
| `mbdsdr_ai/time_sync.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tool_budget.py` | 1 | 0 | 1 |
| `mbdsdr_ai/verify_chain.py` | 1 | 0 | 1 |
| `mbdsdr_ai/vna_pipeline.py` | 1 | 0 | 1 |
| `mbdsdr_ai/web/server.py` | 1 | 1 | 0 |
| `mbdsdr_ai/workflow_engine.py` | 1 | 0 | 1 |
| `mbdsdr_ai/workflow_recorder.py` | 1 | 1 | 0 |
| `scripts/access_layer_fault_injection.py` | 1 | 0 | 1 |
| `scripts/serial_gnss_probe.py` | 1 | 1 | 0 |
| `tests/conftest.py` | 1 | 0 | 1 |
| `tests/test_adsb_dump1090_golden.py` | 1 | 0 | 1 |
| `tests/test_agc_speed.py` | 1 | 1 | 0 |
| `tests/test_app_mode.py` | 1 | 1 | 0 |
| `tests/test_bookmark_tags.py` | 1 | 1 | 0 |
| `tests/test_clean_exc.py` | 1 | 0 | 1 |
| `tests/test_cli.py` | 1 | 1 | 0 |
| `tests/test_desktop_ui_responsiveness.py` | 1 | 1 | 0 |
| `tests/test_device_switch_flow.py` | 1 | 1 | 0 |
| `tests/test_ft8_wsjtx_golden.py` | 1 | 0 | 1 |
| `tests/test_full_integration.py` | 1 | 0 | 1 |
| `tests/test_gimbal_follow_wiring.py` | 1 | 1 | 0 |
| `tests/test_gk2a_jp2.py` | 1 | 0 | 1 |
| `tests/test_headless_smoke.py` | 1 | 1 | 0 |
| `tests/test_identity_redline.py` | 1 | 0 | 1 |
| `tests/test_main_window.py` | 1 | 1 | 0 |
| `tests/test_mcp_server.py` | 1 | 1 | 0 |
| `tests/test_mode_switch_preset.py` | 1 | 1 | 0 |
| `tests/test_orphan_connection.py` | 1 | 1 | 0 |
| `tests/test_record_replay_config.py` | 1 | 1 | 0 |
| `tests/test_satellite_tracker.py` | 1 | 0 | 1 |
| `tests/test_serial_gnss.py` | 1 | 0 | 1 |
| `tests/test_sky_capability_negotiation.py` | 1 | 1 | 0 |
| `tests/test_sky_daylight_stars.py` | 1 | 0 | 1 |
| `tests/test_sky_track_wiring.py` | 1 | 1 | 0 |
| `tests/test_three_column_layout.py` | 1 | 1 | 0 |
| `tests/test_touch_adaptation.py` | 1 | 1 | 0 |
| `tests/test_ui_flow_guards.py` | 1 | 1 | 0 |
| `tests/test_user_layouts.py` | 1 | 1 | 0 |
| `tests/tool_health_check.py` | 1 | 0 | 1 |
