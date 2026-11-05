# 设置与配置盘点（settings-inventory）

> 取证时间：2026-11-05。范围：`desktop/` 桌面端 + `mbdsdr_ai/config.py`。
> 本文件是"设置与配置架构线"的交付证据；所有行号以当前工作区为准（主窗口正在被
> 另一条线改动，行号可能小幅漂移，已尽量同时给出常量名/键名）。

## 0. 结论（先给数字）

| 项 | 数量 | 说明 |
|---|---|---|
| 盘点的配置/持久化入口 | **6 处** | DesktopSettings、gui_config、AI config、QSettings、layout_store、bookmarks |
| 盘点出的"硬编码但用户会想改"条目 | **32 条** | 见 §3 清单（新增 #25 调谐步进、#32 静噪门限） |
| 其中已暴露到设置面板 | **26 条** | 23 条落 DesktopSettings，2 条落 gui_config，5 条落 AI config；去重后 30 个 schema 项 |
| 判定锁死、不暴露 | **6 类** | 理由见 §4（不是偷懒，每条都有具体消费点缺失或双入口风险） |
| schema 总条目 | **43** | 30 暴露 + 13 内部键（频率/增益/音量等已有即时控件） |
| 设置面板新增测试 | **39 个** | `tests/test_settings_schema.py`，全绿 |
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
| 23 | Bortle / 肉眼极限星等 | `rf_sky_view.py:1714` 默认 3；新增公开 `set_bortle` | 设置面板 1–9 级 | **暴露** `bortle`（立即生效，实测 3↔8 星数 1456↔194） |
| 24 | 星象仪图层四开关 | `rf_sky_view.py:6944-6957` | 底栏即时，不持久 | **锁死**（双入口） |
| 25 | 调谐吸附步进 | 旧 `spectrum_widget.py:921` `snap_interval=10_000`；控制面板步进下拉 | 控制面板即时；本轮起也持久化 | **暴露** `tuning_step_hz`（与顶部「步进」双向同步，改哪边都落盘） |
| 26 | Max/Min hold、dB 范围 | `spectrum_widget.py` 会话态 | 工具栏 | **锁死**（测量态非偏好） |
| 27 | 录音目录 | `main_window.py:5749` 旧值 `~/mbdsdr_recordings`；新默认由 `settings.py:default_record_dir()` 给出 | 设置面板可改 + 选择/打开目录 | **暴露** `record_dir`（默认 `D:\mbdsdr\recordings`，只影响新录音） |
| 28 | 日志级别 | 无 `basicConfig`，只有分散 `getLogger` | 不能 | **锁死**（没有消费点） |
| 29 | 采样率/带宽/增益/解调/音量 | 旧 `settings.py` + 设备/控制面板 | 面板即时且已持久 | **锁死**（已有单一入口） |
| 30 | AI provider/model/key/base_url | `ai_panel.py` 配置对话框 | 已有 UI | **锁死**（避免密钥双入口） |
| 31 | 星象仪 FOV / 时间倍率 | 星象仪底栏 | 已有 UI | **锁死**（会话态视图操作） |
| 32 | 静噪门限默认值 | `mbdsdr_ai/squelch.py:52` `DEFAULT_SQUELCH_DBFS=-50`；旧门控 `main_window.py:520` 实际按 -80 | 控制面板滑杆即时；本轮起也持久化 | **暴露** `squelch_default_db`（默认/范围直接取内核常量；与滑杆双向同步） |

**统计口径**：32 条中 26 条暴露（#1–#23、#25、#27、#32），6 类锁死（#24、#26、#28–#31）。

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
| FFT 点数 | 找弱信号/看细节 vs 要流畅 | 2048：现有默认 + SDR++ 档位 | 1024–16384 枚举；未知回 2048；即时 `set_fft_size` |
| 窗函数 | 测幅度（Flattop）/低旁瓣（Nuttall） | Nuttall：现有默认 | 6 种枚举；未知回 Nuttall；即时 |
| 平均帧数 | 弱信号平滑 vs 快速响应 | 1：现有默认 | 1/4/8/16；未知回 1；即时 |
| 瀑布配色 | 弱信号层次 vs 不刺眼 | default：现有第 0 项 | default/sdrpp；未知回 default；即时 |
| 峰值阈值 | 噪声大/信号密集 | 6.0 dB：既有 `peak_rel_db` | 1–30 dB，越界夹住；即时 |
| 标注峰值 | 只想看强信号 | True：既有默认 | 布尔；即时 |
| 瀑布平滑 | 追踪弱轨迹 | False：既有默认 | 布尔；即时 |
| 显示书签 | 小屏省空间 | True：既有默认 | 布尔；即时 |
| 显示频段彩带 | 只想看谱 | True：既有默认 | 布尔；即时 |
| 调谐步进 | 细调弱信号 vs 快速跨段 | 10 kHz：`spectrum_widget.py` 既有 `snap_interval`，档位同控制面板 | 10 Hz–1 MHz 六档枚举；未知回 10 kHz；即时推 `step_combo` + `set_snap_interval`；顶部改动反写设置 |
| 静噪门限 | 底噪大 / 弱信号被切断 | -50 dBFS：直接取 `mbdsdr_ai.squelch DEFAULT_SQUELCH_DBFS`（schema 不写第二份） | -100～-20 dBFS（取 `SQUELCH_MIN/MAX_DBFS`），越界夹住；即时推滑杆 + `_on_squelch_changed`；滑杆改动反写设置 |
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

- 新增：`tests/test_settings_schema.py` **39 passed**，覆盖
  单一事实源、越界/坏类型不崩、损坏备份、写失败可见、落盘往返、单项/全部恢复默认、
  导出含版本且无密钥、同主版本旧文件补齐迁移/较新次版本未知字段列出/跨主版本拒绝、
  面板分组齐全、真实控件信号→落盘→生效、观测站写 gui_config、AI 参数不动密钥、
  重启项标注、锁死项有理由；Bortle 1–9 schema + 即时生效 + 重启仍生效 + **同 FOV
  星数反证（Bortle 3=1456、Bortle 8=194）** + setter 越界/坏值；录音目录默认不落
  用户目录、留空回默认、`ensure_record_dir` 建目录、盘不可用时返回可执行文案且
  状态栏与弹窗同时可见、面板行与"选择/打开目录"按钮；
  **A5 静噪** schema 默认/范围与 `mbdsdr_ai.squelch` 常量逐值相等 + 改设置后
  滑杆与门控都变 + 滑杆反写设置 + 旧配置迁移提示（提示同时把新键按默认值落盘，
  下次启动不再重复；不覆盖旧文件里已有的键）；**A11 调谐步进**改设置后
  `snap_interval` 与下拉框都变 + 下拉反写设置；
  服务面板保存/启动/停止失败都在状态行可见。
- 回归：`test_settings_convergence` / `test_settings_semantics` / `test_theme_switch` /
  `test_theme_tokens` / `test_layout_store` / `test_touch_adaptation` /
  `test_desktop_i18n` = **96 passed**（另有本文件 39 项专项测试，合计 135）。
- 集成：`test_three_column_layout` + `test_ui_panels_integration` = **29 passed**
  （含新"设置"页签停靠与三栏布局不回归）。
- 棘轮：`lint_swallow_all.py --no-fail` 扫描，`desktop/settings.py` 0、
  `desktop/config_schema.py` 0、`desktop/settings_panel.py` **0（基线 11 → 0）**、
  `tests/test_settings_schema.py` 0；本线文件全部不越线。

## 8. 与其它线的边界 / 在途冲突

- `desktop/main_window.py` 仅做了**最小停靠接线**（已提交 `49eddcf`，9 insertions /
  1 deletion）：新增 `SettingsPanel` 导入 + 在服务页签之后创建"设置"页签，未改任何
  既有逻辑。
- `desktop/spectrum_widget.py` **未改一行**；只通过其既有公开方法/控件消费配置。
- `desktop/rf_sky_view.py` **只新增**公开 `set_bortle()`（不改既有逻辑/私有状态调用），
  面板与持久化都走它；该文件另有坐标视图线的未提交 logging 改动，本线提交时只取
  自己那段 hunk，不覆盖对方修改。
- 录音目录消费点仍是 `desktop/main_window.py:5749` 的 `~/mbdsdr_recordings` 与
  `:6957` 回放对话框（归操作逻辑线）。本线已提供 `settings.record_dir` /
  `ensure_record_dir()` 读取入口，**未改消费代码**；接入说明见 §8.1。
- 现场另有他人未提交改动（`desktop/rf_sky_view.py`、`desktop/receive_pipeline.py` 等），
  本线未触碰、未覆盖；`receive_pipeline.py` 的棘轮越线由审计线另案处理。

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

| # | 条目 | 处置 | schema 键 / 默认 / 范围 / 单位 | 生效方式 | 消费点与证据 |
|---|---|---|---|---|---|
| A1 | 过境最低仰角 | **暂不暴露**（消费点在卫星线） | 拟 `pass_min_elevation_deg` / 10 / 0–45 / 度 | 接入后即时 | `sat_track_panel.py`、`rf_sky_view.py`、`new_spacetime_panel.py`；本线未改 |
| A2 | 过境搜索窗口 | **暂不暴露**（消费点在卫星线） | 拟 `pass_window_hours` / 24 / 1–72 / 小时 | 接入后即时 | `new_spacetime_panel.py:533 compute_passes`；本线未改 |
| A3 | 录音总容量保留上限 | **不做**（无消费点） | 拟 `recording_retention_total_gb` / 0–2000 / GB | — | `RetentionPolicy` 存在但 desktop 无调用者；加了也没人执行 |
| A4 | 录音保留天数 | **不做**（无消费点） | 拟 `recording_retention_days` / 0–3650 / 天 | — | 同上 |
| A5 | 静噪门限默认值 | **已落地** | `squelch_default_db` / `int(DEFAULT_SQUELCH_DBFS)`=-50 / `int(SQUELCH_MIN_DBFS)`=-100 ～ `int(SQUELCH_MAX_DBFS)`=-20 / dBFS | **即时**：滑杆 + 门控；滑杆反写设置 | `control_panel.squelch_slider` → `main_window._on_squelch_changed`；默认/范围取自 `mbdsdr_ai/squelch.py`，schema 无第二份字面量 |
| A6 | AI 工具超时 | **不做**（无消费点） | 拟 `ai_tool_timeout_s` / 120 / 10–600 / 秒 | — | `mbdsdr_ai/agent.py:630` 为常量，无配置字段可读，改了不生效 |
| A7 | 瀑布深度行数 | **不做**（Python 无 setter） | 拟 `waterfall_depth_rows` / 256 / 128–4096 / 行 | — | `cpp/tokens.h:698` 可选，Python `spectrum_widget` 固定且无 `set_waterfall_depth` |
| A8 | 大气消光系数 | **不做**（无 setter） | 拟 `sky_extinction_k` / 0.2 / 0–0.5 / 星等 | — | `stars.py` 有 `k` 参数但视图不传，无公开入口 |
| A9 | 显示低仰角过境 | **暂不暴露**（消费点在卫星线） | 拟 `show_low_elevation_passes` / False / 布尔 | 接入后即时 | `sat_passes` / `sat_track_panel` / `new_spacetime_panel`；无公开 setter |
| A10 | 默认采样率 | **不新增第二键** | 复用既有 `sample_rate_hz`（2.4 MSPS 由后端能力回落） | 即时 | `main_window.py:2944/3363-3367`、`spectrum_widget.py:188`、`sdr_backend.DEFAULT_SAMPLE_RATE_HZ` 已覆盖 |
| A11 | 调谐步进 | **已落地** | `tuning_step_hz` / 10 000 / 10–1 000 000 / Hz（六档） | **即时**：`step_combo` + `spectrum.set_snap_interval`；顶部下拉反写设置 | `control_panel.step_combo`、`spectrum_widget.py:921 snap_interval`；无代码缺口（本项为「无缺口更正」） |

### 9.1 给卫星/天空线的接入说明（可直接照抄）

以下三条在**对方文件**里改，本线不代办。已在 `DesktopSettings.get()` 提供通用读取；
等对方接上消费点后，再按 §9 第一列把 key 加入 `ALL_ITEMS` 并设 `exposed=True`：

```python
# A1：过境最低仰角（度）
min_elev = float(getattr(self, "settings", None).get("pass_min_elevation_deg", 10))
# 传给 sf.compute_passes(..., min_elevation_deg=min_elev)

# A2：过境搜索窗口（小时）
hours = int(getattr(self, "settings", None).get("pass_window_hours", 24))
# 传给 new_spacetime_panel.compute_passes(..., hours=hours)

# A9：是否显示低仰角过境
show_low = bool(getattr(self, "settings", None).get("show_low_elevation_passes", False))
# 传给过境列表过滤：if not show_low: rows = [r for r in rows if r.max_elev >= min_elev]
```

> 注意：在这三条被真正读取之前，本线**不会**把它们写进 schema/面板——否则就是又一个
> “能存能读但没人用”的假设置。A5/A11 已按反证测试（改设置 → 消费点读到新值）落地。
