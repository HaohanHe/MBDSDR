# 2026-11-11 P0 静态推断实测核查（P0-03 / P0-04 / P0-10 / P0-11）

> 任务：把 roadmap §1 里 4 条「**仅静态推断，未实测**」的 P0 变成实测结论。
> 性质：**只新增文件**。本文与 `scripts/repro/` 下 4 个脚本之外，未改动任何产品代码、
> 未改任何阈值或基线。
> 结论一句话：**4 条全部「未复现」——静态判断已不成立，缺陷在 HEAD 里已被别的线修掉**；
> 但 P0-04 的实测顺带抓到一个**新**的假成功形态（噪声 WAV 被报成 `decoded`）。

---

## 0. 口径与环境

| 项 | 实测值 |
|---|---|
| 仓库 HEAD（最终采集时） | `b9d10690` |
| Python | 3.14.0 |
| 平台 | Windows / PowerShell 5，`QT_QPA_PLATFORM=offscreen`（仅 P0-10 面板项、P0-11） |
| 硬件 | **无 SDR、无触控板**；4 个脚本都不需要硬件 |
| 工作区状态 | 采集时 `desktop/status_panel.py`（1 行）、`mbdsdr_ai/sdr_backend.py`（+78 行，他线 P1-30 回读）为未提交改动；已核对 `git diff` **不含** `offset` 相关增删，故 P0-10 读数等价于 HEAD |
| 用户真实配置 | 4 个脚本只读产品代码；P0-11 把 `HOME/USERPROFILE/APPDATA/LOCALAPPDATA` 全部重定向到 `D:\mbdsdr\output\tmp\p0-11-profile-*`，结束即删 |

退出码约定（4 个脚本一致）：

| 码 | 含义 |
|---|---|
| 0 | **复现成功**——脚本观测到缺陷行为 |
| 1 | **未复现**——当前行为已符合 roadmap 的「完成判据」 |
| 2 | **无法判定**——环境不满足（import/Qt/构造失败），或**反证对照组失效**（脚本抓不到旧行为，不可用于判定） |

每个脚本内部都自带**反证对照组**：在本进程内把关键修复点临时改回旧语义，同一批输入必须
重新出现缺陷行为。没有这一步，「未复现」只能说明脚本没测到，不能说明缺陷不存在。

原始输出留存：`D:\mbdsdr\output\tmp\p0-out\*.txt`（临时目录，不入库；本文已逐条摘录关键行）。

复跑方式：

```
python scripts/repro/2026-11-11-p0-03-decoder-streamability.py
python scripts/repro/2026-11-11-p0-04-empty-frame-evidence.py
python scripts/repro/2026-11-11-p0-10-offset-fake-effective.py
python scripts/repro/2026-11-11-p0-11-device-attr.py
```

---

## 1. 总表

| 条目 | roadmap 原证据等级 | 本次实测退出码 | 结论 | 已修提交（HEAD 内） | 仓内回归测试 |
|---|---|---|---|---|---|
| P0-03 `start_decoder` 8 类静默空跑 | 仅静态推断 | **1（未复现）** | 静态判断不成立 | `2161ac27` | `tests/test_decoder_streamability.py`、`test_start_decoder_capability_honesty.py`、`test_mcp_decoder_registry.py` |
| P0-04 WAV 0 帧当成功 | 仅静态推断 | **1（未复现）** | 原断言不成立；**另发现同族假成功**（见 §3） | `69da65a8`、`07681f40`、`68eb37db` | `tests/test_decode_router_honesty.py` |
| P0-10 Offset Tuning 假生效 | 仅静态推断 | **1（未复现）** | 静态判断不成立；**roadmap 路径写错**（见 §2.3） | `0aaa6afe` | `tests/test_offset_truthfulness.py` |
| P0-11 `.device` AttributeError | 仅静态推断 | **1（未复现）** | 静态判断不成立 | `9dbf3f38` | `tests/test_device_name_fallback.py` |

> 「已修提交」均用 `git log -- <file>` + `git show --stat <sha>` 核对到对应文件，不是只读提交标题。

---

## 2. 逐条

### 2.1 P0-03：`start_decoder` 对 8 类文件级解码器静默空跑，返回 `ok=True` 恒 0 条

**原断言（roadmap 原文）**：`start_decoder` 对 8 类解码器静默空跑，返回 `ok=True` 恒 0 条。
证据等级「仅静态推断，未实测」，指向 `mbdsdr_ai/core/controller.py:46-47`、`:971-990`。

**脚本**：`scripts/repro/2026-11-11-p0-03-decoder-streamability.py`
（不连硬件、不连后端，直接调 `SDRController.start_decoder`）

**实测输出（原样摘录）**：

```
模块常量: STREAMABLE_DECODERS=('adsb', 'rds')
          FILE_ONLY_DECODERS=('aprs', 'noaa_apt', 'meteor', 'sstv', 'ft8', 'wspr', 'pocsag', 'acars')

实测 A：8 类文件级解码器调用 start_decoder（无硬件、未连接）
  aprs       -> 拒绝：DecoderNotStreamableError（期望）
  noaa_apt   -> 拒绝：DecoderNotStreamableError（期望）
  meteor     -> 拒绝：DecoderNotStreamableError（期望）
  sstv       -> 拒绝：DecoderNotStreamableError（期望）
  ft8        -> 拒绝：DecoderNotStreamableError（期望）
  wspr       -> 拒绝：DecoderNotStreamableError（期望）
  pocsag     -> 拒绝：DecoderNotStreamableError（期望）
  acars      -> 拒绝：DecoderNotStreamableError（期望）

实测 B：可流式解码器（adsb）必须仍返回句柄
  adsb       -> 返回句柄 dec-0001（期望：可流式）
  rds        -> 返回句柄 dec-0002（期望：可流式）

反证对照组：STREAMABLE_DECODERS 改回「全部可流式」后重跑 aprs
  对照组返回句柄 dec-0001，0.5s 后消息数=0（旧行为已复现：不报错、无产出）

结论：当前实现复现 P0-03 : False；反证对照组可复现旧行为: True
[未复现] 8 类文件级解码器全部被显式拒绝，不存在「返回 ok 恒 0 条」。
```

**判定**：**未复现，静态判断不成立**。

- `start_decoder` 现在按 `STREAMABLE_DECODERS=('adsb','rds')` 与
  `FILE_ONLY_DECODERS`（8 类）分流，文件级一律抛 `DecoderNotStreamableError` 并给出
  `--decode-file --mode <x>` 的指路，不再返回句柄。
- 可流式两类仍正常返回句柄（没有被一刀切拦掉）。
- 修复提交：`2161ac27 fix(decoder): split streamable vs file-only decoder declarations`；
  采集时 `mbdsdr_ai/core/controller.py` 工作区干净，读数即 HEAD。

**为什么原静态判断会错**：旧报告的调用链停在 `_resolve_decode_fn()` 返回 `None` →
「不崩、队列空」，但没有继续读到 `start_decoder` 前置的 `dt not in STREAMABLE_DECODERS`
分支——那个分支在静态快照里可能被前置的 `SUPPORTED_DECODERS` 判断掩盖了。

**未覆盖**：`mbdsdr_ai/core/mcp_server.py:917-948` 的 MCP 侧注册表没有在本次脚本里跑
（采集时该文件有他线未提交改动）。controller 是唯一事实源，MCP 侧由
`tests/test_mcp_decoder_registry.py` 覆盖，本文不代替它。

---

### 2.2 P0-04：WAV 解 0 帧被当成成功，无法区分没信号／前导不对／算法失败

**原断言（roadmap 原文）**：WAV 解 0 帧被当成成功，无法区分三态。
证据等级「仅静态推断，未实测」，指向 `mbdsdr_ai/decode_router.py:1031-1045`。

**脚本**：`scripts/repro/2026-11-11-p0-04-empty-frame-evidence.py`
（现场生成 1 s / 48 kHz / PCM16 单声道 WAV，走 `run_digital_mode(..., mode='acars')`；
ACARS 依赖纯 Python，不需要外部工具）

**实测输出（原样摘录）**：

```
实测 A（原断言）：静音 WAV —— 0 帧是否被当成成功
  请求：解一段 1s 全零 PCM16 WAV（无任何信号）
  实际：ok=False status='empty' error_code='nothing_detected' num_messages=0 preamble_hits=0 crc_ok=0
  期望：ok=False, status='empty', error_code='nothing_detected'
  原断言复现=False；符合三态=True

反证对照组：_finalize_honest_status 置空（=旧实现不做收敛）
  静音：ok=True status=None error_code='' num_messages=0 ...
  对照组复现「ok=True 且 0 条」: True

结论：原断言（0 帧→成功）复现 : False
[未复现] 静音 WAV 已显式失败并带 nothing_detected；原断言「0 帧当成成功」不成立。
```

**判定**：**未复现，原断言不成立**。

- 0 帧 + 无能量 → `ok=False / status='empty' / error_code='nothing_detected'`；
  三态码常量在模块里齐全：`nothing_detected`、`preamble_not_found`、
  `signal_present_no_frame`。
- 收敛点在 `decode_router._finalize_honest_status()`（`decode_router.py:904-957`），
  由 `run_digital_mode()` 在 `:1140` 调用；把它换成空操作，静音输入立刻回到
  「`ok=True` + 0 条」的旧形态——证明脚本抓得住旧行为。
- 修复提交：`69da65a8`（分类空结果 + WAV 能量证据）、`07681f40`（per-mode 失败码）、
  `68eb37db`（`signal_present_no_frame` 端到端注册）。

**注意**：原断言不成立**不等于**这条没别的问题——同一次实测抓到一个新的假成功形态，
见 §3。它不属于「0 帧」形态，因此不改变本条判定。

---

### 2.3 P0-10：Offset Tuning 假生效（勾选＋状态栏双确认，实际未下发）

**原断言（roadmap 原文）**：Offset Tuning 假生效。
证据等级「仅静态推断，未实测」，roadmap 写作 `desktop/sdr_backend.py:2551-2558`。

**先纠一个事实错误**：仓库里**没有** `desktop/sdr_backend.py`
（`Test-Path D:\mbdsdr\MBDSDR\desktop\sdr_backend.py` → `False`），
实际文件是 `mbdsdr_ai/sdr_backend.py`。roadmap 这一行的 `path:line` 是错的，
这也是「静态推断」阶段没有真去跑的一个旁证。

**脚本**：`scripts/repro/2026-11-11-p0-10-offset-fake-effective.py`
（`object.__new__(RTLSDRBackend)` 绕过硬件初始化，只注入 `_sdr` 驱动桩与
`_offset_tuning`，调真实产品方法）

**实测输出（原样摘录）**：

```
实测 A：驱动没有 set_offset_tuning（本机 pyrtlsdr 实际情况）
  请求：set_offset_tuning(True)
  实际：supports=False 返回值=False 状态=False 驱动被调用=False
  期望：supports=False 返回值=False 状态=False

实测 B：驱动有该方法且成功（正例，不能被一刀切拦掉）
  实际：supports=True 返回值=True 状态=True 驱动收到=[True]

实测 C：能调用但下发失败（USB 掉线），状态不得改成新值
  请求：先 True（成功）→ 再 False（抛 OSError）
  实际：首次=True 第二次返回值=False 状态=True

反证对照组：重放修复前实现（先写状态、探测不到就 return True）
  实际：返回值=True 状态=True 驱动有方法=False
  对照组复现「假生效」: True

面板读数：StatusPanel.rx_offset = '关'（期望「关」）

结论：当前实现复现 P0-10 : False（命中=无）
[未复现] 不支持/下发失败都不写状态；面板读数为「关」。
```

**判定**：**未复现，静态判断不成立**。

- `RTLSDRBackend.set_offset_tuning()` 现在先走 `supports_offset_tuning()`，
  驱动没有该方法时返回 `False` 且**不写** `_offset_tuning`；新增只读属性
  `offset_tuning` 供状态面板取值，面板显示「关」。
- 下发抛错时也保持上一次真正生效值，不把被拒的新值写进状态。
- 连接流程按能力探测跳过（`main_window.py` 的 `_apply_connect_params` 段），
  不再把「不支持」记成「未生效」。
- 修复提交：`0aaa6afe fix(offset): 先判驱动能力再写状态，不支持不再冒充已开启`，
  同提交带 `tests/test_offset_truthfulness.py`。

**未覆盖**：真机 0 Hz 直流尖峰的实际搬移效果没有在本脚本里验证（无硬件）。
本条断言的是「界面/状态谎报」，不是「偏移算法对不对」。

---

### 2.4 P0-11：BladeRF/Lime 连接成功路径 `.device` AttributeError，半链路中断无提示

**原断言（roadmap 原文）**：BladeRF/Lime 连接成功路径 `.device` AttributeError，
半链路中断无提示。证据等级「仅静态推断，未实测」，指向
`desktop/main_window.py:6105`（另 `6183`/`3292`/`9555`）。

**脚本**：`scripts/repro/2026-11-11-p0-11-device-attr.py`
（offscreen Qt + 真 `MainWindow` + 无 `device` 属性的后端桩）

**实测输出（原样摘录）**：

```
后端桩 hasattr(backend, 'device') = False
请求：MainWindow._connect_backend(backend)
实际：返回值=True 异常=无
      _apply_connect_params 执行 1 次，_backfill_real_rf_readings 执行 1 次
      设备槽文本='状态: _StubBackendNoDevice（设备名称不可读）'
期望：返回值=True，两步各 1 次，槽内出现「设备名称不可读」

反证对照组：把取名字换回「直取 backend.device.name」
  实际：AttributeError: '_StubBackendNoDevice' object has no attribute 'device'（旧行为已复现）

结论：当前实现复现 P0-11（半链路）: False
[未复现] 无 device 的后端仍完整走完连接后半段，并明确提示名称不可读。
```

**判定**：**未复现，静态判断不成立**。

- `MainWindow._resolve_device_display_name()`（`main_window.py:5996`）用
  `getattr(backend, "device", None)` 受保护取值，拿不到就回退后端类名，
  并返回「名称不可读」标志；调用点把该标志写进设备槽与状态栏。
- 增益范围收放 / 参数下发 / 读数回填三个后续步骤各执行 1 次，没有「半个连接」。
- 修复提交：`9dbf3f38 fix(connect): 设备名不可读不得打断连接成功槽`，
  同提交带 `tests/test_device_name_fallback.py`（三个用例：无 name、无 device、
  有真名）。
- 同文件 `:3292`、`:6212` 两处残留的 `.device` 直取都在 `try/except` 内，
  不会把槽打断；本次实测走的是连接成功槽，未单独触发这两处。

**局限**：这次采集时 `desktop/main_window.py` 已在 HEAD 内（工作区干净），
读数就是 HEAD 行为；真机 BladeRF/LimeSDR 未接（也不需要——断言的是 Python 侧
属性访问，不是设备驱动行为）。

---

## 3. 新发现（超出 4 条原断言，建议另立条目）

**ACARS 对纯噪声输入返回 `ok=True / status='decoded'`，并给出乱码报文。**

同一次 P0-04 实测，5 个固定种子的 1 s 均匀噪声 WAV（RMS≈0.115）全部命中：

```
seed=20261111  ok=True status='decoded' error_code='' num_messages=1 preamble_hits=0 crc_ok=0
  首条报文（乱码）："].^Suc(UY'Kvv).|lM*k.9fu.4.sL,i%..UE.V.OR.+9..PnX "
seed=1         ok=True status='decoded' ... num_messages=1 preamble_hits=0 crc_ok=0
seed=2         ok=True status='decoded' ... num_messages=1 preamble_hits=0 crc_ok=0
seed=3         ok=True status='decoded' ... num_messages=2 preamble_hits=0 crc_ok=0
seed=4         ok=True status='decoded' ... num_messages=1 preamble_hits=0 crc_ok=0
命中「无前导、0 条 CRC 通过、仍报 decoded」：5/5
```

判据：`ok=True` 且 `num_messages>0` 且 `crc_ok_count==0` 且 `preamble_hits==0`。

为什么这不是 P0-04 原断言：原断言是「0 帧 → 成功」；这里解出了 1~2 条**帧**，
只是没有一条过 CRC、也没有任何前导命中。它属于「0 条可信帧 → 成功」，
两种形态不同，需要单独立条（建议 P0/P1 级：ACARS 面板会把乱码当真实报文展示）。

建议的完成判据（供派工参考，本文不改 roadmap 条目）：`num_messages>0` 但
`crc_ok_count==0` 且 `preamble_hits==0` 时不得报 `status=decoded`，
至少降级为 `crc_failed` / `signal_present_no_frame`，或把「CRC 未通过」作为
报文可见属性；反证：把降级判据删掉 → 噪声用例必须重新变绿。

**状态**：本项**仅实测取证，未修**（任务限定只新增文件、不改产品代码）。

---

## 4. 本文没做的事

- 没有改任何产品代码、阈值、基线；没有改 `docs/COORDINATION.md` 的占用表。
- 没有跑全量 pytest；4 个脚本都是独立进程、不依赖 pytest 顺序。
- 没有把「4 条都已修」当成「这 4 条可以销账」——roadmap 的 §7 里
  `W62`（P0-03）、`W63`（P0-04）、`W73`（P0-10）的状态列仍写「即办」，
  是否需要改状态由 roadmap 维护方决定（本次只回填 §1 那 4 行）。
- P0-11 没有 W 号工单（roadmap 把它归在 `main_window.py` 串行链之外）。

## 5. 变更记录

| 日期 | 内容 |
|---|---|
| 2026-11-11 | 新建本文；新增 `scripts/repro/` 下 4 个复现脚本；4 条 P0 全部测得「未复现」；回填 roadmap §1 对应 4 行证据单元格 |
