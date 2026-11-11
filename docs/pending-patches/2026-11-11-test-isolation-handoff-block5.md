# 交接单：测试隔离系统修复（给区块 5）——现成清单，不必重扫

> 来源：设备缺口修复线（`tests/test_panel_connect_placeholder.py` 补
> `isolated_profile` 时的扫描结果，提交 `81b1d561`）。
> 状态：**直接可用**。清单按「是否有 `isolated_profile` / 是否读真实
> `%APPDATA%` / 是否依赖真实 mtime」分类；本线只改了
> `test_panel_connect_placeholder.py` 一个文件，其余未动。

## 一、REALCFG：无 `isolated_profile`，会碰真实用户配置（25 个）

按文件名字典序：

1. `tests/test_bookmark_builtin_label.py`
2. `tests/test_close_recording_guard.py`
3. `tests/test_desktop_ui_responsiveness.py`
4. `tests/test_digital_decode_ui.py`
5. `tests/test_disconnect_state.py`
6. `tests/test_discoverability_help.py`
7. `tests/test_gimbal_follow_wiring.py`
8. `tests/test_gui_real_hardware.py`
9. `tests/test_interaction_wiring.py`
10. `tests/test_no_device_onboarding.py`
11. `tests/test_orphan_connection.py`
12. `tests/test_ptt_capability_rows.py`
13. `tests/test_record_replay_config.py`
14. `tests/test_shortcut_focus_priority.py`
15. `tests/test_shortcuts_override.py`
16. `tests/test_sky_action_visibility.py`
17. `tests/test_sky_capability_negotiation.py`
18. `tests/test_sky_track_wiring.py`
19. `tests/test_sky_tune_honesty.py`
20. `tests/test_three_column_layout.py`
21. `tests/test_touch_adaptation.py`
22. `tests/test_ui_hard_defects.py`
23. `tests/test_ui_interaction_guard.py`
24. `tests/test_ui_layout_actions.py`
25. `tests/test_user_layouts.py`

另：`tests/test_device_capability_probe.py` 实测同样会让会话级真实配置守卫
报红（本线验证时踩到过），它在上述扫描之后才被确认，建议一并纳入。

## 二、PATCHED：已有局部隔离/打补丁，但未整体 `isolated_profile`（3 个）

- `tests/test_main_window.py`
- `tests/test_no_sim_regression.py`
- `tests/test_ui_layout_locks.py`

## 三、依赖真实 mtime / 是守卫本体（3 个，改法不同）

- `tests/conftest.py` —— 会话级 `_real_config_guard_fixture` 本体；
- `tests/test_ui_settings_isolation_guard.py` —— 守卫自测，必须读真实路径；
- `tests/test_startup_log_policy.py` —— 读真实 `%APPDATA%` 的 mtime。

## 四、实测症状（供归因，不必复现）

- 症状：`desktop_settings.json` 大小与 sha256 都不变，仅 `mtime_ns` 变，
  会话结束时守卫仍报「测试会话改动了用户真实配置」。
- 归因：**并发 pytest 会话**（Qt 互斥锁等待 60–100 s 时必然出现）里有文件
  没隔离，写了一次真实配置；不是当前文件自己写的。
- 复现判据：安静环境独占复跑同一文件即绿（本线 `test_sample_rate_wording.py`
  98 s 拿锁后 4 passed；此前两文件合跑只因 mtime 变化报 1 error）。
- 修复方向：给上表 REALCFG 逐个加模块级 `@pytest.mark.usefixtures(
  "isolated_profile")`（`tests/conftest.py:474` 已提供），不要改守卫放宽比较。
