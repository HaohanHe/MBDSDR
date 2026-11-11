# 交接：P0 13 条 roadmap 回填单元格（给 roadmap 合成线）

日期：2026-11-11（第四轮改口：触控已修、快照硬纪律、首拍真屏判据）
来源：`docs/research/2026-11-11-p0-static-inference-check-rest.md`（当前实测快照 `c3b48881`；
历史 `33b8de5e` / `a2837671` / `c899eb51`）。本文件只是建议文本，roadmap 由持有方写入，本线不抢文件。

回填要求：只改「当前真实行为（证据来源）」列，不重排表格；数字必须带 commit 短哈希，
并标注**尺子**（offscreen / 真屏指纹）。**现状只认当前 HEAD 重测值，旧快照读数标「历史」。**

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
| P0-15 | 实测（`c3b48881`，offscreen）：构造期 **0 次**、构造返回 `agent is None`（构造 1566–2452 ms），**已由 `1fdf1958` 修复（构造期）**；对照 `28a59c87` 复现构造期 1 次 6216 ms（退出码 0）。**首拍代价另立 P0-15b**，不要记成 P0-15 未修 |
| P0-16 | 实测（`c3b48881`，offscreen）：控件 1626，鼠标墙钟 **369–512 ms**、触控 **225–454 ms**，应用级 `setStyleSheet` **均为 0 次**、QSS 长度恒定（鼠标 20127 / 触控 20655），**已由 `0ce78fb3`（鼠标）+ `68962abc`（触控）修复**；对照 `20f64d35`（鼠标 5814–8027 ms、每切 1 次）与 `33b8de5e`（触控 2029–2849 ms、每切 1 次）退出码 0。首轮 4527 ms 是 `c899eb51` 修复前快照值；`33b8de5e` 的触控数是**历史**，不得当现状 |
| P0-17 | 实测（`a2837671`）：`_connect_backend` 2659 ms 期间心跳 32 次（空转 31 次），**已由 `bedf3c4d` 修复**；对照 **`35e09799d1b84eaf1df7df8c65876b7234c2df3b`**（= `bedf3c4d^`，W26 直接父提交）复现 2824 ms/心跳 1 次（退出码 0，脚本 `scripts/repro/2026-11-11-p0-17-connect-blocks-gui.py`）。W26 已覆盖手动路径，无需重开 |

新增三条（建议单列，不塞回原条目）：

| 新条目 | 建议写入 | 完成判据（反证） |
|---|---|---|
| P0-15b 首拍代价 | `c3b48881`：构造期 0 次，首个事件循环拍 **真屏指纹（W89 判据）1568–2418 ms**（冷态首跑 3293 ms；offscreen 同快照 2446–2847 ms），74–94% 是内核（`init_agent` 的导入：`sat_image_processing→scipy.ndimage` 约 0.95 s、`orbit_perturbation/orbit_dynamics→scipy.integrate` 约 0.63 s）；模拟「首次打开能力矩阵才装载」后首拍 **272–311 ms**、首次打开 770–1433 ms。脚本 `scripts/repro/2026-11-11-p0-15b-first-tick-cost.py --screen --show`（退出码 0）。**判据钉真屏，不许用 offscreen 数判达标** | 真屏指纹 + show 下首拍 ≤800 ms 且内核不在首拍执行；把装载改回首拍 → 必红 |
| P0-16b 触控切主题（**已修**） | 历史：`bb4f2de9` 把每切应用级重设 2→1 次（`a2837671` 3566–6848 ms → `33b8de5e` 2029–2849 ms，QSS 每次 +528 字符）。现状：**`68962abc` 1→0 次**——`_apply_theme` 只记 `theme._qss()`、`_apply_touch_stylesheet` 按模式标记未变即 `return`；`c3b48881` 实测 225–454 ms、CPU 234–281 ms、应用级 0 次、QSS 恒 20655 字节；反证改回读 `app.styleSheet()` → 21,183 → 31,743。守卫 `tests/test_theme_qss_growth.py`（快照上 2 passed） | 触控模式墙钟 < 1000 ms 且每切应用级 `setStyleSheet` 0 次；恢复重复追加 → 必红 |
| P0-13b 闸门闪退 | `33b8de5e`：`scripts/ui_narrow_breakdown.py --json` 3/3 `0xC0000005`，栈在 `main_window.py:3788 eventFilter` → `threading.get_ident()` SystemError；同一颗雷在窗口析构/解释器收尾也会触发。只登记，证据已转给查崩溃的线 | 闸门能跑完并输出三档读数；恢复线程判定 → 复现 |

复跑命令与四行对照表见 `docs/research/2026-11-11-p0-static-inference-check-rest.md` §3、§7。
