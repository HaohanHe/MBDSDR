# 第四十一阶段 · 未解决项总账（open items master ledger）

> 生成：Phase41 块2+4（README 定稿 + 文档横幅 + 本总账），2026-10-05。
> 口径：本表只登记**截至当前仍未关闭**的项；逐项带来源阶段与文件 file:line。数字一律取自实测或历轮交付报告，不推测、不预估已完成。
> 状态取值：`PENDING`（未开始/等外部条件）/ `部分落地`（主体已做、残留未跑）/ `登记未改`（本块红线禁改代码，仅留账）/ `待决策`（需产品/架构拍板）。

---

## A. 真机 / 硬件类（云内无硬件，全部待用户本地真机）

| 项 | 状态 | 卡点 | 解锁条件 | 溯源 |
|---|---|---|---|---|
| A1 真机端到端验收 `acceptance_run.sh`（selfcheck→diag_wizard→onboard adsb/apt/cw→exp_ota_run） | PENDING | 云 VM 无 USB/串口/声卡/GNSS，真机工具只能返回诚实空态，不能代替真机跑通 | 用户本地真机跑 `tools/acceptance_run.sh --event`，按 `docs/learn/phase6/P2-hw-report-format.md` 回传 JSON | phase4/B1、phase34/SPEC |
| A2 SSTV 真实过境射频出图（Robot72 实际行数/制式） | PENDING | 仅离线 `real_sstv.wav` 合成包流闭环出图；真实卫星过境射频未收过 | 活动现场首度过境实接收图，回写 runbook 对应行 | phase34/event-rx-runbook「未真机验证项汇总」#1 |
| A3 SSDV 物理层串联（IQ→BPSK→ASM→Viterbi→解扰→RS→218B 包）现场出 `.bin` | PENDING | `ccsds_rx.py` 零件就绪但未串入 `onboard --step all`；仅合成包流端到端验过 | 真机先手动录 IQ、现场快速产出包字节文件再喂 ssdv decode | phase34/recv-chain-conclusion §6 #1 |
| A4 真实下行频率/排班 + 正式 TLE | PENDING | 435.075/436.210 MHz 为社区旁证非官方规格；AutoTLE 快照仅至 2026-10-04，超 3 天需重拉 | 现场核实频率、重拉 TLE | phase34/event-rx-runbook「未真机验证项汇总」#3#4 |
| A5 arm64（Chromebook/Crostini）构建 + ctest + USB 直收 | PENDING | 仅文档拓扑；arm64 cmake 配置/全量编译/ctest 通过数/qt6+librtlsdr 包齐全性/USB 实机表现均未跑 | arm64 真机跑 `cmake --build` + `ctest` 回传 | phase30/arm64-verification-notes §6 |

## B. 论文 / 在线 LLM 类（无 key、无硬件，保持诚实空态）

| 项 | 状态 | 卡点 | 解锁条件 | 溯源 |
|---|---|---|---|---|
| B1 LLM 在线列（Fig.6 AMR 列 / Fig.10·Tab.VI 末行） | PENDING（`PENDING_ONLINE_RUN`） | 云内无 `MBDSDR_LLM_API_KEY`；经典规则与 KNN 本地真算，LLM 列诚实留空 | 带 key 环境跑 `exp_llm_baseline.py`、`exp_weak_model_toolcall.py` 回填 | phase10/P2-submission-check §5；phase33/SPEC |
| B2 OTA / 真实录制解码列（recorded） | PENDING（`empty_state_no_recording` n=0） | 云内无硬件录不到真实 SigMF | C++ recorder 录 SigMF 后 `exp_ota_run.py --recordings-dir` 回放，manifest 标 `recorded` | phase10/P2-submission-check §5；phase33/SPEC |
| B3 作者名单 / 单位 / 通讯邮箱 / 致谢 | PENDING | `main.tex:30-34`、`main.md:5` 为「Firstname Lastname… Bi4MIB open-source contributors」匿名占位 | 人工补真实作者、单位、通讯邮箱、致谢；删 `\thanks` 的 PENDING | phase10/P2-submission-check §5；phase33/SPEC |
| B4 Fig.5 + Fig.6 三类对比图合并 | PENDING（依赖 B1） | 现为 120/SNR 与 60/SNR 两张预留 LLM 曲线的图，未合并 | LLM 曲线（B1）到位后合并为一张三类对比图 | phase10/P2-submission-check §5 |
| B5 论文 LaTeX 全量编译 + 转义修正 | PENDING（投稿前必做） | 环境无 `IEEEtran.cls`；`main.tex:55` 摘要 `PENDING_ONLINE_RUN` 下划线未转义会 `Missing $ inserted` | 带 IEEEtran 的 TeX 环境全量编译；`PENDING_ONLINE_RUN`→`\texttt{}` 或转义 `\_` | phase10/P2-submission-check §4；phase33/SPEC |

## C. LLM transport / AI 运行时

| 项 | 状态 | 卡点 | 解锁条件 | 溯源 |
|---|---|---|---|---|
| C1 429/503 真实在线重试 e2e | 部分落地 | transport 已透传真实 HTTP 状态码（`llm_client.cpp` 读 `HttpStatusCodeAttribute`）、`classifyLlmError(int httpStatus,…)` 按码分类（`429/5xx` 退避、`4xx` 终态）；但云内无 key/网络，真实 socket 限流/5xx 退避路径从未在线实跑 | 带 key 在线触发一次真实 429/5xx，观察退避重试与写动作不自动重试 | phase31/_WAVE2_AGENT_LANDING 未解决项 #2#3；phase32 block2 |

## D. UI 弹性 / 反硬编码（登记未改代码）

| 项 | 状态 | 卡点 | 解锁条件 | 溯源 |
|---|---|---|---|---|
| D1 窄窗 3 处 | 登记未改 | ①顶部工具条单行不折行：窄窗下拉值截断、游标按钮挤靠、S-meter 刻度连排；②主窗口最小逻辑宽钳到 960px，820×640 目标宽不可达；③部分 QComboBox/QSpinBox 行内 26px 未逐个补 `setMinimumHeight(44)` | 后续 Wave 定工具条折行/省略策略；是否下调频谱最小宽；逐控件补可点区 ≥44 | phase37/three-state-verification §4 |
| D2 `compass_dial.dart` 画布字面量 | 登记未改 | 极坐标画布几何（标注字号 8.5/8/10.5、dotR 3.0/4.5/5.0/7.5、strokeWidth 1.2/1.6/1.4/1.0、标签偏移 10/15/8）仍为裸数，未入 token 组 | 单开「sky-token 组」对齐桌面 `kSky*`/`kMap*` | phase37/mobile-audit §4 #1 |

## E. 移动端能力差（G 候选，待 Wave2 排期）

| 项 | 状态 | 卡点 | 解锁条件 | 溯源 |
|---|---|---|---|---|
| E1 mobile G2–G5 真差距未补 | PENDING（待排期） | G2 录制回放落地（高）/ G3 双游标 A/B（中）/ G4 扫频完备性：方向·暂停·命中停留·只扫书签·命中存书签（低-中）/ G5 AI 工具补面：`set_squelch`·bookmark CRUD·recordings list-delete·`scan_band`（中） | Wave2 按优先级逐项补、每项带 flutter 测试 | phase39/architectural-diffs §3 G2–G5；capability-matrix §9 |
| E2 mobile G1 / G6（同簇登记） | PENDING（待排期） | G1 AI 上下文压缩（移动 history 全量直传无折叠）；G6 多普勒实时 range-rate 补偿（`dopplerHz` 恒 null，仅捕获时一次性预测调谐） | G1 先定压缩策略；G6 需设计补偿引擎与安全边界 | phase39/architectural-diffs §3 G1/G6；capability-matrix §7#§8 |
| E3 mobile AI 会话重命名 + 压缩入口 | PENDING（小差距） | 移动无手动重命名入口；无「压缩上下文」按钮（长会话撑 token 预算） | 加重命名入口；接 G1 压缩 | phase39/capability-matrix §8 |
| E4 Wave2 边界决策 3 项 | 待决策 | ①远程解码面板是否补 `GET /` 发现 / `/status` 更多字段；②AI 压缩触发阈值轮数 + 摘要模型/是否同 key；③G6 多普勒先只显值还是自动微调调谐 | Wave2 会议拍板边界 | phase39/architectural-diffs §5 |

## F. 构建 / 测试遗留

| 项 | 状态 | 卡点 | 解锁条件 | 溯源 |
|---|---|---|---|---|
| F1 Phase40 全量构建截断 + 5 个 test target 未逐个复验 | 部分落地（预期全绿未亲跑） | `qt_audio_sink.h` 改动触发 ~30 目标重编被预算截断；`test_shortcuts`/`test_phase21_empty_state`/`test_device_ui`/`test_control_http_e2e_prod`/`test_ai_sessions_prod` 已改 QSettings 隔离但未逐个重跑 ctest | 单目标 `-j2` 重编后 `QT_QPA_PLATFORM=offscreen ctest --timeout 120` 全量复跑（预期 127/127） | phase40/test-stability §7 #3；phase41/SPEC 基线注 |

---

## 汇总

- 共 **18 条**：A 真机 5、B 论文/在线 5、C transport 1、D UI 2、E 移动 4、F 构建 1。
- 全部带阶段溯源，无新增、无推测；凡"预期/应"均已在"卡点"列如实标注为未亲跑确认。
- 本文件与根 `README.md`「真机与云测状态」「LLM 基线诚实说明」两节相互指向；关闭任一项后请在此表勾除并回写溯源。
