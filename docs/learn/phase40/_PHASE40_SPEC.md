# 第四十阶段实现规格：稳定性收官

> 基线：HEAD = 80b2192（已推送），ctest 127/127、flutter 351、pytest 42/42。
> 背景：tests/ 大套件已拆标签（mainwindow=8/slow=18/快循环 109）+ QSettings 隔离；e2e_smoke 环境性（无 PulseAudio）、device_ui 顺序相关墙钟（单跑即过）、audio_sink 间歇性；CI ci.yml 三 job 结构完整但"待 runner 配置验证"；8GB cgroup OOM。

## 块 1+3（A：tests/src 域）
- 逐一复跑 e2e_smoke/device_ui/audio_sink/tests 大套件（offscreen），确认真实状态（真失败/间歇/环境性），每个用例根因+处置（修复 / skip-if-no-audio 带原因 / 文档化+理由）；
- 能修就修：qt_audio_sink 无音频设备时诚实 SKIP 带原因（非硬失败）、e2e_smoke 无音频诚实 SKIP；
- 启动健壮性：无显示/无音频/无 SDR 三无环境启动不崩（MainWindow 空态）——补/验测试；异常退出码与崩溃日志路径检查；
- 全量 ctest 127/127（含新增，全量输出）。

## 块 2（B：CI 域）
- ci.yml 核对真实脚本（scripts/run_all_tests.sh 已存在），补齐缺失步骤/注释；YAML 语法校验；云端无法实测 runner → 如实写"未实测"。

## 质量门 / 红线
- 修复必须带证据（复跑日志）；环境性失败标注"环境性+原因+复现命令"，不假装修好；诚实空态不 mock；无比赛字样；活动参数禁入；OOM 约束单目标增量构建；只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、测试全量输出、剩余环境性项与理由。
