<!-- SPDX-License-Identifier: MIT | Phase36 G3: GNU Radio AGC 机制深读（clean-room，只学不抄） -->

# Phase36 G3：GNU Radio AGC 与增益控制

> 精读对象：`repos/gnuradio/gr-analog/`（真读源码）
> 我方对照：`cpp/src/dsp/agc.{h,cpp}`（Phase35 已落地块级峰值前瞻防削波）
> 红线：只学机制，不抄 GPL 代码；file:line 留痕。

## 1. 机制总结

GNU Radio 的 AGC 家族分四条技术路线，本质区别在"增益由谁决定、何时更新、是否看未来"：

| 类 | 拓扑 | attack/release | 看未来？ | 增益更新频率 |
|---|---|---|---|---|
| `kernel::agc_cc` / `agc_ff` | 反馈 leaky integrator | 无（单 rate） | 否 | 每样本 |
| `kernel::agc2_cc` / `agc2_ff` | 反馈，双速率切换 | attack 1e-1 / decay 1e-2 | 否 | 每样本 |
| `agc3_cc_impl` | 反馈 IIR on 目标增益 + 首块线性快捕 | attack/decay | 否（但首块扫 4×decim 样本） | 每 `iir_update_decim` 样本 |
| `feedforward_agc_cc` | 前馈块峰值取反 | 无（无反馈环） | 是（向后看 nsamples） | 每样本（但增益随窗内峰值跳变） |

> 注：上游无独立 `fast_agc` 类；规格表写的 "fast_agc" 对应 `agc3_cc`（首块线性快捕）与 `agc2` 的快 attack 模式。

### 1.1 agc（单极点反馈，无 attack/release 区分）

`include/gnuradio/analog/agc.h:58-68`（cc 版）核心三行：

```
output = input * gain;
gain  += rate * (reference - |output|);   // leaky integrator on 误差
gain   = min(gain, max_gain);
```

- `rate` 默认 1e-4（`agc.h:40`），极慢；无独立 attack/release——信号突然变大时增益下降和信号变小时增益上升用同一个速率。
- `agc_ff`（`agc.h:118-125`）把 `sqrt(re²+im²)` 换成 `fabsf(output)`，省一次 sqrt。
- **机制本质**：增益是被误差直接积分的状态变量；`rate` 同时是环路带宽和阻尼。

### 1.2 agc2（快慢双环）

`include/gnuradio/analog/agc2.h:64-85`（cc 版）：

```
tmp   = |output| - reference;
rate  = (tmp > gain) ? attack_rate : decay_rate;   // agc2.h:70-73
gain -= tmp * rate;
if (gain < 0) gain = 10e-5;                        // agc2.h:78-79，上游自注 "Not sure"
gain  = min(gain, max_gain);
```

- attack_rate 默认 1e-1、decay_rate 默认 1e-2（`agc2.h:41-42`），差 10×。
- **切换判据古怪**：`tmp > gain`（误差超过当前增益才切快），不是"信号在上升"。`agc2_ff`（`agc2.h:141-145`）用 `fabsf(tmp) > gain`，对称但同样是经验判据。
- 负增益地板 `10e-5` 是速率过大导致环跑飞时的创可贴（`agc2.h:76-79` 注释原话），**不是特性**。

### 1.3 agc3（快捕 + IIR 目标增益平滑）

`lib/agc3_cc_impl.cc:130-183`：

- **首块线性快捕**（`agc3_cc_impl.cc:138-156`）：reset 后扫 `iir_update_decim*4` 个样本求平均幅度，直接 `gain = reference * N / sum(|x|)`，一步到位；再用 volk 批量缩放这 N 个样本。
- **稳态 IIR**（`agc3_cc_impl.cc:159-180`）：每 `iir_update_decim` 个样本采一个幅度 `mag`：
  ```
  rate = (reference > gain*mag) ? decay : attack;   // agc3_cc_impl.cc:164
  gain = gain*(1-rate) + reference*rate/mag;        // agc3_cc_impl.cc:167
  ```
  注意这里平滑的是 `reference/mag`（目标增益）本身，不是 agc/agc2 的"误差积分"。数学上等价于对 1/|x| 做一阶低通。
- **isnormal 守卫**（`agc3_cc_impl.cc:163,168-169`）：mag 非正规数（近零）时 `gain *= (1-decay)`，避免除零爆增益。
- **线程安全 setter**（`agc3_cc_impl.cc:97-128`）：每个 set_* 都拿 `d_setter_mutex`，因 UI 线程会热改参数。

### 1.4 feedforward_agc（前馈，非因果）

`lib/feedforward_agc_cc_impl.cc:54-73`：

```
for i in output:
    max_env = 1e-4;                                  // agc.cc:65，兼作 max_gain 隐式上限
    for j in 0..nsamples:
        max_env = max(max_env, envelope(in[i+j]));   // 看未来 nsamples
    gain = reference / max_env;
    out[i] = gain * in[i];
```

- `set_history(nsamples)`（`agc_cc_impl.cc:37`）让调度器给 lookahead 历史。
- **包络逼近**（`agc_cc_impl.cc:43-52`）：`max(r,i) + 0.4*min(r,i)`，省 sqrt，给高采样率向量化用。
- **无反馈环**：每个样本的增益由"它后面 nsamples 个样本的峰值"决定，增益逐样本跳变——音频里会 zipper noise，RF 峰值检测场景（如脉冲信号归一）才合适。

## 2. 关键算法 file:line 索引

| 机制 | 文件:行 | 要点 |
|---|---|---|
| 单率反馈积分 | `gr-analog/include/gnuradio/analog/agc.h:62-63` | `gain += rate*(ref-\|out\|)` |
| 浮点 fabs 包络 | `agc.h:121` | `gain += (ref-fabsf(out))*rate` |
| 双率切换判据 | `agc2.h:70-73` | `tmp>gain ? attack : decay` |
| 负增益地板 hack | `agc2.h:78-79` | `gain=10e-5`（上游自疑） |
| 首块线性快捕 | `lib/agc3_cc_impl.cc:138-156` | `gain=ref*N/sum(\|x\|)` |
| IIR on 目标增益 | `agc3_cc_impl.cc:167` | `gain=gain*(1-r)+ref*r/mag` |
| isnormal 守卫 | `agc3_cc_impl.cc:163,168-169` | 近零 mag 时衰减增益 |
| 互斥 setter | `agc3_cc_impl.cc:97-128` | 热改参数线程安全 |
| 前馈块峰值 | `lib/feedforward_agc_cc_impl.cc:63-71` | `gain=ref/max_{i..i+n}\|x\|` |
| 包络 sqrt 逼近 | `feedforward_agc_cc_impl.cc:43-52` | `max+0.4*min` |

## 3. 可借鉴点（机制层，不抄代码）

1. **IIR on 目标增益**（`agc3:167`）：`gain ← gain·(1-r) + (ref/|x|)·r` 比 agc/agc2 的"误差积分"在数学上更稳——它直接平滑 inverse magnitude，不会因误差项符号来回冲激。但我方 `env ← env+α·(|x|-env); g=ref/env`（`agc.cpp:60,65`）是包络跟随后除法，拓扑不同但等价，**不换**。
2. **首块线性快捕**（`agc3:138-156`）：reset 后用前 N 样本平均一步定增益，避免 env=0 起步时增益冲到 ceiling。我方 reset 后 `env_=0`（`agc.cpp:36`），首块靠块级前瞻兜底（见 §4），但**第一样本**仍会先算 `gRaw=0.3/1e-4=3000→cap 12` 再被前瞻 pin。可借鉴：reset 后用块首样本均值直接估 `env_`，省掉首样本的 cap→pin 跳变。**候选小补**。
3. **isnormal 守卫**（`agc3:163,168`）：近零幅度时显式衰减增益而非除零。我方用 `max(env,1e-4)`（`agc.cpp:65`）地板除法，语义等价但不区分"信号真近零"和"噪声底"。**已对齐**。
4. **max_env 地板兼作 max_gain**（`feedforward:65`）：一个常量同时防除零和限幅，简洁。我方 `maxGain_` 与 `1e-4` 地板分开写（`agc.h:24`、`agc.cpp:65`），更显式。**已对齐**。
5. **互斥 setter**（`agc3:97-128`）：UI 线程热改 attack/decay 时加锁。我方 `setAttackMs/setDecayMs` 是无锁 inline（`agc.h:42-43`），当前 DSP 单线程独占 AGC 状态，**YAGNI**；未来若从 UI 线程热改音频 AGC 参数再加。

## 4. 我方差距判定（对照 `cpp/src/dsp/agc.{h,cpp}`）

Phase35 已落地：块级峰值前瞻防削波（`agc.cpp:43-84`）。逐项裁决：

| GNU Radio 机制 | 我方现状 | 判定 | 理由 |
|---|---|---|---|
| agc 单率反馈 | 我方是**非对称** attack(5ms)/decay(100ms) 包络跟随（`agc.cpp:59-60`） | **反超** | 单率是退化形态；我方非对称更贴合语音 |
| agc2 双率切换 | 我方 `mag>env ? attackα : decayα`（`agc.cpp:59`）按"信号是否上升"切，比 agc2 的 `tmp>gain` 古怪判据更正确 | **反超** | agc2 判据是经验 hack；我方是教科书包络跟随 |
| agc2 负增益地板 hack | 我方 `g=ref/max(env,1e-4)` 结构上恒正，无负增益可能 | **反超** | 不抄创可贴 |
| agc3 IIR on 目标增益 | 我方包络跟随后除法（`agc.cpp:60,65`） | **已对齐** | 拓扑不同等价，不换 |
| agc3 首块线性快捕 | 我方 reset env=0，首样本 cap→前瞻 pin 兜底（`agc.cpp:36,75-79`） | **小补候选** | 见 §3.2：reset 后用块首均值估 env_，消除首样本跳变；~5 行 |
| agc3 IIR 抽取更新（每 N 样本） | 我方每样本更新；音频 48kHz 下 abs+乘加开销可忽略 | **YAGNI** | 抽取是给高采样率 IQ 省算力；我方 ComplexCarrierAgc（`agc.cpp:109-116`）在 IQ 域跑，但每样本 abs() 也不热 |
| agc3 isnormal 守卫 | `max(env,1e-4)` 地板（`agc.cpp:65`） | **已对齐** | 语义等价 |
| agc3 互斥 setter | 无锁 inline（`agc.h:42-43`） | **YAGNI** | 当前单 DSP 线程；热改参数路径在配置层 |
| feedforward 逐样本块峰值取反 | 我方**块级**前瞻：扫一次块峰值 O(n)（`agc.cpp:51-55`），若 `blockPeak*g>ceiling` 则 pin env（`agc.cpp:75-79`），增益仍由反馈包络决定 | **反超（混合拓扑更优）** | 纯前馈逐样本增益跳变会 zipper noise；我方是"反馈包络 + 前馈峰值防削波"混合，既稳又防瞬态削波 |
| feedforward 包络 sqrt 逼近 | 我方音频用 `std::abs(float)`（`agc.cpp:58`），IQ 用 `std::abs(complex)`（`agc.cpp:111`） | **YAGNI** | 逼近是给高采样率向量化省 sqrt；音频域非热点 |
| max_gain 上限 | `maxGain_=12`（音频，`agc.h:24`）/ `10000`（carrier，`agc.h:79`） | **已对齐** | 我方还多了 OutputCeiling=1.0 输出钳位（`agc.h:30`） |
| 输出限幅 | `std::clamp(-1,1)`（`agc.cpp:82`） | **已对齐** | 且前瞻让峰值样本落在 ~target 而非撞 clamp |
| history/lookahead | 我方天然整块 vector 在手（`agc.cpp:38-84`），无需 set_history | **已对齐** | 块式处理自带 lookahead |

### 4.1 唯一候选小补：reset 首样本快捕

我方 `reset()` 仅 `env_=0`（`agc.cpp:36`）。首样本：
- `gRaw = 0.3/max(0,1e-4) = 3000 → g=min(3000,12)=12`；
- 前瞻检查 `blockPeak*12 > 1.0` → 若本块有信号（blockPeak>0.083），pin `env_=blockPeak`，重算 g。

功能正确，但首样本经历了一次"cap 12 → pin"的增益跳变。借鉴 agc3 首块均值法：`processWithGain` 开头若 `env_==0`（刚 reset），用块首 N 样本平均幅度直接初始化 `env_`，省掉跳变。**判定：值得补，但非本轮 G3 范围**——记 gap-table，等 Wave2 落地时一并。

## 5. 反例核查（上游不要学的地方）

1. **agc2 的 `if (gain<0) gain=10e-5`**（`agc2.h:78-79`）：上游自己注释 "Not sure about this; will blow up if gain<0… but is this the solution?"。这是速率过大时环跑飞的创可贴，暴露了 agc2 反馈方程本身在参数不当时不稳定。我方 `g=ref/env` 结构上恒正，**不要引入负增益概念**。
2. **agc2 的 attack/decay 切换判据 `tmp>gain`**（`agc2.h:71`）：用误差和增益比大小来切快慢，不是"信号上升/下降"。`agc2_ff` 的 `fabsf(tmp)>gain`（`agc2.h:143`）更怪——对称地把"信号低于参考很多"也切快 attack，会让安静时增益乱跳。我方 `mag>env` 才是正确的包络跟随判据。
3. **feedforward_agc 逐样本增益**（`feedforward:63-71`）：每个样本 i 用 `[i,i+nsamples)` 的峰值算独立增益，导致增益在窗内逐样本跳变。音频场景会 zipper/modulation noise。我方把前瞻当"防削波 pin"而非"增益源"，**不要学纯前馈逐样本增益**。
4. **agc/agc_ff 单率**（`agc.h:62,121`）：无 attack/release 区分，语音场景下"大声压小声"和"小声拉大声"同速，听感闷。这是入门实现，不是目标形态。
5. **agc3 的 volk 批量缩放首块**（`agc3:150-153`）：`volk_32f_s32f_multiply_32f` 给首 N 样本一次乘。我方是逐样本 `out[i]=in[i]*g`（`agc.cpp:82`），音频块小（~1000 样本），volk 调用开销不划算。**不引入 VOLK 依赖**（与 Phase35 gap-table §2 决议一致）。

## 6. 收口

- 我方 AGC（音频包络跟随 + 块级前瞻防削波 + ComplexCarrierAgc）在**拓扑正确性**上已优于 GNU Radio agc/agc2；与 agc3 比，缺一个"reset 首样本均值快捕"小优化。
- **本轮 G3 不补代码**：候选小补（reset 首块 env 初始化）记 Wave2 gap-table，~5 行，带 ctest。
- **反例**：agc2 负增益地板、`tmp>gain` 切率、纯前馈逐样本增益、单率 agc——均不引入。
- 证据：`gr-analog/include/gnuradio/analog/agc.h:58-138`、`agc2.h:64-170`、`lib/agc3_cc_impl.cc:130-183`、`lib/feedforward_agc_cc_impl.cc:43-73`；我方 `cpp/src/dsp/agc.h:11-97`、`agc.cpp:9-127`。
