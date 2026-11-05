# 多智能体协作台账（WorkBuddy × TRAE）

> **本文件是并行干活的唯一协调面。开工第一件事读它，收工前更新它。**
> 谁办啥事、手上在途什么、下一件干什么、边界在哪——全在这，别靠猜。
>
> 会话约定：**WorkBuddy 会话 = 「我」**（协议真值/CI/台账线），**TRAE = 「翠」**
> （构建打包/新功能线）。两边都会读写本文件。

---

## 一、分工边界（谁办啥事）

### WorkBuddy 会话（我）
- **协议真值 / golden 对拍线**：FT8、ADS-B 等解码链的外部真值验证、跨实现
  对拍、fixture 管理（血规：代码与金样必须同一提交走）
- **CI 红灯清盘 + 钉扎契约**：typing 守卫、控件盘点快照、漂移红归因、
  tests/(A)(B)/mbdsdr_ai 基线维护
- **审计台账口径**：capability-map / settings-surface-audit /
  cpp-tool-ui-reverse-audit 的销账与 not_verified 如实记录
- **运行库布防 / 弹窗根治**：构建树 DLL 部署（`deploy_win_runtime`）、
  裸跑零弹窗验收
- 本协调台账 + 会话记忆维护

### TRAE（翠）
- **C++ 构建/打包线**：CMake 重构（含 `deploy_win_runtime` 模块化拆分）、
  ctest 全量收口、Qt 插件/辅助运行库部署
- **便携包交付线**：`package_windows.ps1` + windeployqt（注意：旧 zip 曾是
  RTL-SDR **stub 构建**、USB 直连没编进去——交付前必须验真机构建）
- **新协议/功能线**：DVBS2、FT4/WSPR、GPS 测向、TC-CLTU 等新能力开发
- **C++ UI / 功能开发**：星象仪等

> **边界原则：谁在途谁所有。** 对方有未提交编辑的文件一律不碰；
> 共有文件（如 `cpp/CMakeLists.txt`）按 **hunk 归属**拆分提交，只提交自己的 hunk。

---

## 二、当前在途（2026-11-03 22:40 更新）

### 我这边
- **LRPT 图像链破层批**（本地全绿，待提交推送）：`mbdsdr_ai/lrpt_image.py`
  新模块（VCDU→M_PDU→空间包→MCU→RGB，结构真值=TU Berlin 同录音
  第三方解码器逐行核对）+ **比特约定第二处分叉裁决**（真录 VCDU 重组
  必须 woven/CCSDS 往返切分，`decode_cadu(order=...)` 参数化）+
  图像金样 6 项。真录图像已出图（40-80s：38 包/13 行；全过境：49 包/
  17 行/551 块，产物在 D:/mbdsdr/output/real_ota/）。真录金样文件
  现 18 项 ~3.5 min/轮。**注意**：远端 71881a0/27b8b69 是天空模式会话
  的提交，本批需变基后再推。

### TRAE 那边（观察到的在途，未打扰）
- `scripts/smoke_exe_no_popup.ps1`（??）——弹窗冒烟脚本在途
- CMake 布防模块化（deploy_runtime.cmake）已落（1b675e7）
- 便携包线待收（windeployqt / package_windows.ps1）
- 天空模式会话：71881a0 摄像机概念/卫星锁定坐标系 + 27b8b69 工作流重构

### 星象仪/彩带会话（本窗口，2026-11-03 登记）
- **已收工**（按时序）：
  1. SDR++ 频段彩带 v2 补强（`dc62be0`，已推）；
  2. A3 删除预制工作流与触发短语（`27b8b69`，已推）——`_preset_*` ×6 与
     `match_trigger`/`workflow_trigger_match` 全清，引擎只服务用户/AI 自定义
     JSON，回归锁 `tests/test_workflow_engine_no_presets.py`；
  3. A1 摄像机概念与「卫星锁定」坐标系（本提交）——`rf_sky_view` 新增
     `_camera_lock`（Stellarium StelMovementMgr::flagTracking 语义：每 33ms
     tick 真 TLE+sgp4 重算目标 az/el，k=0.25 指数趋近贴视图中心；拖拽/
     双击/触屏/重置/反选/落地/数据失效全路径解锁），「📷 摄像机」按钮 +
     锁定 HUD + 术语统一（重置视角→重置摄像机）；
     B2 TLE 可追溯：目录条目带 tle_source/tle_epoch 进信息卡，
     `get_satellite_pass`/`PassInfo` 加同名字段，`orbit.tle_epoch_iso` 单一
     来源；回归锁 `tests/test_sky_camera_lock.py`（21 项）。
- **所有物**：`desktop/bandplan.json`、`desktop/spectrum_widget.py`、
  `tests/test_spectrum_bandplan.py`、`desktop/rf_sky_view.py`、
  `mbdsdr_ai/workflow_engine.py`、`mbdsdr_ai/agent.py`（工作流注册段）、
  `mbdsdr_ai/scheduler.py`（文档串）、`mbdsdr_ai/core/controller.py`、
  `mbdsdr_ai/orbit.py`、`tests/test_workflow_engine_no_presets.py`、
  `tests/test_sky_camera_lock.py`
- **禁区（对方在途，绝不碰）**：`cpp/CMakeLists.txt`、`scripts/package_windows.ps1`、
  `cpp/cmake/*`、`scripts/smoke_exe_no_popup.ps1`、`docs/WINDOWS_POPUP_PLAYBOOK.md`、
  `.github/workflows/ci_windows.yml.disabled`、各 build 树、批量 exe 冒烟；
  不跑有声/弹窗的东西
- **观察到的 CI 红（65eea2d，均非本会话引入）**：
  1. ~~cpp/ctest `engine_integration`（slow）~~ **已销账（fbbe5c8）**——
     本地单跑 3 次全过、全量 ctest 162/162 绿，判定 CI runner 环境特有
     flaky，非代码真错；
  2. ~~tests/(B) `test_identity_redline` 误伤~~ **已修（fe43c74，WorkBuddy）**——
     49dc00d 入库的金样 `tests/golden/dvbs2_ldpc_vectors.json` 里 SHA-256
     摘要十六进制串内嵌 11 位号码形态（摘要巧合，非 PII）；扫描器学会忽略
     长 hex/base64 token 内的数字巧合（散文里的真号照咬，双向钉扎）
- **收工状态**：全量 pytest 六面全绿（数字见提交说明）；彩带 v2 补强
  待推送（本文件提交后随第二提交 fast-forward）；真机肉眼验收未做

### 真机七环验收会话（本窗口，2026-11-03 19:00 登记）
- **收工**：**6 环 PASS + 1 环 UNVERIFIABLE**，无阻断缺陷。报告
  `docs/real_hw_acceptance_20261103.md`，可复跑脚本 `scripts/real_hw_acceptance.py`
- 环 6（数字模式 OTA）不可验原因：FC0012 上限 948.6 MHz（ADS-B 物理不可达）+
  会话窗内无 APRS 台活动/无卫星过境；`--ring 6` 可在有过境时复跑
- 登记待办（一般）：`get_satellite_pass` 不透出 TLE source/epoch，建议补字段
- **观察到未触碰**：`cpp/CMakeLists.txt`（M）、`cpp/tests/test_lrpt_real_cross.cpp`
  （??）在途，归属待认领者登记
- **禁区**：同星象仪会话禁区 + 彩带三文件；未改任何公共构建/冒烟脚本

### 悬账备忘（不属于任何人，认领者标注）
- ~~dump1090 全量解码清单对拍~~ **已闭环（9d3efea）**：dump1090 本尊编译当
  裁判，10/10 参考帧 + 全帧 CRC 语义钉死
- 本机自采 OTA 解码记录仍为零（负证据不撤销，对外表述按 capability-map 口径）
- 便携包需验证「真 USB 直连构建」打出的包（非 stub）
- ~~bch 独立 KAT（dvbs2_bch_vectors.json）仍 workspace-only~~ **已销
  （347d09f）**：BCH 独立 KAT + 标准参数摘录入库
- ~~LRPT 图像段重组真录链——被连帧能力阻塞~~ **阻塞已解除（本批）**：
  16 条 VCDU/宽窗素材已足（全过境约 2-3 倍）；重组器
  （VCDU→M_PDU→空间包→JPEG 段）待认领/下一轮
- LRPT 连帧的两个诚实未启用件：RRC 匹配滤波（sps=2 前馈下反而降 VCDU）、
  Gardner 定时环（原型 τ 发散）——留档 lrpt_modem.py 开关默认关，若做
  4+ sps 重采样 + 修好的 Gardner 可再战（预期再抬 VCDU 上限）

---

## 三、接下来要办啥（队列）

1. **[TRAE]** `scripts/smoke_exe_no_popup.ps1` 收口提交
2. **[TRAE]** 便携包线收口：package_windows.ps1 验真机构建打包，旧 stub zip
   问题闭环
3. ~~[我] dump1090 全量解码清单对拍~~ **已闭环（9d3efea）**
4. ~~CI 观察项：d1efe19 mbdsdr_ai 红~~ **已销（49dc00d）**
5. **[两边]** 共有文件再冲突时按 hunk 拆分提交（脚本先例：
   extract_hunk 姿势，见 2026-11-02 会话记录）
6. ~~[我] C++ LRPT 双实现对拍~~ **已闭环（8b66410）**
7. **[认领]** P2 前端缺口（gaplist 第三批）：频率轴滚轮缩放、FFT 仪器化
   （RBW/瀑布时间跨度）——文件在 spectrum_widget.py，星象仪/彩带会话
   若要做手势线请认领，否则协议真值线下一轮接手
8. ~~[我] LRPT 图像段重组真录链~~ **已闭环（本批）**：真录图像出图
   （40-80s 38 包/13 行 + 全过境 49 包/17 行/551 块），结构真值与
   woven 约定裁决见 capability-map
9. **[认领]** LRPT 连帧上限再抬：4+ sps 重采样 + 修 Gardner 环（τ 发散
   现象在 capability-map）+ RRC 匹配滤波复测——预期把 26% 帧可解率再推
   高、图像覆盖率从 3-8% 抬向可辨云图
10. **[认领]** 图像链收尾件：部分-RS 帧的包级利用（形状判据过滤）、
    合成链约定统一到 woven（改 lrpt_pipeline.build_cadu 往返切分 +
    decode_cadu 默认翻转 + C++ lrptDecodeCadu 同迁 + 全部 concat 钉
    随迁）、图像内容级真值对拍（对照第三方产物）

---

## 四、协调规则（血泪换来的，别再踩）

1. **开工三件套**：读本文件 → `git log --oneline -8` → `git status`，
   分清哪些是对方在途、哪些是自己的。
2. **谁在途谁所有**：不碰对方未提交编辑；不确定归属就不提交那个文件。
3. **共有文件按 hunk 拆**：`cpp/CMakeLists.txt` 这类高频混账文件，
   只把属于自己的 hunk `git apply --cached` 进暂存区。
4. **代码与金样必须同一提交走**：改编解码语义时 fixture 重生成物（JSON/LLR/
   IQ raw/期望向量）随代码一起交，否则 CI 拿旧金样打新代码必红（一天 5 起）。
   测试引用的金样**要么入库（tests/golden/），要么缺席必须诚实 skip**——
   收集期硬读仓库外路径是 CI 整包团灭的固定姿势（2026-11-03 又中一回）。
5. **push 会取消在跑的 CI run**：需要裁决就等结论再推；连续推会让裁决永远出不来。
6. **HELLO 脏提交**（另一会话周期性 `git add -A; git commit -m HELLO`）：
   动手前后核对 HEAD 与作者；未推送的 HELLO 按先例弃置重提。
7. **弹窗/DLL 问题**：三棵真构建树（build_cpp / cpp/build / cpp/build-local）
   已布防运行库，直跑零弹窗；`.tmp/dll_audit/sandbox` 是审计道具（故意裸奔），
   不要往里补 DLL。
8. **收工必须更新本文件**「当前在途」和「队列」两节——这是下一轮的开工状态。

---

## 五、变更日志

- 2026-11-03 00:20 初建（WorkBuddy）。背景：FT8/ADS-B 真值对拍 + CI 全绿 +
  DLL 布防收口后，用户要求多会话并行工作模式下立协调台账。
- 2026-11-03 10:30（WorkBuddy）：销 mbdsdr_ai 红（49dc00d，DVB-S2 LDPC
  金样入库 + 仓库外金样收集期炸裂根治）；队列/TRAE 在途刷新。
- 2026-11-03 10:55 事故记录（用户投诉扬声器放测试音 + CMD 刷屏）：TRAE 的
  smoke 全量 run（10:35 起，旧版脚本）裸跑 test_engine_audio_e2e 打真声卡
  （中 C+两八度脉冲音），161 exe 串行每个冒一个 CMD。TRAE 10:38 已给
  smoke_exe_no_popup.ps1 打补丁（强制 MBDSDR_NULL_AUDIO=1 + WindowStyle
  Hidden），但旧 run 在内存里继续跑旧代码——WorkBuddy 10:5x 掐掉僵尸 run。
  **硬规矩追加：任何批量直跑测试 exe 的脚本必须强制 `MBDSDR_NULL_AUDIO=1`
  （可同时保持 PATH 干净做 DLL 审计）**，改脚本后必须等旧 run 结束/掐掉
  再起新 run，否则用户机器上双鬼拍门。
- 2026-11-03 19:50（WorkBuddy）：LRPT 连帧破层批完成本地验证
  （Costas 闭环 + FFT 帧同步 + Viterbi 向量化 + 松阈值/ASM 验证，
  宽窗 16 条 VCDU 对旧 2 条）；悬账销 2 项（BCH KAT 入库 347d09f、
  C++ LRPT 对拍 8b66410）；队列加图像段重组（可证级硬通货）与
  连帧上限再抬两条。
- 2026-11-03 22:40（WorkBuddy）：LRPT 图像链破层批——lrpt_image.py
  全链 + **woven 约定裁决**（decode_cadu order 参数化，concat=合成链
  口径/woven=真录真值序）+ 真录图像出图 + 图像金样 6 项。CI 9dd272d
  全绿（行尾根治生效）。队列 #8 销账，收尾件拆出 #10。
- 2026-11-05（可发现性/帮助线）：新增 `desktop/capability_catalog.py`、
  `desktop/shortcuts_panel.py`、`desktop/help_panel.py`，`PanelSpec` 加 `summary`；
  `shortcuts.py` 只追加 5 条（`view.all_sky`/`view.reset_position`/`help.*` ×3，
  **未删任何既有键位**）。能力目录 53 项（可用 47 / 需条件 1 / 暂不可用 5）
  全部由面板/解调/数字解码/长尾解码器/AI 工具域注册表实时生成；
  `tests/test_discoverability_help.py` 15 条逐 source 反查注册表，
  `test_shortcuts_guard.py` 10 条全绿（含原 2 条存量红灯：A 键与重置窗口位置）。
  **报备 main_window（按 hunk 拆提交，未带走同文件在途的设置面板改动）**：
  `QKeySequence("A")` → `_key("view.all_sky")`；新增「帮助」菜单 3 项；
  新增 `_show_help_panel` / `_show_shortcuts_panel`（非模态）。
  未改 `rf_sky_view.py` / `spectrum_widget.py` / 设置面板。盘点见
  `docs/discoverability.md`。未推送。

- 2026-11-05（可发现性/快捷键线，本笔修复 HEAD ImportError）：**同文件并发事故**。
  `desktop/main_window.py` 被另一条线整文件提交时，带走了本线尚未提交的
  `owns_local_shortcuts` 导入，而 `desktop/spectrum_widget.py` 侧实现仍在
  工作区 → HEAD 上 `python -m desktop.main_window` 直接 ImportError，应用起不来。
  本笔把控件侧实现、`shortcuts.py`（spectrum scope 13 条局部键）与测试同批落盘，
  并用 `tests/test_shortcut_focus_priority.py::test_main_window_imports_without_shim`
  钉住「两侧符号必须同时存在」。
  **新增纪律：同一文件同一时间只允许一条线写；谁先占谁写，占之前先在工作区
  声明（在本台账或会话里写明文件名与占用时段）。** 共有文件按 hunk 拆分提交时
  只允许 `git apply --cached` 自己的 hunk，**禁止整文件 `git add`**；
  也不得为抢文件执行 stash / checkout 他人改动（咨询线已因此险些丢活）。
