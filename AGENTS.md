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
- 写工具一律走 `arm_manual` 人工门；调用前四道串联：command hook →
  进程内 hook → policy deny → arm_manual；
- 新工具必须登记 `_TOOL_DOMAIN`（否则分组视图静默丢工具，有钉扎测试防这个）；
- 跨语言一致性（mobile catalog / C++ 注册表 / 域集合）用钉扎测试锁死，
  不许靠口头约定。

## 4. Windows/PowerShell 施工纪律（本仓主要开发环境）

- commit message 走 `-F 文件`（here-string 遇 CJK+引号会被 PS 5.1 咬断，
  已多次事故）；消息内容同样遵守第 1 条身份红线；
- 推送用 `git -c http.extraHeader="Authorization: Basic <b64>"`（token 不落
  config/URL）；token 属用户信任面，用完提醒吊销；
- 批量文本替换先写一次性脚本到 %TEMP%\kilo\（不进仓库），再跑全量测试。

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

根 `tests/`（大套件，165 个测试文件）同样是对拍门禁所在。**它整跑很慢**：
单进程跑 160 个文件约 45 分钟——慢在同进程内单例 QApplication 跨文件累积
（前面的文件留下成百上千个 widget，后续每次新建窗口都要连带做全量样式/
布局重算，越往后越慢；孤立构造 MainWindow 只需 5.5 s，累积后可要 11 分钟）。
对策有两条，都已落地：
① `tests/conftest.py` 的模块级 autouse fixture 在每个模块结束后回收遗留
   顶层 widget（治本，显著降耗）；
② **CI 与本地跑测脚本都按 A/B 两组并行跑**（`scripts/ci_pytest_groups.py`
按文件名排序交替分组，自动生成无需维护清单）——分组后单组只要
约 3–4 分钟（实测 A 组 695 项 3.6 分钟、B 组 807 项 3.1 分钟），
单进程整跑的累积问题自然消失。

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
