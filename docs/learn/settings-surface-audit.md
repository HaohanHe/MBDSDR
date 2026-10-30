# 设置面前后端对拍审计（settings surface audit）

日期：2026-10-30 · 范围：desktop/（Python/PySide6 原型桌面端）+ cpp/src/（C++/Qt6 主力端）
方法：控件创建 / signal.connect / 槽定义脚本化扫描 + 逐条肉眼复核；`set_*` 工具面与 UI 控件双向对拍。
纪律：不编造缺陷；核实不成立的项**如实记入"不成立"**，不留假缺口；C++ 侧无工具链，只审计不改。

## 用户原话（问题定义）

> "很多就是有前端没后端，有后端没前端，完了设置他们不符合人类很多"

三类问题：A 有前端没后端（点了没反应）· B 有后端没前端（能力不可达）· C 设置反人类（改了不落盘 / 语义看不懂 / 重启回退）。

## 一、已修（Python 桌面端，全部已提交并测试钉住）

| # | 缺陷 | 类 | 修复 | 提交 |
|---|---|---|---|---|
| 1 | 服务开关（远程控制 TCP / Web）改了**不落盘**，重启回到默认关闭 | C | rc/web 的 enabled+port 四键进 `settings.DEFAULTS`；端口归 `_INT_KEYS`（不登记会被 `_coerce` 落成 str，下游 `int()` 再炸）；面板接 `DesktopSettings`，toggled/valueChanged 持久化；`restore_enabled_services()` 由主窗口在 UI 就绪后按用户上次显式意图恢复（**默认全关红线不变**） | `15ad860` |
| 2 | `theme` / `gnss_port` / `gnss_baudrate` 在 settings 与 gui_config **双写双读**：GNSS 对话框只写 gui_config，settings 那份永远停在默认值 → 改了重启读回旧值 | C | 权威源统一为 settings；`_save_gui_config` 不再写这三键；`_load_gui_config` 按"读旧键 → 迁移 → 回写 settings"一次性迁移（幂等，仓库既有范式）；GNSS 对话框改完写 settings | `15ad860` |
| 3 | `touch_mode` 三态（auto/on/off）登记在 settings、`_on_touch_mode_changed` 也预留了适配路径，却**没有任何 UI 入口**，用户只能手改 JSON 文件 | B | 视图菜单加「触屏模式」（自动检测 / 强制触屏 / 强制鼠标）+ `_set_touch_mode` 落 settings 并重新适配 | `15ad860` |
| 4 | `module_panel.bw_spin`（带宽旋钮）的值从未进连接载荷（dev 只有 `_sample_rate`/`_gain`），`_connect_backend` 也不接受 bandwidth → 用户调带宽**被静默丢弃**，连接永远走后端默认（0=自动） | C | 三环补全：`dev["_bandwidth"]` 进载荷 → `_connect_backend(bandwidth=...)` → `backend.set_bandwidth()`（失败仅状态栏警告，不阻断连接；0=自动与后端 `bandwidth_hz` 语义一致） | `14c7ed0` |
| 5 | `settings_panel` 两个端口槽（`_on_rc_port_changed` / `_on_web_port_changed`）**写了没 connect**（正是本审计的 A 类死槽） | A | 补 `valueChanged.connect` | `15ad860` |

同轮（`00c677b`，UI 接线审计子任务）：`module_panel` 采样率/增益控件改了不进连接载荷、`control_panel` 删书签不落盘，已修并钉住。

测试锚点：`tests/test_settings_convergence.py`（11 项）、`tests/test_ui_wiring_audit.py`（含死控件回归门禁）。

## 二、核实**不成立**的项（曾疑为缺口，查证后排除）

这几条初判是"有工具没 UI"，逐条查证后**不成立**——记在这里以免后来者重复误判：

| 曾疑缺口 | 核实结果 | 证据 |
|---|---|---|
| `set_color_map` 无 UI | **有入口**，走色图文件选择/持久化路径 | `cpp/src/ui/main_window.cpp:5992`（set_color_map 路径生效）、`spectrum_display.cpp:195`（启动重应用外部色图） |
| `set_fft_params` 无 UI | **有入口**，FFT 点数经信号链 | `main_window.cpp:2902` `connect(spectrum_, &ui::SpectrumWidget::fftSizeRequested, ...)` |
| `set_noise_blanker` 无 UI | **有入口** | `main_window.cpp:978` `connect(engine_, &dsp::SpectrumEngine::setNoiseBlanker)` |
| C++ 增益任意浮点（离散档位缺失）= 反人类 | **已做对**：滑杆带范围（负增益=RF 衰减，SDR++ 风格）+ 离散档位下拉（仅在驱动报合法档位时显示） | `main_window.cpp:639-651` |
| `_on_anr_strength` 是空槽 = ANR 强度改了没反应 | **误判**：面板内部直接写 `self._anr.cfg.alpha = alpha`（`anr_panel.py:146`），强度生效；主窗口槽只是冗余通知 | `14c7ed0` 已把台账改为钉住真实生效路径 |

## 三、尚未闭环（如实登记，不声称已修）

1. **C++ 侧工具面 ↔ UI 全量对拍未做完**。已提取 16 个 `set_*` 工具（`set_bandwidth`/`set_cdcss`/`set_color_map`/`set_ctcss`/`set_doppler_compensation`/`set_fft_params`/`set_lrpt`/`set_mode`/`set_network_audio_sink`/`set_noise_blanker`/`set_squelch`/`set_vfo_armed`/`set_vfo_bandwidth`/`set_vfo_frequency`/`set_vfo_mode`/`set_vna_sweep`）并逐个在 `cpp/src/ui/` 核对，本轮抽查的项均找到入口（见上表）；但**反向**（UI 有控件、工具面无对应）与**语义反人类**（范围/默认值/单位与硬件能力不一致）的全量清单未完成。
2. **本机无 Qt6/CMake 工具链，C++ 侧改动无法编译验证**。本轮未改 C++ 源码；上表 C++ 证据均为只读审计结论。
3. **两套带宽语义并存**：`settings.bandwidth_hz`（VFO 带宽，0=随模式自动）与 `module_panel.bw_spin`（硬件带宽，100–250000 Hz，不含 0）语义不同、UI 上无区分说明——用户看到两个"带宽"会困惑。**已销账**（`4cd0881`）：两处命名区分为「硬件带宽 / VFO 带宽」+ 互指 tooltip；硬件带宽 0=自动改为显式「自动」档。
4. **三套配置键漂移**：`current_freq_mhz`/`current_mode`/音量/瀑布在 gui_config 与 settings 双写双读（gui_config 启动 100ms 后恢复会覆盖 settings 值）。**已销账**（`d12910e`）：四键单源化到 settings（`current_freq_mhz` 是 MHz，迁移时 ×1e6 转 Hz），gui_config 不再写，旧值一次性迁移回写（幂等），音量/瀑布补 settings.set 写路径。另修卫星跟踪调谐的控制面板显示死路径（写不存在的 `freq_spin` 被 except pass 吞掉，改走 `set_frequency_hz`）。

## 四、第二轮（同日续，三路并行；下列五~八为 desktop 语义线详账）

| 线 | 产出 | 提交 |
|---|---|---|
| C++ 反向对拍（UI 有控件 → 工具面无对应） | **D1 有 UI 无工具 45 条**（增益/采样率工具面为零、解调模式三层错位 15/12/6 致 BPSK/QPSK/ADS-B 工具不可达、fft_size 假成功、AI 连不上 VNA、发射类零确认）、D2 语义不一致 9 条、D3 反人类 8 条。全量清单见 `docs/learn/cpp-tool-ui-reverse-audit.md` | `81f8d4d` |
| desktop 语义反人类（E1 魔法值 / E2 单位 / E3 范围 / E4 文案 / E5 失败提示） | 23 处已修（含新坐实：枚举错误被"未发现设备"空态覆盖、卫星下行频率上限卡死仓库自己的 LRO 2271 MHz）；10 文件 32 处 `{e}`/traceback 泄漏走 clean_exc；只报未修 5 条（含增益范围硬编码需设备动态档位） | `4cd0881` |
| 配置键漂移收敛 + 死路径 | 四键单源化（`d12910e`，见上文第 4 条） | `d12910e` |

诚实性纪律执行情况：三路均带"核实不成立"分账（合计 10 条），未把做对的说成缺陷；断言随修复更新（`test_gui_config_honesty` 两条契约变更）而非放宽换绿。

用户指出的三类问题全部坐实且已有实修：反人类的"改了不落盘"两处、"有后端没前端"一处、"有前端没后端"两处（含一处我自己犯的死槽）。C++ 端抽查项多为做对的（增益档位、色图/FFT/噪声消除入口），**不能因"要找问题"而把做对的说成缺陷**。剩余缺口如实列在第三节，未闭环的不声称已闭环。

---

# 第二轮：「设置语义反人类」批量排查（E1–E5，2026-10-30）

范围：`desktop/*.py`（禁改 settings.py / settings_panel.py / main_window.py /
test_settings_convergence.py）。方法：逐面板读码，按五病分类取证（文件:行），
修后用 `tests/test_settings_semantics.py`（21 项，offscreen）钉住。
纪律同前：不编造缺陷；核实不成立如实记账；高风险只报不改。
本轮新增一处**排查中坐实的真缺陷**（E3-d，见下）。

## 五、已修（三账之一：已修）

### E1 魔法值无说明（3 条）

| # | 证据 | 问题 | 修复 |
|---|---|---|---|
| E1-a | `module_panel.py:280`（修前） | `bw_spin` 值域 100–250000，后端"0=自动"语义（`module_panel.py:366` 注释、`bandwidth_hz` 约定）在 UI 无法表达，也没人告诉用户 0 是什么 | 值域含 0 + `setSpecialValueText("自动")`（0 档显式显示「自动」）+ tooltip 写明"0=自动"并区分 VFO 带宽 |
| E1-b | `control_panel.py:289`（修前） | `freq_input` 裸数字语义（FM/WFM/未选→MHz、AM→kHz、>10000→Hz，`control_panel.py:583-590`）只活在代码里 | 占位符改示例「98.5M / 145.5MHz / 7050k」+ tooltip 写清解析规则与可调范围 |
| E1-c | `control_panel.py:385`、`anr_panel.py:92`（修前） | 音量 0–63 刻度（下游 `volume/63×2.0`，main_window）与 ANR 强度 0–100→α0–10（`anr_panel.py:143-145`）无说明，标签裸显 "2.0" | 两处 tooltip 写明映射；强度标签改「α 2.0」 |

### E2 单位/量纲缺失（2 条）

| # | 证据 | 问题 | 修复 |
|---|---|---|---|
| E2-a | `vfo_panel.py:52-55`（修前） | `_fmt_bw_hz` 裸显 "12.5k"/"500"，同文件频率列却带 MHz/kHz/Hz | 统一带单位「12.5 kHz」「500 Hz」 |
| E2-b | `control_panel.py:523,775`（修前） | 静噪值裸显 "-80"（信号语义 dBFS 见 `:119`） | 显示改「-80 dB」 |

### E3 范围与能力不符（4 条，含 1 条排查中新坐实）

| # | 证据 | 问题 | 修复 |
|---|---|---|---|
| E3-a | `device_panel.py:254-266`（修前） | 采样率/增益/PPM 解析失败**静默改写**成默认值（2.048M/30/0）照连不误——用户以为连的是自己填的参数 | 解析失败/非正采样率/超出所选设备 `sample_rate_range`/`gain_range`（枚举能力，`sdr_backend.py:2269`）一律拒绝连接并弹诚实提示（含范围数字），**不改写用户值** |
| E3-b | `sat_track_panel.py:83`（修前） | 下行频率 100–2000 MHz 卡死合法值：仓库自己的 LRO 目标 2271 MHz（`doppler_panel.py:365`）、AO-40 S 波段 2401 MHz 都进不去 | 放宽到 0.01–6000 MHz（对齐 scanner_panel 同参数）+ tooltip 声明实际以设备为准 |
| E3-c | `module_panel.py:252`（修前） | 频率上限 7.2 GHz 是 SoapySDR 无范围兜底值（`sdr_backend.py:2201`），超过所有在列后端 | 对齐项目范围 10 kHz–6 GHz，常量收口 `constants.py`（`FREQ_MIN_HZ/FREQ_MAX_HZ`，control_panel 继续 re-export）+ tooltip |
| E3-d（新） | `module_panel.py:305-316`（修前） | 枚举异常先 `setText(错误)`，随后又被"未发现SDR设备"空态文案**覆盖**——失败被吞，"枚举坏了"与"没插设备"混为一谈 | 错误态与空态分开显示：「设备枚举失败: …（这不等于没插设备）」，且错误文本经 clean_exc 整形 |

### E4 命名/文案反直觉（4 条）

| # | 证据 | 问题 | 修复 |
|---|---|---|---|
| E4-a | `module_panel.py:284` / `control_panel.py:438`（修前） | 两套「带宽」同名不同义（硬件射频带宽 vs VFO 信道带宽），即上轮残留项 3 | 分别改「硬件带宽」「VFO 带宽」，tooltip 互相点名区分 |
| E4-b | `control_panel.py:527`（修前） | 静噪自动开关英文「Auto」 | 「自动」+ tooltip |
| E4-c | `control_panel.py:496`（修前） | 「Offset 调谐」混排翻译腔 | 「偏移调谐」+ tooltip（写明 Offset Tuning 是什么） |
| E4-d | `ntrip_panel.py:150-161`（修前） | 行标「Caster Host:/Port:/Mountpoint:」裸英文 | 「Caster 主机:/端口:/挂载点:」+ 术语 tooltip（Caster=差分数据服务器、Mountpoint=数据流名） |

### E5 失败提示不说人话（10 文件 32 处）

统一走 `mbdsdr_ai.tool_registry.clean_exc`（phase69 战役先例）整形；
中文消息原样透传（既有测试钉住的「工具调用失败: 炸了」等格式不受影响）。
traceback 堆栈一律不再进 UI（ai_panel/weather_panel/doppler_panel/mcp_worker）。

| 文件 | 修前位置（修前行号） | 泄漏形态 |
|---|---|---|
| `module_panel.py` | 316 | `枚举异常: {e}`（并被空态覆盖，见 E3-d） |
| `scanner_panel.py` | 142 | `扫频失败: {e}` |
| `ntrip_panel.py` | 268, 270 | `网络错误: {e}` / `未知错误: {e}` |
| `bookmark_panel.py` | 144, 254, 268, 283, 327, 344 | `str(e)` 直进弹窗/状态行 |
| `doppler_panel.py` | 85, 485, 651 | traceback 全文 / `{e}` |
| `modulation_panel.py` | 79, 89 | `读取 IQ 失败: {e}` / `识别失败: {e}` |
| `satellite_image_panel.py` | 188, 205 | `解码失败: {e}` / `保存 PNG 失败: {e}` |
| `ai_panel.py` | 71 | `{type}: {e}\n{traceback[:500]}` 堆栈进对话区 |
| `weather_panel.py` | 218, 707 | traceback / `后处理失败: {e}` |
| `mcp_worker.py` | 137-139, 161, 180, 205, 213-214, 239-240, 254, 268 | `{e}` / `traceback.format_exc()` 进日志与错误信号 |

测试锚点：`tests/test_settings_semantics.py`（21 项：tooltip/占位、单位后缀、
越界拒绝且提示、命名区分、失败消息无英文/无堆栈）；既有
`test_ui_wiring_audit`/`test_ui_vfo_constellation`/`test_ntrip_panel`/
`test_mcp_worker_behavior`/`test_settings_convergence`/`test_clean_exc`/
`test_desktop_ui_responsiveness`/`test_ui_stability_resources`/
`test_orphan_connection` 等全量回归绿（不跑 test_user_layouts.py，已知 Qt 挂死）。

## 六、核实**不成立**的项（三账之二：不成立）

| 曾疑缺口 | 核实结果 | 证据 |
|---|---|---|
| device_panel 采样率/增益/PPM 缺单位说明（E2） | **不成立**：label 与 tooltip 均带单位 | `device_panel.py:110-120`（"采样率(Hz)"+"采样率 Hz" 等） |
| scanner_panel 四个旋钮缺单位（E2） | **不成立**：全部有 suffix | `scanner_panel.py:186-205`（MHz/MHz/MHz/dB） |
| control_panel 增益标签 "20dB" 缺空格（E2） | **不成立**（单位在，格式被测试钉住不动） | `test_ui_stability_resources.py:305` 精确断言 `"20dB"` |
| spectrum 峰值阈值旋钮缺单位（E2） | **不成立**：同行标签「阈值dB:」带单位 | `spectrum_widget.py:1074-1080` |
| freq_input 解析失败变红无提示（E5） | **不成立**：变红+回退是既有钉住行为，语义已由 tooltip/占位说明（E1-b 修复）覆盖 | `test_desktop_ui_responsiveness.py:84-86` |

## 七、只报未修 / 未闭环（三账之三）

1. **增益范围硬编码**（`module_panel.py` 滑杆 0–50、`control_panel.py` 滑杆 0–49
   注释"RTL-SDR 典型"）：真修要按所连设备动态取档位（`gain_panel.py` 已示范
   从 GainStager 查范围），但接线在 main_window（本轮禁改）。已补 tooltip
   诚实声明"实际档位以设备为准，后端钳位"。**只报**。
2. **音量刻度 0–63 本身反直觉**（为何 63 不是 100？）：重映射波及 gui_config
   `volume` 键、工具栏 0–100→0–63 映射（main_window，禁改）与 MCP `set_volume`
   工具契约。已补 tooltip 说明刻度与增益换算。**只报**。
3. **module_panel 频率旋钮用 Hz 显示大数字**（7 位数不友好）：改 MHz 会改变
   `tune_requested` 信号量纲，消费方在 main_window（禁改）。**只报**。
4. **main_window.py:5484** `self.control_panel.freq_spin.setValue(freq_mhz)`
   引用不存在的控件（control_panel 只有 `freq_input`），被 try/except pass 吞掉
   ——天空/卫星点击→控制面板频率显示这步从未生效（A 类死路径）。main_window
   禁改，**只报**。
5. **clean_exc 对 Windows 错误串无映射**：`[WinError 10061]/URLError` 包裹串
   不命中 `_EN_MSG_MAP` 的 `[Errno 111] Connection refused` 模式，Windows 上
   网络类失败会显示「计算错误: <urlopen error …>」而非「连接被拒绝」。属内核
   映射表扩展（`tool_registry.py`），本轮测试用确定性超时串（`^timed out$` 有
   映射）钉住行为。**未闭环**。
6. 三套配置键漂移（上轮残留 4）、C++ 侧工具面对拍（上轮残留 1/2）：本轮范围外，
   维持未闭环。

## 八、第二轮结论

"设置不符合人类"的语义五病在 Python 桌面端全部过了一遍筛：E1 三条、E2 两条、
E3 四条（含排查中新坐实的"失败被空态覆盖"一条）、E4 四条已修并钉住；E5 的
英文异常/堆栈泄漏 10 文件 32 处统一收口到 clean_exc。核实不成立的五条如实记账，
**不为凑数把做对的说成缺陷**；六条只报/未闭环均给出原因与证据，不声称已修。
