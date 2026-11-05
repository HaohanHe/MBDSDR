# AGENTS.md — MBDSDR 仓库协作规则

> 本文件是 agent 在本仓库工作前的必读。任何会话（人或 AI）违反其中红线，
> 按 bug 处理，不留情面。

## 1. 公开仓库 = 永久公开（身份红线）

本仓库对外是**作品本身**，不是作者的名片。以下内容禁止出现在任何
tracked 文件（含 commit message）中：

- 作者姓名、呼号、年龄、就学计划与学校、联系方式、财务与行踪信息；
- 真实地理位置默认值（站点坐标用中性示例值，如 0.0, 0.0 “示例站点”）；
- 硬编码的“能力数字”（commits 数、测试数、工具数）——它们随时间浮动，
  写入即过期，且属虚荣指标；要证明能力用测试与可复现命令，不用数字标语。

呼号等身份字面量在协议测试夹具中出现时，用中性示例值（当前约定 `BI1AAA`）。

**例外（经作者明确要求）**：commit author 元数据保持作者指定的归一身份
（具体值见 git log，不写进本文件）；内容层（含 commit message）同样执行
本条红线。

**机械护栏**：`tests/test_identity_redline.py` 扫 git tracked 文本文件，
命中个人信息或就业意向措辞即红（邮箱/手机号/学校/居所类词；规则字面量用
拼接构造，门禁自身不命中自己）。新增或修改 tracked 文件后该门禁必须仍绿。

## 2. 诚实文化（本仓库的第一性）

- 无硬件/无信号 → 显式空态或错误信封，**绝不 mock、绝不伪造数据**；
- 未校准/半校准数据必须标注口径（如 VNA 仅 load/isoln 校准）；
- 死参数、文档与实现掰腕子，按 bug 处理（先例：`traceback_depth` 死参数）；
- 测试期望值与常量成对更新；不许删断言、放宽比较让测试变绿。

## 3. 工程约定

- 全仓 MIT；学上游只学机制，**不抄 GPL 代码**（对照 NOTICE.md）；
- 布局模型唯一事实源在 C++ renderer；Python 侧只做 agent 侧构造/校验/投影；
- 写工具一律走外部确认门（一次性 `confirm_token`，绑定动作摘要/有效期/单次消费）；
  模型自报 `arm_manual=true` 不构成确认；调用前四道串联：command hook →
  进程内 hook → policy deny → 一次性 token；
- 新工具必须登记 `_TOOL_DOMAIN`（否则分组视图静默丢工具，有钉扎测试防这个）；
- 跨语言一致性（mobile catalog / C++ 注册表 / 域集合）用钉扎测试锁死，
  不许靠口头约定；
- **窄带/高阶 IIR 一律 `butter(..., output="sos")` + `sosfilt`，禁用 ba
  系数 `lfilter`**：2026-10-28 真实 FM 样本解出 `wfm_stereo_lite` 的
  `pilot_snr_db=nan`——5 阶 butter 在 1.024MHz 采样率 / 19k±800Hz
  （归一化带宽 1.5e-3）下 ba 系数数值爆炸，`pilot_band**2` 溢出成 inf。
  合成 fixture 幅度小，**永远测不到**，只有真实大动态信号能咬出来；
- 真实/大动态样本是合成 fixture 测不出的数值稳定性与量纲类缺陷的唯一
  探针：样本入仓后必须立刻接进解码链跑一遍，不能只当资产摆着。

## 4. Windows/PowerShell 施工纪律（本仓主要开发环境）

- commit message 走 `-F 文件`（here-string 遇 CJK+引号会被 PS 5.1 咬断，
  已多次事故）；消息内容同样遵守第 1 条身份红线；
- 推送用 `git -c http.extraHeader="Authorization: Basic <b64>"`（token 不落
  config/URL）；token 属用户信任面，用完提醒吊销；
- 批量文本替换先写一次性脚本到 %TEMP%\kilo\（不进仓库），再跑全量测试。
- 私有 `GIT_INDEX_FILE` 文件名必须带**唯一前缀**：`idx_<会话标识>_<YYYYMMDD_HHMM>`，
  禁止 `idx_a` / `idx-b` / `idx_private` 这类固定名；建名前先确认该文件不存在，
  用完立即 `Remove-Item`。同名私有 index 会让两条线往同一份 index 里 add，
  先提交的一方把对方内容一起卷走（实测事故：`09b53221` 卷进他线 4 个文件，
  只能靠 `d8b40eb0` 逐文件还原）；提交清单再用
  `scripts/check_commit_scope.py --staged --expect <路径...>` 兜底。

## 5. 基线

`python -m pytest mbdsdr_ai/tests -q` 基线锚点（commit 级回归手段，不作对外宣传数字）：全绿时**共 1168 项**（远端沙箱 2026-10-21 实测 1164 passed / 4 skipped；本沙箱整合轮后实测 1163 = 1157 passed / 6 skipped。skip 全是无 env/无硬件的诚实跳过——SatDump 目录缺失、`reedsolo` 未装、`MBDSDR_RTL_TCP` 未配置、无网络 TLE、`zstandard` 未装；passed/skip 分布随环境浮动，但测试文件 108 个 / 1051 个 `test_` 函数与远端主线逐字一致，差异纯环境）
（2026-10-21 实测：1097→1168 净增 71。同期 `mbdsdr_ai/tests` 由 104 个文件 /
992 个 `test_` 函数增至 108 个文件 / 1051 个 `test_` 函数，新增 test_acquisition、
test_cross_golden、test_modulation、test_rrc_single_source 四个文件。
主要增量来源：多普勒定轨链的量纲/传播系统性修复、CCSDS 通用数字接收链、
调制内核收敛（RRC 与 FreeDV QPSK 归一到单一来源）、工具载荷契约与
"错误:即失败"的诚实语义、外部 API 静默降级修复。
历史增量：915→1041（phase66 零覆盖模块首套行为测试）→1086（phase67 模型体验）
→1097（skyfield 1.54 兼容 + 跨语言对拍）→1168（材料能力补齐 + 门禁化）
→整合轮（2026-10-22 并入远端 76 提交：GPS 扩频/射电干涉/CCSDS 接收链/卫星
IQ 出图 + 七道根套件门禁 + capability-map；测试代码双边一致，本沙箱实测 1163）。
无网环境下 `test_real_tle_fresh_or_skip` 等真实联网用例转 skip，
passed 数相应下浮——skip 均属无 env/无硬件的诚实 skip）。任何改动不得
降低此数。

（2026-10-28 深夜工程轮实测：**1246 passed / 4 skipped / 0 failed**。
自 1168 起净增 78，来源：RTL-SDR 枚举回退与 rtl_tcp 兜底、导出路径收拢、
入参 NaN/Inf+数组+区间三闸、MCP 回环绕代理、tab 身份稳定化、GUI 配置
诚实落盘、Ctrl+C 焦点感知、滚轮 tooltip、CRC 内核双重钉扎、QPSK 解调链
三处真缺陷修复、ModelManager 4xx 重试、会话批准 TTL、workflow_recorder
参数化、FT8 Costas 同步、接收流水线冒烟。唯一曾经的红用例
test_http_unreachable_server_envelope 已证实为真 bug（回环被代理劫持）
并修复销账，不是环境问题。）

（2026-10-28 工程轮（六轮）实测：**1535 passed / 4 skipped / 0 failed**。
自 1246 起净增 289，来源：MCP 旧壳安全面收口（ToolCallGate 共用 + 读工具 n
上界）、零测试模块 20 个补齐行为测试（DSP/扫描感知/轨道杂项三批，合成信号
口径）、真缺陷修复 26+ 组（Viterbi ACS 恒输出全 0、version_store 撞 ID 丢
历史与空串冒充、gimbal RPRT 20 假成功、wfm_stereo_lite 串扰、cw_decoder
5 处、time_sync NTP 时间戳错位、ft8_callsign 28 位环回等）、首份真实 OTA
FM 样本入仓（tests/fixtures/real_fm_88m1，2^19 样本 cu8+SigMF，12 项
真实性验证）。）

（2026-10-29 工程轮（八轮）实测：**1565 passed / 5 skipped / 0 failed**。
自 1535 起净增 30，来源：A2UI 信封栈补测（render/messages 共 +17）、AI 步骤
与 AI 面板行为测试、以及四处真缺陷修复——其中 `ai_panel._on_ai_finished`
静默吞 dict 形 arguments 后**以空参数向真硬件发调用**（AI 操作员执行层
最危险形态：门过了参数却是空的）。桌面 UI 稳定性六处（关窗定时器/事件
过滤器/touch 信号不回收、NaN 被夹成满增益与最高频并真下发调谐）与真实
样本反哺的 wfm sos 溢出修复同轮入账。）

根 `tests/`（大套件，291 个测试文件，2026-11-04 `--check` 实测）同样是
对拍门禁所在。**它整跑很慢**：
慢在同进程内单例 QApplication 跨文件累积——真因已实测定位（`8fae7d5`）：
`ui_actions/sky_actions` 的 `bus.executor`（窗口绑定方法）与
`PanelRegistry._widgets`（面板强引用）两条**进程级单例钉子**把已关窗口钉住
不回收，于是每建一个 MainWindow，`topLevelWidgets` 净增 39 且永不回落
（0→39→…→234），app 级 `setStyleSheet` 的全量重抛光成本随之线性上涨
（实测孤立构造 2.8 s，同进程第 6 个窗口 16.6 s；历史极端值可要 11 分钟）。
热点栈会误导：py-spy 采样常落在 `Theme.apply → app.setStyleSheet`，但样式
守卫本身无辜（空载实测 0.5 ms）——它只是被累积的存活控件放大成了显性热点。
对策有两条，都已落地：
① `tests/conftest.py` 的模块级 autouse fixture 在每个模块结束后回收遗留
   顶层 widget（治本，显著降耗）。**回收必须用
   `app.sendPostedEvents(None, QEvent.DeferredDelete)`**：实测
   `deleteLater()` + `processEvents()` 是空转（顶层控件 39→39 纹丝不动，
   DeferredDelete 按投递时的事件循环层级延后，processEvents 不保证派发），
   换成 sendPostedEvents 后同一批窗口 78→0。粒度停在 module 不下沉到
   function：`test_app_mode` / `test_desktop_ui_responsiveness` 等把
   MainWindow 做成模块级 fixture 整模块复用，逐测试强拆会删掉还在用的窗口。
   **护栏本身要有钉扎**：这个 fixture 的实现曾在一次 conftest 改写中整段丢失
   而本文档仍记"已落地"，连续多轮因此把挂死误归因到别处——护栏要配
   `tests/test_ui_stability_resources.py` 那种"关窗必须松钉"的断言 + 反证。
② **CI 与本地跑测脚本都按 A/B 两组并行跑**（`scripts/ci_pytest_groups.py`
按文件名排序交替分组，自动生成无需维护清单；收件覆盖 `test_*.py` /
`*_test.py` / `*roundtrip*.py` 三种命名——历史上只收第一种，phase71 修正，
四个 `main()` 体检脚本 tool_selftest/tool_health_check/tool_param_smoke/
smoke_live_tools 故意不入组）——分组后单组十余分钟即跑完
（2026-11-04 本机实测：A 组 1561 passed / 4 skipped / 0 failed，12:50；
B 组 1440 passed / 2 skipped / 2 failed，11:05——两条红均为环境性，
见下；共 291 个文件），
单进程整跑的累积问题自然消失。
两条本地跑法坑（都实测踩过）：脚本打印的是 **Windows 反斜杠 + CRLF**
路径，直接喂 pytest 会得到 exit 4「no tests ran」，必须先
`tr -d '\r' | tr '\\' '/'`；**真机棒子插着时，大套件里的真机用例会随机
假红 `LIBUSB_ERROR_ACCESS(-3)`**——ACCESS(-3) 的语义是「设备被占用」而
非权限不足，任何并发进程持棒即触发。三次实测把归因收窄到这个结论：
A/B 两组并跑红；B 组单独跑**仍红**（同一时刻另一会话在跑 hw 车道测试）；
安静环境独占复跑两次全绿（1 passed / 16.30 s、1 passed / 13.75 s）。
对策：真机用例只在大套件静默时单独验，不要拿大套件的红去改产品代码
（CI runner 无硬件自然 skip，不受影响）。

根 `tests/` 新增 6 个门禁文件：
`test_material_task_chains.py`（五份材料 39 个任务步的存在性 + 端到端链路）、
`test_tle_parse_crosscheck.py`（TLE 文本↔SGP4 解析对拍）、
`test_empty_result_diagnostics.py`（空结果必须带说明）、
`test_external_api_honesty.py`（外部 API 错误载荷不得谎报为空）、
`test_rx_acceptance.py`（材料验收口径可执行判据）、
`test_material_table_consistency.py`（材料表 ↔ 实现：工具存在性、声明
用例数、验证文件存在）、`test_identity_redline.py`（身份红线机械护栏）。

C++ 桌面端（`cpp/`）：`ctest --test-dir build_cpp`（Qt 6.8.2 MinGW 工具链 +
offscreen）基线 **155/155 全绿**（2026-10-20 实测；含跨语言对拍 cross_golden、
i18n l10n、freq_input、快速调谐/首启向导接线、core::AppSettings 设置隔离）。
新增测试目标只增不减；测试目标数与 ctest 注册数对不上按 bug 处理。

移动端（`mobile/`）：`flutter test` 基线 **447 passed**（整合轮本地 Flutter 3.47.7
实测：phase67 帮助页/首跑引导 5 项 + AI 工具描述门禁 15 项 + phase67.1 工具面
i18n 三语约 40 项全绿；404→447 是远端新增测试入库后的实数，只升不降）。
`flutter analyze` 零问题（Flutter 3.47.7，SDK 约束 >=3.9，勿用 /opt/flutter
旧版 3.0 跑）。注意：跑测试前若报 `AppLocalizations` 未定义，先
`flutter gen-l10n`（gen 产物按 l10n.yaml 输出到 lib/l10n/gen，不入库）。
