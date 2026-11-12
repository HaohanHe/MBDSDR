# D-9 裁决记录：录音格式与容量策略（2026-11-12）

> 对象：`docs/research/2026-11-11-roadmap.md` §6 D-9。
> 性质：**产品决定裁决**，不改代码。裁决前查证见 §2，裁决见 §3。
> 与 `docs/hardcoded-parameters.md` 的 D-9（AI 工具重试）**不是同一议题**；撞号已在该文件内改名（见 §5）。

## 1. 裁决前 D-9 的原始口径（原句）

1. roadmap §6（`2026-11-11-roadmap.md:242`）：

   > `| D-9 | 录音格式与容量策略 | gap-recording #4/#14/#15/#16：回放上限 10 s、非 SigMF、固定 cf32（≈69 GB/h）、无 WAV 入口 | A 维持 cf32 + 分段；B 加位深选择；C 转 SigMF + WAV 双轨 | C 为目标态，B 可先落；A 需明确告知容量 |`

2. W37 交接单（`docs/pending-patches/2026-11-11-handoff-w37-replay-total-file-end.md:3`）：

   > `> 提出：2026-11-11；D-9 已裁定「先落 B（最小可回滚），C 单列后续条目」。`

3. W37 复现脚本（`scripts/repro/repro_playback_10s.py:6`）：

   > `2.4 MS/s 录音回放恒在约 10 s 结束。开工 W37（D-9 裁定 B：最小可回滚）之前，`

**第 2、3 处只是转述，且转述本身把 B 说成了已裁定。** 本次裁决以代码与文档原文为准，不以转述为准。

## 2. 裁决前查证（2026-11-12 对 HEAD）

### 2.1 读侧封闭集已经定死，不是待决项

`mbdsdr_ai/baseband_io.py:54-66` 定义 `SUPPORTED_IQ_FORMATS = ("wav", "cs16", "cf32", "cu8")`，并按扩展名分派；`:80-111` 已解析 `.sigmf-meta` 的 `global`（datatype / sample_rate / center_freq）。

回放侧已消费头部字段，而非当前设备值：

- `desktop/main_window.py:12435` 文件过滤器已列 `*.iq *.cf32 *.cs16 *.wav`；
- `desktop/main_window.py:12476` 调 `load_iq`；
- `desktop/main_window.py:12550-12551` 使用 `rec["sample_rate"]` / `rec["center_freq_hz"]`。

**结论：W97 所述症状（“按当前设备速率播 / 只认自家 cf32”）在 HEAD 不成立。** 该条按「已实现（2026-11-12 复核）」关闭。

### 2.2 真正未定的是写侧，不是容器集合

- 写侧固定：`desktop/main_window.py:10015` `"format": "cf32"`（gap-recording 原记 `:9444`，行号已漂移）。
- 两套写栈并存：`desktop/main_window.py` 自写 cf32 + `.json`；`mbdsdr_ai/recorder.py` 已有 `IQRecorder`（SigMF）与多格式 `save_iq`。gap-recording R-13 原文即写「两套并存」。
- 容量：2.4 MS/s × cf32 ≈ **69 GB/h**（gap-recording R-14）。

### 2.3 W37 的卡点与格式无关

W37 的问题是**整文件读入、无流式播放**（`REPLAY_MAX_SAMPLES = 24_000_000`，约 10 s @2.4 MS/s），以及回放终态判定。容器是 cf32 还是 cu8 不改变这两个问题。交接单里的 B 方案也只动终态判定。

## 3. 裁决

### 3.1 目标态选 A：只定界

- **读**：以 `mbdsdr_ai/baseband_io.py` 为唯一入口，封闭集维持 `wav / cs16 / cf32 / cu8` + SigMF meta。本次**不重复决策**。
- **写**：维持 cf32 + `.json` sidecar，不迁移、不改默认。
- **遗留**：文件对话框过滤器缺 `*.sigmf-data`。这是一行改动，**并入 `main_window.py` 下次批次**，不单列工单。

### 3.2 B 单列为 W98「录制备选位深与容量」

B 的真实动机是 **69 GB/h 的容量**，不是格式边界；因此不塞进 D-9，单列为 W98（未开工），排在 W96 之后、`main_window.py` 录音段串行链内。

### 3.3 C 明确不立项

C（写侧转 SigMF 双轨 + 可选音频 WAV）**不立项**，不是「暂缓」：

- 要重写 W37 已写好的终态判据（W37 按 `.iq` 路径与文件长度判定）；
- 要动卫星 LRPT 面板的落盘/读取路径；
- 收益主要是跨工具互通，当前不具备抵消上述两项风险的紧迫性。

写下「不立项」是为了防止下一个人把同一提案再提一遍。

## 4. 对 roadmap 的影响

- W97：阻塞 → **已实现（2026-11-12 复核）**；遗留过滤器一行随下一批 `main_window.py` 处理。
- 新增 W98：未开工。
- W37：**解除与 D-9 的绑定**，卡点记为整文件读入/无流式播放。

## 5. 编号冲突处置

`docs/hardcoded-parameters.md` §2.4 的 D-9（AI 工具重试）与录音 D-9 撞号。该文件内已改为 **D-9-AI**，并加注「此处曾与录音 D-9 重名，已改」。选择改它的理由：录音 D-9 已被 parity、roadmap、W37 交接单、复现脚本共同引用；AI 重试项只有该文件两处引用，改名代价最小。
