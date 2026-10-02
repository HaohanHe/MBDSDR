# 第六阶段实现规格：发布收尾 + 真机回传 + 对标缺口 + 文档同步

> 基线：远端 main = 96b87d9（本地已同步）。第五阶段已交付（31739b8+）：真机联调向导 tools/onboarding（onboard.py 13 测试）、实验补强（LLM 基线/带宽/混淆矩阵/时长，pytest 22）、移动录音接线（flutter 288）、缺口清扫（D6/D7/D9/D10/D3 + sdr_tools 8 真 bug）。
>
> **环境新事实（2026-10-02）**：Android 工具链已在云 VM 搭通（用户已真编译出 app-debug.apk 163MB）：JDK 21/25 在 /home/user/jdks、Android SDK 36 在 /home/user/.local/android-sdk（local.properties 已指）、Gradle 9.3.1 已缓存、flutter 3.47.5。→ 本阶段 APK 编译验证可在云内真跑。

## 0. 现状确认
- 包名：Android `namespace`/`applicationId` = `com.example.mbdsdr_mobile`（mobile/android/app/build.gradle.kts:8,19）；Kotlin 包目录 `com/example/mbdsdr_mobile/MainActivity.kt`；iOS `PRODUCT_BUNDLE_IDENTIFIER` = `com.example.mbdsdrMobile`（+ RunnerTests ×2）。均为模板默认。
- 无 release 签名配置；README 可能残留模板 TODO。
- 真机回传：onboard.py/selfcheck.py 均已支持 --json，但无"回传格式说明 + 云侧解析"。
- 对标缺口（B4 审计结论）：热插拔已做拉取式 diff（device_lister）但**无 UI 提示接线**；增益已做吸附离散档但**UI 无档位选择**（rtl_sdr_source 已暴露 availableGainsDb()/gain()）；声卡已接线但无 underrun/重连健壮性；多 VFO 端到端已通、仅选中 VFO 出声（可补交互体验）；DMR 等受保护编码器不内置。

## 1. Wave1（四个并行批次）

| 批次 | 范围 | 交付/验收 |
|---|---|---|
| **P1 包名与发布收尾（高优先）** | Android：build.gradle.kts `namespace`/`applicationId` → `mbdsdr.app`；Kotlin 源目录迁移 `com/example/mbdsdr_mobile/` → `mbdsdr/app/`（package 语句与所有引用同步，含 AndroidManifest、测试）；iOS：project.pbxproj 两处 `PRODUCT_BUNDLE_IDENTIFIER` → `mbdsdr.app`（RunnerTests 用 `mbdsdr.app.RunnerTests`）；清理 README/代码里模板 TODO 注释；release 签名指引：`tools/android/gen_keystore.sh`（生成不入库的 keystore）+ `key.properties` 模板 + .gitignore 增加 `*.jks`/`key.properties`/`keystore*` + `docs/learn/phase6/P1-release-build.md`（debug/release 构建步骤、签名配置说明，密钥永不入库）。**验收**：flutter analyze 0、flutter test 288 基线不破、`flutter build apk --debug` 云内真编译通过（首次可能 10-25 分钟）。 | 代码 + 脚本 + 文档 + APK 编译验证 |
| **P2 真机回传格式** | `docs/learn/phase6/P2-hw-report-format.md`：selfcheck `--json` 与 onboard `--json` 的完整输出示例（全部字段逐项说明、预期取值范围、无硬件时的 FAIL/SKIP 值）、真机用户操作步骤（跑完两条命令→把 JSON 贴回）；云侧解析脚本 `tools/onboarding/parse_hw_report.py`：吃贴回的 JSON（支持文本内含多段 JSON 的提取），输出结构化结论（设备/tuner/丢包/串口/依赖逐项 + 后续可做步骤建议），配 pytest 确定性测试（好 JSON/坏 JSON/无设备/部分缺失）。 | 文档 + 解析脚本 + 测试 |
| **P3 对标缺口** | 先学后做，补真实实现（不铺空壳），C++/Flutter 按所有权分：①热插拔 UI 提示（桌面：设备在场 diff 状态变化→状态栏/设备面板提示"设备已移除/已连接"；Flutter 侧 radio_controller 断连提示已有基础可补）；②分段增益真机选档（桌面 UI：增益控件改离散档位下拉/步进，接 availableGainsDb()/gain()，无档位表时诚实禁用）；③实时声卡播放健壮性（underrun 检测与提示/重连逻辑，参考 sdrpp 的 packer/重连机制，学机制不照抄 GPL）；④多 VFO 实战体验（选中 VFO 出声切换的 UI 明确化、状态显示）。每项带云内可跑的确定性测试；真机项明确标注。**验收**：cpp ctest 87 基线不破+新增；flutter 288 基线不破+新增；analyze 0。 | 代码 + 测试 + 文档 |
| **P4 文档同步** | 根 README、experiments/README、docs/learn 索引与当前行为同步：工具清单（mbdsdr_ai 摘除项/诚实标注项）、实验产物与口径、第六阶段交付物、真机联调流程、LLM 基线 PENDING 诚实标注保持；无"比赛/competition"字样、无密钥/敏感信息。 | 文档更新 |

## 2. 验收（每批必过）
- cpp：ctest **87/87 基线不破坏** + 新增全绿（P3 涉及）。
- Flutter：**288 基线不破坏** + 新增全绿；analyze 0（P1 改包名后必须全量重跑）。
- **APK 编译**：P1 交付后 `flutter build apk --debug` 云内真编译通过（验收硬项）。
- pytest：新增脚本测试全绿。
- git：只暂存本批相关文件（禁 add -A），排除 build/scratch/key/keystore/*.jks/key.properties；无"比赛/competition"字样；无密钥入库（gen_keystore 产物必须进 .gitignore 且不入暂存）。
- 推送：云环境无有效凭据（历次一致）——本地建好提交，推送待用户/外部。

## 3. 红线（一贯）
先学后做、真读代码；禁假数据/假执行/假成功/静默 mock；无硬件/无档位表诚实空态+明确原因；MIT 干净室（GPL 只学机制）；DMR 等受保护编码器不内置；分批推进、每批独立验证后再推下一批；诚实披露未完成项与环境限制。

## 4. 交付物索引（2026-10-02 P4 文档同步复核）

| 批次 | 落位文件 | 状态 |
|---|---|---|
| **P1 包名与发布收尾** | `mobile/android/app/build.gradle.kts`（namespace/applicationId→`mbdsdr.app`）、Kotlin/iOS 包目录迁移、`tools/android/gen_keystore.sh` + `key.properties` 模板 + `.gitignore`（`*.jks`/`key.properties`）、`docs/learn/phase6/P1-release-build.md` | 并行批次交付；APK 编译验证以 `flutter build apk --debug` 云内真跑为准 |
| **P2 真机回传格式** | `docs/learn/phase6/P2-hw-report-format.md`（selfcheck/onboard `--json` 字段逐项说明 + 真机操作步骤）、`tools/onboarding/parse_hw_report.py` + 确定性测试 | 并行批次交付；真机用户跑完两条 `--json` 命令后按该文档回传 |
| **P3 对标缺口** | `cpp/src/dsp/`（热插拔 UI 提示 / 增益档位选择 / 声卡 underrun 检测 / 多 VFO 交互）+ 云内确定性测试 | 并行批次交付；真机项（真实拔插、出声）仍标注待真机，不宣称已验证 |
| **P4 文档同步** | 本根 `README.md`、`mbdsdr_ai/README.md`、`experiments/README.md`、各 `_PHASE*_SPEC.md` 顶部交付状态标注、本索引 | **本批交付** |

> 说明：P1/P2/P3 与本批（P4）在同一 Wave 并行推进。上表"落位文件"为各批次既定产出位置；
> 凡真机专项（USB 拔插、声卡出声、USB-GNSS、release 签名）一律标注"待真机/待 Xcode"，
> 云内只验证可离线确定性验证的部分。LLM 基线无 key 时保持 `PENDING_ONLINE_RUN` 空态，不写成已在线跑过。

