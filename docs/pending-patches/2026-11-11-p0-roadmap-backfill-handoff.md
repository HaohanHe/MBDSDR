# 交接：P0 13 条 roadmap 回填单元格（给 roadmap 合成线）

日期：2026-11-11
来源：`docs/research/2026-11-11-p0-static-inference-check-rest.md`（实测快照 `a2837671`；
首轮快照 `c899eb51`）。本文件只是建议文本，roadmap 由持有方写入，本线不抢文件。

回填要求：只改「当前真实行为（证据来源）」列，不重排表格。

| 条目 | 建议单元格 |
|---|---|
| P0-01 | 实测 2026-11-11（`a2837671`）：`tracking_policy_for` 返 `allow_tune=False`、仰角 45° 时 `_on_tune_sdr` 仍被调 1 次（145.8 MHz/FM）；`07167624` 只闸住 `_start_satellite_tracking`，逐拍 `_poll_satellite_track` 未闸。脚本 `scripts/repro/2026-11-11-p0-01-dead-tle-tracking-gate.py`（退出码 0；显式读 policy 对照 0 次） |
| P0-02 | 实测：3 份 catalog 6/6 一致、无同名 CATNR 冲突；downlink 表多 AO-73/FOX-1B、缺 METEOR M2/FY-3D → 交集 4 / 并集 8；TLE 缓存 3 种目录口径。`f94d6bf0`/`221fbce2` 只收敛了前三份。脚本 `…p0-02-satellite-list-single-source.py`（退出码 0） |
| P0-05 | 实测：`fft_size=4096`、`wf_colors='soft'`、`peak_rel_db=21.0` 重启后全还原，**已由 `685e8f50` 修复**；对照 `d77d9b78` 复现 1024/sdrpp/6.0（退出码 0） |
| P0-06 | 实测：仅含 theme 的导入不改任何哨兵（`absent=[…]` 合并语义），**已由 `6f8f5fcf` 修复**；对照 `a1506664` 复现 98M/10000/''/0.7（退出码 0） |
| P0-07 | 实测：`reset_all` 后运行态 5 键全保持、仅 `fft_size 4096→2048`，**已由 `59eb6754`+`905d5483` 修复**；对照 `d372f3e7` 复现整组重置（退出码 0） |
| P0-08 | 实测：A `completed`、B `failed` 且不写「录制完成」、C 仅 1 次警告，**已由 `bf06e9a4`+`f81f1301`+`c2ce967a`+`bc199e01` 修复**；对照 `82bb5421` 复现按钮不复位+假完成（退出码 0） |
| P0-09 | 实测：开段 145.5 MHz、调谐到 146.5 MHz 后 sidecar 仍 145.5 MHz 且 `center_freq_changes=None`，回放错位 1.0 MHz。脚本 `…p0-09-record-center-freq-sidecar.py`（退出码 0） |
| P0-12 | 实测：HEAD 调用 0 / 定义 0；`git log --all -S "_sky_to_screen_opt"` 无任何提交 → 从未入库。**记「无法判定」，不得记「已修」**。脚本 `…p0-12-sky-opt-undefined-call.py`（退出码 1 + 注入式对照 1/0） |
| P0-13 | 实测（`c899eb51`）：闸门已按 `legacy_gate/detail_open/default` 三档测量（`e0900f6e`），且滑杆已被 `ff48f7b9` 移出右栏 → 三档 0 px；对照 `0aaa6afe` 旧闸门无 `states`（退出码 0）；注入式（旧树+新尺 @DPR1.0）= 6/7/24。**注意**：`a2837671` 上闸门稳定闪退（`eventFilter:3788` `threading.get_ident` SystemError，`0xC0000005`），HEAD 复测受阻，另立条目跟踪 |
| P0-14 | 实测：splash 185 ms、窗口 10663 ms，**已由 `a52ce510` 修复**；对照 `1f72f0c3` 复现 splash 6890 ms / 窗口 14503 ms（退出码 0） |
| P0-15 | 实测：构造期 **0 次**、构造返回 `agent is None`，**已由 `1fdf1958` 修复（构造期）**；对照 `28a59c87` 复现构造期 1 次 6216 ms（退出码 0）。**残余**：首个事件循环拍装载 2183 ms（延后≠消失），建议另立条目，不要记成 P0-15 未修 |
| P0-16 | 实测：控件 1620，墙钟 498/810/954 ms、CPU 516/1047/1125 ms，应用级 `setStyleSheet` 0 次，**已由 `0ce78fb3` 修复**（改 QPalette）；对照 `20f64d35` 复现 5814–8027 ms、app 级每次 1 次（退出码 0）。首轮 4527 ms 是修复前快照值 |
| P0-17 | 实测：`_connect_backend` 2659 ms 期间心跳 32 次（空转 31 次），**已由 `bedf3c4d` 修复**；对照 `35e09799` 复现 2824 ms/心跳 1 次（退出码 0）。W26 已覆盖手动路径，无需重开 |

复跑命令与四行对照表见 `docs/research/2026-11-11-p0-static-inference-check-rest.md` §3、§7。
