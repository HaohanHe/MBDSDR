# librtlsdr 真实硬件参数移植笔记

> 本笔记记录从 `repos/librtlsdr` **C 源码**（非 README）逐条核对后，移植进
> `mbdsdr_ai/rtlsdr_params.py` 的真实驱动参数。每条都标注 `文件:行号`。
> 红线：增益表是 `rtlsdr_get_tuner_gains()` 里的**真实离散档**，不是按物理量程线性铺开的假数据。

## 1. 数据源（实际读过的源文件）

| 文件 | 作用 |
|---|---|
| `repos/librtlsdr/src/librtlsdr.c` | 主驱动：增益表、采样率区间、ppm、直采、AGC、异步读 |
| `repos/librtlsdr/src/tuner_e4k.c` | E4000 调谐器：频率范围、LNA/IF 增益级 |
| `repos/librtlsdr/src/tuner_r82xx.c` | R820T/R828D：LNA/Mixer/VGA 步进、频段划分 |
| `repos/librtlsdr/src/tuner_fc0012.c` | FC0012 增益档位 |
| `repos/librtlsdr/include/rtl-sdr.h` | API、枚举、采样率/直采/异步语义 |
| `repos/librtlsdr/src/convenience/convenience.c` | `nearest_gain()` 等便捷函数 |

## 2. 增益表（0.1 dB → dB）

来源：`librtlsdr.c:956-1008` `rtlsdr_get_tuner_gains()`。
C 注释明确：*"all gain values are expressed in tenths of a dB"*（`:958`），
`rtl-sdr.h:197` 也写明 `115 means 11.5 dB`。

| 调谐器 | C 数组位置 | 档数 | 最小档 | 最大档 |
|---|---|---|---|---|
| E4000 | `librtlsdr.c:959-960` | **14** | -1.0 dB | 42.0 dB |
| FC0012 | `librtlsdr.c:961` | **5** | -9.9 dB | 19.2 dB |
| FC0013 | `librtlsdr.c:962-964` | **23** | -9.9 dB | 19.7 dB |
| R820T | `librtlsdr.c:966-969` | **29** | 0.0 dB | 49.6 dB |
| R828D | `librtlsdr.c:991-993` | 同 R820T（共用 `r82xx_gains`） | 0.0 dB | 49.6 dB |

### 为什么不是「E4000 53 档 / R820T 63 档」？

常见说法把物理量程当成了线性步进表：
- E4000 物理量程约 -10 ~ +42 dB；
- R820T 物理量程 0 ~ +49.6 dB，VGA 步进约 0.8 dB。

但 `rtlsdr_get_tuner_gains()` 实际只返回驱动**暴露的离散档**：
- R820T 的档是 LNA + Mixer 步进累加的结果（`tuner_r82xx.c:1007-1017`
  循环累加 `r82xx_lna_gain_steps` / `r82xx_mixer_gain_steps`，
  步进表见 `:967-977`），并不连续；
- E4000 的 14 个档是混频/IF 级组合出的点。

因此本移植忠实于 C 驱动的真实返回值（14 / 29 档），物理量程只作为
`gain_min_db / gain_max_db` 端点记录。要把目标增益落到真实可设档位，
用 `rtlsdr_params.nearest_gain()`（移植自 `convenience.c:116-141`）。

## 3. 频率范围

| 调谐器 | 范围 | 来源 |
|---|---|---|
| E4000（默认规格） | 64 ~ 1700 MHz | `tuner_e4k.c:351-352` |
| E4000（OUT_OF_SPEC 编译） | 50 ~ 2200 MHz | `tuner_e4k.c:347-349` |
| R820T / R828D | 24 ~ 1766 MHz | `tuner_r82xx.c:1168`（HF≤28.8 / VHF 28.8-250 / UHF>250） |
| FC0012 / FC0013 | 22 ~ 948.6 MHz | Fitipower 公开范围 |

直采模式（Direct Sampling）下 `center_freq` 改控 DDC 的 IF，可直采
**0 ~ 28.8 MHz**（`rtl-sdr.h:296-305`），低于 24 MHz 的短波靠这条路径。

## 4. 采样率

来源：`librtlsdr.c:1100-1101`：

```c
if ((samp_rate <= 225000) || (samp_rate > 3200000) ||
    ((samp_rate > 300000) && (samp_rate <= 900000)))
    return -EINVAL;
```

即合法区间是两段：**(225000, 300000] ∪ (900000, 3200000]**，
`300k ~ 900k` 是死区（驱动直接拒绝）。`rtl-sdr.h:260-263` 补充：
> 225001-300000 Hz / 900001-3200000 Hz；超过 2.4 MSPS 会丢采样。

推荐档（`rtlsdr_params.SUPPORTED_SAMPLE_RATES`）：
250k、1.024M、1.536M、1.8M、1.92M、2.0M、2.048M、2.4M、2.56M、3.2M。

## 5. 其它硬件控制语义

- **数字 AGC（RTL2832 内部）**：`librtlsdr.c:1157-1163`，demod 寄存器 `0x19`，
  开 `0x25` / 关 `0x05`。与 tuner 前端 AGC（`set_tuner_gain_mode`，
  `librtlsdr.c:1073`）相互独立。
- **ppm 频偏校正**：`librtlsdr.c:915-938` `rtlsdr_set_freq_correction(dev, ppm)`，
  整数 ppm，会同时校正采样率并重新锁相。廉价棒典型 20~50 ppm。
- **直采模式**：`librtlsdr.c:1165-1226`，`on=0` 关 / `1`=I-ADC / `2`=Q-ADC。
- **异步读取**：`rtl-sdr.h:340,369-373`，回调签名
  `void(*)(unsigned char *buf, uint32_t len, void *ctx)`；
  `buf_num` 默认 15，`buf_len` 默认 `16*32*512 = 262144`（须为 512 的倍数）。

## 6. Python 侧接口（`mbdsdr_ai/rtlsdr_params.py`）

```python
from mbdsdr_ai import rtlsdr_params as rp

rp.get_gain_table("R820T")        # -> [0.0, 0.9, ..., 49.6] （29 档 dB）
rp.get_frequency_range("E4000")   # -> (64e6, 1700e6)
rp.get_supported_sample_rates()   # -> [250000, ..., 3200000]
rp.is_valid_sample_rate(600_000)   # -> False（死区）
rp.nearest_gain("R820T", 49.0)    # -> 49.6（吸附到最近真实档）
rp.get_tuner_summary("FC0013")    # -> 完整摘要 dict
```

`RTLSDRBackend`（`sdr_backend.py`）在 `connect()` 探测到调谐器型号后载入对应
增益表，`_apply_gain()` 先吸附到最近离散档再下发（对齐 `convenience.c:116`）。

## 7. 已注册工具（ToolRegistry）

- `rtlsdr_list_gains(tuner)` — 列出真实离散增益档
- `rtlsdr_get_freq_range(tuner)` — 频率范围 + 采样率合法区间
- `rtlsdr_set_params(tuner, gain_db, sample_rate_hz)` — 吸附到真实可设档位

## 8. 验证

`python3 -m pytest tests/rtlsdr_params_test.py -q`
覆盖：各调谐器增益档数与端点、频率范围、采样率区间/死区、`nearest_gain`、
以及无设备时 `RTLSDRBackend.list_devices()` 返回空列表不崩溃。
