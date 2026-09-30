# SatNOGS Client 深度研究笔记 — 地面站观测自动化

> 仓库：`repos/satnogs-client`（AGPLv3，SatNOGS/Libre Space Foundation）
> 本地 checkout 说明：仓库内 `master` 仅剩「Move to GitLab」占位提交（`3be44b4`），真正源码已迁至 GitLab。
> 本笔记源码取自上游 GitLab `master`（commit `75836917`，2026-09 拉取），通过 `git fetch gitlab master:satnogs-upstream` 只读获取，未改动工作区任何文件、未写代码。
> 笔记日期：2026-09-30
> 目的：研究 satnogs-client 如何做「过境调度 → 接收/解码 → 产物上传」的闭环，对照 MBDSDR 现有
> 「卫星过境预测 + 一键捕获 + 多普勒补偿」（`cpp/src/ui/main_window.cpp` 的 `skyTimeSlider_` /
> `onCapturePassClicked` / `dopplerLimiter_`），提炼可直接落地的本地观测自动化方案。
>
> **标注约定**：「源码」= 直接读过上游代码并给出 `file:line`；「推断」= 基于代码与常识的合理推断，未在仓库内直接验证。

---

## 0. 一句话定位

satnogs-client 是一个 **跑在地面站树莓派/服务器上的常驻 Python 进程**：它周期性向 SatNOGS 云网络
（`network.satnogs.org`）拉取「分配给本站的未来观测任务」，按任务里给的 `start` 时刻准时触发一次接收，
接收期间用两条后台线程分别驱转台（rotator）和外置 rig 做多普勒，同时拉起一个 GNU Radio flowgraph 子进程
做解调/译码/录音频/画 waterfall；过境结束后把音频、waterfall 图、译码帧按文件名约定 PUT 回云端。
**它自己不做过境预测**——过境预测由云端完成，客户端是「被下发任务的执行器」。

---

## 1. 架构概览

### 1.1 进程拓扑

```
                SatNOGS 云网络 (Django REST, network.satnogs.org/api/)
                         ▲                    ▲
          GET jobs/      │   PUT observations/<id>/ (音频/图/帧/元数据)
                         │                    ▲
┌────────────────────────┴────────────────────┼──────────────────────────┐
│  satnogs-client  (常驻 Python, APScheduler)  │                          │
│                                             │                          │
│  BackgroundScheduler (UTC, MemoryJobStore)  │                          │
│   ├─ interval 60s  get_jobs()        ───────┼──► 拉取任务 diff       │
│   ├─ interval 180s post_data()      ───────┼──► 扫描输出目录上传    │
│   └─ date jobs (每个观测一条)        ─┐      │                          │
│        run_date = job['start']       │      │                          │
│        fn = spawn_observer           │      │                          │
└──────────────────────────────────────┼──────┼──────────────────────────┘
                                       ▼      │
                          ┌────────────────────┴─────────┐
                          │  Observer (一次观测)       │
                          │  ├─ WorkerRot 线程 (az/el)  │──► rotctld / 串口转台
                          │  ├─ WorkerFreq 线程 (doppler)│──► rigctld (外置 LO)
                          │  ├─ flowgraph_dispatcher 子进程│──► gr-satnogs 解调脚本
                          │  └─ (可选) gr_satellites 子进程│──► UDP IQ → KISS 帧
                          └─────────────────────────────┘
```

来源：`satnogsclient/__init__.py:38-54`（`main()`）、`satnogsclient/scheduler/__init__.py:10-23`、
`satnogsclient/scheduler/tasks.py:284-296`。

### 1.2 模块职责表（源码）

| 模块 | 职责 | 关键位置 |
|---|---|---|
| `scheduler/tasks.py` | 与网络 API 交互：拉任务 diff、触发观测、周期上传 | `tasks.py:198` `get_jobs()`、`tasks.py:104` `post_data()`、`tasks.py:29` `spawn_observer()` |
| `observer/observer.py` | 单次观测编排：setup、起线程、起 flowgraph、等结束、改名、上传元数据 | `observer.py:163` `setup()`、`observer.py:222` `observe()` |
| `observer/worker.py` | 两条 daemon 线程：转台 az/el、rig 多普勒频率 | `worker.py:91` `WorkerTrack`、`worker.py:197` `WorkerFreq` |
| `observer/orbital.py` | 对 TLE 做实时 alt/az/距离速率解算（封装 `satnogs-predict`） | `orbital.py:36` `pinpoint()` |
| `radio/flowgraphs.py` | 模式→gr-satnogs 脚本映射；拼装命令行参数；Popen 启动 dispatcher | `flowgraphs.py:10-126`、`flowgraphs.py:196-215` |
| `radio/grsat.py` | （可选）并行拉起 `gr_satellites`，过境后把 KISS 帧切成 JSON | `grsat.py:118-199` |
| `waterfall.py` | 把 `.dat` waterfall 二进制画成 PNG | `waterfall.py` 全文 |
| `artifacts.py` | 把 waterfall 数据 + 元数据打包成 HDF5 上传到 db.satnogs.org | `artifacts.py:10-95` |
| `locator/locator.py` | 可选：从 GPSD 读本站经纬度海拔 | `locator/locator.py` 全文 |
| `settings.py` | 全部配置走环境变量 / `.env` | `settings.py:24-139` |

### 1.3 观测配置格式（云端下发的 JSON，源码）

`spawn_observer` 的 docstring 直接给了一份真实任务字典（`scheduler/tasks.py:37-51`）：

```jsonc
{
  "id": 38215,                       // 观测 ID，也是本地 job id
  "start": "2025-07-31T16:30:02Z",   // UTC，APScheduler date job 触发时刻
  "end":   "2025-07-31T16:33:50Z",   // UTC，接收硬截止
  "ground_station": 338,
  "tle0": "FOX-1A (AO-85)",
  "tle1": "1 40967U 15058D   25212.20879382 ...",
  "tle2": "2 40967  64.7744 ...",
  "frequency": 145978500,            // Hz，目标下行标称频率
  "mode": "DUV",                     // 解码器选择键，见 1.4
  "transmitter": "ZyjKNJ9KqnTHBCUzAPN5G5",
  "baud": 200.0,                     // CW 时是 WPM；缺省则 0
  "max_altitude": 19.0,
  "norad_cat_id": 40967              // gr_satellites 用它查卫星配置
}
```

字段在 `tasks.py:53-72` 被解包进 `Observer.setup()`。注意 **客户端不自己算 AOS/LOS**——`start/end` 是
云端根据本站坐标 + TLE + 仰角门限算好后下发的（推断：云端调度算法不在本仓库内）。

### 1.4 解码器编排（gr-satnogs / gr-satellites）

客户端不内置解调器，而是按 `mode` 字符串查表启动对应脚本：

- 脚本表 `flowgraphs.py:10-22`：`AFSK1K2 / AMSAT_DUV / APT / ARGOS_BPSK_PMT_A3 / BPSK / CW / FM / FSK / GFSK_RKTR / GFSK/BPSK / SSTV`。
- 模式表 `flowgraphs.py:24-126`：把云端更细的 mode 名（`AFSK / BPSK PMT-A3 / FSK AX.100 Mode 5 / MSK / GMSK / SSTV …`）
  映射到上面脚本，并标注 `has_baudrate`、`has_framing`（`ax25` / `ax100_mode5` / `ax100_mode6`）。
- 默认模式兜底：`SATNOGS_FLOWGRAPH_MODE_DEFAULT = 'FM'`（`flowgraphs.py:126`），未知 mode 也走 FM（`observer.py:371-376`）。

启动方式：统一入口脚本名是 `flowgraph_dispatcher`（`flowgraphs.py:127`），所有参数以 `--key=value` 拼出来
（`flowgraphs.py:204-210`），主要参数（`flowgraphs.py:150-180`）：

```
--soapy-rx-device=...  --samp-rate-rx=...  --rx-freq=...
--file-path=<raw out>   --waterfall-file-path=<dat>  --decoded-data-file-path=<data>
--doppler-correction-per-sec=...   --lo-offset=...  --ppm=...
--rigctl-host=... --rigctl-port=...   --gain-mode=... --gain=... --antenna=...
--dc-removal=... --bb-freq=... --bw=...
--enable-iq-dump=... --iq-file-path=...
--udp-dump-host=... --udp-dump-port=...
[--baud=9600]
```

**并行解码器 gr-satellites**（可选，`GR_SATELLITES_ENABLED`，`settings.py:102`）：
`observer.py:254-261` 在 flowgraph 之前拉起 `gr_satellites <norad_id> --samp_rate N --iq --udp --udp_raw
--udp_port 57356 --kiss_out ... --satcfg`（`grsat.py:146-162`）。即 **flowgraph 把多普勒校正后的 IQ 通过 UDP
喂给 gr_satellites**，后者把 KISS 帧落到本地文件；观测结束时 `grsat.py:187-195` 再把 KISS 逐帧切成
`data_<obsid>_<ts>_g<N>` 的 JSON（`{"decoder_name":"gr-satellites","pdu":<base64>}`，`grsat.py:67-72`）。

采样率选择 `grsat.py:89-115`：按解码器类型用 sps（samples-per-symbol）反推——`_bpsk/_ssb` 用 decimation×baud，
`_fsk/_qubik` 同理，`_sstv/_apt` 固定 `4*4160*4`，CW/FM/AFSK 直接 48 kHz。

---

## 2. 观测调度与自动化

### 2.1 调度器骨架（源码）

- APScheduler `BackgroundScheduler`，时区 UTC，内存 jobstore，线程池 20；
  `coalesce=True, max_instances=1, misfire_grace_time=30`（`scheduler/__init__.py:10-21`）。
- `status_listener()`（`tasks.py:284-296`）：启动后先 `remove_all_jobs()`，再注册两个周期任务：
  - `get_jobs`：每 `SATNOGS_NETWORK_API_QUERY_INTERVAL` 秒（默认 60s，`settings.py:57-60`）；
  - `post_data`：每 `SATNOGS_NETWORK_API_POST_INTERVAL` 秒（默认 180s，`settings.py:61-63`）。

### 2.2 任务 diff 算法（源码）

`get_jobs()`（`tasks.py:198-281`）：

1. （可选）GPSD 更新本站坐标（`tasks.py:200-201` → `locator.py`）。
2. `GET {API}jobs/`，query 参数 `ground_station / lat / lon / alt=int(海拔)`（`tasks.py:207-212`），
   Header `Authorization: Token <SATNOGS_API_TOKEN>`，超时 45s。
3. 把云端返回 `requested_jobs`（`job['id'] → job`）与本地已排 `scheduled_jobs`（APScheduler 里
   `job.name == 'spawn_observer'` 的 job，`tasks.py:229-232`）做集合 diff：
   - **dropped**（本地有、云端没了）→ `job.remove()`（`tasks.py:244-246`，云端删了/改了）；
   - **common**（两边都有）→ 若参数字典变了，`add_job(..., replace_existing=True)`（`tasks.py:248-266`）；
   - **new** → `add_job(spawn_observer, 'date', run_date=start, id=str(job_id), kwargs={'obj': obj})`
     （`tasks.py:268-281`）。

这是一个非常干净的「**云端为 Source-of-Truth，本地 APScheduler 镜像同步**」模式——没有复杂的本地预测，
只做集合差量更新。

### 2.3 到点触发与单观测锁（源码）

`spawn_observer`（`tasks.py:29-91`）：

- 解出 `tle / end / frequency / mode / baud`，构造 `Observer` 并 `setup(**setup_kwargs)`（`tasks.py:53-76`）。
- 关键：**全局 `OBSERVER_LOCK = threading.Lock()`**（`tasks.py:19`），acquire 超时 = `end - now`
  （`tasks.py:78-83`）。即同一时刻只允许一个观测在跑——SDR 硬件只有一个，排队等上一个结束，
  若等到 end 还没拿到锁就放弃本次观测。
- 拿到锁后 `observer.observe()`，任何异常都 `finally: OBSERVER_LOCK.release()`（`tasks.py:84-89`）。

### 2.4 一次观测的执行时序（源码，`observer.py`）

`observe()`（`observer.py:222-350`）顺序：

1. **pre-observation 脚本**（可选，`observer.py:224-245`）：模板替换
   `{{FREQ}} {{TLE}} {{TIMESTAMP}} {{ID}} {{BAUD}} {{SCRIPT_NAME}}`，`shlex.split` 后 `subprocess.call`。
   典型用途：过境前给 LNA 上电、转台预置。
2. APT 模式特殊处理：译码文件名先用 `receiving_data_` 前缀（`observer.py:250-252`）。
3. 可选 `gr_satellites` 子进程（`observer.py:254-261`）。
4. 可选转台线程 `run_rot()`（`observer.py:263-265` → `worker.py:114-126`）：daemon 线程每
   `sleep_time=3s`（`observer.py:353-355`）算一次 `pinpoint()`，az/el 超过
   `SATNOGS_ROT_THRESHOLD`（默认 4°，`settings.py:74`）才下发新位置（`worker.py:190-194`）。
   支持天线翻转 flip（`SATNOGS_ROT_FLIP / FLIP_ANGLE=75°`，`worker.py:158-180`）。
5. rig 多普勒线程 `run_rig()`（`observer.py:269` → `worker.py:213-229`）：daemon 线程每
   `_sleep_time=0.1s` 算一次 `pinpoint()`，取 `rng_vlct`（距离速率）调
   `get_doppler_adjusted_frequency(rng_vlct, f_nominal)`，把结果 int 后写 `rig.set_freq`
   （`worker.py:245-250`）。**这是外置 LO/rig 的多普勒校正**。
6. 主 flowgraph 子进程（`observer.py:272-280`）：`Flowgraph(...)` 构造参数（见 1.4），
   `flowgraph.enabled = True` 触发 Popen。
7. **阻塞等待**：`poll_gnu_proc_status(flowgraph)`（`observer.py:280` 调用，实现 `observer.py:378-391`）
   就是 `while enabled and now <= observation_end: sleep(1)`；到点后 `enabled=False`，
   依次 `trackstop()` freq/rot 线程、`grsat.stop_gr_satellites()`、跑 post-observation 脚本
   （`observer.py:393-414`，同样支持模板变量）。
8. 收尾：raw `.out` rename 成 `.ogg`（`observer.py:426-430`）、`receiving_data_` rename 成
   `data_`（`observer.py:432-437`）、用 `Waterfall` 对象把 `.dat` 画成 PNG（`observer.py:286-297`）。
9. PUT 元数据（见 §3.2）；可选 HDF5 artifact 上传（`observer.py:331-350`）。

### 2.5 频率→设备选择（源码）

`rx_device_for_frequency()`（`observer.py:32-52`）：`SATNOGS_SOAPY_RX_DEVICE` 支持写成
`"minMHz-maxMHz:spec1 minMHz-maxMHz:spec2"`，按本次观测频率自动选对应硬件——单站多频段 SDR 并存的做法。

### 2.6 与 MBDSDR 现有实现的对照（源码 vs 源码）

| 概念 | satnogs-client | MBDSDR 现状（`cpp/src/ui/main_window.cpp`） |
|---|---|---|
| 过境预测 | **客户端不做**，云端下发 start/end | `passes_` 列表 + `skyTimeSlider_` 预览（`main_window.cpp:734-745`），本地 SGP4 已实现 |
| 到点自动触发 | APScheduler `date` job 到 `start` 自动跑 `spawn_observer` | **无**：用户必须在过境列表里选中行、手动点「捕获」按钮（`onCapturePassClicked` `main_window.cpp:3443`） |
| 多普勒补偿 | 0.1s 线程驱 rigctl（`worker.py:245-250`）+ flowgraph 内 NCO `--doppler-correction-per-sec` | 1 Hz 循环里 `dopplerLimiter_.advance()` 步进调 VFO offset（`updateLiveSatellite` 内 `main_window.cpp:3643`），软件 NCO 等价物 |
| 单次硬件锁 | `OBSERVER_LOCK` 串行化（`tasks.py:19,81`） | 单 VFO 单 SDR，天然串行，未显式建模 |
| 转台 | WorkerTrack + 阈值/flip | （MBDSDR 暂无 rotator，推断） |
| 录制启停 | 随 `observation_end` 硬截止（`observer.py:379`） | 捕获后不自动停录（推断，未在代码里看到自动停止） |
| 前后钩子 | pre/post 脚本 + 模板变量（`observer.py:224-245,393-414`） | 无 |

**可直接借鉴的概念**：
1. 「到点自动触发」用一条 `date` 定时任务即可——MBDSDR 已经在算未来过境，只差一个
   QTimer/调度器在 `start` 时刻自动调 `onCapturePassClicked()` 并开录制。
2. 单硬件锁 + 排队语义（`OBSERVER_LOCK` 超时 = `end - now`）值得照搬，防止连续过境打架。
3. pre/post 脚本钩子（带 `{{FREQ}}/{{TLE}}/{{ID}}` 模板）是非常轻量的扩展点。
4. `flowgraph_dispatcher --key=value` 的「统一入口 + 参数化」模式，比 MBDSDR 当前
   `recommendSatelliteMode()` 写死模式表要可扩展。

---

## 3. 上传 / API 交互

### 3.1 三类产物的上传（源码）

`post_data()`（`tasks.py:104-148`）周期扫描 `SATNOGS_OUTPUT_PATH`，按文件名前缀分流：

| 文件名前缀 | 类型 | multipart 字段 | 上传后 |
|---|---|---|---|
| `receiving_satnogs_*` | 录制中音频（半成品） | **跳过**（`tasks.py:110-112`） | — |
| `satnogs_<id>_<ts>.ogg` | 音频 | `payload` | 成功后移到 `SATNOGS_COMPLETE_OUTPUT_PATH` 或删除（`tasks.py:94-101`） |
| `receiving_waterfall_*` | 半成品 | 跳过 | — |
| `waterfall_<id>_<ts>.png` | waterfall 图 | `waterfall` | 同上 |
| `receiving_data_*` | 译码中 | 跳过 | — |
| `data_<id>...` | 译码帧（JSON 含 `pdu` 或裸二进制） | `demoddata`（JSON 则 `base64.b64decode(pdu)`） | 同上 |

 observation_id 从文件名 `split('_')[1]` 取（`tasks.py:145-148`）——**文件名就是元数据协议**。

### 3.2 上传请求（源码）

`upload_observation_data()`（`tasks.py:151-195`）：

```
PUT {SATNOGS_NETWORK_API_URL}observations/<observation_id>/
Headers: Authorization: Token <token>
Body (multipart): {'payload' | 'waterfall' | 'demoddata': file}
```

- 超时 `SATNOGS_NETWORK_API_TIMEOUT=1800s`（`settings.py:64`），`stream=True`。
- **404**（观测已被云端删除）→ 文件 rename 到 `SATNOGS_INCOMPLETE_OUTPUT_PATH`（默认
  `/tmp/.satnogs/data/incomplete`，`settings.py:40-41`），不丢数据。
- **403 且 body 含 "has already been uploaded"** → 认为已传过，本地清理。
- 其他 HTTP 错误 → 仅打日志，下一个 180s 周期重试。

元数据 PUT（`observer.py:299-329`）：观测结束后立刻一次
```
PUT {API}observations/<id>/
Body (form): client_version=<VERSION>,
             client_metadata=<JSON: {radio:{name:'gr-satnogs', version, parameters:{...}},
                                     latitude, longitude, elevation, frequency}>
```
radio 版本号通过 `python3 -m satnogs.satnogs_info` 现场探测（`flowgraphs.py:231-244`）。

### 3.3 HDF5 Artifact（源码）

`POST {ARTIFACTS_API_URL}artifacts/`（`observer.py:72-85`），body 为 `<uuid>.h5`：
`artifacts.py:34-94` 用 h5py 把 waterfall 的 offset/scale/data/relative_time/absolute_time/frequency
全部 gzip 存进一个 `waterfall` group，attrs 里塞 `observation_id / frequency / tle / location`。
默认 endpoint `https://db.satnogs.org/api/`（`settings.py:114`）。

### 3.4 配置项速查（源码，`settings.py`）

- 必填：`SATNOGS_API_TOKEN / SATNOGS_STATION_ID / SATNOGS_SOAPY_RX_DEVICE / SATNOGS_RX_SAMP_RATE / SATNOGS_ANTENNA`
  （`settings.py:136-139`）。
- 站坐标：`SATNOGS_STATION_LAT/LON/ELEV`；不开 GPSD 时三者必填（`settings.py:170-180`）。
- 关键路径：`SATNOGS_OUTPUT_PATH`（上传暂存）、`SATNOGS_COMPLETE_OUTPUT_PATH`（成功后归档）、
  `SATNOGS_INCOMPLETE_OUTPUT_PATH`（404 归档）。
- 多普勒：`SATNOGS_DOPPLER_CORR_PER_SEC`（flowgraph 内 NCO）、外置 rig `SATNOGS_RIG_IP/PORT=4532`。
- UDP IQ 转发：`UDP_DUMP_HOST/PORT=57356`（喂 gr_satellites / 外部分析器）。

> 与旧笔记 `docs/learn/satellite_groundstation.md §2.2` 的差异：旧笔记写的是
> `GET /api/observations/next/` 和 `POST .../finished/<id>/`——那是 satnogs-client **老版本**接口。
> 上游 master 实际已是 `GET jobs/` 做 diff + `PUT observations/<id>/` 传文件/元数据（源码确认）。

---

## 4. 对 MBDSDR「本地观测自动化」的可落地清单

MBDSDR 当前已有：过境预测列表、sky 预览滑条、一键捕获（调谐到 f0+峰值多普勒）、1 Hz 多普勒限幅
VFO 跟踪。**缺的是「无人值守」那一半**。对照 satnogs-client，建议落地：

### 4.1 调度层（最高优先级）

1. **AOS 自动捕获**：为 `passes_` 里未来每个过境注册一个定时任务，触发时刻 = AOS（提前 1~2s），
   回调等价于 `onCapturePassClicked()` + 自动开录制。到 LOS 自动停录。
   （借鉴 `tasks.py:276-281` 的 `add_job(spawn_observer,'date',run_date=start)`。）
2. **单硬件锁**：一个 `ObservationSession` RAII 对象，SDR 被占时新请求排队或拒绝；
   借鉴 `tasks.py:19,81` 的 `OBSERVER_LOCK.acquire(timeout=end-now)`。
3. **过境状态机**：
   `IDLE → SCHEDULED(已排期) → ARMED(到点前 N 秒) → TRACKING(AOS~LOS, 1Hz 多普勒) →
   FINISHING(停录/改名/画 waterfall) → ARCHIVED/UPLOADED`。
   对齐 r2cloud `ObservationStatus` 与 satnogs 的「receiving_ 前缀 → rename」协议。

### 4.2 产物层

4. **录制文件命名协议**：`<kind>_<obsid>_<UTCts>.<ext>`，录制中用 `receiving_<kind>_` 前缀，
   结束 rename——崩溃时一眼能看出哪些是半成品（`observer.py:180-214`）。
5. **过境后自动渲染 waterfall PNG**：MBDSDR 已有 IQ 录制，可复用 `waterfall.py` 思路（matplotlib Agg）
   在 FINISHING 阶段自动出图。
6. **归档目录约定**：`complete/`（成功）、`incomplete/`（失败/中断），后台周期扫描重试上传
   （`tasks.py:94-101, 186-188`）。

### 4.3 扩展点

7. **pre/post 钩子**：过境前后各跑一次用户配置的外部命令，参数支持
   `{{FREQ}}/{{TLE}}/{{ID}}/{{BAUD}}` 模板（`observer.py:224-245,393-414`）。
   用途：LNA 电源、rotator 预置、推消息通知。
8. **mode→解码器注册表**：把现在 `recommendSatelliteMode()` 的隐式映射显式成
   `{mode: (decoder_cmd, baud, framing)}` 表（对齐 `flowgraphs.py:24-126`），新增解调模式只改表。
9. **UDP IQ 出口**：把多普勒校正后的实时 IQ 镜像到 UDP 端口（`flowgraphs.py:175-176` 的
   `udp-dump-host/port`），这样外部 gr-satellites / inspectrum / 自定义解码可以旁路接入，
   不影响主录制。
10. **多频段设备选择**（远期）：若未来 MBDSDR 接多支 SDR，按频率范围选设备的
    `rx_device_for_frequency()`（`observer.py:32-52`）是现成范式。

### 4.4 不需要照搬的部分

- **云端 jobs diff 同步**：MBDSDR 是本地单机，过境预测自己已经能做（`passes_`），不需要把云端
  当 Source-of-Truth；但「本地排期列表 vs 实际预测结果」的 diff 同步思路（`tasks.py:237-281`）
  可用于 TLE 更新后重排。
- **外置 rigctl 多普勒线程**：MBDSDR 软件 VFO offset 已经等价于 flowgraph 内 NCO，0.1s 粒度的
  rig 线程对纯 SDR 无意义。
- **rotator flip**：MBDSDR 当前无转台，先不引入。

---

## 5. 来源 / 推断 标注汇总

**源码确认（均在本笔记中给了 file:line）**：
- 进程/调度骨架：`__init__.py:38-54`、`scheduler/__init__.py:10-21`、`tasks.py:284-296`。
- jobs diff 与 date job 触发：`tasks.py:198-281`。
- 观测 JSON 字段：`tasks.py:37-51` docstring（注意：这是注释里的示例，字段含义是从代码解包处
  `tasks.py:53-72` 交叉验证的）。
- 单观测锁、observe() 时序、pre/post 脚本：`tasks.py:19,81`、`observer.py:222-414`。
- 多普勒/转台线程：`worker.py:91-250`、`orbital.py:36-69`。
- 解码器表与 dispatcher 参数：`flowgraphs.py:10-215`。
- gr-satellites 并行解码与 KISS→JSON：`grsat.py:39-75,118-199`。
- 上传三类产物与 404/403 分支：`tasks.py:94-195`、`observer.py:299-350`。
- HDF5 artifact：`artifacts.py:10-95`。
- 全部环境变量：`settings.py:24-139`。

**推断（未在仓库内直接验证）**：
- 云端「怎么算 start/end、怎么分配观测」是 Django 端逻辑，不在 client 仓库——本笔记未读 satnogs-network 源码。
- `transmitter`、`max_altitude` 字段在客户端只被透传/记录，未参与决策（从代码引用处推断）。
- MBDSDR 现状对比表中「捕获后不自动停录」「无 rotator」是基于读到的 `main_window.cpp` 片段推断，
  未全量通读 cpp 工程。
- 旧笔记 `satellite_groundstation.md §2.2` 描述的 `GET observations/next/`、`POST finished/<id>/`
  判定为老版本接口——基于当前 master 代码不再出现这两个端点推断。

---

## 6. 一句话总结

satnogs-client 的精髓不是解调（那是 gr-satnogs 的事），而是 **「云端任务 diff + APScheduler date job
+ 单硬件锁 + receiving_/完成 rename 的文件协议 + 周期上传扫描」** 这套薄编排层。MBDSDR 已经把
解调侧（SDR、模式推荐、软件多普勒）做完了，最值得补的就是这层薄编排：给已有的 `passes_` 加一个
「到点自动捕获 + 到 LOS 自动停录 + 成品文件命名归档 + 过境后自动出图」的状态机。
