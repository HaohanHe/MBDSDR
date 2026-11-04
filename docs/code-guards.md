# 代码守卫：catch-all 静默吞噬（P13 补充）

- 结论：全仓 catch-all 处理器 **1883** 处，其中「无记录」静默吞噬 **1399** 处，白名单 **1** 处，**须修 1398 处**（`ERROR` 574 / `WARN` 824）。
- 扫描快照：2026-11-05，1066 个 Python 文件、377 个测试文件；行号随在途文件变化，以脚本实时输出为准。
- 事故首例：`desktop/rf_sky_view.py::RFSkyView._find_body_at` 的 `float(None)` 抛 `TypeError` 被 `try/except Exception: pass` 吞掉，深空天体既不可见也不可点，无日志无报错。该文件快照内有 6 处（2 ERROR + 4 WARN），修复归属桌面线。
- 本轮只报不修（除本人文件内的两处）；`docs/limitations-register.md` 未触碰，`desktop/main_window.py` 未触碰。

## 判据

扫描器 `scripts/lint_swallow_all.py` 用 AST（不是正则）识别 catch-all：

- catch-all = `except:` / `except Exception:` / `except BaseException:`，或元组里含 Exception/BaseException；
- 静默 = 处理器体内没有「记录原因」的动作（日志、print、warning/error 状态字段、错误容器 append），也没有 `raise`：
  - `EMPTY`：只有 `pass`、`...` 或注释（AST 里注释不可见，等同 Pass）→ **ERROR**
  - `SILENT_CONTROL`：只 `return None` / `return <常量>` / `continue` / `break` → **WARN**
  - `SILENT_FALLBACK`：有语句但无记录（塞默认值后继续）→ **WARN**
- 有记录（RECORDED）或重新抛出（RERAISE）→ 只计数，不入缺陷；
- 裸 `except:` 当前为 **0** 处。

每处白名单必须同时回答三问，缺一即不合格：

1. `reason`：为什么允许降级（必须是外部边界：第三方库/网络/硬件/平台）；
2. `user_visible`：降级后用户看到什么（不能是「什么都没有」）；
3. `test`：哪条测试覆盖这条降级路径（文件必须存在）。

## 白名单（1 处）

| 位置 | ① 为什么允许降级 | ② 用户看到什么 | ③ 覆盖测试 |
| --- | --- | --- | --- |
| `mbdsdr_ai/audio_out.py:37` | 外部边界：`sounddevice`/PortAudio 是可选依赖，导入失败（库缺失、平台无 PortAudio、后端初始化失败）必须整体降级，不能拖垮整个应用。 | `_SD_AVAILABLE=False`，`AudioPlayer.available=False`；`start()` 返回 False 并保持静音；`write()` 返回 0；UI 音频入口显示不可用，而不是崩溃或假装播放。 | `tests/test_swallow_guard.py::test_audio_player_degrades_without_sounddevice`（拦截 `import sounddevice`，断言 `available/start/write/stop` 的降级行为）。 |

其余 1398 处**没有一处能同时回答三问**：绝大多数连日志都没有，更谈不上覆盖降级路径的测试。因此不把「可选依赖 = 自动豁免」当作批量白名单——豁免必须逐条给出三问，否则就是静默丢功能。

## 分布

按目录：`mbdsdr_ai` 692 / `desktop` 574 / `tests` 62 / `experiments` 32 / `scripts` 26 / `tools` 8 / 根脚本 3 / `docs` 1。

按类别：`ERROR/EMPTY` 574、`WARN/SILENT_CONTROL` 523、`WARN/SILENT_FALLBACK` 301。

高风险前 10（完整 256 个文件见附录 A）：

| 文件 | 合计 | ERROR | WARN |
| --- | --- | --- | --- |
| `desktop/main_window.py` | 341 | 239 | 102 |
| `mbdsdr_ai/sdr_backend.py` | 112 | 50 | 62 |
| `desktop/rf_sky_view.py` | 71 | 21 | 50 |
| `mbdsdr_ai/agent.py` | 51 | 11 | 40 |
| `mbdsdr_ai/sdr_tools.py` | 49 | 17 | 32 |
| `desktop/receive_pipeline.py` | 21 | 18 | 3 |
| `desktop/dock_layout_manager.py` | 20 | 11 | 9 |
| `mbdsdr_ai/device_manager.py` | 16 | 8 | 8 |
| `mbdsdr_ai/core/controller.py` | 15 | 1 | 14 |
| `mbdsdr_ai/orbit_tools.py` | 13 | 0 | 13 |

典型危险形态（都与「用户看不见的功能丢失」同构）：

- 设备打开/调谐失败后 `pass`：状态栏仍显示「已连接」，实际无数据（`sdr_backend.py` 多处）。
- 面板/控件构建异常后 `pass`：界面缺块且无提示（`main_window.py::_build_central_widget`、`desktop/*_panel.py`）。
- 天体/星表查找失败后 `continue` / 置 `None`：图层绘制与点击命中整段失效（`rf_sky_view.py::_find_body_at`）。
- `float(None)`、`KeyError`、`AttributeError` 等**编程错误**被 catch-all 吞掉：本类一律算缺陷，不能以「兜底」名义保留。

## 反证（修前必红）

`tests/test_swallow_guard.py` 共 10 条：

- 扫描器行为 6 条：DSO 事故同构（`float(None)` 被 `except Exception: pass` → 命中 `ERROR/EMPTY`）、静默 return → WARN、窄异常不误报、有记录/重抛不误报、白名单三问缺项判不合格、三问齐全才豁免。
- 降级路径 1 条：`audio_out` 无 `sounddevice` 时整链降级（覆盖唯一白名单）。
- 仓库现状 3 条：白名单有效且未失效、扫描确定性、**`test_repo_has_no_unwhitelisted_silent_catch_all` 当前为红**——它打印前 15 个 `文件:行号` 并以「须修 1398 处」失败。这是本任务的修前必红证据，不是环境问题。

复跑：

```
python scripts/lint_swallow_all.py              # 汇总 + 须修清单（有缺陷退出 1）
python scripts/lint_swallow_all.py --list       # 打印每一处 catch-all
python scripts/lint_swallow_all.py --json out.json
python -m pytest tests/test_swallow_guard.py -q  # 9 passed / 1 failed（红为反证）
```

## 修复归属（只报不修）

- `desktop/` 574 处 → 桌面线（含 `main_window.py` 341、`rf_sky_view.py` 71；`main_window.py` 本轮不碰，仅报）。
- `mbdsdr_ai/` 692 处 → AI/后端各模块归属线（`sdr_backend.py` 112、`agent.py` 51、`sdr_tools.py` 49 优先）。
- `tests/` 62 处 → 测试线（多为 teardown 静默吞异常，至少补 `logging.debug`）。
- `experiments/` 32 / `tools/` 8 / `scripts/` 26 → 对应工具线；其中 `scripts/tool_param_fuzz.py` 与 `lint_swallow_all.py` 已由本线收窄异常类型（自身缺陷清零）。
- 修法（按序）：
  1. 能收窄异常类型的直接收窄（`except ValueError` 等），不吞编程错误；
  2. 确实要兜底的外部边界，写日志/状态字段 + 覆盖降级路径的测试，然后登记白名单三问；
  3. 不许改成 `contextlib.suppress(Exception)` 或等价写法绕过门禁。

## 附录 A：按文件分布（256 个文件）

| 文件 | 合计 | ERROR | WARN |
| --- | --- | --- | --- |
| `desktop/main_window.py` | 341 | 239 | 102 |
| `mbdsdr_ai/sdr_backend.py` | 112 | 50 | 62 |
| `desktop/rf_sky_view.py` | 71 | 21 | 50 |
| `mbdsdr_ai/agent.py` | 51 | 11 | 40 |
| `mbdsdr_ai/sdr_tools.py` | 49 | 17 | 32 |
| `desktop/receive_pipeline.py` | 21 | 18 | 3 |
| `desktop/dock_layout_manager.py` | 20 | 11 | 9 |
| `mbdsdr_ai/device_manager.py` | 16 | 8 | 8 |
| `mbdsdr_ai/core/controller.py` | 15 | 1 | 14 |
| `mbdsdr_ai/orbit_tools.py` | 13 | 0 | 13 |
| `mbdsdr_ai/serial_gnss.py` | 13 | 7 | 6 |
| `desktop/spectrum_widget.py` | 12 | 8 | 4 |
| `desktop/settings_panel.py` | 11 | 6 | 5 |
| `mbdsdr_ai/startup_sequence.py` | 11 | 4 | 7 |
| `mbdsdr_ai/decoders.py` | 10 | 0 | 10 |
| `mbdsdr_ai/solar_system.py` | 10 | 3 | 7 |
| `mbdsdr_ai/orbit_determination.py` | 9 | 3 | 6 |
| `mbdsdr_ai/subagents.py` | 9 | 2 | 7 |
| `experiments/satellite_rx/scripts/stack_up.py` | 8 | 4 | 4 |
| `mbdsdr_ai/acquisition.py` | 8 | 0 | 8 |
| `mbdsdr_ai/gnss_monitor.py` | 8 | 1 | 7 |
| `mbdsdr_ai/hal.py` | 8 | 2 | 6 |
| `mbdsdr_ai/sat_passes.py` | 8 | 0 | 8 |
| `desktop/ai_panel.py` | 7 | 6 | 1 |
| `mbdsdr_ai/code_editor.py` | 7 | 0 | 7 |
| `mbdsdr_ai/modulation.py` | 7 | 0 | 7 |
| `mbdsdr_ai/rtklib_adapter.py` | 7 | 5 | 2 |
| `desktop/ntrip_panel.py` | 6 | 3 | 3 |
| `desktop/touch_helpers.py` | 6 | 4 | 2 |
| `mbdsdr_ai/audio_out.py` | 6 | 2 | 4 |
| `mbdsdr_ai/context_manager.py` | 6 | 1 | 5 |
| `mbdsdr_ai/hal_enhanced.py` | 6 | 4 | 2 |
| `mbdsdr_ai/sat_tracker.py` | 6 | 0 | 6 |
| `mbdsdr_ai/sync_tools.py` | 6 | 0 | 6 |
| `mbdsdr_ai/tx_guard.py` | 6 | 1 | 5 |
| `scripts/ui_interaction_audit.py` | 6 | 3 | 3 |
| `tools/hw_selfcheck/selfcheck.py` | 6 | 1 | 5 |
| `desktop/adsb_map_panel.py` | 5 | 4 | 1 |
| `desktop/bookmark_panel.py` | 5 | 0 | 5 |
| `desktop/control_panel.py` | 5 | 2 | 3 |
| `desktop/device_panel.py` | 5 | 0 | 5 |
| `desktop/satellite_image_panel.py` | 5 | 1 | 4 |
| `desktop/scanner_panel.py` | 5 | 0 | 5 |
| `mbdsdr_ai/audio_device_manager.py` | 5 | 0 | 5 |
| `mbdsdr_ai/mcp_client.py` | 5 | 4 | 1 |
| `mbdsdr_ai/reconnection.py` | 5 | 3 | 2 |
| `mbdsdr_ai/spyserver_client.py` | 5 | 4 | 1 |
| `scripts/soak_receive_chain.py` | 5 | 1 | 4 |
| `tests/test_lrpt_meteor_real_golden.py` | 5 | 0 | 5 |
| `desktop/aprs_panel.py` | 4 | 0 | 4 |
| `desktop/gain_panel.py` | 4 | 2 | 2 |
| `desktop/main.py` | 4 | 2 | 2 |
| `desktop/status_panel.py` | 4 | 0 | 4 |
| `mbdsdr_ai/ccsds_tools.py` | 4 | 0 | 4 |
| `mbdsdr_ai/fengyun_sat.py` | 4 | 1 | 3 |
| `mbdsdr_ai/frame_layout_tool.py` | 4 | 0 | 4 |
| `mbdsdr_ai/gimbal.py` | 4 | 2 | 2 |
| `mbdsdr_ai/m17_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/op25_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/openapi_integration.py` | 4 | 0 | 4 |
| `mbdsdr_ai/openwebrx_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/orbit.py` | 4 | 0 | 4 |
| `mbdsdr_ai/pat_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/radio_control.py` | 4 | 3 | 1 |
| `mbdsdr_ai/rtltcp_client.py` | 4 | 3 | 1 |
| `mbdsdr_ai/sandbox.py` | 4 | 3 | 1 |
| `mbdsdr_ai/sat_consistency.py` | 4 | 0 | 4 |
| `mbdsdr_ai/sdrpp_decoders.py` | 4 | 0 | 4 |
| `mbdsdr_ai/sdrsharp_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/sdrtrunk_adapter.py` | 4 | 0 | 4 |
| `mbdsdr_ai/sstv_decoder.py` | 4 | 1 | 3 |
| `mbdsdr_ai/urh_adapter.py` | 4 | 0 | 4 |
| `tests/test_ui_hard_defects.py` | 4 | 4 | 0 |
| `desktop/adsb_panel.py` | 3 | 1 | 2 |
| `desktop/ai_steps.py` | 3 | 0 | 3 |
| `desktop/layout_store.py` | 3 | 0 | 3 |
| `desktop/mcp_worker.py` | 3 | 1 | 2 |
| `desktop/themes.py` | 3 | 1 | 2 |
| `desktop/vfo_panel.py` | 3 | 0 | 3 |
| `experiments/exp_ota_run.py` | 3 | 0 | 3 |
| `experiments/exp_sstv_identification.py` | 3 | 0 | 3 |
| `mbdsdr_ai/ardop_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/ax25.py` | 3 | 0 | 3 |
| `mbdsdr_ai/bch_codecs.py` | 3 | 0 | 3 |
| `mbdsdr_ai/blind_param_est.py` | 3 | 0 | 3 |
| `mbdsdr_ai/core/mcp_server.py` | 3 | 1 | 2 |
| `mbdsdr_ai/cubicsdr_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/gps_bit_sync.py` | 3 | 0 | 3 |
| `mbdsdr_ai/gps_nav.py` | 3 | 0 | 3 |
| `mbdsdr_ai/gqrx_receiver.py` | 3 | 0 | 3 |
| `mbdsdr_ai/hdsdr_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/inspectrum_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/js8call_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/lrpt_vcdu.py` | 3 | 0 | 3 |
| `mbdsdr_ai/model_manager.py` | 3 | 0 | 3 |
| `mbdsdr_ai/module_graph.py` | 3 | 2 | 1 |
| `mbdsdr_ai/new_spacetime.py` | 3 | 2 | 1 |
| `mbdsdr_ai/new_spacetime_timeline.py` | 3 | 0 | 3 |
| `mbdsdr_ai/protocol_stack.py` | 3 | 0 | 3 |
| `mbdsdr_ai/self_evolution.py` | 3 | 1 | 2 |
| `mbdsdr_ai/sigdigger_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_ai/skill_registry.py` | 3 | 1 | 2 |
| `mbdsdr_ai/skyengine/satellites.py` | 3 | 0 | 3 |
| `mbdsdr_ai/tc_uplink.py` | 3 | 0 | 3 |
| `mbdsdr_ai/tool_registry.py` | 3 | 0 | 3 |
| `mbdsdr_ai/windows_setup.py` | 3 | 3 | 0 |
| `mbdsdr_ai/xastir_adapter.py` | 3 | 0 | 3 |
| `mbdsdr_mcp_client.py` | 3 | 1 | 2 |
| `scripts/tool_availability_audit.py` | 3 | 1 | 2 |
| `tests/test_gui_real_hardware.py` | 3 | 2 | 1 |
| `tests/test_ui_layout_actions.py` | 3 | 3 | 0 |
| `tests/test_ui_layout_locks.py` | 3 | 3 | 0 |
| `desktop/anr_panel.py` | 2 | 0 | 2 |
| `desktop/modulation_panel.py` | 2 | 0 | 2 |
| `desktop/module_panel.py` | 2 | 0 | 2 |
| `desktop/settings.py` | 2 | 1 | 1 |
| `desktop/touch_manager.py` | 2 | 0 | 2 |
| `desktop/weather_panel.py` | 2 | 0 | 2 |
| `experiments/exp_llm_baseline.py` | 2 | 0 | 2 |
| `experiments/nanovna_rh770/scripts/vna_capture2.py` | 2 | 2 | 0 |
| `experiments/satellite_rx/scripts/mbdsdr_ctl.py` | 2 | 0 | 2 |
| `experiments/satellite_rx/scripts/pass_recorder.py` | 2 | 1 | 1 |
| `experiments/satellite_rx/scripts/passes_leo.py` | 2 | 2 | 0 |
| `experiments/satellite_rx/scripts/sat_radar.py` | 2 | 2 | 0 |
| `mbdsdr_ai/aprs_parser.py` | 2 | 1 | 1 |
| `mbdsdr_ai/astronomy.py` | 2 | 0 | 2 |
| `mbdsdr_ai/builtin_tle_meta.py` | 2 | 2 | 0 |
| `mbdsdr_ai/conversation.py` | 2 | 1 | 1 |
| `mbdsdr_ai/df_position.py` | 2 | 0 | 2 |
| `mbdsdr_ai/digital_modes.py` | 2 | 0 | 2 |
| `mbdsdr_ai/doppler_link.py` | 2 | 0 | 2 |
| `mbdsdr_ai/fhss_dehop.py` | 2 | 0 | 2 |
| `mbdsdr_ai/fm_scan.py` | 2 | 0 | 2 |
| `mbdsdr_ai/gnss_timestamps.py` | 2 | 0 | 2 |
| `mbdsdr_ai/gnuradio_blocks.py` | 2 | 0 | 2 |
| `mbdsdr_ai/gpredict_adapter.py` | 2 | 0 | 2 |
| `mbdsdr_ai/gps_ca.py` | 2 | 0 | 2 |
| `mbdsdr_ai/guardian.py` | 2 | 1 | 1 |
| `mbdsdr_ai/hackrf_params.py` | 2 | 1 | 1 |
| `mbdsdr_ai/interference_eval.py` | 2 | 0 | 2 |
| `mbdsdr_ai/interferometry.py` | 2 | 0 | 2 |
| `mbdsdr_ai/limesuite_params.py` | 2 | 2 | 0 |
| `mbdsdr_ai/llm_judge.py` | 2 | 0 | 2 |
| `mbdsdr_ai/mobile_server.py` | 2 | 0 | 2 |
| `mbdsdr_ai/multi_user.py` | 2 | 2 | 0 |
| `mbdsdr_ai/nanovna_dump.py` | 2 | 1 | 1 |
| `mbdsdr_ai/orbit_perturbation.py` | 2 | 0 | 2 |
| `mbdsdr_ai/osmosdr_source.py` | 2 | 0 | 2 |
| `mbdsdr_ai/playback.py` | 2 | 2 | 0 |
| `mbdsdr_ai/r2cloud_adapter.py` | 2 | 0 | 2 |
| `mbdsdr_ai/sat_links.py` | 2 | 0 | 2 |
| `mbdsdr_ai/sat_pipeline_runner.py` | 2 | 1 | 1 |
| `mbdsdr_ai/sat_profile.py` | 2 | 0 | 2 |
| `mbdsdr_ai/satellite/tracker.py` | 2 | 1 | 1 |
| `mbdsdr_ai/self_learning.py` | 2 | 2 | 0 |
| `mbdsdr_ai/tle_fetch.py` | 2 | 0 | 2 |
| `mbdsdr_ai/ui_actions.py` | 2 | 0 | 2 |
| `mbdsdr_ai/web/streamer.py` | 2 | 1 | 1 |
| `scripts/ai_tool_consistency.py` | 2 | 1 | 1 |
| `scripts/bench_dsp_throughput.py` | 2 | 2 | 0 |
| `scripts/decode_sensitivity.py` | 2 | 0 | 2 |
| `scripts/real_hw_acceptance.py` | 2 | 0 | 2 |
| `scripts/rtl_selfcheck.py` | 2 | 0 | 2 |
| `tests/test_interaction_wiring.py` | 2 | 2 | 0 |
| `tests/test_no_sim_regression.py` | 2 | 0 | 2 |
| `tests/test_sdr_backend_audio.py` | 2 | 1 | 1 |
| `tests/test_ui_interaction_guard.py` | 2 | 2 | 0 |
| `tests/test_ui_vfo_constellation.py` | 2 | 2 | 0 |
| `tests/tool_param_smoke.py` | 2 | 1 | 1 |
| `tests/tool_selftest.py` | 2 | 0 | 2 |
| `tools/onboarding/onboard.py` | 2 | 0 | 2 |
| `desktop/constants.py` | 1 | 0 | 1 |
| `desktop/mode_registry.py` | 1 | 0 | 1 |
| `desktop/tokens.py` | 1 | 0 | 1 |
| `docs/learn/phase14/pass_predict.py` | 1 | 0 | 1 |
| `experiments/exp_digital_modes.py` | 1 | 0 | 1 |
| `experiments/exp_fhss_detection.py` | 1 | 0 | 1 |
| `experiments/exp_weak_model_toolcall.py` | 1 | 0 | 1 |
| `experiments/nanovna_rh770/scripts/vna_capture3.py` | 1 | 1 | 0 |
| `experiments/satellite_rx/data/check_tles.py` | 1 | 0 | 1 |
| `experiments/satellite_rx/scripts/sat_catch.py` | 1 | 0 | 1 |
| `mbdsdr_ai/__init__.py` | 1 | 1 | 0 |
| `mbdsdr_ai/atmosphere.py` | 1 | 0 | 1 |
| `mbdsdr_ai/audio_resampler.py` | 1 | 0 | 1 |
| `mbdsdr_ai/background_decoder.py` | 1 | 0 | 1 |
| `mbdsdr_ai/bladerf_params.py` | 1 | 1 | 0 |
| `mbdsdr_ai/channelizer.py` | 1 | 0 | 1 |
| `mbdsdr_ai/cma_equalizer.py` | 1 | 0 | 1 |
| `mbdsdr_ai/df_amplitude.py` | 1 | 0 | 1 |
| `mbdsdr_ai/doppler_compensation.py` | 1 | 0 | 1 |
| `mbdsdr_ai/dsp.py` | 1 | 0 | 1 |
| `mbdsdr_ai/feature_manager.py` | 1 | 0 | 1 |
| `mbdsdr_ai/fec.py` | 1 | 0 | 1 |
| `mbdsdr_ai/gk2a_lrit.py` | 1 | 0 | 1 |
| `mbdsdr_ai/golden_regression.py` | 1 | 0 | 1 |
| `mbdsdr_ai/hooks.py` | 1 | 1 | 0 |
| `mbdsdr_ai/kismet_adapter.py` | 1 | 0 | 1 |
| `mbdsdr_ai/lrpt_image.py` | 1 | 0 | 1 |
| `mbdsdr_ai/lrpt_pipeline.py` | 1 | 0 | 1 |
| `mbdsdr_ai/mimo_v2.py` | 1 | 0 | 1 |
| `mbdsdr_ai/orbit_dynamics.py` | 1 | 0 | 1 |
| `mbdsdr_ai/plugin_system.py` | 1 | 1 | 0 |
| `mbdsdr_ai/position_service.py` | 1 | 1 | 0 |
| `mbdsdr_ai/remote_control.py` | 1 | 0 | 1 |
| `mbdsdr_ai/rtcm3_decoder.py` | 1 | 0 | 1 |
| `mbdsdr_ai/rtk_solver.py` | 1 | 0 | 1 |
| `mbdsdr_ai/sat_image_processing.py` | 1 | 0 | 1 |
| `mbdsdr_ai/satnogs_adapter.py` | 1 | 0 | 1 |
| `mbdsdr_ai/scheduler.py` | 1 | 0 | 1 |
| `mbdsdr_ai/sdrangel_plugins.py` | 1 | 0 | 1 |
| `mbdsdr_ai/sky_actions.py` | 1 | 0 | 1 |
| `mbdsdr_ai/spacetime_predict.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/exp_phase58_multisat.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/test_mobile_server.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/test_optional_deps.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/test_phase58_multisat.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tests/test_sdr_rtltcp_registration.py` | 1 | 0 | 1 |
| `mbdsdr_ai/time_sync.py` | 1 | 0 | 1 |
| `mbdsdr_ai/tool_budget.py` | 1 | 0 | 1 |
| `mbdsdr_ai/verify_chain.py` | 1 | 0 | 1 |
| `mbdsdr_ai/vna_pipeline.py` | 1 | 0 | 1 |
| `mbdsdr_ai/web/server.py` | 1 | 1 | 0 |
| `mbdsdr_ai/workflow_engine.py` | 1 | 0 | 1 |
| `mbdsdr_ai/workflow_recorder.py` | 1 | 1 | 0 |
| `scripts/access_layer_fault_injection.py` | 1 | 0 | 1 |
| `scripts/serial_gnss_probe.py` | 1 | 1 | 0 |
| `tests/conftest.py` | 1 | 0 | 1 |
| `tests/test_adsb_dump1090_golden.py` | 1 | 0 | 1 |
| `tests/test_agc_speed.py` | 1 | 1 | 0 |
| `tests/test_app_mode.py` | 1 | 1 | 0 |
| `tests/test_bookmark_tags.py` | 1 | 1 | 0 |
| `tests/test_clean_exc.py` | 1 | 0 | 1 |
| `tests/test_cli.py` | 1 | 1 | 0 |
| `tests/test_desktop_ui_responsiveness.py` | 1 | 1 | 0 |
| `tests/test_device_switch_flow.py` | 1 | 1 | 0 |
| `tests/test_ft8_wsjtx_golden.py` | 1 | 0 | 1 |
| `tests/test_full_integration.py` | 1 | 0 | 1 |
| `tests/test_gimbal_follow_wiring.py` | 1 | 1 | 0 |
| `tests/test_gk2a_jp2.py` | 1 | 0 | 1 |
| `tests/test_headless_smoke.py` | 1 | 1 | 0 |
| `tests/test_identity_redline.py` | 1 | 0 | 1 |
| `tests/test_main_window.py` | 1 | 1 | 0 |
| `tests/test_mcp_server.py` | 1 | 1 | 0 |
| `tests/test_mode_switch_preset.py` | 1 | 1 | 0 |
| `tests/test_orphan_connection.py` | 1 | 1 | 0 |
| `tests/test_record_replay_config.py` | 1 | 1 | 0 |
| `tests/test_satellite_tracker.py` | 1 | 0 | 1 |
| `tests/test_serial_gnss.py` | 1 | 0 | 1 |
| `tests/test_sky_capability_negotiation.py` | 1 | 1 | 0 |
| `tests/test_sky_daylight_stars.py` | 1 | 0 | 1 |
| `tests/test_sky_track_wiring.py` | 1 | 1 | 0 |
| `tests/test_three_column_layout.py` | 1 | 1 | 0 |
| `tests/test_touch_adaptation.py` | 1 | 1 | 0 |
| `tests/test_ui_flow_guards.py` | 1 | 1 | 0 |
| `tests/test_user_layouts.py` | 1 | 1 | 0 |
| `tests/tool_health_check.py` | 1 | 0 | 1 |
