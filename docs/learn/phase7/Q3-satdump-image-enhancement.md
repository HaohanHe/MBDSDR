# Q3 学习笔记：SatDump 图像增强管线

> 上游：[SatDump](https://github.com/SatDump/SatDump)（GPLv3）。本笔记只学机制，不照抄代码；落地实现为 MIT 干净室重写。

## ① 上游真实做法（file:line）

### 1.1 亮度/对比度：tan-slant 曲线
`repos/SatDump/src-core/image/brightness_contrast.cpp:8-38`：

```cpp
float brightness_v = brightness / 2.0f;
float slant = tanf((contrast + 1.0f) * 0.785398163f);  // tan((c+1)*π/4)
// per pixel:
if (brightness_v < 0.0)  v = v * (1.0 + brightness_v);   // 压暗（乘性）
else                     v = v + ((1.0 - v) * brightness_v); // 抬亮（向1逼近）
v = (v - 0.5) * slant + 0.5;                            // 对比度曲线
```

关键设计：对比度不是线性缩放，而是 `tan((c+1)·π/4)`——c=0 时 slant=1（恒等），c>0 时在 0.5 处拐折拉开反差，比线性 `v*contrast` 更自然。

### 1.2 直方图均衡：CDF 查表
`repos/SatDump/src-core/image/histogram_utils.cpp:12-30`：

```cpp
std::vector<int> get_histogram(std::vector<int> values, int len);
std::vector<int> equalize_histogram(std::vector<int> hist) {
    equ_hist[0] = hist[0];
    for (int i = 1; i < hist.size(); i++)
        equ_hist[i] = hist[i] + equ_hist[i-1];   // CDF
    return equ_hist;
}
```

另含 `make_hist_match_table`（:82-93）做直方图匹配（把输入直方图对齐到目标直方图）。

### 1.3 伪彩色 LUT：锚点 + 双线性插值
`repos/SatDump/src-core/image/image_lut.cpp:8-22`：

```cpp
template <typename T> Image create_lut(int channels, int width, int points, vector<T> data);
template <typename T> Image LUT_jet() {
    return create_lut<T>(3, 256, 4, {0,0,max, max,0,max, max,0,max, max,0,0});
}
```

机制：给定 N 个锚点 RGB，沿宽度方向双线性插值到 256 项 LUT，再逐像素查表映射。

### 1.4 APT 专用：遥测楔校准 + 黑白点线性拉伸
`repos/SatDump/plugins/analog_support/noaa_apt/module_noaa_apt_decoder.cpp`：

- **解调链**（:205-214）：RealToComplex → FreqShift(-2.4kHz) → RationalResampler → FIR lowpass(1040Hz) → ComplexToMag。
- **同步**（:1013-1048）：逐行对 39 像素 sync 模板 `{0,0,0,255,255,0,0,255,...}` 做绝对差互相关找行首。
- **黑白点拉伸**（:53-65, `scale_val`）：
  ```cpp
  void scale_val(T &val, int &new_black, int &new_white) {
      float v = (val - new_black) / (new_white - new_black) * 65535;
      valv = clamp(v, 0, 65535);
  }
  ```
- **楔提取**（:1176-1204, `get_calib_values_wedge`）：从遥测楔的 ref8（白参考）和 zero_mod_ref（黑参考）算 `new_white/new_black`，再全图线性拉伸——即"用卫星自带校准条自动定标"。
- **通道切分**（:802-803）：A 通道 x=86..995，B 通道 x=1126..2035（各 909 像素）。

## ② 我方现状（file:line）

| 能力 | 我方位置 | 现状 |
|---|---|---|
| APT 解调 | `cpp/src/dsp/apt_decoder.cpp:157-178`（C++）、`mbdsdr_ai/noaa_apt_lite.py:288-358`（Python） | ✅ 已有：I/Q 混频 + LPF + 包络检波 + 行同步 + A/B 切分 |
| 黑白点拉伸 | `mbdsdr_ai/noaa_apt_lite.py:340-347`（`to_gray`） | ⚠️ 仅 2%~98% 百分位拉伸；无遥测楔自动定标 |
| 亮度/对比度 | 无独立函数 | ❌ `sat_image_processing.py` 无 dedicated brightness/contrast |
| 直方图均衡 | `mbdsdr_ai/sat_image_processing.py:109-217`（全局 + CLAHE） | ✅ 已有，功能比 SatDump 全局 eq 更强（含 CLAHE） |
| 伪彩色 LUT | `mbdsdr_ai/sat_image_processing.py:513-604`（gray/iron/rainbow） | ✅ 已有，但与 APT 解码链未打通 |
| 输出步 | `tools/onboarding/onboard.py:728-742`（旧） | ❌ 旧代码 `result.get("image")` 永远 None（key 是 image_a/image_b）；存图仅 `img/255*255` 裸拉伸 |

## ③ 差距判定

1. **APT 解码→出图链断了**：onboard.py 查错 key，实际从未成功存 APT PNG；且存图无增强。
2. **SatDump 式 tan-slant 对比度缺失**：我方 `sat_image_processing.py` 有 CLAHE/中值/Kuwahara，但没有 SatDump 那种可交互的 brightness/contrast 曲线。
3. **遥测楔自动定标未实现**：SatDump 从 APT 遥测楔 ref8/zero_mod_ref 提取真实黑白电平；我方仅百分位拉伸（对真信号可行但不如楔校准精确）。
4. **已有能力未串联**：`sat_image_processing.py` 的增强器没有接进 APT 解码输出。

## ④ 落地建议

**已落地**（本阶段）：
- 新建 `mbdsdr_ai/image_enhance.py`：干净室实现 tan-slant brightness/contrast、黑白点线性拉伸、百分位自动色阶、CDF 直方图均衡、jet/iron/gray LUT、`enhance_apt()` 一站式管线。
- 修改 `tools/onboarding/onboard.py`：修复 image_a/image_b key 错误；输出步调用 `IE.enhance_apt()` 存增强后灰度 A + iron 伪彩 B。
- 测试：`tests/test_image_enhance.py`（21 个确定性测试，合成图逐像素断言）。

**后续可做（未落地）**：
- 遥测楔自动定标：从解码后的行数据里切楔区（x=997..1038, x=2037..2078），平均 ref8/zero_mod_ref，喂给 `stretch_black_white()`。
- 直方图匹配（histogram matching）：把 APT 图对齐到参考色阶。
