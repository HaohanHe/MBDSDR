# 文档断言白名单（存量豁免，只减不增）

> 门禁：`tests/test_claims_guard.py`。`docs/` 下任何「已修复/已提交/已落地」类断言，
> 四元组 `<结论> — <hash> — <path>:<line> — <改动后字面量>` 不完整即红；
> 本表只登记**建表时 HEAD 已提交**的未证实断言，逐条写「为什么暂不补证」。
> 销账方式：补四元组（变可证实）或删断言 → 从本表删行并把 `waived-total` 同步调小；
> **不许新增行**（只减不增）。新建/改写断言一律按红处理，先补齐四元组再提交。
>
> 指纹 = sha256(`文档\0断言原文`)[:12]；断言原文改动即指纹变化 = 按新增判红。
> 在途未提交的 docs 改动不享本白名单——那正是门禁要拦的「修改后无证据」。

<!-- waived-total: 138 -->

建表口径：HEAD 断言 144 条 = 可证实 2 + 文档过期 46 + 无 hash 96；白名单只覆盖后两类。

| # | 文档 | 指纹 | 断言摘要 | 暂不补证理由 |
|---|------|------|----------|--------------|
| 1 | docs/COORDINATION.md | c7357a8b2b36 | Gardner 环修复版已改进 lrpt_modem.py（中点插值+归一+钳制+热启动， | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 2 | docs/COORDINATION.md | 6e4913bfc99d | 已改为**让产品从原仓库代码长出来**：`build/sync_from_repo.py` 只读 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 3 | docs/COORDINATION.md | c8503b24ac33 | 已改：`31-celestial.js` 换「相机在球心 → 视图空间(X/Y/Z) → 全屏平面投影」， | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 4 | docs/COORDINATION.md | 376610dc684e | - **[11-05 22:00] 产品工程阶段 A 入库（5a977026，本线已提交）**：详见队列 #11。 | 有 hash 但缺 <path>:<line>，无法定位改动；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 5 | docs/COORDINATION.md | 33211081caac | GPScacodes.py 裁决更正（标准码的反极性版本，原"非标准"判词有误）； | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 6 | docs/COORDINATION.md | ad3e2bc78eab | 扫描器自指命中（HEAD 干净，`git grep` 无命中）；本条已改写为不引字 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 7 | docs/COORDINATION.md | 8ebc6df4d0a2 | 2. ~~tests/(B) `test_identity_redline` 误伤~~ **已修（fe43c74，WorkBuddy）**—— | 有 hash 但缺 <path>:<line>，无法定位改动；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 8 | docs/COORDINATION.md | b1f0ec5d9d1f | 现予以更正）： | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 9 | docs/COORDINATION.md | ff534af6fa81 | （对应会话已修），全仓 `git grep` 仅剩台账自指。已按 AGENTS.md 第 1 条 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 10 | docs/COORDINATION.md | 8349229e45f2 | ① Gardner 修复版实测（代码已改默认关）② 擦除辅助 RS（2e+f≤32， | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 11 | docs/COORDINATION.md | 1807e9fedefe | 下一轮看到绿就以为已修。硬口径：任何红要么当天转绿并留下复跑命令， | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 12 | docs/COORDINATION.md | a813e6a71653 | 同样判红**）。**已修 `f0f68cc6`**（`mw(qapp, isolated_profile)`）， | 有 hash 但缺 <path>:<line>，无法定位改动；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 13 | docs/COORDINATION.md | 70caf05d9c64 | - 2026-11-05（接续线，**给操作逻辑线**）：PTT 置灰问题已修，按钮状态可复位。`sdr_backend.SDRBackend` 新增 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 14 | docs/COORDINATION.md | 8d5bd45fa3b9 | - 2026-11-05（硬编码与参数治理线，裁定落地 `12a43a0`）：三轮裁定已落地两条、 | 有 hash 但缺 <path>:<line>，无法定位改动；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 15 | docs/COORDINATION.md | 1694bdfa48dd | - 2026-11-05（硬编码线，补交 `6ed9ee7`，接上条 `12a43a0`）：③ 采样率已落地， | 有 hash 但缺 <path>:<line>，无法定位改动；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 16 | docs/COORDINATION.md | d8cb2fdab266 | 95 passed，agent 采样相关 21 passed。文档 §10-3 已改为"已落地"。 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 17 | docs/COORDINATION.md | 4ff0e566e818 | 撤销已提交内容只有两个合法动作： | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 18 | docs/PRD-现状对照.md | 7fa7679cc10c | / P0-04 / 半有 → **有** / S15 已修：`_teardown_connection` 在断硬件前先停跟踪（[main_window.py:_teardown_connecti… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 19 | docs/PRD-现状对照.md | 104dd169989e | / P0-06/07/09/10 / 仍半有，但缺陷减少 / B1（[_teardown_connection](file:///D:/mbdsdr/MBDSDR/desktop/main_wi… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 20 | docs/PRD-现状对照.md | 745c1a2c72ca | / P0-06 / SDR 工作台基本功 / **半有** / 仅自测（无真机） / 调谐语义 [spectrum_widget.py:L872](file:///D:/mbdsdr/MBDSD… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 21 | docs/PRD-现状对照.md | 9f8b98a391f2 | / P0-07 / 录音、回放、录音库 / **半有** / 仅自测 / 录音 [main_window.py:L4950-L5037](file:///D:/mbdsdr/MBDSDR/des… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 22 | docs/PRD-现状对照.md | e3d4556a8938 | / P0-09 / 一句话收星闭环 / **半有** / 仅自测 / 动作总线/执行器 [main_window.py:L1330-L1402](file:///D:/mbdsdr/MBDSDR… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 23 | docs/PRD-现状对照.md | b29658055d0c | / P0-10 / 真实性与诚实性基线 / **半有** / 部分真机 + 部分仅自测 / 错误目录 [error-catalog.md](file:///D:/mbdsdr/MBDSDR/do… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 24 | docs/PRD-现状对照.md | d9e57f6c5804 | / 1 / P0-06 / B1 换设备不复位旧后端 / 接入层 / 切换时旧后端 `disconnect` 必被调用、旧读停止、新设备真实出数后 UI 才显示"已连接"；失败回滚旧后端 / *… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 25 | docs/PRD-现状对照.md | 5daf1390ad8d | / 4 / P0-10 / S2 配置损坏静默覆盖 / 数据 / 损坏文件先备份并提示；保存原子写；损坏且备份失败时跳过保存 / **已修，待回归** / [L6939-L6956](file:… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 26 | docs/PRD-现状对照.md | f7446423922f | / 5 / P0-06 / S3 重建删用户 VFO/覆盖解调 / 操作逻辑 / 重建后用户 VFO 数量不变；解调模式保持用户选择，不被 `status.demod_mode` 覆盖 / **… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 27 | docs/PRD-现状对照.md | 7023b11e620b | / 7 / P0-07 / S5 录音/回放不互斥 / 操作逻辑 / 开始回放前停录并落盘；录音状态不假活；回放结束不自动续录 / **已修，待回归** / [L4953-L4956](file… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 28 | docs/PRD-现状对照.md | c7a90b659b24 | / 10 / P0-06 / S8 断源无上报、频谱定格 / 接入层 / 断源 2s 内 UI 显示「已断开（原因）」；频谱停止并清帧；给重连入口；不得无限重试假活 / **已修，待回归** /… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 29 | docs/PRD-现状对照.md | 680638f21a71 | / 13 / P0-09 / S11 失败前 UI 已改 / 操作逻辑 / 工具失败时频率/模式/音量不变；先执行、成功后同步 UI / 部分已修（硬件链路成功后才同步；旧链路仍先同步） / [… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 30 | docs/PRD-现状对照.md | edb24671fe3e | / 15 / P0-09 / S13 人工门是模型自报 / 工具审计 / 入口剥离 `arm_manual/confirmed`；真人弹窗确认后才下发；超时=拒绝；全程审计 / **已修，待回归… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 31 | docs/PRD-现状对照.md | 70783600302e | / 17 / P0-04 / S15 断连不停跟踪 / 天空 / 断开/切换设备前先停跟踪；`frequency_hz` 不再被多普勒改写 / **已修，待回归** / [main_window… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 32 | docs/PRD-现状对照.md | c2272b0d849a | > 已关闭工单：W-02 星表接线（P0-02 已完成）；W-04 断连收跟踪（S15 已修）。保留回归用例，不再派开发。 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 33 | docs/PRD-现状对照.md | d764380e7ac9 | > 行号随提交浮动；本表引用 2026-11-04 当前工作树。审计文档 `docs/interaction-flows.md` 的缺陷行号可能滞后于已修代码，以本表"证据"列的当前行号为准。 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 34 | docs/PRD.md | 6cbe1950c775 | > 复核口径提醒：审计文档与代码存在时间差。当前代码里 **B1、S2、S3、S5、S13、S15 已修**（详见对照表"现状"列），全星表 UI 也已接线（`desktop/rf_sky_vi… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 35 | docs/PRD.md | 4b6bcf68e4ee | 6. 把时间拨到 ×300：轨迹按模拟时刻重算；锁定目标逐帧直贴并提示「快转下平滑跟随跟不上，已改为逐帧直贴」。 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 36 | docs/PRD.md | 6ac82f40fcc2 | 7. 时间跳转 +10 分钟：若目标仍在地平线上，继续锁定并直接贴住；已落地则诚实解锁并说明。 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 37 | docs/PRD.md | 989879db442e | - **现状**：有｜证据：`mbdsdr_ai/new_spacetime.py:142-190`（`set_rate` 含 rate=0 暂停与负值倒流）；`desktop/rf_sky_v… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 38 | docs/PRD.md | ebdcf96dddbc | - **现状**：有｜证据：`docs/limitations-register.md` L31；`desktop/rf_sky_view.py:1855`（`loop_state_lines`… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 39 | docs/PRD.md | 239144875224 | - **现状**：半有｜证据：`desktop/spectrum_widget.py:872`（`tune_mode` 默认 `center`）、`:1818-1825`（中心/VFO 切换按钮… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 40 | docs/PRD.md | 1ed72551432d | - **现状**：半有｜证据：`desktop/main_window.py:4950-5037`（`_start_recording`，流式写盘）、`:5039-5085`（`_stop_re… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 41 | docs/PRD.md | ad8fbb6e0a6d | - **现状**：半有｜证据：动作总线 `desktop/main_window.py:1330-1340` 与执行器 `:1341-1402`（`TARGET_MISMATCH`/`UNAVA… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 42 | docs/PRD.md | 7502bd759f36 | - **现状**：半有｜证据：错误码单一来源 `docs/error-catalog.md` + `tests/test_error_catalog_guard.py`；离线 A/B 二分 `d… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 43 | docs/PRD.md | 7f6683248e15 | / P2-05 / 打包与许可交付 / 星表已进 wheel/sdist 并有包内断言（`test_star_catalog_packaging.py`）；再分发需保留 HYG 署名与 CC B… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 44 | docs/PRD.md | b295d219aef9 | - AC-R11（待补）：自动重连接入后，断网 30s 内状态依次 `retrying → gave_up`；重连成功回填真实频率（不得回填 0 Hz，`4abde1a` 已修假 0 Hz）。 | 有 hash 但缺 <path>:<line>，无法定位改动；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 45 | docs/PRD.md | 707861baadb5 | / 时间源 / `docs/time-source.md` / 18 场景、14 条违反已修、测试纪律 / | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 46 | docs/constellations.md | ac337af5dc66 | > 结论先行：IAU 88 座已补齐（拉丁名/缩写/中文名/示意连线，88/88）； | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 47 | docs/design-basis-sky-figma-note.md | 3eea4e7e7446 | **对「334×334 大圆」这一描述的更正**：`284_177/index.module.scss` 写的是 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 48 | docs/design-basis.md | d1db6fb52bad | 取证附带发现（`af7208b` 已修）：`test_ui_layout_locks.py` 原来的 `800x600` 参数**实际跑在 1000×600**。 | 有 hash 但缺 <path>:<line>，无法定位改动；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 49 | docs/design-basis.md | 23b9f2f6b319 | / 4 / 无设备引导是「基准外」的我们多做项 / 横幅 + 状态栏双份引导；横幅已改单行不换行（`e6f0e7d`） / 仍是双份占位：横幅独占一行高度 / wordWrap 已撤（§3）；下… | 有 hash 但缺 <path>:<line>，无法定位改动；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 50 | docs/design_tokens_mapping.md | ac1079782e56 | （4.15→5.22:1）均已修到达标。品牌橙新增 `primary_text` `#9A5F33` | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 51 | docs/hardcoded-parameters.md | c850ad585a26 | / A10 / `default_sample_rate_msps` / 默认采样率 / `2.4` / 设备支持档位 / MS/s / 设备与后端 / "启动时优先用 2.4 MS/s；设备不… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 52 | docs/hardcoded-parameters.md | 707ff33e1312 | - **A11 调谐步进**：**更正**——C++ 调谐步进本就是 10 kHz（§D-2 撤回）， | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 53 | docs/hardcoded-parameters.md | 96044a799385 | / P-1 / `cpp/src/core/tokens.h`（频谱线已改） / Python `6.0` 与 C++ 原 `15.0` 两套显示峰阈值；原"统一到 15 dB"已被实测驳回 /… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 54 | docs/hardcoded-parameters.md | c62a48c734e9 | - ~~**D-3 FFT 档位**~~ **已收口（2026-11-05）**：`FFT_SIZES` 已删掉 8192/16384， | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 55 | docs/hardcoded-parameters.md | 38856a625a5c | / 1 / 静噪门限统一到「相对该模式噪声基线的余量」（刻度 `-100..-20`，默认 `-50` ≈ 6 dB 余量） / **已落地**（§10-1）。剩余：`main_window` … | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 56 | docs/hardcoded-parameters.md | 601d94035be0 | / 2 / 过境仰角拆成 `VISIBLE=5°` / `PASS=10°`，`next_pass()` 用 10° / **已落地**（§10-2）。剩余：`rf_sky_view` 的 `P… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 57 | docs/hardcoded-parameters.md | c62289517319 | / 3 / 默认采样率 2.4 MS/s（按设备能力回落） / **已落地**（§10-3）：内核默认 2.4M + `nearest_sample_rate()` 回落 + 状态回报实际值；三… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 58 | docs/hardcoded-parameters.md | 7c1d44bdf464 | / 控制器 / 固定 10° / 24 h 字面量 / **已改为显式入参注入**：`get_satellite_pass(min_elevation_deg=None, window_hour… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 59 | docs/hardcoded-parameters.md | da98f7eb5925 | / 1 / `mbdsdr_ai/analog_demod.py:123-131` / `"am" ... sql_db=-40.0` / `"wfm" ... sql_db=-30.0` / … | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 60 | docs/hardcoded-parameters.md | 6694cfa54dfb | / 9 / `desktop/main_window.py:5534-5535` / `dbfs = 10.0 * math.log10(p + 1e-12)` / `if dbfs < sel… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 61 | docs/hardcoded-parameters.md | db1b11eabf00 | 表中 1-7、10-14 是**明确登记的另一些测量点/死参数**，处置如上，不冒充已收口。 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 62 | docs/limitations-register.md | 2d31e68b87e9 | / L29 / 快转（×60/×300/×1800）一致性是**按模拟时刻重算 + 诚实提示**，不是「延平滑」 / `desktop/rf_sky_view.py`：`LOCK_SMOOTH_… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 63 | docs/limitations-register.md | 1ca40533f6e1 | / L32 / 时间控制与摄像机锁定的交互语义（倍率切换 / 一次性跳时 / 暂停 / 倒放） / `desktop/rf_sky_view.py`：`notify_time_jump/_pen… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 64 | docs/limitations-register.md | 3ca1335288b3 | enum 校验先于 handler），**真红、已修**； | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 65 | docs/limitations-register.md | 3e1e2396b627 | / 控制台 Ctrl+C（仅 `python desktop/main.py` 开发场景；pythonw/打包版无控制台） / **中，已降为低** / 修前：中断被 PySide 吞，可能吃掉… | 有 hash 但缺 <path>:<line>，无法定位改动；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 66 | docs/limitations-register.md | 6d439f88d421 | / L46 / **`solar_system` 三个后端把「日月距角」当成「月处相位角」直接套 `0.5*(1+cos)`，照度整条反转**：2026-11-09 13:30 UTC 新月（距… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 67 | docs/limitations-register.md | 93c228e42146 | / L47 / **`get_planet_position` 把行星日心距与地心距都填成 `pos.distance_au`**，日-行星-地球三角形退化为 `cos χ = R/(2Δ)`：… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 69 | docs/limitations-register.md | 40bffa49dc65 | / L50 / **TLE 老化后过境仍照算，且看起来和新 TLE 一样** / 30 天：首条预测 20:47:47 / 12.18°（较新鲜首条 +7h12m），真实中天时刻 **-43.3… | 有 hash 但缺 <path>:<line>，无法定位改动；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 70 | docs/limitations-register.md | fddb5d89bbbc | / L52 / **`station_warning` 的真实可见面（不是「进了日志就算」）** / (0,0) 控制器实测返回 **2 条**并带 `station_warning="null… | 有 hash 但缺 <path>:<line>，无法定位改动；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 71 | docs/narrow-screen-plan.md | 15fb3ab8e89a | > DPR 1.0 口径，验收线 **1344×700**（1366×768 扣边框/任务栏）。已落地： | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 72 | docs/narrow-screen-plan.md | f2c2f94e32f7 | 2. **已落地（2026-11-05）**：顶栏 1625→**1265**（§2.3.1）、频谱 1941→**688**（§2.1.1）、 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 73 | docs/narrow-screen-plan.md | 8db68d25d0bd | > **已更正**：`minimumSizeHint()` ≠ 窗口地板；声明最小宽（1000） | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 74 | docs/narrow-screen-plan.md | 9c3f596afb44 | 6. 上述省量是估算，**已落地实测为 85 / 140**（见 §3.1）；若后续再改分组框内容， | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 75 | docs/narrow-screen-plan.md | 87bcc22756c3 | **宽度已收口**：offscreen 窗口宽 **1625→1265**（顶栏，§2.3.1）；真屏 **1237→960**。 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 76 | docs/network-layer.md | c5867aec39fc | 该复现的 faulthandler 实抓栈（本机，Python 3.14，2s 超时；线程名已改）： | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 77 | docs/packaging.md | be31b61be6c1 | 2. 修法已落地：[MBDSDR.spec](../../desktop/MBDSDR.spec) 增加按文件系统遍历 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 78 | docs/packaging.md | 19ac2a62d66a | Qt 随后自己 abort 成英文弹窗；已改为按实际请求平台精确匹配后复测通过。） | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 79 | docs/packaging.md | 2882e4fa8ea4 | / 约束 / 实测行为 / 期望 / 差距 / 已修 / | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 80 | docs/packaging.md | fa864f5bb53e | 4. ACL 级只读（非 Program Files）不再靠启动期同步判断：已改为后台线程试写 + | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 81 | docs/parameter-unification.md | e1906b2ae6d7 | / 1 / 连接对话框 `sr_combo` 能力化 + 逐档回读/显式拒绝 / **已落地** / 删除 11 档直灌 → `_fill_sr_combo(device)` 按设备能力生成；`… | 有 hash 但缺 <path>:<line>，无法定位改动；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 83 | docs/parameter-unification.md | 50a84f50aa61 | / 3 / 逐档回读端到端证据 / **已落地（真机 + 注入双证）** / 新增 `scripts/sample_rate_readback_check.py`：逐档 `set_sample_… | 有 hash 但缺 <path>:<line>，无法定位改动；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 85 | docs/prototype-gap.md | d8d2b47e377a | / 批 2（已完成） / A10 DSO 四步（两型 glyph、中文标签、真实轴比、轴比未知）；状态卡超高滚动 / `pytest tests/test_sky_dso_layer.py te… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 86 | docs/prototype-gap.md | 9f84477f0dae | **OpenNGC v2 换源口径（已落地）**：`dso_catalog.json` 已是 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 87 | docs/prototype-gap.md | dcf687b3939a | P0–P4 顺序未失效。**每档上限已落地（当前实现 `LABEL_TIER_CAPS`）**： | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 88 | docs/prototype-gap.md | acefb2e8145b | - **已落地优化**：视图旋转 `lru_cache(32)`；方位网格/地景/地平线批量投影； | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 89 | docs/python-crash-20261105.md | 5b18bcb1ebb6 | **结论一句话**：不是单条用例的确定性崩溃，也不是显卡/平台插件问题；直接崩溃点是 PySide6 绑定层空指针解引用（`pyside6.abi3.dll+0x1c91e`，落在导出符号 `P… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 90 | docs/python-crash-20261105.md | 96732be0ef35 | / ② / `desktop/main.py::_install_sigint_graceful_quit()`：`signal.SIGINT` 只置标志 + 0.2 s `QTimer` 调 … | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 91 | docs/real_hw_acceptance_20261103.md | e85843173c3b | / R5 / 录音落盘 / **PASS** / 录制中流式增长 2,883,584 → 5,898,240 B；stop 后 745,472 样本；两次采样率 sidecar `core:sa… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 92 | docs/real_hw_acceptance_20261103.md | c391462d01be | 独立基准工具的环境行为，非本仓库代码问题；验收脚本已改为不依赖 rtl_power | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 93 | docs/reference-mechanisms.md | 815c7035b8d7 | > **2026-11-05 更正**：`reference/` 下不存在 `decoders`、`blackmagic` 目录；`satdump` 已在本轮补读。见 §8。 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 94 | docs/reference-mechanisms.md | 0288c1e90a4d | / 卫星地影 / S12（`gpredict/src/sat-vis.c:82-105`） / **已补齐**：`mbdsdr_ai/sat_illumination.py:204-303`；`… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 95 | docs/reference-mechanisms.md | f292eb428cdb | **S12 更正细节**：上一轮记为「无」，本轮核实已实现。`sat_illumination.py:78-80` 定义 `sunlit/umbra/penumbra` 三态，`:204` 起为… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 96 | docs/reference-mechanisms.md | a8b23ef93460 | > - 「决定」三态：**照做**（机制已按参考思路落地，保持）／**改良后做**（路线不同或只落一半，已落地或已排给某条线）／**明确不做**（给理由与可追踪的挂账/验收门）。 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 97 | docs/reference-mechanisms.md | 352d301ca9b8 | / S1 摄像机锁定 / `StelMovementMgr.cpp:319-321`（`flagLockEquPos`）；`navigation.c:87-109`（slerp） / 已落地：`… | 587d55da 未改动 StelMovementMgr.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 98 | docs/reference-mechanisms.md | 8a09ecd34379 | / S4 消光公式 / `RefractionExtinction.cpp:35-61`（Rozenberg / Young） / 已落地但双份：`179ce20d` → `skyengine/… | 179ce20d 未改动 RefractionExtinction.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 99 | docs/reference-mechanisms.md | 6aadcf11ebd6 | / S7 标签优先级 / `labels.c:136-154`（priority + 远近排序） / 已落地：`rf_sky_view.py:3147-3150` 按星等/类型分级 / **明确… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 100 | docs/reference-mechanisms.md | 3a9bf1978155 | / S8 星等→半径/亮度 / `StelSkyDrawer.cpp:403-417`（<0.3px 丢弃、1.2px 防闪） / 已落地：`e5ca57ce` → `skyengine/sta… | e5ca57ce 未改动 StelSkyDrawer.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 101 | docs/reference-mechanisms.md | 7b9806c4d084 | / S9 视大小符号 / `Planet.cpp:3904`（`screenRd = angularRadius * pixelPerRad`） / 已落地：`9645d406` → `rf_s… | 9645d406 未改动 Planet.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 102 | docs/reference-mechanisms.md | c8ab08ecb937 | / S10 地平线/地景 / `Landscape.cpp:1030-1046`（ground/decor/fog 顺序） / 已落地：`a4611eae` → `rf_sky_view.py:… | a4611eae 未改动 Landscape.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 103 | docs/reference-mechanisms.md | 50303cb4498d | / S12 卫星地影 / `sat-vis.c:82-105`（`Sat_Eclipsed` + 晨昏门限） / 已落地：`3fc46b98` → `sat_illumination.py:20… | 3fc46b98 未改动 sat-vis.c；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 104 | docs/reference-mechanisms.md | cd2752b63e64 | / S13 卫星 az/el 极图 / `gtk-polar-view.c:202-246`（`r*(1-el/90)` + 方向枚举） / 已落地兼容接口：`rf_sky_view.py:28… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 105 | docs/reference-mechanisms.md | cc6c821a0041 | / S14 时间倍率/暂停/倒放 / `StelCore.cpp:1386-1413` / 已落地：`a7c7e410` → `new_spacetime.py:142-190` / **照做*… | a7c7e410 未改动 StelCore.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 106 | docs/reference-mechanisms.md | 94b84109dbfb | / P16 瀑布环形缓冲 / `sdrpp/core/src/gui/widgets/waterfall.cpp:611-616`（`(i+current)%height`）；`waterfal… | 82e32da2 未改动 sdrpp/core/src/gui/widgets/waterfall.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 107 | docs/reference-mechanisms.md | 9e7f60ee7dbf | / H1 统一信号源注册表 / `sdrpp/core/src/signal_path/source.h:9-31`；`source.cpp:10-60` / 已落地：`d4ebd5bf` → … | d4ebd5bf 未改动 sdrpp/core/src/signal_path/source.h；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 108 | docs/reference-mechanisms.md | 169770a0ddf5 | / H2 注销回落空源 / `source.cpp:19-33`（`nullSource`） / 已落地相反策略：`d4ebd5bf` → `device_manager.py:530` 抛错 … | d4ebd5bf 未改动 source.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 109 | docs/reference-mechanisms.md | 6c1e65043651 | / H3 设备枚举/刷新 / `rtl_sdr_source/src/main.cpp:117-122` 手动 `refresh`、`:394-395` 按钮 / 库层已落地：`d4ebd5bf… | d4ebd5bf 未改动 rtl_sdr_source/src/main.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 110 | docs/reference-mechanisms.md | fda3eaef80f8 | / H4 单设备打开失败隔离 / `rtl main.cpp:185-195`（失败仅 `flog::error`） / 已落地：`d4ebd5bf` → `device_manager.py:… | d4ebd5bf 未改动 main.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 111 | docs/reference-mechanisms.md | 8fcd780709ca | / H5 设备错误要求重启 / `gqrx/src/applications/gqrx/mainwindow.cpp:602-611`、`:364` / 已落地相反策略：`device_watc… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 112 | docs/reference-mechanisms.md | e26b002995bd | / H6 USB 事件式热插拔 / `UsbRadioController.kt:68`、`:104-107` / 已落地替代：`d4ebd5bf` → `device_manager.py:6… | d4ebd5bf 未改动 device_manager.py；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 113 | docs/reference-mechanisms.md | bc5ee50d550e | / H7 设备表 + 歧义抛错 / `satdump/.../dev.h:11-21`、`:30-36`；`dev.cpp:9-31`（>1 实例即 throw） / 已落地：`d4ebd5bf… | d4ebd5bf 未改动 satdump/.../dev.h；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 114 | docs/reference-mechanisms.md | 3cd3eb46cf54 | / D1 CCSDS 质量门 + BER/锁态 / `module_ccsds_conv_concat_decoder.cpp:105-118`、`:180-196`、`:202-213` / … | 7d04a3de 未改动 module_ccsds_conv_concat_decoder.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 115 | docs/reference-mechanisms.md | 19dba571e591 | / D5 全文件同步对齐 / `noaa-apt/src/decode.rs:106-118`（≥5 帧） / 已落地替代：`5d44109c` → `noaa_apt_lite.py:23-5… | 5d44109c 未改动 noaa_apt_lite.py；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 116 | docs/reference-mechanisms.md | 8a9ec3de4b39 | / D6 AGC→Costas→Gardner 闭环 / `meteor_demod/demod.c:8-12`、`:36-40` / 已落地替代：`560f6564` → `lrpt_pipe… | 560f6564 未改动 meteor_demod/demod.c；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 117 | docs/reference-mechanisms.md | 5a1407f69b12 | / D7 FEC API（RS(204,188)） / `onair/.../fec_proto.h:13-20` / 已落地：`fd7359e2` → `lrpt_fec.py:246-350… | fd7359e2 未改动 onair/.../fec_proto.h；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 118 | docs/reference-mechanisms.md | 9f13f8c5b664 | / D8 分级解码器 + 失败计数 / `rtl_433/include/r_device.h:73`、`:85-87`；`r_api.c:443-454`、`:867-874` / 已落地替代… | 390a0f39 未改动 rtl_433/include/r_device.h；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 119 | docs/reference-mechanisms.md | 6250abde3a55 | / T1 过境预测 / `gpredict/predict-tools.c:413-421`；`onair/.../passes.cpp:248-292`；`satdump/.../passes… | 69e2c1d1 未改动 gpredict/predict-tools.c；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 120 | docs/reference-mechanisms.md | aebbcd62effd | / T5 TLE 新鲜度/重试 / `onair/.../passes.cpp:296-306`（1 天/6 小时） / 已落地替代：`f7f1d278` → `tle_integrity.py… | f7f1d278 未改动 onair/.../passes.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 121 | docs/reference-mechanisms.md | af520ffdee49 | / T6 多普勒频移 / `gtk-sat-list.c:626-631`；`onair/.../passes.h:56-57` / 已落地：`357076eb` → `sat_tracker.… | 357076eb 未改动 gtk-sat-list.c；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 122 | docs/reference-mechanisms.md | bccf8ba5c058 | / T7 受晒/地影 / `sat-vis.c:82-105` / 已落地：`3fc46b98` → `sat_illumination.py:204-303`；`sat_passes.py:4… | 3fc46b98 未改动 sat-vis.c；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 123 | docs/reference-mechanisms.md | 0343628b8fde | / C1 坏配置恢复 / `sdrpp/core/src/config.cpp:34-43`（直接覆盖写默认） / 已落地改良：`a96c010f` → `settings.py:350-356… | a96c010f 未改动 sdrpp/core/src/config.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 124 | docs/reference-mechanisms.md | f6fb78056199 | / C2 原子写 / `config.cpp:47-52`（`ofstream` 直接覆盖） / 已落地改良：`357076eb` → `settings.py:454-462`（`.tmp` … | 357076eb 未改动 config.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 125 | docs/reference-mechanisms.md | b62b8c8341be | / C6 版本化迁移 / `gpredict/src/sat-cfg.c:302-330`（1.1→1.4 重写键） / 已落地改良：`605e89da` → `settings.py:250-… | 605e89da 未改动 gpredict/src/sat-cfg.c；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 126 | docs/reference-mechanisms.md | b1bfa421b026 | / X2 人工确认→一次性 token→审计 / 无参考实现 / 已落地自研：`440424b9` → `tx_guard.py:543-597`（`arm`）、`:599-620`（`revo… | 440424b9 未改动 tx_guard.py；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 127 | docs/reference-mechanisms.md | 620db22963e8 | / X3 能力声明 / `satdump/dev.h:30-36`（静态 `MODE_SINGLE_TX`） / 已落地改良：`1869648b` → `hal.py:139-141`（`sup… | 1869648b 未改动 satdump/dev.h；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 128 | docs/reference-mechanisms.md | b966baad12aa | / 卫星过境缓存与刷新 / gpredict 只实时时间；onair `passes.cpp:296-306` 到点抓取 / 已落地：`8d3ea22c` → `rf_sky_view.py:7… | 8d3ea22c 未改动 passes.cpp；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 130 | docs/sat-illumination.md | 29be1b8c9337 | - 既有 `sat_passes._is_sunlit_geometric()` 已改为委托本模块，避免第二份地影数学源。 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 131 | docs/settings-inventory.md | d97d002af22a | - `desktop/main_window.py` 仅做了**最小停靠接线**（已提交 `49eddcf`，9 insertions / | 有 hash 但缺 <path>:<line>，无法定位改动；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 132 | docs/settings-inventory.md | 37bf58072ff7 | / A1 / 过境最低仰角 / **已落地（读取入口）** / `pass_min_elevation_deg` / 10 / 0–45 / 度 / 设置面板写盘；消费方下次刷新调用函数读值 /… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 133 | docs/settings-inventory.md | 4a33fb9903de | / A2 / 过境搜索窗口 / **已落地（读取入口）** / `pass_window_hours` / 24 / 1–72 / 小时 / 同上 / `settings.effective_p… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 134 | docs/settings-inventory.md | 86b2d5e2e73e | / A5 / 静噪门限 / **已落地** / `squelch_default_db` / `int(DEFAULT_SQUELCH_DBFS)`=-50 / `int(SQUELCH_MIN… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 135 | docs/settings-inventory.md | 797637cf9833 | / A7 / 瀑布深度行数 / **已落地** / `waterfall_depth_rows` / 256 / 128–4096 / 行 / **即时**：`spectrum.set_wate… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 136 | docs/settings-inventory.md | 02c297ebf835 | / A8 / 大气消光系数 / **已落地** / `sky_extinction_k` / 0.2 / 0.0–0.5 / 星等 / **即时**：`sky_view.set_extincti… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 137 | docs/settings-inventory.md | f1dc27a6fe45 | / A9 / 显示低仰角过境 / **已落地（读取入口）** / `show_low_elevation_passes` / False / 布尔 / 同上 / `settings.effect… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 138 | docs/settings-inventory.md | c20bad856004 | / A10 / 默认采样率 / **已落地（统一 2.4 MS/s）** / 复用既有 `sample_rate_hz`，默认**派生自** `desktop/constants.py: DEF… | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 139 | docs/settings-inventory.md | 3c941c92647d | / A11 / 调谐步进 / **已落地** / `tuning_step_hz` / 10 000 / 10–1 000 000 / Hz（六档） / **即时**：`step_combo` … | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 140 | docs/settings-inventory.md | a0cd442edfb8 | / # / 项对（A ↔ B） / 约束 / 只改 A 不改 B 的后果 / 面板联动提示（已落地） / | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 141 | docs/settings-inventory.md | fb7a2a12d205 | （“启动时已修正配置：…”），不静默写日志。视觉冻结：只加文本，不碰布局/颜色/尺寸。 | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
| 142 | docs/star-catalog.md | d4fa8e2b56ba | 10. **打包**：已修。`pyproject.toml` 的 `package-data` 现含 `data/*.npz`； | 未写 commit hash，无法用 git 复核；本轮只锁增量，存量由该文档 owner 逐条补 hash 或删断言 |
