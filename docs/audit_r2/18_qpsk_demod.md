# R2-18 QPSK 数字解调器审查（mbdsdr_ai/demod.py）

- 审查文件：`mbdsdr_ai/demod.py`（487 行）
- 审查方式：逐行通读 + numpy 只读复算（不修改源码）
- 链路：RRC 匹配滤波 → QPSK Costas 环 → Gardner 位同步 → 硬判决 → （独立）K=7 r=1/2 Viterbi 解码 + CCSDS/NRZ-M 解扰
- 总体结论：**链路骨架正确，但 Viterbi 解码器有 3 个互相叠加的致命 bug，Gardner 环实际是开环（不收敛），这正好解释"实验室绿、真机红"**。

---

## 一、致命 Bug（[真bug]）

### B1. Viterbi 回溯方向用了前向转移，而非逆转移 —— 上一线索确认成立
**位置：`demod.py:436-439`**

```python
for i in range(n_symbols - 1, -1, -1):
    bit = survivors[i, state]
    decoded[i] = bit
    state = self.next_state[state, bit]   # <-- 439 行
```

`survivors[i, ns] = b` 的语义是：在时刻 i，**到达新状态 ns 的那条幸存路径，其输入比特为 b**，对应的上一时刻状态是某个前驱 `sp` 满足
`next_state[sp, b] = ns`。

回溯时应当沿**逆转移**求前驱：
```
sp = ((ns & ((1<<(K-2))-1)) << 1) | b
```
但代码写的是 `state = self.next_state[state, bit]`，这是**从当前状态 ns 出发、以 b 为输入向前走到 i+1 时刻的状态**，与 i-1 时刻的真正前驱毫无关系。

实测（用 `_build_transitions` 同一套转移表，正向编码 50 比特后取末端状态）：
- 末端状态 = 47，最后比特 = 1，真正前驱 = **31**
- 代码 `next_state[47, 1]` = **55** ❌
- 逆转移 `predecessor(47, 1)` = **31** ✅

即每往回走一步，状态就被随机甩到另一条无关路径上，后续 `survivors[i-1, state]` 全部取错。

---

### B2. Viterbi 幸存决策（decisions）从来没被写进去 —— ACS 比较逻辑写反
**位置：`demod.py:417-427`**

```python
for input_bit in [0, 1]:
    ns = self.next_state[:, input_bit]
    t  = total[:, input_bit]
    np.minimum.at(new_pm, ns, t)          # 422: 先就地把 new_pm[ns] 更新成 min(...)
    mask = (t < new_pm[ns])               # 426: 再拿 t 和"已经被更新后的 min"比较
    decisions[ns[mask]] = input_bit
```

`np.minimum.at` 执行完后，`new_pm[ns]` 已经是 `min(旧值, t)`，必然 `≤ t`。于是 `t < new_pm[ns]` **恒为 False**（浮点边界也最多等于），`decisions` 整列保持初始 0。

实测：对干净编码流跑前 5 个符号，每个时刻 `np.count_nonzero(decisions) = 0`（应等于 64）。
后果：`survivors[sym_idx]` 全零，回溯时 `bit` 恒为 0，叠加 B1，整条解码路径是假的。

正确写法应该在 `minimum.at` **之前**先记录比较：例如先存 `new_pm[ns]` 的旧值，比较 `t < old`，再更新；或对每个 `next_state` 一次性比较两个前驱 `total[state0,0]` vs `total[state1,1]`。

---

### B3. 生成多项式 171/133 被写成十进制字面量，不是八进制
**位置：`demod.py:323`**

```python
def __init__(self, K: int = 7, G1: int = 171, G2: int = 133):
```

docstring（`demod.py:327-328`）明确说"G1/G2（八进制）"，但 Python 3 里 `171` 是**十进制**。实测：
- 代码实际值：`G1=171 = 0b10101011`，`G2=133 = 0b10000101`
- CCSDS 标准期望：`G1=0o171=121=0b1111001`，`G2=0o133=91=0b1011011`
- 两者完全不同（低 7 位都不一样）。

只要调用方不显式传 `G1=0o171, G2=0o133`，解码器用的就是一组从未在发射端使用的生成多项式，必然解错。这是从 Python 2（`0171` 为八进制）或 C（前导 0 为八进制）移植时漏改的典型问题。

> B1+B2+B3 叠加：即便把 B3 改成正确八进制，B2/B1 不修，干净编码流实测仍有 **101/200 比特错误**（≈50%，等同随机）。

---

### B4. Gardner 定时恢复是开环 —— `gain_mu` 计算了误差但从未反馈给 `mu`
**位置：`demod.py:168, 197-219`**

```python
self.gain_mu = gain_mu          # 168: 存下来
...
err = np.real(out - self.line[idx+1]) * np.imag(mid)   # 212: 算了误差
...
self.mu -= self.sps              # 215
# ← 这里缺了 self.mu += self.gain_mu * err
self.mu += 1.0                   # 217
```

在 `work()` 源码里 grep `gain_mu`，除了 `__init__` 赋值外**再无引用**。`err` 算完就被丢弃，`mu` 只做固定的"每 4 个采样减 4"。

后果：
- 这是一个**纯固定 NCO 分频器**，不是闭环位同步。
- 合成信号采样率与符号率严格整数倍、无时钟漂移时，固定 4 抽 1 碰巧能工作 → "实验室绿"。
- 真机 TCXO 有几十~上百 ppm 偏差，眼图采样点会在几个符号内扫出眼睛中心，BER 飙升 → "真机红"。
- `work_batch` 里 `gain_mu=0.05` 这个调参参数是死代码。

此外，即便补上反馈，误差检测器本身也有问题（见 B5）。

---

### B5. Gardner 误差检测器取错了"前一个符号"样本
**位置：`demod.py:209-212`**

```python
mid_idx = idx - self.sps // 2          # = idx - 2
if mid_idx >= 0 and idx + 1 < len(self.line):
    mid = self.line[mid_idx]
    err = np.real(out - self.line[idx + 1]) * np.imag(mid)
```

标准复 Gardner TED 应为
`e = Re[(y_k - y_{k-1}) · conj(y_{k-1/2})]`，其中 `y_k` 是当前符号点、`y_{k-1}` 是上一个符号点、`y_{k-1/2}` 是二者中点。

这里：
- `out` 是 `line[idx]` 的线性插值（当前点 y_k）；
- `line[idx+1]` 是延迟线里**比 out 新一个采样**的点（即下一个采样，不是上一个符号 y_{k-1}）；
- `mid = line[idx-2]` 是 2 个采样之前，对 sps=4 而言大概在 y_{k-1/2} 附近，位置勉强；
- 公式还少了 `conj`，直接 `imag(mid)` 而非 `imag(mid·conj(...))` 的完整结构。

即使 B4 补上反馈，这个误差信号方向/尺度也是错的，环收不到正确的定时校正量。

---

## 二、次要问题（[真bug 候选] / [建议]）

### S1. Costas 环噪声带宽公式与 GNU Radio 不一致，且 NCO 符号约定可疑
**位置：`demod.py:95, 116`**

```python
wn = noise_bw * 2 * np.pi                 # 95
...
nco = np.exp(1j * self.phase)             # 116
corr = sample * nco
```

- GNU Radio `control_loop::set_noise_bandwidth` 用的是
  `ωn = Bn · (8ζ)/(4ζ²+1)`，**不乘 2π**，且带 ζ 因子。这里直接 `wn = 2π·Bn`，实际环路带宽与标称 `noise_bw=0.01` 对不上（差 ~2 倍以上）。调参时按文档给的 0.001–0.1 范围未必落在期望带宽。
- NCO 用 `exp(+1j·phase)`，误差检测器后接正反馈。我用 RRC 成型信号实测：`exp(+j)` 在零频偏下仍收敛出 `freq=+0.26` 的虚假频偏估计、眼图质量 0.54；改成 `exp(-j·phase)` 后零偏频漂降到 +0.006、眼图质量 0.32。相位环两个符号约定下都能"锁住"（4 象限周期对称），但 `exp(+j)` 下直流频漂明显偏大。**建议在集成链路上用真实信号对比两种符号约定**，并把带宽公式换成 GNU Radio 标准式。
- 环路按**采样率**（sps=4）每样本跑一次符号判决型误差检测器，3/4 的采样落在符号间，误差噪声大；标准做法是按符号率（每 4 个采样）喂一次误差，或先做 Gardner 恢复再给 Costas 喂符号点。

### S2. RRC 滤波器特殊点用浮点相等判断，实际永不触发
**位置：`demod.py:41`**

```python
elif abs(ti) == 1 / (4 * beta):
```

`ti` 是整数 `taps` 除以 `sps=4` 的网格点（…, -0.25, 0, 0.25, …），而 `1/(4·0.35)=0.71428…` 不在网格上，这个分支永远走不到。常规公式在该点有可去奇点，网格不命中时由相邻点近似，**结果不致命**，但应改成 `np.isclose` 或直接在构造时用 `np.sinc` 形式化避免奇点。

### S3. CCSDS 解扰器多项式自相矛盾，且结构错了
**位置：`demod.py:452-471`**

- docstring 第 452 行写 `x^8+x^7+x^5+x^3+1`（抽头 3/5/7/8）；
- 第 455 行写 `1+x^5+x^7+x^8`（抽头 5/7/8，**漏了 x³**）；
- 代码 `poly=[1,5,7,8]` 实际按后者实现，漏了 x³ 抽头，与 CCSDS LRIT/HRPT 标准不一致。
- 结构上 CCSDS 扰码器是**同步扰码器**（寄存器初始 0xFF，移位时不反馈数据），这里却用 `state[0] = result[i]` 把解扰后的数据喂回移位寄存器，是**自同步扰码器**结构。两者不能互换。
- `state = np.zeros(8)` 初始化为全 0，而 CCSDS 要求全 1。

真机 NOAA/MetOp 信号解不出来时这是高嫌疑点。

### S4. Viterbi 喂的是硬判决比特，浪费软判决增益
**位置：`demod.py:286-287` → `demod.py:377`**

`demodulate()` 末尾 `np.sign(...)` 把符号硬判决到 ±1/√2，`symbols_to_bits()` 再切成 0/1；而 `ViterbiDecoder.decode` 的 docstring 明说"软判决为 0-1 间浮点数"，内部用 Manhattan 距离 `|output-r|` 做分支度量。软硬接口不匹配：要么喂 `0.5±幅值` 形式的软信息，要么把分支度量改成硬汉明距离。当前链路即使 B1–B3 修好，编码增益也会损失 2–3 dB。

### S5. `symbols_to_bits` 的 Gray 码注释与实现不符
**位置：`demod.py:291-308`**

注释画了 Gray 映射表（00→(1,1) 等），实现却是直接 `I=sign(real), Q=sign(imag)` 两个独立比特，没有任何 Gray 编码逻辑。若发射端确实做了 Gray 映射，这里解出来的比特会相邻错两位；若发射端没做 Gray，注释就是错的。需与发射端帧格式对齐。

### S6. 本文件内 Viterbi/解扰器与 demodulate() 没有接线
**位置：`demod.py:265-289` vs `315-486`**

`QPSKDemodulator.demodulate` 只返回复符号，从不调用 `ViterbiDecoder` / `descramble_ccdb`。这些类在本文件内是"可达但未被集成"的死代码段。若项目其他地方 import 后手动调用，B1–B3 会在那里爆炸；若没有任何地方调用，则是未完成的占位实现。

---

## 三、[空壳]/[占位] 汇总

| 位置 | 现象 |
|---|---|
| `demod.py:168, 215-217` | Gardner `gain_mu` 存而不用，环开环（B4） |
| `demod.py:291-308` | `symbols_to_bits` 注释承诺 Gray 映射，实现无 Gray |
| `demod.py:315-486` | Viterbi + 两个解扰器在本文件内未被 `demodulate` 调用（S6） |
| `demod.py:448-473` | CCSDS 解扰器多项式与结构双双偏离标准（S3） |

---

## 四、"实验室绿、真机红"归因小结

按嫌疑度排序：

1. **B4 Gardner 开环**（最高嫌疑）：合成信号无采样率偏差，固定 4 抽 1 能工作；真机 TCXO ppm 偏差 → 采样点扫出眼图 → BER 飙升。
2. **S1 Costas 环按 4× 采样率跑 + 带宽公式偏差**：合成 QPSK 频偏为 0 时能凑活；真机有几十~几百 Hz 频偏时拉不到锁定带宽内，或锁定后抖动大。
3. **B1–B3 Viterbi 三连错**：只要走到解码阶段必错；若实验室测试用的是未编码数据或只测到符号级星座，就不会暴露。
4. **S3 CCSDS 解扰器多项式错**：真机卫星帧解扰后全是噪声。

---

## 五、验证脚本（只读，未改源码）

- 复算脚本：`verify_demod.py`（Viterbi 往返、decisions 非零计数、回溯前驱对比、Gardner gain_mu 引用检查）
- Costas 符号/带宽：`verify_costas2.py`（去 ISI 后测残余旋转角与眼图质量）

关键实测结果：
- `decisions` 每个时刻非零数 = 0（应 64）
- 末端状态 47/比特 1：真前驱 31，`next_state[47,1]=55`，逆转移 = 31
- 正确八进制多项式下仍 101/200 比特错（B1+B2 导致）
- `gain_mu` 在 `work()` 中零引用
