# 设置与配置盘点（settings-inventory）

> 取证时间：2026-11-05。范围：`desktop/` 桌面端 + `mbdsdr_ai/config.py`。
> 本文件是"设置与配置架构线"的交付证据；所有行号以当前工作区为准（主窗口正在被
> 另一条线改动，行号可能小幅漂移，已尽量同时给出常量名/键名）。

## 0. 结论（先给数字）

| 项 | 数量 | 说明 |
|---|---|---|
| 盘点的配置/持久化入口 | **6 处** | DesktopSettings、gui_config、AI config、QSettings、layout_store、bookmarks |
| 盘点出的"硬编码但用户会想改"条目 | **37 条** | 见 §3 清单（新增 #33 瀑布深度、#34 大气消光、#35–#37 过境筛选） |
| 其中已暴露到设置面板 | **31 条** | 23 条落 DesktopSettings，2 条落 gui_config，5 条落 AI config；去重后 35 个 schema 项 |
| 判定锁死、不暴露 | **6 类** | 理由见 §4（不是偷懒，每条都有具体消费点缺失或双入口风险） |
| schema 总条目 | **48** | 35 暴露 + 13 内部键（频率/增益/音量等已有即时控件） |
| 设置面板新增测试 | **67 个** | `tests/test_settings_schema.py`，全绿（含 10 对联动提示钉扎 + 13 项手改 JSON 非法值矩阵） |
| 相关回归 | **96 + 29 个** | 配置/主题/布局/触屏/i18n 96；三栏与面板集成 29 |

**接手时的现状（已在本轮修复，记录备查）**：用户点"设置"当时**不是空面板，而是只有
一个"服务设置"页签**——2 个开关（远程控制 TCP、Web 服务）+ 2 个端口输入框。
主题与触屏只藏在"视图"菜单，语言只有配置文件，观测站坐标、频谱偏好、AI 参数、
星象仪气象参数全都没有 UI。用户的抱怨成立。现在设置面板已有 7 个分组，
详见 §1 与 §5。

## 1. 现状取证：点"设置"到底能看到什么

| 交互项 | 位置 | 写哪个 key | 行为 |
|---|---|---|---|
| 启用远程控制（TCP） | `desktop/settings_panel.py` 服务页签 | `desktop_settings.json: rc_enabled` | 启动/停止 `RemoteControl` |
| 远程控制端口 | 同上 | `rc_port` | 改端口需重启该服务 |
| 启用 Web 服务 | 同上 | `web_enabled` | 启动/停止 `WebServer` |
| Web 服务端口 | 同上 | `web_port` | 同上 |
| （新增）恢复本组默认 | 同上 | 4 个键 | 回 schema 默认并停服务 |
| 主题 | "视图"菜单，不在设置页 | `theme` | 即时换主题 |
| 触屏适配 | "视图"菜单，不在设置页 | `touch_mode` | 即时 |
| 瀑布显示 | 频谱工具栏 | `show_waterfall` | 即时 |
| FFT/窗/平均/调色板/峰值阈值 | 频谱工具栏 | **不持久化** | 每次启动回硬编码 |
| 星象仪图层 | 星象仪底栏 | **不持久化** | 会话态 |
| 观测站坐标 | **无 UI**，只能手改 `gui_config.json` | `observer_lat/lon` | 启动读回 |
| 界面语言 | **无 UI**，只能手改 `desktop_settings.json` | `ui_language` | 启动安装翻译器 |
| AI 温度/top_p/上下文/超时 | **无 UI**，只能手改 `~/.mbdsdr/config.json` | `temperature` 等 | 下次建 agent 生效 |
| （新增）光害等级 Bortle | 设置 → 星象仪 | `bortle` | 即时 `set_bortle`，可见星数随之变化 |
| （新增）录音目录 | 设置 → 存储与日志 | `record_dir` | 写入后只影响新录音；可选/打开目录 |

## 2. 六处存储：谁管什么

| # | 文件 / 类 | 负责键 | 谁读 | 谁写 | schema | 默认值唯一来源 | 损坏时 |
|---|---|---|---|---|---|---|---|
| 1 | `~/.mbdsdr/desktop_settings.json` / `DesktopSettings` | 频率、增益、解调、带宽、采样率、呼号、GNSS、AGC、音量、瀑布、主题、触屏、4 个服务键、语言、形态 + 本次新增 11 个频谱/星象仪键 | `main_window`、`spectrum_widget`、`settings_panel` | `main_window`、`settings_panel`、`settings.py` 防抖写 | **有**（`config_schema.py`） | `config_schema.defaults("settings")` | 备份 `*.corrupt-时间戳` + 回默认 + `last_error` 可见 |
| 2 | `~/.mbdsdr/gui_config.json` | 观测站经纬度、窗口几何之外的 GUI 手工项 | `main_window._load_gui_config` | `main_window._save_gui_config` | 部分（经纬度已进 schema） | schema 默认 `None` | 既有 `*.corrupt-时间戳` + 原子写 |
| 3 | `~/.mbdsdr/config.json` / `mbdsdr_ai/config.py` | api_key、base_url、model、temperature、top_p、timeout、上下文/输出上限 | `AgentConfig` / AI 面板 | AI 面板、`save_config`、设置面板 | 部分（5 个采样键已进 schema） | `mbdsdr_ai/config.py DEFAULT_CONFIG` | AI 面板既有处理；设置面板读坏时备份并提示 |
| 4 | `QSettings("MBDSDR","Desktop")` | 窗口几何、停靠状态 | `main_window._restore_window_state` | `_save_window_state` | 否 | Qt 自身 | Qt 忽略坏值 |
| 5 | `%APPDATA%/MBDSDR/layouts.json` / `LayoutStore` | 用户自定义布局预设 | `LayoutStore` | `LayoutStore.save_preset` | 否 | 无（预设是用户数据，不是设置） | 既有 JSON 容错 |
| 6 | `~/.mbdsdr/bookmarks.json` / `BookmarkManager` | 用户书签/频率库 | 频率管理器 | `BookmarkManager.save` | 否 | 无（用户数据） | 既有容错 |

> 说明：`QSettings` 只做窗口几何，不承担"用户偏好"；`layouts.json` 与
> `bookmarks.json` 是**用户数据**而非默认值型配置，因此没有纳入 schema，
> 也不应出现在"设置"面板（放进去会把"数据"和"配置"混淆）。

## 3. 硬编码清单（37 条）

"现在在哪定义"一列给出当前证据；"改动方式"区分"改配置即可"与"要动消费代码"。

| # | 用户会想改的东西 | 现在在哪定义 | 现在能改吗 | 结论 |
|---|---|---|---|---|
| 1 | 观测站纬度 | `main_window.py:447` 初始 None；`:7768` 读 gui_config | 只能手改 JSON | **暴露** `observer_lat` |
| 2 | 观测站经度 | 同上 `:7769` | 只能手改 JSON | **暴露** `observer_lon` |
| 3 | 界面语言 | 旧 `settings.py` `ui_language="zh"` | 只能手改 JSON | **暴露**，标注重启 |
| 4 | FFT 点数 | `spectrum_widget.py:151` 2048 | 工具栏即时，不持久 | **暴露** `fft_size` |
| 5 | 窗函数 | `spectrum_widget.py:150` Nuttall | 工具栏即时，不持久 | **暴露** `fft_window` |
| 6 | 平均帧数 | `spectrum_widget.py:152` 1 | 工具栏即时，不持久 | **暴露** `fft_avg_frames` |
| 7 | 瀑布配色 | `spectrum_widget.py:1789` 第 0 项 | 工具栏即时，不持久 | **暴露** `waterfall_palette` |
| 8 | 峰值阈值 | `spectrum_widget.py:859` 6.0 dB | 工具栏即时，不持久 | **暴露** `peak_threshold_db` |
| 9 | 显示峰值 | `spectrum_widget.py:858` True | 工具栏即时，不持久 | **暴露** `show_peaks` |
| 10 | 瀑布平滑 | `spectrum_widget.py:173` False | 工具栏即时，不持久 | **暴露** `waterfall_smooth` |
| 11 | 显示书签 | `spectrum_widget.py:852` True | 工具栏即时，不持久 | **暴露** `show_bookmarks` |
| 12 | 显示频段彩带 | `spectrum_widget.py:855` True | 工具栏即时，不持久 | **暴露** `show_bandplan` |
| 13 | 瀑布可见 | 旧 `settings.py` `show_waterfall=True` | 菜单/工具栏 | **暴露** |
| 14 | 默认主题 | `themes.py:545` `default` | 视图菜单 | **暴露** |
| 15 | 触屏适配 | 旧 `settings.py` `touch_mode="auto"` | 视图菜单 | **暴露** |
| 16 | 星象仪大气压 | `rf_sky_view.py:227` 1013.25 hPa | **不能**（无 setter 之外的 UI） | **暴露**（`set_refraction_params`） |
| 17 | 星象仪气温 | `rf_sky_view.py:228` 10 ℃ | 同上 | **暴露** |
| 18 | AI 温度 | `mbdsdr_ai/config.py:24` 0.7 | 只能手改 JSON | **暴露** `ai_temperature` |
| 19 | AI top_p | `mbdsdr_ai/config.py:25` 0.9 | 只能手改 JSON | **暴露** |
| 20 | AI 单次输出上限 | `mbdsdr_ai/config.py:21` 2048 | 只能手改 JSON | **暴露** |
| 21 | AI 上下文窗口 | `mbdsdr_ai/config.py:20` 8192 | 只能手改 JSON | **暴露** |
| 22 | AI 超时 | `mbdsdr_ai/config.py:26` 60 s | 只能手改 JSON | **暴露** |
| 23 | Bortle / 肉眼极限星等 | `rf_sky_view.py:1714` 默认 3；新增公开 `set_bortle` | 设置面板 1–9 级 | **暴露** `bortle`（立即生效，实测 3↔8 星数 1456↔194） |
| 24 | 星象仪图层四开关 | `rf_sky_view.py:6944-6957` | 底栏即时，不持久 | **锁死**（双入口） |
| 25 | 调谐吸附步进 | 旧 `spectrum_widget.py:921` `snap_interval=10_000`；控制面板步进下拉 | 控制面板即时；本轮起也持久化 | **暴露** `tuning_step_hz`（与顶部「步进」双向同步，改哪边都落盘） |
| 26 | Max/Min hold、dB 范围 | `spectrum_widget.py` 会话态 | 工具栏 | **锁死**（测量态非偏好） |
| 27 | 录音目录 | `main_window.py:5749` 旧值 `~/mbdsdr_recordings`；新默认由 `settings.py:default_record_dir()` 给出 | 设置面板可改 + 选择/打开目录 | **暴露** `record_dir`（默认 `D:\mbdsdr\recordings`，只影响新录音） |
| 28 | 日志级别 | 无 `basicConfig`，只有分散 `getLogger` | 不能 | **锁死**（没有消费点） |
| 29 | 采样率/带宽/增益/解调/音量 | 旧 `settings.py` + 设备/控制面板 | 面板即时且已持久 | **锁死**（已有单一入口） |
| 30 | AI provider/model/key/base_url | `ai_panel.py` 配置对话框 | 已有 UI | **锁死**（避免密钥双入口） |
| 31 | 星象仪 FOV / 时间倍率 | 星象仪底栏 | 已有 UI | **锁死**（会话态视图操作） |
| 32 | 静噪门限默认值 | `mbdsdr_ai/squelch.py` `DEFAULT_SQUELCH_DBFS=-50`（换算走 `margin_threshold_db()`） | 控制面板滑杆即时；本轮起也持久化 | **暴露** `squelch_default_db`（默认/范围直接取内核常量；与滑杆双向同步） |
| 33 | 瀑布历史保留行数 | `spectrum_widget.py` `WF_DEPTH_DEFAULT_ROWS=256`，环形缓冲固定深度 | 改前不可调；改后设置面板即时 | **暴露** `waterfall_depth_rows`（128–4096 行；改深度保留最近历史，内存随深度线性增长） |
| 34 | 大气消光系数 | `mbdsdr_ai/skyengine/stars.py` `EXTINCTION_K=0.2`；`extinction_delta_mag()` 已按 airmass 逐星算 | 改前不可调；改后设置面板即时 | **暴露** `sky_extinction_k`（0.0–0.5；只压低低仰角星，不动 Bortle 决定的天顶极限星等） |
| 35 | 过境最低仰角 | `new_spacetime_panel.refresh_passes()` 硬编码 `PASS_MIN_ALT_DEG` | 改前不可调 | **暴露** `pass_min_elevation_deg`（0–45°；读取入口 `settings.effective_pass_min_elevation_deg()`，消费点待卫星线接一行） |
| 36 | 过境搜索窗口 | `new_spacetime_panel.refresh_passes()` 硬编码 `hours_ahead=24.0`；`rf_sky_view.PASS_WIN_HOURS=24.0` | 改前不可调 | **暴露** `pass_window_hours`（1–72 小时；读取入口 `settings.effective_pass_window_hours()`） |
| 37 | 显示低仰角过境 | 两处过滤都只认 `PASS_MIN_ALT_DEG`（10°/5° 各自硬编码） | 改前不可调 | **暴露** `show_low_elevation_passes`（开时门降到 5°：`settings.effective_pass_visibility_gate_deg()`） |

**统计口径**：37 条中 31 条暴露（#1–#23、#25、#27、#32–#37），6 类锁死（#24、#26、#28–#31）。

## 4. 为什么锁死（逐条理由，当前真实状态）

以下 6 类**仍然锁死**，其余此前登记为锁死的 Bortle、录音目录、调谐步进已按实际
消费链改为暴露（见 §9 处置表）：

1. **星象仪图层**：底栏已有方位网格/赤道网格/罗盘/轨迹四个开关；设置里再开一套会
   出现"设置里打开、底栏显示关闭"的假象。持久化应由坐标视图线在底栏直接写 schema。
2. **Hold / dB 范围**：是测量态临时工具，重启清零是预期行为；持久化反而让用户以为
   下次还在测同一个信号。
3. **日志级别**：桌面端没有统一 logging 配置，只有分散 `getLogger`，没有可改的消费点。
   先补日志基础设施，再谈级别配置。
4. **采样率/带宽/增益/解调/音量**：设备面板与控制面板已有即时控件且已持久化，设置里
   重复暴露会产生两个入口（§2 表第 1 行已覆盖这些键，因此它们仍是 schema 的内部键）。
5. **AI provider/model/key/base_url**：AI 面板已有配置对话框（`ai_panel.py:355-415`），
   设置里再放一个 Key 输入框会导致两处密钥、两套保存路径。
6. **星象仪 FOV / 时间倍率**：底栏已有倍率与重置摄像机按钮，且属于会话态视图操作，
   不是长期偏好。

> 已移出锁死（按用户裁定与真实消费链修正）：
> - **Bortle**：此前以"无 setter"为由锁死是错误的——那是实现缺口。已补公开
>   `RFSkyView.set_bortle(level)`，进 schema（1–9），面板即时生效。
> - **录音目录**：此前以"消费点在主窗口线"为由锁死是协作问题而非产品理由。已进
>   schema 与面板（选择/打开目录），并提供公开读取入口 `DesktopSettings.record_dir`
>   与 `ensure_record_dir()`；主窗口消费点由操作逻辑线接入（改前需报备）。
> - **调谐步进**：此前担心"控制面板会覆盖设置值"；改为双向同步（顶部下拉改 → 写回
>   settings，设置改 → 同时推 `control_panel.step_combo` 与
>   `spectrum.set_snap_interval`）后不再是假配置，已进 schema（10 Hz–1 MHz）。

## 5. 暴露项三问（何时改 / 默认依据 / 改错校验）

| 设置项 | ① 用户何时会改 | ② 默认值及依据 | ③ 改错会怎样（校验/范围） |
|---|---|---|---|
| 界面语言 | 换母语/给外国朋友演示 | zh：`tr()` 源文即中文（`l10n.py:5`） | 枚举 zh/en/ja；未知回 zh；重启生效并在行内提示 |
| 触屏适配 | 平板/二合一接鼠标 | auto：`touch_manager` 自动探测 QTouchDevice | 枚举 auto/on/off；未知回 auto |
| 主题 | 夜间/强光/无障碍 | default：`themes.py DEFAULT_THEME` | 枚举 5 套；未知回 default；即时 `_apply_theme` |
| 显示瀑布 | 弱机省绘制 / 只看谱线 | True：旧 settings 默认 | 布尔；即时 `toggle_waterfall` |
| 大气压 | 高原/低压天气 | 1013.25：标准大气（`rf_sky_view` 常量） | 800–1100，越界夹住并提示；即时 `set_refraction_params` |
| 气温 | 冬夏/高低温 | 10 ℃：该常量注释的标准大气 | -50–50，越界夹住；即时 |
| 光害等级（Bortle） | 换观测地（城里/乡下/山顶） | 3：`stars.py DEFAULT_BORTLE=3`（城郊） | 1–9，越界夹住；即时 `set_bortle`；同一 120° 视场实测可见星 Bortle 3=1456 颗、Bortle 8=194 颗 |
| 录音目录 | 换盘符/避免写满系统盘 | 留空 = 程序目录同级 `recordings`（本机 `D:\mbdsdr\recordings`），不写 C 盘用户目录 | 文本路径；留空回默认；选择/打开目录失败弹可读原因；只影响新录音 |
| FFT 点数 | 找弱信号/看细节 vs 要流畅 | 2048：现有默认；档位与 C++ `settings_dialog` 对齐 | 1024/2048/4096 三档枚举；未知/8192/16384 回 2048 并记原因；即时 `set_fft_size` |
| 窗函数 | 测幅度（Flattop）/低旁瓣（Nuttall） | Nuttall：现有默认 | 6 种枚举；未知回 Nuttall；即时 |
| 平均帧数 | 弱信号平滑 vs 快速响应 | 1：现有默认 | 1/4/8/16；未知回 1；即时 |
| 瀑布配色 | 弱信号层次 vs 不刺眼 | default：现有第 0 项 | default/sdrpp；未知回 default；即时 |
| 峰值阈值 | 噪声大/信号密集 | 6.0 dB：既有 `peak_rel_db` | 1–30 dB，越界夹住；即时 |
| 标注峰值 | 只想看强信号 | True：既有默认 | 布尔；即时 |
| 瀑布平滑 | 追踪弱轨迹 | False：既有默认 | 布尔；即时 |
| 显示书签 | 小屏省空间 | True：既有默认 | 布尔；即时 |
| 显示频段彩带 | 只想看谱 | True：既有默认 | 布尔；即时 |
| 调谐步进 | 细调弱信号 vs 快速跨段 | 10 kHz：`spectrum_widget.py` 既有 `snap_interval`，档位同控制面板 | 10 Hz–1 MHz 六档枚举；未知回 10 kHz；即时推 `step_combo` + `set_snap_interval`；顶部改动反写设置 |
| 静噪门限 | 底噪大 / 弱信号被切断 | -50（≈6 dB 余量）：直接取 `mbdsdr_ai.squelch DEFAULT_SQUELCH_DBFS`，换算走 `margin_threshold_db()` | 刻度 -100～-20（相对余量 0～16 dB，非绝对 dBFS），越界夹住；即时推滑杆 + `_on_squelch_changed`；滑杆改动反写设置 |
| 瀑布历史行数 | 想回溯更久的信号历史 / 弱机省内存 | 256 行：既有环形缓冲默认；2048 行在 512 bin 下约 4 MiB、2048 bin 下约 16 MiB | 128–4096 行，越界夹住；即时 `SpectrumPanel.set_waterfall_depth_rows()`（保留最近历史，实时增量绘制耗时不变） |
| 大气消光系数 k | 换观测地（城郊雾霾 / 高山通透） | 0.2：`stars.py EXTINCTION_K`（晴朗海平面） | 0.0–0.5，越界夹住；即时 `RFSkyView.set_extinction()`（兼容别名 `set_extinction_k()`），按 airmass 逐星 `Δm = k/sin(仰角)` |
| 过境最低仰角 | 只看高仰角好机会 / 连贴地也列 | 10°：`settings.PASS_MIN_ELEVATION_DEFAULT_DEG`（schema 派生） | 0–45°，越界夹住；读取统一走 `settings.effective_pass_min_elevation_deg()`（消费方下次刷新生效） |
| 过境搜索窗口 | 今晚 / 未来三天 | 24 小时：`settings.PASS_WINDOW_HOURS_DEFAULT` | 1–72 小时，越界夹住；`settings.effective_pass_window_hours()` |
| 显示低仰角过境 | 追地平线附近的低轨机会 | False：`settings.effective_show_low_elevation_passes()` | 布尔；开时 `effective_pass_visibility_gate_deg()` 把门降到 5° |
| 观测站纬度 | 换城市/设台 | None：未配置就保持空状态，不编造位置 | -90–90，越界夹住；空白=未设置；即时写 gui_config + 重算过境 |
| 观测站经度 | 同上 | None | -180–180，同上 |
| AI 温度 | 要稳定操作建议/要发散 | 0.7：`DEFAULT_CONFIG` | 0–2，越界夹住；写 config.json + 现有 agent 即时生效 |
| AI top_p | 控制采样范围 | 0.9：`DEFAULT_CONFIG` | 0–1；同上 |
| AI 输出上限 | 长回答/省额度 | 2048：`DEFAULT_CONFIG` | 128–16384；同上 |
| AI 上下文 | 长对话/小模型 | 8192：`DEFAULT_CONFIG` | 1024–131072；同上 |
| AI 超时 | 本地模型慢/网络差 | 60 s：`DEFAULT_CONFIG` | 5–300；同上 |

> 服务 4 项（远程控制/Web 开关与端口）沿用既有页签；它们也进了 schema，因此参与
> 导入导出与"恢复全部默认"，默认值同样只有一处。

## 6. 落地机制（对应铁律）

- **单一事实源**：`desktop/config_schema.py` 的 `ALL_ITEMS` 同时定义 key、默认值、
  类型/范围、枚举、分组、人话文案、生效方式、存储后端。`settings.py` 的 `DEFAULTS`
  由 `defaults("settings")` 派生；`settings_panel.py` 只按 schema 渲染，服务页签的
  端口默认值也改成读 `SCHEMA`（测试钉住）。
- **立即生效**：主题/触屏 → 主窗口既有 `_apply_theme` / `_set_touch_mode`；
  频谱 → 既有 `generator.set_*` + 工具栏控件同步；星象仪 → `set_refraction_params` /
  **`set_bortle`**（新公开 setter，面板构造时也把持久化值推给天空图，重启不丢）；
  观测站 → 写 `gui_config` 并调 `_apply_observer_location` / `_save_gui_config`；
  录音目录 → 写入 settings 并即时更新生效路径提示（消费方读 `settings.record_dir`）；
  保存时立刻调 `ensure_record_dir()`，失败在弹窗+状态栏可见（值仍保留，方便用户插回外接盘）；
  **静噪/步进 → 同时推控制面板控件与消费点**（`_on_squelch_changed` /
  `set_snap_interval`），顶部控件改动也反写 settings；应用静噪默认值**不会**把
  「自动静噪」偷偷关掉（推送前记录、推送后恢复）；
  **瀑布深度 → `spectrum.set_waterfall_depth_rows()`**（保留最近历史、重建环形
  缓冲，回看缓存作废）；**消光 k → `sky_view.set_extinction()`**（改完即时按
  新 k 重算星表可见性与逐星亮度）；**过境筛选三键 → `settings.effective_pass_*`**
  （设置面板写盘后尽力调用 `host.new_spacetime_panel.refresh_passes()`，面板存在
  即即时刷新；消费方只需在刷新时调用函数读值）；
  AI → 写 `config.json`（保留 api_key）并同步现有 `agent.config`。
  **只有界面语言需要重启**：下拉框正下方橙色提示与 tooltip 原文为
  「重启后生效：Qt 翻译器必须在窗口创建前安装，切换后重启应用才生效。」，
  改值后的状态栏消息也追加「（重启后生效）」——不让人误以为已经切换。
- **损坏/写失败可见**：读取失败 → 备份 `*.corrupt-时间戳` + `last_error` 文案；
  写盘失败 → `on_error` 回调（设置面板状态栏显示"设置保存失败"）。服务面板的
  回填/保存/启动/停止失败也都写到可见状态行（有测试），不再有静默 pass。
  扫描器 `scripts/lint_swallow_all.py` 下 `settings.py`、`config_schema.py`、
  **`settings_panel.py` 全部为 0**（此前 11 处已清零：导入缺失收窄为 `ImportError`，
  端口/服务边界收窄为 `OSError/RuntimeError/ValueError` 或 `TypeError/ValueError`）。
- **恢复默认**：每行一个"默认"按钮（单项）；底部"恢复全部默认"（settings + 观测站清空
  + AI 采样参数 + 服务关闭）。恢复后控件回填、host 行为同步（测试
  `test_panel_reset_all_restores_behavior`）。
- **导入/导出**：导出一份可读 JSON，含 `schema_version=1` 与 `exported_at`；
  导出**永不含 api_key**（`build_export` 过滤 secret）。导入按**主版本**判定：
  同主版本（含 `1` / `"1.0"` / `"1.9"`）允许导入并迁移——文件缺少的字段按当前
  schema 默认补齐（逐项记入 `filled`，结果写"已按默认补齐 N 项"）、文件多出的字段
  进 `unknown` 并弹窗列出（"忽略 N 项"），**不静默丢弃**；跨主版本才拒绝，并在
  错误文案里写明"为什么不能导入 + 该怎么办（重新导出/手抄少数项/换对应版本）"。
  跨段字段进 `rejected`；越界值夹取并写进 `warnings`。
- **文案**：不出现 `min_fov`、`fft_size` 这类内部词；标题用"频谱分辨率（FFT 点数）"
  "最远/最近能看到的天空范围"之外的现有术语均配一句话解释。

## 7. 测试证据

- 新增：`tests/test_settings_schema.py` **45 passed**，覆盖
  单一事实源、越界/坏类型不崩、损坏备份、写失败可见、落盘往返、单项/全部恢复默认、
  导出含版本且无密钥、同主版本旧文件补齐迁移/较新次版本未知字段列出/跨主版本拒绝、
  面板分组齐全、真实控件信号→落盘→生效、观测站写 gui_config、AI 参数不动密钥、
  重启项标注、锁死项有理由；Bortle 1–9 schema + 即时生效 + 重启仍生效 + **同 FOV
  星数反证（Bortle 3=1456、Bortle 8=194）** + setter 越界/坏值；录音目录默认不落
  用户目录、留空回默认、`ensure_record_dir` 建目录、盘不可用时返回可执行文案且
  状态栏与弹窗同时可见、面板行与"选择/打开目录"按钮；
  **A5 静噪** schema 默认/范围与 `mbdsdr_ai.squelch` 常量逐值相等 + 改设置后
  滑杆与门控都变 + 滑杆反写设置 + 旧配置迁移提示（提示同时把新键按默认值落盘，
  下次启动不再重复；不覆盖旧文件里已有的键）；**A5 二次裁定**文案含「余量」、
  -50 显示为 ≈6 dB 余量、不再出现旧绝对 dBFS/ -80 解释；**A11 调谐步进**改设置后
  `snap_interval` 与下拉框都变 + 下拉反写设置；
  **A7 瀑布深度** 改设置后真实 `SpectrumDataGenerator` 的缓冲行数/内存时长按新深度
  变化（256→2048 实测 0.5→4 MiB @512 bin），越界（4096+）夹到上限；
  **A8 消光 k** 改设置后真实星表选择与逐星亮度都变（同 FOV 120°：k=0 见 1650 颗、
  k=0.5 见 488 颗 @alt20°），且分别对 Bortle 3/8 验证——k 只改低仰角衰减，
  不改变天顶极限星等；
  **A1/A2/A9** 读取函数 `settings.effective_pass_*()` 从真实 `DesktopSettings` 读到
  新值（默认 10° / 24h / 关；改成 30° / 48h / 开后门降到 5°），越界与坏值夹取；
  服务面板保存/启动/停止失败都在状态行可见。
- 回归：`test_settings_convergence` / `test_settings_semantics` / `test_theme_switch` /
  `test_theme_tokens` / `test_layout_store` / `test_touch_adaptation` /
  `test_desktop_i18n` = **96 passed**（另有本文件 45 项专项测试，合计 141）。
  `test_settings_convergence::test_satellite_tune_updates_control_panel` 现按 AST
  取 `main_window.py` 的**同一函数体**匹配，不扫全文件（避免启动恢复里的同名调用
  在联动点断线时误过）。
- 集成：`test_three_column_layout` + `test_ui_panels_integration` = **29 passed**
  （含新"设置"页签停靠与三栏布局不回归）。
- 棘轮：`lint_swallow_all.py --no-fail` 扫描，`desktop/settings.py` 0、
  `desktop/config_schema.py` 0、`desktop/settings_panel.py` **0（基线 11 → 0）**、
  `tests/test_settings_schema.py` 0；本线文件全部不越线。

## 8. 与其它线的边界 / 在途冲突

- `desktop/main_window.py` 仅做了**最小停靠接线**（已提交 `49eddcf`，9 insertions /
  1 deletion）：新增 `SettingsPanel` 导入 + 在服务页签之后创建"设置"页签，未改任何
  既有逻辑。
- `desktop/spectrum_widget.py`：既有配置仍走它的公开方法/控件；本轮按 A7 只**新增**
  `set_waterfall_depth()` / `wf_rows_at()` / `SpectrumPanel.set_waterfall_depth_rows()`
  与瀑布滚轮回看，不改既有绘制语义（实时增量绘制路径不变）。
- `mbdsdr_ai/skyengine/star_catalog.py`：按 A8 给 `select_for_view()` /
  `effective_limit_mag()` 增加可选 `extinction_k` 参数（默认仍是 `EXTINCTION_K`，
  旧调用方行为不变），透传逐星 `Δm = k/sin(仰角)`。
- `desktop/rf_sky_view.py`：在既有 `set_bortle()` 旁新增公开 `set_extinction()`
  （`set_extinction_k()` 为同一函数的兼容别名；不改既有逻辑/私有状态调用），
  面板与持久化都走它；该文件另有坐标视图线的未提交
  logging 改动，本线提交时只取自己那段 hunk，不覆盖对方修改。
- **`gain_db` 一行接线**：已派给操作逻辑线，位置 `main_window.py::_on_gain_changed`
  （增益改动写回 `SharedDemodConfig.gain_db`，变化才触发静噪基线重标）。本线只登记
  契约，不改该文件。
- 录音目录消费点仍是 `desktop/main_window.py:5749` 的 `~/mbdsdr_recordings` 与
  `:6957` 回放对话框（归操作逻辑线）。本线已提供 `settings.record_dir` /
  `ensure_record_dir()` 读取入口，**未改消费代码**；接入说明见 §8.1。
- 现场另有他人未提交改动（`desktop/spectrum_widget.py` 峰值检测重写归频谱线、
  `desktop/rf_sky_view.py` 标签分级归天空线、`desktop/receive_pipeline.py` 等），
  本线**只提交自己的 hunk**：共享文件先用 `git hash-object` 构造「HEAD + 本线改动」
  的 blob 再 `update-index`，不 stash、不 checkout 对方工作区；对他人改动零覆盖。
  占用登记（`scripts/file_claim.py`）显示这两个文件当时分别被频谱线/天空线持有，
  本线的 A7/A8 提交需等其释放后再落（或由对方提交后基于新 HEAD 重放本线 hunk）。
  `receive_pipeline.py` 的棘轮越线由审计线另案处理。

### 8.1 录音目录接入说明（给操作逻辑线，可直接照抄）

1. **读哪个属性**：`self.settings.record_dir`。它已把设置值归一成绝对路径；
   设置留空时返回默认 `D:\mbdsdr\recordings`（程序目录同级，不写 C 盘用户目录）。
2. **什么时候建目录**：真正要写文件前调用 `self.settings.ensure_record_dir()`，
   返回 `(path, error)`；`path` 就是本次要用的目录，`error is None` 表示可用。
3. **失败怎么显示**：`error` 非空时必须在状态栏/对话框显示该文案，并**拒绝开始
   本次录音**；不要再静默退到 `tempfile.gettempdir()`（那会让用户以为录到了目标盘）。
   **盘不存在（外接盘拔了/盘符没挂载）的约定**：`ensure_record_dir()` 返回
   `(原路径, "录音目录不可用（<路径>）：<系统错误>。请检查该磁盘/网盘是否已连接，
   或在「设置 → 存储与日志」重新选择目录。")`——**保留用户填的路径**，不擅自改回
   默认值，也不抛异常。界面用同一段文字：弹窗标题「录音目录不可用」，正文即上面
   的 error 全文；同时写进设置页状态栏。设置值仍会保存（用户可能只是暂时拔盘），
   但本次录音必须被拒绝。
4. **要替换的旧点**：`desktop/main_window.py:5749` 的
   `rec_dir = os.path.expanduser("~/mbdsdr_recordings")` 及随后的
   `makedirs`/兜底；`desktop/main_window.py:6957` 回放文件对话框的初始目录也改读
   `self.settings.record_dir`。
5. **语义**：改目录只影响新录音，已有录音不迁移、不删除。

## 9. 参数治理线 A1–A11 处置表（按 `docs/hardcoded-parameters.md` §4）

原则：**没有真实消费点的不进 schema**（进了也没人读 = 假设置）；消费点在别人文件、
且没有公开 setter 的，本线只登记读取入口与接入代码，不越界修改对方文件。
A1/A2/A9 例外：读取入口已做成真实函数（`settings.effective_pass_*()`，有反证测试），
消费方只需把硬编码换成函数调用（§9.1），因此允许先进 schema 暴露给用户。

| # | 条目 | 处置 | schema 键 / 默认 / 范围 / 单位 | 生效方式 | 消费点与证据 |
|---|---|---|---|---|---|
| A1 | 过境最低仰角 | **已落地（读取入口）** | `pass_min_elevation_deg` / 10 / 0–45 / 度 | 设置面板写盘；消费方下次刷新调用函数读值 | `settings.effective_pass_min_elevation_deg()`（真实函数，越界夹取）；消费点待接一行：`new_spacetime_panel.refresh_passes`、`rf_sky_view._refresh_passes`（§9.1） |
| A2 | 过境搜索窗口 | **已落地（读取入口）** | `pass_window_hours` / 24 / 1–72 / 小时 | 同上 | `settings.effective_pass_window_hours()`；消费点待接一行：`refresh_passes` 的 `hours_ahead`、`rf_sky_view.PASS_WIN_HOURS`（§9.1） |
| A3 | 录音总容量保留上限 | **不做**（无消费点） | 拟 `recording_retention_total_gb` / 0–2000 / GB | — | `RetentionPolicy` 存在但 desktop 无调用者；加了也没人执行 |
| A4 | 录音保留天数 | **不做**（无消费点） | 拟 `recording_retention_days` / 0–3650 / 天 | — | 同上 |
| A5 | 静噪门限 | **已落地** | `squelch_default_db` / `int(DEFAULT_SQUELCH_DBFS)`=-50 / `int(SQUELCH_MIN_DBFS)`=-100 ～ `int(SQUELCH_MAX_DBFS)`=-20 / 刻度=**相对噪声基线余量**（-100≈0、-50≈6、-20≈16 dB），不是绝对 dBFS | **即时**：滑杆 + 门控；滑杆反写设置 | `control_panel.squelch_slider` → `main_window._on_squelch_changed`；默认/范围/换算取自 `mbdsdr_ai/squelch.py`，schema 无第二份字面量 |
| A6 | AI 工具超时 | **不做**（无消费点） | 拟 `ai_tool_timeout_s` / 120 / 10–600 / 秒 | — | `mbdsdr_ai/agent.py:630` 为常量，无配置字段可读，改了不生效 |
| A7 | 瀑布深度行数 | **已落地** | `waterfall_depth_rows` / 256 / 128–4096 / 行 | **即时**：`spectrum.set_waterfall_depth_rows()`；保留最近历史、重建环形缓冲 | `SpectrumDataGenerator.set_waterfall_depth()` + `wf_rows_at()` + 瀑布滚轮回看。实测（512 bin、真实 IQ 灌 2048/4096 帧）：256 行 = 0.5 MiB，整段历史重绘 3.2 ms；2048 行 = 4 MiB，整段重绘 21.6 ms、实际视口(240 行) 2.3 ms；4096 行 = 8 MiB，整段重绘 28–40 ms、视口 2.1–2.8 ms；实时每帧只画 1 行 0.09–0.15 ms（不随深度变慢）。2048 bin 时内存线性放大 4×（2048 行 16 MiB、4096 行 32 MiB）。4096 是实测可用上限（整段重绘 <100 ms），越界夹取；截图 `a7_history_256.png` / `a7_history_2048.png` 对应历史长度 256 vs 2048 行（高度 8×） |
| A8 | 大气消光系数 | **已落地** | `sky_extinction_k` / 0.2 / 0.0–0.5 / 星等 | **即时**：`sky_view.set_extinction_k()`，重算星表可见性与亮度 | `star_catalog.effective_limit_mag()/select_for_view(extinction_k=)` + `stars.extinction_delta_mag(alt,k)`。实测同 FOV 120°、alt 20°：k=0 见 1650 颗、k=0.5 见 488 颗；alt 60° 2292→858；天顶 2372→1076。极限星等 Δm：天顶 -0.50、60° -0.58、20° -1.46、15° -1.93、5° -3.50（证明按 airmass 逐星衰减，Bortle 只定天顶极限，两者不重复） |
| A9 | 显示低仰角过境 | **已落地（读取入口）** | `show_low_elevation_passes` / False / 布尔 | 同上 | `settings.effective_show_low_elevation_passes()` + `effective_pass_visibility_gate_deg()`（开时门降到 5°）；消费点与 A1 同一调用点（§9.1） |
| A10 | 默认采样率 | **已落地（统一 2.4 MS/s）** | 复用既有 `sample_rate_hz`，默认**派生自** `desktop/constants.py: DEFAULT_SAMPLE_RATE_HZ`（2.4 MS/s，源头 `sdr_backend`），不写字面量 | 即时 | 三值相等有测试：schema 默认 / `settings.DEFAULTS` / `control_panel.sample_rate_combo.currentData()`。**遗留（他线文件）**：`desktop/main_window.py` 兜底项文案仍写「2.048 MS/s」却绑 `DEFAULT_SAMPLE_RATE_HZ`（2.4M）数据，需操作逻辑线统一为同一常量，本线未越界修改 |
| A11 | 调谐步进 | **已落地** | `tuning_step_hz` / 10 000 / 10–1 000 000 / Hz（六档） | **即时**：`step_combo` + `spectrum.set_snap_interval`；顶部下拉反写设置 | `control_panel.step_combo`、`spectrum_widget.py:921 snap_interval`；无代码缺口（本项为「无缺口更正」） |

### 9.1 A1 / A2 / A9「待接线清单」（转操作逻辑线）

前置：**函数已经写好并有测试**（`desktop/settings.py`）：

```python
from settings import (
    effective_pass_min_elevation_deg,      # A1：0–45°，默认 10°
    effective_pass_window_hours,           # A2：1–72h，默认 24
    effective_show_low_elevation_passes,   # A9：bool，默认 False
    effective_pass_visibility_gate_deg,    # A1+A9：compute_passes 实际用的门（开低仰角时降到 5°）
)
```

消费方**只需把硬编码换成函数调用**（传 `self.settings`；没注入就传 `None`，函数回默认）：

| # | 文件 | 函数 / 符号（**引用符号名，不写行号**） | 怎么接（一行） | 期望行为（一句话） |
|---|---|---|---|---|
| 1 | `desktop/new_spacetime_panel.py` | `NewSpacetimePanel.refresh_passes()` | `hours_ahead=effective_pass_window_hours(self.settings)`；`min_elevation=effective_pass_visibility_gate_deg(self.settings)`（替换硬编码 `24.0` / `PASS_MIN_ALT_DEG`） | 点「刷新过境」按用户小时数算窗口、按用户峰值门筛列表；开低仰角后 5°–A1 的过境也进表 |
| 2 | 同上 | `NewSpacetimePanel._build_passes_section()`（`passes_filter_label`）与 `refresh_passes()` 内的 `passes_hint` 文案 | 文案里用同样两个函数值拼字符串，不再写死「24 小时 / ≥10°」 | 标签与空提示必须和刷新逻辑同源，不许出现「面板说 24h、实际算 72h」 |
| 3 | 同上 | `NewSpacetimePanel.__init__()`（现只收 `parent`） | 加 `settings=None` 形参并保存为 `self.settings`（或 `attach_settings()`），冒烟时传 `None` 也不崩 | 面板能拿到 `DesktopSettings`，设置面板改完下次刷新即生效 |
| 4 | `desktop/rf_sky_view.py` | `RFSkyView._refresh_passes()`；类常量 `RFSkyView.PASS_WIN_HOURS` | 窗口改用 `effective_pass_window_hours(self.settings)`；过滤门改用 `effective_pass_visibility_gate_deg(self.settings)` | 天空图过境列表与时空面板用同一组窗口/门槛，两个界面不许出现不同数 |
| 5 | `desktop/sat_track_panel.py` | `SatTrackPanel.update_position()`；常量 `_MIN_ELEVATION_DEG = VISIBLE_MIN_ALT_DEG` | **不接这三条** | 这里的 5° 是「当前还能不能收 / 过境是否结束」的可见门，不是列表筛选门；拿 A1 覆盖它会把「过境结束」提示改错 |

> 设置面板已把三项写入 `DesktopSettings`，并在写盘后尽力调用
> `host.new_spacetime_panel.refresh_passes()`；消费方接上函数后即“改设置 → 下次刷新生效”。

> A1/A2/A9 的三项设置已在 schema/面板可见并可保存；反证测试覆盖「改设置 → 函数读到
> 新值」。消费点接上后即可端到端生效。

## 10. 本轮机恢复与提交（2026-11-05）

| 提交 | 内容 |
|---|---|
| `b742f89` | A5/A7/A8 落地 + 导入迁移/全局恢复默认测试（真造旧格式 JSON、漏项即红） |
| `4ef3a15` | 采样率统一 2.4 MS/s：schema 默认派生自 `constants.DEFAULT_SAMPLE_RATE_HZ` |
| `605e89d` | FFT 收紧到 1024/2048/4096；旧 8192/16384 回落 2048 并记 `migration_notes` |

导入迁移口径（`config_schema.parse_import`）：**同主版本允许迁移**，缺字段取默认并
报告「已按默认补齐 N 项」，多出字段按 `section.key` 逐条列名（不静默丢）；
**只有跨主版本拒绝**，错误文案说明「按旧语义解释会写坏设置」及怎么办。

冻结表缺口（C）：`tests/test_hardcoded_parameter_registry.py` 在 `5ece441` 已同时
冻结 `kPeakThresholdDefault=15` 与 `kPeakRelDb=6.0`，本线复核通过，未改动该文件。

## 11. 分组与呈现架构（2026-11-05 实测）

入口分层：右栏停靠页签「设置」（`SettingsPanel`）→ 组页签（`QTabWidget`）→
组内滚动区（`QScrollArea`）→ 每项一行（`QGroupBox`：控件 + 「默认」按钮 + help 文案）。
**服务与远程访问不在这个面板**：四个键在独立停靠页签「服务设置」
（`ServiceSettingsPanel`），避免两个面板各持一套 `RemoteControl`/`WebServer`；
「存储与日志」是特殊页签（`_build_storage_tab`），只有录音目录一项。
每行 help 由 `QLabel(item.help)` 渲染，因此 §12 的联动提示**只改 schema 文案即可显示**，
不需要动布局。

| 分组 | 面板位置 | 项数 | 项（生效方式） | 找一个项要几层 |
|---|---|---|---|---|
| 通用 | 设置页签「通用」 | 2 | 界面语言（**需重启应用**）、触屏适配（即时） | 3 |
| 显示与主题 | 设置页签 | 2 | 主题（即时）、显示瀑布图（即时） | 3 |
| 星象仪 | 设置页签 | 4 | 大气压（即时）、气温（即时）、光害等级 Bortle（即时）、大气消光 k（即时） | 3 |
| 频谱与解码 | 设置页签 | 12 | FFT 点数/窗/平均帧数、瀑布配色/深度/平滑、峰值阈值/标注、书签/频段彩带、静噪默认、调谐步进（均即时） | 3 |
| 卫星与多普勒 | 设置页签 | 5 | 地面站纬度/经度（即时写 `gui_config` 并重算）、过境最低仰角/搜索窗口/显示低仰角（即时保存，过境列表**下次刷新**生效） | 3 |
| AI | 设置页签 | 5 | 温度/top_p/输出上限/上下文/超时（即时写 `config.json`，下一轮请求即用） | 3 |
| 存储与日志 | 设置页签（特殊页） | 1 | 录音目录（只影响新录音） | 3 |
| 服务与远程访问 | **独立「服务设置」页签** | 4 | 启用 RC/Web（即时启停）、RC/Web 端口（即时保存，**服务需重启**） | **4** |

合计 35 个暴露项（设置面板 31 个，含 AI 与观测站坐标；独立「服务设置」4 个），
与 §0 口径一致。

**「翻几层」结论**：普通项 3 层（设置入口 → 组页签 → 行）；服务四项 4 层，
要先切到另一个「服务设置」停靠页签，最容易「在设置里找不到端口」。

### 11.1 错位分组（只记录，视觉冻结不动布局）

| # | 现状 | 问题 | 建议（文案/信息架构，非本轮改动） |
|---|---|---|---|
| 1 | `ui_language`（需重启）与 `touch_mode`（即时）同在「通用」 | 生效方式不同的项混排；用户可能以为语言已切换 | 面板已有橙色「重启后生效」行内提示，不误导；若要更清楚可把重启项单列一节 |
| 2 | `show_waterfall` 在「显示与主题」，`waterfall_depth_rows`/`waterfall_smooth` 在「频谱与解码」 | 同一「瀑布」概念拆两个页签，用户找深度要换页 | 保留现状（瀑布深度属频谱渲染参数）；help 已互相点名 |
| 3 | `squelch_default_db`/`tuning_step_hz` 在「频谱与解码」，实际控件在顶部控制面板 | 用户可能先去「通用」找静噪/步进 | help 写明「与顶部控件双向同步/换算入口」 |
| 4 | `observer_lat/lon` 在「卫星与多普勒」，同时驱动星象仪过境列表 | 天空线用户会去「星象仪」找坐标 | help 写明两者共同决定过境；不搬组 |
| 5 | 服务四项不在设置面板 | 4 层入口，用户找不到端口 | 属于既有设计（单一服务实例）；保持 |

## 12. 依赖约束表（改 A 必须同时改 B 才成立）

机械护栏：`tests/test_settings_schema.py::test_dependency_help_carries_cross_hint`
断言下列 10 对项**两侧 help 都点名对方 label 且含「联动」**。面板每行 help 是
`QLabel(item.help)`，所以提示直接可见，不涉及布局/颜色/尺寸。

| # | 项对（A ↔ B） | 约束 | 只改 A 不改 B 的后果 | 面板联动提示（已落地） |
|---|---|---|---|---|
| 1 | `show_low_elevation_passes` ↔ `pass_min_elevation_deg` | 开启低仰角时实际门降到 5°（`PASS_LOW_ELEVATION_FLOOR_DEG`）；`pass_min` 本身 ≤5° 时开低仰角不再增加任何过境 | 门已是 5° 时勾选开关「没反应」，用户以为坏了 | `show_low...` 点名「过境最低仰角」；`pass_min...` 点名「显示低仰角过境」 |
| 2 | `sky_extinction_k` ↔ `bortle` | 两项独立叠加：Bortle 定天顶极限星等，k 只按 `Δm=k/sin(仰角)` 压低低空天体 | 只调 Bortle 时低仰角变化不足；只调 k 时天顶星数不变——都正常，但不能互相替代 | `bortle` 点名「大气消光系数 k」；`sky_extinction_k` 点名「光害等级（Bortle）」 |
| 3 | `fft_size` ↔ `fft_avg_frames` | 延迟随两者乘积增长：4096 点 + 16 帧最慢 | 只调点数以为「只是更细」，实际刷新明显变慢 | 两侧都写明「叠加」 |
| 4 | `show_waterfall` ↔ `waterfall_depth_rows` | 瀑布关闭时深度没有可见效果，但数值保留 | 关闭瀑布后改深度「看不出变化」，误以为设置失效 | 两侧互相点名，注明关闭时保留取值 |
| 5 | `show_peaks` ↔ `peak_threshold_db` | 阈值只在「标注峰值」开启时才参与绘制 | 只调阈值但峰值开关关着，谱面毫无变化 | 两侧互相点名 |
| 6 | `waterfall_smooth` ↔ `show_waterfall` | 平滑只在瀑布绘制时生效 | 关闭瀑布后改平滑无效果 | 两侧互相点名 |
| 7 | `rc_enabled` ↔ `rc_port` | 端口只在启用远程控制时监听；运行中改端口必须重启该服务 | 只改端口不重启 → 实际仍监听旧端口 | `rc_enabled` 点名「远程控制端口」；`rc_port` 点名「启用远程控制（TCP）」 |
| 8 | `web_enabled` ↔ `web_port` | 同上（Web 服务） | 同上 | 两侧互相点名 |
| 9 | `observer_lat` ↔ `observer_lon` | 经纬度缺一不可算过境；只填一个时过境列表保持空态 | 只填纬度 → 没有任何过境，用户以为 TLE 坏了 | 两侧写明「缺任一项过境列表都为空」 |
| 10 | `ai_temperature` ↔ `ai_top_p` | 两个采样参数共同决定回答风格 | 只调温度可能与 top_p 截断冲突，效果被抵消 | 两侧互相点名 |

不在约束表内的「伪依赖」（避免过度联动）：`squelch_default_db` 与增益/模式之间是
**基线重标**关系（切模式或改增益会重标噪声基线），不是同一组设置必须同时改；
`bortle` 与 `sky_pressure_hpa/temperature_c` 是独立物理量，互不覆盖。

## 13. 手改 JSON 非法值实测（逐项：夹取 / 回默认 / 拒绝）

同主版本迁移已覆盖「旧导出文件缺字段」；本节补的是**用户直接手改
`desktop_settings.json` 写入非法值**的路径。`DesktopSettings.load` 对每个已知键调用
`_coerce` → `validate_value`，归一成功即落地，原因写入 `migration_notes`（不静默）。
以下每行都有参数化测试
`test_hand_edited_json_outliers_are_clamped_and_recorded` 钉住。

| 键 | 非法输入 | 实测落地 | 处理方式 | 原因可见 |
|---|---|---|---|---|
| `bortle` | `99` | `9` | 夹到上限 | 是（“高于上限 9，已夹到上限”） |
| `sky_extinction_k` | `9.0` | `0.5` | 夹到上限 | 是 |
| `pass_min_elevation_deg` | `-5` | `0.0` | 夹到下限 | 是 |
| `pass_window_hours` | `999` | `72` | 夹到上限 | 是 |
| `peak_threshold_db` | `100` | `30.0` | 夹到上限 | 是 |
| `squelch_default_db` | `100` | `-20` | 夹到上限（内核常量上界） | 是 |
| `rc_port` | `70000` | `65535` | 夹到上限 | 是 |
| `web_port` | `0` | `1` | 夹到下限 | 是 |
| `waterfall_depth_rows` | `384` | `256` | **非档位值回默认**（六档枚举） | 是 |
| `fft_size` | `8192` | `2048` | 非档位值回默认（三档枚举） | 是 |
| `ui_language` | `"fr"` | `"zh"` | 未知枚举回默认 | 是 |
| `tuning_step_hz` | `5000` | `10000` | 非档位值回默认（七档枚举） | 是 |
| `show_low_elevation_passes` | `"maybe"` | `False` | 无法识别布尔 → 回默认 | 是 |
| `show_waterfall` | `"yes"` | `True` | 合法别名（`1/true/yes/on`），**不记原因** | 否（预期） |
| `record_dir` | `123` | `"123"` | text 强制转字符串 | 否（预期） |
| 枚举字符串 `"2048"` | `"2048"`（fft_size） | `2048`（int） | 容忍数字字符串，不记原因 | 否（预期） |

**已确认的缺口（本线不改，登记给对应线）**：

1. **内部键没有上下限**：`frequency_hz` / `gain_db` / `bandwidth_hz` / `sample_rate_hz`
   等 13 个内部键没有 `minimum/maximum`，手改 `frequency_hz: 1e9`、`gain_db: 999`
   会被原样接受（测试 `test_internal_keys_from_hand_edited_json_are_not_bounded` 钉住
   现状）。它们由控制面板/设备层写入、UI 侧自带范围，但手改 JSON 可绕过——是否在
   schema 补范围需与设备线确认（补错范围会把合法的频率/带宽夹坏）。
2. **内部 text 键不收窄**：`demod_mode` / `agc_speed` / `app_mode` 是 text，
   手改 `"FMX"` 会原样接受；消费点在 `main_window`/后端，本线不越界改。
   若要做枚举，应先由消费方给出完整合法集合，避免把可用模式误拒。
3. **“拒绝”路径只有跨主版本**：同主版本内一律归一（夹取/回默认），不拒绝单个键；
   这是有意选择——手改一个键不应让整份配置导入失败。跨主版本由
   `parse_import` 整体拒绝并给出原因与替代做法（§10）。

## 14. A7 / A8 遗留收尾确认

| 遗留 | 状态 | 证据 |
|---|---|---|
| A7 瀑布深度六档枚举的非法值处理 | **已收** | schema `choices=(128,256,512,1024,2048,4096)`，帮助文案写明「非档位值（如 384）回落 256 并在状态栏说明原因」；`test_waterfall_depth_schema_is_six_tier_enum` + 本轮参数化 `384 → 256`（带 `migration_notes`） |
| A8 消光范围与 `set_extinction` 对齐 | **已收** | schema `minimum=0.0, maximum=0.5`（与 `rf_sky_view.EXTINCTION_K_MIN/MAX` 一致）；`RFSkyView.set_extinction()` 越界夹取并回显，`set_extinction_k()` 是**同一函数对象**的别名（`test_set_extinction_clamps_and_returns_effective_value`）；本轮参数化 `9.0 → 0.5`；端到端星数测试 `test_extinction_setting_end_to_end_changes_visible_star_count` 仍绿 |

