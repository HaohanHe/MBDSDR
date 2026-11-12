# 冗余登记表（Redundancy Register）

> 范围：`D:\mbdsdr\MBDSDR`（Python/PyQt6 桌面 + C++/Qt 内核 + Flutter 移动端）
> 建表基线：`HEAD 5915277c`；删除提交 `c14ac51e`、`1c0464b6`；合并提交 `f529ba62`
> 判据：**每条结论必须有调用方证据（全仓命中数）；命中 0 才可称"无调用方"**。
> 本表只负责"是否冗余、如何处置、风险是什么"；**同一物理量的取值裁定仍以
> [`docs/parameter-unification.md`](parameter-unification.md) 为唯一事实源**，两表冲突时以参数账本为准并回改本表。
> 本表为**单一 owner 文件**（冗余线）；其它线路的登记行经 `D:\mbdsdr\.tmp\` 导出后并入此处，不另开第二份清单。

## 0. 结论速览

共登记 **41 项**，分四类：

| 类型 | 项数 | 已处置（删除/合并） | 必须保留 | 待裁定/待修 |
|---|---|---|---|---|
| 重复实现 / 常量复用 | 12 | 2（采样率格式化**已合并** `f529ba62`（取证见 §5.5）；`kDbUpperDefault` 常量复用**已拆** `ef07c133`（四元组见 A12）） | 10（频谱双实现、频率分精度、错误码标签/下一步/常量、ACARS、dBFS/dBµV、路径引导） | 0 |
| 多真值源 | 11 | 1（`module_panel` 5 档改判为能力驱动 `7bea149a`，见 §5.1） | 10（采样率白名单/候选/后端表/AI 参考表/回退/移动端/硬件吸附档/默认值分层/配置路径/工具目录快照） | 0 |
| 死代码 | 12 | 2（4 个函数 `c14ac51e`；2 个纯函数 `1c0464b6`；删除取证见 §4.1/§4.2，C12 见 §6.2） | 9（三录音模块、C++ 同名模块、仅测试模块、未接线方法、触屏控件、两条动态调用误报、测试钉住函数） | 1（46 个 library-only 模块，已三类归档 §5.3） |
| 死分支/死配置 | 6 | 0 | 6（ACARS 死分支、`not_implemented`、零消费常量、全部在用设置键、无死键结论、legacy 别名） | 0 |

> 另：§5.10（2026-11-11）记录新发现的一类**运行时多真值源**——同一源码被裸导入与包导入加载成
> 两个模块对象（29 个模块存在两种形态，6 个带可变模块级状态）。它不改变本表 41 项的计数口径。

**一句话结论**：真正能动的只有 2 组死函数（已删 6 个，见 §4.1/§4.2）、1 组逐字相同的格式化函数（已合并，见 §5.5）和 1 处常量复用（已拆分 `ef07c133`，见 A12）；
其余"看起来重复"的，要么是**两种用途**（采样率白名单 vs 候选探测表），要么是
**两个独立 App 各自的实现**（C++ Qt 与 Python PyQt6 无共享运行时），要么是
**动态调用/泛化读取导致 grep 假阴性**，都不得按"简洁"合并。

---

## 1. 取证方法与边界

### 1.1 已执行的取证

```powershell
# 全仓文本命中（含 desktop / mbdsdr_ai / cpp / mobile / scripts / tests / tools / experiments / docs）
git grep -n --no-color "<symbol>"
rg -c "<symbol>" D:\mbdsdr\MBDSDR
```

- 统计范围显式包含 `scripts/`、`tests/`、`docs/`、`cpp/`、`mobile/`、`tools/`、`experiments/`。
- **数字引用规矩**：任何 `verify_claims` 数字必须带口径 + HEAD 短哈希，两种口径不可混比：
  - `HEAD <hash>（提交态）`：`git worktree add --detach <tmp> <hash>` 取**整仓**快照（文档与代码同一 commit）后跑 `--root <tmp>`；**不得**用「文档取 HEAD、代码取工作区」的混口径——拼起来的结果不可归因到任何一个 commit（旧记法 `git archive HEAD docs` + 工作区代码已废弃）。
  - `HEAD <hash> + 工作区在途（WT）`：直接跑工作区，含他线未提交改动。
  - **口径自证**：提交态与 WT 的差 = 在途文档条数 + 在途代码影响条数。2026-11-06 实测 `HEAD 78003d87`（同一 hash 三跑）：提交态 **138 / 74 / 15 / 49**；混合口径（HEAD 代码 + WT 文档）**140 / 74 / 16 / 50**；纯 WT **140 / 74 / 16 / 50**。混合与纯 WT 逐项相同 → 这 +2 条（+1 文档过期、+1 无 hash）**全部来自在途文档，代码侧贡献 0 条**。
  - 存档（旧混口径，不可归因，只作对照）：`HEAD e4810574（文档 HEAD + 代码工作区）156 / 63 / 12 / 81`；`HEAD e4810574 + WT 128 / 70 / 12 / 46`。**禁止裸报一个总数。**
  - 采样率线的 `164 / 58 / 17 / 89`：**已撤回**（该线自认不可复现）。同口径重跑 `b0a440e3` = **156 / 58 / 12 / 86**，复现式见 `docs/evidence-standard.md` §7.8（`git archive` 快照 + `git show <HEAD>:scripts/verify_claims.py` + 顶层非递归 + `EXCLUDE_FILES={claim-waivers.md}`）。引用他线数字必须带其 hash、范围与复现式，不得与本表两组混比。
- 删除前后各跑同一组测试并对比通过项数（见 §4）。
- 采样率真机事实来自另一条线的实测记录（见 §3）。

### 1.2 明确说"可能漏"的动态调用形态

grep 命中 0 **不等于**没有调用方。本仓库已确认存在以下会漏检的形态，凡涉及都已人工核实：

| 形态 | 实例 | 结论 |
|---|---|---|
| `getattr(self, f"_preset_{suffix}")()` | `desktop/dock_layout_manager.py:225` 拼名调用 `_preset_focus/_preset_analysis/_preset_grid`（`464/478/497`） | 静态零引用，**不是死代码** |
| `__import__(mod, fromlist=...)` + `getattr(obj, attr)` | `mbdsdr_ai/device_capabilities.py:_backend_rate_table` 按 driver 名动态取后端档位表 | 档位表来源不是硬编码 |
| schema 泛化读取 | `item.key[3:]` 剥离 `ai_` 前缀写入 `ai_config`，消费 `ai_max_context_tokens/ai_max_output_tokens/ai_timeout` | 字面量 0 命中，**键在用** |
| Qt `connect` 字符串/`QMetaObject` | 未见字符串形态 connect；槽均以方法引用连接 | 未发现漏检 |
| 字符串拼模块名 / importlib | `mbdsdr_ai` 无 `pkgutil` 自动发现、无 `register_tool_registry` 扫描 | 仅测试模块不会被动态注册 |

> 另：`desktop/module_panel.py` 里 `recorder` 的两处命中（`17`、`327` 行）是
> **sink 下拉框文案**，不是 import，不计作调用方。

---

## 2. 登记表

### A. 重复实现

| # | 冗余项 | 类型 | 证据（位置 + 命中数） | 处置 | 风险 |
|---|---|---|---|---|---|
| A1 | C++ `detectPeaks` 与 Python `find_peaks_with_snr/_detect_peak_bins` 各一套峰值检测 | 重复实现（跨端） | C++ `cpp/src/dsp/peak_detector.cpp:11`，全仓 9 处（生产 1：`cpp/src/ui/spectrum_display.cpp:1094`；测试 2 文件；声明+注释）。Python `desktop/spectrum_widget.py:655`（生产 2 处：`:613 advance_peak_tracks` 帧跟踪、`:1752` 标签生成）、`:697`（`:628` 调用 + 3 测试文件，27 处） | **保留双实现，不合并**。两套定义差异 8 条已由 `docs/parameter-unification.md` D.6/D.7 取证（`:578-599`） | 合并会改用户可见峰值标签：Python 独有宽通道吸收、C++ 独有 9 帧陈旧保持；D.7 实测完整定义链 30/30 一致但显示帧 484/484 陈旧保持——**同一输入两端标签不同** |
| A2 | `find_peaks()` docstring 称"双通道"但实际委托单通道 | 陈旧注释 | `desktop/spectrum_widget.py:682-694`；调用方仅 3 个测试文件（`test_spectrum_scale_peak_range.py`、`test_dsp_perf_optimizations.py`、`test_no_sim_regression.py`），生产 0 | 保留函数，**只登记注释陈旧**（本次不改代码） | 下一个人按注释判断"已有宽通道合并"会误删 `_detect_peak_bins` 的宽通道逻辑，改变可见结果 |
| A3 | `desktop/constants.format_sample_rate` 与 `mbdsdr_ai/sdr_tools._fmt_sample_rate` 逐字相同 | 重复实现（同字面量） | 原位置 `desktop/constants.py:30-45`、`mbdsdr_ai/sdr_tools.py:3057`。全仓 57 处 / 9 文件；测试 `tests/test_copy_units_audit.py:27-34` 与 `:299-304` 两边逐字断言 | **已合并**（`f529ba62`）：唯一实现在 `mbdsdr_ai/sdr_backend.format_sample_rate`，`sdr_tools._fmt_sample_rate` 改为别名，`desktop/constants` 直接复用（`format_sample_rate is sdr_backend.format_sample_rate` 成立） | 低。`desktop/constants` 保留 mbdsdr_ai 缺失时的**安全回退**（与 `DEFAULT_SAMPLE_RATE_HZ` 同一模式），回退副本仅打包兜底，不是第二真值源 |
| A4 | 频率格式化 5 份不同小数位 | 重复实现（不同字面量） | `desktop/status_panel.py:82`（≥1 MHz 6 位 / kHz 3 位）；`desktop/vfo_panel.py:43`（MHz 3 位 / kHz 1 位）；`desktop/rf_sky_view.py:1147`（1 位）；`mbdsdr_ai/device_capabilities.py:68 format_hz`（自适应 2/1/0 位）；`cpp/src/ui/status_format.h:19/26/36`。命名命中 18 处 / 5 文件 | **保留，禁止合并** | 合并会改显示精度：状态栏 6 位是给频偏读数的，VFO 3 位是给调谐盘位的；统一成任一种都会丢精度或产生无意义尾零 |
| A5 | 路径根/`sys.path` 引导重复 | 重复实现（同功能） | `_REPO_ROOT`/`REPO_ROOT=` 70 处 / 52 文件；`sys.path.insert(...parents/dirname)` 103 处 / 100 文件（生产约 13 个：`desktop/main.py`、`main_window.py`、`module_panel.py`、`mcp_worker.py` 等） | **保留**（脚本可独立执行，引导必须就地）；如需收敛，另开"启动引导"专项 | 强行抽公共函数会让脚本在"仓库外被拷走"时全部失效 |
| A6 | 错误码语义源 | 部分收敛（不是单纯冗余） | `mbdsdr_ai/error_catalog.py:698 ERRORS` 是码表/标题/详情/处置的单一源；`desktop/error_remedy.py:38` 派生 `REMEDY_BY_CODE`（`:42`）、`mapping_gaps()`（`:117`）、`overlaps()`（`:125`）；`docs/error-catalog.md` 为镜像，`tests/test_error_catalog_guard.py:225-226` 防漂移 | **保留 error_catalog 为唯一语义源**；但解码原因码的短标签与"下一步"另有两份拷贝（见 A7/A8），原"已完全收敛"判定**过宽，本表改判为部分收敛** | 同一码两处措辞不同：catalog「未知数字模式」vs 状态栏「未知模式（能力表未登记）」 |
| A7 | `desktop/status_panel.py:185 DECODE_ERROR_LABELS` | 多真值源（局部，账本 R8） | 至少 11 条短标签；与 `mbdsdr_ai/error_catalog.py:226-249` 的 `unknown_mode/missing_dependency/missing_sample/unsupported_format/decode_error/not_implemented` 同码不同文案 | **保留**（界面短标签）；建议后续从 catalog 派生短标题，本轮不改 | 新错误码只改 catalog 时，状态栏仍显示旧短文案或退回裸码 |
| A8 | `desktop/troubleshooting.py:249 CODE_NEXT_STEPS` | 多真值源（局部，账本 R8） | 至少 8 条逐码"下一步"；与 catalog 每个码第 4 参数（remedy）在 `insufficient_samples`/`invalid_wav`/`no_frame_recovered` 等重叠 | **保留分层，禁止直接合并** | 同一码两处建议不同步（catalog 说"重录一整帧"，排障页说"≥5s"），用户照哪条都可能失败 |
| A9 | 解码错误码常量 3 套 | 重复定义（账本 R9） | `mbdsdr_ai/decode_router.py:51-61`、`mbdsdr_ai/decode_pipeline.py:54-64`、`mbdsdr_ai/decoders.py:871/1037` 裸串；catalog 另有同一批码 | **保留待合并**（需先确认无外部脚本按名导入 `ERR_*`/`REASON_*`） | 漏改一处会让路由与管线对同一输入判定分裂（一处接受、一处拒绝） |
| A10 | ACARS 双实现（账本 R3） | **重复实现（两实现并存，入口不止一个，退休条件未满足）** | `8213a3d7` — `mbdsdr_ai/decode_router.py:556` — `ACARSDecoder`（基准 `8213a3d7`，该行 blame）（2026-11-11 按 `git blame -L 556,556 --porcelain HEAD` 重定位；原 `:552` 已漂成空行，`82bb5421` 不是该行 blame，作废）。`mbdsdr_ai/acars_protocol.py`（13 命中）与 `mbdsdr_ai/acars_decoder.py`（33 命中）**都活着**：协议实现是验证核心（`mbdsdr_ai/acars_decoder.py:585` 的 `acars_decode_iq` 直接委托它），解码器实现提供 MSK 解调/帧解析/状态机。入口不止一个：`mbdsdr_ai/decoder_registry.py:78`（`decode_func="acars_decode"`，但 `desktop/capability_catalog.py:212` 另注「registry 尚未接主调度」）、`mbdsdr_ai/acars_decoder.py:585`（AI 工具）、`mbdsdr_ai/decode_router.py:517`（CLI `--decode-file --mode acars` 的生产解码路径，经 `mbdsdr_ai/decoders.py:1157`）、`mbdsdr_ai/agent.py:1375-1376`（三个工具）。差异表与退休判据见 §5.6 | **不退休**：整体退休任一侧都会打断生产入口（判据见 §5.6）；本行不再写「已收敛/唯一入口」 | `ACARSDecoder.decode_audio`（`mbdsdr_ai/acars_decoder.py:448`）是 CLI 生产路径，却**无直接测试**（`tests/acars_test.py:27` 仅 import）且自合成信号自环 `crc_ok=False`（`:578-583` 自述）；把它当「已验证实现」会误判解码质量 |
| A11 | dBFS / dBµV 两套格式化（账本 R7） | 重复实现（不同量纲） | `desktop/status_panel.py:49/62`；`desktop/main_window.py:87 _fmt_dbfs`（0 生产调用、13 测试引用）；`:105` 1 调用 | 保留量纲两份；`_fmt_dbfs` 另见 C11 | 把 dBµV 当 dBFS 合并会差约 107 dB（50Ω/75Ω 基准不同） |
| A12 | `cpp/src/core/tokens.h` 原 `kDbUpperDefault` **一个常量担两个语义** | **常量复用**（不是重复实现，也不是多份表） | 语义①「默认视窗上限」→ 现 `kDbViewUpperDefault=-20`（`cpp/src/ui/main_window.cpp:5644`、`cpp/src/ui/settings_dialog.cpp:227/432/543`、`cpp/src/ui/spectrum_widget.cpp:308/658`）；语义②「显示天花板硬上限」→ 现 `kDbCeilingMax=0`（`cpp/src/ui/spectrum_display.cpp:693/705`）。修复前实测：显式 `setDbRange(-100, 0)` 被 `:705` 夹到 -20；同一帧 -10 dB 峰值渲染成 **255（纯白）**，按 -100..0 视窗应为 **229** | **已修** — `ef07c133` — `cpp/src/core/tokens.h:338` — `kDbViewUpperDefault`：拆成 `kDbViewUpperDefault=-20`（默认视野）+ `kDbCeilingMax=0`（0 dBFS 满量程硬上限）；2 个 ctest + 全部频谱与 UI 集成共 **10 个目标全绿**，**未改任何期望值**；反证把 `kDbCeilingMax` 改回 -20 → 红项槽级明细与修复前**逐字一致**。**原建议「硬上限 = `kDbSpinUpperMax` = 20」已否决**：上界取 20 会让"超过满量程"与"未超过"的画法不再有区别，等于抹掉 0 dBFS 满量程这个物理事实 | 不拆则所有"手动视窗上限 > -20 dB"的调用都被静默改档、波形顶部白化；拆后须保留 A/B 同帧对拍 255→229 作为回归证据（格式见 §4.3） |

### B. 多真值源：采样率档位逐份清单

> 采样率是本次最容易被误判成"冗余"的一组。**逐份列字面量与消费方**，不做合并。
>
> **本账本不引用采样率同步面数量（旧 23/26）**；如后续需要，以采样率线在 HEAD 的重数为准（同步面 28：代码 17 / 测试 7 / 文档 4；只数档位集合为 24：代码 13）。
>
> B1–B4、B6–B8 已按 HEAD 逐条复核：行号用 `git show HEAD:<path>` 对字面量，hash 用 `git show --name-only <hash> -- <path>` 反查归属、`git log -S <字面量>` 命中后才写。

| # | 表 / 位置 | 字面量 | 消费方（命中） | 判定 |
|---|---|---|---|---|
| B1 | C++ 可下发白名单 `cpp/src/core/tokens.h:287 kSampleRatesHz` | `1.024 / 2.048 / 2.4 / 3.2 MHz`（4 档） | `cpp/src/control/control_hub.cpp:503/507`（档外显式拒绝） | **保留 = 控制面白名单**。与 B2 是"白名单 ⊆ 候选"关系，**不许合并**。证据：`474f7cd2` — `cpp/src/core/tokens.h:287` — `kSampleRatesHz`（`show --name-only` 触及 + `log -S` 命中） |
| B2 | C++ 设备候选探测表 `cpp/src/dsp/device_capabilities.cpp:36-48 kCandidateRatesHz` | `250k / 1024k / 1536k / 1792k / 1920k / 2048k / 2160k / 2400k / 2560k / 2880k / 3200k`（11 档） | `:131 for (const double r : kCandidateRatesHz)` 按设备实际范围过滤 | **保留 = 候选探测表**。`:46` 已是 `2_880_000`：真机请求 `2,800,000` 被设备吸附到 `2,880,000`（读回 +80,000 Hz，E3，见 `limitations-register` 附 M.4 L60），候选表只列设备实际给出值。证据：`30214fac` — `cpp/src/dsp/device_capabilities.cpp:36` — `kCandidateRatesHz`；吸附档 `dd72ba1c` — `:46` — `2880000.0` |
| B3 | Python 后端档位 `mbdsdr_ai/sdr_backend.py:1501 RTLSDRBackend.SAMPLE_RATES` | 与 B2 **同 11 档**（含 `2_880_000`） | `mbdsdr_ai/device_capabilities.py:205` 动态 `__import__` 取表 → `sample_rate_options()`（`:217`）；`desktop/control_panel.py:1360` 间接消费 | **保留**：Python 侧后端自报档位，无 C++ 运行时共享，不能"派生自 C++"。证据：`4b8060f7` — `mbdsdr_ai/sdr_backend.py:1501` — `SAMPLE_RATES` |
| B4 | Python AI 参考表 `mbdsdr_ai/rtlsdr_params.py:139-150 SUPPORTED_SAMPLE_RATES` | `250k / 1024k / 1536k / 1800k / 1920k / 2000k / 2048k / 2400k / 2560k / 3200k`（**10 档，与 B2 不同**） | **生产消费方存在**：`mbdsdr_ai/tool_registry.py:2583`、`:2618` 作为 AI 工具 `recommended_rates_hz` 输出；测试 `tests/rtlsdr_params_test.py:102` | **保留 = AI 工具推荐表（故意不同）**：含 1.8M/2.0M 常用收音机档，缺 1.792M/2.16M/2.88M。它不驱动任何 UI 下拉，**不参与"可见档位"**。证据：`3e316233` — `mbdsdr_ai/rtlsdr_params.py:139` — `SUPPORTED_SAMPLE_RATES` |
| B5 | 桌面模块面板采样率下拉 `desktop/module_panel.py:740 sample_rate_combo_items(device)`（`_fill_sample_rate_options` 在 `:733`） | **无前端字面量档位表**：`items = sample_rate_combo_items(device)`（`:740`，基准 `7bea149a`）→ `constants.sample_rate_combo_items`（`desktop/constants.py:51`）→ `mbdsdr_ai.device_capabilities.sample_rate_options(device)`（后端离散表 ∩ 设备量程）；显示文案统一走 `format_sample_rate`（`mbdsdr_ai/sdr_backend.py:114/131`，`MS/s` + 三位小数） | 初始化 `:348 _fill_sample_rate_options(None)`；选设备 `:529-532 _on_device_selected`；取值 `:557 selected_sample_rate_hz()` → `:600 dev["_sample_rate"]` → `:607 device_selected.emit`；接线 `desktop/main_window.py:1635 device_selected.connect` → `:7400 _on_module_device_selected`；未报能力时单项「未知（…将用默认 X）」且置灰（`:346-348` 注释 + `:550-556` tooltip） | **已改能力驱动（已落库）**：`7bea149a` — `desktop/module_panel.py:579` — `sample_rate_combo_items(device)`（基准 `7bea149a`，该行 blame）。全文件无 `1.024 / 1.440 / 2.048 / 2.400 / 3.200 MS/s` 任一档位字面量，原「5 档静态表、代码未落地」判定**作废**。与 §5.1 原规格的差异：`selected_sample_rate_hz()` 仍回退 `DEFAULT_SAMPLE_RATE_HZ`（不返回 `None`），但能力未知时控件禁用，该回退只作连接兜底、不再冒充可选档位 | 若产品确需 `1.440 MS/s`，仍须进内核两表 + 真机读回（B1/B2/B3/B4 均无此档）；桌面侧不得单补 |
| B6 | 桌面默认值回退 `desktop/constants.py:19-26` | `DEFAULT_SAMPLE_RATE_HZ`（优先导入内核值，失败回退 `2_400_000`） | 全桌面共享 | **保留**（安全回退，与内核基准一致，不是新真值）。证据：`6ed9ee7d` — `desktop/constants.py:26` — `DEFAULT_SAMPLE_RATE_HZ = 2_400_000` |
| B7 | 移动端历史默认 `mobile/lib/app/tokens.dart:222-227 sampleRatesHz` | 4 档，已标 `@Deprecated('历史默认，不被任何人读取')` | 全仓 Swift/Dart 代码命中 **0**；仅 `mobile/lib/pages/spectrum_page.dart:761` 一句注释 | **保留**：删除条件写在 `tokens.dart:217-220` = "确认无外部引用且旧版本回滚窗口关闭"。回滚窗口无法从仓库核实 → 不删。证据：`61d4eaf6` — `mobile/lib/app/tokens.dart:222` — `sampleRatesHz` |
| B8 | 真机硬件吸附档 `2,800,000 → 读回 2,880,000`（`cpp/src/dsp/device_capabilities.cpp:46`） | `2880000.0`（候选表 11 档见 `:36-47`） | 另一条线真机逐档实测：**11 档一致，仅 2800k 档驱动返回成功但静默吸附到 2880k（+80 kHz）**；`rtl_sdr_source` 侧 `rtlsdr_get_sample_rate` 读回已接上 | **不是冗余，不许删**。任何把 2.88M 当"多余档位"删掉的判断都是错的；该档的价值是"必须靠读回才发现"的负样本。证据：`dd72ba1c` — `cpp/src/dsp/device_capabilities.cpp:46` — `2880000.0` |

### C. 死代码 / 无人调用

| # | 冗余项 | 类型 | 证据（位置 + 命中数） | 处置 | 风险 |
|---|---|---|---|---|---|
| C1 | Python `mbdsdr_ai/recording_library.py` | 死代码（桌面） | `desktop/` 命中 **0**；`tests/` 6 个文件 import（`test_recording_library.py`、`_retention`、`_migration`、`_stats_audio`、`_concurrency`、`test_e2e_operation_scenarios.py`） | **不删、不接线**（承载库行为与测试） | 删除会让 6 个测试失去被测对象；接线属产品决定。登记为 **library-only** |
| C2 | Python `mbdsdr_ai/recorder.py` | 死代码（桌面） | `desktop/` import 命中 0（仅 `module_panel.py:17/373` 两处下拉文案）；消费方：`mbdsdr_ai/recorder_enhanced.py:28`、`scripts/real_hw_acceptance.py:434`、`scripts/hw_acceptance/ring4_recording_playback.py:61`、5 个 recording_library 测试 + `test_tx_session.py` / `test_tx_no_hardware.py` / `test_playback.py` / `mbdsdr_ai/tests/test_recorder_bitdepth.py` / `test_transmit_doppler.py` | **不删** | 桌面直接写盘绕过它（见 C3），删了脚本/live 验收先断 |
| C3 | 桌面"直接写盘 + `baseband_io.load_iq` 回放" vs `recorder`/`playback` 并行链路 | 重复实现（功能重叠、行为不同） | 桌面直写：`desktop/main_window.py:6468`、`:6598`（`baseband_io.write_sidecar`）；桌面回放：`:8093-8098`（`baseband_io.load_iq`）。`playback` 真实消费方：`tools/onboarding/onboard.py:471`、`experiments/common/datasource.py:101`、`experiments/exp_ota_run.py:198`、`experiments/demo_spacetime.py:49`、`tools/onboarding/test_onboarding.py:142` + 多测试 | **保留两条，登记为并行实现**；不合并、不接线 | `playback` **不是死代码**（原计划判"三模块全死"，本表改判）。两条链路对元数据/SigMF 处理不同，合并会改桌面回放行为 |
| C4 | C++ `cpp/src/ui/recording_library.cpp` | 同名不同物 | 被 `cpp/src/ui/main_window.h` 引用；`cpp/CMakeLists.txt` 构建；测试 `cpp/tests/test_recording_library.cpp`、`test_playback_loopback.cpp` | **保留** | 与 Python C1 同名但无任何关系；按名字判冗余会误删在用的 C++ UI 模块 |
| C5 | 4 个全仓零调用函数：`themes.get_monospace_font()`、`shortcuts.registered_keys()`、`mode_registry.display_names()`、`device_capabilities.unknown_values()` | 死代码 | 删除前全仓代码命中 0（含字符串/`getattr` 形态）；删除后仅 `docs/audit_r2/43_ui_status_themes_mcp.md:81-84/279`、`FINAL_REPORT_R2.md:393` 这些**历史审计报告**提及 | **已删除**，提交 `c14ac51e`（4 files, 26 deletions） | 无。删除前后测试 91→91（见 §4） |
| C6 | **46 个** `mbdsdr_ai` 模块仅被自身测试引用（原记"约 32"，复核后按新口径记为 46 个；逐文件清单见 §5.3） | 死代码（library-only） | 口径：`git ls-files` 代码文件（`.py/.dart/.cpp/.h/.ps1/.bat/.sh`）全形态 import（`mbdsdr_ai.X`、`from mbdsdr_ai import X`、`from .X import`、`from ..X import`、`from . import X`）非测试路径命中 0；且自身无 `register_tool_registry`（否则 `mbdsdr_ai/core/cli.py:264-269` 的 `pkgutil.iter_modules` 会在生产 CLI 自动发现）；裸名命中已逐条人工核实（`device_watchdog`/`vna_calio`/`web_bridge` 均为注释/docstring，非导入） | **不删；按三类归档**（§5.3） | 删一个模块会让对应测试整批消失，通过项数必然下降（违反"通过项数不许下降"）；且可能是对外 API 面 |
| C7 | 8 个桌面面板零调用公开方法：`set_disconnected`、`set_observer_ready`、`is_anr_enabled`、`submit_frame`、`set_default_params`、`attach_host`、`clear_objects`、`last_capability_report` | 疑似死代码 | 全仓调用命中 0；均定义在 `desktop/` 面板类内 | **保留** | 更像"功能未接线"而非冗余；删除等于消灭待接的接口，且可能被未来 `connect` 使用 |
| C8 | `desktop/touch_helpers.py:214/236` 两个触屏控件类 | 死代码 | 零实例化（全仓 `TouchFriendlyScrollArea(` / `TouchFriendlyListWidget(` 命中 0） | **只登记，不删** | 文档红线明确"不碰触屏（touch_manager/touch_helpers）"，属他人领域 |
| C9 | `dock_layout_manager._preset_focus/_preset_analysis/_preset_grid` | 死代码（**误报**） | 静态零引用，但 `:225 getattr(self, f"_preset_{method_suffix}")()` 动态调用（定义 `:464/478/497`） | **保留** | 按静态 grep 删掉会让"恢复预设布局"直接 `AttributeError` |
| C10 | `ai_max_context_tokens / ai_max_output_tokens / ai_timeout` | 死配置（**误报**） | 字面量命中 0；经 schema 泛化 `item.key[3:]` 写入 `ai_config` 后被消费 | **保留** | 按字面量判死会删掉 AI 设置项 |

### D. 死分支 / 死配置

| # | 冗余项 | 类型 | 证据（位置 + 命中数） | 处置 | 风险 |
|---|---|---|---|---|---|
| D1 | ACARS `unsupported_format` 分支 | 死分支 | `scripts/decoder_capability_report.py:436` 已登记；能力表声明但代码不可达 | **保留** | 删掉后能力表与实现继续漂移且无记录；应修能力表而非删分支（属解码线） |
| D2 | `not_implemented` 结果 | 死分支（仅 1 条可达） | 全仓仅 ft8 路径可达（能力表其余模式无调用） | **保留** | 与 D1 同源，删了会吞掉"未实现"的诚实回报 |
| D3 | C++ `kFreqCoarseStepHz` | 死配置（零消费） | `cpp/src/core/tokens.h` 定义；消费点 0（`docs/parameter-unification.md` 第 5 组已登记）；Python 实际用 `snap_interval` | **保留并沿参数账本处理** | 真接进细调会与 Python 的 0.1×snap 差 10 倍（账本已登记）；本专项不动 |
| D4 | C++ `kSettingsKey*` 设置键 | 死配置（曾疑似） | 35 个全部 ≥2 处使用（定义 + 读/写） | **保留**，判定**无死键** | 无 |
| D5 | Python 配置 schema 键无人读 | 死配置（曾疑似） | 未发现确认死键；字面量 0 的 3 个 `ai_*` 键已由 C10 判为泛化读取 | **保留** | 无 |
| D6 | `_LEGACY_KEY_ALIAS` | 死配置（必须保留） | 旧配置兼容别名 | **保留，不许动** | 删掉会让老用户配置键失效（静默丢设置） |

---

## 3. 因新事实而改变判定的项（对比原计划）

> 这一节专门记录"原计划怎么判 → 事实是什么 → 现在怎么判"，避免下一个人再走一遍误判。

| 项 | 原计划 | 新事实（另一条线实测） | 现判定 |
|---|---|---|---|
| C++ 采样率 4 档 vs 11 档 | 判为"多真值源，应收敛成一份内核真值 + 其余派生" | 4 档 = **可下发白名单**（`tokens.h:287`，档外显式拒绝，`control_hub` 刚补校验）；11 档 = **设备候选探测表**（`device_capabilities.cpp:36`）。关系是 **白名单 ⊆ 候选**，不是同一物理量的两套真值 | **不合并**；登记表逐份写明用途（B1/B2），避免下一个人再判成冗余 |
| 2.8M 档 | 曾按"候选表里多余档位"考虑删除 | 真机逐档回读：11 档一致，**仅请求 2,800,000 时驱动返回成功但读回 2,880,000（+80,000 Hz）** | **不删，且必须保留**（B8）。它的价值是"硬件静默吸附、只能靠读回发现"的证据；`rtl_sdr_source` 侧 `rtlsdr_get_sample_rate` 读回已接上，"读数≠请求即报警"会覆盖它 |
| `recording_library` / `recorder` / `playback` | 三模块"全仓无桌面调用方"，按死代码处理 | `recording_library`：桌面 0、6 测试；`recorder`：桌面 0，但脚本/验收/测试在用；`playback`：**不是死代码**（`tools/onboarding/onboard.py:471`、`experiments/common/datasource.py:101`、`exp_ota_run.py:198` 在用），桌面回放只是**绕过它**直接走 `baseband_io.load_iq` | **三个都不删、不接线**；登记为 **library-only**（C1/C2）+ **两条并行实现（功能重叠、行为不同）**（C3） |
| 桌面录音/回放 | 视为"直接调用 recorder/playback" | 桌面直写 `baseband_io.write_sidecar`（`main_window.py:6468/6598`），回放 `baseband_io.load_iq`（`:8093-8098`），从未 import 两模块 | 从"调用方证明"改为"并行实现登记"，不删任何一侧 |

**被这四条事实否掉的原计划动作**：① 采样率表收敛；② 删除 2800k/2880k；③ 三录音模块当死代码清理；④ 把桌面录音判成"未走统一入口需改接"。以上四项本轮全部**不做**。

---

## 4. 已执行的删除与反证

### 4.1 删除内容（提交 `c14ac51e`）

| 文件 | 删除 | 原位置 |
|---|---|---|
| `desktop/themes.py` | `get_monospace_font()` 15 行 | 文件末尾 |
| `desktop/shortcuts.py` | `registered_keys()` 3 行 | 文件末尾 |
| `desktop/mode_registry.py` | `display_names(category)` 5 行（`mode_names()` 保留） | 原 203-205 |
| `mbdsdr_ai/device_capabilities.py` | `unknown_values(report)` 3 行 | 文件末尾 |

`git show --stat c14ac51e`：`4 files changed, 26 deletions(-)`

### 4.2 删除反证（通过项数不许下降）

```powershell
python -m pytest tests/test_theme_switch.py tests/test_theme_tokens.py \
  tests/test_shortcuts_guard.py tests/test_shortcut_focus_priority.py \
  tests/test_mode_registry.py tests/test_mode_switch_preset.py \
  tests/test_device_capability_probe.py tests/test_sample_rate_options.py -q
```

| 时点 | 结果 |
|---|---|
| 删除前 | **91 passed**, 4 warnings |
| 删除后 | **91 passed**, 4 warnings |

另：`tests/test_hardcoded_parameter_registry.py` 单独运行 **26 passed**（峰值定义对拍未受影响）。

### 4.3 吞错守卫（棘轮）越线核查

`tests/test_swallow_guard.py::test_repo_ratchet_within_baseline` 失败，**不是本线越线**：
越线文件来自他线提交、但基线 `docs/code-guards-baseline.json` 未同步刷新的存量/新增文件；
本线改动文件（`desktop/constants.py`、`mbdsdr_ai/sdr_backend.py`、`mbdsdr_ai/sdr_tools.py`、
`desktop/config_schema.py`、`tests/test_desktop_constants.py`）**不在清单内**，也没有新增 `try/except`。

| 越线文件 | 越线计数 | 引入提交 | 该刷新基线的 owner（只出清单，不动手） |
|---|---|---|---|
| `mbdsdr_ai/reconnection.py` | WARN 3 > 基线 2（+1） | `1f96cfe2`「修复(重连): 增加立即取消入口…」 | 接入层 / 重连线（作者） |
| `scripts/hw_acceptance/common.py` | ERROR 1 > 基线 0（+1） | `4187ae4f`「验收(hw): 七环 SKIP 原因码落地」 | `claims.json` 该文件归 **操作逻辑线**；提交作者按验收线口径 |
| `scripts/hw_acceptance/manual_usb_cycle.py` | 基线无此文件（2 ERROR / 4 WARN） | `4694f425`「test(network): 2s/4s 阈值实测 + 拔线半自动验收」 | 验收线（作者） |
| `scripts/perf_probe.py` | 基线无此文件（1 ERROR / 2 WARN） | `3abdd772`「perf(startup): 桌面启动导入降 75%…」 | 性能线（作者） |
| `tests/test_link_state_visibility.py` | 基线无此文件（1 ERROR） | `928bd78f`「接入层：环形缓冲积压错误码可见」 | 接入层（作者） |
| `desktop/main_window.py` | ERROR **228 > 227**（已定钉，不再随保存波动） | **未提交** hunk（工作区当前 `3668-3671`，原 `3659-3663`）：`default_freq = float(self.control_panel.get_frequency_hz())` 外包 `except Exception: default_freq = 98_500_000.0` | **操作逻辑线**（该文件 claim ACTIVE）；要求其提交时同批刷新基线 |

总量 `1293~1294 > 基线 1284`（+9~+10）。**不得为此上调基线**；基线刷新归造成越线的线路，
本线只做登记。`desktop/main_window.py` 那条已定钉为**一次具体未提交改动**（连接后默认频率读回包 `except`），
不是"保存即波动"；此前"未提交的新文件"表述不准确，本表按上表口径重写。

**本类问题（静默吞噬/常量复用）的标准取证格式**（内核线 A/B 实测先例，建议后续沿用）：

1. **A/B 两次构建**：A = 改前常量/旧代码，B = 拆分后/修复后；两次都用同一编译配置（同 CMake preset、同 Qt 版本）。
2. **同机同环境**：同一台机器、同一 GPU/驱动、同一显示缩放；记录构建号与 `git rev-parse HEAD`。
3. **同一输入**：固定同一帧 `frame.dbfs`（或同一 `setDbRange(-100, 0)` 调用），不靠"看起来不一样"。
4. **可量化断言**：记录像素/数值（本次为峰值 `255` vs `229`、`dbCeilDb_ 0 → -20`），并写进测试或登记表。
5. **反证结论**：只有 B 侧数值回到期望且 A 侧可复现缺陷，才算修复成立；否则视为环境差异，不结案。

---

## 5. 裁定落地：规格、协调与归档

### 5.1 B5：`module_panel` 采样率档位改为能力驱动（已落库 `7bea149a`）

**现状**（按 HEAD 复核，2026-11-06；原写「已准改、代码未落地」的判定作废）：
- `desktop/module_panel.py:534 _fill_sample_rate_options(device)` 的档位来自 `:541 items = sample_rate_combo_items(device)`；`:346-348` 注释明写「档位来自所选设备能力…不持有前端静态档位表」。
- 全文件 `git grep` 无 `1.024 / 1.440 / 2.048 / 2.400 / 3.200 MS/s` 任一档位字面量；显示文案统一走 `format_sample_rate`（`mbdsdr_ai/sdr_backend.py:114/131`，`MS/s` + 三位小数），桌面无第二套格式化。
- 能力未知（未连接/驱动未报量程）：combo 只留一项「未知（设备未报告采样率范围，将用默认 X）」（`itemData=None`）并 `setEnabled(False)`；断开时保持置灰，重连后按新设备能力重填（`:529-532 _on_device_selected`）。
- 仍然残留：`:557 selected_sample_rate_hz()` 回退 `DEFAULT_SAMPLE_RATE_HZ`，不返回 `None`（见下表差异）。因控件在能力未知时禁用，该回退只作连接兜底，不再冒充可选档位。

**与原规格的差异**（原 §5.1 规格是「代码未落地」时写的，落库实现与它有三处不同）：

| 原规格 | 落库实现 |
|---|---|
| 占位文案「未连接，档位随设备」 | 「未知（设备未报告采样率范围，将用默认 X）」 |
| `selected_sample_rate_hz()` 返回 `Optional[float]`/`None` | 仍返回 `float`，无效时回退 `DEFAULT_SAMPLE_RATE_HZ` |
| 新增 `set_sample_rate_options(...)` 注入接口 + tests/test_module_panel_sample_rate_capability.py（原规格提案，未落地） | 直接按 `device` dict 查能力，无独立注入接口；他线新增 `tests/test_module_panel_truthful_controls.py`（工作区未跟踪，本线未复跑） |

三处差异都是**行为收窄**（禁用 + 显式「未知」），不改变「用户可见档位不再来自前端静态表」这一结论。

**协调下家**：

| 文件 | owner（claims 现状） | 状态 |
|---|---|---|
| `desktop/module_panel.py` | 操作逻辑线 | **已落库**（`7bea149a`） |
| `desktop/main_window.py` | 操作逻辑线（工作区仍有在途改动） | 接线在 HEAD 已存在：`:1635 device_selected.connect` → `:7400 _on_module_device_selected`；落库后按 §5.7 复核行号 |
| C++ 下发侧 | 采样率真值线 | 确认 `sample_rate_options_hz` 已下发：`cpp/src/control/control_hub.cpp:1083`，测试 `cpp/tests/test_control_hub.cpp:1378-1379`；修正 `docs/parameter-unification.md:467` 仍写"未接进 get_capabilities"的旧记录 |

**`1.440 MS/s` 若产品确需**：必须进 `cpp/src/dsp/device_capabilities.cpp kCandidateRatesHz` + `cpp/src/core/tokens.h kSampleRatesHz` 白名单 + 真机读回；**不能只补前端**（当前派生链里没有这一档）。

### 5.2 常量白名单反证（已完成）

| 步骤 | 操作 | 结果 |
|---|---|---|
| 1 | `desktop/constants.py` 临时加 `DEAD_PROBE_CONSTANT = 42`（无人使用） | `python -m pytest tests/test_desktop_constants.py -q` → **`1 failed, 11 passed`**，失败信息 `Extra items in the left set: 'DEAD_PROBE_CONSTANT'` |
| 2 | 删除该常量 | 复跑 → **`12 passed`**（含 Qt 测试互斥锁等待 81.1s） |

结论：白名单是**活的锁**，不是文档；任何新公开名都会立刻变红。

### 5.3 C6：46 个仅测试模块的三类归档（不删）

**复核口径**（替代原"约 32"）：`git ls-files` 的代码文件（`.py/.dart/.cpp/.h/.ps1/.bat/.sh`）中，全形态 import（`mbdsdr_ai.X`、`from mbdsdr_ai import X`、`from .X import`、`from ..X import`、`from . import X`）在**非测试路径命中 0**；自身无 `register_tool_registry`（否则 `mbdsdr_ai/core/cli.py:264-269` 的 `pkgutil.iter_modules` 会在生产 CLI 自动发现）；裸名命中逐条人工核实（`device_watchdog`/`vna_calio`/`web_bridge` 均为注释/docstring）。原 32 少算是因为漏了 `from mbdsdr_ai import X` 与 `from . import X` 两种形态。

**处置**：不删、不移目录。测试全部按 `mbdsdr_ai.X` 模块路径 import（含 `from mbdsdr_ai import X`），移目录会改导入路径、让测试直接红；因此三类都优先**只加 `@experimental` 标记**，不移动。

**① 对外 API / 服务面（10 个；保留 + 补外部入口说明）**

| 文件 | 当前唯一引用点 | 保留理由 |
|---|---|---|
| `web_bridge.py` | `tests/test_web_bridge.py` | web 产品契约入口；`web/product/*` 文档已引用 |
| `webspectra.py` | `tests/test_webspectra.py` | 对外频谱服务面 |
| `remote_control_v2.py` | `tests/test_remote_control_v2.py` | 远程控制协议面 |
| `mobile_server.py` | `mbdsdr_ai/tests/test_mobile_server.py` | 移动端服务端协议面 |
| `multi_user.py` | `tests/test_multi_user.py` | 多用户会话面 |
| `protocol_analyzer.py` | `tests/test_protocol_analyzer.py` | 协议分析对外工具面 |
| `position_service.py` | `mbdsdr_ai/tests/test_position_service.py` | 位置服务面（GNSS/观测者） |
| `hal_enhanced.py` | `tests/test_hal_enhanced.py` | 增强 HAL 面（设备族差异） |
| `module_system.py` | `tests/test_module_system.py` | 模块/插件系统公开面 |
| `feature_manager.py` | `mbdsdr_ai/tests/test_feature_manager.py` | 特性插件管理器公开面 |

**② 实验 / 原型 / 兼容垫片（15 个；保留 + 标 `@experimental`，不移动）**

| 文件 | 当前唯一引用点 | 保留理由 |
|---|---|---|
| `aigui_demo.py` | `mbdsdr_ai/tests/test_aigui_render.py` | AI GUI 渲染原型 |
| `flowgraph_runtime.py` | `tests/test_flowgraph_runtime.py` | 流图运行时原型 |
| `frequency_manager_v2.py` | `tests/test_frequency_manager_v2.py` | 频率管理 v2 原型 |
| `gain_calibration.py` | `tests/test_gain_calibration.py` | 增益校准实验 |
| `janvna2_proto.py` | `mbdsdr_ai/tests/test_janvna2_proto.py` | JanVNA2 协议原型 |
| `nanovna_v2_proto.py` | `mbdsdr_ai/tests/test_nanovna_v2_proto.py` | NanoVNA v2 协议原型 |
| `mimo_v2.py` | `mbdsdr_ai/tests/test_mimo_v2.py` | MIMO v2 原型 |
| `module_graph.py` | `mbdsdr_ai/tests/test_module_graph.py` | 模块图原型 |
| `marker_math.py` | `mbdsdr_ai/tests/test_marker_math.py` | 标记数学实验 |
| `spectrum_accumulator.py` | `tests/test_spectrum_accumulator.py` | 频谱累积实验 |
| `vfo_enhanced.py` | `tests/test_vfo_enhanced.py` | VFO 增强原型（`desktop/vfo_panel.py:6` 仅注释提及） |
| `device_watchdog.py` | `mbdsdr_ai/tests/test_optional_deps_fallback.py`、`tests/test_adsb_map_extras.py` | 设备看门狗实验（`reconnection.py:18-19` 仅注释对比） |
| `optional_deps.py` | `mbdsdr_ai/tests/test_optional_deps.py` | 可选依赖垫片 |
| `s_meter.py` | `tests/test_s_meter.py` | Python S 表实验（C++/Flutter 另有同名实现，与本模块无关） |
| `startup_sequence.py` | `tests/test_audio_selection_e2e.py`、`tests/test_startup_sequence.py` | 启动序列实验 |

**③ 领域解码 / DSP（21 个；保留待能力表/工具面接线）**

| 文件 | 当前唯一引用点 | 保留理由 |
|---|---|---|
| `anr_iq.py` | `tests/test_anr.py` | IQ 域降噪算法 |
| `background_decoder.py` | `tests/test_background_decoder.py` | 后台解码器 |
| `block_stream.py` | `tests/test_block_stream.py` | 块流处理 |
| `channel_analyzer.py` | `tests/test_channel_analyzer.py` | 信道分析 |
| `dvbs2_datafield.py` | `tests/test_dvbs2_datafield_vcdu.py` | DVB-S2 数据域（风云卫星链） |
| `gated_recorder.py` | `tests/test_gated_recorder.py` | 门控录制 |
| `gps_tracking.py` | `mbdsdr_ai/tests/test_gps_tracking.py` | GPS 跟踪环 |
| `lrpt_image.py` | `tests/test_lrpt_meteor_real_golden.py` | LRPT 图像解码 |
| `lrpt_pipeline.py` | `mbdsdr_ai/tests/test_lrpt_pipeline.py` | LRPT 处理链 |
| `lrpt_render.py` | `mbdsdr_ai/tests/test_lrpt_render.py`、`test_optional_deps_fallback.py` | LRPT 渲染 |
| `polyphase_resampler.py` | `tests/test_dsp_frontend_polyphase_resampler.py` | 多相重采样 |
| `recorder_enhanced.py` | `tests/test_recorder_enhanced.py` | 增强录制（`recorder.py:28` 为其依赖） |
| `recording_library.py` | 6 个 recording_library 测试（见 C1） | 录音库（见 C1） |
| `sat_calibration.py` | `mbdsdr_ai/tests/test_sat_calibration.py` | 卫星定标 |
| `sat_pipeline_runner.py` | `tests/test_sat_pipeline.py` | 卫星流水线执行器 |
| `sdrpp_decoders.py` | `mbdsdr_ai/tests/test_sdrpp_decoders.py` | SDR++ 解码器移植 |
| `tc_cltu.py` | `mbdsdr_ai/tests/test_tc_cltu.py` | TC CLTU 编码 |
| `usb_watchdog.py` | `tests/test_reconnection_e2e.py`、`tests/test_usb_watchdog.py` | USB 看门狗 |
| `vna_avg.py` | `mbdsdr_ai/tests/test_vna_avg.py`、`test_vna_property.py` | VNA 平均 |
| `vna_calio.py` | `mbdsdr_ai/tests/test_vna_calio.py`、`test_vna_e2e.py` | VNA 校准 IO（`core/mcp_server.py:802` 仅注释） |
| `ysf_demod.py` | `tests/test_ysf_demod_roundtrip.py` | YSF 数字解调 |

### 5.4 C7：8 个零调用面板方法——4 个定下家，4 个贴标签

| 方法 | 定义位置 | 下家（线） | 具体功能点（接线位置） | 接线后验证 |
|---|---|---|---|---|
| `set_disconnected()` | `desktop/status_panel.py:866` | **操作逻辑线** | `main_window._panels_set_sdr_connected`（`:4137`）的 status_panel 分支（当前 `:4220-4222` 调 `set_sdr_connected(bool)`）改调 `sp.set_disconnected()`；断开路径 `:837/:4324/:4393` | 断开后状态卡整组禁用；该方法生产调用 ≥1 |
| `set_observer_ready(ready)` | `desktop/sat_track_panel.py:254` | **操作逻辑线 + 卫星跟踪/星象线** | `main_window._apply_observer_location`（`:8356-8379`）设置观测者后调 `set_observer_ready(True)`；GNSS 自动填坐标路径 `:8919-8923` 同调；无坐标时 `False` | 面板显示"观测者就绪/未就绪"，跟踪按钮按就绪态门控 |
| `is_anr_enabled()` | `desktop/anr_panel.py:156` | **操作逻辑线** | `main_window._on_anr_enabled`（`:7098-7100`）改为读 `anr_p.is_anr_enabled()`，删除影子标志 `self._anr_enabled`；音频回调 ANR 分支（`:6306` 附近）同源查询 | 单一真值：面板开关与音频路径行为一致 |
| `last_capability_report()` | `desktop/device_panel.py:296` | **冗余线 + ux-basis + 操作逻辑线** | 与 §5.1 同批：`_panels_set_sdr_connected(True)` / `dp.set_connected` 后取 `dp.last_capability_report()` 的 `sample_rate_options` 喂 `module_panel.set_sample_rate_options(...)` | 面板档位来自真实报告；断连清空禁用 |

其余 4 个（`submit_frame`、`set_default_params`、`attach_host`、`clear_objects`）**保留并贴"待接线"标签**；两个版本内仍无生产调用再评估删除，删除须单独提交并附测试前后数。

### 5.5 其余既定项（不变）

| 项 | 结论 |
|---|---|
| A3 采样率格式化合并 | **改动在位**（`f529ba62`）：`constants.format_sample_rate is sdr_backend.format_sample_rate` 于 2026-11-06 复验成立；`53 passed` 为 `f529ba62` 提交信息所记当次实跑（2026-11-06 02:21 +0800，本机 Windows/PowerShell `pytest`），**本轮未复跑** |
| C3 桌面写盘 vs `recorder`/`playback` | 不删不接线（录音/回放线持线） |
| A1 频谱双实现 | 维持双实现；统一前须跑 D.7 对拍 |

### 5.6 A10 两份 ACARS 实现：差异对照与退休判据

> 取证口径：HEAD 树 `git grep -nE ... HEAD -- ...`，2026-11-06。结论：**两实现并存、入口不止一个、退休条件未满足**。

| 维度 | `mbdsdr_ai/acars_protocol.py`（自包含协议/验证核心） | `mbdsdr_ai/acars_decoder.py`（MSK 解调 + 状态机） |
|---|---|---|
| 覆盖模式 | 仅 ACARS；`mode` 为 1 字符**透传**（无按 mode 分支）；支持 1200/2400 baud | 同左：`mode` 透传（`:393`）；另含帧合成 `build_acars_frame`（`:494`）/ `synthesize_msk_audio`（测试与样本生成用） |
| 解码链路 | `_fsk_discriminate` → 16 相位 × 2 极性 × 8 字节对齐搜索，取 CRC 通过最多的候选（`:421-455`） | `ACARSMDemod` → `ACARSFrameSynchronizer` → `ACARSMessageParser`；`decode_audio`（`:448`）优先取 CRC 通过者，全不过则返回候选（`crc_ok=False`） |
| 解码质量 | 合成帧在 1200/2400、15 dB SNR 下 `crc_ok=True`（`tests/test_acars_consistency.py`） | 自合成信号自环 `crc_ok=False`（`:578-583` 自述）；对外 `acars_decode_iq` 因此委托协议实现（`:585`） |
| 测试覆盖 | `tests/test_acars_roundtrip.py` 5 项 + `tests/test_acars_consistency.py` 4 项（双入口一致、ETB、1200 bps） | 解析器/解调有测试（`tests/acars_test.py` 的 `TestMessageParse`/`TestMSKDemod`）；**`ACARSDecoder.decode_audio` 0 直接测试**（`tests/acars_test.py:27` 仅 import） |
| 调用方 | `mbdsdr_ai/decoder_registry.py:78`；`mbdsdr_ai/acars_decoder.py:585`（委托）；`scripts/make_sensitivity_fixtures.py:109`（生成 `acars.wav`） | `mbdsdr_ai/decode_router.py:499/517`（CLI `--decode-file --mode acars` 生产解码）；`mbdsdr_ai/agent.py:1375-1376`（三个 AI 工具）；`scripts/decode_sensitivity.py:480`（经 `acars_decode_iq`，实际走协议） |

**可退休性判定**：

- 整体退休 `acars_protocol`：**不成立**。会同时打断 `decoder_registry` 的 ACARS 描述、`acars_decode_iq`（AI 工具/灵敏度脚本）、样本生成与两组往返测试。
- 整体退休 `acars_decoder`：**不成立**。会打断 CLI 生产路径（`decode_router.py:517`）与三个 AI 工具；且帧合成/解调/解析是协议模块没有的独立能力。
- 唯一可考虑的退休候选是 `ACARSDecoder.decode_audio` 状态机路径（改由 `acars_protocol.acars_decode` 承担 `decode_router` 的 ACARS 路由）。**当前条件未满足**，判据如下（需全部成立才可出 patch，patch 放 `docs/pending-patches/`）：

1. **真实采集对拍**：≥3 段真实 ACARS 音频（`.wav`/`.cf32`，含至少 1 段低 SNR）两条路径的 `crc_ok/reg/label/text` 一致。现有 `tests/fixtures/sensitivity/acars.wav` 由 `acars_protocol.acars_encode` 生成（`scripts/make_sensitivity_fixtures.py:109-116`），**不是真实采集**，不能作对拍证据。
2. **调用点改线清单**：仅 `mbdsdr_ai/decode_router.py:499`（import）与 `:517`（调用）改为 `acars_protocol.acars_decode`，保持结果键 `messages`/`num_messages`/`crc_ok_count` 不变；不动 `agent.py`（它已走协议）。
3. **先补测试再删**：为 `ACARSDecoder.decode_audio` 增加自环回归并记录红/绿；删除后同组测试通过项数不得下降（`ACARSMDemod`/`ACARSMessageParser` 仍被工具与测试使用，只能删 `ACARSFrameSynchronizer` 及 `decode_audio` 本身）。
4. **回滚窗口**：`:582-583` 明写「待真实采集的时域对齐问题排查后再切回」；该排查未出结论前保留原生路径，不得按「重复实现」删除。
5. **删除纪律**：单独提交、附改前/改后同组测试通过项数、`git grep` 删除后命中 0。

结论：**不退休、不删生产路径、不出 patch**；本项保持双实现，待判据 1/3/4 满足后再议。

### 5.7 剩余登记项：落库依赖表（2026-11-06）

> 规则：**不要按工作区行号回填**。所有定位以「锁定符号」为准；等 owner 提交后，用
> `git grep -nE '<符号>' HEAD -- <path>` 取 HEAD 行号再改文档。
> 「落库」判据：该文件 `git status` 干净、符号在 HEAD 可命中；claim 到期但没提交**不算**落库（以 `git log`/`git status` 为准）。

**A. 等「操作逻辑线」落库（`desktop/main_window.py` 仍有在途改动；`desktop/module_panel.py` 已于 `7bea149a` 落库）**

| 登记项 | 锁定符号（按它找，不按旧行号） | 落库后要改的定位 | 判据 |
|---|---|---|---|
| ~~B5~~ **已落库**（`7bea149a`） | — | 已按 HEAD 落到 `:88`/§5.1：`module_panel.py:534/541/557/600/395/346-348`、`main_window.py:1635/7400`（后两行待 `main_window.py` 落库后复核） | 四元组 `7bea149a — desktop/module_panel.py:541 — sample_rate_combo_items(device)` 过 R6；全文件无档位字面量 |
| C2 `:98` | `module_panel.py` 中两处 `recorder`（下拉文案 + 注释） | 已定：`module_panel.py:17/373`（HEAD） | 命中恰 2 处且都是文案/注释，非 import |
| C3 `:99` | `baseband_io.write_sidecar`、`baseband_io.load_iq` | `main_window.py:6468`、`:6598`、`:8093-8098` → HEAD（当前 HEAD 为 `6807-6809/6986/7014` 与 `8546`，落库后可能再漂） | 三条都能按符号定位；不得沿用旧行号 |
| A11 `:392` | `def _fmt_dbfs` 及其唯一调用点 | `main_window.py:87`、`:105` → HEAD（当前 def 在 `:108`） | 仍保持 0 生产调用、13 处测试引用；行号命中 |
| §5.4 `:312-315` | `_panels_set_sdr_connected`、`_apply_observer_location`、`_on_anr_enabled`、`last_capability_report`、`set_disconnected`、`set_observer_ready`、`is_anr_enabled` | `:4137`→HEAD `4284`；`:4220-4222`→status 分支；`:837/:4324/:4393`→HEAD `891/4508/4579`；`:8356-8379`→HEAD `8863`；`:8919-8923`→HEAD `9461/9884`；`:7098-7100`→HEAD `7490`；`:6306`→HEAD `6644`；`device_panel.py:296`→HEAD `366` | 四个方法各有 ≥1 处生产调用（C7 验收）；行号命中 |
| §4.3 吞错基线（ERROR 228>227） | `default_freq` 回落 `98_500_000.0`（工作区当前 `3845/3850-3852`；文档写的 `3668-3671` 已第二次漂移） | 落库后按新 hunk 行重算，更新「当前 x～y（原 `3659-3663`）」 | 错误数与落库后的工作区一致；**不上调基线** |

**B. 无他线在途依赖的开放项（不混入 A 表等待队列）**

| 项 | 现状 | 解锁条件 / 归属 |
|---|---|---|
| C7 其余 4 方法（`submit_frame`、`set_default_params`、`attach_host`、`clear_objects`） | 保留、贴「待接线」 | 产品决定接线；两个版本仍无调用再评估删除，删除须单独提交并附测试前后数 |
| A9 三套错误码常量 | 保留待合并 | 先确认无外部脚本按名导入 `ERR_*`/`REASON_*`（冗余线自查，无他线依赖） |
| A7/A8 短标签与「下一步」 | 保留分层 | 是否改为从 `error_catalog` 派生属产品/文案决定 |
| B7 移动端 4 档 deprecated | 不删 | 外部引用确认 0 + 旧版本回滚窗口关闭（窗口无法从仓库核实，维持不删） |
| §5.3 46 个 library-only 模块 | 已决定不删、不移目录 | **无后续动作**，仅保留清单 |
| A3 证据里的「原位置 `sdr_tools.py:3057`」 | 文案审计线正在改 `sdr_tools.py`（claim 在途） | 这是**历史位置**语义，不追行号；只需 `f529ba62` 的四元组证据不变 |

**C. 当前即可校正（无 claim、HEAD 已定）**

| 定位 | 现状 | 处理 |
|---|---|---|
| `desktop/device_panel.py:296` | HEAD 已为 `:366`（`def last_capability_report`） | 无 owner 占用，可随时改；建议与 A 表 §5.4 同批，避免再漂 |

---

### 5.8 死代码扫描：全仓公开函数/类（2026-11-06，**只登记不删**）

扫描口径（基线 HEAD `ce561bcb` 的工作区快照，含他线在途改动）。2026-11-06 按 HEAD `e4810574` 复核：17 条零命中符号 `git grep -w` 仍仅定义行 1 处；对 `ce561bcb..e4810574` 的 16 个变更 `.py` 做增量核对，新增公开定义均有引用、无净删除，116 计数不变。
- 范围：`mbdsdr_ai/`、`desktop/` 的顶层公开 `def`/`class`（不含 `_` 前缀）；`scripts/`、`tools/`、`experiments/`、`tests/` 内的定义不计入候选（脚本入口/测试夹具）。
- 引用判定：全仓（`.py`/`.dart`/`.cpp`/`.h`/`.md`/配置文件）整词匹配，排除定义行自身；零命中项再用 `git grep -n -w <symbol> HEAD` 复核，必须全仓只有定义一行。
- 已排除：`__all__` 导出（候选里 `all=1` 为 0 条）；生产代码中按名取用的 `getattr`/`importlib`（未发现能命中这些符号名的形态，§1.2 那几类动态调用已单独核过）；`connect(self.x)` 这类信号槽属正常标识符引用，已计入。
- 结果：候选 **116** 项 = 确认无用 10 / 动态调用存疑 7 / 上游 API 保留 99。**本清单不计入 §0 的 41 项，处置一律"登记待批"，不删任何代码。**

#### 5.8.1 确认无用（10 项）——分级与 patch 计划见 `docs/pending-patches/2026-11-06-deadcode-unused-symbols.md`

| 分级 | 位置 | 符号 | 删了谁可能受影响 | 删除判据（摘要） |
|---|---|---|---|---|
| 需先补测试再删 | `desktop/rf_sky_view.py` | `load_ground_station_config` | 环境变量+配置回退的地面站策略；当前"有写入、无人读"，直接删会抹掉该集成缺口 | 先补"未配置/非法坐标→`None`"行为测试，并核对写入侧口径 |
| 真可删 | `mbdsdr_ai/adsb_lite.py` | `build_short_frame` | 无调用方；短帧不在 `decode_baseband` 主链 | adsb 3 个测试文件全绿（2026-11-06 删除判据快照，未在现树复核） |
| 真可删 | `mbdsdr_ai/astronomy/moon_albedo.py` | `grid_resolution_pix_per_deg` | 一行委托 `albedo_meta`，月面几何走 `screen_to_disc`/`albedo_at_disc` | `tests/test_moon_albedo.py` 全绿（2026-11-06 删除判据快照，未在现树复核） |
| 真可删 | `mbdsdr_ai/dab_plus_lite.py` | `audio_info_from_service` | 未接线的音频信息构造入口，FIC 解析链不受影响 | `tests/dab_plus_test.py` 全绿（2026-11-06 删除判据快照，未在现树复核） |
| 入口角色不动 | `mbdsdr_ai/decoder_registry.py` | `select_decoder` | 注册表"按模式选择"公开入口；可能被外部/CLI 按名调用 | 不删（零命中不构成删除许可） |
| 入口角色不动 | `mbdsdr_ai/fengyun_sat.py` | `fy4_lrit_pipeline` | FY-4 LRIT 流水线入口；零件有测试 ≠ 入口可删 | 不删 |
| 真可删 | `mbdsdr_ai/ft8_decode.py` | `hard_tones_to_llr` | FT8 主链用 `soft_tones_to_llr`，硬判决仅离线实验 | 3 个 FT8 测试文件全绿（2026-11-06 删除判据快照，未在现树复核） |
| 需先补测试再删 | `mbdsdr_ai/gps_ca.py` | `nav_bits_from_corr` | 导航比特提取（含门限策略）无测试钉扎 | 先补已知 C/A 相关向量对拍 |
| 真可删 | `mbdsdr_ai/meteor_sat.py` | `convolutional_interleave` | LRPT 主链不复用该交织器 | `tests/test_apt_meteor_roundtrip.py` 全绿（2026-11-06 删除判据快照，未在现树复核） |
| 需先补测试再删 | `mbdsdr_ai/orbit_determination.py` | `ecef_state_to_keplerian` | ECEF→开普勒根数换算属数值 API，现有 orbit 测试未钉 | 先补已知向量测试（ISS 单点） |

> **执行结果（2026-11-06，HEAD `86ae9807`）**：8 条删除、2 条入口角色加注释保留。
> 真可删 5 条：`build_short_frame` `c2058f36`、`grid_resolution_pix_per_deg` `bdfe445c`、
> `audio_info_from_service` `73e5fa25`、`hard_tones_to_llr` `29f2fbda`、`convolutional_interleave` `46b6d437`。
> 补测试再删 3 条：`ecef_state_to_keplerian` 基线 `bf7370bc` + 删除 `24d20bba`；
> `nav_bits_from_corr` 基线 `0703da1b` + 删除 `3c618811`；
> `load_ground_station_config` 基线 `326c902b` + 删除 `3539e6b9`。
> 入口角色 2 条：`select_decoder` / `fy4_lrit_pipeline` 保留注释 `e645b781`。
> 每条删前/删后测试数与「零引用证据」见各提交说明；删后 `git grep -w` 代码命中均为 0。
> 补测试发现的现状缺陷（`ecef_state_to_keplerian` 的 argp/ν 公式、退化轨道除零；
> `load_ground_station_config` 的 `MBDSDR_GS_*` 唯一实现）已随删除记录在提交说明中。

> **文档侧收尾（2026-11-06）**：本轮 8 个删名（`build_short_frame`、`grid_resolution_pix_per_deg`、
> `audio_info_from_service`、`hard_tones_to_llr`、`convolutional_interleave`、`nav_bits_from_corr`、
> `ecef_state_to_keplerian`、`load_ground_station_config`）连同 `MBDSDR_GS_*` 环境变量名，在
> `docs/**/*.md`（含 `docs/learn/data-inventory*`、`docs/learn/capability-map.md`）中 **0 命中**——
> 唯一命中就是本表与 `docs/pending-patches/2026-11-06-deadcode-unused-symbols.md` 自身。
> 因此无需向其它 owner 出交接单。

#### 5.8.2 动态调用存疑（7 项）——确认手段与结论

确认手段（全部已执行）：① 字符串/配置：扫 `*.json/*.yaml/*.toml/*.ini/*.cfg/*.conf/*.txt`，7 个名字 **0 命中**；② `getattr`/`importlib`/`eval`/`exec`/`globals()[...]`：生产代码中无按这些名字取用的形态；③ Qt 字符串 connect：无 `connect("...")` 形态；④ `git grep -w`：全仓仅定义行。

| 位置 | 符号 | 结论 | 依据 |
|---|---|---|---|
| `mbdsdr_ai/ax25.py` | `APRSWeather` | **确认无用** | 仅 `docs/audit_r2/14_ax25_aprs.md` 描述"能发不能收"，类未被实例化；无字符串/配置引用 |
| `mbdsdr_ai/ft8_ldpc.py` | `get_colorder` | **确认无用** | `docs/learn/wsjtx.md:244` 记"读了 colorder 文件但没人用"；`docs/audit_r2/08_ft8_full.md:36` 称 `reorder_to_ldpc` 调用它，但 HEAD `mbdsdr_ai/ft8_decode.py:74 reorder_to_ldpc` 已不调用（`git grep -w` 无命中），该审计记述已过期；主链不用 |
| `mbdsdr_ai/new_spacetime.py` | `GNSSDataSource` | **保留（无法确认）** | `docs/audit_r2/28_astronomy_gnss.md` 记为空壳，但 `docs/learn/serial_gnss_ntrip.md` 声明串口 GNSS 接入；`desktop/new_spacetime_panel.py` 仍在途（§5.7） |
| `mbdsdr_ai/new_spacetime.py` | `SDRDataSource` | **保留（无法确认）** | 数据源插件，存在按名注册的可能；等他线面板落库后再判 |
| `mbdsdr_ai/new_spacetime.py` | `format_dms` | **确认无用** | `docs/audit_r2/26_new_spacetime.md:174-175` 明记全仓无人调用；纯显示辅助 |
| `mbdsdr_ai/new_spacetime.py` | `parse_nmea_sentence` | **保留（无法确认）** | `docs/learn/serial_gnss_ntrip.md:43` 声明为串口 GNSS 分发入口，接线未落地 |
| `mbdsdr_ai/new_spacetime.py` | `web_mercator_project` | **确认无用** | `docs/audit_r2/26_new_spacetime.md:174-175` 明记全仓无人调用；纯投影辅助 |

> 结论：4 条落"确认无用"（`APRSWeather`、`get_colorder`、`format_dms`、`web_mercator_project`），可并入删除 backlog；3 条落"保留"（GNSS/SDR 数据源与 NMEA 入口），等 `new_spacetime_panel` 落库后重判。删除 `format_dms`/`web_mercator_project` 前须确认落库版本的面板未新增调用。

#### 5.8.3 上游 API 保留（99 项，按文件分组）

保留口径：模块属 §5.3 library-only（①/②/③）、对外适配器（`*_adapter`/`*_source`/外部工具对接）、或已被测试钉住（删了会让测试失去被钉对象）。

| 文件 | 项数 | 符号 |
|---|---|---|
| `desktop/error_remedy.py` | 2 | `mapping_overlaps`, `no_entry_reason` |
| `desktop/layout_store.py` | 1 | `reset_layout_store_singleton` |
| `desktop/rf_sky_view.py` | 2 | `RFSkyViewPanel`, `satellite_catalog_mismatches` |
| `mbdsdr_ai/acars_decoder.py` | 2 | `frame_bits_from_bytes`, `synthesize_msk_audio` |
| `mbdsdr_ai/adsb_lite.py` | 1 | `synthesize_modes_iq` |
| `mbdsdr_ai/ais_decoder.py` | 4 | `build_type18`, `build_type24a`, `nmea_decode`, `nmea_encode` |
| `mbdsdr_ai/analysis/protocol_parser.py` | 1 | `infer_field_boundaries` |
| `mbdsdr_ai/ardop_adapter.py` | 2 | `ARDOFFrame`, `demodulate_qam` |
| `mbdsdr_ai/astronomy.py` | 3 | `FrameType`, `get_observer_from_gps`, `mjd_to_jd` |
| `mbdsdr_ai/astronomy/moon_albedo.py` | 3 | `albedo_at_disc`, `screen_to_disc`, `selenographic_to_disc` |
| `mbdsdr_ai/audio_resampler.py` | 1 | `resample_audio` |
| `mbdsdr_ai/dab_plus_lite.py` | 2 | `crc_fire_code`, `dabplus_samplerate` |
| `mbdsdr_ai/decode_pipeline.py` | 2 | `benchmark_pipeline`, `sweep_frequency_offset` |
| `mbdsdr_ai/decode_router.py` | 1 | `capability_matrix` |
| `mbdsdr_ai/digital_modes.py` | 1 | `demodulate_qpsk` |
| `mbdsdr_ai/dmr_demod.py` | 4 | `dmr_decode_iq`, `dmr_encode_iq`, `dmr_encode_voice_lc_header`, `qr1676_encode` |
| `mbdsdr_ai/dsdcc_lite.py` | 1 | `MBEParams` |
| `mbdsdr_ai/dsp.py` | 3 | `DSPChain`, `QuadratureDemod`, `make_agc2` |
| `mbdsdr_ai/fengyun_sat.py` | 7 | `bits_to_bpsk`, `bpsk_to_bits`, `build_fy3_hrpt_frame`, `build_fy4_image_information`, `build_fy4_xrit_file`, `extract_plframe`, `fy3_extract_channels` |
| `mbdsdr_ai/fldigi_modes.py` | 2 | `OliviaMFSK`, `ThorMode` |
| `mbdsdr_ai/frequency_manager.py` | 1 | `get_mode_params` |
| `mbdsdr_ai/frequency_manager_v2.py` | 1 | `FrequencyManagerV2` |
| `mbdsdr_ai/fst4_ldpc.py` | 1 | `fst4_decode` |
| `mbdsdr_ai/gk2a_lrit.py` | 3 | `build_gk2a_lrit_file`, `cadu_to_iq`, `vcdus_to_cadu` |
| `mbdsdr_ai/gpredict_adapter.py` | 1 | `jd_from_datetime_utc` |
| `mbdsdr_ai/gps_ca.py` | 1 | `ca_code_taps` |
| `mbdsdr_ai/hal.py` | 1 | `DeviceCapability` |
| `mbdsdr_ai/hooks.py` | 1 | `create_auto_record_hook` |
| `mbdsdr_ai/js8call_adapter.py` | 1 | `tones_to_audio` |
| `mbdsdr_ai/kismet_adapter.py` | 1 | `build_beacon_frame` |
| `mbdsdr_ai/m17_adapter.py` | 2 | `golay24_encode`, `m17_scramble` |
| `mbdsdr_ai/meteor_sat.py` | 1 | `lrpt_find_frames` |
| `mbdsdr_ai/module_graph.py` | 1 | `enumerate_sources` |
| `mbdsdr_ai/multimon_decoders.py` | 4 | `PocsagWord`, `pocsag_build_batch`, `pocsag_encode_address_word`, `pocsag_encode_message_word` |
| `mbdsdr_ai/nrsc5_lite.py` | 1 | `HDCDecoder` |
| `mbdsdr_ai/op25_adapter.py` | 1 | `p25_imbe_pack_params` |
| `mbdsdr_ai/openapi_integration.py` | 1 | `get_satellite_passes` |
| `mbdsdr_ai/openwebrx_adapter.py` | 2 | `owx_apply_waterfall`, `owx_soapy_settings_parse` |
| `mbdsdr_ai/orbit_determination.py` | 1 | `plot_residual_histogram` |
| `mbdsdr_ai/osmosdr_source.py` | 2 | `SoapySDRSource`, `osmosdr_list_backends` |
| `mbdsdr_ai/radio_control.py` | 2 | `cw_decode_from_audio`, `text_to_cw_audio` |
| `mbdsdr_ai/rds_lite.py` | 1 | `build_4a_group` |
| `mbdsdr_ai/remote_control_v2.py` | 1 | `RemoteControlV2` |
| `mbdsdr_ai/rtcm3_decoder.py` | 1 | `build_rtcm3_frame` |
| `mbdsdr_ai/rtklib_adapter.py` | 3 | `SatelliteOrbit`, `klobuchar_ion_delay`, `ntrip_manager_from_config` |
| `mbdsdr_ai/rtl433_decoder.py` | 1 | `manchester_encode_bits` |
| `mbdsdr_ai/rtlsdr_params.py` | 1 | `get_gain_table_tenths` |
| `mbdsdr_ai/sat_image_processing.py` | 1 | `channel_algebra` |
| `mbdsdr_ai/sat_pipeline_params.py` | 1 | `list_pipelines` |
| `mbdsdr_ai/sat_pipeline_runner.py` | 1 | `run_pipeline` |
| `mbdsdr_ai/satdump_adapter.py` | 1 | `circular_orbit_ecef` |
| `mbdsdr_ai/satnogs_adapter.py` | 2 | `ObservationPipeline`, `SatnogsNetworkClient` |
| `mbdsdr_ai/sdrangel_adapter.py` | 3 | `MagAGC`, `NFMDemodSink`, `SSBDemodSink` |
| `mbdsdr_ai/sdrangel_plugins.py` | 1 | `encode_flarm_frame` |
| `mbdsdr_ai/spectrum_accumulator.py` | 1 | `SpectrumAccumulator` |
| `mbdsdr_ai/urh_adapter.py` | 1 | `slice_bits_from_pulses` |
| `mbdsdr_ai/xastir_adapter.py` | 1 | `APRSItem` |
| `mbdsdr_ai/ysf_demod.py` | 3 | `ysf_decode_iq`, `ysf_encode_data_frame`, `ysf_encode_iq` |

> 说明：本清单只给"谁没有调用方"，**不给删除许可**。删除须另开 patch + 回滚窗口结论；`select_decoder`/`fy4_lrit_pipeline`/`GNSSDataSource`/`SDRDataSource` 这四项属"入口/按名注入"角色，即便零命中也不得直接删。

### 5.9 棘轮提案「未引用公开符号只降不升」：**本轮不实施**

**判断：现在加会天天误红，先不加。** 理由三条：

1. 口径不稳：116 项里 99 项是"上游 API 保留"（library-only 模块/适配器/测试钉住）；是否把 `docs/`、配置、字符串算引用，一改口径数字就跳变，棘轮会变成"改基线"比赛。
2. 在途未接线是常态：`desktop/new_spacetime_panel.py` 仍在工作区 M，§5.8.2 的 3 个符号会被它接线；§5.7 A 表还有 6 项等他线落库。此时"只降不升"会惩罚正常的先写后接。
3. 新函数天然先有定义后接线：若每次新增都上调基线，棘轮就失去约束力。

**若将来要加，最低设计（三条必须同时满足）：**
- 只对"确认无用"类计数（不含 API 保留/存疑），基线 `docs/deadcode-baseline.json` 记 `{head, count, entries}`；
- 豁免通道：定义处 `# deadcode: pending-wire <owner>` 注释（脚本跳过），或能力表/注册表里的登记字符串；**只有无标注的新符号才报警**；
- 过渡期：允许同提交上调基线，但 commit message 必须写明新增符号与接线计划；CI 只拦"基线未更新且计数上升"。

**结论**：等 §5.7 A 表清零、`new_spacetime_panel` 落库后重新评估；在此之前 §5.8 只作为人工清单维护。

#### 5.8.4 已删代码的已知缺陷（重写时不得复原）

删除前用「先补测试再删」逼出来的实现缺陷。**这些函数已不在 HEAD**，此处留档的唯一目的是：
日后若重写同一能力，必须按正确公式实现，**不得把原实现照抄回来**。

| 原符号 | 原位置（删除前） | 基线提交 / 删除提交 | 已证实的缺陷 |
|---|---|---|---|
| `ecef_state_to_keplerian` | `mbdsdr_ai/orbit_determination.py:796` | 基线 `bf7370bc` / 删除 `24d20bba` | ① `argp` 的 `atan2` 把**未归一化的 `e_vec`** 与 `cos ν` 混作两个分量（正确做法：`atan2(dot(n̂, ê), dot(ê, r̂))`，`ê` 必须归一化）；② `ν = atan2(cos ν, sin ν)` **参数颠倒**；③ 已知根数 `a=7000 km, e=0.01, i=51.6°, Ω=30°, ω=60°, ν=45°` 实测 `argp=0.405°`、`M=45.2°`（应为 60° / 45°），`a/e/i/Ω` 正确；④ 退化轨道（ECEF 下随地球自转、ECI 静止，`h=0`）直接抛 `ZeroDivisionError`，无 NaN/显式报错分支。 |

**重写约束（写死）**：若日后重写 ECEF→开普勒，必须按正确公式与归一化实现，并补
「已知根数 → ECI → ECEF → 还原」的闭环数值测试（含 `e>0` 与退化轨道两类用例），
**不得复原此实现**。

## 5.10 双模块实例（裸导入 + 包导入）—— 2026-11-11 新增

> 定性：**多真值源（运行时副本）**。与 §2 各条的“同一事实写两处”不同，这里同一份源码被
> Python 加载成**两个互不相干的模块对象**，各自的模块级变量是两份。不计入 §0 的 41 项口径。
> 本条**只出结论，未改代码**。

### 5.10.1 成因（不是谁写错了，是两处垫路径叠出来的）

- `desktop/main.py:21/35/36` 把 `desktop/` 与仓库根先后塞进 `sys.path`；
- `desktop/__init__.py` 的“双上下文垫片”又把 `desktop/` 垫一次，目的是让
  `from desktop.ai_panel import …` 能解析同级裸导入；
- `tests/conftest.py:44-46` 同样把**仓库根 + desktop/** 都垫进去。

于是同一进程里 `import themes`（解析到 `desktop/themes.py`，模块名 `themes`）与
`import desktop.themes`（同一文件，模块名 `desktop.themes`）会各执行一遍模块体。
实测（本机，仓库根 + desktop 同时在 path）：

```
themes             | same object: False | themes.py  themes.py
shortcuts          | same object: False | shortcuts.py shortcuts.py
constants          | same object: False | constants.py constants.py
settings           | same object: False | settings.py settings.py
spectrum_processor | same object: False
new_spacetime      | same object: False
```

`themes` 的后果已由用户实测：用 `desktop.themes` 设降档 → `_FONT_SCALE_NOTE` 写进 A 副本，
面板从 B 副本读 → **提示永远不显示**（沉默失效，不报错）。

### 5.10.2 全仓盘点（29 个模块同时存在两种导入形态）

| 模块 | 裸导入 / 包导入（文件数） | 可变模块级状态 | 后果 |
|---|---|---|---|
| `desktop.themes` | 8 / 0（包名形态运行时可构造） | `_FONT_SCALE_OVERRIDE`、`_FONT_SCALE_NOTE` | **沉默失效（已实测）**：写入一份、读取另一份 |
| `desktop.shortcuts` | 7 / 0 | `_OVERRIDES` | **同型沉默失效**：用户改键覆盖写一份、面板读另一份 → 自定义静默丢失（未单独复现，机制与 themes 相同） |
| `mbdsdr_ai.new_spacetime` | 15 / 82 | `_global_engine` | **假绿风险最高**：两份 `TimeEngine`，桌面走包名、`mbdsdr_ai` 内部走裸名 → 时间基准分裂、测试可因副本不同而绿 |
| `mbdsdr_ai.orbit` | 8 / 15 | `_TLE_FETCHER` | 两份抓取器/缓存：重复取数、缓存不同步（沉默，非致命） |
| `mbdsdr_ai.time_basis` | 8 / 3 | `_guard` | 两份时钟守卫：保护/告警各算一份（沉默，非致命） |
| `desktop.spectrum_widget` | 55 / 6 | `_BANDPLAN_CACHE` | 缓存双份：内存浪费 + 可能读旧 bandplan（沉默，非致命） |
| `desktop.main_window` / `rf_sky_view` / `control_panel` 等 11 个 UI 模块 | 4–208 / 2–39 | 无 `global` 可变，但定义 **Qt 类** | **类型身份分裂**：两份同名类对象，跨副本 `isinstance` / 信号连接互不识别（当前未触发，属潜在） |
| `mbdsdr_ai.tool_registry` | 2 / 126 | 注册表（函数式写入，无 `global`） | 裸形态当前 `ImportError`（模块内相对导入）→ **暂不形成双实例**；一旦有人在 `mbdsdr_ai/` 目录内跑脚本即分裂 → 工具注册到一份、另一份看不到（沉默丢失） |
| `mbdsdr_ai.spectrum_processor`、`decoders`、`sat_passes`、`serial_gnss`、`hal`、`gnss_monitor`、`tle_integrity`、`new_spacetime_tle`、`builtin_tle_meta`、`audio_resampler` | 1–15 / 2–44 | 无可变状态（纯函数/常量） | 无害，但模块体重复执行、内存翻倍 |
| `desktop.constants`、`desktop.settings` | 26 / 1、21 / 0 | 无可变状态 | 无害（两份同值）；`settings` 若日后加缓存，立即升级为沉默失效 |
| `mbdsdr_ai.config`、`mbdsdr_ai.sdr_backend` | 0 / 17、0 / 30 | — | **单形态，无副本**（不在风险面内） |

### 5.10.3 治理方案（三选一：选 ①，并配 ③ 作长期规则）

- **① 每个模块顶部做 `sys.modules` 双向别名（选定，立即做）**
  在模块最上方加 6 行：以裸名加载时 `sys.modules.setdefault("desktop.<名>", 当前模块)`；
  以包名加载时若裸副本已存在，则 `sys.modules[__name__] = 裸副本` 并立即返回式复用。
  这样无论谁先加载，后到的名字都拿到同一个对象，模块体只执行一次。
  **选它的理由**：附加式、不改任何一处 import 语句，改动面 = 风险面（今晚在途的
  `main_window.py`/`rf_sky_view.py` 一行都不用碰）；可逐模块上线、可单独 revert；
  双向生效，脚本上下文（`python desktop/main.py`）与包上下文（pytest/工具）都被覆盖。
  配套一条棘轮测试：固定清单逐个断言 `import X is import 包.X`，回归即红，不再沉默。
- **② 全仓统一成包式导入、删掉裸导入路径（否决）**
  要改 600+ 处 import，且今晚这些文件正被他线高频编辑 → 冲突与回归成本极高；
  更关键的是 `scripts/**`、`mbdsdr_ai/tests/**` 依赖裸导入直接运行，统一后必须给每个
  入口补 `sys.path` 引导，等于把“垫路径”从 3 处扩散到几十处。**改动面 > 收益面。**
- **③ 禁止模块级可变状态，改由单例持有（采纳为长期规则，不在本轮铺开）**
  它不消灭副本，只把“副本分裂”的爆炸半径从“功能静默失效”降成“多一个实例”。
  配合 ① 后收益最大；单独用则 `themes` 这类“状态就长在模块里”的模块依然无解。
  本轮只立规矩：新代码不得新增模块级可变全局；已有 6 处（`themes`/`shortcuts`/
  `spectrum_widget`/`new_spacetime`/`orbit`/`time_basis`）登记在案，随 ① 落地后逐条迁移。

**执行顺序（待授权）**：① 先做 6 个带可变状态的模块 → 再补 UI/纯函数模块 → 最后上棘轮测试；
每步单独提交、跑该模块测试，禁一次性全铺。

## 5.11 同一实体、两个可变状态持有者（对象级多真值源）—— 2026-11-11 新增

> 定性：与 §5.10 同族但不同层。§5.10 是「同一文件被加载成两个模块对象」；
> 本条是「同一个事实被两个对象各存一份**可写**状态」。
> 判据不是「代码看起来重复」，而是：**能否从两个入口独立改变该事实，且没有任何一方被强制同步**。
> 本轮只出结论，**未改任何代码**；卫星闸门修复属他线提交（`94ab10cd`），本表只登记其**残留**。
> 行号会随工作树漂移，检索以 `path::符号` 为准；下文行号取 2026-11-11 夜工作树。

### 5.11.1 判定标准（先立规矩，再看清单）

**合理（允许两个持有者）** —— 须同时满足：

1. **单一写入口**：只有一个对象能改；另一个是只读派生（每次从真值重算）或显式投影
   （写方整份 push，读方不保留差异）。
2. **有失效协议**：要么每次读都重算，要么写方广播一次全量；不存在「各带 TTL、互相不可见」。
3. **读取方不参与控制决策**：只用于显示，可容忍一拍旧值。
4. **外部可变来源只有一个读取入口**（磁盘文件 / 环境变量不得被多个面板各自直读）。

**必然出事（必须收敛）** —— 命中任一条：

1. **两个写入口**：两侧都能被独立修改（`themes._FONT_SCALE_OVERRIDE`、`dev["_sample_rate"]`）。
2. **写 A 读 B**：写入方与决策方不是同一个对象（tracker 闸门读天空图侧）。
3. **各带缓存/TTL**：同一份数据两份内存副本，失效互不可见（`orbit._TLE_CACHE` 24h
   与 `sat_tracker._catalog`）。
4. **用副本校验/回滚主副本**：UI 先接受请求值、失败再手工回滚（采样率 4 处回滚）。
5. **副本绕过真值源直读盘**：面板自己读 `gui_config.json` 并回填（ADS-B 每 5s）。

**灰色**：两份都可写但生命周期极短、退出即全量重建（对话框草稿）——登记，不急着动。

### 5.11.2 清单（按严重度）

| 实体 | 持有者（path::符号） | 可变 | 后果 | 级别 |
|---|---|---|---|---|
| 卫星 TLE 目录 / 站址 / 选中星 | 天空图侧 `desktop/main_window.py::MainWindow.sat_tracker`，其目录在 `desktop/rf_sky_view.py::SatelliteTracker._catalog`；执行侧 `MainWindow._loop_sat_tracker`，其目录在 `mbdsdr_ai/sat_tracker.py::SatelliteTracker._catalog` | 是 | 闸门已改读执行侧（`94ab10cd`）；但天空图 `refresh()` 只用自持 `_catalog`，`_reload_catalog()` 仅在 `__init__` 调一次，「更新 TLE」（`MainWindow._on_update_sat_tle`）只写执行侧 → 同一会话内天空图星历与执行/调谐不一致（沉默，不报错） | **P1**（原 P0） |
| 同一批 TLE 的三份内存状态 | `mbdsdr_ai/orbit.py::_TLE_CACHE`（`fetch_tle` 默认 24h）／`mbdsdr_ai/sat_tracker.py::SatelliteTracker._catalog`／磁盘 `~/.mbdsdr/tle_cache`（`orbit._CACHE_DIR` 与 `sat_tracker._tle_cache_dir` 同目录） | 是 | 只有磁盘共享；刷新落盘后另一侧内存仍读旧值（最长 24h），无失效广播 | **P1** |
| 星历计算实现 ×3 | `desktop/rf_sky_view.py::SatelliteTracker`／`mbdsdr_ai/sat_tracker.py::SatelliteTracker`／`mbdsdr_ai/satellite/tracker.py::SatelliteTracker`（第三个由 `rf_sky_view::RFSkyView._build_tune_tracker` 每次构造） | 否（无状态） | 同一 az/el 三条代码路径，口径漂移不报错；判断「哪颗可跟踪」可能三个答案 | P2 |
| 字体降档状态 | `desktop/themes.py::_FONT_SCALE_OVERRIDE` / `_FONT_SCALE_NOTE`，双模块实例（§5.10） | 是 | 已实测：写 A 读 B，提示永不显示 | **P0** |
| 快捷键覆盖 | `desktop/shortcuts.py::_OVERRIDES`，双模块实例 | 是 | 同型：改键写一份、面板读另一份 → 自定义静默丢失 | **P1** |
| 时间基准 | `mbdsdr_ai/new_spacetime.py::_global_engine`（`get_time_engine()`），双模块实例 | 是 | 两份 `TimeEngine`：跟踪、过境、测试可能不同基准；测试可因副本不同而绿（假绿） | **P1** |
| 采样率 | 真值：`mbdsdr_ai/sdr_backend.py::SDRBackend._applied_sample_rate_hz`（驱动回读）；副本：`desktop/control_panel.py::ControlPanel.set_sample_rate` 下拉、`desktop/spectrum_widget.py::SpectrumState.sample_rate_hz`（每帧覆写）、`dev["_sample_rate"]`（`device_panel.py::DevicePanel._on_connect_clicked` / `module_panel.py::ModulePanel._emit_connect`）、`DesktopSettings` 的 `sample_rate_hz` | 是 | 真值唯一但需 4 处手工同步；失败回滚散在 `MainWindow` 采样率分支。漏一处即显示、录制段头、设置三份口径分裂 | **P1** |
| 中心频率 | 后端值 / `SpectrumState.center_freq_hz`（每帧覆写）/ `settings.frequency_hz` / 状态栏与 VFO 显示 | 是 | 调谐失败有回滚路径，回滚期间三处短暂不一致；显示值本身是合法投影 | P2 |
| 观测站坐标 | `MainWindow._observer_lat/_observer_lon`／`~/.mbdsdr/gui_config.json`／`read_config_site()` 镜像／`desktop/adsb_map_panel.py::AdsbMapPanel._refresh_observer_from_config`（每 5s 直读盘）／`new_spacetime_panel` 内 `_observer_lat` 副本／`rf_sky_view` 内部 observer | 是 | 代码自认「这些面板以前各读各的盘」（`MainWindow._push_site_to_panels` docstring）。ADS-B 距离列可能与主窗不同站；外部改 gui_config 后主窗不重载 | **P1** |
| 设备连接参数（采样率/增益/PPM/带宽） | ~~`ModulePanel._devices` 与 `DevicePanel` 各自枚举的 `dev` dict，均写 `_sample_rate/_gain/_ppm` 副本~~ → `desktop/connect_request_store.py`（按设备身份键唯一持有请求） | 是（唯一 owner） | **已收口（2026-11-12，方案 A）**：两面板只在选中时渲染、编辑时写回 store，载荷由 `fill_payload` 统一生成。身份键 serial/uri 为稳定键；空 serial 降级 `PROVISIONAL` 并显式告警；同键撞车加 `#n` 区分并告警（`tests/test_connect_request_store.py` 反证）。残留：无 serial 的 RTL 只能按枚举序号暂记，拔插后参数不跟随设备（已告警，不静默） | P2（已收口） |
| 设备能力报告 | `DevicePanel._last_capability_report` vs 其它面板每次 `build_capability_report()` 重算 | 否（派生） | 合理；但缓存不失效，换设备后旧报告仍可能被展示 | P3 |
| 频段表 | `desktop/spectrum_widget.py::_BANDPLAN_CACHE`（模块级）vs `SpectrumState.bandplan`（实例） | 否（只读） | 无害：同源重复解析；双模块时各一份缓存，内容相同 | 无害 |
| 配置实例 | `desktop/main.py::_boot_settings`（只读快照）vs `MainWindow.settings`（唯一可写实例） | 一写一读 | 合理；若 boot 实例也 `set()`，两个 flush 会整份覆盖（当前未触发） | P3 |

> 另：claim 标签（用户已知）与 §5.10 的模块级副本不在本表重复登记。
> 复现双模块身份：`python -c "import sys; sys.path[:0]=[r'D:\mbdsdr\MBDSDR', r'D:\mbdsdr\MBDSDR\desktop']; import themes, desktop.themes; print(themes is desktop.themes)"`
> 实测 `themes / shortcuts / constants / settings / spectrum_processor / new_spacetime` 均 `same object: False`。

### 5.11.3 治理（承接 §5.10，仍不动代码）

原则：**一个实体一个 owner**；其余只允许「整份投影」或「可重算派生」。三种合法形态：

- **真值持有者**：唯一写入口，docstring 指名。
- **投影**：写方 push 全量，读方不保留差异。
- **派生**：每次重算，或带显式失效。

落地顺序：

1. **卫星（P1）**：天空图 `_catalog` 改为订阅执行侧目录，删自持目录与私有加载入口；
   `orbit._TLE_CACHE` 收进同一 TLE 服务，刷新后显式失效。
   验收：刷新后一拍内天空图与执行侧 epoch 相同；反向测试——只更新一侧必须红。
2. **采样率（P1）**：删 `dev["_sample_rate"]` 的独立写，连接时问后端回读；
   `control_panel.set_sample_rate` 只做显示，回滚改为「回读覆盖」一次完成。
3. **坐标（P1）**：`gui_config` 只留一个读取入口（settings 的 adapter）；
   `adsb_map_panel` 删 5s 直读，改订阅 push。
4. **模块副本（P1）**：`themes` / `shortcuts` / `time_basis` 按 §5.10 方案 ①（`sys.modules` 双向别名）。
5. **P2/P3**：先加「同源断言」测试再收敛；`_last_capability_report` 加失效或直接删缓存。

**红线**：不许把显示层副本改成真值对象（显示层本来就该有副本）；只砍「可写副本」与「第二写入口」。
每条收敛必须带反向测试（两侧对调必须红），否则就是另一种假绿。

## 5.12 发现：`frequency_manager_v2` 生产零实例化（2026-11-12，交冗余线裁定，未动代码）

> 来源：设置三环线在修 `frequency_manager.py` 损坏恢复时旁证发现。**本线只登记，不删代码。**

**一句话现状**：`mbdsdr_ai/frequency_manager_v2.py` 的 `FrequencyManagerV2` 在生产代码里零实例化，只有 `tests/test_frequency_manager_v2.py` 在 new 它——属于「死代码 / 未接线功能」的典型样本，不是「已经合并掉的旧实现」。

**证据（全仓 grep）**：

```powershell
git grep -n "FrequencyManagerV2("     # 仅 tests/test_frequency_manager_v2.py 5 处
git grep -n "frequency_manager_v2"    # 生产侧 0 命中；仅 docs 与测试
```

- `mbdsdr_ai/agent.py` 只实例化 `MemoryStore` / `Guardian` / `WorkflowEngine` / `Scheduler`，没有 v2 频率管理器；
- `desktop/` 全目录 0 命中；
- v2 落盘的 `bookmarks_v2.json` 与生产在用的 `bookmarks.json` 不是同一个文件（后者由 `frequency_manager.py` 与书签面板共用，已是单真值源）。

**与 §5.3 的关系**：§5.3② 已把它归为「实验 / 原型，保留 + 标 `@experimental`」。本节补的是更强的结论：**连原型接线都不存在**，生产用户永远碰不到它。因此它不参与「用户可见的数据丢失概率」排序——设置三环线已据此把它从第一档 7 条排到最后（生产零实例化 = 用户感知为零）。

**移交事项**（冗余线裁定，二选一）：

1. 认定为死代码 → 随下一次删除批次删模块 + 测试，并同步 §5.3② 计数（46 → 45）；
2. 认定为未接线功能 → 保留，补一条接线计划与验收（谁实例化、写哪个文件、与 `frequency_manager.py` 的单真值源关系）；在此之前不得声称它是书签 v2 的落点。

**本线已做**：只登记，不改 `frequency_manager_v2.py`，不动其测试。

## 6. 账本线登记行并入（R1–R20）

> 来源：`D:\mbdsdr\.tmp\account_redundancy_rows.md`（账本线导出 20 行，不是 16 行）。
> 去重规则：同项合并到本表一行；冲突以可复核证据为准（见 6.3）。本表是唯一清单，导出文件不再维护。

### 6.1 去重映射

| 账本行 | 本表落点 | 并入方式 |
|---|---|---|
| R1 频谱双实现 | A1 | 已一致，补 Python 生产调用 `:613` |
| R2 4 档 vs 11 档 | B1 + B2 | 已一致 |
| R3 ACARS 双实现 | **A10（新增）** | 新登；2026-11-06 改判为「两实现并存、入口不止一个、退休条件未满足」，差异与判据见 §5.6 |
| R4 录音库同名 | C1 + C4 | 已一致 |
| R5 桌面录制/回放旁路 | C3 | 行号按当前树校正为 `:6405/:6468/:8105` |
| R6 频率格式化 4 套 | A4 | **冲突**：本表证据为 5 份；裁定见 6.3 |
| R7 dBFS/dBµV 两套 | **A11（新增）** | 新登 |
| R8 错误码文案 3 套 | A6（改判）+ **A7/A8（新增）** | 拆分，见 6.3；实现位置已按 HEAD 更正：`90f5eccc` — `desktop/status_panel.py:157` — `DECODE_ERROR_LABELS`（短标签）、`desktop/troubleshooting.py:249 CODE_NEXT_STEPS`（逐码"下一步"）、`mbdsdr_ai/error_catalog.py:704 ERRORS`（码表/标题/详情/remedy 语义源） |
| R9 错误码常量 3 套 | **A9（新增）** | 新登 |
| R10 `~/.mbdsdr` 路径 | **B10（新增）** | 新登 |
| R11 移动端工具目录快照 | **B11（新增）** | 新登 |
| R12 默认采样率 2.4/2.048 | **B9（新增）** | 新登 |
| R13 Python 录音库/recorder | C1 + C2 | 已一致 |
| R14 `_fmt_dbfs` | **C11（新增）** | 新登 |
| R15 ACARS `unsupported_format` | D1 | 已一致 |
| R16 `not_implemented` | D2 | 已一致 |
| R17 `ai_*` 三键 | C10 | 已一致 |
| R18 `_LEGACY_KEY_ALIAS` | D6 | 已一致 |
| R19 2800k→2880000 | B8 | 已一致，补提交 `dd72ba1c` |
| R20 config_schema 两函数 | **C12（新增，已删）** | 已删 `1c0464b6`（见 §6.2 C12） |

### 6.2 新增登记行

| # | 冗余项 | 类型 | 证据（位置 + 命中数） | 处置 | 风险 |
|---|---|---|---|---|---|
| B9 | 默认采样率 2.4M vs 2.048M | 多真值源（有意分层，账本 R12） | `cpp/src/core/tokens.h:289-301` 明确 2.4M 为刻意选择、不与 Python 2.048 统一；`mbdsdr_ai/sdr_backend.py` 默认 2.4M | **保留**（设计选择） | 统一会改瀑布宽度与抽取链 |
| B10 | `~/.mbdsdr` 配置目录解析分散 | 多真值源（账本 R10） | `git grep -lF ".mbdsdr" -- "*.py"` = **91 个 tracked 文件**；`expanduser` = 69 个文件（含测试与文档串） | 保留，**禁止直接改落点**；需先出迁移/兼容方案 | 符号链接/漫游目录/OneDrive 重定向下解析不同；直接改会让老用户配置"消失" |
| B11 | 移动端工具目录快照 | 过期多真值源（账本 R11） | `mobile/lib/app/tool_catalog.dart:6/37` 注释称桌面 90 个工具；真源为 `cpp/src/ai/tool_schema.cpp:registeredToolSpecs` | 保留为只读参考，改由生成器刷新 | 数字不一致时会展示不存在的工具或漏掉新工具 |
| C11 | `desktop/main_window.py:87 _fmt_dbfs` | 死代码（测试钉住，账本 R14） | 生产调用 0（文件内仅定义行）；`tests/test_copy_units_audit.py` **13 处**引用 | **保留**（删除必删断言，违反"通过项不许下降"） | 删函数即测试失败；要删须同批改断言并单独提交 |
| C12 | `desktop/config_schema.keys_by_storage` / `is_known` | 死代码（账本 R20） | 全仓仅定义行命中；唯一疑似命中是测试函数名 `test_tx_channels_zero_is_known_no_tx`（子串）；无 `__all__`/getattr/importlib | **已删除** `1c0464b6`（8 deletions）；`test_settings_schema + test_desktop_constants` **131→131** | 无；仓库外按名导入的脚本会受影响（已登记） |

### 6.3 冲突裁定（以证据为准）

| 冲突项 | 账本口径 | 本表复核证据 | 裁定 |
|---|---|---|---|
| R6 频率格式化"4 套、可合并" | 4 套，前置为视觉冻结解除 | 实际 5 份：状态栏（≥1MHz 6 位）、VFO（3 位）、`rf_sky_view`（1 位）、`device_capabilities.format_hz`（自适应）、C++ `status_format.h` 三函数；精度不同是功能差异 | **保留分精度，不计入"安全合并"**；只有"视觉冻结解除 + 重跑截图基线"后才可议 |
| R8 错误码"3 套" vs A6"已收敛" | 3 套文案，`error_catalog` 为语义源 | `error_catalog` 含标题/详情/remedy；`status_panel` 短标签与 `troubleshooting` 下一步是**独立文案**，同码不同措辞 | A6 改判为"**部分收敛**"；拆出 A7/A8，保留但登记漂移风险 |
| R1 Python 生产调用 `:613` vs A1 `:1752` | 账本记 `:613` | `:613 advance_peak_tracks` 与 `:1752` 标签生成都在用；`:628` 调 `_detect_peak_bins` | 两者都对，A1 补成**生产 2 处** |
| R19 2800k | 账本已记 2,880,000 | `cpp/src/dsp/device_capabilities.cpp:46` 已为 `2_880_000`，注释写明「真机请求 2,800,000 被设备吸附到 2,880,000（读回 +80,000 Hz）」并指附 M.4 L60（`dd72ba1c` 起，本轮换理由） | B8 维持：档位表写 **2880k**；2800k 只作真机实验请求值 |

---

## 7. 变更记录

| 日期 | 提交 | 变更 |
|---|---|---|
| 2026-11-06 | `c14ac51e` | 删除 4 个全仓零调用死函数（themes/shortcuts/mode_registry/device_capabilities），测试 91→91 |
| 2026-11-06 | `f529ba62` | 采样率显示口径上收内核（`sdr_backend.format_sample_rate` 唯一实现），`sdr_tools`/`constants` 改为派生；53 passed（+1，无下降） |
| 2026-11-06 | `1c0464b6` | 删除 `config_schema.keys_by_storage`/`is_known`（零调用），131→131 |
| 2026-11-06 | （本表更新提交） | 并入账本线 R1–R20（去重后 +10 行，共 40 项）；改判 A6（部分收敛）、A3（已合并）、R6（保留分精度）；修正 §4.3 吞错守卫归属 |
| 2026-11-06 | （本表更新提交） | 新增 A12（`kDbUpperDefault` 常量复用，owner 内核线）；§4.3 逐文件钉住 6 条吞错越线的引入提交与 owner，补 A/B 重编取证格式；§5 落地 B5 能力驱动规格/测试/协调下家、白名单反证记录、46 个仅测试模块三类归档、4 个零调用方法的具体接线点；R19 文案按 `device_capabilities.cpp:46` 现状更新；共 41 项 |
| 2026-11-06 | （本次提交） | A12 状态：**已修** — `ef07c133` — `cpp/src/core/tokens.h:338` — `kDbViewUpperDefault`；拆分后为 `kDbViewUpperDefault=-20`（默认视野）+ `kDbCeilingMax=0`（0 dBFS 满量程硬上限）；10 个目标全绿、未改期望值；反证改回 -20 红项与修复前逐字一致；原建议 `kDbSpinUpperMax=20` 标注**已否决**（会抹掉满量程物理事实） |
| 2026-11-06 | `7bea149a` | B5 **已改**能力驱动（已落库）：`7bea149a` — `desktop/module_panel.py:740` — `sample_rate_combo_items(device)`（基准 `7bea149a`，该行 blame）；`_fill_sample_rate_options`（`desktop/module_panel.py:733`）的档位来自 `desktop/constants.py:51` → `sample_rate_options(device)`，全文件无档位字面量；标签仍走 `format_sample_rate`（`MS/s` 三位小数）。原「5 档硬编码、代码未落地」判定作废。历史：`385d0e15` 的五标签 `MS/s` 文案改动已被派生链取代（`1.440 MS/s` 已不在 HEAD） |
| 2026-11-06 | （本次提交） | A10 `:74` 补四元组 `8213a3d7 — mbdsdr_ai/decode_router.py:499 — ACARSDecoder`（`git show --name-only` 触及 + `log -S` 命中）；R8 `:398` 补可点开实现位置（`desktop/status_panel.py:185 DECODE_ERROR_LABELS` 四元组 `90f5eccc`、`desktop/troubleshooting.py:249 CODE_NEXT_STEPS`、`mbdsdr_ai/error_catalog.py:704 ERRORS`）；§5.7 的 B5/C2 行按 `7bea149a` 落库结果收口 |
| 2026-11-06 | （本次提交） | B1–B4/B6–B8 按 HEAD 逐条复核：行号校正（B2 `:128→:131`、B3 `1442→1501`/`198→205`/`1253→1360`、B4 测试 `:101→:102`、B6 `:16-20→:19-26`、B7 `218-220→217-220`、B8 补 `cpp/src/dsp/device_capabilities.cpp:46`），每条补证据三元组（7 个 hash 均先经 `show --name-only` 反查归属）；新增 §5.8 死代码扫描（116 候选 = 无用 10 / 存疑 7 / API 保留 99，只登记不删）；B 表前补"本账本不引用 23/26"一句 |
| 2026-11-06 | （本次提交） | §5.8.1 十条无用符号分三级（真可删 5 / 需先补测试再删 3 / 入口角色不动 2），patch 计划落 `docs/pending-patches/2026-11-06-deadcode-unused-symbols.md`；§5.8.2 七条存疑跑完四种确认手段后落"确认无用 4 / 保留 3"；§1.1 加数字引用规矩（必须带口径 + HEAD 短哈希）；§5.9 棘轮提案评估结论为"本轮不实施"并写明过渡期豁免设计 |
| 2026-11-06 | （本次提交） | A10 改判：**两实现并存、入口不止一个、退休条件未满足**（`decoder_registry.py:78` 只是一个入口；CLI 生产路径 `decode_router.py:517` 仍走 `acars_decoder` 状态机）；新增 §5.6 差异表（模式/链路/质量/测试/调用方）+ 5 条退休判据，不出 patch、不删生产路径；清单 2 的 A3/C5/C12 摘要行补「见 §X」指针；A3 的 `53 passed` 标注为 2026-11-06 02:21 实跑记录（本轮未复跑） |
| 2026-11-06 | （本次提交） | 新增 §5.7 落库依赖表：A 表 6 项等「操作逻辑线」提交 `main_window.py`/`module_panel.py` 后按 HEAD 校正（B5、C2、C3、A11、§5.4、§4.3）；B 表 6 项无他线依赖（C7 其余 4 方法、A9、A7/A8、B7、§5.3、A3 历史位置）；C 表 `device_panel.py:296`→HEAD `366` 可即改；明确「claim 到期但没提交不算落库」 |
| 2026-11-12 | （本表更新提交） | 新增 §5.12：`frequency_manager_v2` 生产零实例化（`FrequencyManagerV2(` 仅命中 `tests/test_frequency_manager_v2.py` 5 处，`agent.py` 只实例化 MemoryStore/Guardian/WorkflowEngine/Scheduler）。设置三环线只登记、不删代码，裁定权交冗余线（死代码 vs 未接线功能） |
