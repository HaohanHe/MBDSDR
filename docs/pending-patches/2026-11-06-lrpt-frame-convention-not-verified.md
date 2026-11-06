# LRPT 帧约定未对齐（not_verified 登记，2026-11-06）

> 状态：**未验证 / 未打通**。本单只登记事实与边界，不声称链路已接通。
> 触发原因：`weather_panel.DecodeWorker._run_meteor` 曾把上游 CADU 直接喂
> `compose_visible_image`，返回 None 却报成「段不全或数据损坏」——把格式错
> 报成信号差。本批已修成"先诊断、分类报错"（`meteor_sat.probe_cadu_sync`）。

## 一、事实：仓内至少四套互不兼容的帧首约定

| 名称 | 帧首字节 | 生产者 | 消费者 |
|---|---|---|---|
| `lrpc_simplified_3b` | `1DFCDC` (3B) | `meteor_sat.lrpc_build_frame` | `meteor_sat.compose_visible_image`（默认 `sync`）→ `lrpt_vcdu.reassemble_meteor_scan` → `lrpt_huffman.decode_meteor_scan` |
| `satdump_cadu_4b` | `1DCFFC1D` (4B) | `lrpt_vcdu.LRPT_CADU_SYNC` / `frames_to_meteor_segments` | 同一重组器（需显式传 `sync`） |
| `ccsds_asm_4b` | `1ACFFC1D` (4B) | `lrpt_fec.LrptCaduDecoder`（**真录实证**：`tests/golden/lrpt_meteor_real_soft_manifest.json` 的 ASM 逐字复原） | `lrpt_image.parse_vcdu_header` → `reassemble_space_packets` → `parse_image_packet` → `lrpt_packets_to_rgb` |
| `meteor_sat_asm_8b` | `CBC465E232F11978` (8B) | `meteor_sat.extract_cadu` 的 64-bit ASM 位流，经 `_cadu_item_to_bytes` 打包后的形态 | 目前**无消费者**；`demodulate_lrpt` 产出的帧就是这个形态 |

另外 `lrpt_modem.LRPT_SYNC_WORD = 0xFCA2B63DB00D9794`（64 bit，差分编码后同步字），
`lrpt_pipeline.LrptE2EPipeline` 走的是第五套闭环口径。

## 二、实测证据（本机可复现）

1. `demodulate_lrpt` → `extract_cadu` 的帧首是 `CBC465E232F11978`；
   `compose_visible_image` 默认只认 `1DFCDC` → **每一帧都被计入 `n_no_sync`**
   → `return None`。旧面板文案却说「段不全或数据损坏」。
   取证：`tests/test_lrpt_frame_convention.py::TestProbe`（四套分类 + 反证）、
   `TestWorkerDiagnosis`（约定未对齐时 `compose_visible_image` 调用次数 = 0）。
2. 真录 golden 两条 892B VCDU（`lrpt_meteor_real_soft_{0,1}.f32`）经
   `LrptCaduDecoder` 解出的头：
   - frame0 `40 73 00 EC C3 2D F0 D3 AD CE 12 02 …`
   - frame1 `40 62 00 0A 26 08 76 08 00 02 00 00 …`
   喂 `lrpt_image.reassemble_space_packets` 得
   `n_frames=2 / n_packets=0 / n_resets=1 / n_fhp_out_of_range=1`。
   两帧是**孤立的 winner frames**（vcdu_id 与 counter 都不连续），本来就不
   可能拼出完整图；但 FHP 越界也提示 VCDU 头偏移/语义仍待用连续真录核实。
3. `lrpt_image.parse_vcdu_header` 用 `VCDU_HDR_LEN=8` + `MPDU_HDR_LEN=2`
   （CCSDS 标准主头是 6B），仓内自洽但与标准口径不同；无连续真录样本前
   **不改**，改了也验不了。

## 三、明确没验证的边界

- 未验证：`meteor_sat.demodulate_lrpt`（demod.py 的 QPSKDemodulator）与
  `lrpt_fec.LrptCaduDecoder`（真录裁决过的 pair-swap 口径）能否共用一条
  物理链——两者都做 Viterbi/解扰，重复且约定不同。
- 未验证：`compose_visible_image` 的 RFC 2435 段重组 + K.3 熵解码在**真实
  Meteor OTA** 上出过整图。仓内 `mbdsdr_ai/tests/test_compose_visible_image.py`
  只证明对 `lrpc_build_frame` 造的合成帧能出图，不是真录。
- 未验证：`lrpt_image` 的 VCDU→空间包→MCU 链在连续真录上能出整图。
- 因此：**任何"LRPT 已能出真云图"的表述都不可用**。当前能力是"能从录制
  IQ 真解调出 CADU 帧并如实报帧数/失败原因"，不是"能出图"。

## 四、已落地的行为（不算接通）

- `meteor_sat.probe_cadu_sync()`：只读诊断帧首归属，`compose_compatible` 仅在
  3B 简化帧时为 True；不修改数据、不猜格式、不自动补头。
- `weather_panel.DecodeWorker._run_meteor`：约定不对齐 → 报帧首 hex + 口径名 +
  `not_verified`，根本不调用图像重组器；约定对齐但段不全 → 才报「段重组/熵解码」。
- `SatelliteImagePanel` 的 METEOR 选项：不再谎称「图像重组待实现」，改为
  选择 complex64 录制 IQ，走与气象云图面板同一个 `DecodeWorker`；采样率优先
  读 SigMF meta，无 meta 时手动值明写来源；成功出真 PNG、失败保持空态。

## 五、要真正打通还差什么（下一步，未排期）

1. 拿一段**连续**的 Meteor-M2 过境真录（≥几十秒、VCDU 计数器连续），
   而不是两条孤立 winner frames。
2. 用真录裁决物理层：把 `demodulate_lrpt` 与 `LrptCaduDecoder` 合为一条
   （pair-swap 口径已由真录裁决），产出标准 `1ACFFC1D` CADU → 892B VCDU。
3. 用连续 VCDU 核实 `parse_vcdu_header` 的头偏移与 FHP 语义，再让
   `reassemble_space_packets` 出空间包、`lrpt_packets_to_rgb` 出 RGB。
4. 图像内容级交叉验证：与公开 Meteor 云图或 SatDump 输出对拍，不能只靠
   "代码跑通没抛异常"。
5. 上述任一步未做之前，UI 文案不得写成「LRPT 已支持出图」。
