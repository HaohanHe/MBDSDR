<!-- SPDX-License-Identifier: MIT | Phase36 块3：反例清单（docs 域）。
来源：本目录五篇笔记 §反例核查。每条 = 上游做法 + file:line + 我方对照结论。
红线：只学机制不抄代码；以下均为"不要照搬"的点。 -->

# Phase36 反例清单（GNU Radio 不要照搬的坑）

> 逐条带上游 file:line 证据与我方对照。结论分两类：**证伪**（常见误读）与
> **不抄**（上游取舍不适用于我方架构/约束）。

---

## 1. 调度与缓冲

### 1.1 goto 编织的状态机
- **上游**：`block_executor.cc` 用 `blkd_in_try_again / blkd_out_try_again / try_again / were_done`
  四处 goto 编织 noutput_items 协商控制流。
- 证据：`gnuradio-runtime/lib/block_executor.cc:292,319,404,431,465,553,568`（二分回退处
  `:548-554` 即 `noutput_items /= 2; goto try_again`）。
- **我方对照**：继续用普通循环 + 显式状态枚举（READY/BLKD_IN/... 不用 goto 拼），
  保证可读可测；我方 pull 泵本身就是顺序调用，无此复杂度。

### 1.2 一块一线程的线程爆炸
- **上游**：TPB 对 used blocks 拓扑排序后逐个 `create_thread`，大扇出图（一源→几十 VFO）
  会起几十个线程，上下文切换与 condvar 开销反噬吞吐——GR 公认已知短板。
- 证据：`gnuradio-runtime/lib/scheduler_tpb.cc:60-89`；主循环 condvar 等待
  `tpb_thread_body.cc:129-147`。
- **我方对照**：坚持单 pump 线程一次跑完一帧（`spectrum_engine.cpp:run()`），
  VFO 在同线程串行扇出，零线程零锁；这正是我方在 OOM/桌面约束下优于 TPB 的理由。

### 1.3 环形 −1 预留槽
- **上游**：`space_available = bufsize - most_data - 1`，永远浪费 1 个槽换"满/空不歧义"判别。
- 证据：`gnuradio-runtime/lib/buffer_double_mapped.cc:137-169`（关键 `:167`）。
- **我方对照**："最近 n 点"覆盖环用显式 `count_` 判别满/空（`iq_buffer.h`），
  不浪费槽，更简单；不引入 −1 预留槽约定。

### 1.4 双映射 mmap 的平台脆弱性
- **上游**：为凑出"同一份物理页映射两次"，要 createfilemapping / sysv_shm / mmap_shm /
  mmap_tmpfile 四种 factory 逐个试兜底；双映射失败再退单映射 memmove 腾环首。
- 证据：`gnuradio-runtime/lib/vmcircbuf.cc:86-90`；退路 `buffer_single_mapped.cc:303-350`。
- **我方对照**：同线程块间传 `std::vector` 整块拷贝，不碰 mmap shm/tmpfile 平台适配面；
  vmcircbuf 的零拷贝价值在跨线程，同线程不划算。

### 1.5 d_detail 隐式转发的双重状态
- **上游**：块调 `consume` 既写 `d_consumed` 又立即推进读指针，产量与消费在两个方法里
  分别记账，新人容易漏调其一。
- 证据：`gnuradio-runtime/lib/block_detail.cc:94-112`；`block.cc:174-184`。
- **我方对照**：块接口保持单一返回值（`process()->vector`），尾状态收敛在对象内
  （`channelizer.h:126 tail_`、demod delayLine_），不做隐式双记账。

---

## 2. 采样率转换

### 2.1 FFT/直接卷积"运行时自动切换"——证伪
- **误读**：以为 `fir_filter` 块内部会在"短抽头直接点积 / 长抽头 FFT"间按阈值自动切换。
- **上游事实**：`kernel::fir_filter` 只走 volk 直接点积，`kernel::fft_filter_*` 只走
  overlap-save，二者是**两个独立块**；全树无 `if(ntaps>阈值) 切 FFT` 的运行时分支。
  "切换"发生在 flowgraph 设计期选块，不是块内自适应。
- 证据：`gr-filter/lib/fir_filter.cc:91-156`；`fft_filter.cc:72-149`；
  `fft_filter_ccf_impl.h` 仅包一层 FFT 内核。
- **我方对照**：若将来加内核选择，做成配置项/设计期决策（`rational_resampler.h`
  tapsPerBranch 即设计期定值），不搞运行时猜阈值分支。

### 2.2 GCD 未约分告警陷阱
- **上游**：仅在"用户自带抽头却未约分"时打日志提醒 filterbank 复杂度上升；银行规模本应取
  GCD 最简后的 L。
- 证据：`gr-filter/lib/rational_resampler_impl.cc:127-137`。
- **我方对照**：每次都先 `gcd` 自动约分再建银行（`rational_resampler.h:47-49`），
  不存在该陷阱；不引入"等用户约分"的告警路径。

### 2.3 audio_resampler 高阶 MMSE 插值——有意不抄
- **上游**：`mmse_fir_interpolator` 用高阶拉格朗日做分数级插值。
- **我方对照**：`audio_resampler.cpp:83-104` 用线性插值做残差分数级（注释明言窄带近 1、
  WFM 后级 1~2，语音可闻性内无损）。这是**有意降级**而非缺陷；未来上数字语音模式
  （M17/POCSAG 已是整数重采样路径）再评估换 MMSE，本轮不抄。

---

## 3. AGC

### 3.1 agc2 负增益地板 hack
- **上游**：`if (gain<0) gain=10e-5`，上游自己注释 "Not sure about this; will blow up if
  gain<0… but is this the solution?"——这是速率过大导致环跑飞时的创可贴，不是特性。
- 证据：`gr-analog/include/gnuradio/analog/agc2.h:76-79`。
- **我方对照**：我方 `g=ref/max(env,1e-4)`（`agc.cpp:65`）结构上恒正，无负增益概念；
  不引入负增益，也不抄这块地板 hack。

### 3.2 agc2 切换判据 `tmp>gain`（含 fabsf 对称版）
- **上游**：用误差与当前增益比大小切快慢，不是"信号在上升/下降"；`agc2_ff` 的
  `fabsf(tmp)>gain` 更怪——把"信号低于参考很多"也切快 attack，安静时增益会乱跳。
- 证据：`agc2.h:70-73`；`agc2.h:141-145`。
- **我方对照**：我方 `mag>env`（信号是否超过包络）才切 attack（`agc.cpp:59`），
  是正确的包络跟随判据；不抄经验比大小。

### 3.3 纯前馈逐样本增益 = zipper noise
- **上游**：feedforward_agc 每个样本 i 用 `[i,i+nsamples)` 窗内峰值算独立增益，增益在窗内
  逐样本跳变，音频里产生 zipper/modulation noise，仅适合 RF 峰值检测（脉冲归一）。
- 证据：`gr-analog/lib/feedforward_agc_cc_impl.cc:63-71`；包络逼近 `:43-52`。
- **我方对照**：我方把前瞻当"防削波 pin"而非"增益源"——扫一次块峰值，仅在
  `blockPeak·g>ceiling` 时 pin env（`agc.cpp:51-55,75-79`），增益仍由反馈包络决定；
  这种"反馈+前馈防削波"混合拓扑既稳又防瞬态，不抄纯前馈逐样本增益。

### 3.4 单率 agc/agc_ff（无 attack/release 区分）
- **上游**：增益下降与上升同一速率（默认 1e-4 极慢），语音场景"大声压小声"和
  "小声拉大声"同速，听感闷——入门实现形态。
- 证据：`agc.h:62-63,121`。
- **我方对照**：我方非对称 attack/decay 包络跟随（`agc.cpp:59-60`），比单率更贴合语音；
  不以单率为目标形态。

### 3.5 agc3 volk 批量缩放首块
- **上游**：首块快捕后用 `volk_32f_s32f_multiply_32f` 给 N 样本一次乘。
- 证据：`gr-analog/lib/agc3_cc_impl.cc:150-153`。
- **我方对照**：音频块小（约千样本），逐样本 `out[i]=in[i]*g`（`agc.cpp:82`）即可，
  volk 调用开销不划算；不引入 VOLK 依赖（与零外部依赖原则一致）。

---

## 4. FFT 窗口

### 4.1 Flattop 系数两个流派，别随上游换表
- **上游**：GNU Radio 用 SRS SR785 仪表系数，头文件明确警告"与 SciPy/Matlab 不同"。
- 证据：`gr-fft/lib/window.cc:225-230`；警告 `window.h:251-255`。
- **我方对照**：我方用的是 SciPy/D'Antona 系系数（`power_spectrum.cpp:91-92`）。
  不要因为"GNU Radio 这么写"就换成 SRS 版——那会同时改变 Flattop 峰值形状与历史校准值；
  换系数前必须重测电平。

### 4.2 Kaiser 端点必须特判（防 NaN）
- **上游**：Kaiser 不能在循环里算首尾点（端点处 `sqrt(1-(1+eps)²)` 出 NaN），循环外单独
  写 `taps[0]=taps[n-1]=1/Izero(beta)`，注释直接引用 GitHub issue #1348。
- 证据：`gr-fft/lib/window.cc:243-255`；I₀ 级数 `:24-41`。
- **我方对照**：本轮不加 Kaiser（YAGNI）；若未来补，照抄"循环外写首尾点"防 NaN 模式，
  不自己踩 #1348。

### 4.3 GNU Radio 不是周期窗
- **上游**：`coswindow` 全部 M=ntaps-1（对称窗，首尾相等），不是 STFT 周期窗 M=n。
- 证据：`gr-fft/lib/window.cc:108`。
- **我方对照**：我方对称窗约定一致（`power_spectrum.cpp:83-85`）；
  不要误记为"GNU Radio STFT 用周期窗"。

### 4.4 `WIN_BLACKMAN_hARRIS` 小写 h 是历史遗留
- **上游**：枚举别名 `WIN_BLACKMAN_HARRIS=5` 只是给小写 h 拼写兜底，移植时别顺手
  "纠正"大小写，会断兼容。
- 证据：`gr-fft/include/gnuradio/fft/window.h:36-38`。
- **我方对照**：我方 Window 枚举自命名（`power_spectrum.h:22`），无此包袱；
  仅作"别为对齐上游拼写而改名"的警示。

### 4.5 FFTW_MEASURE 首调代价 + Wisdom 文件锁
- **上游**：MEASURE 首次构造要把几十种算法各跑一遍计时（构造期慢几百 ms），故必须配
  Wisdom 跨进程文件锁持久化经验。
- 证据：`gr-fft/lib/fft.cc:185`；Wisdom 文件锁 `:80-92`。
- **我方对照**：我方手写无 plan 的 FFT 恰好绕开此问题（`fft.cpp`）；
  反过来说，若将来为提速引入任何"构造时选最优算法"的机制，必须同步想清楚首调延迟与
  经验缓存，不能只抄"MEASURE"三个字。

### 4.6 build() 不处理 WIN_NONE（−1 抛异常）
- **上游**："无窗"由调用方自己跳过分乘，build 对 −1 走 default 抛异常，不返回全 1 窗。
- 证据：`gr-fft/lib/window.cc:427-428`。
- **我方对照**：我方 Window 枚举无"无窗"档（`power_spectrum.h:22`）；
  若日后加矩形窗选项，让调用方显式跳过分乘，而不是造一个全 1 窗再乘一遍（白多一次 N 点乘法）。

---

## 5. PMT / 消息传递

### 5.1 alist dict 是 O(n) 线性扫描
- **上游**：dict 不是哈希表，是 cons 链表 association list；`dict_ref` 走 `assv` 线性扫描，
  `dict_delete` 递归重建链表——只适合"小而少改"的元数据，是有意取舍。
- 证据：`gnuradio-runtime/lib/pmt.cc:697-704`（dict_ref）、`:686-695`（dict_delete）。
- **我方对照**：我方任何"字典"需求都用 QJsonObject（哈希 map，`llm_worker.h:69`），
  要改要查都更合适；切勿照搬链表 alist，O(n) 扫描对大字典是坑。

### 5.2 symbol 驻留表只增不删
- **上游**：string_to_symbol 进程生命周期常驻，只 new 头插不删（因符号少且稳定），
  同名恒指同一指针。
- 证据：`gnuradio-runtime/lib/pmt.cc:150-175`（只增不删 `:171-174`）。
- **我方对照**：我方绝不对用户输入/动态字符串做驻留，否则内存泄漏；
  仅对稳定端口名/命令名用查表（`control_hub.h:137` CommandRow 表），不做全局符号常驻。

### 5.3 `message_port_pub` 每条消息按名查全局注册表
- **上游**：出端口扇出时每条消息都 `block_lookup(block_id)` 查全局注册表再 post。
- 证据：`gnuradio-runtime/lib/basic_block.cc:127-137`（按名查 `:134`）。
- **我方对照**：订阅者个位数，Qt signal 直连做多播已够；不引入"按名查全局对象"的间接层。

### 5.4 `msg_accepter::post` 用 dynamic_cast 向下转型
- **上游**：基类 post 内部 `dynamic_cast<block*>` 才转发到 `_post`——在基类里猜派生类型，
  脆弱设计。
- 证据：`gnuradio-runtime/lib/messages/msg_accepter.cc:30-46`（向下转型 `:33`）。
- **我方对照**：我方用明确接口/命令表分发（ControlHub CommandRow{name,write,Handler}，
  `control_hub.h:137`；executeTool 字符串分发 `agent_tools.h:13`），不在基类里猜派生类型。

### 5.5 不可变 dict 每次 add 都重建链表
- **上游**：dict_add 函数式不可变，key 已存在先删旧再头插新，返回新 dict、旧 dict 原样保留
  （共享结构）；写多读多场景制造大量垃圾。
- 证据：`gnuradio-runtime/lib/pmt.cc:663-672`。
- **我方对照**：我方配置/状态是原地改（QJsonObject 可变），不采用函数式不可变重建；
  元数据信封（PDU 头/体分离）仅在未来 IQ 帧/录制回放时再考虑。

---

## 收口

- 反例集中在三类：**(a) 调度/缓冲的复杂度税**（goto 状态机、线程爆炸、−1 预留槽、mmap 平台
  脆弱性、双记账）——我方单线程 pull 架构天然免疫；**(b) AGC/FFT 的上游 hack 与误读**
  （负增益地板、`tmp>gain`、纯前馈 zipper noise、FFT 卷积"自动切换"证伪、Flattop 系数流派）；
  **(c) PMT 动态类型系统的代价**（alist O(n)、symbol 只增不删、dynamic_cast 向下转型、
  不可变 dict 重建）——我方 QJsonObject + Qt signal + 命令表已更直接。
- 所有"不抄"均为机制层取舍，无 GPL 代码复制；证据均可回查上游 file:line 与我方对应文件。
