# Wave2 骨架：onboard "ssdv" 模式（核心接通前）

> 依据权威规格 `SSDV_SSTV_SPEC.md`（fsphil 现代方言：15B 头 + MCU 级 JPEG 重组）。
> `mbdsdr_ai/ssdv_decoder.py` 正按 fsphil 重写；本批只落**不依赖核心接口**的部分。

## 已落地（file:line）

- MODES 注册 `tools/onboarding/onboard.py`：`"ssdv"`（freq_hint 显式 `None`——
  官方频率/速率未定，禁硬编码；采样率/时长仅 CLI 占位并标 TODO）。
- 可插拔字节流入口 `_step_decode_ssdv(byte_path, r)` `onboard.py`：
  读**解调后字节流文件**（不是 complex64 IQ，在 IQ 加载前分流）→ 空字节诚实空态；
  有字节则喂 `_ssdv_feed_core(raw)`。
- 接缝 `_ssdv_feed_core(raw) -> (n_packets, jpeg_path, info)` `onboard.py`：
  当前返回 `(0, None, {"core_wired": False})`——核心接通前诚实报 0 包，**绝不伪造图/预存呼号**。
- `step_output` SSDV JPEG 分支：核心产出 `_ssdv_jpeg_path` 时按标准命名拷贝进交付；
  无产出自然空过。
- data_origin：sstv/ssdv 旁证均记 `recorded`。

## 测试（tools/onboarding/test_onboarding.py::TestSsdvOnboardSkeleton）
- ssdv 已注册且 freq_hint 为 None（不硬编码频率）；
- 不存在文件 → FAIL；空字节文件 → FAIL「为空」且无 JPEG 路径；
- 噪声字节（无有效 256B 包）→ FAIL 且 `n_frames==0`、无伪造 JPEG。

## 已接通（fsphil 核心就位后，本批）
`_ssdv_feed_core(raw, byte_path)` `tools/onboarding/onboard.py`：
`SsdvDecoder().feed(raw)` → 过滤 None 失败包 → 按 `pkt.image_id` 收进 `SsdvImage` →
选包最多（EOI 优先）的图 `.build()` → 写 `<dir(byte_path)>/ssdv_rebuilt.jpg`。
返回 `(n_packets, jpeg_path, info)`，info 带 `width/height/mcu_count/received_mcus/
missing_mcus/eoi_seen`。有包但部分 MCU 丢失仍出图并在 evidence 报告 `missing_mcus`
（局部花屏，不造假整图）；0 有效包诚实 FAIL 空态。

## 测试（tools/onboarding/test_onboarding.py）
- 空态/骨架 4 条（注册无硬编码频率、不存在文件 FAIL、空字节 FAIL、噪声字节不造假图）。
- 正向链 `test_ssdv_positive_chain_reassembles_jpeg`：SsdvEncoder 64×64 渐变图分包
  → 每包注入 10 错字节（RS t=16 内可纠）→ onboard ssdv → PASS、JPEG 落盘、
  Pillow 打开、尺寸一致、missing_mcus 为空。
- 超纠错 `test_ssdv_over_capacity_reports_missing`：首包注入 18 错字节（>t=16）被丢
  → 诚实报 missing_mcus，其余包 JPEG 仍可打开。

onboarding 套件 **26 passed**（原 20 + ssdv 骨架 4 + ssdv 正向/超纠错 2）。

