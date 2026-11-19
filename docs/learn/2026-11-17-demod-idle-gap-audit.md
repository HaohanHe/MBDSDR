# 停止解调（idle）后仍在工作的路径审计（2026-11-17）

> **本文为当时状态快照**：记录当日工作状态与结论，未逐条复核；
> 不作现状依据。共 1 条未复核；需要现状请查 `docs/COORDINATION.md`、
> `docs/known-red-register.md` 与 checklist。

> 用户口径（SDR++ 式）：
> - 设备保持连接，频谱 / FFT / 瀑布继续跑；
> - **VFO / DDC / 解调 DSP / 音频写卡全部真正停止**，不再为解调消耗 CPU。
>
> 本文是**只读审计**：未改源码、未跑 pytest、未启动 GUI / 未连真机。
> 所有结论来自静态阅读；运行期望值均明确标注。
>
> 交付项索引：结论摘要→§1；数据流图→§3；逐条表格→§4；一条命令判据→§9；
> 已证实 / 推测→§10。

## 0. 已落地（2026-11-17，审计后）

- **RecordTap 未录制不再复制/回调**（原 §4 #2，`receive_pipeline.py:590-620`）：
  新增 `enabled` 回调；关时 `flush()` + `continue`，不跑 `np.array` 也不跨线程回调。
  `ReceivePipeline(record_enabled=...)` 透传，`main_window.py:9637-9638` 绑
  `lambda: self._recording`。
- 反证用例：`tests/test_listen_state_machine.py::test_record_tap_skips_copy_when_disabled`
  （关闭期回调必须 0 次且仍在排水；打开后必须恢复回调）。该文件 17 passed。
- **流水线"不点收听不解调"改成显式开关**（2026-11-17 补）：
  `ReceivePipeline.__init__(audio_enabled=False)` 把门控从"UI 事后同步"提前到构造
  参数——GUI 仍默认关（`main_window.py:9630` 不传，随后由 `_sync_listen_workers`
  按收听态回推），但非 GUI 消费者（`scripts/bench_latency_jitter.py`、
  `bench_dsp_throughput.py`、`soak_receive_chain.py`）必须显式
  `audio_enabled=True`。**这是真红修复**：改默认关之后
  `test_bench_latency_jitter` 音频样本从 235 → 0（实测 `waterfall count=235,
  audio count=0`），因为基准脚本没点收听。反证用例
  `test_listen_state_machine.py::test_pipeline_audio_gate_is_explicit_opt_in`
  同时钉住"默认关 + 显式开"。
- **未做（有意）**：§4 #4 回放 `_demod_and_play` 不查 `_listen_state`。回放是用户
  显式发起的"放录音"动作、且与实时流水线互斥（13549 先 `_stop_iq_streams`），
  按 idle 直接静音会被当成"回放坏了"。若要做，正确口径是进回放前先把收听态
  切到 listen 并在 UI 显示，而不是在解调入口静默 return。
- **复核修正（2026-11-17 晚）**：下面两条在审计定稿后已被另一条线改掉，本文
  §1.5 / §1.6 的"未做"口径按当前代码失效——
  - `AudioPlayer.available` 不再是永久粘性：`mbdsdr_ai/audio_out.py:207-214`
    在 `start()` 里重新 `sd_available()` 探测；
  - muted 不再跳过 AGC/tap：`desktop/receive_pipeline.py:413-427` 只掐
    `player.write`，`audio_tap` 照跑。
  留此记录是为了避免下一个人按旧结论重做一遍。
- **idle 旁路收口（2026-11-17 续，未提交）**：把 §1.2 里 idle 仍在工作的
  剩余三条按 SDR++ 口径停住，并补可观测计数——
  - 星座图 tap：`_on_pipeline_frame_ready` 只在该面板可见、收听/静音态、
    且距上次喂入 ≥0.1 s 时才调 `feed_iq`（旧实现 146 帧/s 无门控，
    idle 下星座点照跳，观感上就是"没停"）；
  - 回放：新增 `_pause_replay_for_idle()` / `_resume_replay_after_idle()`，
    `_apply_listen_state("idle")` 停 50 ms 回放定时器并记
    `_replay_paused_by_idle`，回收听/静音时从原位置续播；
  - splitter：`ReceivePipeline.set_demod_enabled(bool)` 在 idle 时把
    `_vfo1_stream` / `_vfo2_stream` 从 splitter 解绑（每帧 128 KiB 拷贝停掉），
    收听时重绑；`_sync_listen_workers` 末尾统一调用；
  - 计数：`DemodWorker.frames_demod` / `frames_skipped`，idle 下前者不增长、
    后者按帧增长——这是"停止解调真的停了"的机械证据；
  - 缓冲积压恢复文案改按 `_listen_state`（muted 不再被写成"待机"）。
  探针实测（`%TEMP%\kilo\idle_probe.py`，直接跑、不经 pytest）：
  `idle_unbound True spectrum_kept True`、`enabled False vfo_calls 0
  frames_demod 0 frames_skipped 5`、`enabled True vfo_calls 5 frames_demod 5`。


---

## 1. 结论摘要

1. **流水线里的主解调确实被 idle 门控住了。**
   `_sync_listen_workers(False)` → `DemodWorker.set_audio_enabled(False)`；worker 在
   `VFO.process()` 之前就 `flush()` + `continue`，不碰 DDC / 解调 / 声卡。
   证据：`desktop/receive_pipeline.py:486-488`、`desktop/main_window.py:4701,4720-4752`；
   已有机械断言 `tests/test_listen_state_machine.py:238-246`（`VFO.process` 必须 0 次）。
   另：下拉"再点一次同一档"的接线已补（`main_window.py:1914-1919`，注释直接引用
   2026-11-16 真机反馈），所以本次问题不在信号接线。

2. **但 idle ≠ 零工作。仍有 4 条与"解调/解码/录制"相关的路径没被收听态门控：**

   | 严重度 | 路径 | 位置 |
   | --- | --- | --- |
   | 高（常态、每帧） | `RecordTap` 未录制也整帧复制并回调 | `desktop/receive_pipeline.py:598-612`（复制在 606，回调在 609） |
   | 高（常态、每帧） | splitter 仍向 `_vfo1_stream` / `_record_stream` 复制 | `mbdsdr_ai/dsp.py:1291-1293`；`desktop/receive_pipeline.py:722-726` |
   | 中（常态、每帧） | 星座图 tap 无可见性/状态门控，逐帧 `abs + percentile + tolist` | `desktop/main_window.py:10032-10037`；`desktop/constellation_panel.py:682-698,308-344` |
   | 中（回放模式） | 回放 `_demod_and_play` 是第二个真解调入口，不查 `_listen_state` | `desktop/main_window.py:13608,10395-10397`；启动 13549-13577 |

3. **没有发现第二个/按 VFO 动态创建的 DemodWorker。** 生产代码只有
   `_demod1` / `_demod2` 两个实例（`receive_pipeline.py:696-700`），当前 `vfo2_dsp=None`
   （`main_window.py:9604`），`_vfo_workers` 只登记 `main_vfo_id → _demod1`
   （`main_window.py:9653-9655`）。"+ VFO" 只建 UI 对象、不建 worker
   （`main_window.py:9867-9886`）。也没有实时 FT8 / AIS / POCSAG 解码线程：
   `decode_router` 只被能力目录列举，桌面实时链路没有调用点（§4 #16）。
   **结构风险**：`_sync_listen_workers` 写死遍历
   `_demod1/_demod2`（4733），将来若加 `_demod3` 或动态 worker，会漏同步。

4. **流水线重建不会在 idle 下偷偷开解调。** 新 worker 初值已是
   `audio_enabled=False`（`receive_pipeline.py:697,699`），重建末尾又用
   `_listen_flags()` 统一回推（`main_window.py:9661-9662`）。
   注意 `main_window.py:9656-9657` 的注释仍写"worker 默认 audio_enabled=True"，**注释已过时**。

5. **反向 bug（想解调却不解调）：`AudioPlayer.available` 是粘性标志。**
   一次 `start()` 失败就把 `available=False` 永久钉死（`mbdsdr_ai/audio_out.py:225-229`），
   `stop()` 不会恢复（317-332）。此后即使用户点"▶ 收听"，`_player_ok()` 永远为
   False（`receive_pipeline.py:468-474`），状态栏却显示"接收中"
   （`main_window.py:4685`）。"🔇 不收听"分支还忽略 `start()` 的返回值
   （4690-4697），无声卡时"静音解调"实际不跑。

6. **"不收听"（muted）的解码承诺也没兑现。** `_push_audio` 在 AGC / `audio_tap`
   之前就 `return`（`receive_pipeline.py:415-416`），而 `audio_tap` 正是 APRS /
   卫星 APT 的音频入口（`main_window.py:10504-10562`）。UI 文案和
   `set_muted` docstring（441-447）都说"解码继续"，实际拿不到音频。

---

## 2. 三档语义 vs 实际

| 档位 | 设备 | 频谱/瀑布 | DDC+解调 | 声卡 | 代码事实 |
| --- | --- | --- | --- | --- | --- |
| ▶ 收听 | 连接 | 跑 | 跑 | 出声 | `_sync_listen_workers(True,False)`，4662-4687 |
| ⏸ 停止解调（idle） | 连接 | **跑（应该）** | **停** | 关 | 主解调停（486-488）；但 RecordTap/星座图/回放未停 |
| 🔇 不收听（muted） | 连接 | 跑 | 跑 | 静音 | 解调跑；但 `available=False` 或 player=None 时实际不跑（468-474） |

---

## 3. 真实数据流（ASCII，标出 idle 仍在跑的部分）

```
后端设备
  │  RTL-SDR: _ThreadedRingReader 持续读设备（sdr_backend.py:2212-2219）
  ▼
backend.read_samples() ────────────────┐
  │                                    │（桌面不调 backend.start_audio；
  │                                    │  headless _audio_loop 不启动：main_window.py:9517-9520）
  ▼
IqReaderThread（receive_pipeline.py:122-181）
  │  16384 complex64/块，≈146.5 块/s @2.4 MSPS
  ▼
_input PingPongStream（678）
  │
  ▼
StreamSplitter（679；mbdsdr_ai/dsp.py:1278-1300）  ← idle 仍在复制
  ├──► _spectrum_stream ──► FftWorker（195-236）──► frame_ready ──► UI
  │        └─ idle：必须继续（频谱/瀑布/iq_bus/ADS-B/星座图 tap）
  ├──► _vfo1_stream ──────► DemodWorker._demod1（696）
  │        └─ idle：读帧→flush→continue，DDC/解调不跑（486-488）   ✅
  │           但 splitter 仍在为这一路做一次 128 KiB 拷贝           ❌ 多余
  ├──► _vfo2_stream ──────► 不存在（vfo2_dsp=None，9604）           ✅
  └──► _record_stream ────► RecordTap（584-612）                     ← idle 仍在复制
           └─ 606 无条件 np.array 复制；609 无条件回调
              10096 才判 _recording（所以不写盘，但拷贝照做）        ❌ 多余

UI 线程 _on_pipeline_frame_ready（9983-10064）  ← 每帧都执行
  ├─ iq_bus.publish（10013-10018）               廉价，只存引用
  ├─ ADS-B 列表 feed_iq（10022-10029）           仅 dock 可见时
  ├─ 星座图 feed_iq（10032-10037）               ❌ 无门控，逐帧 numpy 重活
  ├─ VFO 列表刷新（10040-10046）                 10 帧节流
  ├─ RSSI（10048-10052）                         10 Hz 节流
  └─ 状态栏（10054-10064）                       10 帧节流

回放模式（与实时流水线互斥：13549 先 _stop_iq_streams）
  _replay_timer(50ms) → _replay_tick（13587-13625）
      → _demod_and_play（10381-10482）  ← ❌ 只查 available，不查 _listen_state
          → VFO.process / WFMReceiver.process → player.write
```

**关键结论（回答 Q4）**：频谱与解调**不共用 DDC**。FFT 只吃 `_spectrum_stream`
（`FftWorker` 不调 `VFO.process`），DDC 在 `DemodWorker` 内、只服务 `_vfo1_stream`。
所以 idle 停 DDC 不会停频谱——这正是 SDR++ 口径（`docs/learn/sdrpp-demod-lifecycle.md`）。
问题不在"共用链"，而在 splitter / RecordTap / 星座图 / 回放这四个**旁路**没有被
`_listen_state` 约束。

---

## 4. 逐条核对表

| # | 路径 | file:line | idle 时被门控？ | 证据 | 建议最小修法 |
| --- | --- | --- | --- | --- | --- |
| 1 | 主解调 `DemodWorker._demod1` | `receive_pipeline.py:696-697,462-490`；`main_window.py:4701,4720-4752` | **是** | `set_audio_enabled(False)`；`_player_ok()` False → `flush(); continue`（486-488），`VFO.process` 在 538 之后 | 无需改 |
| 2 | 第二解调 `_demod2` | `receive_pipeline.py:698-700`；`main_window.py:9604` | **是**（且当前不存在） | `vfo2_dsp=None`；即便创建，初值 `audio_enabled=False`（699） | 无需改 |
| 3 | 按 VFO 动态 worker | `main_window.py:9867-9886,9653-9655` | 不存在 | "+ VFO" 只 `mgr.add()` + 推 offset；`_vfo_workers` 只有 main→`_demod1` | 若将来做动态 VFO，`_sync_listen_workers` 应遍历 `_vfo_workers.values()` 而不是写死 `_demod1/_demod2` |
| 4 | `RecordTap`（录制 tap） | `receive_pipeline.py:598-612`（606/609）；`705,726,732` | **否** | 类 docstring 自认"录制开关在 UI 侧判定"（585）；每帧 `np.array(buf[:n])` 后无条件下发（606-609）；`_recording` 只在 UI 侧 10096 判 | 给 `RecordTap` 加 `set_enabled(bool)`（或构造注入 `should_capture`）；未录制时 `read()+flush()+continue`，跳过复制与回调 |
| 5 | splitter 向 vfo1/record 的复制 | `mbdsdr_ai/dsp.py:1291-1293`；`receive_pipeline.py:722-726` | **否** | 每个 bound 输出都执行 `out.write_buf[:n] = buf[:n]` | idle 时 `unbind(_vfo1_stream)` / 未录制时 unbind `_record_stream`；恢复收听前重新 bind（注意与 stop/start 的锁时序）。至少先修 #4 |
| 6 | 星座图 tap | `main_window.py:10032-10037`；`constellation_panel.py:682-698,308-344` | **否** | 只判对象存在；`_connected` 在连接时置 True（`main_window.py:7486-7488`，`constellation_panel.py:573-574`）；`view.feed` 每帧 abs/percentile/tolist（308-344），`_points.extend` 在 338；代码注释自认"面板在别的页签也照跑"（333-334）。`main_window.py:10031` 注释说"节流 ~10fps"但无节流 | 加 `constellation_dock.isVisible()` 门控 + 10 Hz 节流；或按产品口径在 idle 停喂 |
| 7 | ADS-B 列表 tap | `main_window.py:10022-10029` | **仅可见性** | `adsb_dock.isVisible()` 才 `feed_iq` | 若产品口径是"停解调=停所有解调器"，再加 `_listen_state != "idle"`；按 SDR++ 模块语义可保留 |
| 8 | ADS-B 地图（iq_bus 订阅） | `adsb_map_panel.py:167-182,218-263`；`iq_bus.py:50-69` | **仅可见性+频段** | `showEvent` 启 200 ms timer / `hideEvent` 停（175-181）；中心不在 1090±1.5 MHz 或 sr<1.2 MSPS 直接跳过（249-256） | 同上 |
| 9 | `iq_bus.publish` | `main_window.py:10013-10018`；`iq_bus.py:50-69` | 否，但廉价 | 只存最新一帧引用 + 一把锁，不拷贝 | 无需改 |
| 10 | 回放解调 `_demod_and_play` | `main_window.py:13608,13587-13610,10395-10397`；启动 13549-13577 | **否** | 回放先 `_stop_iq_streams()`（13549）再起 50 ms timer；`_demod_and_play` 只查 `player.available`；`_apply_listen_state` 不碰 `_replay_timer` | idle 分支（4700-4709）暂停 `_replay_timer`（注意别直接调 `_stop_replay()`：它会在 13648-13649 重建实时管线）；`_demod_and_play` 增加 `_listen_state == "idle"` 早退 |
| 11 | 旧 UI 轮询 `_poll_sdr_iq → _demod_and_play` | `main_window.py:1334-1336,10178-10185,10250` | **实际不跑（死路径）** | 定时器只 `connect` 不 `start`；测试 `test_listen_state_machine.py:381` 断言 `isActive() is False` | 建议删除/标注，避免以后误 start |
| 12 | 后端 headless 音频线程 | `sdr_backend.py:1142-1170,1087-1140`；`main_window.py:9517-9520` | **桌面不启动** | 桌面端不调用 `backend.start_audio()`，注释明说"两套同时启动会三路解调" | 无需改（MCP/CLI 是另一进程/另一部署形态） |
| 13 | IQ 前端校正链 | `main_window.py:9553-9566` | 否（频谱需要） | reader 线程逐块 DC/IQ 平衡/抗混叠 | 属频谱必要成本，保留 |
| 14 | 扫频 / 调制识别 / 文件解码 | `main_window.py:740-820`；`modulation_panel.py:60-90`；`scanner_panel.py:70-141`；`weather_panel.py:207-237` | 非后台常驻 | 均为按钮触发的一次性 QThread 或文件解码 | 无需改；注意不要与流水线同时 `read_samples` 抢环形缓冲 |
| 15 | 音频 tap（APRS/卫星 APT） | `receive_pipeline.py:413-421`；`main_window.py:10504-10562` | idle：停（随解调停）；**muted：也停** | `_push_audio` 在 muted 时先 `return`（415-416），AGC/tap 都没跑 | muted 分支把 `return` 移到 AGC/tap 之后；静音交给 `player.set_muted(True)` |
| 16 | FT8 / AIS / POCSAG 等 decoder router | `mbdsdr_ai/decode_router.py:122,189,301`；`desktop/mode_registry.py:156-176`；`receive_pipeline.py:502-503` | 不适用（桌面无实时解码线程） | `decode_router` 在 `desktop/` 里只被 `capability_catalog.py` 列举能力，实时链路没有调用点；DIG 模式 `outputs_audio=False`，`DemodWorker` 在 502-503 直接 `continue` | 无需改；将来接实时解码器时要单独定义它是否随 idle 停 |

---

## 5. 回放路径补充（第二个真解调入口）

- `_replay_tick` 每 50 ms 调 `_demod_and_play`（`main_window.py:13608`），该函数会执行
  `VFO.process`（10463）或 WFM 鉴频（10453），再 `player.write`（10441）。
- 唯一门控是 `player is None or not player.available`（10395-10397），**与
  `_listen_state` 无关**。
- 回放启动时 `_stop_iq_streams()`（13549）已把实时流水线停掉并 `player.stop()`；
  随后重新 `player.start()`（13555）。此时用户在工具栏选"⏸ 停止解调"：
  `_apply_listen_state("idle")` 只停 worker / `player.stop()`（4701-4706），
  **不会停 `_replay_timer`**。于是 worker 无了、声卡流关了，但 50 ms 一次的
  VFO/DDC/解调仍在跑，`player.write()` 返回 0（`audio_out.py:250-251`）。
- 这是"idle 时仍在做解调 DSP"的一条真实路径（仅限回放模式）。声卡已停，
  所以用户听不到，但 CPU 仍在消耗。

---

## 6. 流水线重建路径（回答 Q2）

全仓没有 `_create_pipeline` / `_setup_pipeline` 命名；桌面端唯一重建入口是
`_build_pipeline`（`main_window.py:9542`），由 `_start_iq_streams`（9498-9522）
在连接 / 换采样率 / 换设备 / 回放结束时调用。

| 触发 | 代码 | 新 worker 初值 | 是否回推收听态 |
| --- | --- | --- | --- |
| 连接成功 | `_start_iq_streams`（9498-9522）→ `_build_pipeline` | `audio_enabled=False`（697/699） | 是，9661-9662 |
| 换采样率 / 换设备 | 同样走 `_build_pipeline`（9567-9574 先 shutdown 旧管线） | 同上 | 是 |
| 回放结束恢复 | `_stop_replay`（13648-13649）→ `_start_iq_streams` | 同上 | 是 |
| 断连 | `_teardown_connection` 复位 `_listen_state="idle"`（7644-7645） | — | 下次重建按 idle |

结论：重建**不会**默认 True 重新解调。`receive_pipeline.py:693-695` 的注释
已经把这个坑写明："旧实现 `_demod1` 默认 True，一旦上面的同步没跑到（或异步
顺序变化）就会'没点收听却在解调'，这正是真机上'点了停止还在解调'的温床"。
两个注意点：
1. `receive_pipeline.py:265-268` 的 docstring 说 idle 时"直接 msleep 让路"，
   实际是阻塞在 `_in.read()`，每帧醒一次做 `read/flush`（≈146 次/s）。不是 DSP，
   但不是零唤醒；文档漂移。
2. `_start_iq_streams` 只在 `_listening` 时开声卡（9508-9516）；`muted` 态重建
   不会启动 OutputStream，但仍会解调，`write()` 返回 0。行为与"静音"接近，
   但与"静音由 player 执行"的语义不一致。

---

## 7. `_player_ok()` 与声卡可用性（回答 Q3）

```python
def _player_ok() -> bool:
    return bool(player is not None
                and getattr(player, "available", False)
                and self._audio_enabled)
```

- **idle**：`_audio_enabled=False` → False（正常，DDC 不跑）。`player` 正常不为 None
  （`main_window.py:1172-1176`），只有 AudioPlayer import 失败才 None。
- **listen / muted 的正确性隐患（已证实为代码事实）**：
  1. `available` 是"sounddevice 能 import"的粘性标志（`audio_out.py:118`）；
     `start()` 打开设备失败会把它置 False（225-229），`stop()` 不恢复（317-332）。
     一次瞬时失败后，后续所有"收听"都拿不到解调数据，状态栏却写"接收中"（4685）。
  2. muted 分支 `player.start()` 的返回值被忽略（4690-4697）；若失败，
     `_player_ok()` False → "静音解调"实际不解调，与文案（4698-4699）相反。
  3. player 为 None（AudioPlayer 依赖不可用）时，listen 分支不会早退
     （4662 的条件是 `player is not None`），仍置 `_listen_state="listen"` 和
     "接收中"（4675,4685），但 worker 门控要求 player 非 None（472-474）→ 假状态。
- 建议：`available` 与"本次 start 是否成功"分离；`start()` 失败后允许重试
  （不要把能力位永久钉死），并让 `_player_ok()` 只表达"是否应该解调"，
  "能不能写卡"由 `write()` 自己返回 0 处理。这样 muted 态才不会因声卡异常而不解调。

---

## 8. UI 可观测性（回答 Q5）

现状（idle 时用户能看到的）：

| 位置 | 内容 | 持续性 |
| --- | --- | --- |
| 工具栏下拉 `listen_combo` | 显示"⏸ 停止解调"（1908-1912） | 常驻 |
| 底部 Dock 右侧 `status_processing` | "待机"（4475 初始，4707 设置，5346-5351） | 常驻，但语义模糊（"待机"也可理解为没设备） |
| 状态栏消息 | "已停止解调：保持连接，解调线程让出 CPU，频谱仍在工作"（4708-4709） | **4 秒后消失**（`MSG_NORMAL=4000`，第 46 行） |

**没有任何每秒解调帧数 / DDC 调用计数 / DSP 状态指示。**
`receive_pipeline.py` 里也没有 `frames_*` / `fps` 计数器（全文 grep 无匹配）。
并且 `_warn_buffer_overflow()` 会把状态改成"缓冲积压（丢样）"8 秒
（9821-9847），恢复时按 `_listening` 判断（9840-9841）——muted 态会被错误恢复成
"待机"，进一步误导。

**最合适的落点与最小改法（不实施）：**

1. 在 `DemodWorker.run()`：
   - `486-488` 的短路分支 `self.frames_skipped += 1`；
   - 正常解调帧末尾 `self.frames_demod += 1`。
   两个计数器都是普通 int，GIL 下读写足够（仅用于展示/断言）。
2. 在 `main_window._on_pipeline_frame_ready()` 已有的 10 帧节流块
   （`10054-10064`）读取 `pipe._demod1.frames_demod/frames_skipped`，把
   `status_processing` 写成"待机 · 解调 0 帧/s"（或 muted 时"静音解调 N 帧/s"）。
   这样用户看到的不是"待机"两个字，而是可反驳的 0。
3. 顺手把 `_warn_buffer_overflow` 的恢复文案改为按 `_listen_state`
   （`9840-9841`），避免 muted 被显示成"待机"。

---

## 9. 如何用命令证明（可执行判据，均未在本审计中运行）

### 9.1 已有判据：idle 时 DDC 真正不跑

```powershell
python -m pytest tests/test_listen_state_machine.py::test_idle_skips_ddc_and_demod_inside_worker -q
```

判据：`audio_enabled=False → VFO.process 调用 0 次`，`audio_enabled=True → > 0`
（`tests/test_listen_state_machine.py:238-246`）。这是目前唯一机械证明。

### 9.2 证明 RecordTap 在 idle 仍在复制/回调（预期当前会"失败"）

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -c "import sys,time; sys.path[:0]=['.','desktop','tests']; import test_receive_pipeline_smoke as t; from PySide6.QtWidgets import QApplication; app=QApplication([]); p,g,v1,v2,rec=t._build(app); p.start(); time.sleep(0.6); p.shutdown(); p.wait_for_shutdown(4000); print('VFO.process=',v1.calls,'record_callbacks=',len(rec))"
```

预期输出（当前代码）：`VFO.process= 0 record_callbacks= 80+`
——**解调停了，但录制 tap 每帧仍在复制并回调**。
修复后应为 `record_callbacks= 0`。

### 9.3 证明 `available` 一旦失败就永久失败（预期 `False False`）

```powershell
python -c "from mbdsdr_ai.audio_out import AudioPlayer; p=AudioPlayer(); p.available=False; print(p.start(), p.available)"
```

预期：`False False`，且没有任何路径能把 `available` 再置回 True
（`audio_out.py:207-208,225-229,317-332`）。

### 9.4 星座图（静态已证实，建议补一条测试）

`_on_pipeline_frame_ready(iq, sr)` 在 idle 下执行后，
`win.constellation_panel.view._points` 仍会增长（`main_window.py:10032-10037`，
`constellation_panel.py:682-698,338`）。
建议补断言的落点：`tests/test_listen_state_machine.py` 中新建一例，先
`_apply_listen_state("idle")`，再调用两次 `_on_pipeline_frame_ready`，
断言 `len(view._points)` 不增长（修复后）。

---

## 10. 已证实 vs 推测

### 已证实（静态代码 / 现有测试）

- 主解调在 idle 被门控，`VFO.process` 0 次：`receive_pipeline.py:486-488`，
  `test_listen_state_machine.py:238-246`。
- 只有 2 个生产 `DemodWorker` 实例、1 个 `RecordTap`：`receive_pipeline.py:696-700,705`；
  全仓 `DemodWorker(` 无其他生产调用点。
- 重建初值 False + 末尾同步：`receive_pipeline.py:697,699`；`main_window.py:9661-9662`。
- RecordTap 无条件下发：`receive_pipeline.py:606-609`；UI 侧才判 `_recording`：10096。
- splitter 无条件复制到所有 bound 输出：`dsp.py:1291-1293`。
- 星座图 tap 无状态/可见性门控：`main_window.py:10032-10037`；
  `constellation_panel.py:689` 只判 `_connected`。
- 回放是第二个解调入口且不查 `_listen_state`：`main_window.py:13608,10395-10397`。
- `available` 粘性、`stop()` 不恢复：`audio_out.py:118,225-229,317-332`。
- muted 时 `_push_audio` 跳过 AGC/tap：`receive_pipeline.py:415-420`。
- 旧 `_poll_sdr_iq` 路径不启动：`main_window.py:1334-1336`；
  `test_listen_state_machine.py:381`。
- idle 无帧计数器：`receive_pipeline.py` 全文无 `frames/fps/counter` 匹配。

### 推测 / 需运行时确认

- 上述多余拷贝与星座图计算在用户机器上的实际 CPU 占比（本文只做数量级估算：
  16384 complex64 = 128 KiB/块，2.4 MSPS 下 ≈146 块/s；splitter 3 路 + FFT 1 份
  + RecordTap 1 份 ≈5×128 KiB/块，其中 idle 多余约 3×128 KiB/块 ≈ 55–60 MB/s
  memcpy；星座图约 146 次/s 的 512 点 `abs+percentile+tolist`）。
- `AudioPlayer.start()` 失败是否就是用户现场的那一次（代码事实确定，触发条件未观测）。
- ADS-B 解码在 idle 继续是否符合产品口径（SDR++ 的 decoder module 与 VFO 解耦，
  按该语义属"独立模块"，不算 demod 泄漏；需产品确认）。
- 动态 VFO worker 是结构风险而非当前 bug（当前 +VFO 不建 worker）。
- 用户所说的"仍然在跑这个频谱"是否其实指"频谱继续"本身；按 SDR++ 口径，
  频谱继续是正确行为，本文按该口径审计。

---

## 11. 附：顺手发现的注释/文档漂移（不影响结论）

| 位置 | 现状 | 事实 |
| --- | --- | --- |
| `receive_pipeline.py:265-268` | idle 时"直接 msleep 让路" | 实际阻塞在 `_in.read()`，每帧醒一次 read/flush |
| `receive_pipeline.py:441-447` | `set_muted` docstring"解调/AGC/tap 照跑" | `_push_audio` 在 muted 时先 return，AGC/tap 不跑（415-416） |
| `main_window.py:9656-9657` | "worker 默认 audio_enabled=True" | 已是 False（`receive_pipeline.py:697,699`） |
| `main_window.py:10031` | 星座图 tap"节流 ~10fps" | 实际无节流，每帧都喂（10032-10037） |
| `main_window.py:9870-9873` | "+ VFO…尝试绑 DSP 通道" | 实际只 `mgr.add()` + 推 offset，不建/不绑 worker |
