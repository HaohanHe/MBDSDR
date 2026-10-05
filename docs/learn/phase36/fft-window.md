<!-- SPDX-License-Identifier: MIT | Phase36 G4: GNU Radio FFT 窗口/频谱机制深读（clean-room，只学不抄） -->

# Phase36 G4：GNU Radio FFT 窗口与频谱处理

> 精读对象：`repos/gnuradio/gr-fft/`（真读源码：`include/gnuradio/fft/window.h`、`lib/window.cc`、`fft.h`、`lib/fft.cc`）
> 我方对照：`cpp/src/dsp/fft.{h,cpp}`、`cpp/src/dsp/power_spectrum.{h,cpp}`（Hann/Flattop/Blackman 已暴露）、`cpp/src/ui/spectrum_render.h`
> 红线：只学机制，不抄 GPL 代码；file:line 留痕。

## 1. 机制总结

gr-fft 分两层：**window 是一组纯系数查表器**（无状态、每次新建 vector），**fft 是 FFTW plan 的薄封装**（plan 有状态、 Wisdom 持久化）。两层之间没有耦合——窗函数乘在输入上发生在 gr-fft 之外。

### 1.1 window：余弦和（cosine-sum）窗是唯一主轴

头文件枚举 16 种窗（`window.h:28-53`），但实现上 80% 的窗都是同一个模板：

```
w(n) = c0 - c1·cos(2πn/M) + c2·cos(4πn/M) - c3·cos(6πn/M) + c4·cos(8πn/M)
```

`window::coswindow` 提供 3/4/5 系数三个重载（`window.cc:105-114`、`116-126`、`128-140`），M 恒为 `ntaps-1`（对称窗约定，首尾相等）。其余全部是系数表：

| 窗 | 系数 (c0,c1,c2,c3,c4) | 证据 |
|---|---|---|
| Blackman（精确） | 0.42, 0.5, 0.08 | `window.cc:172-175` |
| Blackman-harris 92 dB | 0.35875, 0.48829, 0.14128, 0.01168 | `window.cc:201-202` |
| Nuttall（Nuttall4c） | 0.3635819, 0.4891775, 0.1365995, 0.0106411 | `window.cc:214-217` |
| Nuttall CFD（Nuttall4b） | 0.355768, 0.487396, 0.144232, 0.012604 | `window.cc:220-223` |
| Flattop（SRS SR785） | (1.0, 1.93, 1.29, 0.388, 0.028)/4.63867 | `window.cc:225-230` |

- `blackman_harris(ntaps, atten)` 按 61/67/74/92 dB 四档切换系数（`window.cc:192-207`），非法档直接 `throw out_of_range`。
- **非余弦和窗**只有 Kaiser（修正贝塞尔 I₀ 级数，`window.cc:26-41` 的 `Izero`）与几个多项式/指数窗（Welch/Parzen/Exponential/Riemann/Tukey/Gaussian，`window.cc:272-371`）。
- `build(type, ntaps, param, normalize)` 是统一分发器（`window.cc:373-430`）；`normalize=true` 时先造未归一窗，再按 `1/sqrt(mean(w²))` 除出单位 RMS 窗（`window.cc:378-391`）。
- `max_attenuation(type, param)` 给出每档窗的预期旁瓣衰减表（`window.cc:53-103`），Kaiser/Tukey 用经验线性/分段拟合，Tukey 段注明"中位误差 <0.5 dB，只高不低"（`window.cc:90-94`）。
- **枚举设计细节**：`WIN_NONE=-1` 表示"不加窗"（`window.h:29`），但 `build()` 对 -1 走 default 抛异常（`window.cc:427-428`）——"无窗"由调用方自己跳过分乘，而不是让 build 返回全 1；`WIN_HANNING=WIN_HANN=1`、`WIN_BLACKMAN_HARRIS=WIN_BLACKMAN_hARRIS=5` 是纯别名（`window.h:32`、`37-38`），为兼容旧拼写。
- **选型语义**（从 `window.h:30-52` 旁瓣注释读出）：矩形 21 dB 适合纯正弦测频；Hann 44 dB 是通用观察屏默认；Blackman 74 dB 适合弱信号邻大信号；Flattop 93 dB 峰值平坦，适合**幅度校准**而不是看旁瓣——这解释了为什么我方把 Flattop 列为可选窗而不是默认。

### 1.2 fft：FFTW plan 封装 + Wisdom 持久化 + 全局计划锁

模板类 `fft<T, forward>`（`fft.h:68-119`）用两个 type-trait 把"实数输入/实数输出"折进同一模板：`fft_inbuf<float,false>` → complex（c2r）、`fft_outbuf<float,true>` → complex（r2c）（`fft.h:47-66`）。构造函数顺序（`fft.cc:138-168`）：

```
planner::mutex() 全局加锁                       // fft.cc:148
config_threading(nthreads)                      // fft.cc:157, fftwf_init_threads 仅一次
lock_wisdom() → import_wisdom()                 // fft.cc:158-159, 读 cache/fftw_wisdom
initialize_plan(...)  // FFTW_MEASURE           // fft.cc:161, fft.cc:185
export_wisdom() → unlock_wisdom()               // fft.cc:166-167, 新经验写盘
```

- plan 一律用 `fftwf_plan_many_dft`（复数，`fft.cc:173`）/ `many_dft_r2c`（`fft.cc:210`）/ `many_dft_c2r`（`fft.cc:227`），`nffts` 批次维支持一次算 N 个等大 FFT。
- Wisdom 跨进程由 `boost::interprocess::file_lock` 保护（`fft.cc:48`、`80-92`）；Windows 下还有一套 fgetc/fputc 适配（`fft.cc:17-33`）。
- 析构也要拿 `planner::mutex()` 再 `fftwf_destroy_plan`（`fft.cc:243-249`）——FFTW 计划的创建/销毁本身非线程安全。
- 缓冲区用 `volk::vector`（SIMD 对齐），`get_inbuf()/get_outbuf()` 直接泄指针（`fft.h:92-99`），零拷贝喂数据；`execute()` 一行 `fftwf_execute(d_plan)`（`fft.cc:265-268`），输入输出指针在建 plan 时就被 FFTW 记住，运行期不做任何拷贝。
- 线程数运行期可调：`set_nthreads(n)` 改 `fftwf_plan_with_nthreads`（`fft.cc:252-262`），且 `static_assert(sizeof(fftwf_complex)==sizeof(gr_complex))`（`fft.cc:150-151`）保证 reinterpret_cast 安全。

### 1.3 整条频谱链在 gr-fft 里的位置

gr-fft 只提供"窗系数 + FFT 原语"两步；Window→乘加→FFT→|·|²→dB→帧平均这条链，GNU Radio 放在 `gr-filter`/`gr-fft/ctrlport_probe_psd_impl.cc` 等上层。对照我方：`PowerSpectrum::process`（`power_spectrum.cpp:106-146`）已经把这条链（窗乘 → `fft` → `std::rotate` fftshift → `std::norm` → 可选 ring 平均 → `10·log10`）收口在一个类里，结构上与上游分层同形，只是少了"窗可选系数表"和"窗归一化"两个旋钮。

### 1.4 上游设计取舍（与我方不同的地方）

- **窗不缓存**：`build()` 每次新建 `vector<float>`（`window.cc:374` 起），无 n→窗表。我方 `PowerSpectrum::windowLen_` 缓存（`power_spectrum.cpp:38`、`112`）反而更省——上游是"库不管、调用方自己缓存"的薄 API 风格。
- **系数 float、特殊函数 double**：余弦窗循环用 `float`（`window.cc:111`），Kaiser 的 `Izero` 全程 `double`（`window.cc:26-41`）再最后转 float——耗时的特殊函数保精度，高频 cos 循环省带宽，是合理的分层。
- **无 overlap-add / STFT 封装**：gr-fft 不管帧叠，帧管理在上层。我方单帧观察屏同样不需要。

## 2. 关键算法（file:line 证据）

1. **余弦和窗系数表驱动**：所有"高阶旁瓣"窗（BH/Nuttall/Flattop）= 一个循环 + 一张表，`window.cc:105-140`。加新窗 = 加一行系数，不加分支。
2. **非余弦窗的对称折叠技巧**：Welch/Parzen/Exponential/Gaussian/Tukey/Riemann 都只算 `[0, mid]` 一半，再 `taps[ntaps-1-i]=taps[i]` 镜像（如 `window.cc:277-280`、`308-311`、`365-369`）。这比写全循环少一半 exp/pow 调用——值得借用到我方任何对称窗。
3. **Kaiser 端点防 NaN**：循环外单独写 `taps[0]=taps[n-1]=1/Izero(beta)`，注释直接引用 GitHub issue #1348（`window.cc:243-255`）——端点处 `sqrt(1-(1+eps)²)` 会出 NaN。
4. **单位能量归一化**：`normalize` 走 `accumulate(b*b)/size` 再 `sqrt`（`window.cc:380-388`），不是除以峰值。
5. **FFTW_MEASURE + Wisdom 缓存**：首次构造慢、之后从盘读经验（`fft.cc:94-106`）；plan 与 buffer 生命周期绑定，禁止拷贝（`fft.h:83-84` `= delete`）。
6. **批处理 many-plan**：`nffts` 让一次 plan 服务多个等长 FFT（`fft.cc:173-185`），避免逐帧重复 plan 开销。
7. **Tukey 的 alpha 连续族**：alpha=0 退矩形、alpha=1 退 Hann（`window.h:337-340` 注释），实现上边缘半段余弦、中间恒 1（`window.cc:345-353`）——"一族窗一个参数"的设计，比枚举离散窗更省概念空间。
8. **Kaiser 的 I₀ 级数**：`Izero(x)` 是修正贝塞尔函数的级数展开 `Σ (x²/4)^k / (k!)²`，循环条件 `u >= 1e-21·sum` 控制收敛（`window.cc:24-41`）。Kaiser 窗 = `I0(β√(1-t²))/I0(β)`，β 越大主瓣越宽旁瓣越低——它是唯一"连续可调"的经典窗，代价是要自己实现一个特殊函数。

## 3. 可借鉴点

- **系数表驱动的窗注册**：把 `(名称, 系数表, 旁瓣 dB)` 三元组存成静态表，`build()` 变成查表+一次循环。我方现在 `rebuildWindow` 里 if/else 三种窗硬编码（`power_spectrum.cpp:87-100`），加第四种窗就要改函数本体。
- **半窗镜像**：对称窗只算一半再镜像（`window.cc:277-280`），我方 `makeHannWindow`/`rebuildWindow` 是全循环——对 8192 点窗每次 rebuild 省一半 cos 调用，rebuild 虽不频繁但零风险。
- **窗归一化保持电平一致**：`normalize=true` 语义（`window.cc:378-391`）对应我方真痛点——见 §4。
- **Kaiser 端点特判**：若将来加 Kaiser，照抄这个防 NaN 模式（循环外写端点），不用自己踩 #1348。
- **plan/构造期锁**：我方手写 FFT 无 plan，但"重资源构造走全局锁"这一原则适用于任何 DSP 状态对象（如 `Channelizer` 重系数）。
- **枚举自文档化**：`win_type` 每个枚举值行尾都写旁瓣 dB（`window.h:30-52`），用户不用查外部资料就知道选窗代价。我方 `PowerSpectrum::Window`（`power_spectrum.h:22`）三档无注释——顺手补上即可，成本为零。

## 4. 我方差距判定（对照 fft.h / power_spectrum.h / spectrum_render.h）

| 项 | GNU Radio | 我方现状 | 判定 |
|---|---|---|---|
| 窗种类 | 16 种，系数表驱动 | Hann/Flattop/Blackman 三种 if/else（`power_spectrum.cpp:87-100`） | **YAGNI**：SDR 观察屏三种够用；但建议重构成表驱动（低风险、加窗零成本），不算补齐 |
| 窗切换电平漂移 | `normalize` 可选单位 RMS（`window.cc:378-391`） | 硬编码 `scale=4/(n·n)`（`power_spectrum.cpp:142`），对窗无补偿 | **真差距，建议补齐**：不同窗 processing gain（`sum(w)/n` 与 `sum(w²)/n`）差异显著——Hann 峰值衰减约 -6 dB、Flattop（SciPy 系系数）峰值衰减约 -13 dB 量级，用户在三种窗之间切换时整屏 dBFS 电平会漂移数 dB，误以为信号变了。修法：`rebuildWindow` 末尾除 `sqrt(mean(w²))`（与 GNU Radio `normalize` 同机制），并把归一化因子乘进 `scale`，三窗电平即对齐 |
| 窗对称性 | M=n-1 对称窗（`window.cc:108`） | 同（`power_spectrum.cpp:83-85`） | 一致，无差距；STFT 50% 重叠用周期窗（M=n）理论更优，但观察屏场景差别 <0.1 dB，**YAGNI** |
| FFT 引擎 | FFTW MEASURE + Wisdom + 多线程 + many-plan | 手写 radix-2 in-place，幂二校验，每次重算 twiddle（`fft.cpp:42-46`） | **YAGNI（明确理由）**：我方 FFT 尺寸 2048–8192、帧率 ~30 fps，手写 radix-2 在现代 CPU 上 ~0.1–0.5 ms，不构成热点；引入 FFTW 依赖违反"无外部依赖"（`fft.h:2` 自注）。唯一值得做的小优化：twiddle 因子按 n 缓存（thread_local 即可，`power_spectrum.cpp:32-37` 已有 thread_local 窗缓存范式可复用） |
| r2c 实数 FFT | 模板特化支持（`fft.cc:208-239`） | 无（频谱全是复输入） | **YAGNI**：IQ 源本就是复数；实数路径无调用方 |
| 旁瓣衰减文档 | `max_attenuation` 表 + 枚举注释（`window.h:30-52`） | 无 | 顺手补：枚举注释里写 44/93/74 dB 即可，零代码 |
| spectrum_render.h | — | block-max 抽稀/LUT/JSON 色板/nice tick（`spectrum_render.h:82-107`、`121-140`、`261-279`） | GNU Radio 不做渲染层，无对照项；我方这块已独立 clean-room 完成，**无差距** |

**电平漂移的定量推导**（为何判"真差距"）：满幅单音 A=1、bin 对齐时，加窗 DFT 峰值 ≈ A/2 · Σw。我方 `scale=4/n²` 是按矩形窗 `(n/2)²·4/n²=1`（0 dBFS）标定的。代入三窗：

- Hann：`Σw ≈ n/2` → 峰值功率 `(n/4)²·4/n² = 0.25` → **-6.0 dB**；
- Blackman：`Σw ≈ 0.42n` → 约 **-7.5 dB**；
- Flattop（SciPy 系 c0=0.2156）：`Σw ≈ 0.2156n` → 约 **-13.3 dB**。

即用户在 Hann/Blackman/Flattop 间切换，同一信号显示电平会跳 ~7 dB。GNU Radio `normalize=true` 把窗除成单位 RMS 后，`scale` 与窗无关，三窗读数即对齐。

## 5. 反例核查（避免照搬时踩坑）

1. **Flattop 系数有两个流派**：GNU Radio 用 SRS SR785 仪表系数（`window.cc:225-230`），头文件 `window.h:251-255` 明确警告"与 SciPy/Matlab 不同"；**我方用的正是 SciPy/D'Antona 系系数 0.21557895…（`power_spectrum.cpp:91-92`）**。不要因为"GNU Radio 这么写"就把系数表换成 SRS 版——那会让 Flattop 峰值形状和历史校准值同时改变。换系数前必须重测电平。
2. **Blackman 写法相位差 0.5 等价**：我方 `0.42 + 0.5·cos(2π(x-0.5)) + 0.08·cos(4π(x-0.5))`（`power_spectrum.cpp:98-99`）经 `cos(θ-π)=-cosθ` 化简与 GNU Radio `0.42 - 0.5·cos(2πx) + 0.08·cos(4πx)`（`window.cc:174`）完全等价——已核对，不是 bug。
3. **GNU Radio 不是周期窗**：`coswindow` 全部 M=n-1（`window.cc:108`）。若笔记或日后实现里写"GNU Radio STFT 用周期窗"，是错的。
4. **`WIN_BLACKMAN_hARRIS` 小写 h**（`window.h:36`）是历史遗留拼写，枚举别名 `WIN_BLACKMAN_HARRIS=5`（`window.h:37-38`）只是兜底——移植枚举名时别顺手"纠正"大小写，会断兼容。
5. **Kaiser 不能省端点特判**：`window.cc:243-249` 的注释是真实 issue（#1348），不是过度防御；若我方补 Kaiser，必须同样循环外写首尾点。
6. **Wisdom 文件锁不是可选优化**：多进程同时首启会互踩 `fftw_wisdom`（`fft.cc:80-92`）。我方无 FFTW，此条不适用，仅作"持久化经验文件需文件锁"的范式记录。
7. **FFTW_MEASURE 的首调代价**：`FFTW_MEASURE` 标志意味着首次 `initialize_plan` 要把几十种算法各跑一遍计时（`fft.cc:185`），构造期可能慢几百毫秒——这就是它必须配 Wisdom 持久化的原因。我方手写无 plan 的 FFT 恰好绕开这个问题，但反过来说：若将来为提速引入任何"构造时选最优算法"的机制，必须同步想清楚首调延迟和经验缓存，不能只抄"MEASURE"三个字。
8. **`build()` 不处理 WIN_NONE**：如 §1.1 所述，-1 走 default 抛异常（`window.cc:427-428`）。我方 `PowerSpectrum::Window` 枚举无"无窗"档（`power_spectrum.h:22`），若日后加矩形窗选项，应学 GNU Radio 让调用方显式跳过分乘，而不是造一个全 1 窗再乘一遍——白白多一次 N 点乘法。

## 6. 本轮结论

- **建议落地（Wave 2 候选，高价值低成本）**：窗 RMS 归一化（修 Hann/Flattop/Blackman 切换电平漂移）+ 窗函数重构为系数表驱动。两者都在 `power_spectrum.cpp` 内，带 ctest 守住回归。
- **验证计划（落地时）**：单测断言 (a) 三窗 `sqrt(mean(w²))` 归一化后 `process()` 输出的 0 dBFS 满幅正弦峰值差 <0.5 dB；(b) 各窗首尾点对称 `w[0]==w[n-1]`；(c) 表驱动重构前后 Hann/Blackman 逐点一致（Flattop 不动系数表，见反例 1）。
- **明确不做**：换 FFTW、加 r2c、加 Kaiser/Nuttall/BH、周期窗——无调用方、违反零依赖原则或收益 <0.1 dB。
