# PRD 现状对照（P0 → 仓库现状 → 工单）

> 配套 [PRD.md](PRD.md)。本表回答三件事：**每条 P0 当前做到哪一步、证据在哪、差的那段谁接。**
> 口径：**实现状态**回答"缺不缺"；**证据强度**回答"验到什么程度"；**复核时间**为 2026-11-04，按**当前代码**而非历史审计文档判定。
> 行号说明：表内行号为 2026-11-04 快照。`desktop/main_window.py` 仍在他线在途修改，行号会漂移；以**函数名/符号**为语义锚点（如 `_teardown_connection`、`_on_pipeline_read_failed`），行号仅供快速定位。

## 0. 判读口径与统计

| 维度 | 取值 | 定义 |
|---|---|---|
| 实现状态 | **有** | 端到端实现，产品定义上无缺口（已知缺陷不改变该条定义） |
| | **半有** | 有主要实现，但存在**定义级缺口**：未接线 / 单边状态 / 两套来源 / 时间基准分叉 / 严重缺陷导致验收不成立 |
| | **无** | 未实现 |
| 证据强度 | 真机真验 | 有真机、真实样本或第三方真值背书 |
| | 外部真值 | 外采官方样本/第三方清单 |
| | 仅自测 | 只有本仓合成 fixture、假后端或单测自洽 |
| | 未验证 | 本轮环境物理上做不到（无硬件/无过境/无 key/无网络） |

统计：**P0 共 11 条 —— 有 4、半有 7、无 0。**

| 实现状态 | 条目 |
|---|---|
| 有（4） | P0-01 双形态、P0-02 全星表天空、P0-03 时间引擎、P0-04 双环锁定 |
| 半有（7） | P0-05 站址单一来源、P0-06 SDR 基本功、P0-07 录音/回放/库、P0-08 AI 协商真模型、P0-09 收星闭环、P0-10 诚实性、P0-11 动态 UI 边界 |
| 无（0） | — |

**与上一版的判定变化**

| P0 | 变化 | 依据 |
|---|---|---|
| P0-02 | 半有 → **有** | UI 已接线：`_star_selection` 调 `select_for_view`（[rf_sky_view.py](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L3221-L3245)），绘制/拾取共用同一份星集合（[L3273-L3345](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L3273-L3345)、[L4735-L4754](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L4735-L4754)）。`docs/star-catalog.md` §7 第 7 条"UI 接线未做"已滞后。 |
| P0-04 | 半有 → **有** | S15 已修：`_teardown_connection` 在断硬件前先停跟踪（[main_window.py:_teardown_connection](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3817-L3823)）。 |
| P0-06/07/09/10 | 仍半有，但缺陷减少 | B1（[_teardown_connection](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3773-L3845)）、S3（[L4472-L4477](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L4472-L4477)）、S5（[L6085-L6091](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L6085-L6091)）、S8（[_on_pipeline_read_failed](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L4790-L4820)）、S13（[L3915](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3915)）、S2（[L6939-L6956](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L6939-L6956)）已修。 |
| 半有 → 无 | **0 条** | 7 条半有均有可点开的实现证据；缺的是缺口收口，不是"没有实现"。 |

---

## 1. P0 逐条对照（证据均可点开）

| ID | P0 功能 | 状态 | 证据强度 | 证据锚点 | 缺口 / 收口判据 |
|---|---|---|---|---|---|
| P0-01 | 双形态（星象仪 / 工作台） | 有 | 仅自测 | 壳层构建 [main_window.py:L1503-L1511](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L1503-L1511)；进入 [L1672-L1702](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L1672-L1702)；退出归位 [L1704-L1734](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L1704-L1734) | B2/M2/L1 未收口（见 §2 对应行） |
| P0-02 | 全星表天空视图与视场 LOD | **有** | 仅自测（数据层有外部样本对拍） | 数据层 [star_catalog.py:L16-L20](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/skyengine/star_catalog.py#L16-L20)、[L388-L525](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/skyengine/star_catalog.py#L388-L525)；UI 接线 [rf_sky_view.py:L3268-L3292](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L3268-L3292)、[L3320-L3345](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L3320-L3345)、[拾取 L4788-L4830](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L4788-L4830) | 补 AC-02.1 逐星像素对拍；修 `star-catalog.md` §7 第 7 条滞后描述 |
| P0-03 | 时间引擎与可控时轴 | 有 | 仅自测 | 引擎 [new_spacetime.py:L142-L190](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/new_spacetime.py#L142-L190)；倍率/跳时/标签 [rf_sky_view.py:L1702-L1703](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L1702-L1703)、[L6002-L6040](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L6002-L6040)、[L6101-L6128](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L6101-L6128)、[L1987](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L1987) | 保持 `test_sky_time_lock.py` / `test_time_input_guard.py` 绿 |
| P0-04 | 双环锁定 | **有** | 仅自测 | 四组合文案 [rf_sky_view.py:L1855](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L1855)；站址失效 [L2220](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L2220)；勾选回写 [L2767-L2777](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L2767-L2777)；S15 修复 [main_window.py:_teardown_connection:L3817-L3823](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3817-L3823) | 补断连探针回归；S15 见 §2 |
| P0-05 | 观测站单一来源 | **半有** | 仅自测（无真 GNSS） | 统一入口 [main_window.py:L6249-L6275](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L6249-L6275)；配置 [L7044-L7047](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L7044-L7047)；第二套输入 [new_spacetime_panel.py:L327-L347](file:///D:/mbdsdr/MBDSDR/desktop/new_spacetime_panel.py#L327-L347)、[L498-L529](file:///D:/mbdsdr/MBDSDR/desktop/new_spacetime_panel.py#L498-L529) | S14：面板改坐标不回写/不落盘/不影响天空；AC-05.1–05.4 |
| P0-06 | SDR 工作台基本功 | **半有** | 仅自测（无真机） | 调谐语义 [spectrum_widget.py:L872](file:///D:/mbdsdr/MBDSDR/desktop/spectrum_widget.py#L872)、[L1818-L1825](file:///D:/mbdsdr/MBDSDR/desktop/spectrum_widget.py#L1818-L1825)、[L1939-L1941](file:///D:/mbdsdr/MBDSDR/desktop/spectrum_widget.py#L1939-L1941)；刻度/峰值 [L915-L972](file:///D:/mbdsdr/MBDSDR/desktop/spectrum_widget.py#L915-L972)、[L438-L528](file:///D:/mbdsdr/MBDSDR/desktop/spectrum_widget.py#L438-L528) | 已修 B1/S3/S8（待回归）；待修 S1/S9（AC-06.6–06.9） |
| P0-07 | 录音、回放、录音库 | **半有** | 仅自测 | 录音 [main_window.py:L4950-L5037](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L4950-L5037)；停止/边车 [L5039-L5085](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L5039-L5085)；回放 [L6044-L6120](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L6044-L6120)；`load_iq` [baseband_io.py:load_iq:L244-L327](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/baseband_io.py#L244-L327) | 已修 S5；待修 S4/S6/S7/M4/M5/L3（AC-07.5–07.9） |
| P0-08 | AI 能力协商 | **半有** | 仅自测（假模型/规则替身） | 账本 [sky_negotiation.py:L36-L52](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/sky_negotiation.py#L36-L52)、[L164-L297](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/sky_negotiation.py#L164-L297)；协商文案 [L315-L375](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/sky_negotiation.py#L315-L375)（"不是问卷、不是固定流程" `:349`；"不要生成表单/固定顺序" `:367-368`）；工具 [agent.py:L2236-L2337](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/agent.py#L2236-L2337) | 真 provider 未联调；无"固定问卷"运行时断言；AC-08.5 阈值待拍板 |
| P0-09 | 一句话收星闭环 | **半有** | 仅自测 | 动作总线/执行器 [main_window.py:L1330-L1402](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L1330-L1402)；人工门 [L3875-L3900](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3875-L3900)、[L3915](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3915)、[L3961](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3961) | 已修 S13；待修 S10、S11（旧链路） |
| P0-10 | 真实性与诚实性基线 | **半有** | 部分真机 + 部分仅自测 | 错误目录 [error-catalog.md](file:///D:/mbdsdr/MBDSDR/docs/error-catalog.md)；离线 A/B [offline-behavior.md](file:///D:/mbdsdr/MBDSDR/docs/offline-behavior.md)；配置修复 [main_window.py:L6939-L6956](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L6939-L6956)、[L6995-L7033](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L6995-L7033) | 已修 S2；待修 S12；H-3/H-8 未全绿 |
| P0-11 | 动态 UI 边界 | **半有** | 仅自测 | 总线 [main_window.py:L1261-L1269](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L1261-L1269)；执行器 [L1271-L1325](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L1271-L1325) | 比例未校验（`L1314` 直接 `int()`）；无撤销栈（M10）；AC-11.3/11.4 |

> **§2.4 战略节证据**：[PRD.md §2.4](PRD.md) 的体验基准是 `D:\mbdsdr\output\mbdsdr-product\dist\index.html`（955,391 字节，2026-11-04 23:03:57，自包含）；原型演示项与"不许照抄"清单见 §2.4.4，逐项走查以该表为准。

---

## 2. 23 条流程缺陷 → P0 → 专线 → 完成判据（无孤儿）

> 专线取值：**接入层**（后端/读线程/连接）、**UI频谱**（频谱/页签/布局）、**操作逻辑**（状态机/窗口/互斥）、**天空**（站址/双环/轨道）、**工具审计**（AI 工具/人工门/错误码）、**数据**（录音文件/sidecar/配置）。
> 23 = B1/B2 + S1–S15 + M2/M4/M5/M10 + L1/L3。每一行都有唯一接手专线；无"谁都接不了"的条目。

| # | P0 | 缺陷 | 专线（接手人） | 完成判据（DoD） | 现状 | 证据 |
|---|---|---|---|---|---|---|
| 1 | P0-06 | B1 换设备不复位旧后端 | 接入层 | 切换时旧后端 `disconnect` 必被调用、旧读停止、新设备真实出数后 UI 才显示"已连接"；失败回滚旧后端 | **已修，待回归** | [main_window.py:_teardown_connection:L3773-L3845](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3773-L3845) |
| 2 | P0-01 | B2 窗口恢复可能到屏外 | 操作逻辑 | 启动位置必须落在任一屏可见区内；越界回主屏；菜单提供「重置窗口位置」 | 待修 | [main_window.py:L7036-L7043](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L7036-L7043) |
| 3 | P0-06 | S1 首启引导挂不可见页 | UI频谱 | 首启 5s 内，当前可见页出现引导横幅，含「连接」入口；8s 后仍有常驻入口 | 待修 | [L1003](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L1003)、[L1038-L1047](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L1038-L1047)、[L1069](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L1069) |
| 4 | P0-10 | S2 配置损坏静默覆盖 | 数据 | 损坏文件先备份并提示；保存原子写；损坏且备份失败时跳过保存 | **已修，待回归** | [L6939-L6956](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L6939-L6956)、[L6995-L7033](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L6995-L7033) |
| 5 | P0-06 | S3 重建删用户 VFO/覆盖解调 | 操作逻辑 | 重建后用户 VFO 数量不变；解调模式保持用户选择，不被 `status.demod_mode` 覆盖 | **已修，待回归** | [L4149](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L4149)、[L4472-L4477](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L4472-L4477) |
| 6 | P0-07 | S4 录音中改率混流 | 数据 | 改率生成新文件段或明确拒绝；读取端按 `sample_rate_changes` 分段；`duration_s` 自洽 | 待修 | [main_window.py:L3608-L3619](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3608-L3619)、[baseband_io.py:load_iq 分段语义:L244-L327](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/baseband_io.py#L244-L327) |
| 7 | P0-07 | S5 录音/回放不互斥 | 操作逻辑 | 开始回放前停录并落盘；录音状态不假活；回放结束不自动续录 | **已修，待回归** | [L4953-L4956](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L4953-L4956)、[L6085-L6091](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L6085-L6091) |
| 8 | P0-07 | S6 长录音同步 `.tolist()` | 数据 | ≥12 万样本回放不阻塞 UI（后台/分块）；`load_iq` 不返回 Python list | 待修 | [baseband_io.py:iq.tolist():L285/L564](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/baseband_io.py#L285)、[main_window.py:_replay_recording](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L6947) |
| 9 | P0-07 | S7 写盘失败静默退内存 | 数据 | 磁盘不可用直接拒绝开始；写失败停录并报已写字节；内存缓冲不得冒充落盘 | 待修 | [L5014-L5018](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L5014-L5018) |
| 10 | P0-06 | S8 断源无上报、频谱定格 | 接入层 | 断源 2s 内 UI 显示「已断开（原因）」；频谱停止并清帧；给重连入口；不得无限重试假活 | **已修，待回归** | [receive_pipeline.py:STALL_TIMEOUT_S/read_failed:L87-L94](file:///D:/mbdsdr/MBDSDR/desktop/receive_pipeline.py#L87-L94)、[main_window.py:_on_pipeline_read_failed:L4790-L4820](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L4790-L4820)、[接线 L4767](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L4767) |
| 11 | P0-06 | S9 连接失败只改标签 | 接入层 | 连接失败禁用调谐/录音/回放等依赖控件，并显示原因；恢复后自动解禁 | 待修 | [L3338-L3349](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3338-L3349) |
| 12 | P0-09 | S10 命令条绕过忙锁 | 工具审计 | 命令条与 AI 面板共享同一忙锁；连发两条只有一条执行，另一条排队或拒绝 | 待修 | [ai_panel.py:L659-L661](file:///D:/mbdsdr/MBDSDR/desktop/ai_panel.py#L659-L661)、[main_window.py:L3450-L3455](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3450-L3455) |
| 13 | P0-09 | S11 失败前 UI 已改 | 操作逻辑 | 工具失败时频率/模式/音量不变；先执行、成功后同步 UI | 部分已修（硬件链路成功后才同步；旧链路仍先同步） | [L3929-L3931](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3929-L3931) vs [L3953-L3969](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3953-L3969) |
| 14 | P0-10 | S12 缺参落到演示频率 | 工具审计 | 缺 `freq_mhz` 直接报错；UI 不变；不得出现 98.5 MHz | 待修 | [L3940-L3943](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3940-L3943) |
| 15 | P0-09 | S13 人工门是模型自报 | 工具审计 | 入口剥离 `arm_manual/confirmed`；真人弹窗确认后才下发；超时=拒绝；全程审计 | **已修，待回归** | [L3875-L3900](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3875-L3900)、[L3915](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3915)、[L3961](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3961) |
| 16 | P0-05 | S14 站址两套来源 | 天空 | 面板输入回写唯一事实源并落盘；天空/过境/AI 同刻 az/el 误差 ≤0.01° | 待修 | [new_spacetime_panel.py:L327-L347](file:///D:/mbdsdr/MBDSDR/desktop/new_spacetime_panel.py#L327-L347)、[L498-L529](file:///D:/mbdsdr/MBDSDR/desktop/new_spacetime_panel.py#L498-L529)、[main_window.py:L6249-L6275](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L6249-L6275) |
| 17 | P0-04 | S15 断连不停跟踪 | 天空 | 断开/切换设备前先停跟踪；`frequency_hz` 不再被多普勒改写 | **已修，待回归** | [main_window.py:_teardown_connection:L3817-L3823](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L3817-L3823) |
| 18 | P0-01 | M2 星象仪恢复成窗口化 | 操作逻辑 | 启动恢复星象仪即全屏；退出后下次启动仍全屏 | 待修 | [L1700-L1702](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L1700-L1702)、[L1767-L1774](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L1767-L1774) |
| 19 | P0-07 | M4 无录音库列表 | 数据 | 录音库可列出/回放/删除；无文件时按钮置灰并说明 | 待修 | `mbdsdr_ai/recording_library.py` 存在，`desktop/` 零引用；回放仅 [QFileDialog:L6044-L6060](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L6044-L6060) |
| 20 | P0-07 | M5 崩溃残留无提示 | 数据 | 启动扫描未完成 sidecar/残留文件并在 UI 提示（可恢复/可删除） | 待修 | [L4950-L5018](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L4950-L5018) 无启动扫描 |
| 21 | P0-11 | M10 无撤销栈/Ctrl+Z | UI频谱 | 有撤销栈并支持 Ctrl+Z；或移除一切"可撤销"暗示；两者择一且文档一致 | 待修 | [L1271-L1325](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L1271-L1325) 只返回 inverse，无栈 |
| 22 | P0-01 | L1 退出丢最大化 | 操作逻辑 | 退出星象仪恢复进入前的最大化/普通状态 | 待修 | [L1733-L1734](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L1733-L1734) |
| 23 | P0-07 | L3 同秒录音覆盖 | 数据 | 同一秒两次录音生成两个文件（毫秒/序号），不覆盖 | 待修 | [L5002-L5003](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L5002-L5003) |

**孤儿检查**：23 行全部有唯一专线，分布为 **接入层 3**（B1/S8/S9）、**UI频谱 2**（S1/M10）、**操作逻辑 6**（B2/S3/S5/S11/M2/L1）、**天空 2**（S14/S15）、**工具审计 3**（S10/S12/S13）、**数据 7**（S2/S4/S6/S7/M4/M5/L3），合计 23。**无孤儿；无"谁都接不了"的条目。**

---

## 3. 收口工单（按专线派活）

| 工单 | 专线 | 对应 P0 / 缺陷 | 完成定义 |
|---|---|---|---|
| W-01 窗口与形态恢复 | 操作逻辑 | P0-01 / B2、M2、L1 | 启动位置校验 + 重置入口；星象仪启动即全屏；退出还原最大化；三条探针复跑 |
| W-05 站址单例 | 天空 | P0-05 / S14 | 面板输入回写唯一事实源并落盘；三面 az/el ≤0.01°；AC-05.1–05.4 |
| W-06 连接与空态 | 接入层 + UI频谱 | P0-06 / S1、S8、S9 | 首启引导在当前页；断源 2s 内可见；失败禁用依赖控件；AC-06.7/06.9 |
| W-07 录音状态机与库 | 数据 + 操作逻辑 | P0-07 / S4、S6、S7、M4、M5、L3 | 改率分段或拒绝；后台加载；写失败即停；录音库入口；启动残留提示；同秒不覆盖 |
| W-08 真模型协商回归 | 工具审计 | P0-08 | 建议语料 ≥40 条（**不设阈值**）；不重复问；`unknown≠available`；无固定问卷断言；通过阈值待定，当前不设阈值 |
| W-09 收星并发与 UI 一致性 | 工具审计 + 操作逻辑 | P0-09 / S10、S11 | 共享忙锁；失败零 UI 改动；AC-09.7/09.8 |
| W-10 诚实性反例 | 工具审计 + 数据 | P0-10 / S12 | 删除 98.5 MHz 兜底；H-11 用例；S2 回归保持绿 |
| W-11 动态 UI 边界 | UI频谱 | P0-11 / M10 | 比例非法返回 `ERROR` 且界面不变；撤销栈或移除暗示；AC-11.3/11.4 |

> 已关闭工单：W-02 星表接线（P0-02 已完成）；W-04 断连收跟踪（S15 已修）。保留回归用例，不再派开发。

---

## 4. P0 之外的前置条件（不拍板就会卡住）

| 编号 | 事项 | 卡住什么 | 影响 |
|---|---|---|---|
| L18 | `arm_manual` 是否允许会话 token 自动 arm | 接收写与发射写是否同门 | P0-09；当前实现为"真人弹窗、超时拒绝"，token 未做 |
| L19 | 发射频段/合规策略 | 发射演示与默认拒发口径 | 演示脚本能否包含发射 |
| L22 | 本轮集成验收红线 | M2 出口 | 环境红/真红的处置口径 |
| L23 | 天空/AI 时间基准分叉 | P1-02 与 AI 结论一致性 | 违反 H-8 |

---

## 5. P1/P2 现状摘要

| 条目 | 现状一句话 |
|---|---|
| P1-01 受晒接通 | `sat_illumination.py` 接口在，`orbit.py` 无 `sunlit` 字段 → 天空全显示「未知」（L34） |
| P1-02 时间基准 | `sky_view_visible` 走 TimeEngine，`sdr_satellite_sky_view` 走墙钟（L23） |
| P1-04 dB 自动量程 | 只有两个滑杆（`spectrum_widget.py:1699-1711`），量程默认写死；无自动/Y 拖动 |
| P1-05 真实录音库 | 迁移/回收站/损坏索引仅合成库自测（L10） |
| P1-06 真模型回归 | L12–L15 全部仅自测 |
| P1-07 MCP 面 | 82 个控制器工具 vs 495 AI 工具，未拍板（L21） |
| P1-09 DPI/触屏 | `main.py` PassThrough；布局四档基线未跑 |
| P2-01 银河 | 无数据、不画（L25/L28），状态卡已如实标注 |
| P2-05 打包许可 | 星表已进 wheel/sdist 且有包内断言（`star-catalog.md` §7 第 10 条）；第三方许可清单待补 |

---

## 6. 复跑命令（证据可刷新）

```powershell
python scripts/ai_tool_consistency.py
python scripts/tool_availability_audit.py
python scripts/ui_interaction_audit.py
$env:MBDSDR_OFFLINE=1; python -m pytest mbdsdr_ai/tests/test_offline_behavior_audit.py -q
python -m pytest tests/test_sky_dual_loops.py tests/test_sky_time_lock.py tests/test_sky_readouts.py -q
```

> 行号随提交浮动；本表引用 2026-11-04 当前工作树。审计文档 `docs/interaction-flows.md` 的缺陷行号可能滞后于已修代码，以本表"证据"列的当前行号为准。
