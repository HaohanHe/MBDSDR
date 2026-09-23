# R2 深度代码审查 — `mbdsdr_ai/decoders.py`（解码调度/分发器）

- 审查对象：`/home/user/Doubao/chats/38438160041798146/mbdsdr_ai/decoders.py`（829 行）
- 审查方式：只读，通读全文 + 跨文件核对后端真实接线（adsb.py / ax25.py / sstv_decoder.py / ft8_lite.py / noaa_apt_lite.py / orbit.py / sdr_tools.py）
- 结论速览：**本文件并非"全模式中央调度器"，它只是一个 4 模式（adsb/ft8/aprs/wspr）的窄分发器，且 4 个里只有 adsb 真正调到后端；FT8/APRS/WSPR 在本层均为 note-only 占位。** BPSK31/RTTY/PSK63/Olivia/Hellschreiber/JT65/JT9/FT4 在本文件中**连名字都没出现**，不存在"注册了但空壳"，而是根本未注册。

---

## 0. 重要定性：这里没有"模式注册表"

本文件内**不存在**一个 `MODE_REGISTRY` / `HANDLERS = {...}` 之类的全模式分发表。唯一近似注册表的是 `decode_digital_mode()` 内的 `external_tools` 字典：

```
decoders.py:746-751
  external_tools = {
      "ft8":  ["jt9", "wsjtx"],
      "wspr": ["wsprd", "wsjtx"],
      "aprs": ["direwolf", "aprs"],
      "adsb": ["dump1090", "dump1090-fa"],
  }
```

它只列了 **4 个模式**，且含义仅是"该模式可选外部二进制清单"，不是编解码后端表。`decode_digital_mode()` 内的 `if/elif` 分支也只覆盖 `adsb`(776)、`ft8`(798)、`aprs`(803) 三个，**wspr 没有分支**，其余模式全部落到兜底 note（809-813）。

---

## 1. 已注册模式 → 后端实现状态 对照表

| 模式 | 是否在本文件出现 | 分发分支 | 真正调用的后端 | 后端是否真存在 | 状态 |
|---|---|---|---|---|---|
| **adsb** (Mode-S DF17) | ✅ | `decoders.py:776` | `from .adsb import decode_baseband`（778）→ `decode_baseband(iq, fs=...)`（788） | ✅ `adsb.py:185 decode_baseband(iq, fs=4e6)` | **[真] 真解码** |
| **noaa_apt** | ✅（独立函数 300） | `decode_noaa_apt` 内部 | `from .noaa_apt_lite import decode_apt, save_apt_png`（323） | ✅ `noaa_apt_lite.py` | **[真] 真解码**（含 IQ→FM 鉴频→重采样） |
| **sstv** | ✅（独立函数 524） | `decode_sstv` 内部 | **无**（本函数内只做 `np.abs(audio)` 包络，575） | 真后端 `sstv_decoder.py:737 decode_sstv(...)` **存在但本函数不调用** | **[空壳] 本函数是包络假图**（见 §3.2） |
| **ft8** | ✅ | `decoders.py:798` 仅 `result["note"]=...` | **无**（不 import ft8_lite） | 真后端 `ft8_lite.py:121 decode_ft8_audio` / `analyze_ft8_audio` 存在 | **[占位] 本层 note-only**（真路径在 sdr_tools 旁路） |
| **aprs** | ✅ | `decoders.py:803` 仅 `result["note"]=...` | **无**（不 import ax25） | 真后端 `ax25.py:1024 parse_ax25_from_audio(audio_path)` 存在 | **[空壳/未接线] + 字段永远空 [真bug]**（见 §2.2） |
| **wspr** | ⚠️ 仅在 external_tools(748) | **无分支**，落兜底 note | 无（wsprd 只探测不执行） | 无内置实现 | **[挂名/占位]**（见 §2.3） |
| **fhss**（跳频检测） | ✅（独立函数 612） | 内部 FFT 峰值跟踪 | 自包含 numpy | — | **[真·基础版]**（见 §3.3） |
| **BPSK31** | ❌ 全文 0 命中 | 无 | 无 | 无 | **未注册 / 无后端** |
| **RTTY** | ❌ 全文 0 命中 | 无 | 无 | 无 | **未注册 / 无后端** |
| **PSK63** | ❌ 全文 0 命中 | 无 | 无 | 无 | **未注册 / 无后端** |
| **Olivia** | ❌ 全文 0 命中 | 无 | 无 | 无（仅 `subagents.py:108` 宣传文案提及） | **未注册 / 无后端** |
| **Hellschreiber** | ❌ 全文 0 命中 | 无 | 无 | 无 | **未注册 / 无后端** |
| **JT65** | ❌ 全文 0 命中 | 无 | 无 | 无 | **未注册 / 无后端** |
| **JT9** | ❌ 全文 0 命中 | 无 | 无 | 无 | **未注册 / 无后端** |
| **FT4** | ❌ 全文 0 命中 | 无 | 无 | 仅 `digital_modes.py` 参数表，无编解码 | **未注册 / 无后端** |

> 结论（回应审查重点 2、7）：**BPSK31/RTTY/PSK63/Olivia/Hellschreiber/JT65/JT9/FT4 在 decoders.py 里既没注册名字、也没后端**——不是"注册了名字的空壳"，而是压根没接进来。这与上一轮 `03_digital_modes.md` 的发现一致：digital_modes.py 里这些模式也只有参数字典、无编解码。两边都没有挂名空壳，但产品宣传（`subagents.py:108`、白皮书 981 行）声称支持，属于**文档/宣传超前于实现**。

---

## 2. 调度层的硬伤

### 2.1 [真bug] 外部工具"只探测、从不执行"
`decoders.py:754-759` 用 `shutil.which(tool)` 仅判断二进制是否存在，全文 **无任何 `subprocess` / `os.system` / `Popen` / `check_output`**（已 grep 确认）。因此：
- 即使本机装了 `jt9` / `wsprd` / `direwolf` / `dump1090`，分发器也**只是把名字塞进 `available_tools` 然后继续走内置/兜底路径**，从不真正调用它们解码。
- `decoders.py:809-811` 还会输出 `"检测到外部工具 jt9，可用于完整解码"` —— **这句承诺是假的**：检测到了但代码路径里没有任何一行去跑它。
- 影响：ADS-B 除外（内置 numpy 真解），其余模式无论环境装没装外部工具，调度层都拿不到真解码结果。

### 2.2 [空壳 + 真bug] APRS：声称有 ax25 能力，但既不调用 ax25，又读取永远为空的字段
- 分发分支 `decoders.py:803-807` 只写 note："内置 ax25.py 已具备 FM 鉴频+PLL+CRC-16 能力"，**但本文件从未 `import ax25`、从未调用 `ax25.parse_ax25_from_audio`**（grep 确认）。
- 真后端确实存在：`ax25.py:1024 parse_ax25_from_audio(audio_path)`、`ax25.py:307 class AFSKModem`。
- 工具层 `sdr_tools.py:3217` 调 `decode_digital_mode(input_path, mode="aprs")` 后，在 `sdr_tools.py:3224` 读 `result.get("decoded_frames", [])` 打印——**而 `decode_digital_mode` 全程从未给 result 赋过 `decoded_frames` 键**（已核对 765-829，只有 found/crc_ok/icao/callsign/raw_hex/preamble_index/spectral_*）。结果：APRS 工具永远打印 `结果: []`，并说"direwolf 未安装，无法真解码"——即使 ax25.py 本地就能解。
- 分类：**[空壳]**（调度未接真后端）+ **[真bug]**（工具层读取一个永远不存在的字段，输出恒为空列表，误导用户以为没信号）。

### 2.3 [挂名/占位] WSPR：在 external_tools 里挂了名，但连分支都没有
- `decoders.py:748` 把 `wsprd` 列进 external_tools，但 `decode_digital_mode` 的 if/elif 链里**没有 `elif mode == "wspr"`**（776 adsb / 798 ft8 / 803 aprs 之后直接到 809 兜底）。
- 调用 `mode="wspr"` 会走到 `decoders.py:809-813`：因 wspr 在 external_tools 里，`available_tools` 可能非空，于是打印 `"检测到外部工具 wsprd，可用于完整解码"`——**同样从不执行 wsprd**。
- 分类：**[挂名]**（名字进了表、有"可解码"提示，但无内置分支、外部二进制也不被调用）。

### 2.4 [占位] FT8：分发器 note-only，真解码被旁路到工具层
- `decoders.py:798-802` 只写 note，不 import ft8_lite。
- 真路径在 `sdr_tools.py:3188 _decode_ft8` → `ft8_lite.analyze_ft8_audio`（找音峰+8FSK 硬判决，呼号还原仍需 jt9，这点诚实）。
- 分类：作为"中央调度器"，FT8 分支是 **[占位 note]**；真解码绕过分发器由工具层直连后端。调度器名不副实。

---

## 3. 本文件内部函数逐个体检

### 3.1 [真] 卫星轨道 `compute_satellite_position`（115-221）
- sgp4 真解算（146 `satellite.sgp4(jd, fr)`），TLE 优先联网 `orbit.fetch_tle(catnr)`（81，签名 `orbit.py:74 fetch_tle(catnr, max_age_hours=24)` 匹配），失败回退内置 BUILTIN_TLE。
- **[真bug / 真机红] 多普勒恒为 0**：`decoders.py:199 radial_velocity = 0  # km/s，简化为 0`，于是 202 行 `doppler = freq_hz * 0 / c = 0`，`SatellitePass.doppler_hz` 永远是 0。sgp4 在 146 行明明算出了速度向量 `v`，却被丢弃（`e, r, v = ...` 之后 v 再未使用）。真机过境时 LO 不做多普勒跟踪，信号会漂出 ~10–30 kHz 音频带宽 → **实验室单看仰角/方位是绿的，真机收不到信号**。
- **[占位] `compute_doppler_correction`（252-293）**：用 `max_doppler*cos(elev)*0.5`（279）拍脑袋近似，自己在 292 行 note 承认"需要卫星速度向量"。未与真实 `v` 向量做视线投影。
- **[建议] ECI→观测点坐标系忽略地球自转**（155 注释自述），长过境仰/方位有漂移，短过境可接受。

### 3.2 [空壳] `decode_sstv`（524-605）
- 自述"简化版"，实际做法是 `envelope = np.abs(audio)`（575）后把包络重采样成 320×256（577-580），**没有任何 1200 Hz 同步检测、没有 FM 频率解调、没有频率→亮度映射**（570-572 注释自己承认缺这三步）。产出的是"音频包络蒙在一张图上"，不是 SSTV 云图。
- 真后端 `sstv_decoder.py:737 decode_sstv(file_path, output_path, mode)`（支持 Martin M1/Scottie/Robot36/PD）**存在但本函数不 import**。
- 缓解：工具层 `sdr_tools.py:3124-3140` 优先 `from .sstv_decoder import decode_sstv as decode_sstv_new`，异常才回退到本假函数（3143）。故**线上主路径是真后端**，本函数仅兜底；但它仍被 `__init__.py:50` 对外导出，一旦有人绕过 sdr_tools 直接调 `decoders.decode_sstv`，拿到的就是假图。分类 **[空壳]（兜底路径）**。

### 3.3 [真·基础版] `detect_fhss`（612-713）
- 自包含：分帧 FFT + 单峰跟踪 + 跳变阈值（669-676）+ 聚类唯一频点（688-691）。逻辑闭环、参数齐全。
- 局限：每帧只取 `argmax` 单峰（662），多信号并存时会漏；阈值 `-60 dB` 是绝对值假设，未做噪声底归一。**能跑通、非空壳**，但属基础演示级，标注 **[建议]** 增强。

### 3.4 [真] `decode_noaa_apt`（300-376，活动段）
- 真路径：wav 直接读（326-334）或 IQ 经 `FileIQBackend`→宽带 FM 鉴频（353）→Butterworth 低通（354）→重采样 24 kHz（356-359）→`noaa_apt_lite.decode_apt(audio, a_sr, min_lines=8)`（363）。物理参数注释（310-312）正确（2 行/秒、2080 样本/行、sync 39+space 909）。
- **[占位/死代码] 378-517 行**：被 `if False:`（379）包裹的旧实现（AM 包络、10 行/秒、20800 Hz 等错误物理假设），永不执行，代码自带注释"勿用"。建议清理，避免误读。
- `decoders.py:304 sample_rate=20800` 默认参数只在死代码块（400）用到；真路径从 `FileIQBackend.get_sample_rate()` 取真值（339），**不构成真机硬编码风险**。

---

## 4. "实验室绿、真机红"专项（硬编码采样率/中心频率）

| 项 | 位置 | 性质 |
|---|---|---|
| 多普勒恒 0，`SatellitePass.doppler_hz=0` | `decoders.py:199-202` | **[真bug] 真机过境 LO 不跟踪多普勒 → 收不到** |
| 多普勒修正用 `cos(elev)*0.5` 经验拍脑袋 | `decoders.py:279` | [占位] 近似，未用真实速度向量 |
| ECI→观测点忽略地球自转 | `decoders.py:155` | [建议] 短过境可接受 |
| `decode_digital_mode` 默认 `sample_rate=1e6` | `decoders.py:724`；工具层 `sdr_tools.py:3235` 同默认 | ADS-B 真录音频常 2–4 MHz；782 行有"fs 须 1MHz 整数倍"告警兜底，**不会静默错**，但默认 1 MHz 偏低 |
| 中心频率 | `detect_fhss` 由参数 `center_freq` 传入（615/659），noaa/adsb 均无硬编码本振偏移 | ✅ 未发现硬编码中心频偏 |

---

## 5. 与各解码模块接口的参数匹配核对

| 调用点 | 实际传参 | 后端签名 | 匹配 |
|---|---|---|---|
| `decoders.py:788` adsb | `decode_baseband(iq, fs=float(sample_rate))`，iq=c64 由 cf32 交错拼成（779-780） | `adsb.py:185 decode_baseband(iq, fs=4e6)` | ✅ 匹配；782 行校验 fs/1e6 为整数 |
| `decoders.py:363` noaa | `decode_apt(audio, a_sr, min_lines=8)` | `noaa_apt_lite.decode_apt`（活动段读取 apt_present/lines_aligned/lock_ratio/image_a/b） | ✅ 键一致 |
| `decoders.py:81-82` tle | `fetch_tle(catnr)` 返回 (l1,l2) | `orbit.py:74 fetch_tle(catnr, max_age_hours=24)->Tuple[str,str]` | ✅ 匹配 |
| aprs → ax25 | **根本没调用** | `ax25.py:1024 parse_ax25_from_audio(audio_path)` | ❌ 未接线 |
| ft8 → ft8_lite | **调度层根本没调用**（工具层 sdr_tools:3203 才调） | `ft8_lite.py:121 decode_ft8_audio` / `:112 analyze_ft8_audio` | ❌ 调度层未接线 |
| sstv → sstv_decoder | **本函数根本没调用**（工具层 sdr_tools:3127 才调） | `sstv_decoder.py:737 decode_sstv(file_path, output_path, mode)` | ❌ 调度层未接线 |

---

## 6. 汇总（按类别）

**[真bug]**
1. `decoders.py:199-202` 多普勒硬编码 `radial_velocity=0` → `doppler_hz` 恒 0，真机卫星过境不跟踪多普勒。
2. `decoders.py:803-807` APRS 分支不调用 `ax25.parse_ax25_from_audio`；叠加 `sdr_tools.py:3224` 读取永不赋值的 `decoded_frames` → APRS 工具恒输出空列表并误报"direwolf 未安装"。
3. `decoders.py:754-813` 外部工具只 `shutil.which` 探测、从不 subprocess 执行，却在 811 行声称"可用于完整解码"。

**[空壳]**
4. `decoders.py:524-605 decode_sstv`：包络假图，非 FM 解调+同步检测；真后端 `sstv_decoder.py` 存在但未被本函数引用（仅工具层旁路使用）。
5. `decoders.py:803-807 APRS 分支`：note-only，未接 ax25。

**[占位]**
6. `decoders.py:798-802 FT8 分支`：note-only，真解码旁路到 sdr_tools。
7. `decoders.py:748 + 无 wspr 分支`：WSPR 挂名，落到兜底 note，wsprd 从不被执行。
8. `decoders.py:252-293 compute_doppler_correction`：经验公式近似，未用真实速度向量。
9. `decoders.py:378-517`：`if False` 包裹的旧 APT 死代码（错误物理假设），永不执行。

**[建议]**
10. `detect_fhss`（612）每帧只取单 argmax 峰值，多信号并存会漏；阈值 -60 dB 未做噪声底归一。
11. ECI→观测点转换忽略地球自转（155），长过境方位有漂移。
12. 宣传文案 `subagents.py:108` 列了 Olivia/M17/LoRa/TPMS/FT8/WSPR 等，多数无内置后端；建议对外口径与实现对齐。
13. `sample_rate=1e6` 默认（724）对 ADS-B 偏低，建议工具层默认 2e6 或强制用户显式传入。

**未注册/无后端（非空壳，即压根没接进来）**：BPSK31、RTTY、PSK63、Olivia、Hellschreiber、JT65、JT9、FT4 —— 在 decoders.py 全文 0 命中。
