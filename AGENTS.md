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
（具体值见 git log，不写进本文件）——贡献图谱需要可归属到真人，这是求职
场景的投名状功能，不属于“内容层身份信息”；内容层（含 commit message）
同样执行本条红线。

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

`python -m pytest mbdsdr_ai/tests -q` 当前基线 **564 passed / 10 skipped**
（2026-10-14 身份脱敏后复测）。任何改动不得降低此数；10 个 skip 是无 env/
无硬件的诚实 skip，属预期。
