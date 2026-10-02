# MBDSDR Onboarding（真机联调向导）

一条命令从"插好 RTL-SDR"走到"出图/出报文"。

## 用法

```bash
# 自检（无设备也能跑，诚实报无硬件）
python3 tools/onboarding/onboard.py --step detect

# 全链：ADS-B 飞机报文
python3 tools/onboarding/onboard.py --step all --freq 1090e6 --mode adsb

# 全链：NOAA APT 云图
python3 tools/onboarding/onboard.py --step all --freq 137.5e6 --mode apt --n 4.8e6

# 机器可读 JSON
python3 tools/onboarding/onboard.py --step detect --json
```

## 分步

| 步骤 | 做什么 |
|---|---|
| `detect` | 复用 hw_selfcheck，检测 RTL-SDR 是否在线 |
| `capture` | rtl_sdr 起流写 uint8 interleaved IQ |
| `record` | uint8 → SigMF cf32_le（meta+data，与 playback.py 对齐） |
| `decode` | mbdsdr_ai 真实解码器（adsb / ax25 / cw / apt） |
| `output` | 报文转储 + 频谱 PNG + manifest（全标口径/参数/时间戳） |

默认 `--step all`，detect 失败即停（绝不静默跳过、绝不 mock）。

## 支持模式

| mode | 频率 | 说明 |
|---|---|---|
| `adsb` | 1090 MHz | 飞机 ADS-B 报文（CRC-24 校验） |
| `apt` | 137.5 MHz | NOAA 气象卫星 APT 云图 |
| `cw` | 任意 HF | 莫尔斯电报 |
| `ax25` | 144.39 MHz | APRS 位置报文（FCS-16 校验） |

## 回传与解析（真机结果贴回聊天）

真机跑两条 `--json` 命令后把输出整段贴回，云侧即可得到结构化结论：

```bash
# 真机上跑这两条（都要带 --json）
python3 tools/hw_selfcheck/selfcheck.py --json
python3 tools/onboarding/onboard.py --step all --freq 1090e6 --mode adsb --json

# 云侧 / 本地解析贴回的文本（支持混杂了人类对话的多段 JSON）
python3 tools/onboarding/parse_hw_report.py report.txt        # 文件
cat report.txt | python3 tools/onboarding/parse_hw_report.py # stdin
python3 tools/onboarding/parse_hw_report.py report.txt --json # 额外吐结构化 JSON
```

解析器输出：设备（有/无、tuner、丢包）、声卡、GNSS 串口（NMEA 判定）、依赖缺失、
onboard 各步结果与产物清单，以及**与检测事实挂钩的后续可做步骤建议**；找不到任何
JSON 时以退出码 2 报明确错误，绝不崩。

- 字段逐项说明与完整输出示例：见 `docs/learn/phase6/P2-hw-report-format.md`
- 解析器测试：`python3 -m pytest tools/onboarding/test_parse_hw_report.py -v`

## 录制产物回填为论文图（recorded 口径）

`onboard` 的 `record` 步写出标准 SigMF（`.sigmf-data` + `.sigmf-meta`）、`decode` 步写出
`*_messages.json`。拿到这个产物目录后，把它交给回填脚本即可出 recorded 口径图/CSV/manifest：

```bash
python3 experiments/exp_ota_run.py --recordings-dir paper/experiments/onboarding_<mode>_<时间戳>/
```

- `parse_hw_report.py` 只解析 **JSON 日志**（告诉你产物目录在哪、设备是否正常）；
  `exp_ota_run.py` 才**真读目录里的 IQ 波形**重算指标。
- 产出：解码成功率 vs 估算 Eb/N0（Wilson CI）、AMR 预测、多普勒观测数；SNR 未标定、
  需真机校准；无录制时诚实空态 N=0、退出码 0。
- 完整三步说明见 `docs/learn/phase8/P1-ota-backfill.md`。

### 解析输出示例（真跑）

贴回一段**混了人类对话、含两段 JSON（selfcheck + onboard）**的聊天文本后，解析器
自动抽出两段并输出（字段为真跑输出，非手写）：

输入（节选）：`...selfcheck 输出 {…PASS:6…} … onboard 输出 {…mode=adsb…} …谢谢！`

```text
====================================================================
MBDSDR 真机回传 · 云侧解析结论
====================================================================
共从贴回文本中识别到 2 个 JSON 对象：selfcheck=有，onboard=有

── SDR 设备 ──────────────────────────────
  • 检测到设备 0bda:2838 — RTL2838UHIDIR (目标真机) (RTL2838UHIDIR)
  • tuner：Rafael Micro R820T（增益 29 档）
  • 3 秒实读丢包：0 字节（流读状态=PASS）
  • udev 规则：已装 ['/etc/udev/rules.d/99-rtlsdr.rules']

── 声卡 ────────────────────────────────
  状态=PASS  播放卡=1  录音卡=1  /dev/snd=存在

── GNSS 串口 ────────────────────────────
  状态=PASS  候选串口=['/dev/ttyUSB0']
  NMEA 判定：读到 $-开头 NMEA 语句

── 依赖 ─────────────────────────────────
  状态=PASS
  python rtlsdr=OK  SoapySDR=OK  gpsd=/usr/sbin/gpsd

── Onboard 分步结果 ─────────────────────
  mode=adsb  freq=1090000000.0  sr=2400000.0  n=240000  gain=24.0dB
  [PASS] step=detect: 检测到 RTL-SDR 设备
  [PASS] step=capture: 采集完成
        写入 480,000 字节，丢包 0 字节
  [PASS] step=record: SigMF 录制完成
        SigMF：240,000 样本，时长 0.100s
  [PASS] step=decode: ADS-B 解码完成，3 有效帧
        解码有效帧：3
  [PASS] step=output: 产物落盘完成：4 个文件
  产物清单（4）：
    - /x/a_messages.txt
    - /x/a_messages.json
    - /x/spectrum.png
    - /x/manifest.json

── 后续可做步骤建议 ──────────────────────
  1. 【设备就绪】selfcheck 第 1/2/3 项通过 → 可直接跑：python3 tools/onboarding/onboard.py --step all --freq 1090e6 --mode adsb
  2. 【全链路通过】产物已落盘（4 个），目录：/x。
====================================================================
```

> 完整输入文本（两段 JSON 全字段）、`--json` 结构化结论节选、以及**无设备诚实空态**
> （只报 `target_hits 为空` / `SKIP`、不臆造设备）的输出，见
> `docs/learn/phase6/P2-hw-report-format.md` §6。序列号只回显前 3 位 + `***`，
> 不原样暴露。

## 测试

```bash
python3 -m pytest tools/onboarding/test_onboarding.py tools/onboarding/test_parse_hw_report.py -v
```

离线确定性测试：注入合成 IQ / 假设备检测结果，验证：
- 无设备时各步明确失败、不 mock
- SigMF meta+data 写入自洽（能被 IQPlayback 解析回读）
- decode 步对固定 IQ 输入的确定性输出
- output 步产物含口径/参数/时间戳

## 详细文档

见 `docs/learn/phase5/P1-onboarding.md`。
