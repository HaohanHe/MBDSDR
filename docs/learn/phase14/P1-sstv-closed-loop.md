# P1 交付：SSTV 真机闭环（数组解码入口 + onboard sstv 模式 + 确定性测试）

> Wave1 / P1 完成。范围：卫星 SSTV 活动接收（FM 语音信道）从 IQ 采集到出图的一条命令闭环。
> 红线遵守：只改 `mbdsdr_ai/sstv_decoder.py`、`tools/onboarding/`、对应测试、本目录；MIT SPDX；
> 无信号诚实空态（不 mock、不伪造图）；频率不硬编码（仅 2m 波段提示）。

## 1. 实现要点（file:line）

### sstv_decoder.py —— 新增数组入口
- `_save_image(image, output_path)`：:770。PIL 存 PNG，无 PIL 退化为 npy。
- `_decode_core(samples, mode)`：:783。**核心解码与文件解耦**——输入已是 48k 重采样后的
  单声道浮点音频，做瞬时频率 / VIS / 数据驱动制式识别 / 逐行采样；成功返回带
  `image`(H×W×3 uint8) 键，失败返回 `{"success": False, "error": ...}`。
  WAV 路径与数组路径共用，行为一致。
- `decode_sstv(file_path, output_path, mode="auto")`：:1001。读 wav → 重采样 → `_decode_core` → 存盘。
- **`decode_audio(samples, sample_rate, out_png=None, mode="auto")`**：:1038。本批新增的数组入口。
  多维自动取声道均值；重采样到 48k；`out_png` 给定时落盘并返回 `output_path`，
  为 `None` 时把 `image` 放内存返回；噪声/弱信号诚实 `success=False`，绝不兜底出图。
- `decode_sstv_from_samples(...)`：:1077。改为直接委托 `decode_audio`，不再写临时 wav。

### onboard.py —— 新增 "sstv" 模式
- MODES 注册：:93（2m 波段 `freq_hint=145.8e6`，具体卫星/频率以官方过境排班为准）。
- `step_decode` sstv 分支：:638。IQ → `_demod_fm(max_dev=5000)` 得基带音频 →
  `np.interp` 重采样到 48k → `decode_sstv_from_samples(..., mode="auto")` → 写
  `sstv_decoded.png` 到 SigMF 同目录。解码失败 → 诚实 FAIL + 修复建议，不产出 PNG。
- `step_output` sstv 分支：:831。把解码 PNG 以标准命名 `sstv_image__<origin>__...png` 纳入交付。
- `main` 口径：:1083。sstv 旁证 `data_origin="recorded"`（真实录制后解码）。

## 2. 物理链路（为什么这样接线）

卫星 SSTV 走 FM 语音信道（2m 144.x）。合成测试里：

```
SSTV 基带音频 b(t)（1200–2300Hz 正弦，pysstv 按制式参数生成）
  → FM 调制：IQ = exp(j·2π·∫ f_dev·b dt)        （f_dev=5kHz）
  → 地面 NFM 解调（相位差分）→ 还原 b(t)
  → 重采样 48k → decode_audio → PNG
```

解调增益 `1/(2π·f_dev/fs)` 与 `_demod_fm(max_dev=5000)` 对齐，线性还原基带；
瞬时频率用过零率估计，对幅度不敏感。

## 3. 测试

`tools/onboarding/test_onboarding.py::TestSstvDecodeAudioRoundtrip / TestSstvOnboardChain`：
- Martin M1 合成音频 → `decode_audio` 出图（320×256，≥200 行，PNG>1KB，像素非空）。
- Robot 36 合成音频 → `decode_audio` 出图（320×240）。
- 内存模式不落盘返回 `image`；纯噪声诚实 `success=False` 且不写 PNG。
- onboard 正向链：FM 调制 Robot36 IQ → `step_decode("sstv")` PASS 且出图。
- onboard 空态：纯噪声 IQ → `step_decode("sstv")` FAIL 且无 `_sstv_output_path`。

## 4. 未完成项 / 交接
- Wave2：onboard 追加 "ssdv" 模式（等 P2 `ssdv_decoder`）。
- 真机：频率/过境排班待官方发布后录入；多普勒补偿建议过境时开启。
- `tests/test_sstv_onboard_e2e.py` 为脚本式（依赖 `real_sstv.wav`），确定性 pytest 已在
  onboarding 测试内覆盖；该脚本保留供真机回归。
