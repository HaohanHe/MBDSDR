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

---

# 追加：卫星追踪 / 过境预测 / APT 解调管道（mbdsdr_ai/satellite/ 移植对照）

> 本节对应任务「深学 SatDump 源码，移植到 mbdsdr_ai/satellite/」。
> 上游镜像：`repos/SatDump/`（已存在，无需 clone）。
> 注意：上游任务书里写的 `src-core/satellite/sat_tracker.cpp` 在当前 SatDump 已重构到
> `src-core/common/tracking/` 与 `src-core/libs/predict/`（libpredict 打包），下文按真实路径标注。

## 8. 卫星追踪（SGP4 传播 + 站心观测）

### 8.1 类结构 `SatelliteTracker`
来源：`src-core/common/tracking/tracking.h:10-46`

```cpp
class SatelliteTracker {
  predict_orbital_elements_t *satellite_object;   // :13
  predict_position satellite_orbit;                // :14
  LinearInterpolator *interp_x/y/z, *interp_vx/vy/vz; // :16-21 备用：JSON 星历插值
public:
  SatelliteTracker(TLE tle);                      // :24  from TLE
  geodetic::geodetic_coords_t get_sat_position_at(double utc_time);  // :28
  predict_observation get_observed_position(double utc, gs_lat, gs_lon, gs_alt); // :32-45
};
```

### 8.2 TLE 解析与传播
来源：`src-core/common/tracking/tracking.cpp:10-13`
- 构造：`predict_parse_tle(line1, line2)` → `predict_orbital_elements_t*`。
- 传播：`predict_orbit(sat, &orbit, predict_to_julian_double(utc))`（:64, :85），
  内部走 `libs/predict/sgp4.c / sdp4.c`（NORAD SGP4/SDP4 标准）。
- ECI→经纬高：`Calculate_LatLonAlt(time, pos, &out)`（:73）。

### 8.3 站心观测（仰角/方位/距离）
来源：`tracking.h:32-45`
```cpp
observer = predict_create_observer("Main", gs_lat*DEG_TO_RAD, gs_lon*DEG_TO_RAD, gs_alt);
predict_orbit(sat, &orbit, jd);
predict_observe_orbit(observer, &orbit, &observation_pos);  // observation_pos.elevation / .azimuth (rad)
```
**移植到 MBDSDR**：复用 `mbdsdr_ai/orbit.py::_state_from_satrec()`——它已用
sgp4 库做 TEME→ECEF(GMST)→ENU 变换，输出 elevation/azimuth/range_km/range_rate_kms，
与 libpredict 数学等价（orbit.py:120-174 已与 gpredict 交叉验证 <0.02°）。

## 9. 过境预测（AOS/LOS）

### 9.1 数据结构
来源：`src-core/common/tracking/scheduler/passes.h:8-14`
```cpp
struct SatellitePass { int norad; double aos_time, los_time; float max_elevation; };
```

### 9.2 核心算法（LEO）
来源：`passes.cpp:9-106`
1. :12 建 observer；:13 按 norad 从 kepler DB 取 TLE；:20 解析。
2. :23 `predict_is_geosynchronous()` — GEO 单独处理（若可见则整段算一个过境）。
3. :42-79 LEO 主循环：
   - :45 `next_los = predict_next_los(obs, sat, jd)` — 找下一个 LOS；
   - :50-54 从该 LOS 往前每 10s 回退调用 `predict_next_aos()`，直到 AOS 时间早于 LOS；
   - :62-69 在 [AOS, LOS] 之间分 50 段采样，记录 `max_el`；
   - :78 `current_time = next_los + 1` 继续下一圈。
4. :108-117 `filterPassesByElevation` — 按 max_el 阈值过滤。

**移植到 MBDSDR**：`orbit.py:285-400 predict_passes()` 已用「60s 粗扫定位跨越 →
二分法收敛到 0.25s（`_bisect_threshold` orbit.py:254-282）→ 2s 细采样找 max_el」
等价实现，且额外输出 rise/set azimuth、持续时长、多普勒 min/max。
本次 `mbdsdr_ai/satellite/tracker.py` 在此基础上封装成 `SatelliteTracker` / `PassPredictor`
两个类，输出任务书要求的 `{aos, los, max_el, max_el_t, doppler_at_aos}`。

## 10. NOAA APT 解调/解码管道

### 10.1 解调（IQ WAV → APT 音频）
来源：`plugins/analog_support/noaa_apt/module_noaa_apt_demod.cpp`
- :36 重采样到符号率；:43 `QuadratureDemodBlock`（正交 FM 鉴频），
  增益 `hz_to_rad(d_symbolrate/2, d_symbolrate)`。
- :112 输出 int16 WAV（采样率 = APT 符号率 ~4160Hz 或音频采样率）。

### 10.2 解码（音频 → 16-bit 图像）
来源：`plugins/analog_support/noaa_apt/module_noaa_apt_decoder.cpp` + `.h`

常量（`.h:14-15`）：
| 常量 | 值 | 含义 |
|---|---|---|
| `APT_IMG_WIDTH` | 2080 | 每行像素数（A+B 两通道） |
| `APT_IMG_OVERS` | 4 | 过采样（4 样本/像素） |

接收链（`.cpp:205-214`）：
```
int16 WAV → RealToComplex → FreqShift(-2400 Hz)   // :206 把副载波搬到零频
         → RationalResampler → 2080*2*4 = 16640 sps // :208
         → FIR low_pass(1040 Hz)                   // :213 抗混叠
         → ComplexToMag                            // :214 取包络 = 视频
```
- :234 视频幅度 `v = (v*2)*65535` → 16-bit 灰度。

行同步（`.cpp:1013-1048 synchronize()`）：
- :1015 同步字 `sync_a[39] = {0,0,0,255,255,0,0,255,255,0,0,...,0}`（39 样本 0/255）；
- :1018-1020 按 OVERS=4 上采样到 156 样本模板；
- :1025-1045 每行在 2080*4 个位置做滑动绝对差互相关，取最小差位置为行首，
  每 4 样本抽 1 还原 2080 像素/行。

通道切分（`.cpp:311-315, :802-803`）：
| 段 | 像素区间（2080 宽） |
|---|---|
| 通道 A 图像 | 86 .. 86+909 |
| 通道 B 图像 | 1126 .. 1126+909 |
| 遥测楔 1 | 997 .. 997+41 |
| 遥测楔 2 | 2037 .. 2037+41 |
| 空格 A | 42 .. 42+43 |
| 空格 B | 1081 .. 1081+43 |

卫星自动识别（`.cpp:115-153`）：
| 下行频率 | 卫星 | NORAD |
|---|---|---|
| 137.1000 MHz | NOAA-19 | 33591 |
| 137.9125 MHz | NOAA-18 | 28654 |
| 137.6200 MHz | NOAA-15 | 25338 |
（:752-766 NORAD 映射）

行时间戳（`.cpp:969-973`）：每行 `start_tt + line*0.5`（2 行/秒，行率 0.5s/行）。

**移植到 MBDSDR**：`mbdsdr_ai/noaa_apt_lite.py` 已逐常量对照 noaa-apt(Rust) 实现了
完整合成/解调/同步/切通道；本次 `mbdsdr_ai/satellite/decoders.py` 提供更薄的
`NOAAAPTDecoder` 类封装（WAV/原始 IQ 入 → PNG 出），常量与 SatDump 上表完全一致。

## 11. 产品生成（ImageProduct）

来源：`src-core/products/image_product.h:42-115`
```cpp
class ImageProduct : public Product {
  struct ImageHolder {           // :71-83
    int abs_index;               //   绝对通道号（-1 不校准）
    std::string channel_name;    //   "AVHRR-1" ...
    image::Image image;          //   像素矩阵
    int bit_depth = 16;
    double wavenumber = -1;      //   波数（红外通道）
    channel_polarization_t polarization;
    std::string calibration_type;
  };
  std::vector<ImageHolder> images;   // :85
  void set_proj_cfg_tle_timestamps(json cfg, TLE tle, vector<double> timestamps); // :110
};
```
**移植到 MBDSDR**：`mbdsdr_ai/satellite/products.py::ImageProduct` 用 numpy 数组 +
dataclass 镜像该结构，提供 `channels` 字典、`set_projection()`、`compose_rgb()`
假彩色合成、`save_png()`。

## 12. 移植清单（本次新增，不改现有文件）

| 新文件 | 作用 |
|---|---|
| `mbdsdr_ai/satellite/__init__.py` | 包入口，导出 SatelliteTracker/PassPredictor/NOAAAPTDecoder/ImageProduct |
| `mbdsdr_ai/satellite/tracker.py` | SGP4 传播、AOS/LOS 预测、实时多普勒（复用 orbit.py） |
| `mbdsdr_ai/satellite/decoders.py` | NOAA APT：同步字检测、行重组、A/B 通道分离、PNG 输出 |
| `mbdsdr_ai/satellite/products.py` | ImageProduct：通道数据/地理元数据/假彩色合成/PNG |
| `tests/test_satellite_tracker.py` | SGP4 位置对照、AOS/LOS、多普勒 |
| `tests/test_apt_decoder.py` | 合成 APT 信号 → 解码出正确通道图像 |
| `tests/test_products.py` | 图像产品、通道合成 |

## 13. 增强（任务书「我们的增强」）

- `decoders.auto_detect_decoder(freq_hz)`：按下行频率识别 NOAA-15/18/19 → 选 APT 解码器
  （对照 SatDump `.cpp:115-153`），MetOp/FY 留接口；
- `tracker.SatelliteTracker.next_pass_countdown()`：下一次过境倒计时；
- `tracker.doppler_correction()`：实时 `f_rx = f_carrier*(1 - v_los/c)`（orbit.py:205-229）；
- `products.ImageProduct.estimate_geocorrection_offset()`：AI 估计图像行偏移占位接口；
- 与 rf_sky_view 联动：选中卫星 → `tuner_freq = f_down + doppler`（见 tracker.to_rf_command()）。
