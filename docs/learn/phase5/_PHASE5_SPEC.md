# 第五阶段实现规格：真机即出成果 + 实验补强 + 移动接线 + 缺口清扫

> **交付状态（Phase6 复核，2026-10-02）：已交付。** P1 联调向导 `tools/onboarding/onboard.py`（分步 + 离线测试）、
> P2 实验补强（`exp_llm_baseline.py` / `exp_rate_bandwidth.py` / `exp_amr.py` / `exp_doppler_duration.py` + `experiments/common/` 扩展）、
> P3 移动录音/回放 Dart 接线（生产侧闭环、原生端标注待真机）、P4 缺口清扫（workflow 跨步回填 / cron 星期映射 /
> sdr_tools 重复副作用与 None 保护 / astro/ntrip 真 bug，见 `mbdsdr_ai/README.md` §2）。本文件保留为历史规划快照。

> 基线：远端 main = be1c653（本地已同步）。第四阶段已交付：hw_selfcheck 自检工具（14 测试）、论文实验管线（5 公共模块 + 4 脚本，29 pytest）、移动原生 building blocks（WAV/sidecar/Store/FilePlayer/NMEA，281 flutter）、C++ 断流看门狗/设备 diff/增益吸附（87 ctest）。

## 0. 环境事实（2026-10-01）
- 云 VM 无硬件（无 USB/串口/声卡），真机（RTL-SDR + GNSS + 天线）在用户本地。
- rtl-sdr CLI 在 ~/.local/bin（rtl_test/rtl_sdr/rtl_eeprom）；librtlsdr C 库在 ~/.local 但 CMake 未找到（stub 构建）。
- 移动端：production 外壳（radio_controller / spectrum_page / home_shell）**尚未接线**录音/回放 building blocks（grep 无 startRecording/FilePlayer 调用）；recordings_page 已更新空态文案。
- mbdsdr_ai：sdr_tools.py 6926 行、agent.py 3333 行；A2 审计遗留 D6（workflow_execute 跨步变量）、D7（pose 仿真）、D9（收窄描述）、D10（cron）未做；workflow_engine/scheduler 已接线。
- experiments：exp_amr.py 有旧式混淆矩阵（非公共模块 manifest/CI 口径）；exp_weak_model_toolcall.py 是 LLM 弱模型实验（无 key 云内跑不了）；exp_ebno_decode/doppler_orbit/baseline_compare/ota_handoff 已接公共模块。
- paper/experiments/ 被 .gitignore 忽略（生成物），文档以 experiments/README.md 为准（已同步一份）。

## 1. Wave1（四个并行批次）

| 批次 | 范围 | 交付 |
|---|---|---|
| **P1 真机联调向导** | 新增 tools/onboarding/ 一键流程：自检（复用 hw_selfcheck）→ 捕获（rtl_sdr 命令行起流，或 rtl_tcp 起流）→ 录制 SigMF（写 meta+data，参照 C++ recorder SigMF 格式与 mbdsdr_ai/playback 读取面）→ 解调/解码（复用 mbdsdr_ai 真实解码器）→ 输出（CSV/PNG 数据图、报文转储、定轨结果）。无硬件时每步给出明确失败原因（禁 mock）。CLI 分步可单跑、可全链；含离线确定性测试（注入假命令输出/假设备）；文档 docs/learn/phase5/P1-onboarding.md + README。 | 一键脚本 + 分步脚本 + 文档 + 测试 |
| **P2 实验补强** | ①exp_llm_baseline.py：LLM 基线可运行（模型可插拔、api key 只从环境变量读、禁入库；云内无 key 时诚实空态"PENDING_ONLINE_RUN"不伪造），与经典/KNN 同指标同数据集对比留接口；②新实验：解码成功率 vs 采样率/带宽、AMR 混淆矩阵（并入公共模块口径：manifest/CI/图）、定轨收敛 vs 观测时长；③experiments/README.md 与 paper/experiments 说明同步维护。全部图标口径/样本数/CI。 | 脚本 + 公共模块扩展 + 文档 + pytest |
| **P3 移动接线** | ①原生静态审查：对 MainActivity.kt（AudioTrack 实时+文件回放+USB host）与 AppDelegate.swift 做编译错误预扫（云内无法真编译则产出静态审查清单：权限声明、AndroidManifest usb.host/uses-feature、iOS Info.plist、通道注册点、API 用法核对——逐项 PASS/FAIL/待验）；②Flutter 生产接线：RadioApi 增加 startRecording/stopRecording（或已有则接线）→ FileRecordingSink + RecordingStore 注入 → spectrum 页/外壳录音按钮真实开关；recordings 页回放按钮 + FilePlayer 注入（无文件时诚实空态不渲染）；analyze 0、flutter 281 基线不破坏 + 新增。 | 静态审查文档 + 生产接线 + 测试 |
| **P4 缺口清扫** | 对照 docs/learn/phase3/audits/A2-fake-tools.md 遗留项：D6 workflow_execute 跨步变量回填（executor 返回 dict 对齐 workflow_engine 期望，或如实标注）；D7 pose_update_imu/gps 传感器推流（无硬件源时诚实空态/标注，不靠 LLM 手填冒充）；D9 描述收窄；D10 cron/定时触发（接 scheduler 真实现或摘除）；sdr_tools.py 大文件已知 bug 复查（6926 行，重点 scan/demod/record 路径）与修复；孤儿功能（只有前端或只有后端）清点处理；README 与当前行为同步。每项"接真/摘除/如实标注"三选一，不允许挂着假工具。 | 清扫报告 + 代码修复 + 测试 |

## 2. 验收（每批必过）
- cpp：ctest **87/87 基线不破坏** + 新增全绿；全量构建 0 错误（P1/P4 若改 C++ 才需要）。
- Flutter：**281 基线不破坏** + 新增全绿；analyze 0 issue（P3 必过）。
- Python：pytest 既有 29 不破坏 + 新增；LLM 实验无 key 时诚实空态可跑。
- git：只暂存本批相关文件（禁 add -A），排除 build/scratch/figma/key/record 原始数据；无"比赛/competition"字样；无密钥入库（LLM key 只读 env，代码里禁止任何 key 字面量）。
- 推送：云环境无有效凭据（GITHUB_TOKEN 被拒、gh 未登录、无 SSH key）——本地建好提交，推送待用户/外部（与前几阶段一致）。

## 3. 红线（一贯）
先学后做、真读代码；禁假数据/假执行/假成功/静默 mock；无硬件/无录制诚实空态+明确失败原因；MIT 干净室；无魔法数；分批推进、每批独立验证后再推下一批；诚实披露未完成项。
