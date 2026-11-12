# 工程治理合规报告（八维）

- 生成时间：2026-11-13
- 违规：272（应修 119，复核 110，豁免 43）
- 反证自检：全部通过

## 八维总览

| 维度 | 标题 | 违规 | 应修 | 复核 | 豁免 |
|---|---|---:|---:|---:|---:|
| D1 | 分层与依赖方向 | 5 | 4 | 1 | 0 |
| D2 | 真值源清单 | 69 | 57 | 12 | 43 |
| D3 | 接口契约与命名 | 33 | 0 | 33 | 0 |
| D4 | 门禁与验收 | 42 | 30 | 12 | 0 |
| D5 | 流程与协作 | 40 | 10 | 30 | 0 |
| D6 | 目录与文档结构 | 39 | 18 | 21 | 0 |
| D7 | 任务分派与优先级 | 0 | 0 | 0 | 0 |
| D8 | 豁免与债务登记 | 1 | 0 | 1 | 0 |

## 反证自检

| 规则 | 反证 | 结果 | 正例 | 负例 | 来源 |
|---|---|---|---:|---:|---|
| A01 | 读数 f-string 就地拼装 | PASS | 1 | 0 | desktop/main_window.py 状态条 / desktop/control_panel.py 的 MHz f-string |
| A02 | 频率被 3 位小数 MHz 量化 | PASS | 1 | 0 | desktop/main_window.py 状态条 `.3f} MHz`；readouts.py 文档记录的 2400 Hz→2 kHz |
| A03 | 持久化读取静默回空 | PASS | 1 | 0 | mbdsdr_ai/conversation.py 读会话文件 except Exception: pass |
| A04 | 读损坏后仍整文件覆写 | PASS | 1 | 0 | mbdsdr_ai/conversation.py 读失败回默认后继续 json.dump |
| A05 | 物理上不可能通过的断言 | PASS | 1 | 0 | tests/test_network_failure_modes.py:407 慢滴握手（消息自述需要 307s） |
| A06 | 非 strict xfail 的空反证 | PASS | 1 | 0 | docs/code-guards.md 要求删除被测逻辑后必须变红 |
| A07 | 约束只放在一个入口 | PASS | 1 | 0 | desktop/device_panel.py 的采样率范围校验 vs 其他 set_sample_rate 路径 |
| A08 | 守卫条件恒假 | PASS | 1 | 0 | mbdsdr_ai/decoders.py:435 `if False:` |
| A09 | 依赖缺失被登记成环境 skip | PASS | 1 | 0 | tests/test_web_bridge_mcp.py 6 处「MCPToolSet 在此环境不可构造」 |
| A12 | 同名不同实现 | PASS | 1 | 0 | desktop/main_window.py 的 _fmt_dbfs 与 desktop/readouts.py 的 fmt_dbfs |
| A13 | 用 owner 名字判同一 | PASS | 1 | 0 | scripts/file_claim.py:540 `if holder != owner:`（release 只比名字） |
| D1 | 纯 Python 层依赖 Qt | PASS | 1 | 0 | mbdsdr_ai 不得 import PySide6（分层契约） |
| D2 | 同一读数多文件产生 | PASS | 1 | 0 | desktop 多处各自拼 Hz 读数，未登记真值源 |
| D3 | 同一概念两个名字 | PASS | 1 | 0 | 仓内 bw / bandwidth 两套命名并存 |
| D4 | 门禁脚本没有任何入口 | PASS | 1 | 0 | scripts/check_*.py 既未接入钩子也未在文档登记 |
| D5 | 已知红缺转绿条件 | PASS | 1 | 0 | docs/known-red-register.md 要求每条红必须带转绿条件 |
| D6 | 文档引用不存在的路径 | PASS | 1 | 0 | 本轮文档与代码不一致（例如「不改颜色」但代码仍有硬编码颜色） |
| A10 | 文档引用不存在的提交 | PASS | 1 | 0 | docs/推进报告-本轮排错.md:141 引用不存在的 `5d09bb2` |
| A11 | 绿色声明绑定到不存在的树 | PASS | 1 | 0 | docs/learn/phase40/ci-notes.md 的绿色记录距 HEAD 752 个提交 |
| D8 | 豁免缺到期/批准人 | PASS | 1 | 0 | docs/axioms.md 豁免三要素 |
| D7 | 损失优先于整洁 | PASS | 1 | 0 | docs/axioms.md 派活规则 |

## D1 分层与依赖方向（违规 5，豁免 0）

> 规范：哪一层可以 import 哪一层；纯 Python 层不得依赖 Qt。

- 事实：模块 1458 个；包间依赖边 8620 条
- 事实：依赖 mbdsdr_ai -> mbdsdr_ai：4037 条
- 事实：依赖 tests -> mbdsdr_ai：3308 条
- 事实：依赖 desktop -> mbdsdr_ai：511 条
- 事实：依赖 tests -> desktop：294 条
- 事实：依赖 scripts -> mbdsdr_ai：269 条
- 事实：依赖 tools -> mbdsdr_ai：65 条
- 事实：依赖 desktop -> desktop：53 条
- 事实：依赖 tools -> tools：45 条
- 事实：依赖 scripts -> tools：11 条
- 事实：依赖 scripts -> desktop：11 条
- 事实：依赖 mbdsdr_ai -> desktop：8 条
- 事实：依赖 mbdsdr_ai -> tools：6 条
- 事实：模块级强连通分量：0 个

- **[应修]** `D1-reverse` `mbdsdr_ai/:0` — 8 处 import
  - 为什么：依赖方向反了：纯 Python 后端依赖了 Qt 桌面层，后端无法脱离桌面单独测试/复用。
  - 怎么修：把被依赖的部分下沉到 mbdsdr_ai（或独立纯模块），desktop 只允许单向依赖 mbdsdr_ai。
- **[复核]** `D1-test-reverse` `tests/:0` — 294 处 import
  - 为什么：tests 反向依赖 desktop，测试/工具会被 UI 生命周期绑住。
  - 怎么修：只依赖 mbdsdr_ai 的纯接口；确需 UI 的用例放在 tests/ui 并显式标注。
- **[已消解]** `D1-qt-in-core` `mbdsdr_ai/desktop_sdr_session.py:38` — import PySide6.QtCore（该文件已整体迁至 `desktop/ai_sdr_session.py`，不再属 mbdsdr_ai 层）
  - 为什么：纯 Python 层（mbdsdr_ai）直接 import 了 Qt；这层一旦被 CLI/MCP 复用就会拖入 GUI 依赖。
  - 怎么修：把 Qt 相关实现移到 desktop/，mbdsdr_ai 只保留可注入的接口。
- **[应修]** `D1-qt-in-core` `mbdsdr_ai/device_watchdog.py:25` — import PySide6.QtCore
  - 为什么：纯 Python 层（mbdsdr_ai）直接 import 了 Qt；这层一旦被 CLI/MCP 复用就会拖入 GUI 依赖。
  - 怎么修：把 Qt 相关实现移到 desktop/，mbdsdr_ai 只保留可注入的接口。
- **[应修]** `D1-qt-in-core` `mbdsdr_ai/optional_deps.py:40` — import PySide6
  - 为什么：纯 Python 层（mbdsdr_ai）直接 import 了 Qt；这层一旦被 CLI/MCP 复用就会拖入 GUI 依赖。
  - 怎么修：把 Qt 相关实现移到 desktop/，mbdsdr_ai 只保留可注入的接口。

## D2 真值源清单（违规 69，豁免 43）

> 规范：每个事实只能有一个产生点；同一事实两份实现即为违规。

- 事实：读数单位 Hz：42 个文件各自拼装
- 事实：读数单位 dB：36 个文件各自拼装
- 事实：读数单位 MHz：30 个文件各自拼装
- 事实：读数单位 kHz：18 个文件各自拼装
- 事实：读数单位 GHz：2 个文件各自拼装
- 事实：读数单位 dBFS：2 个文件各自拼装
- 事实：读数单位 dBm：1 个文件各自拼装
- 事实：同名常量存在多值：86 组

- **[应修]** `A01` `desktop/device_panel.py:761` — self.status_label.setText(f"采样率范围未知，无法校验；将以 {sr:.0f} Hz 下发")
  - 为什么：用户可见读数在本文件内用 f-string 就地拼装，绕过了 desktop/readouts.py；同一个值换个面板就会拼出另一种字符串。
  - 怎么修：改为调用 desktop/readouts.py 里对应的唯一产生函数；新口径只在该文件新增。
- **[应修]** `A01` `desktop/modulation_panel.py:247` — self.bw_label.setText(f"{bw/1e3:.1f} kHz" if bw > 0 else "--")
  - 为什么：用户可见读数在本文件内用 f-string 就地拼装，绕过了 desktop/readouts.py；同一个值换个面板就会拼出另一种字符串。
  - 怎么修：改为调用 desktop/readouts.py 里对应的唯一产生函数；新口径只在该文件新增。
- **[应修]** `A01` `desktop/new_spacetime_panel.py:597` — self.bw_label.setText(f"{result.bandwidth_hz / 1e3:.1f} kHz")
  - 为什么：用户可见读数在本文件内用 f-string 就地拼装，绕过了 desktop/readouts.py；同一个值换个面板就会拼出另一种字符串。
  - 怎么修：改为调用 desktop/readouts.py 里对应的唯一产生函数；新口径只在该文件新增。
- **[应修]** `A01` `desktop/scanner_panel.py:354` — self.status_label.setText(f"扫频中… {center_hz / 1e6:.3f} MHz")
  - 为什么：用户可见读数在本文件内用 f-string 就地拼装，绕过了 desktop/readouts.py；同一个值换个面板就会拼出另一种字符串。
  - 怎么修：改为调用 desktop/readouts.py 里对应的唯一产生函数；新口径只在该文件新增。
- **[应修]** `A01` `desktop/spectrum_widget.py:1069` — label = f"VFO {letter}  {center_hz/1e6:.3f} MHz"
  - 为什么：用户可见读数在本文件内用 f-string 就地拼装，绕过了 desktop/readouts.py；同一个值换个面板就会拼出另一种字符串。
  - 怎么修：改为调用 desktop/readouts.py 里对应的唯一产生函数；新口径只在该文件新增。
- **[应修]** `A01` `desktop/weather_panel.py:924` — line = (f"{self.freq_edit.text()} MHz | "
  - 为什么：用户可见读数在本文件内用 f-string 就地拼装，绕过了 desktop/readouts.py；同一个值换个面板就会拼出另一种字符串。
  - 怎么修：改为调用 desktop/readouts.py 里对应的唯一产生函数；新口径只在该文件新增。
- **[应修]** `A02` `desktop/adsb_map_panel.py:237` — f"当前 {frame.center_hz / 1e6:.3f} MHz / "
  - 为什么：频率读数保留 3 位小数（MHz）会把 1 Hz 调谐分辨率量化成 1000 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/bookmark_panel.py:342` — f"{best.name} @ {best.frequency_hz/1e6:.4f} MHz\n"
  - 为什么：频率读数保留 4 位小数（MHz）会把 1 Hz 调谐分辨率量化成 100 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/bookmark_panel.py:303` — f"删除 {sel['name']} @ {sel['frequency_hz']/1e6:.4f} MHz？",
  - 为什么：频率读数保留 4 位小数（MHz）会把 1 Hz 调谐分辨率量化成 100 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/control_panel.py:1249` — default_name = f"{self._freq_hz / 1e6:.3f}MHz {self._current_mode}"
  - 为什么：频率读数保留 3 位小数（MHz）会把 1 Hz 调谐分辨率量化成 1000 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/control_panel.py:1285` — f"删除书签「{name}」（{hz / 1e6:.3f} MHz）？此操作不能撤销。",
  - 为什么：频率读数保留 3 位小数（MHz）会把 1 Hz 调谐分辨率量化成 1000 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/control_panel.py:1207` — full = f"{shown}{tag} ({hz / 1e6:.3f}MHz {mode})"
  - 为什么：频率读数保留 3 位小数（MHz）会把 1 Hz 调谐分辨率量化成 1000 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/scanner_panel.py:354` — self.status_label.setText(f"扫频中… {center_hz / 1e6:.3f} MHz")
  - 为什么：频率读数保留 3 位小数（MHz）会把 1 Hz 调谐分辨率量化成 1000 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/scanner_panel.py:365` — text = (f"{icon} {seg['start_freq']/1e6:.3f}–"
  - 为什么：频率读数保留 3 位小数（MHz）会把 1 Hz 调谐分辨率量化成 1000 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/scanner_panel.py:365` — text = (f"{icon} {seg['start_freq']/1e6:.3f}–"
  - 为什么：频率读数保留 3 位小数（MHz）会把 1 Hz 调谐分辨率量化成 1000 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/spectrum_widget.py:2240` — return (f"{verb} {freq_hz / 1e6:.4f} MHz"
  - 为什么：频率读数保留 4 位小数（MHz）会把 1 Hz 调谐分辨率量化成 100 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/spectrum_widget.py:1069` — label = f"VFO {letter}  {center_hz/1e6:.3f} MHz"
  - 为什么：频率读数保留 3 位小数（MHz）会把 1 Hz 调谐分辨率量化成 1000 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/spectrum_widget.py:2254` — first = (f"{freq_hz / 1e6:.4f} MHz   "
  - 为什么：频率读数保留 4 位小数（MHz）会把 1 Hz 调谐分辨率量化成 100 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/spectrum_widget.py:2188` — return [(freq_hz, db, snr, f"{freq_hz/1e6:.3f} MHz")
  - 为什么：频率读数保留 3 位小数（MHz）会把 1 Hz 调谐分辨率量化成 1000 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/spectrum_widget.py:2299` — f"{freq_hz/1e6:.3f}MHz {db_txt}")
  - 为什么：频率读数保留 3 位小数（MHz）会把 1 Hz 调谐分辨率量化成 1000 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/spectrum_widget.py:3709` — f"{float(b['start_hz']) / 1e6:.4f}–"
  - 为什么：频率读数保留 4 位小数（MHz）会把 1 Hz 调谐分辨率量化成 100 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A02` `desktop/spectrum_widget.py:3709` — f"{float(b['start_hz']) / 1e6:.4f}–"
  - 为什么：频率读数保留 4 位小数（MHz）会把 1 Hz 调谐分辨率量化成 100 Hz，读者看到的不是实际调谐值。
  - 怎么修：频率用 6 位小数（readouts.format_frequency_hz）；需要短标签时另立明确口径。
- **[应修]** `A03` `desktop/adsb_panel.py:103` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `desktop/ai_panel.py:316` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `desktop/ai_panel.py:1320` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `desktop/ai_steps.py:149` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `desktop/ai_steps.py:136` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `desktop/main_window.py:8327` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `desktop/main_window.py:14454` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `desktop/main_window.py:12760` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `desktop/satellite_image_panel.py:264` — except Exception:  # noqa: BLE001（meta 损坏/缺字段 → 诚实降级）
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/agent.py:4491` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/agent.py:4505` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/amr.py:455` — except Exception:  # noqa: BLE001 - 缓存坏了就重算，不能因此崩
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/conversation.py:120` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/conversation.py:77` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/fengyun_sat.py:936` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/gk2a_lrit.py:907` — except Exception:  # noqa: BLE001 —— 任何解码失败都走回退路径
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/mobile_server.py:52` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/orbit_determination.py:1128` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/orbit_determination.py:1049` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/sandbox.py:224` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/satdump_integration.py:258` — except Exception:  # noqa: BLE001 - 可选增强，坏文件一律忽略
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/sdr_backend.py:3594` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/self_learning.py:399` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/self_learning.py:371` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/session_log.py:109` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A03` `mbdsdr_ai/workflow_recorder.py:458` — except Exception:
  - 为什么：持久化读取失败被宽泛 except 静默吞掉（不记录、不提示、不区分失败原因），调用方拿到的「空」与「真的空」无法区分。
  - 怎么修：记录 reason（含异常类型与路径）并把降级状态暴露给调用方；只有「文件不存在」可以按首次使用处理。
- **[应修]** `A04` `mbdsdr_ai/conversation.py:77` — except Exception:
  - 为什么：读损坏文件时静默回默认值，但同一模块第 92 行会用 json.dump 整文件覆写；用户下一次操作就会永久丢掉原内容，且没有备份。
  - 怎么修：读失败时先备份为 *.corrupt-<时间>、记录原因，并拒绝用默认值覆写原件。
- **[应修]** `A04` `mbdsdr_ai/conversation.py:120` — except Exception:
  - 为什么：读损坏文件时静默回默认值，但同一模块第 92 行会用 json.dump 整文件覆写；用户下一次操作就会永久丢掉原内容，且没有备份。
  - 怎么修：读失败时先备份为 *.corrupt-<时间>、记录原因，并拒绝用默认值覆写原件。
- **[应修]** `A04` `mbdsdr_ai/self_learning.py:371` — except Exception:
  - 为什么：读损坏文件时静默回默认值，但同一模块第 334 行会用 json.dump 整文件覆写；用户下一次操作就会永久丢掉原内容，且没有备份。
  - 怎么修：读失败时先备份为 *.corrupt-<时间>、记录原因，并拒绝用默认值覆写原件。
- **[应修]** `A04` `mbdsdr_ai/self_learning.py:399` — except Exception:
  - 为什么：读损坏文件时静默回默认值，但同一模块第 334 行会用 json.dump 整文件覆写；用户下一次操作就会永久丢掉原内容，且没有备份。
  - 怎么修：读失败时先备份为 *.corrupt-<时间>、记录原因，并拒绝用默认值覆写原件。
- **[应修]** `A04` `mbdsdr_ai/workflow_recorder.py:458` — except Exception:
  - 为什么：读损坏文件时静默回默认值，但同一模块第 425 行会用 json.dump 整文件覆写；用户下一次操作就会永久丢掉原内容，且没有备份。
  - 怎么修：读失败时先备份为 *.corrupt-<时间>、记录原因，并拒绝用默认值覆写原件。
- **[应修]** `A13` `mbdsdr_ai/device_manager.py:422` — serial = it.get("serial", "") or ("rtl-%d" % idx)
  - 为什么：serial 的身份值由枚举序号/循环下标拼成；插拔顺序一变，同一台设备就会变成另一台。
  - 怎么修：身份用序列号（缺失就明确拒绝并提示），不要用枚举序号或可改的标签兜底。
- **[应修]** `A13` `mbdsdr_ai/device_manager.py:451` — serial = str(sarg.get("serial", sarg.get("label", f"soapysdr-{i}")))
  - 为什么：serial 的身份值由枚举序号/循环下标拼成；插拔顺序一变，同一台设备就会变成另一台。
  - 怎么修：身份用序列号（缺失就明确拒绝并提示），不要用枚举序号或可改的标签兜底。
- **[应修]** `A13` `scripts/file_claim.py:540` — if holder != owner:
  - 为什么：用 owner/holder 字符串相等来判断「同一个持有者」；同名但不是同一进程时会误放行。
  - 怎么修：判同一必须用 pid + 进程启动时间（或设备序列号）这类唯一且不可复用的事实。
- **[应修]** `D2-multi-producer` `desktop/constellation_panel.py:0` — Hz 读数由 42 个文件产生
  - 为什么：同一事实存在多个产生点，尚未登记真值源；改一处口径不会传导到其他面板。
  - 怎么修：在 docs/truth-sources.json 登记唯一产生函数，消费点只读该函数。
- **[复核]** `D2-dual-constant` `desktop/adsb_map_panel.py:187` — ADSB_CENTER_HZ 有 2 个不同取值（1_090_000_000.0, 1_090_000_000）
  - 为什么：同名常量在不同模块取不同值；谁引用哪个模块就会得到不同结论。若只是重名不同职，应改名。
  - 怎么修：先判定是同一事实还是同名不同职：同一事实合并到唯一常量；不同事实改名。
- **[复核]** `D2-dual-constant` `desktop/adsb_panel.py:254` — COLUMNS 有 2 个不同取值（["ICAO", "呼号", "纬度",, ["时间", "源呼号", "目的呼号"）
  - 为什么：同名常量在不同模块取不同值；谁引用哪个模块就会得到不同结论。若只是重名不同职，应改名。
  - 怎么修：先判定是同一事实还是同名不同职：同一事实合并到唯一常量；不同事实改名。
- **[复核]** `D2-dual-constant` `desktop/ai_panel.py:29` — AI_CORE_AVAILABLE 有 2 个不同取值（True, False）
  - 为什么：同名常量在不同模块取不同值；谁引用哪个模块就会得到不同结论。若只是重名不同职，应改名。
  - 怎么修：先判定是同一事实还是同名不同职：同一事实合并到唯一常量；不同事实改名。
- **[复核]** `D2-dual-constant` `desktop/bookmark_panel.py:48` — GROUPS 有 2 个不同取值（[, ("菜单", "页签", "能力", "）
  - 为什么：同名常量在不同模块取不同值；谁引用哪个模块就会得到不同结论。若只是重名不同职，应改名。
  - 怎么修：先判定是同一事实还是同名不同职：同一事实合并到唯一常量；不同事实改名。
- **[复核]** `D2-dual-constant` `desktop/config_schema.py:46` — DEFAULT_SQUELCH_DBFS 有 2 个不同取值（-50.0, None  # type: ignore）
  - 为什么：同名常量在不同模块取不同值；谁引用哪个模块就会得到不同结论。若只是重名不同职，应改名。
  - 怎么修：先判定是同一事实还是同名不同职：同一事实合并到唯一常量；不同事实改名。
- **[复核]** `D2-dual-constant` `desktop/constants.py:26` — DEFAULT_SAMPLE_RATE_HZ 有 2 个不同取值（2_400_000, 1024000          # r）
  - 为什么：同名常量在不同模块取不同值；谁引用哪个模块就会得到不同结论。若只是重名不同职，应改名。
  - 怎么修：先判定是同一事实还是同名不同职：同一事实合并到唯一常量；不同事实改名。
- **[复核]** `D2-dual-constant` `desktop/constants.py:45` — FREQ_MIN_HZ 有 2 个不同取值（10_000.0, 325_000_000.0）
  - 为什么：同名常量在不同模块取不同值；谁引用哪个模块就会得到不同结论。若只是重名不同职，应改名。
  - 怎么修：先判定是同一事实还是同名不同职：同一事实合并到唯一常量；不同事实改名。
- **[复核]** `D2-dual-constant` `desktop/constants.py:46` — FREQ_MAX_HZ 有 2 个不同取值（6.0e9, 3_800_000_000.0）
  - 为什么：同名常量在不同模块取不同值；谁引用哪个模块就会得到不同结论。若只是重名不同职，应改名。
  - 怎么修：先判定是同一事实还是同名不同职：同一事实合并到唯一常量；不同事实改名。
- **[复核]** `D2-dual-constant` `desktop/device_panel.py:86` — PERF_DEFAULT_SR 有 2 个不同取值（int(_profile_rate(_D, int(DEFAULT_SAMPLE_R）
  - 为什么：同名常量在不同模块取不同值；谁引用哪个模块就会得到不同结论。若只是重名不同职，应改名。
  - 怎么修：先判定是同一事实还是同名不同职：同一事实合并到唯一常量；不同事实改名。
- **[复核]** `D2-dual-constant` `desktop/mcp_worker.py:35` — HAS_MCP_CLIENT 有 2 个不同取值（True, False）
  - 为什么：同名常量在不同模块取不同值；谁引用哪个模块就会得到不同结论。若只是重名不同职，应改名。
  - 怎么修：先判定是同一事实还是同名不同职：同一事实合并到唯一常量；不同事实改名。
- **[复核]** `D2-dual-constant` `desktop/osm_map.py:81` — USER_AGENT 有 2 个不同取值（("MBDSDR/1.0 (amateu, ("MBDSDR/1.0 (amateu）
  - 为什么：同名常量在不同模块取不同值；谁引用哪个模块就会得到不同结论。若只是重名不同职，应改名。
  - 怎么修：先判定是同一事实还是同名不同职：同一事实合并到唯一常量；不同事实改名。
- **[复核]** `D2-dual-constant` `desktop/rf_sky_view.py:986` — AU_KM 有 3 个不同取值（149597870.7, 149597870.700, 149597870.7         ）
  - 为什么：同名常量在不同模块取不同值；谁引用哪个模块就会得到不同结论。若只是重名不同职，应改名。
  - 怎么修：先判定是同一事实还是同名不同职：同一事实合并到唯一常量；不同事实改名。
- **豁免** `A01` `desktop/main_window.py:7495` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:7496` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:7541` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:7542` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:7574` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:7575` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:8565` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:8566` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:5196` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:7466` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:7467` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:5151` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:5152` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:5153` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:9661` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:14195` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A01` `desktop/main_window.py:14196` — main_window.py 处于冻结期，禁止修改；读数迁移排到解冻后统一做，当前先在 readouts.py 建唯一产生点。
- **豁免** `A02` `desktop/main_window.py:7495` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:7496` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:7541` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:7542` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:7574` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:7575` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:8565` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:8566` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:9184` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:11071` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:12419` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:7466` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:7467` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:10766` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:13529` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:750` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:5151` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:5152` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:5153` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:8220` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:8225` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:12348` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:14195` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:14196` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:14221` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。
- **豁免** `A02` `desktop/main_window.py:745` — 同上：冻结文件内的 3 位小数 MHz 读数不能在本轮改，解冻后统一改为 readouts.format_frequency_hz。

## D3 接口契约与命名（违规 33，豁免 0）

> 规范：模块对外只暴露登记过的东西；同名不同职、同概念两名都算违规。

- 事实：公开函数/类 >=8 但未声明 __all__ 的模块：196 个
- 事实：同名不同职的公开函数：37 组
- 事实：同概念两名 bw/bandwidth：各在 28/26 个文件出现
- 事实：同概念两名 freq/frequency：各在 75/10 个文件出现
- 事实：同概念两名 sr/sample_rate：各在 70/98 个文件出现
- 事实：同概念两名 gain/gain_db：各在 33/33 个文件出现
- 事实：同概念两名 dev/device：各在 28/35 个文件出现
- 事实：同概念两名 cfg/config：各在 35/21 个文件出现
- 事实：同概念两名 amp/amplitude：各在 21/18 个文件出现
- 事实：同概念两名 lvl/level：各在 6/31 个文件出现

- **[复核]** `D3-no-contract` `desktop/ai_steps.py:45` — 模块未声明 __all__
  - 为什么：模块对外暴露面未登记，调用方无法判断哪些名字是契约、哪些是内部实现。
  - 怎么修：补 __all__，只列真正对外承诺的名字。
- **[复核]** `D3-no-contract` `desktop/connect_request_store.py:65` — 模块未声明 __all__
  - 为什么：模块对外暴露面未登记，调用方无法判断哪些名字是契约、哪些是内部实现。
  - 怎么修：补 __all__，只列真正对外承诺的名字。
- **[复核]** `D3-no-contract` `desktop/error_remedy.py:97` — 模块未声明 __all__
  - 为什么：模块对外暴露面未登记，调用方无法判断哪些名字是契约、哪些是内部实现。
  - 怎么修：补 __all__，只列真正对外承诺的名字。
- **[复核]** `D3-no-contract` `desktop/mode_registry.py:35` — 模块未声明 __all__
  - 为什么：模块对外暴露面未登记，调用方无法判断哪些名字是契约、哪些是内部实现。
  - 怎么修：补 __all__，只列真正对外承诺的名字。
- **[复核]** `D3-no-contract` `desktop/rf_sky_view.py:197` — 模块未声明 __all__
  - 为什么：模块对外暴露面未登记，调用方无法判断哪些名字是契约、哪些是内部实现。
  - 怎么修：补 __all__，只列真正对外承诺的名字。
- **[复核]** `D3-no-contract` `desktop/settings.py:71` — 模块未声明 __all__
  - 为什么：模块对外暴露面未登记，调用方无法判断哪些名字是契约、哪些是内部实现。
  - 怎么修：补 __all__，只列真正对外承诺的名字。
- **[复核]** `D3-no-contract` `desktop/shortcuts.py:34` — 模块未声明 __all__
  - 为什么：模块对外暴露面未登记，调用方无法判断哪些名字是契约、哪些是内部实现。
  - 怎么修：补 __all__，只列真正对外承诺的名字。
- **[复核]** `D3-no-contract` `desktop/startup_paths.py:37` — 模块未声明 __all__
  - 为什么：模块对外暴露面未登记，调用方无法判断哪些名字是契约、哪些是内部实现。
  - 怎么修：补 __all__，只列真正对外承诺的名字。
- **[复核]** `D3-no-contract` `desktop/themes.py:200` — 模块未声明 __all__
  - 为什么：模块对外暴露面未登记，调用方无法判断哪些名字是契约、哪些是内部实现。
  - 怎么修：补 __all__，只列真正对外承诺的名字。
- **[复核]** `D3-no-contract` `mbdsdr_ai/acars_decoder.py:103` — 模块未声明 __all__
  - 为什么：模块对外暴露面未登记，调用方无法判断哪些名字是契约、哪些是内部实现。
  - 怎么修：补 __all__，只列真正对外承诺的名字。
- **[复核]** `D3-name-role` `mbdsdr_ai/gimbal_follow.py:31` — angular_separation_deg 与 desktop/rf_sky_view.py:646 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-name-role` `mbdsdr_ai/adsb_lite.py:72` — build_long_frame 与 mbdsdr_ai/adsb.py:354 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-name-role` `mbdsdr_ai/location/places.py:294` — haversine_km 与 mbdsdr_ai/adsb_map.py:60 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-name-role` `mbdsdr_ai/ax25.py:304` — stuff_bits 与 mbdsdr_ai/ais_decoder.py:377 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-name-role` `mbdsdr_ai/ax25.py:320` — unstuff_bits 与 mbdsdr_ai/ais_decoder.py:390 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-name-role` `mbdsdr_ai/new_spacetime.py:804` — predict_satellite_pass 与 mbdsdr_ai/astronomy.py:540 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-name-role` `mbdsdr_ai/blind_param_est.py:388` — register_tool_registry 与 mbdsdr_ai/bch_codecs.py:279 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-name-role` `mbdsdr_ai/fir_taps.py:98` — lowpass_taps 与 mbdsdr_ai/channelizer.py:65 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-name-role` `mbdsdr_ai/sstv_decoder.py:1006` — decode_sstv 与 mbdsdr_ai/decoders.py:580 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-name-role` `mbdsdr_ai/gk2a_lrit.py:521` — rrc_filter 与 mbdsdr_ai/demod.py:23 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-name-role` `mbdsdr_ai/gk2a_lrit.py:1166` — file_to_vcdus 与 mbdsdr_ai/fengyun_sat.py:286 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-name-role` `mbdsdr_ai/lrpt_render.py:158` — save_png 与 mbdsdr_ai/fengyun_sat.py:816 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-name-role` `mbdsdr_ai/ft8_ldpc.py:48` — ldpc_bp_decode 与 mbdsdr_ai/fst4_ldpc.py:58 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-name-role` `mbdsdr_ai/ft8_decode.py:53` — soft_tones_to_llr 与 mbdsdr_ai/ft4_codec.py:179 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-name-role` `mbdsdr_ai/ft8_encode.py:159` — crc14_bits 与 mbdsdr_ai/ft8_codec.py:105 同名但实现不同
  - 为什么：同一个名字在不同模块承担不同职责，调用方按名字迁移就会拿错语义。
  - 怎么修：保留职责更明确的一方，另一方改名（名字里带上领域限定）。
- **[复核]** `D3-concept-alias` `desktop/connect_request_store.py:0` — bw 与 bandwidth 同时使用
  - 为什么：同一概念两个名字，跨模块搜索/替换必然漏掉另一半。
  - 怎么修：选一个名字写进术语表，另一个在公共接口中移除。
- **[复核]** `D3-concept-alias` `desktop/bookmark_panel.py:0` — freq 与 frequency 同时使用
  - 为什么：同一概念两个名字，跨模块搜索/替换必然漏掉另一半。
  - 怎么修：选一个名字写进术语表，另一个在公共接口中移除。
- **[复核]** `D3-concept-alias` `desktop/device_panel.py:0` — sr 与 sample_rate 同时使用
  - 为什么：同一概念两个名字，跨模块搜索/替换必然漏掉另一半。
  - 怎么修：选一个名字写进术语表，另一个在公共接口中移除。
- **[复核]** `D3-concept-alias` `desktop/device_panel.py:0` — gain 与 gain_db 同时使用
  - 为什么：同一概念两个名字，跨模块搜索/替换必然漏掉另一半。
  - 怎么修：选一个名字写进术语表，另一个在公共接口中移除。
- **[复核]** `D3-concept-alias` `desktop/device_panel.py:0` — dev 与 device 同时使用
  - 为什么：同一概念两个名字，跨模块搜索/替换必然漏掉另一半。
  - 怎么修：选一个名字写进术语表，另一个在公共接口中移除。
- **[复核]** `D3-concept-alias` `desktop/adsb_panel.py:0` — cfg 与 config 同时使用
  - 为什么：同一概念两个名字，跨模块搜索/替换必然漏掉另一半。
  - 怎么修：选一个名字写进术语表，另一个在公共接口中移除。
- **[复核]** `D3-concept-alias` `desktop/rf_sky_view.py:0` — amp 与 amplitude 同时使用
  - 为什么：同一概念两个名字，跨模块搜索/替换必然漏掉另一半。
  - 怎么修：选一个名字写进术语表，另一个在公共接口中移除。
- **[复核]** `D3-concept-alias` `mbdsdr_ai/core/controller.py:0` — lvl 与 level 同时使用
  - 为什么：同一概念两个名字，跨模块搜索/替换必然漏掉另一半。
  - 怎么修：选一个名字写进术语表，另一个在公共接口中移除。

## D4 门禁与验收（违规 42，豁免 0）

> 规范：门禁必须真的挂在必经路径上；守卫必须可成立、可反证。

- 事实：已安装 git 钩子：2 个
- 事实：门禁类脚本：17 个；钩子直接调用 2 个；文档登记 5 个
- 事实：测试侧：pytest.skip/skipif 59 处，xfail 18 处

- **[应修]** `A05` `tests/test_network_failure_modes.py:407` — assert finished, (
  - 为什么：断言消息自述通过条件不成立（f"慢滴握手 2.5s 仍未返回（{elapsed:.1f}s）；按当前实现"
                f"需要），该断言在当前实现下不可能变绿。
  - 怎么修：降为显式前置条件并标注原因，或登记进 docs/known-red-register.md 并限制为可跟踪的 xfail(strict=True)。
- **[应修]** `A06` `mbdsdr_ai/tests/test_phase58_multisat.py:21` — except Exception as e:
  - 为什么：在宽泛 except 里 pytest.skip：任何实现错误都会被伪装成「环境不凑巧」。
  - 怎么修：先断言具体异常/依赖探测结果，只有明确的外部缺失才允许 skip。
- **[应修]** `A06` `tests/test_adsb_dump1090_golden.py:105` — except Exception as e:  # noqa: BLE001
  - 为什么：在宽泛 except 里 pytest.skip：任何实现错误都会被伪装成「环境不凑巧」。
  - 怎么修：先断言具体异常/依赖探测结果，只有明确的外部缺失才允许 skip。
- **[应修]** `A06` `tests/test_ft8_wsjtx_golden.py:104` — except Exception as e:  # noqa: BLE001
  - 为什么：在宽泛 except 里 pytest.skip：任何实现错误都会被伪装成「环境不凑巧」。
  - 怎么修：先断言具体异常/依赖探测结果，只有明确的外部缺失才允许 skip。
- **[应修]** `A06` `tests/test_lrpt_meteor_real_golden.py:88` — except Exception as exc:  # noqa: BLE001
  - 为什么：在宽泛 except 里 pytest.skip：任何实现错误都会被伪装成「环境不凑巧」。
  - 怎么修：先断言具体异常/依赖探测结果，只有明确的外部缺失才允许 skip。
- **[应修]** `A06` `tests/test_satellite_tracker.py:67` — except Exception as e:  # 无网/缺星历文件则跳过
  - 为什么：在宽泛 except 里 pytest.skip：任何实现错误都会被伪装成「环境不凑巧」。
  - 怎么修：先断言具体异常/依赖探测结果，只有明确的外部缺失才允许 skip。
- **[应修]** `A07` `desktop/scanner_panel.py:116` — be.set_frequency(float(center_hz))
  - 为什么：set_frequency 的数值前置条件只出现在 desktop/control_panel.py 等入口，desktop/scanner_panel.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[应修]** `A07` `mbdsdr_ai/rtltcp_client.py:729` — self.set_frequency(target_freq)
  - 为什么：set_frequency 的数值前置条件只出现在 desktop/control_panel.py 等入口，mbdsdr_ai/rtltcp_client.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[应修]** `A07` `mbdsdr_ai/sdr_backend.py:2878` — self.set_frequency(target_freq)
  - 为什么：set_frequency 的数值前置条件只出现在 desktop/control_panel.py 等入口，mbdsdr_ai/sdr_backend.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[应修]** `A07` `mbdsdr_ai/spyserver_client.py:663` — self.set_frequency(self.status.frequency_hz or 98_000_000.0)
  - 为什么：set_frequency 的数值前置条件只出现在 desktop/control_panel.py 等入口，mbdsdr_ai/spyserver_client.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[应修]** `A07` `mbdsdr_ai/core/cli.py:149` — ctrl.set_frequency(args.freq)
  - 为什么：set_frequency 的数值前置条件只出现在 desktop/control_panel.py 等入口，mbdsdr_ai/core/cli.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[应修]** `A07` `mbdsdr_ai/core/controller.py:865` — self._backend.set_frequency(center_hz)
  - 为什么：set_frequency 的数值前置条件只出现在 desktop/control_panel.py 等入口，mbdsdr_ai/core/controller.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[应修]** `A07` `mbdsdr_ai/core/mcp_server.py:559` — lambda c, a: (c.set_frequency(float(a["hz"])),
  - 为什么：set_frequency 的数值前置条件只出现在 desktop/control_panel.py 等入口，mbdsdr_ai/core/mcp_server.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[应修]** `A07` `desktop/main_window.py:7358` — _run("带宽", lambda: backend.set_bandwidth(bandwidth))
  - 为什么：set_bandwidth 的数值前置条件只出现在 mbdsdr_ai/sdr_backend.py 等入口，desktop/main_window.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[应修]** `A07` `mbdsdr_ai/core/cli.py:161` — ctrl.set_bandwidth(args.bandwidth)
  - 为什么：set_bandwidth 的数值前置条件只出现在 mbdsdr_ai/sdr_backend.py 等入口，mbdsdr_ai/core/cli.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[应修]** `A07` `mbdsdr_ai/core/mcp_server.py:598` — lambda c, a: (c.set_bandwidth(float(a["hz"])),
  - 为什么：set_bandwidth 的数值前置条件只出现在 mbdsdr_ai/sdr_backend.py 等入口，mbdsdr_ai/core/mcp_server.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[应修]** `A07` `mbdsdr_ai/rtltcp_client.py:726` — self.set_sample_rate(self.status.sample_rate_hz)
  - 为什么：set_sample_rate 的数值前置条件只出现在 desktop/main_window.py 等入口，mbdsdr_ai/rtltcp_client.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[应修]** `A07` `mbdsdr_ai/sdr_backend.py:2872` — self.set_sample_rate(self.status.sample_rate_hz)
  - 为什么：set_sample_rate 的数值前置条件只出现在 desktop/main_window.py 等入口，mbdsdr_ai/sdr_backend.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[应修]** `A07` `mbdsdr_ai/spyserver_client.py:662` — self.set_sample_rate(self.status.sample_rate_hz)
  - 为什么：set_sample_rate 的数值前置条件只出现在 desktop/main_window.py 等入口，mbdsdr_ai/spyserver_client.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[应修]** `A07` `mbdsdr_ai/core/cli.py:158` — ctrl.set_sample_rate(args.sample_rate)
  - 为什么：set_sample_rate 的数值前置条件只出现在 desktop/main_window.py 等入口，mbdsdr_ai/core/cli.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[应修]** `A07` `mbdsdr_ai/core/mcp_server.py:593` — lambda c, a: (c.set_sample_rate(float(a["sr"])),
  - 为什么：set_sample_rate 的数值前置条件只出现在 desktop/main_window.py 等入口，mbdsdr_ai/core/mcp_server.py 这条调用路径没有同一约束；真正执行动作的地方也没有，所以这条路径能绕过去。
  - 怎么修：把约束下沉到动作的必经实现（后端方法）或唯一网关，调用方只负责展示。
- **[复核]** `A08` `mbdsdr_ai/decoders.py:435` — if False:
  - 为什么：分支条件只由字面量组成，折叠结果为 False；这条守卫要么永远执行、要么永远不执行，它看起来在防一件事，实际没防。
  - 怎么修：确认判的是不是该判的对象；条件应来自运行时可得、可能变化的可用性状态。
- **[应修]** `A09` `mbdsdr_ai/tests/test_dvbs2_bch.py:78` — _KAT_SKIP = pytest.mark.skipif(
  - 为什么：skip 理由只写了「external independent DVB-S2 BCH KAT is not availab」，没有可核对的缺失条件；本仓依赖缺失/构造失败会被长期伪装成环境不凑巧。
  - 怎么修：写清缺的是哪个依赖/文件/硬件，并先断言探测结果；本仓自身缺陷一律改红。
- **[应修]** `A09` `mbdsdr_ai/tests/test_dvbs2_bch.py:195` — pytest.skip("verified standard-parameter extract is not available")
  - 为什么：skip 理由只写了「verified standard-parameter extract is not availab」，没有可核对的缺失条件；本仓依赖缺失/构造失败会被长期伪装成环境不凑巧。
  - 怎么修：写清缺的是哪个依赖/文件/硬件，并先断言探测结果；本仓自身缺陷一律改红。
- **[应修]** `A09` `mbdsdr_ai/tests/test_dvbs2_bch.py:89` — marks=pytest.mark.skip(reason="external independent DVB-S2 BCH KAT is not available"),
  - 为什么：skip 理由只写了「external independent DVB-S2 BCH KAT is not availab」，没有可核对的缺失条件；本仓依赖缺失/构造失败会被长期伪装成环境不凑巧。
  - 怎么修：写清缺的是哪个依赖/文件/硬件，并先断言探测结果；本仓自身缺陷一律改红。
- **[应修]** `A09` `tests/test_web_bridge_mcp.py:205` — pytest.skip("MCPToolSet 在此环境不可构造")
  - 为什么：skip 理由只写了「MCPToolSet 在此环境不可构造」，没有可核对的缺失条件；本仓依赖缺失/构造失败会被长期伪装成环境不凑巧。
  - 怎么修：写清缺的是哪个依赖/文件/硬件，并先断言探测结果；本仓自身缺陷一律改红。
- **[应修]** `A09` `tests/test_web_bridge_mcp.py:220` — pytest.skip("MCPToolSet 在此环境不可构造")
  - 为什么：skip 理由只写了「MCPToolSet 在此环境不可构造」，没有可核对的缺失条件；本仓依赖缺失/构造失败会被长期伪装成环境不凑巧。
  - 怎么修：写清缺的是哪个依赖/文件/硬件，并先断言探测结果；本仓自身缺陷一律改红。
- **[应修]** `A09` `tests/test_web_bridge_mcp.py:231` — pytest.skip("MCPToolSet 在此环境不可构造")
  - 为什么：skip 理由只写了「MCPToolSet 在此环境不可构造」，没有可核对的缺失条件；本仓依赖缺失/构造失败会被长期伪装成环境不凑巧。
  - 怎么修：写清缺的是哪个依赖/文件/硬件，并先断言探测结果；本仓自身缺陷一律改红。
- **[应修]** `A09` `tests/test_web_bridge_mcp.py:245` — pytest.skip("MCPToolSet 在此环境不可构造")
  - 为什么：skip 理由只写了「MCPToolSet 在此环境不可构造」，没有可核对的缺失条件；本仓依赖缺失/构造失败会被长期伪装成环境不凑巧。
  - 怎么修：写清缺的是哪个依赖/文件/硬件，并先断言探测结果；本仓自身缺陷一律改红。
- **[应修]** `A09` `tests/test_web_bridge_mcp.py:274` — pytest.skip("MCPToolSet 在此环境不可构造")
  - 为什么：skip 理由只写了「MCPToolSet 在此环境不可构造」，没有可核对的缺失条件；本仓依赖缺失/构造失败会被长期伪装成环境不凑巧。
  - 怎么修：写清缺的是哪个依赖/文件/硬件，并先断言探测结果；本仓自身缺陷一律改红。
- **[应修]** `A09` `tests/test_web_bridge_mcp.py:304` — pytest.skip("MCPToolSet 在此环境不可构造")
  - 为什么：skip 理由只写了「MCPToolSet 在此环境不可构造」，没有可核对的缺失条件；本仓依赖缺失/构造失败会被长期伪装成环境不凑巧。
  - 怎么修：写清缺的是哪个依赖/文件/硬件，并先断言探测结果；本仓自身缺陷一律改红。
- **[复核]** `D4-silent-gate` `scripts/audit_source_text_pins.py:0` — 未接入钩子，也未在文档登记
  - 为什么：门禁脚本存在但没有任何入口，也没有说明它是批次末手动执行；等于静默失效。
  - 怎么修：接入钩子，或在 docs/code-guards.md 登记「手动执行 + 执行时机」。
- **[复核]** `D4-silent-gate` `scripts/check_action_duplication.py:0` — 未接入钩子，也未在文档登记
  - 为什么：门禁脚本存在但没有任何入口，也没有说明它是批次末手动执行；等于静默失效。
  - 怎么修：接入钩子，或在 docs/code-guards.md 登记「手动执行 + 执行时机」。
- **[复核]** `D4-silent-gate` `scripts/check_card_links.py:0` — 未接入钩子，也未在文档登记
  - 为什么：门禁脚本存在但没有任何入口，也没有说明它是批次末手动执行；等于静默失效。
  - 怎么修：接入钩子，或在 docs/code-guards.md 登记「手动执行 + 执行时机」。
- **[复核]** `D4-silent-gate` `scripts/check_data_licenses.py:0` — 未接入钩子，也未在文档登记
  - 为什么：门禁脚本存在但没有任何入口，也没有说明它是批次末手动执行；等于静默失效。
  - 怎么修：接入钩子，或在 docs/code-guards.md 登记「手动执行 + 执行时机」。
- **[复核]** `D4-silent-gate` `scripts/check_destructive_ops.py:0` — 未接入钩子，也未在文档登记
  - 为什么：门禁脚本存在但没有任何入口，也没有说明它是批次末手动执行；等于静默失效。
  - 怎么修：接入钩子，或在 docs/code-guards.md 登记「手动执行 + 执行时机」。
- **[复核]** `D4-silent-gate` `scripts/check_dist_head_parity.py:0` — 未接入钩子，也未在文档登记
  - 为什么：门禁脚本存在但没有任何入口，也没有说明它是批次末手动执行；等于静默失效。
  - 怎么修：接入钩子，或在 docs/code-guards.md 登记「手动执行 + 执行时机」。
- **[复核]** `D4-silent-gate` `scripts/check_help_paths.py:0` — 未接入钩子，也未在文档登记
  - 为什么：门禁脚本存在但没有任何入口，也没有说明它是批次末手动执行；等于静默失效。
  - 怎么修：接入钩子，或在 docs/code-guards.md 登记「手动执行 + 执行时机」。
- **[复核]** `D4-silent-gate` `scripts/check_roadmap_blocking.py:0` — 未接入钩子，也未在文档登记
  - 为什么：门禁脚本存在但没有任何入口，也没有说明它是批次末手动执行；等于静默失效。
  - 怎么修：接入钩子，或在 docs/code-guards.md 登记「手动执行 + 执行时机」。
- **[复核]** `D4-silent-gate` `scripts/check_rollback_table.py:0` — 未接入钩子，也未在文档登记
  - 为什么：门禁脚本存在但没有任何入口，也没有说明它是批次末手动执行；等于静默失效。
  - 怎么修：接入钩子，或在 docs/code-guards.md 登记「手动执行 + 执行时机」。
- **[复核]** `D4-silent-gate` `scripts/check_test_version.py:0` — 未接入钩子，也未在文档登记
  - 为什么：门禁脚本存在但没有任何入口，也没有说明它是批次末手动执行；等于静默失效。
  - 怎么修：接入钩子，或在 docs/code-guards.md 登记「手动执行 + 执行时机」。
- **[复核]** `D4-silent-gate` `scripts/report_token_dual_values.py:0` — 未接入钩子，也未在文档登记
  - 为什么：门禁脚本存在但没有任何入口，也没有说明它是批次末手动执行；等于静默失效。
  - 怎么修：接入钩子，或在 docs/code-guards.md 登记「手动执行 + 执行时机」。

## D5 流程与协作（违规 40，豁免 0）

> 规范：引用必须可复核；已知红必须带转绿条件与复跑命令。

- 事实：已知红条目：12 条
- 事实：协作记录：133 处 claim 提及

- **[应修]** `A10` `docs/COORDINATION.md:3430` — #### 7.3.1 红项账本（诚实工程线 `0d7e433e` 草稿并入，2026-11-06）
  - 为什么：文档引用的提交 `0d7e433e` 在本仓不存在；基于它的后续结论无法复核。
  - 怎么修：改为真实提交号；未落地的改动标注「未落地」，不要用提交口吻引用。
- **[应修]** `A10` `docs/推进报告-本轮排错.md:141` — **本地提交**：`5d09bb2`（`master` 分支，2023 文件入库，工作区干净，`cpp/build/` 已被
  - 为什么：文档引用的提交 `5d09bb2` 在本仓不存在；基于它的后续结论无法复核。
  - 怎么修：改为真实提交号；未落地的改动标注「未落地」，不要用提交口吻引用。
- **[复核]** `A11` `docs/action-duplication-waivers.md:11` — 白名单清零后闸门自然全绿，不需要额外开关。
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/code-guards.md:90` — python -m pytest tests/test_swallow_guard.py -q            # 全绿（棘轮不越线）
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/COORDINATION.md:119` — （`output/_legacy_red_lane3.log`）；身份门 3 全绿；污染+受害+behavior
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/COORDINATION.md:971` — 几乎同名按钮 —— 测试全绿但真机截图里用户不知道点哪个。删掉独立按钮，
  - 为什么：绿色声明绑定的是 868 个提交之前的树；它证明不了当前树的任何事。
  - 怎么修：在当前 HEAD 上重跑并更新提交号，或在文中明确标注「历史快照，不适用于当前树」。
- **[复核]** `A11` `docs/COORDINATION.md:1254` — 瀑布半边兑现：4px 顶缘条复用既有几何/色/开关机制，3 新守卫全绿；
  - 为什么：绿色声明绑定的是 1672 个提交之前的树；它证明不了当前树的任何事。
  - 怎么修：在当前 HEAD 上重跑并更新提交号，或在文中明确标注「历史快照，不适用于当前树」。
- **[复核]** `A11` `docs/COORDINATION.md:3978` — 动作 0/0/0/0；交互守卫 + UI 硬缺陷 20 项全绿；改动相关子集 296 项唯一红
  - 为什么：绿色声明绑定的是 763 个提交之前的树；它证明不了当前树的任何事。
  - 怎么修：在当前 HEAD 上重跑并更新提交号，或在文中明确标注「历史快照，不适用于当前树」。
- **[复核]** `A11` `docs/copy-audit.md:225` — - 快捷键真实性：Python `shortcuts.py` 的 42 条登记项由单一事实源生成，帮助页动态取键，快捷键 guard/焦点优先级测试 15 项全部通过；C++ `shortcuts_catalog.h` 9 条在 `main_window.cpp:3785-3851` 逐条有真实 `QShortcut` 绑定，未发现“写了不生效”或同键双绑。
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/discoverability.md:202` — - `test_shortcuts_guard.py` 全绿（含原先失败的 2 条）。
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/doppler-compensation.md:119` — 2. 调谐/偏移全部通过 `:3512-3514 _worker.request_tool.emit("tune_sdr", ...)`
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/error-code-visibility.md:35` — - `mapping_gaps() == []`、`mapping_overlaps() == []`（复跑 `tests/test_error_remedy_links.py` 全绿）。
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/evidence-standard.md:369` — "工具全绿 = 全仓无假断言"的那个洞。
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/known-red-register.md:73` — collect 清单」映射 nodeid 得到 15 条，并判成「单跑全绿 = 顺序依赖假红」。
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/network-layer.md:406` — 已连接）；恢复分支后 6 条全绿。
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/packaging.md:590` — 子集命令现可独立复现全绿，不依赖全量顺序。
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/PRD-现状对照.md:53` — | P0-10 | 真实性与诚实性基线 | **半有** | 部分真机 + 部分仅自测 | 错误目录 [error-catalog.md](file:///D:/mbdsdr/MBDSDR/docs/error-catalog.md)；离线 A/B [offline-behavior.md](file:///D:/mbdsdr/MBDSDR/docs/offline-behavior.md)；配置
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/PRD.md:552` — - **现状**：半有｜证据：错误码单一来源 `docs/error-catalog.md` + `tests/test_error_catalog_guard.py`；离线 A/B 二分 `docs/offline-behavior.md`（联网功能 A/B 判定、9 项违反项处置见该文档）；读数单点格式化与半格对拍 `docs/limitations-register.md` L30 + `t
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/PRD.md:835` — - AC-R2（可复跑脚本）：`scripts/repro/2026-11-12-deferred-import-verify.py` 三条反证全绿——`gate_missing` 模拟缺件 → 3/3 退出码 3、弹窗文案含「缺了会怎样 / 怎么修」、不导入主窗口；`defer_broken` 模拟「文件在但加载失败」→ 3/3 首帧已出后报 1 条、状态栏有提示、进程不崩；`frame_gap
  - 为什么：绿色声明绑定的是 1351 个提交之前的树；它证明不了当前树的任何事。
  - 怎么修：在当前 HEAD 上重跑并更新提交号，或在文中明确标注「历史快照，不适用于当前树」。
- **[复核]** `A11` `docs/PRD.md:850` — - AC-R5（已有自动化）：`python -m pytest tests/test_s8_backend_loss.py tests/test_backend_resilience.py -q` 全绿。`test_s8_backend_loss.py` 覆盖：后端异常上报为断开、陈旧数据不依赖信号、未连接态、新鲜数据保持、宽限期不误报、未知新鲜度交给信号路径、尚无帧不算丢、短停顿只警告且第三次
  - 为什么：绿色声明绑定的是 1425 个提交之前的树；它证明不了当前树的任何事。
  - 怎么修：在当前 HEAD 上重跑并更新提交号，或在文中明确标注「历史快照，不适用于当前树」。
- **[复核]** `A11` `docs/PRD.md:883` — - AC-R12（已有自动化）：`python -m pytest tests/test_doppler_readout.py -q` 全绿（8 条）。
  - 为什么：绿色声明绑定的是 1478 个提交之前的树；它证明不了当前树的任何事。
  - 怎么修：在当前 HEAD 上重跑并更新提交号，或在文中明确标注「历史快照，不适用于当前树」。
- **[复核]** `A11` `docs/PRD.md:885` — - AC-R14（已有自动化）：`python -m pytest tests/test_tune_failure_no_commit.py -q` 全绿（`d0445ba`）。
  - 为什么：绿色声明绑定的是 1478 个提交之前的树；它证明不了当前树的任何事。
  - 怎么修：在当前 HEAD 上重跑并更新提交号，或在文中明确标注「历史快照，不适用于当前树」。
- **[复核]** `A11` `docs/redundancy-register.md:408` — | 真可删 | `mbdsdr_ai/adsb_lite.py` | `build_short_frame` | 无调用方；短帧不在 `decode_baseband` 主链 | adsb 3 个测试文件全绿 |
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/reference-mechanisms.md:94` — 3. `tests/test_goto_anim_driver.py` 全绿，duration 语义不变；
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/settings-inventory.md:18` — | 设置面板新增测试 | **116 个** | `tests/test_settings_schema.py`，全绿（含 10 对联动提示钉扎 + 14 项手改 JSON 非法值矩阵 + 5 项内部键消费端实测 + 6 项 demod_mode 枚举收口 + 39 项全枚举覆盖：13 个枚举 × 归一/合法值/落盘读回） |
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/star-catalog.md:72` — 新增测试共 **32 项**（星表/守卫 29 + 打包 3），全部通过。
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/WINDOWS_POPUP_PLAYBOOK.md:94` — 一起跑）——全绿则 CI 红为该 runner 环境特有（无声卡/无显示/无硬件/
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/推进报告-本轮排错.md:48` — | 修复后 | **158/158 全绿**（e2e_smoke 无硬件诚实 SKIP） |
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/a11y/2026-11-12-dual-value-governance-plan.md:112` — - 门禁：会少 5 条检查（5 套主题 × `success/bg`），当前全部通过。
  - 为什么：「全绿/全部通过」的声明没有绑定任何提交，读者无法知道它说的是哪棵树。
  - 怎么修：补上实跑所在的提交号，或标注为历史快照。
- **[复核]** `A11` `docs/pending-patches/2026-11-11-first-open-banner-width.md:78` — 3. `pytest tests/test_first_open_welcome.py -q` 全绿，且断言未被放宽；**该用例现在
  - 为什么：绿色声明绑定的是 429 个提交之前的树；它证明不了当前树的任何事。
  - 怎么修：在当前 HEAD 上重跑并更新提交号，或在文中明确标注「历史快照，不适用于当前树」。
- **[复核]** `A11` `docs/research/2026-11-11-roadmap.md:322` — | W50 | `tests/test_right_column_nav.py` | 跳转 y 坐标与菜单集合 20/20（P1-01/P1-02） | W46、W31 | 部分完成（`dc56a37a` 已落 `tests/test_right_column_nav.py`：20 节逐个跳转落点断言已实装；**缺**菜单 20/20——该断言为 `xfail(strict)`，原因写明「W31 
  - 为什么：绿色声明绑定的是 611 个提交之前的树；它证明不了当前树的任何事。
  - 怎么修：在当前 HEAD 上重跑并更新提交号，或在文中明确标注「历史快照，不适用于当前树」。
- **[复核]** `A11` `docs/research/2026-11-11-roadmap.md:540` — - 变更记录 2026-11-12（第二十一笔）：**R-06 ② 改口**——不是「不稳定红」，是**确定性红**：区块 12 在 `ddc081a1^` 的临时 worktree 原样复跑得 `2 failed / 11 passed`（`test_every_catalog_code_is_registered` 断言 `['decoder_not_streamable'] == []`；`
  - 为什么：绿色声明绑定的是 489 个提交之前的树；它证明不了当前树的任何事。
  - 怎么修：在当前 HEAD 上重跑并更新提交号，或在文中明确标注「历史快照，不适用于当前树」。
- **[应修]** `D5-known-red-incomplete` `docs/known-red-register.md:70` — K05 缺：转绿条件、首次记录日期
  - 为什么：已知红缺少可执行字段，无法判断它是否还红、何时能销账。
  - 怎么修：补齐 nodeid、转绿条件、复跑命令与首次记录日期。
- **[应修]** `D5-known-red-incomplete` `docs/known-red-register.md:164` — K05 缺：转绿条件、首次记录日期
  - 为什么：已知红缺少可执行字段，无法判断它是否还红、何时能销账。
  - 怎么修：补齐 nodeid、转绿条件、复跑命令与首次记录日期。
- **[应修]** `D5-known-red-incomplete` `docs/known-red-register.md:195` — K08 缺：复跑命令
  - 为什么：已知红缺少可执行字段，无法判断它是否还红、何时能销账。
  - 怎么修：补齐 nodeid、转绿条件、复跑命令与首次记录日期。
- **[应修]** `D5-known-red-incomplete` `docs/known-red-register.md:227` — K09 缺：转绿条件、复跑命令、首次记录日期
  - 为什么：已知红缺少可执行字段，无法判断它是否还红、何时能销账。
  - 怎么修：补齐 nodeid、转绿条件、复跑命令与首次记录日期。
- **[应修]** `D5-known-red-incomplete` `docs/known-red-register.md:340` — K10 缺：转绿条件、复跑命令、首次记录日期
  - 为什么：已知红缺少可执行字段，无法判断它是否还红、何时能销账。
  - 怎么修：补齐 nodeid、转绿条件、复跑命令与首次记录日期。
- **[应修]** `D5-known-red-incomplete` `docs/known-red-register.md:365` — K11 缺：转绿条件、复跑命令、首次记录日期
  - 为什么：已知红缺少可执行字段，无法判断它是否还红、何时能销账。
  - 怎么修：补齐 nodeid、转绿条件、复跑命令与首次记录日期。
- **[应修]** `D5-known-red-incomplete` `docs/known-red-register.md:380` — K12 缺：复跑命令、首次记录日期
  - 为什么：已知红缺少可执行字段，无法判断它是否还红、何时能销账。
  - 怎么修：补齐 nodeid、转绿条件、复跑命令与首次记录日期。
- **[应修]** `D5-known-red-incomplete` `docs/known-red-register.md:408` — K13 缺：复跑命令
  - 为什么：已知红缺少可执行字段，无法判断它是否还红、何时能销账。
  - 怎么修：补齐 nodeid、转绿条件、复跑命令与首次记录日期。

## D6 目录与文档结构（违规 39，豁免 0）

> 规范：文档里的事实陈述必须与代码一致；引用的路径必须存在。

- 事实：文档引用仓内路径：4297 处；不存在：67 处
- 事实：desktop 非主题文件的硬编码颜色：211 处 / 17 个文件

- **[应修]** `D6-missing-path` `docs/COORDINATION.md:132` — 引用的 docs/architecture.zh.md 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/COORDINATION.md:1127` — 引用的 docs/09-repo-alignment.md 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/COORDINATION.md:1867` — 引用的 scripts/capture_throughput.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[复核]** `D6-missing-path` `docs/data-inventory.md:279` — 引用的 mbdsdr_ai/data/_gpl_probe.json 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。（该路径像运行时产物，需注明生成命令或移除引用。）
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[复核]** `D6-missing-path` `docs/data-inventory.md:297` — 引用的 mbdsdr_ai/data/_gpl_red_probe.json 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。（该路径像运行时产物，需注明生成命令或移除引用。）
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/data-inventory.md:387` — 引用的 docs/03-architecture.md 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/data-inventory.md:387` — 引用的 docs/06-compliance.md 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/data-inventory.md:388` — 引用的 docs/07-roadmap.md 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/data-inventory.md:388` — 引用的 docs/09-repo-alignment.md 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/data-inventory.md:388` — 引用的 docs/10-sky-catalog.md 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/data-inventory.md:436` — 引用的 tests/verify_product.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[复核]** `D6-stale-line` `docs/evidence-standard.md:625` — mbdsdr_ai/solar_system.py:1117 超出文件行数 1116
  - 为什么：文档里的行号已经失效，读者按行号跳转会落到无关位置。
  - 怎么修：改为当前行号或改用符号名定位。
- **[应修]** `D6-missing-path` `docs/limitations-register.md:442` — 引用的 tests/test_squelch_applied_by.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/limitations-register.md:445` — 引用的 tests/test_receive_chain_squelch_domain.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/limitations-register.md:722` — 引用的 scripts/vna_cable_analyze.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/limitations-register.md:758` — 引用的 scripts/vna_screenshot.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/modularization_status.md:51` — 引用的 mbdsdr_ai/my_decoder.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/packaging.md:711` — 引用的 tests/_reverse_probe_literal.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[复核]** `D6-missing-path` `docs/performance-baseline.md:267` — 引用的 docs/perf-evidence/2026-11-11/startup_1..3.json 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。（该路径像运行时产物，需注明生成命令或移除引用。）
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/redundancy-register.md:224` — 引用的 tests/test_module_panel_sample_rate_capability.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/redundancy-register.md:415` — 引用的 tests/apt_meteor_roundtrip.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[复核]** `D6-missing-path` `docs/redundancy-register.md:528` — 引用的 docs/deadcode-baseline.json 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。（该路径像运行时产物，需注明生成命令或移除引用。）
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[应修]** `D6-missing-path` `docs/ux-fix-plan.md:368` — 引用的 tests/test_planetarium_status.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[复核]** `D6-missing-path` `docs/audit_r2/28_astronomy_gnss.md:66` — 引用的 tools/compute-ephemeris.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。（历史快照文档，当时可能确实存在。）
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[复核]** `D6-stale-line` `docs/audit_r2/43_ui_status_themes_mcp.md:179` — desktop/mcp_worker.py:347 超出文件行数 346
  - 为什么：文档里的行号已经失效，读者按行号跳转会落到无关位置。
  - 怎么修：改为当前行号或改用符号名定位。
- **[复核]** `D6-missing-path` `docs/audit_r2/50_test_integration_part2.md:26` — 引用的 tests/test_report.txt 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。（历史快照文档，当时可能确实存在。）
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[复核]** `D6-missing-path` `docs/audit_r2/51_tool_tests.md:3` — 引用的 tests/ft8_roundtrip.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。（历史快照文档，当时可能确实存在。）
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[复核]** `D6-missing-path` `docs/audit_r2/56_real_hardware_closure.md:210` — 引用的 tests/test_report.txt 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。（历史快照文档，当时可能确实存在。）
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[复核]** `D6-missing-path` `docs/incidents/2026-11-12-phantom-delete-incident.md:49` — 引用的 tests/test_sky_lazy_build.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。（历史快照文档，当时可能确实存在。）
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[复核]** `D6-missing-path` `docs/incidents/2026-11-12-phantom-delete-incident.md:56` — 引用的 mbdsdr_ai/scratch_render.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。（历史快照文档，当时可能确实存在。）
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[复核]** `D6-missing-path` `docs/pending-patches/2026-11-06-deadcode-unused-symbols.md:13` — 引用的 tests/ft8_real_roundtrip.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。（历史快照文档，当时可能确实存在。）
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[复核]** `D6-missing-path` `docs/pending-patches/2026-11-06-deadcode-unused-symbols.md:14` — 引用的 tests/apt_meteor_roundtrip.py 不存在
  - 为什么：文档事实指向了仓库里不存在的路径，按文档操作会直接失败。（历史快照文档，当时可能确实存在。）
  - 怎么修：改为真实路径；文件已迁移时同步更新文档；产物路径写明生成方式。
- **[复核]** `D6-color-claim` `docs/COORDINATION.md:1443` — ≈1.02:1，仅触屏模式），与函数 docstring「只改 px/尺寸、不改颜色」不符。颜色早于
  - 为什么：文档声称颜色不改，但 desktop 非主题文件里仍有 211 处硬编码颜色；陈述与代码不一致。
  - 怎么修：要么把颜色迁移到 themes/tokens，要么在文档标注实际例外清单。
- **[复核]** `D6-color-claim` `docs/design_tokens_mapping.md:70` — 不改旧配色，用户手动选到 `figma` 才生效。
  - 为什么：文档声称颜色不改，但 desktop 非主题文件里仍有 211 处硬编码颜色；陈述与代码不一致。
  - 怎么修：要么把颜色迁移到 themes/tokens，要么在文档标注实际例外清单。
- **[复核]** `D6-color-claim` `docs/a11y/2026-11-12-landed-visual-changes.md:114` — ⚠️ `96e5ff2c` 动的是 `tokens.py` / `themes.py`，回退等于在冻结期内再改一次主题令牌，
  - 为什么：文档声称颜色不改，但 desktop 非主题文件里仍有 211 处硬编码颜色；陈述与代码不一致。
  - 怎么修：要么把颜色迁移到 themes/tokens，要么在文档标注实际例外清单。
- **[复核]** `D6-color-claim` `docs/a11y/2026-11-12-remaining-decisions.md:89` — 代价：与真实站址标记的颜色区分变弱。不改：白天示例站址基本看不见（夜里 3.73 尚可）。
  - 为什么：文档声称颜色不改，但 desktop 非主题文件里仍有 211 处硬编码颜色；陈述与代码不一致。
  - 怎么修：要么把颜色迁移到 themes/tokens，要么在文档标注实际例外清单。
- **[复核]** `D6-color-claim` `docs/pending-patches/2026-11-12-fz04-unfreeze-request.md:13` — **解冻理由**：三处都只改颜色值和边框，不动任何控件的位置和大小。批了以后界面骨架完全不变，只是字更清楚、焦点看得见。
  - 为什么：文档声称颜色不改，但 desktop 非主题文件里仍有 211 处硬编码颜色；陈述与代码不一致。
  - 怎么修：要么把颜色迁移到 themes/tokens，要么在文档标注实际例外清单。
- **[复核]** `D6-color-claim` `docs/pending-patches/2026-11-12-fz04-unfreeze-request.md:89` — > 第 3 条 (c) 的一个候选键值外，其余色值等批准后再出。批之前不改任何颜色；
  - 为什么：文档声称颜色不改，但 desktop 非主题文件里仍有 211 处硬编码颜色；陈述与代码不一致。
  - 怎么修：要么把颜色迁移到 themes/tokens，要么在文档标注实际例外清单。
- **[复核]** `D6-color-claim` `docs/research/2026-11-11-roadmap.md:475` — > 而函数 docstring（`:862-867`）称「只改 px/尺寸、**不改颜色**」——**文档与代码不符**。
  - 为什么：文档声称颜色不改，但 desktop 非主题文件里仍有 211 处硬编码颜色；陈述与代码不一致。
  - 怎么修：要么把颜色迁移到 themes/tokens，要么在文档标注实际例外清单。

## D7 任务分派与优先级（违规 0，豁免 0）

> 规范：下一笔做什么由规则算出来，不靠最后回消息的人。

- 事实：排序规则：loss×100 + freq×10 + evidence×3 − 冻结60 − 豁免1000
- 事实：优先级  336 A02 desktop/adsb_map_panel.py:237
- 事实：优先级  336 A03 desktop/adsb_panel.py:103
- 事实：优先级  336 A03 desktop/ai_panel.py:316
- 事实：优先级  336 A03 desktop/ai_panel.py:1320
- 事实：优先级  336 A03 desktop/ai_steps.py:136
- 事实：优先级  336 A03 desktop/ai_steps.py:149
- 事实：优先级  336 A02 desktop/bookmark_panel.py:303
- 事实：优先级  336 A02 desktop/bookmark_panel.py:342
- 事实：优先级  336 A02 desktop/control_panel.py:1207
- 事实：优先级  336 A02 desktop/control_panel.py:1249


## D8 豁免与债务登记（违规 1，豁免 0）

> 规范：所有「暂时不做」必须有理由、到期、批准人。

- 事实：公理豁免登记：3 条
- 事实：已知红 12 条，其中 8 条没写到期/复核条件

- **[复核]** `D8-debt-no-expiry` `docs/known-red-register.md:0` — 8 条已知红没有到期/复核条件：K01, K05, K05, K08, K09, K10, K11, K12
  - 为什么：债务没有到期就不会被重新审视，只增不减。
  - 怎么修：为每条补「复核日期」或「转绿条件」；转绿条件已有时可视为到期判据。
