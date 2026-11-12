# 设置与配置体系：只读缺口调研（2026-11-11）

> 证据状态：E4——本文件数字未逐条绑定原始件/复跑命令，引用前须复核（判据见 docs/evidence-standard.md）。
> 性质：**只读调研**，未修改任何产品代码。所有结论附 `path:line`，可用文末命令复跑。
> 范围：`desktop/config_schema.py`、`desktop/settings_panel.py`、`desktop/tokens.py`、
> `desktop/startup_paths.py`、`desktop/settings.py`、`mbdsdr_ai/config.py`、`desktop/ai_panel.py`（config.json 写入方）、
> 跨端 `mobile/lib/services/settings_service.dart`。

## 0. 先报清点与「需重启」覆盖率

| 指标 | 实测值 | 依据 |
|---|---|---|
| schema 设置项总数 | **53** | `python -c "import config_schema as c; len(c.ALL_ITEMS)"` |
| 面板可见项（exposed=True） | **42** | `len(c.EXPOSED_ITEMS)` |
| 内部键（exposed=False） | **11** | 频率/增益/解调/带宽/采样率/AGC/音量/音量刻度/app_mode + `record_dir_inside_program_ack` |
| 分组数 | **8 个具名组 + 1 个空组**（空组仅存内部键） | [config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L85-L94)、[config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L510-L550) |
| 面板实际渲染分组 | **7**（「服务与远程访问」被跳过，另开独立页签） | [settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L626-L627) |
| 存储后端分布 | settings 46 / gui_config 2 / ai_config 5 | `defaults()` 分桶统计 |
| **标注 `requires_restart=True`** | **2 / 42 = 4.8%**（`ui_language`、`log_level`） | [config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L142-L143)、[config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L421-L422) |
| 有 `help` 影响面/联动文案 | **42 / 42（100%）**，但全部是自由文本，无结构化字段 | `nohelp=[]` 实测 |
| 有 `source` 默认值出处 | **53 / 53（100%）** | `nosource=[]` 实测 |
| 设置项搜索 | **无**（面板无搜索框；命令面板只能定位到「设置」页签） | [settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L609-L650)、[command_palette.py](file:///d:/mbdsdr/MBDSDR/desktop/command_palette.py#L300) |
| 盘点脚本口径 | fields 53 / ui 51 / reasons 2 / ghosts 0 / missing 0 | `settings_inventory.measure()` 实测 |

**结论一句话**：条目与默认值治理（单一事实源、出处、校验、幽灵键守卫）是扎实的；缺口集中在
**「保存了但重启不还原」「导入即全量覆盖」「config.json 三方写入」**三类真实行为，以及**生效条件标注不完整、无搜索**。

## 1. 分组合理性

| 分组 | 暴露项 | 后端 | 观察 |
|---|---|---|---|
| 通用 | 6 | settings | 混装身份（呼号）/硬件接线（GNSS）/性能档，语义跨度大 |
| 显示与主题 | 4 + 外观框 | settings | 外观框直接搬主窗控件（theme/lang/layout），与其 4 个 schema 行存在两套入口外观 |
| 星象仪 | 4 | settings | 3 项气象 + 消光，合理 |
| 频谱与解码 | **12** | settings | 全表最大组，实含频谱/瀑布/标注/步进四类，建议拆 2–3 组 |
| 存储与日志 | 2 | settings | 只有录音目录与日志级别，导入导出按钮也塞在这里 |
| 卫星与多普勒 | 5 | settings(3) + gui_config(2) | **同组两套存储/两套重置语义**（见 S-06） |
| AI | 5 | ai_config | 只有采样参数，provider/model/key 在 AI 面板，本组对用户像"半个设置" |
| 服务与远程访问 | 4 | settings | **不在设置面板内**，只能去右栏「服务设置」页签 |

## 2. 逐项生效链路核查（「改了没生效但界面显示已改」）

### S-01（P1）9 个频谱项：面板显示已保存，重启后被硬编码默认顶掉

- 位置：[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L524-L535)（`_apply_initial_state` 只回推 5 个键）、[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L1083-L1136)（只在用户改值时推送）
- 现象：`fft_size / fft_window / fft_avg_frames / waterfall_palette / peak_threshold_db / show_peaks / waterfall_smooth / show_bookmarks / show_bandplan` 在启动时**没有任何消费点读取**；`spectrum_widget.py` 全文不读 settings（仅有注释 [spectrum_widget.py](file:///d:/mbdsdr/MBDSDR/desktop/spectrum_widget.py#L226)）。重启后频谱回到硬编码默认，设置页仍显示持久化的值。
- 影响：用户"设了 4096 点、Flattop、8 帧平均"，重启全部失效且无任何提示；设置页与运行时长期分叉。
- 成本：低（把 9 个键加入 `_apply_initial_state` 即可，需处理控件未建好时序）。
- 产品决定：否（属缺陷）。
- 反证方式：构造 host 后 `attach_host`，断言 `generator` 实际窗函数/点数等于 schema 值（当前会红）。

### S-02（P1）导入=全量覆盖：未提及的项被恢复默认，观测站被清空、频率跳回 98 MHz

- 位置：[config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L833-L838)（同主版本把**所有**缺失键按默认补齐）、[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L1249-L1269)（补齐值照单全收并逐个 `_store_value`）
- 现象：手写只含 `theme` 的最小导入文件，会同时：清空 `observer_lat/lon`（`_store_value(None)` → `set_observer_site(None,None)`，[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L559-L592)）、重置 5 个 AI 采样参数、把内部键 `frequency_hz` 改回 98 MHz。
- 影响：一次"只改主题"的导入造成静默数据丢失；内部运行态键被配置备份文件带回，属跨机不可预期行为。
- 成本：中（区分 merge/full 两种导入语义，改 UI 文案与返回值）。
- 产品决定：**是**（导入应为"合并"还是"整份替换"必须定口径）。

### S-03（P1）`~/.mbdsdr/config.json` 三方写入，其中一路非原子、全份覆盖、吞异常

- 位置：[ai_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/ai_panel.py#L196-L232)（`open(...,"w")` 直接截断写、`except Exception: pass`）、[mbdsdr_ai/config.py](file:///d:/mbdsdr/MBDSDR/mbdsdr_ai/config.py#L311-L343)（原子+合并+fsync）、[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L593-L606)（原子但用构造期快照 `self._ai`，不合并、无锁）
- 现象：AI 面板保存 → 整份覆盖 `self._saved_config`；设置面板改 temperature → 用可能已过期的 `self._ai` 整份写回；内核 `save_config` → 合并写。三方无锁，最后写者赢。
- 影响：在 AI 面板刚填好的 api_key / base_url 可能被设置面板的旧快照回滚；ai_panel 截断写时进程被杀会留下半截 JSON。
- 成本：中（统一到一个写入入口 + 锁/合并）。
- 产品决定：否（属缺陷）。

### S-04（P1）AI 配置损坏时被静默移走，随后写回的文件只剩一个键

- 位置：[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L447)（构造时 `_load_json_dict(AI_CONFIG_PATH)` **不传 on_error**）、[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L387-L406)（`backup_corrupt_config` 用 `os.replace` 移走原文件）
- 现象：config.json 解析失败 → 被改名为 `*.corrupt-*`、面板内存 `{}`、**无任何提示**；用户此后随便改一项 AI 参数，就写出一个只含该键的 config.json，api_key 从活动配置消失。
- 影响：用户表现为"AI 突然要重新配 key"，且不知道原文件已被移走。
- 成本：低（传 on_error 到面板状态行 + 写回前重新读盘合并）。
- 产品决定：否。

### S-05（P1）「恢复全部默认」会重置 10 个不可见运行态键，但不更新现场

- 位置：[settings.py](file:///d:/mbdsdr/MBDSDR/desktop/settings.py#L527-L539)（`reset_all` 覆盖全部 `DEFAULTS`）、[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L1198-L1218)（随后只对 `EXPOSED_ITEMS` 调 `_apply`）
- 现象：面板只展示 42 项，但 `reset_all` 同时把频率、增益、解调模式、带宽、采样率、AGC、音量等 10 个内部键写回默认；对话框文案（[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L1191-L1195)）只提"设置、观测站坐标与服务开关"。
- 影响：用户为重置界面偏好而点按钮，当前频点/音量/模式在配置层已被改，但屏幕不变 → 重启后突然跳频/跳音量；中途录音/守听会被打断。
- 成本：低（区分"重置界面项"与"重置运行态"，或点击后同步应用到现场）。
- 产品决定：**是**（按钮语义是否包含运行态）。

### S-06（P2）同一分组两套存储与两套重置语义

- 位置：[config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L426-L466)（经纬度 `storage="gui_config"`、过境筛选 `settings`）、[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L1172-L1184)
- 现象：纬度单项点"默认"＝清空**整对**坐标；过境筛选项则是数值回默认。用户在同一组内看到两种"默认"行为。
- 影响：误以为只清纬度即可，结果经度也没了。
- 成本：低（提示文案说明成对清空）。
- 产品决定：否（文案层）。

### S-07（P2）工具栏是第二个入口，改动不落盘

- 位置：[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L473-L503)（只反写了步进与静噪）、[main_window.py](file:///d:/mbdsdr/MBDSDR/desktop/main_window.py#L9759-L9769)（只有 waterfall 可见性回写）
- 现象：频谱工具栏改 FFT 点数/窗/平均/配色/峰值阈值不回写 settings；设置页读数不变，重启后按设置页（或默认）执行。
- 影响：双入口长期分叉，用户无法判断哪个是权威值。
- 成本：中（工具栏控件补 change→settings 信号）。
- 产品决定：否。

### S-08（P2）`show_waterfall` 的联动说明未实现

- 位置：[config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L227-L234)（声称关闭后"瀑布历史深度/时间平滑不再可见"）、[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L740-L775)（无 `setVisible`/`setEnabled` 联动）
- 现象：关掉瀑布后，两项仍完整显示可改。
- 影响：说明与实现不符，用户以为联动失效。
- 成本：低。
- 产品决定：否。

### S-09（P2）生效条件标注覆盖不足：2/42 有结构化标注，另有 6 项属"延迟生效"却未标

- 位置：[config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L112-L113)（字段存在）、[config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L552-L571)
- 现象：`rc_port`/`web_port` 只写作"重启该服务"（工具提示里），`gnss_port`/`gnss_baudrate` 写"下次连接 GNSS 生效"，`perf_profile` 的采样率部分、`record_dir` 只影响新录音——六项都不是即时生效，但 `requires_restart=False`，面板不会出现橙色"重启后生效"行。
- 影响：用户改完看不到即时变化，判定为"设置坏了"。
- 成本：低（增加 `effect=instant|restart|reconnect|new-record` 枚举并渲染）。
- 产品决定：否（口径由现有文案已确定）。

### S-10（P2）无设置搜索，命令面板只能到页签

- 位置：[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L609-L650)、[command_palette.py](file:///d:/mbdsdr/MBDSDR/desktop/command_palette.py#L296-L331)
- 现象：42 项分布在 7 个折叠节、单列长滚动；输入"静噪""FFT"无处可搜。
- 影响：窄屏右栏下查找成本高，接近"不可发现"。
- 成本：中（新增过滤框，按 label/help/组名过滤并自动展开命中节）。
- 产品决定：否（体验项）。

### S-11（P2）面板文案与实现矛盾：日志级别

- 位置：[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L1296-L1302)（"日志级别仍锁死"）、[config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L413-L423)（已暴露且可改）、[logging_setup.py](file:///d:/mbdsdr/MBDSDR/desktop/logging_setup.py#L27-L51)（真实消费点）
- 现象：同一页面既给日志级别下拉框，又在下方说它锁死。
- 影响：用户不敢改，或改了以为无效。
- 成本：低（删一句）。
- 产品决定：否。

### S-12（P2）服务设置不在设置面板内，且「恢复全部默认」会影响它

- 位置：[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L612-L627)、[main_window.py](file:///d:/mbdsdr/MBDSDR/desktop/main_window.py#L9886-L9904)
- 现象：设置页顶部一句"服务开关与端口见右栏「服务设置」一节"，两个页签分列。
- 影响：用户在设置里找不到"远程控制/Web"，而"恢复全部默认"又会把服务关掉。
- 成本：低（设置页内嵌同一实例或加跳转按钮）。
- 产品决定：否。

### S-13（P2）`_log` 未定义：降级路径必抛 NameError

- 位置：[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L58-L61)、[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L77-L83)、[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L85-L95)
- 现象：模块内只有 `logger`（第 38 行），三处却调 `_log.warning`。当 `remote_control` / `web.server` / `settings` 任一导入失败时，走降级分支立刻 `NameError`，面板构造失败。
- 影响："缺内核时优雅降级"的承诺失效，会以更难懂的 ImportError/NameError 冒泡。
- 成本：极低（改 `_log`→`logger`）。
- 产品决定：否。

### S-14（P3）运行态内部键进入导出/导入

- 位置：[config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L720-L748)
- 现象：导出取 `settings.values()` 全量，只要在 SCHEMA 且非 `secret` 就写出，因此 `frequency_hz / gain_db / demod_mode / volume / agc_*` 等出现在"配置备份"里。
- 影响：备份文件被当成"偏好"传播；跨机导入会带过去一个特定频点。
- 成本：低（导出按 `exposed` 或显式白名单）。
- 产品决定：是（运行态是否算配置）。

### S-15（P3）导入不校验 `app` 标识

- 位置：[config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L779-L800)
- 现象：只校验顶层是对象与 `schema_version`，`build_export` 写了 `"app": "MBDSDR"` 但导入不读。
- 影响：任意同版本号的 JSON 都会被当作本应用配置接受。
- 成本：极低。
- 产品决定：否。

### S-16（P3）运行配置文件本身无版本字段，只有"导出文件"有

- 位置：[config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L76-L82)、[settings.py](file:///d:/mbdsdr/MBDSDR/desktop/settings.py#L400-L466)
- 现象：`desktop_settings.json` 无 `schema_version`，迁移靠 `volume_scale_v2`、`has_file_key` 这类一次性旁证；未知键只提示不处理。
- 影响：第 2 次、第 3 次语义变更时旁证会堆叠，无法判断文件属于哪一代。
- 成本：中（加版本键 + 迁移表）。
- 产品决定：否。

### S-17（P3）跨端三套口径，无映射表

- 位置：[mobile/lib/services/settings_service.dart](file:///d:/mbdsdr/MBDSDR/mobile/lib/services/settings_service.dart#L119-L198)、[config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L134-L572)
- 现象：桌面 53 项（schema 驱动、有默认/范围/出处）；移动端自建 ~20 个 Key（`apiModel`/`stationLat`/`rtlHost`…），命名与语义都不同（桌面是 `ai_*`/`observer_lat`）；Web（`web/product`）是静态原型，`settingsKey: control/httpPort` 只作展示，不是真实设置面。
- 影响：同一用户换端后设置不迁移、不解释差异；文档也无法对账。
- 成本：高（先定一份端间字段映射 + 迁移契约）。
- 产品决定：**是**。

### S-18（P3）盘点文档数字漂移（48 → 53）

- 位置：[docs/settings-inventory.md](file:///d:/mbdsdr/MBDSDR/docs/settings-inventory.md#L11-L19)（手写表：schema 48、锁死 6 类、36 项暴露）、实测 53/42/8 组；同文 §3 #28 与 §4.3 仍称"日志级别锁死"
- 现象：脚本生成块与手写汇总表两套口径并存，手写表未随新增项更新。
- 影响：接手者按旧数字判断覆盖面，误判"缺 5 项"为冗余。
- 成本：低（汇总表也改为脚本生成或删掉手写数字）。
- 产品决定：否。

### S-19（P3）样式硬编码绕过 tokens.py

- 位置：[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L766-L773)（`color:#D4956A`）、[tokens.py](file:///d:/mbdsdr/MBDSDR/desktop/tokens.py#L117)（`status_warning: #C8A040`）
- 现象：唯一的"重启后生效"提示色写死为 figma 主题的 accent 色，未走主题令牌。
- 影响：浅色/高对比主题下对比度不受控，主题线改色不会覆盖到这里。
- 成本：极低。
- 产品决定：否。

## 3. 端到端实测复现（同日补测，见 §6 命令）

三条 P1 已用**真 MainWindow + 独立 profile + 独立进程**复现，不是读代码推断：
S-01、S-02、S-05 均**复现成功**，无撤回项。

> 环境说明：四次运行进程退出码均为 `0xC0000005`（PySide6 退出期析构崩溃，见
> `tests/conftest.py` 头注），但 `RESULT_JSON` 与落盘都在崩溃前完成；复现结论
> 只取退出前打印的读数，不受退出码影响。

### 3.1 S-01：9 个频谱项重启不还原 —— 复现成功

三方对照（改 3 项，完全退出进程后重开）：

| 设置项 | 配置文件值 | 重启后设置面板显示 | 重启后控件真实值 | 一致？ |
|---|---|---|---|---|
| `fft_size` | 4096 | 4096 | **1024**（`generator.fft_size`） | 否 |
| `waterfall_palette` | `soft` | `soft` | **`sdrpp`**（`_state.wf_colors`） | 否 |
| `peak_threshold_db` | 21.0 | 21.0 | **6.0**（`_state.peak_rel_db`） | 否 |

补充事实：重启后 `fft_size` 是 **1024**，连 schema 默认 2048 都不是——启动初始化路径
既不读 settings，也不读 schema 默认，直接用了 generator 的硬编码值。

### 3.2 S-02：导入全量覆盖 —— 复现成功

导入文件只含一个合法字段 `settings.theme = "dark"`；返回
`已导入 53 项，已按默认补齐 52 项`（`filled_count=52`）。

| 观测点 | 导入前 | 导入后 |
|---|---|---|
| `settings.frequency_hz` | 145 500 000 | **98 000 000** |
| 观测站 `observer_lat/lon` | 31.23 / 121.47 | **null / null**（gui_config 也写成 null） |
| AI `temperature` | 1.5 | **0.7** |
| AI `top_p` | 0.5 | **0.9** |
| AI `max_output_tokens` | 4096 | **2048** |
| AI `max_context_tokens` | 65536 | **8192** |
| AI `timeout` | 120 | **60** |
| `theme` | dark_car | dark（唯一被导入的字段） |

顺带观测：把导入值写成不存在的枚举（如 `theme="light"`）时不会报错，`validate_value`
静默回落默认值，只在 `warnings` 里出现一行——用户很容易漏看。

### 3.3 S-05：恢复全部默认偷改运行态 —— 复现成功

先设现场：频率 145.5 MHz、音量 77、模式 USB、带宽 12.5 kHz，再点
`reset_all(confirm=False)`（与按钮同一入口）。

| 键 | 恢复后配置文件值 | 恢复后界面控件值 | 分叉 |
|---|---|---|---|
| `frequency_hz` | 98 000 000 | 145 500 000 | 是 |
| `volume` | 48 | 77 | 是 |
| `demod_mode` | `FM` | `USB` | 是 |
| `bandwidth_hz` | 0.0 | 12 500（配置仍为 0 但界面/后端未刷新） | 是 |

面板状态行仍显示「已恢复全部默认」。

## 4. 「延迟生效但未标注」6 项各自要经过什么

| 项 | 改完要经过什么才生效 | 是否会自动触发 | 面板是否标注 |
|---|---|---|---|
| `rc_port` | 关掉再打开「远程控制」服务（或重启应用后重新开启）；监听中不会热换端口 | 否，只把状态行改成「端口已保存，重启服务后生效」 | 工具提示有，schema 无结构化标记 |
| `web_port` | 同 `rc_port`：停/启 Web 服务，或重启应用后重开 | 否 | 同上 |
| `gnss_port` | 下一次「连接 GNSS」（设置变更只刷新主窗口缓存，不自动重连） | 否 | 帮助文本写「下次连接 GNSS 生效」 |
| `gnss_baudrate` | 同上，下一次连接 GNSS | 否 | 同上 |
| `perf_profile`（采样率部分） | 断开并重新连接 SDR（档位只决定「下次连接的默认采样率」；FFT/刷新/GNSS 轮询是即时生效） | 否，采样率不会自动重下发 | 帮助文本有，无结构化标记 |
| `record_dir` | 下一次开始录音（已录文件不迁移，正在录的也不受影响） | 否 | 存储页说明有 |

结论：6 项的生效条件全部只在自由文本/工具提示里，`requires_restart` 均为 False，
面板不会出现橙色「重启后生效」行，机器也无法据此做闸门。

## 5. 其它已核实但未列为缺口的项（避免重复排查）

- 配置损坏：`desktop_settings.json` 会 `*.corrupt-时间戳` 备份并 `last_error` 可见（[settings.py](file:///d:/mbdsdr/MBDSDR/desktop/settings.py#L456-L465)）；`gui_config.json` 有同样的备份机制（[main_window.py](file:///d:/mbdsdr/MBDSDR/desktop/main_window.py#L13028-L13040)）。
- 并发写：`DesktopSettings` 有跨进程锁 + 脏键合并 + 唯一临时文件（[settings.py](file:///d:/mbdsdr/MBDSDR/desktop/settings.py#L594-L699)），这部分是本轮范围里做得最完整的一层。
- 默认值单一事实源：`DEFAULTS = defaults("settings")`（[settings.py](file:///d:/mbdsdr/MBDSDR/desktop/settings.py#L39-L41)），主题默认与 `themes.DEFAULT_THEME` 实测一致（均为 `dark_car`）。
- 键盘/命令面板：命令面板能按名称定位到「设置」页签，只缺项级搜索（S-10）。
- 重启项的门禁：`ui_language`、`log_level` 在面板行内有橙色说明（[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L766-L773)），实现正确，只是覆盖面太小。

## 6. 复跑命令

```powershell
cd D:\mbdsdr\MBDSDR
python -c "import sys; sys.path[:0]=['.','desktop']; import config_schema as c; print(len(c.ALL_ITEMS), len(c.EXPOSED_ITEMS), [i.key for i in c.ALL_ITEMS if i.requires_restart])"
python -c "import sys; sys.path[:0]=['.','desktop','scripts']; import settings_inventory as s; r=s.measure(); print(r.fields, r.ui_entries, r.reasons, len(r.ghosts))"
python -m pytest tests/test_settings_ui_coverage.py tests/test_settings_schema.py -q
```

§3 的端到端复现用一个**一次性**脚本（未入库，位于仓库外 `D:\mbdsdr\output\tmp\repro_settings_gap.py`，
保留以便复跑）驱动真 `MainWindow`，`QT_QPA_PLATFORM=offscreen` +
`HOME/USERPROFILE/APPDATA/LOCALAPPDATA` 全部指向独立 profile 目录，**不碰真实用户配置**：

```powershell
python repro_settings_gap.py set-spectrum  <profile1>          # 进程A：改 3 项并 flush
python repro_settings_gap.py read-spectrum <profile1>          # 进程B：重启后读真实运行态
python repro_settings_gap.py import-overwrite <profile4> <theme-only.json>
python repro_settings_gap.py reset-runtime  <profile3>
```

复现读数取自 `w.spectrum.generator.fft_size` / `w.spectrum._state.wf_colors` /
`w.spectrum._state.peak_rel_db`（真实消费点），不是设置面板显示值。

## 7. 严重度排序表

| # | 严重度 | 缺口 | 位置 | 用户可感知后果 | 成本 | 需产品决定 |
|---|---|---|---|---|---|---|
| S-01 | P1（**已实测**） | 9 个频谱项重启不还原 | settings_panel.py:524-535 | 4096→1024、soft→sdrpp、21→6，页面仍显示旧值 | 低 | 否 |
| S-02 | P1（**已实测**） | 导入即全量覆盖（清观测站/频率回 98 MHz） | config_schema.py:833-838；settings_panel.py:1249-1269 | 只导入 theme，52 项被补默认 | 中 | **是** |
| S-03 | P1 | config.json 三方写入，ai_panel 非原子整份覆盖 | ai_panel.py:196-232；settings_panel.py:593-606 | api_key/模型可能被旧快照回滚 | 中 | 否 |
| S-04 | P1 | AI 配置损坏被静默移走，写回只剩一个键 | settings_panel.py:447、387-406 | "AI 突然要重配 key" | 低 | 否 |
| S-05 | P1（**已实测**） | 恢复全部默认偷偷重置 10 个运行态键 | settings.py:527-539；settings_panel.py:1198-1218 | 配置回 98 MHz/48/FM，界面仍 145.5/77/USB | 低 | **是** |
| S-06 | P2 | 同组 gui_config 与 settings 两套重置语义 | settings_panel.py:1172-1184 | 清纬度连经度一起没 | 低 | 否 |
| S-07 | P2 | 工具栏改 FFT/配色等不回写 | settings_panel.py:473-503 | 双入口长期分叉 | 中 | 否 |
| S-08 | P2 | show_waterfall 联动说明未实现 | config_schema.py:227-234 | 联动承诺落空 | 低 | 否 |
| S-09 | P2 | 生效条件标注仅 2/42，另 6 项延迟生效未标 | config_schema.py:552-571 | 改完没反应被当成坏了 | 低 | 否 |
| S-10 | P2 | 无设置项搜索 | settings_panel.py:609-650 | 42 项长列里找不到 | 中 | 否 |
| S-11 | P2 | 页面文案称日志级别"仍锁死" | settings_panel.py:1296-1302 | 与控件自相矛盾 | 低 | 否 |
| S-12 | P2 | 服务设置不在设置面板内 | settings_panel.py:612-627 | 设置里找不到服务开关 | 低 | 否 |
| S-13 | P2 | `_log` 未定义，降级路径 NameError | settings_panel.py:61、81、92 | 缺内核时面板直接构造失败 | 极低 | 否 |
| S-14 | P3 | 运行态内部键进入导出/导入 | config_schema.py:720-748 | 备份文件携带特定频点 | 低 | **是** |
| S-15 | P3 | 导入不校验 app 标识 | config_schema.py:779-800 | 任意同版本 JSON 被接受 | 极低 | 否 |
| S-16 | P3 | 运行配置文件无版本字段 | settings.py:400-466 | 多代迁移旁证堆叠 | 中 | 否 |
| S-17 | P3 | 桌面/移动/Web 三套口径无映射 | mobile/…settings_service.dart:119-198 | 换端设置不迁移 | 高 | **是** |
| S-18 | P3 | 盘点文档 48 vs 实测 53 | docs/settings-inventory.md:11-19 | 接手者按旧口径判断 | 低 | 否 |
| S-19 | P3 | 提示色硬编码绕过 tokens.py | settings_panel.py:766-773 | 浅色主题对比度不受控 | 极低 | 否 |
