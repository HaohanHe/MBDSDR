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

## 测试

```bash
python3 -m pytest tools/onboarding/test_onboarding.py -v
```

离线确定性测试：注入合成 IQ / 假设备检测结果，验证：
- 无设备时各步明确失败、不 mock
- SigMF meta+data 写入自洽（能被 IQPlayback 解析回读）
- decode 步对固定 IQ 输入的确定性输出
- output 步产物含口径/参数/时间戳

## 详细文档

见 `docs/learn/phase5/P1-onboarding.md`。
