# R2 代码审查：`mbdsdr_ai/sstv_decoder.py`

- 审查对象：`mbdsdr_ai/sstv_decoder.py`（1027 行）
- 审查方式：只读，逐行核对时序常量；并用 `real_sstv.wav` / `syn_robot36.wav` 实测验证
- 实测环境：两 wav 均为 44100 Hz mono int16；syn 时长 36.9s，real 时长 72.9s

---

## 0. 结论速览

**"实验室绿、真机红"的根因已定位**：`real_sstv.wav` 实际是 **Robot72（VIS=0x0C，帧长 ~72s，每行一个同步）**，而不是 Robot36。时序分类器把 300ms 周期一律判成 "Robot 36 grouped"，导致整幅图按 Robot36 两行组结构去拆 Robot72 的每行三段结构，产出彩色错位图。合成素材 `syn_robot36.wav` 是真正的 Robot36 per-line（150ms 周期），走对了分支，所以往返干净。

实测对照：

| 素材 | VIS(实测) | 同步周期 | 真实制式 | 解码器判定 | 结果 |
|---|---|---|---|---|---|
| syn_robot36.wav | 0x08 | 150.0 ms | Robot36 per-line | Robot 36 / per_line | 干净彩条 |
| real_sstv.wav | **0x0C** | **300.0 ms** | **Robot72** | **Robot 36 / grouped** | 彩色错位带 |

强制把 real 信号走 `_decode_robot72` 后，人物主体（持瓶少女）立即可辨，cb_dc=-5.1 / cr_dc=16.0（自然近中性），证明制式判定错误是首要问题。

---

## 1. [真bug] Robot72 被误判为 Robot36 grouped（首要 bug，真机失败根因）

- 位置：`mbdsdr_ai/sstv_decoder.py:419-422`

```python
if pulse_ms >= 7.0 and (130.0 <= period_ms <= 175.0):
    return "Robot 36", {**info, "robot_layout": "per_line"}
if pulse_ms >= 7.0 and (285.0 <= period_ms <= 330.0):
    return "Robot 36", {**info, "robot_layout": "grouped"}
```

实测 real 信号：pulse_ms=8.75、period_ms=299.96ms，命中第二支 → 返回 "Robot 36" grouped。
但实测 VIS=0x0C（= Robot72， parity 校验通过），帧长 72.9s ≈ Robot72 标称 72s，每同步一行（240 个同步脉冲）。

Robot36 grouped 与 Robot72 在"脉宽 9ms + 周期 ~300ms"上几乎无法区分，唯一可靠的判别是 VIS code（0x08 vs 0x0C），但：
- VIS 解码器本身坏了（见 §2），解出 121 而不是 12；
- 兜底白名单 `:444` `if vis_code in (8, 44, 40, 60, 56)` 根本不含 12，也不含 PD 系列；
- 时序分支在到达兜底之前就已 return。

后果：`_decode_robot36` grouped 用 Y0/Cb/Y1/Cr 四段两行组去拆 Robot72 的 Y/R-Y/B-Y 三段，色差错位成竖彩条。强制走 `_decode_robot72` 即恢复可辨图像（已实测验证）。

**建议修复**：在 285–330ms 分支里，若 VIS 能可靠解出 0x0C 就判 Robot72；或按"组内段数"区分（Robot72 每行三段、无中段 porch；Robot36 grouped 有 Cb→Y1 之间的 1500Hz porch）。

---

## 2. [真bug] VIS 解码器整体不可信（极性反 + 位对齐偏 + 漏 parity）

- 位置：`mbdsdr_ai/sstv_decoder.py:255-288`

实测两文件的 VIS 解码对比：

| 素材 | 正确 VIS(手测) | `_detect_vis_header` 返回 |
|---|---|---|
| syn_robot36 | 0x08 (8) | 59 (0x3B) |
| real_sstv | 0x0C (12) | 121 (0x79) |

具体问题：

1. **极性反了**（`:284`）：
   ```python
   if threshold_1300[0] < bit_freq < threshold_1300[1]:
       vis_code |= (1 << bit)
   ```
   标准 SSTV VIS：1100Hz=mark(1)、1300Hz=space(0)。代码把 1300Hz 当 1。实测 real 信号 1100/1300 位完全确认此极性。`:261` 定义的 `threshold_1100=(1050,1150)` 从未被使用——死变量，说明极性分支没写完。

2. **位采样起点偏 1 位**（`:276`）：
   ```python
   vis_code_start = vis_start + int(sample_rate * 0.35)
   ```
   实测 header 结构为：1900Hz 300ms → 1200Hz break 10ms → 1900Hz 300ms → VIS(start+7data+parity+stop≈310ms)。代码在"第一个 1900Hz 段结束"处（0.305s）+350ms=0.655s 采位，而数据位中心在 0.66–0.84s，偏 ~25ms。再加上 `:281` `(bit+1)*bit_samples` 的 +1 偏移，采样窗整体错位。

3. **不校验 parity**（`:280` 只解 7 位）：REAL 信号 parity 实测=0 且偶校验通过，但代码完全不验，错码也照用。

4. **`data_start` 靠巧合正确**（`:286` `return vis_code, vis_code_start + 9*bit_samples`）：=0.305+0.35+0.27=0.925s，正好压在图像起点（0.924s）上。这是 header 双 leader 长度凑出来的巧合，换一个切入时机不同的录音就会偏。

影响：当前因为时序分类器接管了制式判定，VIS 错误不致命；但 §1 的修复恰恰需要"可信的 VIS"来区分 Robot72/Robot36，所以这条必须先修。

---

## 3. [真bug] grouped 模式 `_group_y0_start` 在平区图上失效（次级 bug）

- 位置：`mbdsdr_ai/sstv_decoder.py:535-546`（闭包），调用点 `:549-553`

```python
def _group_y0_start(fr, s, sr, period_ms, pulse_ms):
    ...
    while t + win < scan_end:
        w = fr[t:t + win]
        if np.isfinite(w).sum() >= win // 2 and np.nanstd(w) > 150.0:
            break
        t += step
```

设计意图：sync+porch 之后，Y 内容动态大（std>150），据此定位 Y0 起点。但实测 real 信号前 12 组全部返回 **142.8ms**（应为 ~12ms）：

- 平区背景上 Y 频率变化平缓，8ms 窗内 std 远 <150，检测器一直往后扫；
- 直到撞上 Cb→porch 的大跳变（2174→1500Hz，std>150）才停。

连锁后果（`:554`）：
```python
remaining_ms = max(period_ms - y0_off_ms, 200.0)
```
y0_off=142.8 → period-y0_off=157.2ms，被 `max(...,200.0)` 地板抬到 200ms；再 `uv=200/6=33.3, y=66.7`，段尾 =142.8+200=342.8ms，**越过 300ms 周期 42.8ms，扫进下一行同步**。即使 §1 修了制式，只要 grouped 分支还在，这个 std>150 判据在平区图上仍会崩。

合成彩条图因为边缘剧烈、std 处处 >150，检测一次命中 ~12ms，所以 lab 里测不出来。这是典型"lab green"盲区。

---

## 4. [真bug] grouped 段布局忽略中段 porch，即使 y0_off 正确也会横向漂移

- 位置：`mbdsdr_ai/sstv_decoder.py:555-564`

即使把 y0_off 手动改成正确的 12ms，代码按"Y0/Cb/Y1/Cr 四段无缝首尾相接"切分：
```python
uv_scan = remaining_ms / 6.0; y_scan = 2.0*uv_scan
o_cb = o_y0 + y_samp; o_y1 = o_cb + uv_samp; o_cr = o_y1 + y_samp
```
实测 real（Robot72，虽被误判）在段间存在 porch/间隔（+148–156ms 出现 1500Hz porch），代码不建模这些间隔，导致 o_y1/o_cr 相对真实位置偏 ~8–12ms（≈30–40 像素横向错位），这就是合成通过、真机右侧出紫/绿竖带的直接原因之一。

---

## 5. [建议] 色彩矩阵系数：BT.601 full-range 近似可用，但需注意 SSTV 色差增益

- 位置：`mbdsdr_ai/sstv_decoder.py:470-474`

```python
R = Y + 1.402*(Cr-128)
G = Y - 0.344136*(Cb-128) - 0.714136*(Cr-128)
B = Y + 1.772*(Cb-128)
```

- Y 由 `(f-1500)/800*255` 映射到 0–255（full range），色差同式映射、中心 128。系数本身是标准 BT.601 studio 系数，配 full-range 输入时偏色在可接受范围（实测强制 Robot72 后肤色自然）。
- 但 SSTV Robot 系色差频偏并非严格 ±400Hz（=±127.5 单位）：实测色差段在平区图上集中在 1850–2220Hz，动态小于 luma。当前 DC 中值恢复（`:581-584`）只补直流偏移，不补色差增益，可能整体色饱和偏弱。建议后续用 syn_robot36.npy 原图做往返定量比对，确认是否需要色差增益系数。
- 通用路径里 `:971-973` 内联了一份近似系数（0.344/0.714），与 `_ycbcr_to_rgb` 的 0.344136/0.714136 不一致，应统一调用 `_ycbcr_to_rgb`。

---

## 6. [建议] 同步健壮性 / 漂移 / 丢行

- `_find_sync_markers`（`:299-370`）：周期中位 + ±15ms 窗跟踪，丢行时按周期外推（`:366`），逻辑合理；实测 real/syn 周期 cv 均为 0.001（极稳），无累积漂移问题——因为像素时钟由实测周期反推（`:508-511`/`:686`），而非硬编码。这点设计正确。
- **首行被吞**：`data_start=0.925s` 把 ~0.915s 的首个同步挡掉（`band[:data_start]=False`），`rows_decoded` 实测 239/240，顶行丢 1–2 行。影响小但应注意。
- **`rows_decoded` 计数口径**（`:604` `np.any(image>0)`）：全黑行（像素恰为 0）会被漏计，只是统计偏差，不影响图像。
- **median_filter size=31**（`:244`）在 48kHz 下 = 0.65ms 半径，Robot36 每像素 0.275ms，约跨 2.4 像素，会柔化横向边缘；对噪声鲁棒但代价是锐度。可接受。

---

## 7. [占位/死代码]

- `:261` `threshold_1100=(1050,1150)` 定义后从未使用（见 §2.1）。
- `:966-973` 通用路径的 `elif "Y" in channel_data` Robot36 YUV 分支不可达——Robot36 在 `:797` 已被 `_decode_robot36` 截走，不会走到这里。死代码。
- `:444` VIS 兜底白名单 `(8,44,40,60,56)` 缺 12(Robot72)、0x5E–0x63(PD 系)，即便 VIS 修对也兜不住。
- `decode_sstv_from_samples`（`:1000-1027`）走"写临时 wav→再读"的绕路，功能完整但低效；实时流场景应直接喂 `samples`，属 [建议] 重构而非占位。

---

## 8. [建议] 采样率假设

- `:131` `TARGET_SAMPLE_RATE = 48000`，`_resample_if_needed`（`:182-193`）统一重采样到 48k。两素材本是 44.1k，经 FFT `resample` 后实测周期精度 cv=0.001，可接受。
- 注意：无 scipy 时走线性插值 fallback（`:190-193`），对 44.1k→48k 这种 8.8% 重采样会引入可闻/可见的混叠，建议至少用三次样条。
- `decode_sstv_from_samples`（`:1018`）把传入 `sample_rate` 直接写临时 wav，再由 `decode_sstv` 重采样到 48k——链路正确，无硬编码 bug。

---

## 9. 验证记录（只读，未改源码）

| 实验 | 结果 |
|---|---|
| `decode_sstv(syn_robot36.wav)` | success, per_line, 150ms, 238 行，彩条干净 |
| `decode_sstv(real_sstv.wav)` | success 但误判 Robot36/grouped, 300ms, 240 行，绿底紫蓝竖带 |
| 手测 real VIS 位 | 0x0C=12，parity 偶校验通过 → Robot72 |
| 手测 syn VIS 位 | 0x08=8 → Robot36 |
| 强制 `_decode_robot72(real)` | success, cb_dc=-5.1, cr_dc=16.0, 人物主体可辨（持瓶少女） |
| 实测 real 同步 | 240 脉冲 × 300.0ms（cv=0.001），脉宽 8.75ms |
| 实测 syn 同步 | 239 脉冲 × 150.0ms（cv=0.001），脉宽 8.75ms |

---

## 10. 修复优先级

1. **P0**：§1 制式误判——让 Robot72（300ms/三段/VIS 0x0C）走 `_decode_robot72` 而非 `_decode_robot36` grouped。这是真机失败的直接原因。
2. **P0**：§2 VIS 解码器——修正极性（1100=1）、位对齐、加 parity 校验；否则 §1 无法靠 VIS 区分。
3. **P1**：§3 grouped `_group_y0_start` std>150 判据在平区图失效，且 §3 的 200ms 地板把错位放大成越界。
4. **P2**：§4 grouped 段间 porch 建模；§5 色差增益定量标定；§7 死代码清理。
