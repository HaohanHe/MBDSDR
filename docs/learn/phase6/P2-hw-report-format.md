<!--
SPDX-License-Identifier: MIT
-->

# P2 · 真机结果回传格式与云侧解析

> 适用范围：第六阶段 P2。目标是让真机用户**把两条命令的 `--json` 输出直接贴回聊天**，
> 云侧（或本地）用 `tools/onboarding/parse_hw_report.py` 一键得到结构化结论。
>
> 本文所有字段名均逐字段核对自源码（`tools/hw_selfcheck/selfcheck.py`、
> `tools/onboarding/onboard.py`），**不臆造**。无硬件时的取值均来自本机（云 VM）
> 真实跑测结果。

---

## 1. 真机用户三步操作

在**插好 RTL-SDR 的真机**上，于仓库根目录依次执行：

```bash
# ① 环境自检（只读，不装包、不改 udev；无硬件也能跑，诚实报无设备）
python3 tools/hw_selfcheck/selfcheck.py --json

# ② 全链联调向导（ADS-B 1090 MHz；detect 失败会立即停止，绝不 mock）
python3 tools/onboarding/onboard.py --step all --freq 1090e6 --mode adsb --json

# ③ 把上面两条命令的【完整输出】（从第一个 { 到最后一个 }）整段贴回聊天即可
```

> 命令里的 `--json` **不能省**：不加它跑出来是带颜色的人类可读报告，云侧无法解析。
> 贴回时**两段都要贴**——只有 `onboard` 而没有 `selfcheck`，云侧看不到声卡/GNSS/依赖
> 的细分；只有 `selfcheck` 而没有 `onboard`，云侧不知道采集/解码/产物情况。

云侧拿到贴回文本后运行（也可本地自行验证）：

```bash
# 从文件
python3 tools/onboarding/parse_hw_report.py report.txt
# 或从 stdin（贴回文本直接 pipe 进去）
cat report.txt | python3 tools/onboarding/parse_hw_report.py
# 想要机器可读的结构化 JSON 结论再加 --json
python3 tools/onboarding/parse_hw_report.py report.txt --json
```

解析器会自动从**混杂了人类对话、命令回显、报错信息的聊天文本**里抽出多段 JSON，
识别哪段是 selfcheck、哪段是 onboard，然后输出设备/声卡/GNSS/依赖/分步结果/产物清单，
并给出**与检测事实挂钩的后续建议**。

---

## 2. `selfcheck.py --json` 完整输出结构

顶层信封（`build_json()`，selfcheck.py:779）：

| 字段 | 类型 | 含义 / 取值 |
|---|---|---|
| `tool` | string | 固定 `"mbdsdr-hw-selfcheck"`，用于云侧识别。 |
| `version` | string | 脚本版本，当前 `"0.1.0"`。 |
| `timestamp` | string | 本机带时区的 ISO 时间，如 `2026-10-02T08:13:03+08:00`。 |
| `host` | string | `platform.node()` 主机名。 |
| `os` | string | `platform.platform()` 内核/发行版串。 |
| `python` | string | `platform.python_version()`。 |
| `checks` | array | **固定 6 项**检查，顺序见下。每项结构见 §2.1。 |
| `summary` | object | 汇总结论，见 §2.2。 |

### 2.1 每一项 check 的统一结构

```jsonc
{
  "name": "1. USB/udev：RTL-SDR 枚举与权限",
  "status": "PASS",           // PASS / WARN / FAIL / SKIP
  "evidence": ["..."],         // 脚本实际看到的一行行事实
  "fix": ["..."],             // 修复建议命令（本工具不代执行）
  "detail": { ... }            // 各检查项不同的结构化明细，见下
}
```

`status` 取值语义：

- `PASS`：通过；
- `WARN`：不阻断但建议修（如缺可选依赖、有声卡但命令缺失、有串口但没读到 NMEA）；
- `FAIL`：阻断（典型 = 没看到 RTL-SDR、或采到 0 字节 IQ）；
- `SKIP`：上游前置没过、本项不具备执行条件（**无设备时第 2、3 项恒为 SKIP**，
  `detail.skipped_reason = "no_usb_device"`）。

#### 检查 1 · USB/udev（`name` 以 `1.` 开头）

`detail` 字段：

| 字段 | 类型 | 无硬件时取值 | 说明 |
|---|---|---|---|
| `sys_scanned` | array[string] | `[]` | `/sys/bus/usb` 扫到的全部 `vid:pid product`。 |
| `target_hits` | array[object] | `[]` | 命中目标 USB ID（`0bda:2838` RTL2838UHIDIR / `0bda:2832` 兼容）的设备。每项含 `vid`/`pid`/`product`/`known_as`/`sysdir`。**这是"有没有插设备"的唯一判据**。 |
| `user_groups` | array[string] | `["user"]` | 当前用户所属全部组名。 |
| `udev_rules_hit` | array[string] | `[]` | `/lib/udev/rules.d`、`/etc/udev/rules.d` 里命中 0bda:2838/2832 的规则文件路径列表。空 = 没装规则。 |

#### 检查 2 · 设备枚举 `rtl_test -t`（`name` 以 `2.` 开头）

`detail` 字段：

| 字段 | 类型 | 无硬件/命令缺失时取值 | 说明 |
|---|---|---|---|
| `skipped_reason` | string | `"no_usb_device"` | 仅 SKIP 时有。 |
| `rc` | int | 缺省 | `rtl_test -t` 返回码。 |
| `output_tail` | string | 缺省 | rtl_test 输出末尾 2000 字符。 |
| `parsed` | object | 缺省 | 解析结果：`tuner`（如 `"Rafael Micro R820T"`）、`gain_count`（档数，常见 29）、`gain_list_db`（增益列表）、`devices[]`（每项 `idx/vendor/product/serial`）、`no_supported_devices`（bool）。 |

> 注意：`rtl_test` 命令本身缺失时，本项是 **WARN**（不是 SKIP），evidence 写
> "未找到 rtl_test"；只有"命令在但 USB 没命中"才是 SKIP。

#### 检查 3 · 流读取 `rtl_sdr` 实读 ≤3s（`name` 以 `3.` 开头）

`detail` 字段：

| 字段 | 类型 | 无硬件时取值 | 说明 |
|---|---|---|---|
| `skipped_reason` | string | `"no_usb_device"` | SKIP 时。 |
| `rc` | int | 缺省 | rtl_sdr 返回码。 |
| `tmp_file_bytes` | int | 缺省 | 临时 IQ 文件字节数。真机采到数据 ≈ `7_200_000×2 = 14_400_000` 字节（I/Q 各 8bit）。 |
| `lost_bytes_total` | int | 缺省 | 从 stderr `lost at least N bytes` 累加的丢包字节数。**0 = 无丢包**。 |

#### 检查 4 · 声卡 `aplay -l / arecord -l`（`name` 以 `4.` 开头）

`detail` 字段（命令缺失时这些 key **整个不出现**，detail 为空 `{}`）：

| 字段 | 类型 | 无头云主机取值 | 说明 |
|---|---|---|---|
| `playback_cards` | array[object] | 缺省（不出现） | `aplay -l` 解析出的播放卡，每项 `index/short/name`。 |
| `capture_cards` | array[object] | 缺省 | `arecord -l` 录音卡。 |
| `dev_snd_entries` | array[string] | 缺省 | `/dev/snd` 目录条目；不出现 = `/dev/snd` 不存在。 |

#### 检查 5 · GNSS 串口（`name` 以 `5.` 开头）

`detail` 字段：

| 字段 | 类型 | 无 GNSS 时取值 | 说明 |
|---|---|---|---|
| `candidates` | array[string] | `[]` | 发现的 `/dev/ttyUSB*`、`/dev/ttyACM*`、`/dev/serial/by-id`（后者带 ` -> 真实路径`）。空 = 一个串口都没有。 |
| `probes` | array[object] | 缺省 | 对每个候选串口、分别以 9600/115200 波特率探读的结果，每项 `device/baud/ok/sample/reason`。**`ok=true` 即判定读到 NMEA**（行首 `$GP/$GN/$BD/$GL/$GA`）。 |

#### 检查 6 · 依赖 `pyrtlsdr / SoapySDR / gpsd`（`name` 以 `6.` 开头）

`detail` 字段：

| 字段 | 类型 | 缺失时取值 | 说明 |
|---|---|---|---|
| `py_rtlsdr` | bool | `false` | `import rtlsdr` 是否成功。 |
| `py_soapy` | bool | `false` | `import SoapySDR` 是否成功。 |
| `gpsd` | string\|null | `null` | `which gpsd` 路径；`null` = 没装。 |
| `gpsd_client` | string\|null | `null` | `which cgps` 或 `gpsmon`；`null` = 没装。 |

### 2.2 `summary` 字段

```jsonc
"summary": {
  "counts": { "PASS": 0, "WARN": 3, "FAIL": 1, "SKIP": 2 },
  "conclusion": "未检测到 RTL-SDR 真机设备；本机适合做软件/CI 自检，真机联调请在插好硬件的机器上重跑。",
  "real_machine_todo": ["插入 RTL-SDR (0bda:2838) 到 USB2.0 口...", "..."]
}
```

- `counts`：四项状态计数，**无硬件典型 = `PASS:0, WARN:3, FAIL:1, SKIP:2`**。
- `conclusion`：一句话结论。无设备时会自动切到"未检测到真机设备"分支。
- `real_machine_todo`：仅在"FAIL 且无设备"分支非空，给出真机待办清单；其余场景为空 `[]`。

### 2.3 无硬件真机的真实输出（节选，本机云 VM 实测）

```json
{
  "tool": "mbdsdr-hw-selfcheck",
  "version": "0.1.0",
  "host": "vefaas-...-sandbox",
  "checks": [
    { "name": "1. USB/udev：RTL-SDR 枚举与权限", "status": "FAIL",
      "detail": { "sys_scanned": [], "target_hits": [],
                  "user_groups": ["user"], "udev_rules_hit": [] } },
    { "name": "2. 设备枚举：rtl_test -t（tuner 型号 / 增益档数）", "status": "SKIP",
      "detail": { "skipped_reason": "no_usb_device" } },
    { "name": "3. 流读取：rtl_sdr 实读 ≤3s（2.4 MS/s）丢包统计", "status": "SKIP",
      "detail": { "skipped_reason": "no_usb_device" } },
    { "name": "4. 声卡：aplay -l / arecord -l", "status": "WARN", "detail": {} },
    { "name": "5. GNSS 串口：...", "status": "WARN", "detail": { "candidates": [] } },
    { "name": "6. 依赖：pyrtlsdr / SoapySDR / gpsd", "status": "WARN",
      "detail": { "py_rtlsdr": false, "py_soapy": false,
                  "gpsd": null, "gpsd_client": null } }
  ],
  "summary": { "counts": { "PASS": 0, "WARN": 3, "FAIL": 1, "SKIP": 2 },
               "conclusion": "未检测到 RTL-SDR 真机设备；...", "real_machine_todo": [...] }
}
```

退出码：有 FAIL → `1`；无 FAIL → `0`；脚本自身参数错 → `2`。

---

## 3. `onboard.py --json` 完整输出结构

### 3.1 正常路径外层信封（main()，onboard.py:996）

```jsonc
{
  "tool": "mbdsdr-onboarding",
  "version": "0.1.0",
  "timestamp": "2026-10-02T09:00:05+08:00",
  "host": "realbox",
  "mode": "adsb",                 // adsb / apt / cw / ax25
  "params": {
    "freq_hz": 1090000000.0,      // Hz
    "sample_rate_hz": 2400000.0,  // Hz
    "n_samples": 240000,          // 采样点数
    "gain_db": 24.0
  },
  "steps": [ /* 见 3.2 */ ]
}
```

> **重要**：当 `--step all` 且 **detect 步就 FAIL** 时，脚本会提前 `return 1`，
> 此时只吐**裸 `{"steps":[...]}`，没有上面的 `tool/host/mode/params` 外层信封**
> （onboard.py:886）。云侧解析器已兼容这两种形状。而 `--step detect` 单跑时
> 即使失败也保留外层信封。

### 3.2 每个 step 的统一结构

```jsonc
{
  "step": "detect",            // detect/capture/record/decode/output
  "status": "PASS",            // PASS / FAIL / SKIP（RUNNING 不会出现在最终输出）
  "message": "检测到 RTL-SDR 设备",
  "evidence": ["..."],
  "fixes": ["..."],
  "detail": { ... }            // 各步不同，见下
}
```

| step | `detail` 关键字段 | 说明 |
|---|---|---|
| `detect` | `selfcheck_summary`（含 `counts/conclusion/real_machine_todo`）、`selfcheck_checks`（每项只有 `name/status`） | 内部调用 selfcheck 后的精简结果。detect 是否 PASS = USB 层有没有命中设备。 |
| `capture` | `rc`、`stderr_tail`、`raw_bytes_written`（实际写字节）、`lost_bytes_total`（丢包字节）、`actual_samples`、`out_raw_path` | rtl_sdr 起流。`raw_bytes_written=0` 通常 = 设备被占。 |
| `record` | `sigmf_data`、`sigmf_meta`、`n_samples`、`duration_s` | uint8 IQ → SigMF `cf32_le`。 |
| `decode` | `n_samples`、`mode`、`n_frames`；`adsb`→`decoded_frames[]`（含 `icao/callsign/altitude/speed`）；`cw`→`decoded_text`+`cw_detail{wpm_est,confidence,dit_ms}`；`apt`→`apt_image_shape`；`ax25`→`decoded_frames[{src,dst,info}]` | 真实解码器结果。`n_frames=0` 且 status=FAIL = 没解出有效帧。 |
| `output` | `artifacts`（落盘文件路径数组）、`out_dir` | 产物清单。**stdout 的 detail 里没有 `data_origin`**；它在落盘的 manifest 侧车里。 |

退出码：任一 step FAIL → `1`，否则 `0`。

### 3.3 落盘产物（onboard 跑成功后写到 `--out-dir`）

`output` 步会在 `out_dir` 生成若干文件，其中两个是"口径凭证"：

- `<stamp>_manifest.json`：`{script:"onboard.py", data_origin:"captured",
  origin_label:"捕获", params:{mode,freq_hz,sample_rate_hz,n_samples},
  n_samples, timestamp_utc, code_version, license:"MIT", artifacts:[...],
  decode_summary:{status,message,n_frames}}`。
- `<stamp>_<mode>_messages.json`：`{tool,version,mode,data_origin,
  params:{freq_hz,sample_rate_hz,n_samples}, timestamp_utc, host, frames:[...]}`。

`data_origin` 取值：`captured`（本机实时 rtl_sdr 采的）/ `recorded`（回放录制）/
`ota`。真机首跑恒为 `captured`。

---

## 4. 常见异常输出如何识别

| 异常场景 | 在 JSON 里的识别特征 | 含义 / 对策 |
|---|---|---|
| **rtl_sdr / rtl_test 命令缺失** | selfcheck 检查 2/3 `status=WARN`，evidence 含"未找到 rtl_test/rtl_sdr"；或 onboard `capture` 的 `message` 含"未找到 rtl_sdr 命令" | 没装 librtlsdr。装 `rtl-sdr` 包或编译 `repos/librtlsdr`。 |
| **设备被其他进程占用** | onboard `capture`：`raw_bytes_written=0`、`message` 含"未写入任何数据（文件为空）"；selfcheck 检查 3 `status=FAIL`、`message/evidence` 含"未采到任何 IQ 数据"；`stderr_tail` 常见 `libusb` / `usb_bulk_read` 错 | 有 `rtl_tcp`/`gqrx`/`dump1090` 在占设备。杀掉占用进程或拔插重连。 |
| **采样率越界** | onboard `capture` 的 `message` 含"采样率 … Hz 超出 rtl-sdr 范围 (225k~3.2M)" | `--sr` 写错，改回 `2400000`。 |
| **中心频率越界** | onboard `capture` 的 `message` 含"中心频率 … 超出 RTL-SDR 范围 (24~1700 MHz)" | `--freq` 单位错（要 Hz，如 `1090e6`）。 |
| **USB 已枚举但打不开** | selfcheck 检查 1 PASS（`target_hits` 非空）但检查 2 出现 `parsed.no_supported_devices=true` | 设备被 dvb 内核驱动霸占。`sudo modprobe -r dvb_usb_rtl28xxu`（仅建议，脚本不执行）。 |
| **USB 丢包** | selfcheck 检查 3 `lost_bytes_total>0` 或 onboard `capture.lost_bytes_total>0`，status=WARN | USB 带宽/调度压力。换 USB2.0 直连口、避免 USB3.0 Hub、降采样率。 |
| **GNSS 有串口但没 NMEA** | selfcheck 检查 5：`candidates` 非空但所有 `probes[].ok=false`，status=WARN | 模块未定位/天线没接/波特率不对。`sudo cat /dev/ttyUSB0` 肉眼看 `$` 开头行。 |

---

## 5. 云侧解析脚本

`tools/onboarding/parse_hw_report.py`（纯 Python 标准库，不依赖 numpy）：

- 输入：文件路径参数，或缺省从 stdin 读；
- 自动从聊天文本中**抽取多段 JSON**（正确跳过字符串里的花括号）；
- 识别 selfcheck / onboard（含裸 `{"steps":[...]}` 早退出形状）；
- 输出：设备（有/无、tuner、丢包）、声卡、GNSS（NMEA 判定）、依赖缺失、
  onboard 各步结果与产物清单、**后续可做步骤建议**；
- 容错：字段缺失、detail 空 dict、某段 JSON 损坏都不崩；找不到任何 JSON 时
  以退出码 `2` 报明确错误。

确定性测试：`python3 -m pytest tools/onboarding/test_parse_hw_report.py -v`。

---

## 6. 许可

MIT。见仓库根 `LICENSE` 与各文件头 `SPDX-License-Identifier: MIT`。
