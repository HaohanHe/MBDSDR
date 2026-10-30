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
3. **两套带宽语义并存**：`settings.bandwidth_hz`（VFO 带宽，0=随模式自动）与 `module_panel.bw_spin`（硬件带宽，100–250000 Hz，不含 0）语义不同、UI 上无区分说明——用户看到两个"带宽"会困惑。属于"设置不符合人类"的残留项，待统一命名或补说明。
4. **三套配置**（`desktop_settings.json` / `~/.mbdsdr/gui_config.json` / C++ QSettings）键集合仍有漂移：`current_freq_mhz`/`current_mode`/音量/瀑布等仍在 gui_config，与 `frequency_hz`/`demod_mode` 语义重叠但不同名。本轮只收敛了 theme/gnss 三键，其余待做一次性键迁移。

## 四、结论

用户指出的三类问题全部坐实且已有实修：反人类的"改了不落盘"两处、"有后端没前端"一处、"有前端没后端"两处（含一处我自己犯的死槽）。C++ 端抽查项多为做对的（增益档位、色图/FFT/噪声消除入口），**不能因"要找问题"而把做对的说成缺陷**。剩余缺口如实列在第三节，未闭环的不声称已闭环。
