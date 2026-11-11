# 交接：P0 13 条 roadmap 回填单元格（给 roadmap 合成线）

日期：2026-11-11（第三轮改口：快照硬纪律、首拍另立、触控单列）
来源：`docs/research/2026-11-11-p0-static-inference-check-rest.md`（实测快照 `33b8de5e`；
前两轮 `a2837671` / `c899eb51`）。本文件只是建议文本，roadmap 由持有方写入，本线不抢文件。

回填要求：只改「当前真实行为（证据来源）」列，不重排表格；数字必须带 commit 短哈希。

| 条目 | 建议单元格 |
|---|---|
| P0-01 | 实测 2026-11-11（`33b8de5e`）：`tracking_policy_for` 返 `allow_tune=False`、仰角 45° 时 `_on_tune_sdr` 仍被调 1 次（145.8 MHz/FM）；`07167624` 只闸住 `_start_satellite_tracking`，逐拍 `_poll_satellite_track` 未闸。脚本 `scripts/repro/2026-11-11-p0-01-dead-tle-tracking-gate.py`（退出码 0；显式读 policy 对照 0 次） |
| P0-02 | 实测（`33b8de5e`，退出码 0）：3 份 catalog 6/6 一致、无同名 CATNR 冲突；downlink 表多 AO-73/FOX-1B、缺 METEOR M2/FY-3D → 交集 4 / 并集 8；TLE 缓存目录 3 种。`f94d6bf0`/`221fbce2` 只收敛了前三份 |
| P0-05 | 实测：`fft_size=4096`、`wf_colors='soft'`、`peak_rel_db=21.0` 重启后全还原，**已由 `685e8f50` 修复**；对照 `d77d9b78` 复现 1024/sdrpp/6.0（退出码 0） |
| P0-06 | 实测：仅含 theme 的导入不改任何哨兵（`absent=[…]` 合并语义），**已由 `6f8f5fcf` 修复**；对照 `a1506664` 复现 98M/10000/''/0.7（退出码 0） |
| P0-07 | 实测：`reset_all` 后运行态 5 键全保持、仅 `fft_size 4096→2048`，**已由 `59eb6754`+`905d5483` 修复**；对照 `d372f3e7` 复现整组重置（退出码 0） |
| P0-08 | 实测：A `completed`、B `failed` 且不写「录制完成」、C 仅 1 次警告，**已由 `bf06e9a4`+`f81f1301`+`c2ce967a`+`bc199e01` 修复**；对照 `82bb5421` 复现按钮不复位+假完成（退出码 0） |
| P0-09 | 实测（`33b8de5e`，退出码 0）：开段 145.5 MHz、调谐到 146.5 MHz 后 sidecar 仍 145.5 MHz 且 `center_freq_changes=None`，回放错位 1.0 MHz |
| P0-12 | 实测：HEAD 调用 0 / 定义 0；`git log --all -S "_sky_to_screen_opt"` 无任何提交 → 从未入库。**记「无法判定」，不得记「已修」**。脚本 `…p0-12-sky-opt-undefined-call.py`（退出码 1 + 注入式对照 1/0） |
| P0-13 | 实测（`c899eb51`）：闸门已按 `legacy_gate/detail_open/default` 三档测量（`e0900f6e`），且滑杆已被 `ff48f7b9` 移出右栏 → 三档 0 px；对照 `0aaa6afe` 旧闸门无 `states`（退出码 0）；注入式（旧树+新尺 @DPR1.0）= 6/7/24。**注意**：`33b8de5e` 上闸门仍稳定闪退（`eventFilter:3788` `threading.get_ident` SystemError，`0xC0000005`），复测受阻，另立 P0-13b 跟踪 |
| P0-14 | 实测：splash 185 ms、窗口 10663 ms，**已由 `a52ce510` 修复**；对照 `1f72f0c3` 复现 splash 6890 ms / 窗口 14503 ms（退出码 0） |
| P0-15 | 实测（`33b8de5e`）：构造期 **0 次**、构造返回 `agent is None`（构造 1147–2500 ms），**已由 `1fdf1958` 修复（构造期）**；对照 `28a59c87` 复现构造期 1 次 6216 ms（退出码 0）。**首拍 796–2329 ms 另立 P0-15b**，不要记成 P0-15 未修 |
| P0-16 | 实测（`33b8de5e` 鼠标）：控件 1626，墙钟 207–401 ms、CPU 344–500 ms，应用级 `setStyleSheet` 0 次，**已由 `0ce78fb3` 修复**；对照 `20f64d35` 复现 5814–8027 ms、app 级每次 1 次（退出码 0）。首轮 4527 ms 是 `c899eb51` 修复前快照值。**触控模式另立 P0-16b**：2029–2849 ms、每切 1 次应用级重设、QSS 每次 +528 字符（`bb4f2de9` 只把 2 次降到 1 次，未消除） |
| P0-17 | 实测（`a2837671`）：`_connect_backend` 2659 ms 期间心跳 32 次（空转 31 次），**已由 `bedf3c4d` 修复**；对照 `35e09799` 复现 2824 ms/心跳 1 次（退出码 0）。W26 已覆盖手动路径，无需重开 |

新增三条（建议单列，不塞回原条目）：

| 新条目 | 建议写入 | 完成判据（反证） |
|---|---|---|
| P0-15b 首拍代价 | `33b8de5e`：构造期 0 次，但首个事件循环拍 796–2329 ms，其中内核 1.2–1.6 s（60% 是 `init_agent` 触发的导入：`sat_image_processing→scipy.ndimage` 约 0.95 s、`orbit_perturbation/orbit_dynamics→scipy.integrate` 约 0.63 s）；模拟「首次打开能力矩阵才装载」后首拍 85–126 ms、首次打开 1299 ms。脚本 `scripts/repro/2026-11-11-p0-15b-first-tick-cost.py`（退出码 0） | 首拍 < 1000 ms 且内核不在首拍执行；把装载改回首拍 → 必红 |
| P0-16b 触控切主题 | `33b8de5e`（`--touch`）：2029/2326/2849 ms、CPU 2094/2401/2641 ms、每切 1 次应用级 `setStyleSheet`（`a2837671` 对照：每切 2 次、4377–6848 ms）；根因 `_apply_theme` 把 `_theme_qss` 记成「基座+触控后缀」（`main_window.py:5304`），`_apply_touch_stylesheet` 又追加一次（`:1271-1278`），QSS 每次 +528 字符。脚本 `…p0-16-theme-switch-cost.py --touch`（退出码 0） | 触控模式墙钟 < 1000 ms 且每切应用级 `setStyleSheet` 0 次；恢复重复追加 → 必红 |
| P0-13b 闸门闪退 | `33b8de5e`：`scripts/ui_narrow_breakdown.py --json` 3/3 `0xC0000005`，栈在 `main_window.py:3788 eventFilter` → `threading.get_ident()` SystemError；同一颗雷在窗口析构/解释器收尾也会触发。只登记，证据已转给查崩溃的线 | 闸门能跑完并输出三档读数；恢复线程判定 → 复现 |

复跑命令与四行对照表见 `docs/research/2026-11-11-p0-static-inference-check-rest.md` §3、§7。
