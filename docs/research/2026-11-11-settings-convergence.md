# 配置一致性收敛方案（草案，未改任何代码）

> 证据状态：E4——本文件数字未逐条绑定原始件/复跑命令，引用前须复核（判据见 docs/evidence-standard.md）。
> 性质：**只读设计**。本文只给收敛方案、改动点与反证设计，不含任何代码修改。
> 输入证据：[2026-11-11-gap-settings.md](file:///d:/mbdsdr/MBDSDR/docs/research/2026-11-11-gap-settings.md) §3（三条 P1 已端到端实测复现）。

## 0. 五条硬裁定（先给结论）

| # | 议题 | 裁定 | 理由 |
|---|---|---|---|
| 1 | 写入通道 | `config.json` 只能有一个写入口：`ConfigStore`（读-合并-写全程持锁） | 现状三方各写各的，最后写者赢；api_key 回滚是必然不是偶然 |
| 2 | 导入语义 | **合并（merge）**；缺键=「未表态」，不得视为「改回默认」 | 导入是恢复配置，不是"整机还原"；实测一次 theme 导入清掉 52 项 |
| 3 | `reset_all` | **不改运行态**：只重置面板可见项，9 个运行态键 + 1 个迁移标志 `volume_scale_v2` 不参与；`record_dir_inside_program_ack` 纳入重置 | 最小惊讶；避免守听中途跳频；风险面积最小 |
| 4 | 启动回推 | 单一真值源 = `fft_size`；性能档只做「预设宏」，不再拥有 FFT | 1024 来自 `DEFAULT_PROFILE="eco"` 的档位值，是第二权威源 |
| 5 | 延迟生效 | 新增结构化 `apply_mode` + `consumer`，面板自动渲染橙字，测试全表遍历 | 6 项目前只有自由文本，无法机检 |

## 1. 单一写入通道

### 1.1 现状：三个写入点各自为政

| 写入点 | 触发条件 | 写入范围 | 原子性 | 锁 | 保留未知键 |
|---|---|---|---|---|---|
| [ai_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/ai_panel.py#L196-L232) `_save_config` | 用户点「保存」按钮（`_apply_dialog_result` → `_save_config`） | **整份** `self._saved_config` 覆盖 config.json | 否（`open(...,"w")` 直接截断） | 无 | 只在构造时读过一次；之后别处新增的键会丢 |
| [settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L598-L605) `_store_value`（ai_config 分支） | 改动 5 个 AI 采样参数中任意一项 | **整份** `self._ai`（构造期快照）覆盖 | 是（`_atomic_write_json`） | 无 | 只在"快照里有"时保留；快照过期即丢 |
| [mbdsdr_ai/config.py](file:///d:/mbdsdr/MBDSDR/mbdsdr_ai/config.py#L311-L343) `save_config` | AI 内核侧保存/迁移 | 按字段**合并**磁盘现值 | 是 | 无（读→合并→写之间存在窗口） | 保留 |

三个写入点的"读"分别发生在：面板构造时一次（[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L447)）、每轮保存时（mbdsdr_ai）、只在面板对象构造时读一次、之后从不重读（[ai_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/ai_panel.py#L185-L208)）。所以：

- 设置面板改 temperature → 用旧快照把 ai_panel 刚写的 api_key 顶掉；
- ai_panel 保存 → 把 mbdsdr_ai 写的未知键（如 `tx_audit_*`）顶掉；
- 崩溃时 ai_panel 可能留下半截 JSON（非原子）。

### 1.2 目标语义：一个 store，一次读-改-写

```
ConfigStore(path).update(mutator) -> bool
  1. 取跨进程锁（复用 settings.py 的 _exclusive_lock 纪律）
  2. 锁内重新读磁盘（损坏则先备份，读失败→中止并回报，不盲写）
  3. 把 mutator 应用到"磁盘现值"上（不是内存快照）
  4. 临时文件 + fsync + os.replace
  5. 释放锁，更新内存基线
```

- **谁负责读-改-写**：`ConfigStore.update()` 独占；调用方只传"我要改哪几个键"，不持有整份 dict。
- **加锁还是串行**：跨进程锁（与 `DesktopSettings._write_to_disk` 同款），不是只靠 Qt 单线程串行——`mbdsdr_ai` 可能跑在 agent 线程/独立进程。
- **原子替换怎么做**：唯一临时文件名（pid+uuid）+ `flush`/`fsync` + `os.replace`；失败不删原文件、临时文件必清。

### 1.3 改哪几个函数

| 文件 | 函数 | 改法 |
|---|---|---|
| `mbdsdr_ai/config.py` | 新增 `ConfigStore`、`update_config(mutator)` | 吸收现有 `save_config` 的合并逻辑，补锁与 fsync |
| `mbdsdr_ai/config.py` | `save_config` | 变薄：`ConfigStore.update(lambda d: d.update(config.to_dict()))`，签名与返回值不变 |
| `desktop/ai_panel.py` | `_load_config` / `_save_config` | 保存只提交"本面板拥有的键"（api_key/base_url/model/…）差值集合，不再整份覆盖 |
| `desktop/settings_panel.py` | `_store_value` 的 ai_config 分支 | 改为 `ConfigStore.update(lambda d: d.__setitem__(file_key, value))`，禁止用 `self._ai` 整份写回 |
| `desktop/settings_panel.py` | `_refresh_from_storage` | ai 段改为现读现显，不再缓存整份 dict 作为写回源 |
| `desktop/settings_panel.py` | `import_from` 的 AI 落盘 | 同上，只提交导入涉及的键 |

### 1.4 `api_key` 被旧快照回滚，靠什么保证不再发生

四道闸，缺一不可：

1. **写前重读**：`update()` 在锁内重新读磁盘，合并基线永远是磁盘而不是构造期快照 → 旧快照在结构上不存在。
2. **只提交自己的键**：设置面板的 mutator 只写 `temperature` 等 5 个键，物理上碰不到 `api_key`。
3. **锁**：读→合并→替换在同一把跨进程锁内，杜绝"两个写者都基于同一旧值"。
4. **冲突可见**：若锁内发现磁盘值在"本进程上次基线"之后被改过且与本方写入同键，记 `logger.warning` + 面板状态行提示（沿用 [settings.py](file:///d:/mbdsdr/MBDSDR/desktop/settings.py#L621-L637) 的 `changed_by_other` 判定），不再静默取胜。

### 1.5 反证设计

- `test_config_store_api_key_survives_settings_panel_write`：写 api_key → 设置面板构造 → 改 temperature → 断言磁盘 api_key 仍在。
- 变异反证：把 `_store_value` 改回 `self._ai` 整份写 → 该测试必须红。
- `test_config_store_atomic_on_write_failure`：注入 `os.replace` 抛错，断言原文件字节不变、无 `.tmp` 残留。
- `test_config_store_two_writers_different_keys`：两个 store 实例各写一个键，断言两个键都在（模拟 mbdsdr_ai + 面板）。

## 2. 导入语义

### 2.1 现状

[config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L833-L838) 在文件缺键时按 `ALL_ITEMS` 全量补默认，并把补齐项塞进 `accepted`；[import_from](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L1261-L1283) 照单全收。实测：只含 `theme` 的文件 → `filled=52`，频率回 98 MHz、观测站清空、5 个 AI 参数归零。

### 2.2 两种目标语义的后果（需产品决定）

| 语义 | 行为 | 后果 | 适用场景 |
|---|---|---|---|
| **A. 合并（推荐）** | 只写文件里出现的键；缺键保持现值 | 恢复备份/跨机迁移安全；无法"一键还原到某份快照" | 日常导入 |
| B. 替换 | 文件视为完整快照，缺键回默认 | 可精确复刻一台机器的配置；但任何手写/残缺文件都会造成大面积重置 | 需要"整机克隆"时显式选择 |

裁定：**默认 A**。若产品确实需要 B，必须满足两个硬条件：① 导入前差异预览列出「将改 N 项 / 清空 M 项」；② 用户显式勾选「按完整快照还原（缺省项回默认）」。

### 2.3 目标语义（合并模式的硬约束）

1. **缺键 ≠ 改默认**：`parse_import` 不再把缺键塞进 `accepted`；缺键只进 `absent`（新字段），不参与写盘。
2. **预览先行**：`parse_import` 产出 `changes: [(key, old, new)]`、`clears: [(key, old)]`、`warnings`；面板弹窗展示前 20 条 + 总数，确认后才写。
3. **清空必须单列**：`None`（清观测站）与"回默认"分开计数，不能笼统写"已补默认 N 项"。
4. **版本与标识**：`app` 不匹配直接拒绝；`schema_version` 主版本不一致拒绝（次版本按合并规则处理）。

### 2.4 改哪几个函数

| 函数 | 改动 |
|---|---|
| `config_schema.parse_import` | 去掉缺键补默认；新增 `absent` / `changes` / `clears`；校验 `app` |
| `config_schema.ImportResult` | 增字段 `absent`、`changes`、`clears`；`summary()` 改写为「将修改 N 项、清空 M 项、忽略 K 项」 |
| `settings_panel.import_from` | 先 `parse_import` → 返回预览 → 二次确认后按 `accepted` 写；写 settings 后立即 `flush` 再写 AI/gui |
| `settings_panel.export_to` | 只导出 `exposed=True` 的键 + 运行态是否导出列入产品决定（见 gap S-14） |

### 2.5 反证设计

- `test_import_theme_only_keeps_frequency_and_site`：导入只含 theme 的文件 → 断言 `frequency_hz`、`observer_lat/lon`、5 个 AI 参数**逐一不变**；观测站不为 None。
- `test_import_preview_counts_before_write`：预览返回「将修改 1 项、清空 0 项」，且此时磁盘未变。
- 变异反证：把缺键补默认加回去 → 第一条必须红。
- `test_import_rejects_foreign_app`：`app="Other"` → 返回 error，磁盘零变化。

## 3. `reset_all` 与运行态同步

### 3.1 现状

[settings.py](file:///d:/mbdsdr/MBDSDR/desktop/settings.py#L527-L539) 对**全部** `DEFAULTS` 重置（46 个 settings 键）；[settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L1202-L1242) 只对 `EXPOSED_ITEMS` 调 `_apply`。实测分叉 4 项：`frequency_hz 98↔145.5 MHz`、`volume 48↔77`、`demod_mode FM↔USB`、`bandwidth 0↔12.5 kHz`。

### 3.2 两种选择（必须二选一，本文选「不改运行态」）

| 选择 | 要做的事 | 后果 | 风险 |
|---|---|---|---|
| **A. 不改运行态（推荐）** | `reset_all` 只重置 `EXPOSED_ITEMS` 中 `storage=="settings"` 的键；内部键保留 | 用户点"恢复默认"不会跳频/跳音量；与按钮文案一致 | 低；只改重置范围 |
| B. 改运行态并同步 | 10 个内部键补齐 `_apply`（频率/增益/解调/带宽/采样率/AGC/音量/app_mode），并处理录音中/守听中 | 配置与界面始终一致 | 高：需设备在线、需停录音确认、需重下发采样率 |

选 A 的具体约束：

- ~~`DesktopSettings.reset_all(keys: Iterable[str] | None = None)`：`None` 保持旧语义（测试/工具用），面板传显式白名单。~~
  **实现选择删除该入口（`905d5483`），理由是带参签名等于留后门**：留一个 `None = 全量重置` 的旁路，下一个人（或一次脚本调用）就能绕过面板白名单把运行态一起改掉；现在面板逐项 `reset(key)`，根本不调 `reset_all`。
- 面板白名单 = `[i.key for i in EXPOSED_ITEMS if i.storage == "settings"]`（35 项），不含 9 个运行态键与迁移标志 `volume_scale_v2`；`record_dir_inside_program_ack` 在名单内。
- 对话框文案改为「将恢复 35 项界面/偏好设置；当前频点、音量、解调模式保持不变」。
- 状态行沿用 `abc52bd8` 的行为断言「已恢复本组默认值」口径，不动其断言对象。

### 3.3 反证设计

- `test_reset_all_keeps_runtime_keys`：设 freq/volume/mode/bandwidth 各一个非默认值 → `reset_all` 后断言四者**配置与现场都不变**，且 35 项可见项已回默认。
- 变异反证：把白名单改回 `DEFAULTS` → 该测试必须红。
- `test_reset_all_single_apply_entry`：对 35 项逐个断言 `_apply` 被调用（或消费点状态等于默认），防止"配置回默认但界面没回"的反向分叉。
- 保留既有 `abc52bd8` 的「已恢复本组默认值」测试不动。

## 4. 启动回推

### 4.1 现状与 1024 的来源

- [settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L524-L535) `_apply_initial_state` 只手写 5 个键（bortle / squelch / step / depth_rows / extinction），9 个频谱项不在其中。
- `spectrum_widget` 全文不读 settings（只有注释），启动用 `_PERF_LEVELS` 的档位值。
- 实测重启后 `fft_size=1024` 的来源链：
  [perf_profile.py](file:///d:/mbdsdr/MBDSDR/desktop/perf_profile.py#L40-L72) `DEFAULT_PROFILE="eco"` → `panel_level=2`
  → [spectrum_widget.py](file:///d:/mbdsdr/MBDSDR/desktop/spectrum_widget.py#L2895-L2904) `_PERF_LEVELS[2]=(1024, 256, 100)`
  → [main_window.py](file:///d:/mbdsdr/MBDSDR/desktop/main_window.py#L4192-L4204) `set_performance_mode(2)` → `generator.reconfigure(1024, …)`。

所以 1024 **不是第三处孤立硬编码**，而是"性能档"这条第二权威链把用户保存的 `fft_size` 覆盖了。

### 4.2 目标：单一真值源

裁定：**`fft_size` 是 FFT 点数的唯一真值源；性能档降级为"预设宏"**。

- 档位只拥有：刷新间隔、谱线数、GNSS 轮询、下次连接的采样率。**不再拥有 fft_size**。
- 切档时把该档的建议 FFT **写进** `fft_size`（一次宏动作），之后两者不再平行。
- 用户手改 `fft_size` 后，性能档下拉显示「自定义」或在提示里注明"FFT 已被单独调整"（是否需要新增 `custom` 档 → 产品决定，默认只需提示）。
- `_PERF_LEVELS` 的 `fft_size` 列保留为"建议值"数据，但 `set_performance_mode` 不再用它 `reconfigure`；由 `_apply_fft_size` 统一调用 `generator.reconfigure`。
- 删除第三处权威：`main_window.py` 性能档应用不再覆盖 FFT；`perf_profile.PROFILES[*]["fft_size"]` 的镜像关系保留但仅作展示，测试改为断言"切档后 settings.fft_size == 建议值"。

### 4.3 启动路径

把 `_apply_initial_state` 从"手写 5 键"改为"**遍历 `EXPOSED_ITEMS` 且 `apply_mode == immediate`**"，按固定顺序执行：

1. 存储/日志（无 UI 依赖）
2. 性能档（只设刷新/GNSS/采样率默认，不碰 FFT）
3. `fft_size` / `fft_window` / `fft_avg_frames` / `waterfall_palette` / `peak_threshold_db` / `show_*` / `waterfall_smooth`
4. 其余（静噪/步进/深度/消光/外观/卫星）

要求：**顺序由数据声明**（`SettingItem.apply_order`），不再靠代码里手写元组；新增项忘记加入 → 遍历覆盖，天然不会漏。

### 4.4 反证设计

- `test_startup_restores_all_immediate_items`（子进程两段式，复用本次复现脚本思路）：进程 A 改 9 项 → 进程 B 读真实消费点；断言 `generator.fft_size / window_name / avg_frames`、`_state.wf_colors / peak_rel_db / show_*` 全部等于配置值。
- 变异反证：把 `fft_size` 从遍历里摘掉 → 第一条必须红（回到 1024）。
- `test_perf_profile_does_not_override_fft`：设 `fft_size=4096`、切到 eco 档 → 断言 `generator.fft_size` 仍为 4096（或按产品决定被宏改写的唯一一种结果）。
- `test_no_hardcoded_fft_default_outside_schema`：grep/AST 断言 `_PERF_LEVELS` 的 fft 列只被"写 settings"，不被 `reconfigure` 直接消费。

## 5. 延迟生效标注（可机检）

### 5.1 字段设计

在 `SettingItem`（[config_schema.py](file:///d:/mbdsdr/MBDSDR/desktop/config_schema.py#L97-L124)）新增：

```python
apply_mode: str = "immediate"   # immediate | reconnect | restart_service | restart_app | next_session
consumer: str = ""              # 真实消费点 "path:line" 或 "path:symbol"，机检用
```

六项填法：

| 项 | `apply_mode` | `consumer` 指向 |
|---|---|---|
| `rc_port` | `restart_service` | `desktop/settings_panel.py:303`（`RemoteControl(...)` 构造时绑定） |
| `web_port` | `restart_service` | `desktop/settings_panel.py:348`（`WebServer(...)` 构造时绑定，实现在 `mbdsdr_ai/web/server.py`） |
| `gnss_port` | `reconnect` | `desktop/main_window.py:12410` `_auto_connect_gnss_async` |
| `gnss_baudrate` | `reconnect` | 同上 |
| `perf_profile`（采样率部分） | `reconnect` | `main_window` 连接时下发采样率处（`fft_size` 拆出后本条只剩采样率/GNSS） |
| `record_dir` | `next_session` | `desktop/main_window.py:9474` `_confirm_record_dir_safe` |

`requires_restart` 保留为兼容别名：`apply_mode in {"restart_app"}` 时由 schema 派生，避免两套字段打架。

### 5.2 面板自动渲染

- [settings_panel.py](file:///d:/mbdsdr/MBDSDR/desktop/settings_panel.py#L766-L773) `_build_row`：橙字不再只看 `requires_restart`，改读 `apply_mode`，文案表：
  `reconnect → "下次连接设备后生效"`、`restart_service → "重启该服务后生效"`、
  `restart_app → "重启应用后生效"`、`next_session → "下次开始录音/新会话后生效"`。
- 分组标题旁加一个汇总角标（"本组 2 项需重连"），解决"橙字埋在行里看不到"。

### 5.3 机检测试

新增 `tests/test_settings_apply_mode.py`：

1. **全表遍历**：任何 `apply_mode != "immediate"` 的项，`consumer` 必须非空，且该 `path` 存在、`symbol` 能在文件里检索到（AST 查函数/方法名，不做源码文本匹配）。
2. **反向遍历**：`apply_mode == "immediate"` 的项必须出现在启动回推遍历里（即 `_apply_initial_state` 覆盖集合 == immediate 集合），防止"标了即时其实没消费点"。
3. **消费点真实性**：对 `reconnect` 项，构造连接事件后断言设置值被读取（fake backend 记录参数）。
4. **渲染一致性**：面板对每个 `apply_mode != "immediate"` 的项必须渲染非空橙字，且文案与枚举一一对应。

反证：把 `record_dir` 改成 `immediate` 但不补启动回推 → 第 2 条必须红；把 `consumer` 指向不存在的文件 → 第 1 条必须红。

## 6. `0xC0000005` 退出崩溃：一句判断

**判断：归因不成立，待补原始复现条件。** 现有证据只支持「强制结束进程 / faulthandler 转储期间会崩」这一已知家族，**不支持**「产品正常退出路径有 bug」。`conftest.py` 是规避不是修复——这点不变。

> 本节的脚本、命令、次数、退出码与结论修正已**搬进已入库案卷**
> [2026-11-11-case-0xc0000005.md](file:///d:/mbdsdr/MBDSDR/docs/pending-patches/2026-11-11-case-0xc0000005.md)
> §2 与 §2.1。此处不再重复，避免两份事实源分叉；引用请以案卷为准。


## 7. 落地顺序与影响面

| 顺序 | 批次 | 涉及文件 | 可独立回滚 |
|---|---|---|---|
| 1 | `apply_mode`/`consumer` 字段 + 面板渲染 + 机检测试 | `config_schema.py`、`settings_panel.py`、`tests/test_settings_apply_mode.py` | 是 |
| 2 | 启动回推遍历化 + 性能档让出 FFT | `settings_panel.py`、`main_window.py`、`perf_profile.py`、`spectrum_widget.py` | 是 |
| 3 | `reset_all` 范围收敛 | `settings.py`、`settings_panel.py` | 是 |
| 4 | 导入合并 + 预览 | `config_schema.py`、`settings_panel.py` | 是 |
| 5 | `ConfigStore` 统一 config.json 写入 | `mbdsdr_ai/config.py`、`ai_panel.py`、`settings_panel.py` | 是（保留旧函数签名） |

依赖关系：1 是 2 的前置（启动回推依赖 `apply_mode` 集合）；3、4、5 互不依赖。全程不改导出格式版本号，导入兼容按 `schema_version` 次版本合并。

## 8. 反证清单（每条改动必须配套）

| 改动 | 反证（改回旧行为必红） |
|---|---|
| 统一写入通道 | 两写者各写一键 → 两键都在；api_key 不被 temperature 写入顶掉 |
| 导入合并 | 只含 theme 的导入 → 频率/站址/AI 参数逐一不变 |
| `reset_all` 范围 | 重置后运行态四键配置与现场都不变 |
| 启动回推 | 重启后 9 个频谱项真实消费点等于配置值（非面板值） |
| `apply_mode` | 标 delayed 的项无 consumer → 红；immediate 项不在启动遍历 → 红 |
