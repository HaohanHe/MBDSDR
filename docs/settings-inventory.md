# 设置与配置盘点（settings-inventory）

> 取证时间：2026-11-05。范围：`desktop/` 桌面端 + `mbdsdr_ai/config.py`。
> 本文件是"设置与配置架构线"的交付证据；所有行号以当前工作区为准（主窗口正在被
> 另一条线改动，行号可能小幅漂移，已尽量同时给出常量名/键名）。

## 0. 结论（先给数字）

| 项 | 数量 | 说明 |
|---|---|---|
| 盘点的配置/持久化入口 | **6 处** | DesktopSettings、gui_config、AI config、QSettings、layout_store、bookmarks |
| 盘点出的"硬编码但用户会想改"条目 | **31 条** | 见 §3 清单 |
| 其中已暴露到设置面板 | **22 条** | 其中 19 条落 DesktopSettings，2 条落 gui_config，5 条落 AI config；去重后 26 个 schema 项 |
| 判定锁死、不暴露 | **9 类** | 理由见 §4（不是偷懒，每条都有具体消费点缺失或双入口风险） |
| schema 总条目 | **39** | 26 暴露 + 13 内部键（频率/增益/音量等已有即时控件） |
| 设置面板新增测试 | **21 个** | `tests/test_settings_schema.py`，全绿 |
| 相关回归 | **117 + 29 个** | 配置/主题/布局/触屏/i18n 117；三栏与面板集成 29 |

**必须先说的事实**：用户点"设置"目前**不是空面板，而是只有一个"服务设置"页签**——
里面只有 2 个开关（远程控制 TCP、Web 服务）和 2 个端口输入框（7356 / 8080）。
除此之外**没有任何通用设置面板**：主题与触屏只藏在"视图"菜单里，语言只有配置文件，
观测站坐标、频谱偏好、AI 参数、星象仪气象参数全都没有 UI。用户的抱怨成立。

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

## 3. 硬编码清单（31 条）

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
| 23 | Bortle / 肉眼极限星等 | `rf_sky_view.py:1714` 写死 3 | 不能 | **锁死** |
| 24 | 星象仪图层四开关 | `rf_sky_view.py:6944-6957` | 底栏即时，不持久 | **锁死**（双入口） |
| 25 | 调谐吸附步进 | `main_window.py:4232-4235` 由控制面板步进覆盖 | 控制面板 | **锁死**（会被覆盖） |
| 26 | Max/Min hold、dB 范围 | `spectrum_widget.py` 会话态 | 工具栏 | **锁死**（测量态非偏好） |
| 27 | 录音目录 | `main_window.py:5536` 写死 `~/mbdsdr_recordings` | 不能 | **锁死**（消费点在他人文件） |
| 28 | 日志级别 | 无 `basicConfig`，只有分散 `getLogger` | 不能 | **锁死**（没有消费点） |
| 29 | 采样率/带宽/增益/解调/音量 | 旧 `settings.py` + 设备/控制面板 | 面板即时且已持久 | **锁死**（已有单一入口） |
| 30 | AI provider/model/key/base_url | `ai_panel.py` 配置对话框 | 已有 UI | **锁死**（避免密钥双入口） |
| 31 | 星象仪 FOV / 时间倍率 | 星象仪底栏 | 已有 UI | **锁死**（会话态视图操作） |

**统计口径**：31 条中 22 条暴露（#1–#22），9 类锁死（#23–#31）。

## 4. 为什么锁死（逐条理由）

1. **Bortle / 肉眼极限**：`_bortle` 写死 3 且**没有公开 setter**；`rf_sky_view.py`
   归坐标视图线，擅自 `setattr` 会产生两套不同步状态。等其提供 setter 再进 schema。
2. **星象仪图层**：底栏已有方位网格/赤道网格/罗盘/轨迹四个开关；设置里再开一套会
   出现"设置里打开、底栏显示关闭"的假象。持久化应由坐标视图线在底栏直接写 schema。
3. **吸附步进**：控制面板每次改步进都会覆盖 `snap_interval`（`main_window.py:4232`），
   设置默认值活不过一次调谐，属于假配置。
4. **Hold / dB 范围**：是测量态临时工具，重启清零是预期行为；持久化反而让用户以为
   下次还在测同一个信号。
5. **录音目录**：消费点在 `main_window.py:5536`（归另一条线），本线无权改消费代码；
   暴露开关只会得到"改了不生效"的假设置。等主窗口线提供 `record_dir` 读取点后再加。
6. **日志级别**：桌面端没有统一 logging 配置，只有分散 `getLogger`，没有可改的消费点。
   先补日志基础设施，再谈级别配置。
7. **采样率/带宽/增益/解调/音量**：设备面板与控制面板已有即时控件且已持久化，设置里
   重复暴露会产生两个入口（§2 表第 1 行已覆盖这些键，因此它们仍是 schema 的内部键）。
8. **AI provider/model/key/base_url**：AI 面板已有配置对话框（`ai_panel.py:355-415`），
   设置里再放一个 Key 输入框会导致两处密钥、两套保存路径。
9. **星象仪 FOV / 时间倍率**：底栏已有倍率与重置摄像机按钮，且属于会话态视图操作，
   不是长期偏好。

## 5. 暴露项三问（何时改 / 默认依据 / 改错校验）

| 设置项 | ① 用户何时会改 | ② 默认值及依据 | ③ 改错会怎样（校验/范围） |
|---|---|---|---|
| 界面语言 | 换母语/给外国朋友演示 | zh：`tr()` 源文即中文（`l10n.py:5`） | 枚举 zh/en/ja；未知回 zh；重启生效并在行内提示 |
| 触屏适配 | 平板/二合一接鼠标 | auto：`touch_manager` 自动探测 QTouchDevice | 枚举 auto/on/off；未知回 auto |
| 主题 | 夜间/强光/无障碍 | default：`themes.py DEFAULT_THEME` | 枚举 5 套；未知回 default；即时 `_apply_theme` |
| 显示瀑布 | 弱机省绘制 / 只看谱线 | True：旧 settings 默认 | 布尔；即时 `toggle_waterfall` |
| 大气压 | 高原/低压天气 | 1013.25：标准大气（`rf_sky_view` 常量） | 800–1100，越界夹住并提示；即时 `set_refraction_params` |
| 气温 | 冬夏/高低温 | 10 ℃：该常量注释的标准大气 | -50–50，越界夹住；即时 |
| FFT 点数 | 找弱信号/看细节 vs 要流畅 | 2048：现有默认 + SDR++ 档位 | 1024–16384 枚举；未知回 2048；即时 `set_fft_size` |
| 窗函数 | 测幅度（Flattop）/低旁瓣（Nuttall） | Nuttall：现有默认 | 6 种枚举；未知回 Nuttall；即时 |
| 平均帧数 | 弱信号平滑 vs 快速响应 | 1：现有默认 | 1/4/8/16；未知回 1；即时 |
| 瀑布配色 | 弱信号层次 vs 不刺眼 | default：现有第 0 项 | default/sdrpp；未知回 default；即时 |
| 峰值阈值 | 噪声大/信号密集 | 6.0 dB：既有 `peak_rel_db` | 1–30 dB，越界夹住；即时 |
| 标注峰值 | 只想看强信号 | True：既有默认 | 布尔；即时 |
| 瀑布平滑 | 追踪弱轨迹 | False：既有默认 | 布尔；即时 |
| 显示书签 | 小屏省空间 | True：既有默认 | 布尔；即时 |
| 显示频段彩带 | 只想看谱 | True：既有默认 | 布尔；即时 |
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
  频谱 → 既有 `generator.set_*` + 工具栏控件同步；星象仪 → `set_refraction_params`；
  观测站 → 写 `gui_config` 并调 `_apply_observer_location` / `_save_gui_config`；
  AI → 写 `config.json`（保留 api_key）并同步现有 `agent.config`。
  **只有界面语言需要重启**，行内明确写"重启生效：Qt 翻译器必须在窗口创建前安装"。
- **损坏/写失败可见**：读取失败 → 备份 `*.corrupt-时间戳` + `last_error` 文案；
  写盘失败 → `on_error` 回调（设置面板状态栏显示"设置保存失败"）。不含静默 catch-all：
  扫描器 `scripts/lint_swallow_all.py` 下 `settings.py` 已从基线 2 降到 ERROR 1 / WARN 0，
  `config_schema.py` 为 0。
- **恢复默认**：每行一个"默认"按钮（单项）；底部"恢复全部默认"（settings + 观测站清空
  + AI 采样参数 + 服务关闭）。恢复后控件回填、host 行为同步（测试
  `test_panel_reset_all_restores_behavior`）。
- **导入/导出**：导出一份可读 JSON，含 `schema_version=1` 与 `exported_at`；
  导出**永不含 api_key**（`build_export` 过滤 secret）。导入先校验版本（不符直接拒绝），
  再校验字段：未知字段进 `unknown` 列表并弹窗列出，**不静默丢弃**；跨段字段进
  `rejected`；越界值夹取并写进 `warnings`。
- **文案**：不出现 `min_fov`、`fft_size` 这类内部词；标题用"频谱分辨率（FFT 点数）"
  "最远/最近能看到的天空范围"之外的现有术语均配一句话解释。

## 7. 测试证据

- 新增：`tests/test_settings_schema.py` **21 passed**，覆盖
  单一事实源、越界/坏类型不崩、损坏备份、写失败可见、落盘往返、单项/全部恢复默认、
  导出含版本且无密钥、导入版本拒绝/未知字段上报、面板分组齐全、真实控件信号→落盘→生效、
  观测站写 gui_config、AI 参数不动密钥、重启项标注、锁死项有理由。
- 回归：`test_settings_convergence` / `test_settings_semantics` / `test_theme_switch` /
  `test_theme_tokens` / `test_layout_store` / `test_touch_adaptation` /
  `test_desktop_i18n` + 新测试 = **117 passed**。
- 集成：`test_three_column_layout` + `test_ui_panels_integration` = **29 passed**
  （含新"设置"页签停靠与三栏布局不回归）。
- 棘轮：`lint_swallow_all.py --no-fail` 扫描，本线文件全部不越线；
  `desktop/settings.py` 低于基线。

## 8. 与其它线的边界 / 在途冲突

- `desktop/main_window.py` 仅做了**最小停靠接线**：新增 `SettingsPanel` 导入（1 行）+
  在服务页签之后创建"设置"页签（4 行），未改任何既有逻辑。
- `desktop/spectrum_widget.py`、`desktop/rf_sky_view.py` **未改一行**；只通过其既有公开
  方法/控件消费配置（`set_fft_size`、`set_refraction_params`、`toggle_waterfall` 等）。
- 现场存在他人未提交改动：`desktop/rf_sky_view.py`、`desktop/main_window.py` 等；
  另外 `mbdsdr_ai/sdr_tools.py` 当前有 `IndentationError`、`main_window.py` 近期出现
  `NameError: SQUELCH_MIN_DBFS`，均为**其它线在途状态**，本线未触碰、未覆盖。
  因此"真实主窗口点面板改值"的最终联调被暂时阻塞；面板+假 host 的单测已覆盖全部
  生效路径，待主窗口线恢复后可直接复验。
