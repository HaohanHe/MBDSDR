# 第五十七阶段实现规格：校准/标定工具链 + 真机一键验收脚本

> 基线：HEAD = 5e94140（已推送）。补通用 SDR 平台校准能力，把真机工具串成一条命令。
> 现状：真机工具分散（hw_selfcheck→diag_wizard→onboard→exp_ota_run）；**Phase34 已有 acceptance_run.sh `--event` 模式**（selfcheck→diag→onboard sstv/ssdv→exp_ota_run，活动参数零硬编码，缺频率 exit 3，无硬件诚实 SKIP exit 2）——块 2 扩展它、不重写。
> 用户提示：校准可图形化演示，用户拿手台掐已知频率参考（如 409.7500/438.500 MHz，**仅为示例、必须参数化、不内置默认台**）。

## 块 1（核心）：校准工具链
- **频率校准**：已知参考信号（频率参数化传入）→ 测频偏 → 算 PPM 修正（ppm = offset_hz/ref_hz×1e6，公开算法）；
- **电平校准**：已知/相对参考 → dBFS 标定流程；
- 落 tools/ 或 mbdsdr_ai（纯 stdlib/可复用），合成确定性测试（注入已知频偏/电平断言修正）；无设备诚实空态（每步 FAIL+下一步，不 mock）；校准结果存 JSON（路径参数化）。

## 块 2：真机一键验收脚本
- **扩展现有 acceptance_run.sh（或配套 .py）**：串 selfcheck→diag→校准→onboard（freq/mode 参数化指定）→exp_ota_run；
- 一条命令出：设备状态 + 校准 + 解码结果 + 图 + 回填；每步失败诚实停给原因/修复命令；无硬件云内跑空态路径（逐步 FAIL 不崩）；支持 `--json` 汇总。

## 块 3：回归与文档
- 补校准/验收脚本测试；runbook 加校准步骤与一键验收命令（活动/参考参数只进 docs 不进代码）；
- pytest 191/7 skip、flutter 384、ctest 129 不回归（全量输出实际计数）。

## 质量门 / 红线
- 干净室 MIT 不复制 GPL；无比赛字样；**活动/参考频率禁入代码（参数化、不内置默认台）**；不预置 TLE；诚实空态不 mock；
- 8GB OOM（-j2，如实记录）；只暂存相关文件（禁 add -A）；**不 commit/push**；
- 如实报告 file:line、校准真实数字、脚本实测、未解决项。
