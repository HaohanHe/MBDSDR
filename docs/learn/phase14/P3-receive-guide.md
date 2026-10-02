<!--
SPDX-License-Identifier: MIT
Phase14 P3 接收步骤手册：SSTV / SSDV 两路径
-->

# P3 接收手册：从插设备到出图（SSTV / SSDV 两路径）

> 本手册命令/接口均**来自真实代码**（已逐行核对，不臆造）：
> - SSTV：`tools/onboarding/onboard.py --mode sstv`、`mbdsdr_ai/sstv_decoder.py`；
> - SSDV：`mbdsdr_ai/ssdv_decoder.py`（P2 已交付的**库接口**，见路径 B）。
> **诚实边界**：SSDV 目前只有**库**（无独立 CLI），`onboard.py` 的 `ssdv` 模式在
> Wave2 追加（当前 MODES 尚无 `ssdv`）。故路径 B 给的是**真实可调用的库函数**，
> 不是待补的占位命令；端到端真机录制→SSDV 出图的一条命令等 Wave2 onboard 模式落地。

---

## 0. 准备（两路径共用）

**硬件**
- RTL-SDR 接收机（RTL2832U，0bda:2838/2832），插 USB 2.0 口（避免 USB 3.0 Hub）；
- VHF/UHF 天线：活动频率在 2 m（144 MHz）/ 70 cm（435 MHz）段，配对应八木/棒状天线，
  按预测方位大致对准卫星过境方向（`pass_predict.py` 给 AOS/LOS 方位）。

**软件**
- `rtl_sdr` 命令在 PATH（`which rtl_sdr` 能找到；否则 `apt install rtl-sdr` 或编 librtlsdr）；
- Python 依赖：`pip install -r requirements.txt`（含 numpy/scipy/skyfield/sgp4/Pillow）。

**预测先行**
- 先按 `P3-event-params.md` 录入 TLE + 频率，跑 `pass_predict.py` 拿到本次过境的
  UTC 时刻、中天仰角、多普勒范围，再按下文对准频率。**没录 TLE 不要盲收**。

---

## 路径 A：SSTV（模拟慢扫描，当前已可真机闭环）

真实链路（`onboard.py` `--mode sstv`）：
```
rtl_sdr 采集 uint8 I/Q
  → record: 转 cf32_le SigMF（带 core:datetime / time_source 旁证）
  → decode: FM 解调(max_dev=5 kHz) → 重采样到 48000 Hz
            → decode_sstv_from_samples(audio, 48000, png, mode="auto")
  → output: 拷成 sstv_image__captured__N…__<日期>.png + 频谱图 + manifest
```

### A-1 检测设备

```bash
python3 tools/onboarding/onboard.py --step detect
```
- **预期**：`[PASS] step=detect 检测到 RTL-SDR 设备`，列出 USB `0bda:2838/2832`。
- **失败对照**：
  - `未检测到 RTL-SDR 设备` → 重插 USB；`lsusb | grep -E '0bda:(2838|2832)'` 复核；
  - 有设备但打不开 → `sudo modprobe -r dvb_usb_rtl28xxu`（卸掉占用的内核驱动）。

### A-2 一条命令全链采集→出图（推荐）

```bash
python3 tools/onboarding/onboard.py --step all \
    --freq <官方下行Hz> --mode sstv \
    --sr 250000 --n 30000000 --gain 24 \
    --out-dir ./phase14_rx
```
- 参数（均为 onboard.py 真实 CLI）：
  - `--freq`：**官方发布的下行频率，单位 Hz**（如 145.800e6；未发布前不要跑真机）；
  - `--mode sstv`：内置该模式，默认采样率 250 ksps、默认 30,000,000 样本（≈120 s，
    覆盖 Martin M1 整帧 ~114 s）；
  - `--sr 250000 --n 30000000`：可不写（用 sstv 默认值），显式写便于复核；
  - `--gain 24`：增益 dB，弱信号可逐步加大；
  - `--out-dir ./phase14_rx`：产物目录；
  - 可选 `--gnss <nmea.log>`：同期 GNSS NMEA 日志，合法 RMC/ZDA 会把 GNSS UTC 写进
    SigMF 并标 `time_source=gnss`；否则诚实退化为 `time_source=system`。
- **预期**：detect→capture→record→decode→output 全 `[PASS]`，产物目录里出现
  `sstv_image__captured__N…__<日期>.png`（成功解出的图）+ 同名 SigMF 三件套 + manifest。
- **失败对照**：
  - capture `未写入任何数据 / 文件为空`：设备被占（rtl_tcp/gqrx/dump1090），关掉再跑；
  - capture `字节数偏差过大`：丢包，降采样率或换 USB 2.0 口；
  - decode `SSTV 未解码出图像`：
    1) 录制没覆盖完整一帧（至少录满 ~120 s，整段过境都可录）；
    2) **频率/多普勒没对准**——按 `pass_predict.py` 的多普勒范围偏调中心频率，或开补偿（A-4）；
    3) 仰角太低（<10°）/天线没对准；
    4) 增益不当导致音频削波或太弱。

### A-3 离线：把已录音频/WAV 解出图（回放已有数据）

若你手里已经有一段 SSTV 音频 WAV（或别的软件录的），直接用库函数：

```python
from mbdsdr_ai.sstv_decoder import decode_sstv
res = decode_sstv("recorded.wav", "out.png", mode="auto")
# res: {"success": True, "mode": "Martin M1", "width":320, "height":256, ...}
```
- 真实签名（已核对 `sstv_decoder.py`）：`decode_sstv(file_path, output_path=None, mode="auto")`。
- `mode` 可选：`"auto"`（自动识别）/ `"Martin M1"` / `"Martin M2"` / `"Scottie S1"` /
  `"Scottie S2"` / `"Robot 36"`（另有 PD 系列按 VIS 自动识别）。
- **失败对照**：`{"error": "..."}` → 多半是音频采样率/时长不对；WAV 会被
  `_resample_if_needed` 统一到 48000 Hz（`TARGET_SAMPLE_RATE=48000`）。

### A-4 多普勒补偿（FM SSTV 建议开）

> ⚠️ 现状诚实说明：当前 `onboard.py --mode sstv` 的 decode 分支**尚未内嵌**
> NCO 补偿（直接 FM 解调）。要开补偿，用下面任一方式：
- **方式一（SDR 端跟频）**：若你的接收软件（SDR#/GQRS/SDRangel 等）支持 AFC/多普勒
  自动跟频，直接打开；`pass_predict.py` 给出的 AOS/LOS 频移就是手动偏调的参考。
- **方式二（离线预补偿 IQ）**：先用 `doppler_compensation.remove_doppler_shift` 把
  `compute_doppler_curve` 算出的逐时刻频移逆掉，再喂给 decode：

```python
from mbdsdr_ai import doppler_compensation as dc
from mbdsdr_ai.sat_passes import compute_doppler_curve   # 由 predict_passes 得 pass_
# doppler_curve = compute_doppler_curve(tle_lines, gs, pass_, freq_hz)
# 把曲线上 (time, doppler_hz) 插值成采样时刻的 callable，再：
# iq_comp = dc.remove_doppler_shift(iq, fs, doppler_hz_callable)
```
- `remove_doppler_shift(iq, fs, doppler_hz)`：`doppler_hz` 支持标量 / 逐样本数组 /
  `callable(t_seconds)->Hz`（正好喂多普勒曲线）；给 0 = 不补偿（诚实空态）。

---

## 路径 B：SSDV（数字慢扫描）

> **现状**：P2 已干净室交付 `mbdsdr_ai/ssdv_decoder.py`（**库**，只学公开协议、
> 不抄 GPL 实现）；但它是库函数、无 CLI，`onboard.py --mode ssdv` 是 Wave2 追加项。
> 下面给的是 P2 **真实接口**（已核对源码），按此把「解调字节 → 图像」串起来即可。

### B-1 物理链路（对接现有零件）

```
rtl_sdr 采集下行 IQ（同 onboard record 步，已可录）
  → NFM 解调（mbdsdr_ai.demod_nfm / analog_demod，已有）
  → 4FSK/GMSK 解调 → 得同步后的解调字节流
  → SsdvDecoder.feed(bytes)        # 字节流同步 + RS(255,223) 纠错 + CRC 把关 → 256 字节包
  → ImageReassembler.add(pkt)     # 按 (image_id, packet_id) 收包
  → reassembler.reassemble()       # 按 packet_id 升序拼扫描流（缺包跳空）
  → wrap_jpeg(scan, prefix_header) # restuff + 加 SOI..SOS 头 + EOI → 可解码 JPEG
```

### B-2 真实库接口（已核对 `ssdv_decoder.py`）

```python
from mbdsdr_ai.ssdv_decoder import (
    SsdvDecoder, ImageReassembler, wrap_jpeg,
)

dec = SsdvDecoder()
pkt_list = dec.feed(demodulated_bytes)     # 喂入解调字节，切出完整 256B 包
reass = ImageReassembler()
for pkt in pkt_list:
    reass.add(pkt)
scan = reass.reassemble()                   # 缺包见 reass.missing()
jpeg = wrap_jpeg(scan, prefix_header)      # prefix_header = SOI..SOS 标准段
# 把 jpeg 写盘即为最终图像
```
- 关键方法（真实签名）：`SsdvDecoder.feed(data: bytes)->List[SsdvPacket]`；
  `SsdvDecoder.correct_packet(chunk: bytes)->Optional[SsdvPacket]`；
  `ImageReassembler.add(pkt)` / `.reassemble()->bytes` / `.missing()->List[int]`；
  `wrap_jpeg(scan, prefix_header, eoi=b"\xff\xd9")`。
- RS 纠错复用 `mbdsdr_ai/fec.py` ReedSolomon（SSDV 标准 nsym=32/fcr=112）。

### B-3 当前可做的实事

1. **先把下行录下来**：用 `onboard.py --mode apt` 或直接 `rtl_sdr` 把 SSDV 下行的
   IQ 录成 SigMF/WAV 存证（频率用官方发布值）。录制步骤与 A-1/A-2 相同。
2. **解调→字节**：接 NFM/GFSK 数字零件，输出字节流；再按 B-2 喂 `SsdvDecoder`。
3. **Wave2 落地后**：`onboard.py --mode ssdv` 会把 B-1/B-2 串成一条命令（届时补
   一条真实 CLI 命令到此处）。
4. SSDV 对频偏比 SSTV 更敏感，**务必开多普勒补偿（A-4）+ AFC 跟频**。

---

## 3. 出图后下一步

1. 核对图像清晰、完整（SSTV 无明显错位/条纹；SSDV 无大块马赛克）；
2. 记录该图对应的 **接收 UTC 时间、卫星、仰角、频率**（抄 `pass_predict.py --json`）；
3. 按 `P3-email-checklist.md` 备齐材料，发邮件到 **CASC70@asesspace.com** 换证书。
