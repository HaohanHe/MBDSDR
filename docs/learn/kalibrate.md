# kalibrate（kal）GSM 频率校准工具学习笔记

> 本笔记在干净室条件下只读 `repos/kalibrate`（steve-m/kalibrate-rtl 分支，
> 面向 RTL-SDR 的移植版）C++ 源码整理而成，不逐字复制，每条结论尽量标
> `文件:行号`。目标：搞清它**如何用 GSM 基站当频率基准算出本地晶振的 ppm
> 偏差**，以及 MBDSDR 若要做「自动频率校准」能搬哪几步、需要什么真实信号。
>
> 标注约定：
> - 【源码】= 直接在 `repos/kalibrate/src/*.cc/.h` 看到的事实；
> - 【推断】= 由代码 + GSM 常识推得，源码未明文写出；
> - 【空态】= 当前 MBDSDR 无硬件 / 无真实 GSM 信号，不能跑实测的部分。

## 1. 数据源（实际读过的源文件）

| 文件 | 作用 |
|---|---|
| `src/kal.cc` | 主入口：CLI 解析、打开 RTL-SDR、选扫描/校准两条路径 |
| `src/fcch_detector.cc/.h` | 核心算法：ALE 自适应线谱增强器找 FCCH 突发 + FFT 峰值测频 |
| `src/offset.cc/.h` | 已知信道后的 ppm 测量：循环采集 100 次、截尾平均、出 ppm |
| `src/c0_detect.cc/.h` | 扫描模式：逐信道测功率门限，再找 FCCH，列出周围基站 |
| `src/arfcn_freq.cc/.h` | ARFCN ↔ 频率换算、频段枚举、信道步进 |
| `src/usrp_source.cc/.h` | RTL-SDR 封装（类名仍叫 usrp_source，实为 librtlsdr）：采样率/增益/ppm/调谐 |
| `src/util.cc/.h` | `sort` / `avg` / `display_freq` 小工具 |
| `src/circular_buffer.h` | 环形缓冲区接口（实现略） |
| `README.md` | 原理叙述与示例输出 |

依赖（`src/Makefile.am:22-23`）：`fftw3` + `libusb/rtlsdr`。

## 2. 校准原理：为什么 GSM 基站能当频率基准

【源码 / README】GSM 基站（BTS）被规范要求载频准确度 ≤ **0.05 ppm**
（README "On Clocks" 节），远优于廉价 RTL-SDR 棒内晶振的典型 ±20 ppm
（README 同节；`librtlsdr.md` 已记录典型 20~50 ppm）。因此可以把基站发射
的某个**已知频率分量**当尺子，量出本地 LO 偏了多少。

这个已知分量是 **FCCH（Frequency Correction CHannel）突发**：

- 【源码 / README】FCCH 是 GSM 下行的一个标准时隙图案，调制后是一个**纯正弦**，
  频率恰为 GSM 比特率的 1/4：
  ```
  GSM_RATE = 1625000 / 6 = 270833.333… symbols/sec   (kal.cc:70)
  FCCH_tone = GSM_RATE / 4 = 67708.333… Hz
  ```
  常数 `GSM_RATE` 在 `kal.cc:70`、`fcch_detector.h:68`、`offset.cc:45`、
  `c0_detect.cc:61` 重复定义。
- 【源码 / README】FCCH 每 51 帧重复一次，落在帧 0/10/20/30/40 的时隙 0。
  也就是说每隔约 10 帧（≈ 10×8×156.25 = 12500 symbol ≈ 46 ms）就有一个
  长度约 148 symbol 的纯音突发。

**测量本质**：把接收机中心频率设到某个已知 ARFCN 载频上，基带里应当看到一个
+67708.3 Hz 的纯音。如果本地晶振偏了 δ ppm，这个纯音在基带里的实测位置就会
相对 67708.3 Hz 偏移 Δf。于是 δ ≈ Δf / f_center × 10⁶。

【推断】注意：FCCH 纯音是 BTS 发射的、落在载频 +67.7 kHz 处；接收机把载频
混到基带后，这个纯音应当出现在正频率侧 ~+67.7 kHz。源码里 `itof()` 把
负频率折回的分支是**注释掉的**（`fcch_detector.cc:278-283`），即只在正频率
半边找峰——与该物理图像一致。

## 3. 采样率 / FFT 参数（硬编码，全部【源码】）

kalibrate-rtl 并没有像原 USRP 版那样按 FPGA 主时钟算抽取比，而是把 RTL-SDR
采样率**写死**了：

| 参数 | 值 | 位置 |
|---|---|---|
| 采样率 | `270833.002142` Hz（浮点成员） | `usrp_source.cc:189` |
| 下发给 librtlsdr 的采样率 | `270833`（uint32） | `usrp_source.cc:187,213` |
| FFT 点数 | `FFT_SIZE = 1024` | `fcch_detector.h:69` |
| ALE 预测延迟 D | `8` | `fcch_detector.h:52`（默认参），`fcch_detector.cc:73` `m_filter_delay=8` |
| ALE 抽头长 | `m_w_len = 2*8+1 = 17` | `fcch_detector.cc:74-75` |
| ALE 误差 EMA 系数 p | `1/32` | `fcch_detector.h:52` |
| ALE 步长 G | `1/12.5`（后续会被归一化钳位） | `fcch_detector.h:52`；钳位见 `fcch_detector.cc:468-469` |
| FCCH 突发长度（采样点） | `148 * (sr/GSM_RATE)` ≈ 148 | `fcch_detector.cc:70-71` |
| 最短有效突发（采样点） | `MIN_FB_LEN = 100 * sps` ≈ 100 | `fcch_detector.cc:349` |
| 峰均功率比门限 | `MIN_PM = 50` | `fcch_detector.cc:350`（注释自承 "arbitrary"） |
| 误差判决门限 | `0.7 * avg(error)` | `fcch_detector.cc:372` |
| 单次测量抓样长 | `ceil((12*8*156.25 + 156.25) * sps)` ≈ 15157 点 | `offset.cc:64`、`c0_detect.cc:78` |
| 平均次数 | `AVG_COUNT = 100` | `offset.cc:36` |
| 截尾宽度 | 每端各去掉 `AVG_THRESHOLD = 10`（即中间 80 个求平均） | `offset.cc:37,115` |
| 单次偏移合理性上限 | `OFFSET_MAX = 40000 Hz` | `offset.cc:38`、`c0_detect.cc:41` |
| 连续未找到信道即跳过 | `NOTFOUND_MAX = 10` | `c0_detect.cc:62,181` |

【推断】几个值得记住的数字巧合：

- `sps = sr/GSM_RATE ≈ 270833/270833.33 ≈ 0.999999`，即**几乎 1 个采样/符号**。
  这就是为什么上面所有"符号数"可以直接当"采样点数"用。
- FFT bin 宽 = `sr/1024 ≈ 264.5 Hz`。零频偏时 FCCH 纯音应落在 bin
  `67708.3/264.5 ≈ 256`，正好是 1024 的 1/4。任何 ppm 偏差只会让这个峰
  在 bin 256 附近左右移动——这正是后面要亚-bin 插值的原因。

## 4. 算法细节：ALE 找突发 → FFT 测频 → 截尾平均 → ppm

整体是 README 说的 **ALE + FFT 混合**：ALE 负责"在哪一段数据里有纯音"
（时域、省 FFT），FFT 负责"这段纯音到底多频偏"（频域、精确）。
论文出处：Varma, Sahu, Charan, *Robust Frequency Burst Detection Algorithm
for GSM/GPRS*（`fcch_detector.cc:29-33` 注释）。

### 4.1 ALE 自适应线谱增强（`next_norm_error`，`fcch_detector.cc:452-497`）

对每个采样点跑一次 LMS 自适应预测：

1. 取 17 个抽头的窗 `x[n-16..n]`，算窗内总能量 `E = Σ|x|²`（`:467`）。
2. **归一化步长**：若 `m_G >= 2/E` 则钳到 `1/E`（`:468-469`）——这是把
   步长按输入功率归一，避免大信号发散。
3. 线性预测 `y = Σ conj(w[i])·x[n-i]`（`:472-474`），即用过去 17 点预测当前。
4. 真正的预测目标是 **D=8 个采样之后**的样本 `x[n+D]`：
   `e = x[n+D] - y`（`:479`）。【推断】延迟 D=8 让 ALE 对准窄带周期信号的
   周期，把宽带噪声"预测不掉"而把正弦"预测掉"。
5. LMS 权重更新 `w[i] += G·conj(e)·x[n-i]`（`:482-483`）。
6. 误差功率做 EMA：`m_e = (1-p)·m_e + p·|e|²`，p=1/32（`:487`）。
7. 对外返回的"误差比" = `m_e / (E/m_w_len)`（`:491`）——归一化到输入功率。

**物理意义**：宽带噪声/普通调制信号预测不了，误差比高；一旦进入 FCCH 纯音
段，ALE 很快收敛成一个窄带陷波/预测器，误差比掉到很低。

### 4.2 状态机切出"低误差段"（`scan`，`fcch_detector.cc:346-412`）

1. 把整段采样喂给 ALE，逐点记录误差比到 `m_e_cb`，同时累加 `sum`（`:358-365`）。
2. 算全段平均误差 `avg = sum/e_count`，判决门限 `limit = 0.7·avg`（`:371-372`）。
3. 用 `low_to_high()`（`:148-168`）这个两态机扫误差序列：
   - 误差 < limit → LOW 态，连续计数；
   - 误差 ≥ limit → HIGH 态；LOW→HIGH 跳变时，返回刚才连续 LOW 的样本数
     `l_count`（`:152-157`）。
4. 若 `l_count >= MIN_FB_LEN`（≈100 点），认为这是一个候选突发；取突发开头
   最长 `m_fcch_burst_len`（≈148 点）送入 `freq_detect()` 做 FFT（`:385-389`）。
5. `freq_detect` 算出峰均比 `pm`；只有 `pm > MIN_PM(=50)` 才认定真的是纯音
   突发，`break` 取第一个命中（`:392-393`）。

【源码】`peak_valley()`（`:171-198`）和 `ftoi()`（`:288-293`）定义了但全工程
**从未被调用**（已 grep 确认）；`m_check_G`、`m_lpf_len`（`fcch_detector.h:73,75`）
也是声明了没用的遗留成员。移植时可以丢掉。

### 4.3 FFT 测频 + 亚-bin 插值（`freq_detect` / `peak_detect`，`fcch_detector.cc:301-327, 230-271`）

1. 把候选突发段拷进 1024 点 FFT 输入，不足 1024 补零（`:307-315`），跑
   `fftw_execute`（`:317`）。FFT 计划带 `FFTW_MEASURE` + wisdom 落盘
   `~/.kal_fftw_plan`（`:92-101`）。
2. `peak_detect`：先蛮力找 `|X[k]|` 最大的整数 bin `max_i`（`:237-244`）。
3. **亚-bin 插值**：用 21-tap `sinc` 插值核（`interpolate_point`，`:209-227`）
   在 `max_i-1` 和 `max_i+1` 处取插值点，然后用对分/爬山把峰位细化到
   `incr = 0.5 → 0.25 → … → 1/1024`（`:248-261`）。最终峰位是浮点 bin
   `max_i = early_i + 1.0`（`:261`）。
4. 换算频率：`itof(index) = index · (sr/FFT_SIZE)`（`:274-285`）。
5. 峰均比：`pm = |peak|² / avg_power`，其中
   `avg_power = (Σ|X|² − |peak|²)/(N−1)`（`:268,325`）。

【推断】sinc 插值 + 对分爬山是为了把 264 Hz/bin 的分辨率再细分 1024 倍，
理论频率分辨率 ≈ 264/1024 ≈ 0.26 Hz。这就是 README 示例里 stddev 能到
~4 Hz、range ~14 Hz 的来源。

### 4.4 多帧平均与 ppm 合成（`offset_detect`，`offset.cc:43-129`）

1. 新建 ALE 检测器（`:57`），算 `sps` 和单次抓样长 `s_len`（≈15157 点，
   约 56 ms，`:63-64`）。注释明确：抓 12 帧+1 突发是为了**保证至少捕到一个
   FCCH**（`:59-62`）。
2. 循环最多 `AVG_COUNT=100` 次成功检测（`:70`）：
   - `fill()` 阻塞读够 `s_len` 点（`:73-81`），overrun 则 flush 重来；
   - `l->scan()` 找 FCCH 突发，得到基带里实测峰频 `offset`（Hz）（`:87`）；
   - **减去理论 FCCH 频点与 tuner 自身误差**：
     `offset = offset − GSM_RATE/4 − tuner_error`（`:90`）；
   - 只有 `|offset| < OFFSET_MAX(40 kHz)` 才收下（`:93`），否则记 `notfound`；
   - `purge(consumed)` 消费掉已处理样本（`:107`）。
3. 对 100 个 `offsets[]` 排序（`:114`，`util.cc:66` 是 O(n²) 冒泡），
   **截掉每端 10 个**，对中间 80 个求均值和标准差（`:115`）；
   `min = offsets[10]`、`max = offsets[90-1]`（`:116-117`）。
   【推断】截尾是为了剔除偶发误检（邻道串入、ALE 错锁）。
4. **ppm 公式**（`offset.cc:125`，逐符号：）：
   ```c
   total_ppm = m_freq_corr - ((avg_offset + hz_adjust) / u->m_center_freq) * 1000000;
   ```
   - `m_freq_corr`：本次运行时用 `-e` 已经下发给 RTL-SDR 的 ppm 校正值
     （`kal.cc:194-196` → `usrp_source.cc:148-151` 直接调
     `rtlsdr_set_freq_correction`）；
   - `avg_offset`：上面截尾平均得到的**残差**频偏（Hz）；
   - `hz_adjust`：`-E` 手动加的 Hz 偏（`kal.cc:202-204`）；
   - `m_center_freq`：实际调谐到的载频（Hz，`usrp_source.cc:140` 读回）。
   - 打印 `"average absolute error: %.3f ppm"`（`:127`）。

【源码/推断】符号约定：若 `avg_offset = -1 Hz`、`F=872.6 MHz`、未用 `-e/-E`，
则 `total_ppm = 0 − (−1)/872.6e6·1e6 ≈ +1.15 ppm`，即应把设备 ppm 设成
约 +1 后重测。这与 README 示例 `-c 145` 流程一致：先用粗略 `-e` 把大范围偏
差拉回，再用本工具精测残差。【推断】正负号方向以 `rtlsdr_set_freq_correction`
的约定为准（见 `librtlsdr.md` §5：整数 ppm 同时校正采样率并重新锁相），
移植时不要把符号搞反——这是最容易出 bug 的地方。

### 4.5 扫描模式（`c0_detect`，`c0_detect.cc:59-206`）

`kal -s <band>` 不是直接算 ppm，而是列出周围有哪些基站：

1. **粗扫功率**：对频段内每个 ARFCN 逐个调谐，抓 `frames_len` 点，算
   `sqrt(Σ|x|²)` 作为该信道功率（`:87-109`）。
2. **自适应门限**：把所有信道功率排序，**只取最低 60% 求平均**作为门限
   `a`（`:122-125`）。注释解释：频段里可能有 CDMA 等极强干扰，故意把最吵的
   40% 排除在门限之外（`:111-117`）。
3. **细扫 FCCH**：只对 `power > a` 的信道跑 ALE+FFT 检测；若命中且
   `|offset − GSM_RATE/4| < 40 kHz`，打印信道号、频率、频偏、功率
   （`:137-177`）。
4. 连续 10 个信道都没 FCCH 就直接跳到下一个（`:180-184`）。
5. **诚实提示**：只找到 1 个信道 → 提示可能需要 `-e` 给粗 ppm
   （`:188-194`）；多信道间频偏差 > 1 kHz → 提示初始 ppm 差太远，
   建议先用 FM 广播等已知信号粗校（`:198-204`）。

## 5. ARFCN ↔ 频率表（`arfcn_freq.cc`，全部【源码】）

信道栅格 **200 kHz**，下行频段：

| 频段枚举 | ARFCN 范围 | 下行频率 | 公式（`arfcn_freq.cc`） |
|---|---|---|---|
| GSM-850 | 128–251 | 869.2–893.8 MHz | `824.2e6 + 0.2e6·(n−128) + 45e6`（`:96`） |
| GSM-900 | 1–124 | 935.2–959.8 MHz | `890e6 + 0.2e6·n + 45e6`（`:102`） |
| E-GSM-900 | 0, 975–1023 | 925.2–935.0 MHz | `890e6 + 0.2e6·(n−1024) + 45e6`（`:117`） |
| GSM-R-900 | 955–974 | 921.2–925.0 MHz | 同上（`:110-118`） |
| DCS-1800 | 512–885 | 1805.2–1879.8 MHz | `1710.2e6 + 0.2e6·(n−512) + 95e6`（`:127,140`） |
| PCS-1900 | 512–810 | 1930.2–1989.8 MHz | `1850.2e6 + 0.2e6·(n−512) + 80e6`（`:130`） |

反向 `freq_to_arfcn()`（`:148-193`）、`first_chan()` / `next_chan()` 迭代器
（`:196-324`）都是纯查表，无硬件依赖。

## 6. 与 MBDSDR 的相关性：自动频率校准功能

### 6.1 为什么 MBDSDR 需要它

【源码】`librtlsdr.c` 已经提供 `rtlsdr_set_freq_correction(dev, int ppm)`
（见 `librtlsdr.md` §5，`:915-938`），但**需要外部先算出 ppm 值**。
kalibrate 填的就是"自动算 ppm 这一步"。MBDSDR 若加「一键自动校频」，
kalibrate 的整条流水线可以直接搬。

### 6.2 可落地的算法步骤（按依赖顺序）

| # | 步骤 | 来源 | 可否离线单测 |
|---|---|---|---|
| 1 | ARFCN↔频率 / 频段表（纯查表） | `arfcn_freq.cc:91-193` | ✅ 纯 Python，无需硬件 |
| 2 | 合成测试信号：在基带 ~+67.7 kHz 处放一个正弦 + 高斯噪声，再加一个已知 Hz 偏移 | 【推断】由原理推 | ✅ numpy 即可 |
| 3 | ALE `next_norm_error` 17 抽头 LMS + D=8 延迟 + 归一化步长 | `fcch_detector.cc:452-497` | ✅ 用合成信号测"有纯音时误差比下降" |
| 4 | 低误差段状态机 + `MIN_FB_LEN`/`0.7·avg` 门限 | `fcch_detector.cc:346-412, 148-168` | ✅ |
| 5 | 1024 点 FFT + 整数峰搜索 + 21-tap sinc 插值 + 对分爬山 | `fcch_detector.cc:230-271, 209-227` | ✅ 合成正弦测亚-bin 精度 |
| 6 | 单次偏移：`peak − GSM_RATE/4 − tuner_error`，40 kHz 合理性门限 | `offset.cc:90-93` | ✅ |
| 7 | 100 次循环 + 排序 + 中间 80% 截尾平均 + stddev | `offset.cc:114-117` | ✅ |
| 8 | ppm 合成：`ppm = corr − (avg_off + hz_adj)/F_center·1e6` | `offset.cc:125` | ✅ |
| 9 | 扫描模式：逐信道功率粗扫 → 最低 60% 平均门限 → FCCH 细扫 | `c0_detect.cc:87-186` | ⚠️ 需要真实 RTL-SDR |
| 10 | 把结果回写 `rtlsdr_set_freq_correction(ppm)` | `usrp_source.cc:148-151` | ⚠️ 需要真实硬件 |

### 6.3 可落地功能清单（MBDSDR 侧建议）

- **`mbdsdr_ai/gsm_fcch_cal.py`（纯算法，无硬件）**：
  - `arfcn_to_freq(n, band)` / `freq_to_arfcn(f, band)` 查表；
  - `FCCHDetector` 类：`next_norm_error()` 流式喂样、`scan()` 找突发、
    `freq_detect()` FFT 测频；
  - `calibrate_offsets(offsets) -> (avg, min, max, stddev)` 截尾统计；
  - `compute_ppm(avg_offset_hz, center_hz, applied_ppm=0, hz_adj=0) -> ppm`。
- **CLI / MCP 工具**（对齐 `librtlsdr.md` §6-7 的风格）：
  - `kal_scan_band(band)` — 列周围基站（需要硬件）；
  - `kal_calibrate(arfcn_or_freq)` — 出 ppm + stddev；
  - `kal_apply_ppm(ppm)` — 调 `rtlsdr_set_freq_correction`。
- **合成自测**：用 numpy 生成"载频 + 67708.3 Hz 正弦 + 已知 ppm 偏移"，
  喂进算法，断言算出的 ppm 误差 < 1 ppm。这是**无硬件时唯一能 CI 化的
  回归测试**。
- **UI 提示**：校准前显示"需要真实 GSM 下行信号（GSM900/GSM850 等）"；
  校准后显示 `average [min,max] (range, stddev)` 表，对齐 kalibrate 原输出
  （`offset.cc:119-123`）。

### 6.4 真实数据 / 硬件要求（诚实空态）

【空态】当前 MBDSDR 仓库里**没有真实 GSM 基带 IQ 录制文件**，也没接 RTL-SDR
硬件，因此：

- 上面步骤 1–8 可以写成代码 + 合成信号单测，但**不能在本仓库里跑出真实 ppm
  数字**；
- 步骤 9–10 必须在现场：插 RTL-SDR、接天线、所在位置有活跃 GSM 下行才能验。

【源码 + 推断】现实约束：

1. **必须有活的 GSM 网络**。2G 在全球多地退网/refarm，国内 GSM900/DCS1800
   也在逐步退服；【推断】如果所在区域 GSM 已经关站，本工具直接 `not found: N`，
   无法校准。README 自己也说只能校小范围 ppm，大范围偏要先用 FM 广播等
   已知源粗校（README 顶部 + `c0_detect.cc:188-204`）。
2. **采样率锁死 270833 Hz**（`usrp_source.cc:189`）。这是 RTL-SDR 下约等于
   GSM 1 sample/symbol 的特化参数；搬算法时要么照用，要么按 `sps` 重新标定
   `MIN_FB_LEN`、`m_fcch_burst_len` 等比例量（它们已经用 `sps` 缩放，
   见 `fcch_detector.cc:70-71,348-349`，【推断】这点是可移植的）。
3. **频段受 tuner 限制**。按 `librtlsdr.md` §3：R820T/R828D 上限 1766 MHz，
   E4000 上限 1700 MHz。因此：
   - GSM-850（869–894 MHz）、GSM-900（935–960 MHz）✅ 大多数 RTL-SDR 能收；
   - DCS-1800（1805 MHz 起）、PCS-1900（1930 MHz 起）❌ 在常见 R820T 棒
     频率上限之外，kalibrate 虽然支持这两个 band，但普通棒实际扫不到。
4. **粗 ppm 初值**：晶振若偏超过 ~40 kHz（`OFFSET_MAX`，`offset.cc:38`），
   FCCH 峰会被扫出 FFT 可视范围，工具直接报 not found。需要先用 `-e` 给个
   粗值（`kal.cc:194-196`），或先用 FM 广播（100 MHz 整点载波）粗校一次。
5. **耗时**：100 次成功 × ~56 ms/次 ≈ **≥6 秒**，再加上 notfound 重试和
   overrun flush，现场跑一次大概 10–30 秒。

## 7. 源码确认 vs 推断 一览

| 结论 | 标注 |
|---|---|
| FCCH = 67708.3 Hz 纯音、每 51 帧突发 | 【源码】README + `offset.cc:90` |
| 采样率 270833、FFT 1024、ALE 17 抽头/D=8/p=1/32/G=1/12.5 | 【源码】`usrp_source.cc:189`、`fcch_detector.h:52,69` |
| 误差门限 0.7·avg、MIN_PM=50、MIN_FB_LEN=100·sps | 【源码】`fcch_detector.cc:349-350,372` |
| 100 次平均、截尾每端 10、OFFSET_MAX=40 kHz | 【源码】`offset.cc:36-38,115` |
| ppm 公式 `corr − (avg+adj)/F·1e6` | 【源码】`offset.cc:125` |
| 扫描用"最低 60% 平均"做功率门限 | 【源码】`c0_detect.cc:122-125` |
| ARFCN↔频率表 | 【源码】`arfcn_freq.cc:91-193` |
| ALE 物理意义 = 窄带预测、宽带噪声预测不掉 | 【推断】由 LMS 更新式 + README 论文摘要推得 |
| bin 256 ≈ FCCH 零频偏位置 | 【推断】由 sr/1024 与 67708.3 算得 |
| sps≈1 使符号数≈采样数 | 【推断】sr/GSM_RATE 算得 |
| 国内 2G 退网导致无信号时无法校准 | 【推断】行业常识，源码未提 |
| DCS/PCS 在 R820T 棒上收不到 | 【推断】由 `librtlsdr.md` §3 tuner 上限推得 |
| 无硬件/无 GSM 信号时本仓库跑不出真实 ppm | 【空态】当前实测条件 |

## 8. 一句话总结

kalibrate = **用 GSM BTS 下发的 FCCH 纯音（67708.3 Hz）当尺子**：
ALE 自适应滤波器在时域粗定位纯音突发（省 FFT、抗噪），1024 点 FFT + sinc
亚-bin 插值在频域精测峰位（~0.26 Hz 分辨率），减理论频点后截尾平均 100 次，
最后除以载频换算成 ppm，回喂 `rtlsdr_set_freq_correction`。算法本体（步骤
1–8）不依赖硬件、可用合成信号单测；真正出数字必须在现场有活的 GSM 下行。
