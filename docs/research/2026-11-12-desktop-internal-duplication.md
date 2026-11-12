# desktop/ 内部重复实现审计：同名工具与同形代码块（只出清单，不改码，2026-11-12）

> 结论先行：上一轮只比了「库 vs 面板」，这一轮把范围收进 `desktop/` 内部，
> 找到的存量确实更大，而且**最严重的几处都不需要库参与**：
>
> | # | 同一件事 | 实现处 | 行为 | 用户可见后果 |
> | --- | --- | --- | --- | --- |
> | 1 | Hz 格式化 | 4 个具名函数 + **48 处内联**（另 1 处 `:g`） | **不一致（6/4/3/1 位小数）** | 同一个 145.5 MHz 在四个地方写成四种字符串 |
> | 2 | 增益回读标签 | `control_panel` / `module_panel` | **不一致（`:g` vs 取整）** | 12.34 dB 显示成「12.34 dB」和「12 dB」 |
> | 3 | 连接错误 → 人话/错误码 | `_humanize_error` / `classify_connect_error` | **不一致（分支顺序不同）** | 权限错误文案说缺权限，链接却指向「设备被占用」 |
> | 4 | 信号电平（S 表） | `control_panel` / `status_panel` | **不一致（dBFS vs S 单位 + 平滑 vs 原始）** | 同一信号两块读数不同、跳动节奏也不同 |
> | 5 | 带宽格式化 | `status_panel` vs 其余 3 处 | **不一致（0 位 vs 1 位 kHz；0 值口径不同）** | 2.4 kHz 显示成「2 kHz」 |
> | 6 | 设备枚举管线 | `device_panel` / `module_panel`（6 个同名方法） | **不一致（错误语义分叉）** | 枚举失败：一个说「没插设备」，一个说「枚举失败」 |
> | 7 | dBFS / RSSI 格式化 | 4 处 | 2 处一致，1 处无守卫，1 处 0 位小数 | 负零、NaN、小数位三种口径 |
> | 8 | 历史产物（加载/另存/点开） | `satellite_image_panel` / `weather_panel` | 基本一致，1 处健壮性不同 | 历史文件被删后一个提示、一个静默 |
> | 9 | `_resolved_settings` | 3 处 | 一致 | 无（可收口） |
> | 10 | `_clean_exc` 回退 | 8 处 | 一致 | 无（可收口） |
> | 11 | `__getattr__` 惰性类 | 5 处同形 | 一致 | 无（可收口） |
> | 12 | `set_sdr_connected` | 12 处 | 同名不同职 | 无 |
> | 13 | `_apply_connected_state` | 3 处 | 同名不同职 | 无 |
> | 14 | `_refresh_table` | 3 处 | 同名不同职（列不同） | 无 |
> | 15 | 时间/时长格式化 | 4 处 | 不一致（呈现不同） | 过境倒计时/时间跳转/UTC 时刻三种写法 |
>
> 对原假设的修正：**「多份 Hz 格式化、精度互不相同」的形态成立，
> 但实际分布不是 3/2 位，而是 6/4/3/1 位四种，且不止 3 份**——
> 4 个具名出口之外还有 48 处内联直接写死小数位、1 处用 `:g`（见 §2 的计数）。
> 只改那 4 个具名函数不会让显示一致。

---

## 1. 方法与口径

- 范围：`desktop/*.py`，65 个文件、2391 个函数。
- **AST 精确同形**（去掉 docstring、变量名归一、常量保留）只找到 5 组，
  且全是同文件内的成对方法或无关方法（如 `set_freq_fm` / `set_freq_am`）。
  说明「逐字复制整段函数」很少。
- 真正的存量是**同名 + 同职责 + 各自演化**，所以用三条线交叉：
  1. 跨文件同名函数（80 组、346 个函数）；
  2. 格式化/单位/错误文案/可用性/节流的关键词扫描；
  3. 对每个候选组逐处读源码，比较**输出**而不是方法名。
- 一致性分级：
  - **不一致**：同一输入在两处得到不同结果，且用户能同时看到——按 bug 处理；
  - **一致重复**：输出相同，可收口，无用户可见后果；
  - **同名不同职**：名字撞车，容易误导维护者，但不算重复实现。

复现命令（PowerShell，只读）：

```powershell
# 同名组
Select-String -Path desktop\*.py -Pattern "^\s*def " | Group-Object { ($_.Line -split 'def ')[1].Split('(')[0].Trim() } |
  Where-Object { $_.Count -gt 1 } | Sort-Object Count -Descending
# 内联 MHz 小数位（48 处）：按小数位分组
Select-String -Path desktop\*.py -Pattern '1e6:\.\d[fg]' | Where-Object { $_.Line -match 'MHz' } |
  Group-Object { if ($_.Line -match '1e6:\.6f') {'6'} elseif ($_.Line -match '1e6:\.4f') {'4'} elseif ($_.Line -match '1e6:\.3f') {'3'} else {'g'} }
```

---

## 2. Hz 格式化：4 个出口 + 48 处内联，四种小数位

四个具名出口，**精度、量级分档、空值口径都不同**：

| 出口 | 分档 | 小数位 | 空值 |
| --- | --- | --- | --- |
| [status_panel.py:82-96](../../desktop/status_panel.py#L82-L96) `_fmt_freq` | MHz / kHz / Hz | **6 / 3 / 0** | `—` |
| [vfo_panel.py:43-49](../../desktop/vfo_panel.py#L43-L49) `_fmt_freq_hz` | MHz / kHz / Hz | **3 / 1 / 0** | 无（入参必须可 float） |
| [control_panel.py:158-167](../../desktop/control_panel.py#L158-L167) `_format_frequency_hz` | GHz / MHz / kHz / Hz | **`:g`（去尾零）** | 无 |
| [rf_sky_view.py:1295-1305](../../desktop/rf_sky_view.py#L1295-L1305) `format_frequency_mhz` / `format_frequency_hz` | 固定 MHz 或固定 Hz | 1 / 0，带符号 | `—` |

内联写死的小数位（`Select-String '1e6:\.\d[fg]'` 且行内含 `MHz`，共 48 行）：

| 小数位 | 处数 | 代表位置 |
| --- | --- | --- |
| **6 位** | 5 | [status_panel.py:93](../../desktop/status_panel.py#L93)、[control_panel.py:1011](../../desktop/control_panel.py#L1011)、[main_window.py:403](../../desktop/main_window.py#L403)、[main_window.py:10967](../../desktop/main_window.py#L10967) |
| **4 位** | 11 | [bookmark_panel.py:207](../../desktop/bookmark_panel.py#L207)、[spectrum_widget.py:2240](../../desktop/spectrum_widget.py#L2240)、[main_window.py:11020](../../desktop/main_window.py#L11020) |
| **3 位** | 32 | [main_window.py:5118-5120](../../desktop/main_window.py#L5118-L5120)（状态栏）、[scanner_panel.py:354](../../desktop/scanner_panel.py#L354)、[vfo_panel.py:46](../../desktop/vfo_panel.py#L46) |
| **`:g`** | 1 | [control_panel.py:163-164](../../desktop/control_panel.py#L163-L164)（tooltip） |

**同一个 145 500 000 Hz，用户能同时看到四种写法**：

| 位置 | 输出 |
| --- | --- |
| 状态面板 RX 频率（[status_panel.py:708](../../desktop/status_panel.py#L708) 经 `_fmt_freq`） | `145.500000 MHz` |
| 书签列表（[bookmark_panel.py:207](../../desktop/bookmark_panel.py#L207)） | `145.5000` |
| 主窗状态栏（[main_window.py:5118](../../desktop/main_window.py#L5118)）与 VFO 列表（[vfo_panel.py:46](../../desktop/vfo_panel.py#L46)） | `145.500 MHz` |
| 控制面板频率提示（[control_panel.py:158-167](../../desktop/control_panel.py#L158-L167)） | `145.5 MHz` |

低于 1 MHz 时更明显：455 kHz 在状态面板是 `455.000 kHz`，在 VFO 面板是 `455.0 kHz`。

**重点是「同一控件内也不一致」**：书签保存提示用 3 位
（[main_window.py:8190](../../desktop/main_window.py#L8190) `已收藏 145.500 MHz`），
而书签列表本身用 4 位（`145.5000`）——同一次操作，先看到的确认文案和随后看到的
列表值不一样。

注：`rf_sky_view` 的两个同名函数服务的是**多普勒偏移**（带符号、固定 Hz），
语义与调谐频率不同，不算错；但名字与 `format_frequency_hz` 完全撞车，
是下一类「同名不同职」的来源。

---

## 3. 增益回读：注释说「同口径」，实现不同口径

两处都叫 `set_gain_applied`，注释明确写了要同口径：

- [control_panel.py:1570-1608](../../desktop/control_panel.py#L1570-L1608)：
  `f"{value:g} dB"`，并在「读数 ≠ 配置意图」时写 tooltip 说明未生效、
  有回读通道时标来源。
- [module_panel.py:453-471](../../desktop/module_panel.py#L453-L471)：
  `f"{int(round(value))} dB"`，且**没有** `readback_source` 参数、
  不比较配置意图、不写「未生效」提示。它的 docstring 自己记着：
  「此前只回写了控制面板……同一台设备在两个面板上给出两个读数」，
  说明这个 bug 修过一次，但只修了「值有没有更新」，没修「格式和告警」。
- 调用侧 [main_window.py:7626-7633](../../desktop/main_window.py#L7626-L7633) 对同一个
  `real_db` 先后调 `cp.set_gain_applied(real_db, source)` 与
  `mp.set_gain_applied(real_db)`——连参数都不齐（模块面板拿不到读数来源）。

后果：后端回读 12.34 dB 时，控制面板写 `12.34 dB`，模块面板写 `12 dB`；
后端夹值（意图 30、实际 29.7）时，控制面板标「未生效」，模块面板静默显示 `30 dB`。
**同一个硬件增益，两块面板既不同值也不同告警。**

**告警部分已修（`26d8d1c2`）**：`module_panel` 在读数与滑杆意图不一致时写
tooltip + `logger.warning`，不再静默；格式（`int(round())` vs `:g`）与
`readback_source` 参数按计划留到下一步统一。

---

## 4. 连接错误翻译：两套关键词分类器，同一字符串两种结论

同一处调用同时用了两个分类器：

- [main_window.py:6159-6161](../../desktop/main_window.py#L6159-L6161)：
  `friendly = self._humanize_error(raw_err)`，紧接着
  `code = classify_connect_error(raw_err)`；
- [main_window.py:6435-6439](../../desktop/main_window.py#L6435-L6439) 同样成对。

两个分类器的分支**顺序不同**：

| 顺序 | `_humanize_error`（[main_window.py:6879](../../desktop/main_window.py#L6879)） | `classify_connect_error`（[error_remedy.py:138-172](../../desktop/error_remedy.py#L138-L172)） |
| --- | --- | --- |
| 1 | 缺依赖 | 缺依赖 |
| 2 | 网络 | 网络 |
| 3 | **权限** | **占用** |
| 4 | 占用（关键词含裸 `errno`） | 权限（返回 `None`） |
| 5-8 | 未找到 / 驱动 / 断开 / 采样率 | 未找到 / 驱动 / 断开 / 采样率 |

`error_remedy.classify_connect_error` 的 docstring 写着
「顺序与 `main_window._humanize_error` 的判定顺序保持一致」——**实际没保持一致**。

可证的分歧输入（两处都是纯字符串匹配，可直接断言）：
`"could not open device: Permission denied [Errno 13]"`
- `_humanize_error` 命中第 3 分支 → 文案「USB 权限不足：…udev…zadig…」；
- `classify_connect_error` 先命中第 3 分支的 `could not open` / `errno` →
  `device_busy`，状态栏「查看怎么办」链接打开的是**「设备被占用，请关闭 SDR#/GQRX」**。

用户按链接去关别的软件，权限问题照旧。这是「文案一套、补救入口另一套」的典型。

**已修（`dd0a47f8`）**：两处合并为 `error_remedy.CONNECT_ERROR_RULES`
一张有序表（permission 先于 busy，理由见上：用户动作不同），裸 `errno`
从占用关键词移除；`classify_connect_error` 新增 `usb_permission` 码，
目录与「怎么办」条目同步补齐，链接指向 udev/WinUSB 步骤；
`main_window._humanize_error` 不再自带分支，委托同源函数。
反证：`tests/test_connect_error_ordering.py` 断言
`"Permission denied [Errno 13]"` 的文案与码同分类，旧顺序下必红
（实测返回 `device_busy`）。

---

## 5. 信号电平：一个叫 S-meter，显示的是 dBFS；另一个才是 S 单位

主窗口对同一个 `dbfs` 同时驱动两块面板，但传的值和渲染都不同：

- [main_window.py:9614-9621](../../desktop/main_window.py#L9614-L9621)：
  `cp.update_signal_level(ema)`（**平滑值**）、`sp.update_signal_level(dbfs)`（**原始值**）；
- [control_panel.py:1407-1430](../../desktop/control_panel.py#L1407-L1430)：
  控件名是 `smeter_label` / `smeter_bar`，显示 `f"{v:.0f} dBFS"`（0 位小数），
  进度条直接 `setValue(int(v))`；
- [status_panel.py:816-867](../../desktop/status_panel.py#L816-L867)：
  显示 `_fmt_dbfs(dbfs)`（1 位小数）+ `_dbfs_to_smeter(dbfs)`（`S9 +3 dB` 等），
  进度条按百分比映射，并按 -40/-70 dB 换三种颜色。

后果，同一信号同时可见：
- 左侧控制面板：`-73 dBFS`；
- 右侧状态面板：`-73.0 dBFS` 与 `S9 +0 dB`；
- 两者一个跟 EMA 走、一个跟原始值走，**数字变化节奏也不同**（快速起伏时两块读数会明显错开）。

「S-meter」这个名字在控制面板上没有兑现，在状态面板上兑现了——
这是同名控件、两套语义，比单纯的格式差更容易误导。

---

## 6. 带宽格式化：0 位 vs 1 位 kHz，0 值一个「自动」一个「0 Hz」

| 出口 | ≥1 MHz | 其余 | 0 / None |
| --- | --- | --- | --- |
| [status_panel.py:104-118](../../desktop/status_panel.py#L104-L118) `_fmt_bw` | `.3f MHz` | `.0f kHz` | `自动` / `—` |
| [vfo_panel.py:52-56](../../desktop/vfo_panel.py#L52-L56) `_fmt_bw_hz` | 不涉及 | `.1f kHz` | `0 Hz` |
| [modulation_panel.py:247](../../desktop/modulation_panel.py#L247) 内联 | 不涉及 | `.1f kHz` | `--` |
| [spectrum_widget.py:3782](../../desktop/spectrum_widget.py#L3782)、[:3800](../../desktop/spectrum_widget.py#L3800) | 不涉及 | `.1f kHz` | 不涉及 |

后果：2400 Hz 的带宽，状态面板写 `2 kHz`，VFO 面板写 `2.4 kHz`；
带宽为 0 时状态面板说「自动」，VFO 面板说「0 Hz」——后者会被读成
「设备报告了 0 带宽」而不是「未设置」。
`status_panel._fmt_bw` 是唯一把带宽舍到整数 kHz 的实现，也是唯一区分「自动」的实现，
两者都是对的方向，但只存在于一处。

---

## 7. 设备枚举：两个面板六对同名方法，错误语义已经分叉

`device_panel` 与 `module_panel` 各有一整套「枚举 → 身份 → 连接请求渲染」：

| 职责 | `device_panel` | `module_panel` | 是否一致 |
| --- | --- | --- | --- |
| 同步枚举 | [337-346](../../desktop/device_panel.py#L337-L346)（异常吞成空列表） | [499-513](../../desktop/module_panel.py#L499-L513)（保留 `enum_err`） | **否** |
| 异步枚举 | [348-369](../../desktop/device_panel.py#L348-L369)（有 inflight + 看门狗 + token） | [515-528](../../desktop/module_panel.py#L515-L528)（只有 inflight） | **否** |
| 落控件 | [398-419](../../desktop/device_panel.py#L398-L419)（无 `enum_err` 参数） | [536-570](../../desktop/module_panel.py#L536-L570)（区分「枚举失败」与「没插设备」） | **否** |
| 当前身份 | [582-591](../../desktop/device_panel.py#L582-L591) | [589-598](../../desktop/module_panel.py#L589-L598) | **逐行相同** |
| 请求渲染 | [593-615](../../desktop/device_panel.py#L593-L615)（写编辑框，仅未连接时） | [600-635](../../desktop/module_panel.py#L600-L635)（写 combo/slider，按焦点跳过） | 否（同意图） |
| 请求变更回调 | [642-647](../../desktop/device_panel.py#L642-L647) | [655-660](../../desktop/module_panel.py#L655-L660) | **逐行相同** |

最值钱的是第一行：`module_panel` 的注释明确记录了这是修过的 bug——
「旧实现先 setText(异常) 再被『未发现SDR设备』盖掉，失败被吞」，
所以它现在区分「枚举失败」与「没插设备」。
**同样的修法没有落到 `device_panel`**：它的 `refresh_devices` 仍是
`except Exception: devices = []`，`_apply_devices([])` 直接显示「未检测到 SDR 设备」。
同一台机器、同一次枚举失败，两个面板给用户的结论相反：
一个说「没插设备」，一个说「枚举失败（这不等于没插设备）」。

异步路径的差异也会被用户看见：`device_panel` 有 12 s 看门狗与 token 作废旧结果
（[:382-396](../../desktop/device_panel.py#L382-L396)），枚举卡死会提示「枚举超时」；
`module_panel` 没有看门狗，卡住后 `_enum_inflight` 永远不复位，刷新按钮保持禁用。

---

## 8. dBFS / RSSI：四处实现，三种边界口径

| 实现 | 小数位 | NaN/Inf | 负零 |
| --- | --- | --- | --- |
| [main_window.py:163-178](../../desktop/main_window.py#L163-L178) `_fmt_dbfs` | 1 | `—` | 归零 |
| [status_panel.py:49-52](../../desktop/status_panel.py#L49-L52) `_fmt_dbfs`（经 `_fmt_number`） | 1 | `—` | 归零 |
| [spectrum_widget.py:2227-2229](../../desktop/spectrum_widget.py#L2227-L2229) `_fmt_abs_db` | 1 | 无守卫（只在调用处判 `None`） | 不处理 |
| [control_panel.py:1430](../../desktop/control_panel.py#L1430) 内联 | **0** | 有守卫（早退） | 不处理 |

RSSI 也有一对：`main_window._fmt_rssi_mcp`
（[:181-196](../../desktop/main_window.py#L181-L196)）与
`status_panel._fmt_rssi_mcp`（[:62-65](../../desktop/status_panel.py#L62-L65)），
输出一致，唯一差异在负零：`-0.4` 前者得 `-0 dBµV`，后者得 `0 dBµV`
（RSSI 是 0–127 整数，实际触发不了，仅记录口径差）。

同一文件内还有第二份无语义守卫的格式化：
`status_panel` 的 IMU 读数列用嵌套 `_fmt`
（[:1169-1170](../../desktop/status_panel.py#L1169-L1170)），
`f"{v:.1f}" if v is not None else "—"`——NaN 会渲染成 `nan`，
而同文件的 `_fmt_number` 会走空态。两条路径对「没有读数」的定义不同。

---

## 9. 历史产物：五个同名方法，一处健壮性不同

`satellite_image_panel` 与 `weather_panel` 各有一套「历史列表 + 另存」：

| 方法 | satellite_image_panel | weather_panel | 差异 |
| --- | --- | --- | --- |
| `_load_history` | [425-437](../../desktop/satellite_image_panel.py#L425-L437) | [848-861](../../desktop/weather_panel.py#L848-L861) | 目录/文件名前缀不同，结构相同 |
| `_on_history_clicked` | [439-447](../../desktop/satellite_image_panel.py#L439-L447) | [863-872](../../desktop/weather_panel.py#L863-L872) | 后者先判 `os.path.exists` 并提示「产物已不存在」；前者直接构造 `QPixmap`，文件被删时静默无反应 |
| `_save_current` | [449-470](../../desktop/satellite_image_panel.py#L449-L470) | [874-891](../../desktop/weather_panel.py#L874-L891) | 逻辑逐行相同，只有状态写入目标不同 |
| `start_decode` / `stop_decode` | [212-232](../../desktop/satellite_image_panel.py#L212-L232) | [737-780](../../desktop/weather_panel.py#L737-L780) | 同名不同职（一个缓冲音频、一个起 worker），不算重复 |

`_save_current` 复制得最彻底：连「成功报文件名、失败报文件名 + 异常类型」
的注释都以同一措辞出现两遍。这类复制目前行为一致，但下一次只改一边就会分叉；
`_on_history_clicked` 已经分叉了（健壮性一边有一边没有）。

---

## 10. 一致重复：可收口，不是 bug

| 实现 | 处数 | 位置 | 说明 |
| --- | --- | --- | --- |
| `_clean_exc` 导入回退 | **8** | [ai_panel.py:39](../../desktop/ai_panel.py#L39)、[bookmark_panel.py:43](../../desktop/bookmark_panel.py#L43)、[mcp_worker.py:43](../../desktop/mcp_worker.py#L43)、[modulation_panel.py:41](../../desktop/modulation_panel.py#L41)、[module_panel.py:63](../../desktop/module_panel.py#L63)、[satellite_image_panel.py:52](../../desktop/satellite_image_panel.py#L52)、[scanner_panel.py:51](../../desktop/scanner_panel.py#L51)、[weather_panel.py:55](../../desktop/weather_panel.py#L55) | 都是「导入内核 `clean_exc`，失败退回 `str(e)`」，三行逐字相同；行为一致 |
| `_resolved_settings` | **3** | [new_spacetime_panel.py:666-674](../../desktop/new_spacetime_panel.py#L666-L674)、[right_column.py:466-474](../../desktop/right_column.py#L466-L474)、[sat_track_panel.py:531-535](../../desktop/sat_track_panel.py#L531-L535) | 逐行相同（「显式注入优先，否则取宿主 settings」） |
| `__getattr__` 惰性类 | **5** | [command_palette.py:718-722](../../desktop/command_palette.py#L718-L722)、[diagnostics_panel.py:198-203](../../desktop/diagnostics_panel.py#L198-L203)、[error_remedy.py:286-290](../../desktop/error_remedy.py#L286-L290)、[help_panel.py:324-329](../../desktop/help_panel.py#L324-L329)、[shortcuts_panel.py:313-317](../../desktop/shortcuts_panel.py#L313-L317) | 同形（按名字返回 `_build_*_class()`），只是类名不同；`settings_panel.py:771-776` 是另一种用途，不在此列 |

这三类收口收益低、风险也低；列出来是为了「不要再复制第九份」，
不是建议现在动。

---

## 11. 同名不同职：容易误导，不是重复

| 名字 | 处数 | 实际职责 |
| --- | --- | --- |
| `set_sdr_connected` | 12 | 每个面板各自的连接态开关（各自控件集合不同）；10 处是 2-3 行包装 |
| `_apply_connected_state` | 3 | [anr_panel.py:125-135](../../desktop/anr_panel.py#L125-L135)（SNR 文案）、[gain_panel.py:281-288](../../desktop/gain_panel.py#L281-L288)（AGC + 总增益）、[vfo_panel.py:160-171](../../desktop/vfo_panel.py#L160-L171)（按钮 + 状态行）；三份都不该合并 |
| `_refresh_table` | 3 | [adsb_panel.py:407-423](../../desktop/adsb_panel.py#L407-L423)、[aprs_panel.py:241-257](../../desktop/aprs_panel.py#L241-L257)、[adsb_map_panel.py:337-352](../../desktop/adsb_map_panel.py#L337-L352)，列定义完全不同 |
| `paintEvent` / `__init__` / `_build_ui` | 12 / 88 / 23 | Qt 重载与构造函数，同名是框架要求 |

`_apply_connected_state` 值得单独记一笔：三份同名方法三种实现，
维护者读到「改一下 `_apply_connected_state`」时无法判断指哪一个。
命名撞车的成本是维护成本，不是用户可见 bug，所以放在这一档。

---

## 12. 时间/时长：四种写法，两种用途被混用

| 出口 | 输出形态 | 用途 |
| --- | --- | --- |
| [sat_track_panel.py:645-650](../../desktop/sat_track_panel.py#L645-L650) `_fmt_countdown` | `H:MM:SS` / `MM:SS`，向上取整 | 过境倒计时 |
| [rf_sky_view.py:1335-1347](../../desktop/rf_sky_view.py#L1335-L1347) `format_duration_s` | `+12 秒` / `+3.5 分钟` / `+1.25 小时` | 时间跳转量 |
| [time_machine.py:30-47](../../desktop/time_machine.py#L30-L47) `format_offset` | `与现在同步` / `+1 天 2 小时` | 与现在的差 |
| [new_spacetime_panel.py:1098-1114](../../desktop/new_spacetime_panel.py#L1098-L1114) `_fmt_hm` / `_fmt_dt` | `14:05` / `11-12 14:05` | 过境时刻 |

倒计时与时间偏移是两个语义，分开是实现选择；但「UTC 时刻」的日期精度确实不一致：
`rf_sky_view.format_utc_datetime` 是 `%Y-%m-%d %H:%M UTC`
（[:1329-1332](../../desktop/rf_sky_view.py#L1329-L1332)），
`sat_track_panel._fmt_when` 是 `%m-%d %H:%M:%S UTC`
（[:637-642](../../desktop/sat_track_panel.py#L637-L642)）。
同一个 UTC 时间戳，一处带年份、一处不带；一处到分钟、一处到秒。

---

## 13. 查过但不算重复

- **节流/防抖**：`main_window._warn_throttled` 30 s
  （[:114-124](../../desktop/main_window.py#L114-L124)）、
  `receive_pipeline` 解调失败 2 s（[:376-378](../../desktop/receive_pipeline.py#L376-L378)）、
  `spectrum_widget` 平移事件 0.1 s（[:4133-4134](../../desktop/spectrum_widget.py#L4133-L4134)）。
  三者都是「时间戳 + 间隔」同形，但节流对象完全不同（日志/故障/交互），
  间隔不同是各自需求，合并会引入新 bug。
- **可用性判断**：各面板顶部的 `_ENUM_OK` / `_KERNEL_AVAILABLE` / `_HAS_APT` / `_GAIN_OK`
  是**不同依赖**的导入结果，不是同一判断的复制；真正跨面板的可用性广播只有
  `set_sdr_connected`（见 §11）。
- **几何计算**：`osm_map` 的 Web 墨卡托投影与 `adsb_panel._project` /
  `rf_sky_view._sky_to_screen` 是三种不同投影（地图 / 极坐标 / 天球），不构成重复。

---

## 14. 结论与建议的处置顺序（本文不动代码）

按「用户可见 × 修起来便宜」排序：

1. **§5 信号电平**：先统一「S-meter 到底显示 dBFS 还是 S 单位」这一个决定，
   再决定控制面板那块是否改成 S 单位；顺手对齐平滑值（一块 EMA、一块原始值
   本身就是两块表跳得不一样的原因）。
2. **§4 连接错误翻译**：已修（`dd0a47f8`）——两套表已合并为一张有序规则表，
   文案与错误码同源；`usb_permission` 已有独立条目与补救链接。
3. **§7 设备枚举**：把 `module_panel` 已修好的「枚举失败 ≠ 没插设备」与
   看门狗搬进 `device_panel`（或反向抽成一个共用控件），别让两个面板继续各自演化。
4. **§2 Hz 格式化**：先定「一个频率在 UI 里保留几位」的唯一口径，
   再按这个口径改那 4 个具名出口；51 处内联可以分批替换，
   不需要一次性改完，但要禁止新增 `1e6:.Nf` 字面量。
5. **§3 增益回读**：告警已修（`26d8d1c2`）——`module_panel` 夹值时写
   tooltip + 日志；剩余待办是签名（补 `readback_source`）与格式（`:g`）统一，
   所以 12.34 vs 12 的显示差异仍在。
6. **§6 带宽**：把 `status_panel._fmt_bw` 的「整数 kHz」改成 1 位小数，
   其余三处已经是对的。
7. 一致重复（§10）与同名不同职（§11）暂不处理，只在新增代码时引用现有实现。
