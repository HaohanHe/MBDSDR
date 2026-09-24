# SatDump 真实源码移植笔记

> 上游：<https://github.com/altillimity/SatDump>
> 本地镜像：`repos/SatDump/`（`git clone --depth 1`）
> 移植模块：`mbdsdr_ai/satdump_adapter.py`
> 验证：`pytest tests/satdump_test.py -v`（11 项全过）

本文记录从 SatDump C++ 源码逐行移植到 MBDSDR 的关键算法与常量，所有数值均标注 `文件:行号`。

---

## 1. 数据流总览

```
天线 → IQ 采样 → (RRC+PLL) → 软符号(I/Q)
      → QPSK 硬判决 → Viterbi(K=7,R=1/2)
      → CCSDS 解扰(derand_ccsds) → CADU 同步(0x1dcf fc1d) → 1024B CADU
      → 仪器读出(MSU-MR / AVHRR) → 多通道图像
      → 投影(raytrace 到 WGS84) → 经纬度网格
```

---

## 2. LRPT（METEOR-M2/2-x）

### 2.1 解调参数
来源：`resources/pipelines/Meteor-M.json` → `meteor_m2_lrpt`

| 参数 | 值 | 来源 |
|---|---|---|
| 调制 | QPSK（M2-x 为 OQPSK） | Meteor-M.json `constellation` |
| 符号率 | **72 000 sym/s** | Meteor-M.json `"symbolrate": 72e3` |
| RRC alpha | 0.5 | Meteor-M.json `rrc_alpha` |
| PLL 带宽 | 0.002 | Meteor-M.json `pll_bw` |
| 下行频率 | 137.1 / 137.9 MHz | Meteor-M.json `frequencies` |

### 2.2 帧 / CADU
来源：`plugins/meteor_support/meteor/module_meteor_lrpt_decoder.cpp`

| 常量 | 值 | 来源 |
|---|---|---|
| `BUFFER_SIZE` | 8192 | decoder.cpp:13 |
| `FRAME_SIZE` | **1024** 字节 | decoder.cpp:14 |
| `ENCODED_FRAME_SIZE` | 16384 (=1024·8·2) | decoder.cpp:15 |
| CADU 同步字 | `0x1D 0xCF 0xFC 0x1D` | decoder.cpp:256 |
| QPSK 相关器同步 | `0xfca2b63db00d9794`（非差分） | decoder.cpp:201 |
| Reed-Solomon | RS(255,223) 交织 ×4 | decoder.cpp:204,251 |
| 解扰 | `derand_ccsds(&frame[4], ...)` | decoder.cpp:241 |

### 2.3 Viterbi 卷积码
来源：`src-core/common/codings/viterbi/viterbi27.h:8`

```cpp
static std::vector<int> CCSDS_R2_K7_POLYS = {79, 109};
```

- 约束长度 K=7，码率 1/2。
- SatDump 以**十进制位反转**形式存储：`{79, 109}` = `{0x4F, 0x6D}`。
- 这正是教科书标准 CCSDS 多项式 `{0x79, 0x5B}`（八进制 171/133）的位反转表示。
- 移植实现 `LRPTDecoder.viterbi_decode()`：64 状态 ACS + 前向 PM + 回溯链，
  无噪往返 BER=0，噪声 σ=0.5 仍可全部纠正（卷积码编码增益）。

### 2.4 成像幅宽
来源：`resources/projections_settings/meteor_m2-4_msumr_lrpt.json`

| 参数 | 值 |
|---|---|
| `scan_angle` | **110.1°**（跨轨整行扫描角） |
| `image_width` | **1568** 像元 |
| `roll_offset` | -0.4 |
| 轨道高度 | ~820 km |

---

## 3. HRPT（NOAA / MetOp）

来源：`plugins/noaa_metop_support/noaa/noaa_deframer.cpp`

| 常量 | 值 | 来源 |
|---|---|---|
| 下行码率 | **665 400 bps** BPSK | Meteor-M.json `meteor_hrpt: symbolrate=665400` |
| 60-bit 同步字 | `0x0A116FD719D83C95` | noaa_deframer.cpp:13 |
| 同步字(6 个 10-bit) | `0284 016F 035C 019D 020F 0095` | noaa_deframer.cpp:6-11 |
| `HRPT_SYNC_WORDS` | 6 | noaa_deframer.cpp:15 |
| `HRPT_MINOR_FRAME_WORDS` | **11090** | noaa_deframer.cpp:16 |
| `HRPT_BITS_PER_WORD` | 10 | noaa_deframer.cpp:17 |

- 小帧 = 11090 × 10 = 110 900 bit ≈ 0.1667 s（6 帧/秒）。
- 移植为 `HRPTDecoder` 骨架：60-bit 软相关（容忍 `threshold` 个误码）+
  反转判决分支 + 10-bit 字组帧。AVHRR 5 通道读出留接口（参考
  `instruments/avhrr/avhrr_reader.cpp`，2048 像元/行、10-bit 量化）。

---

## 4. 大地测量与投影

### 4.1 WGS84 椭球
来源：`src-core/common/geodetic/wgs84.h:9-20`

```cpp
a  = 6378.137            // 半长轴 km
rf = 298.257223563       // 扁率倒数
f  = 1/rf
b  = a*(1-f) ≈ 6356.7523 // 半短轴 km
```

### 4.2 ECEF ↔ LLA
来源：`src-core/common/geodetic/lla_xyz.cpp`

- `lla2xyz`（:9-16）：`N = a/√(1-es·sin²lat)`，标准大地正算。
- `xyz2lla`（:18-38）：Bowring 迭代求纬度，高度由 `|P| − |lla2xyz(lat,lon,0)|` 得到。
- 移植 `lla_to_ecef()` / `ecef_to_lla()`，往返误差 < 1e-6°。

### 4.3 等距矩形投影
来源：`src-core/projection/standard/equirect.cpp:20-38`

```cpp
x = lon * RAD2DEG;   // 前向
y = lat * RAD2DEG;
phi = y * DEG2RAD;   // 反向
lam = x * DEG2RAD;
```

即 x=经度、y=纬度的线性映射。

### 4.4 卫星轨道投影（raytrace）
来源：
- `src-core/projection/raytrace/common/normal_line.cpp:39-85`（像元→姿态）
- `src-core/common/geodetic/euler_raytrace.cpp:101-196`（姿态→地面点）

扫描几何（normal_line.cpp:72）：

```
roll = ((pixel_x - image_width/2) / image_width) * scan_angle + roll_offset
```

视线构造（euler_raytrace.cpp）：
1. 天底向量 = 卫星 LLA→alt=0 地面点 ECEF − 卫星 ECEF（:114-129）；
2. yaw：速度向量绕天底轴旋转（:138-139）；
3. `velocity_90`：速度绕天底转 90°（:142-143）；
4. roll：天底向量绕速度向量旋转（:146-147）；
5. pitch：再绕 `velocity_90` 旋转（:150-151）；
6. 视线 `P + d·V` 与 WGS84 椭面 `(x²+y²)/a² + z²/c² = 1` 求二次交（:162-179）；
7. 交点 ECEF → LLA。

移植 `MapProjector.project_pixel()`：星下点像元精确回到卫星星下点（误差 <0.5°），
边缘像元跨轨偏离 >5°，验证通过。

---

## 5. 图像合成

参考 `src-core/image/`（`image_utils.cpp`、`image_lut.cpp`）。

- `SatImageProcessor.normalize_channel`：1%–99% 百分位拉伸。
- `compose_rgb`：三通道归一化叠成 H×W×3 uint8。
- `false_color_ir`：可见光→R、近红外→G、红外窗区→B 且**反转**（云顶亮），
  对应 NOAA AVHRR / METEOR MSU-MR 常见假彩色映射。

---

## 6. APT 交叉印证

SatDump `plugins/analog_support/noaa_apt/module_noaa_apt_decoder.cpp` 印证了
`noaa_apt_lite.py` 的参数：同步 39 像素（:1018）、图像 909 像元、
通道 A 偏移 86、通道 B 偏移 1126（:802-803），下行 137.1/137.9125/137.62 MHz。
与现有实现完全一致，无需改动解码逻辑。

---

## 7. 文件清单

| 文件 | 说明 |
|---|---|
| `mbdsdr_ai/satdump_adapter.py` | 投影/图像/LRPT/HRPT 全部移植 |
| `mbdsdr_ai/meteor_sat.py` | 增加 SatDump 交叉校准注释块 |
| `mbdsdr_ai/noaa_apt_lite.py` | 增加 SatDump APT/HRPT 交叉印证注释 |
| `tests/satdump_test.py` | 11 项投影/图像/Viterbi/帧同步/参数测试 |
| `mbdsdr_ai/tool_registry.py` | 注册 `sat_project_image` / `sat_compose_falsecolor` / `lrpt_decode_full` |

运行验证：

```bash
pytest tests/satdump_test.py -v
```
