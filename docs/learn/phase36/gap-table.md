<!-- SPDX-License-Identifier: MIT | Phase36 块3：差距文档更新（docs 域）。
来源：本目录 block-stream / resample-chain / agc-gain / fft-window / pmt-messaging 五篇笔记。
只做汇总裁决，不新增代码结论；证据 file:line 均可回查原笔记与上游源码。 -->

# Phase36 差距裁决总表（GNU Radio 对照 cpp/src/dsp 流链）

> 判定口径三档：
> - **补齐**：真差距，本轮/Wave2 落地，标注状态。
> - **YAGNI**：当前规模/约束下不补，附理由；列触发条件，待需求出现再回看。
> - **反超 / 已对齐**：我方机制已等价或更优，不补。
>
> W2 = Wave2。本轮（块3）只更新 docs，不改 cpp/。

---

## A. 块式流处理（来源：block-stream.md）

| 机制 | GNU Radio 实现 | 我方现状 | 差距判定 | 证据 file:line | 本轮动作 |
|---|---|---|---|---|---|
| 一块一线程 + condvar 唤醒（TPB） | used blocks 逐个 create_thread，barrier 同步开跑，主循环在 input_cond/output_cond 上 wait | 单线程 pull 泵：一源读一帧→就地前端→FFT→同步扇出各 VFO，无跨块并发 | **反超（YAGNI 不补）**：单源、拓扑启动即定型、桌面端 cgroup OOM 约束下，TPB = 每块一线程 + mutex/condvar + 跨线程环，纯把一次函数调用拆成 N 次上下文切换，是负优化；GR 要它为任意动态拓扑/多速率/设备直通 | `gnuradio-runtime/lib/scheduler_tpb.cc:60-90`；`tpb_thread_body.cc:68-152`；我方 `spectrum_engine.cpp:1055,1087-1150` | 不补；坚持单 pump |
| 块间零拷贝双映射环（vmcircbuf） | 同一份物理页 mmap 两次首尾相接，热路径无取模；失败时 4 种 factory 兜底 + 单映射 memmove 退路 | 块间传 `std::vector<complex<float>>` 整块；`IQBuffer` 仅"最近 n 点"覆盖快照环，非块间零拷贝环 | **YAGNI**：帧仅 ~25ms、单 VFO 链同线程，拷贝在 L2 内，远小于"跨线程共享环 + 预留槽 + 双映射 mmap"的平台脆弱性代价 | `gnuradio-runtime/lib/vmcircbuf.h:46-47`；`buffer_double_mapped.cc:137-169`；`vmcircbuf.cc:86-90`；我方 `iq_buffer.h` | 不补；不碰 mmap shm/tmpfile 适配面 |
| forecast 反推 + noutput_items 二分回退 | 先估产 N 输出需多少输入，required>available 则 noutput_items/=2 重试（goto try_again） | readIQ 帧大小由源决定，下游整帧吃掉，本就不缺数据 | **YAGNI（观察项）**：整帧同步 pull，上游给多少下游算多少；仅当引入异步/变长源（网络偶发短帧）时再回看 | `gnuradio-runtime/lib/block_executor.cc:523,533,548-554`；我方 `spectrum_engine.cpp:1050-1053,1077` | 不立项；记为异步源触发条件 |
| 环缓冲 sizing（2×消费量子 + LCM 对齐） | 容量下限 2·(decim·mult+history)，尺寸取 relative_rate 与定比步进 LCM | Channelizer/decimator 块大小由 readIQ 帧与抽取比自然导出，无显式下限 | **YAGNI（观察项）**：当前帧远大于抽取量子，不暴露问题；仅当"块大小≈抽取量子"的极端低延迟配置导致滤波器历史截断/饿死时才加下限 | `gnuradio-runtime/lib/flat_flowgraph.cc:105-129` | 不补；加 = 过度设计 |
| history/尾状态 + 启动预填零 | 滤波器历史抽头由调度器 reader 预填 0，块内留 T-1 | `FirLowpass::delayLine_`、`Channelizer::tail_`、demod 各级 delayLine_ 均块内留状态、reset() 清零 | **已对齐**：与 GR history 同构 | `gnuradio-runtime/include/gnuradio/block.h:90-99`；我方 `channelizer.h:126`、`demod.h` | 不补 |
| 扇出 = 一 buffer 多 reader + 最慢读者节流 | 下游 buffer_add_reader 复用上游写 buffer，space_available 按最慢消费者定 | vfoManager 同步扇出：宽带 IQ 只存一份，各 VFO 独立 Channelizer+Demod+Resampler 链 | **已对齐**：VFO 扇出模型同构 | `gnuradio-runtime/lib/flat_flowgraph.cc:203-274`；我方 `vfo_manager.h:77-79`、`spectrum_engine.cpp:1150` | 不补 |

---

## B. 采样率转换链（来源：resample-chain.md）

| 机制 | GNU Radio 实现 | 我方现状 | 差距判定 | 证据 file:line | 本轮动作 |
|---|---|---|---|---|---|
| GCD 约分多相银行 | d=gcd(interp,decim) 后 interp/=d、decim/=d，银行规模取最简 L | 每次 gcd 自动约分后再建银行 | **已对齐** | `gr-filter/lib/rational_resampler_impl.cc:127,147-148`；我方 `rational_resampler.h:47-49` | 不补 |
| 换向器游标跨块持久 | d_ctr 跨块持久，出一样本选相位分支并按 M 推进游标 | offset 式跨块持久游标 | **已对齐**（offset 式等价 ctr 式） | `rational_resampler_impl.cc:229-261`（:258）；我方 `rational_resampler.h:99-104`、`channelizer.cpp:169` | 不补 |
| 幂次预抽取 + 残差有理多级 | 多级大 M 拆分是集成方策略，rational_resampler 本身单级 GCD 最简 | 取最大 2^k≤floor(ratio) 预抽取，残差走有理 | **反超（更清晰）** | 我方 `channelizer.cpp:84-99` | 不补 |
| 指标驱动抽头数（Harris 公式） | ntaps=衰减dB·Fs/(22·过渡带) 向上取奇 | 固定 tapsPerBranch=31/32 | **补齐候选（未立项）**：WFM 后级/宽信道下固定抽头可能在过渡带变窄时欠设计；Harris 零依赖一行可算，把魔数变可解释指标 | `gr-filter/lib/firdes.cc:697-699,709-711`；我方 `channelizer.cpp:26`、`rational_resampler.h` tapsPerBranch | 本轮不落地；留作后续小补 |
| Kaiser 窗（β 连续可调） | 默认 β=7.0，修正贝塞尔 I₀ 级数 | 全程 Hann | **YAGNI**：语音窄带 Hann 足够；加 Kaiser 徒增 β 调参面与特殊函数实现，听感无差异 | `rational_resampler_impl.cc:55-56,72`；我方 `channelizer.cpp:36`、`rational_resampler.h:133` | 不补 |
| FFT 卷积 / overlap-save（长抽头频域卷积） | 独立 fft_filter 块：抽头一次前向 FFT，运行期 FFT→逐点乘→IFFT→加尾存尾 | 无（短 FIR 直接循环点积） | **YAGNI**：我方抽头短（~31），直接点积开销远小于两次 FFT；且无"块内自动切换"需求（见反例） | `gr-filter/lib/fft_filter.cc:72-149` | 不补 |
| forecast 输入预算（重采样反推） | (nout+1)·decim/interp+history-1 | 我方 owns 块两端，变长 process 自管输入 | **YAGNI**：块两端都在我方手中，无需对外协商输入量 | `rational_resampler_impl.cc:220-223` | 不补 |
| 残差分数重采样内核 | mmse_fir_interpolator（高阶拉格朗日） | 线性插值做残差分数级 | **已对齐（有意降级）**：窄带近 1、WFM 后级 1~2，语音可闻性内无损；数字语音模式已是整数重采样路径 | 我方 `audio_resampler.cpp:83-104` | 不补；上 M17/POCSAG 后再评估 MMSE |

---

## C. AGC 与增益控制（来源：agc-gain.md）

| 机制 | GNU Radio 实现 | 我方现状 | 差距判定 | 证据 file:line | 本轮动作 |
|---|---|---|---|---|---|
| 单率反馈积分（agc/agc_ff） | gain += rate·(ref-\|out\|)，attack=release 同速 | 非对称 attack/decay 包络跟随 | **反超**：单率是退化形态，我方非对称更贴合语音听感 | `gr-analog/include/gnuradio/analog/agc.h:62-63,121`；我方 `agc.cpp:59-60` | 不补 |
| 双率切换（agc2） | rate = (tmp>gain) ? attack : decay，判据是误差比增益 | mag>env ? attackα : decayα，按"信号是否上升"切 | **反超**：agc2 的 tmp>gain 是经验 hack，我方是教科书包络跟随判据 | `agc2.h:70-73,141-145`；我方 `agc.cpp:59` | 不补 |
| 负增益地板 hack | gain<0 时 gain=10e-5（上游自注 Not sure） | g=ref/max(env,1e-4)，结构恒正，无负增益概念 | **反超**：不创可贴 | `agc2.h:76-79`；我方 `agc.cpp:65` | 不补 |
| IIR on 目标增益（agc3） | gain=gain·(1-r)+ref·r/mag，平滑 inverse magnitude | 包络跟随后除法 env→g=ref/env | **已对齐**：拓扑不同数学等价，不换 | `gr-analog/lib/agc3_cc_impl.cc:167`；我方 `agc.cpp:60,65` | 不补 |
| reset 首块均值快捕（agc3） | reset 后扫 iir_update_decim·4 样本求平均幅度，一步定 gain=ref·N/sum(\|x\|) | reset 仅 env_=0；首样本 gRaw 冲到上限 cap→前瞻 pin 兜底，经历一次增益跳变 | **补齐（W2 落地中）**：reset 处用块首 N 样本平均幅度直接初始化 env_，省掉首样本 cap→pin 跳变，约 5 行带 ctest | `agc3_cc_impl.cc:138-156`；我方 `agc.cpp:36`（reset）、`agc.cpp:75-79`（前瞻 pin） | W2 落地中：agc.cpp reset 处加首块均值快捕 |
| IIR 抽取更新（每 N 样本） | 每 iir_update_decim 样本采一个 mag | 每样本更新 | **YAGNI**：抽取是给高采样率 IQ 省算力；音频 48kHz 下 abs+乘加可忽略 | `agc3_cc_impl.cc:159-180`；我方 `agc.cpp:109-116` | 不补 |
| isnormal 守卫（近零 mag） | mag 非正规数时 gain*=(1-decay) 防除零爆增益 | max(env,1e-4) 地板除法 | **已对齐**：语义等价 | `agc3_cc_impl.cc:163,168-169`；我方 `agc.cpp:65` | 不补 |
| 互斥 setter（UI 热改参数） | 每个 set_* 拿 d_setter_mutex | 无锁 inline setter | **YAGNI**：当前 DSP 单线程独占 AGC 状态，热改参数路径在配置层 | `agc3_cc_impl.cc:97-128`；我方 `agc.h:42-43` | 不补；未来从 UI 线程热改音频 AGC 再加 |
| 前馈逐样本块峰值取反（feedforward） | 每样本 i 用 [i,i+nsamples) 峰值算独立增益，增益逐样本跳变 | 块级前瞻：扫一次块峰值 O(n)，仅在 blockPeak·g>ceiling 时 pin env 防削波，增益仍由反馈包络决定 | **反超（混合拓扑更优）**：纯前馈逐样本增益跳变会 zipper noise；我方"反馈包络+前馈峰值防削波"既稳又防瞬态削波 | `gr-analog/lib/feedforward_agc_cc_impl.cc:63-71`；我方 `agc.cpp:51-55,75-79` | 不补 |
| 包络 sqrt 逼近（max+0.4·min） | 省一次 sqrt，给高采样率向量化 | 音频 std::abs(float)、IQ std::abs(complex) | **YAGNI**：逼近是高采样率热点优化，音频域非热点 | `feedforward_agc_cc_impl.cc:43-52`；我方 `agc.cpp:58,111` | 不补 |
| max_gain 上限 / 输出限幅 / lookahead | max_gain；set_history 让调度器给 lookahead | maxGain_ + OutputCeiling clamp(-1,1)；整块 vector 在手天然 lookahead | **已对齐**：我方还多输出钳位 | 我方 `agc.h:24,30`、`agc.cpp:82` | 不补 |

---

## D. FFT 窗口与频谱（来源：fft-window.md）

| 机制 | GNU Radio 实现 | 我方现状 | 差距判定 | 证据 file:line | 本轮动作 |
|---|---|---|---|---|---|
| 窗归一化（单位 RMS） | build(normalize=true) 先造未归一窗再除 1/sqrt(mean(w²))，scale 与窗无关 | 硬编码 scale=4/(n·n)，按矩形窗标定，对窗无补偿 | **补齐（W2 落地中）**：Hann/Blackman/Flattop 间切换时满幅单音峰值漂移约 -6 / -7.5 / -13 dB，用户误以为信号变了；修法 rebuildWindow 末尾除 sqrt(mean(w²)) 并把归一化因子乘进 scale，三窗电平对齐 | `gr-fft/lib/window.cc:378-391`；我方 `power_spectrum.cpp:142`（scale 硬编码）、`:87-100`（rebuildWindow） | W2 落地中：power_spectrum.cpp:142 硬编码 scale→RMS normalize，带 ctest 守三窗峰值差 <0.5 dB |
| 窗种类（16 种系数表驱动） | 余弦和模板 + 系数表，加窗=加一行系数 | Hann/Flattop/Blackman 三种 if/else 硬编码 | **YAGNI**：观察屏三种够用；建议（非补齐）重构成表驱动，加窗零成本、低风险 | `gr-fft/lib/window.cc:105-140`；我方 `power_spectrum.cpp:87-100` | 不补；表驱动列为后续重构候选 |
| 窗对称性（M=n-1 非周期窗） | coswindow 全部 M=ntaps-1 | 同（对称窗首尾相等） | **已对齐**；STFT 50% 重叠用周期窗 M=n 理论更优但观察屏差别 <0.1 dB，**YAGNI** | `window.cc:108`；我方 `power_spectrum.cpp:83-85` | 不补 |
| FFT 引擎（FFTW MEASURE+Wisdom+多线程+many-plan） | fftwf_plan_many_dft，Wisdom 跨进程文件锁持久化，nffts 批次 | 手写 radix-2 in-place，幂二校验，每次重算 twiddle | **YAGNI（明确）**：FFT 尺寸 2048–8192、~30 fps，手写 radix-2 约 0.1–0.5 ms 不构成热点；引入 FFTW 违反零外部依赖原则 | `gr-fft/lib/fft.cc:138-168,173-227`；我方 `fft.cpp:42-46`、`fft.h:2` 零依赖自注 | 不补；唯一可做小优化=twiddle 按 n 缓存（thread_local），非本轮 |
| r2c 实数 FFT 模板特化 | c2r/r2c 折进同一模板 | 无（频谱全是复输入） | **YAGNI**：IQ 源本就是复数，实数路径无调用方 | `fft.cc:208-239` | 不补 |
| 旁瓣衰减文档（枚举自注释） | win_type 每枚举值行尾写旁瓣 dB | Window 三档无注释 | **顺手补（零代码）**：枚举注释补 44/74/93 dB | `gr-fft/include/gnuradio/fft/window.h:30-52`；我方 `power_spectrum.h:22` | 留作顺手项，随 W2 一并 |
| 渲染层（block-max 抽稀/LUT/tick） | GR 不做渲染层 | spectrum_render 独立 clean-room 完成 | **无对照项**：不涉及差距 | 我方 `spectrum_render.h:82-107,121-140,261-279` | 不补 |

---

## E. PMT / 消息传递（来源：pmt-messaging.md）

| 机制 | GNU Radio 实现 | 我方现状 | 差距判定 | 证据 file:line | 本轮动作 |
|---|---|---|---|---|---|
| 消息在 worker 安全点串行抽干 | TPB 主循环先非阻塞抽干入端口队列→dispatch handler，再跑同步流；外部 post 仅无锁入队+唤醒 | ControlHub dispatch 强制 engine 主线程跑，跨线程走 blocking queued call；QSignals 跨线程走 Qt queued event | **已等价**：发送方只入队不碰接收方状态，消费方固定循环点串行，无数据竞争 | `gnuradio-runtime/lib/tpb_thread_body.cc:73-97`；我方 `control_hub.h:35-38` | 不补 |
| 有界槽 + 无消费者丢最老告警 | block 端口队列超 max_messages(默认100) 从队头丢并告警；独立 msg_queue 有界满则反压 | QSignal 跨线程 queued connection 无界，高频 statusChanged/partialReady 理论可堆积 | **YAGNI（本轮不做）**：当前订阅者个位数、高频 UI 事件量级未爆；背压改造（满则丢中间态保最新）留待后续触发，本轮仅 docs 记录 | `tpb_thread_body.cc:82-91`；`messages/msg_queue.h:30-78`；我方 QSignal queued connection | 本轮不补；记为触发项（高频事件堆积时落地） |
| 端口名 = interned 驻留符号 | string_to_symbol 8192 桶哈希，同名恒指同一指针，相等=指针比较 | ControlHub 已是 CommandRow 名字表；工具名 executeTool 字符串分发 | **已等价**：名字即 key，无需再做符号表 | `gnuradio-runtime/lib/pmt.cc:137,150-175`；我方 `control_hub.h:137`、`agent_tools.h:13` | 不补 |
| 单一 post(port,msg) sink + 扇出 pub/sub | message_port_pub 遍历订阅者列表逐个 post | 1 生产者→少量 UI 直连 signal，Qt 已做多播 | **YAGNI**：订阅者个位数，自造发布订阅层是过度设计 | `basic_block.cc:125-137` | 不补 |
| 全套 PMT 动态类型 / cons/dict/lisp | pmt_t=shared_ptr<pmt_base>，运行时鸭子类型+异常，不可变 alist dict | 工具参数已是 QJsonObject，天然 JSON 可序列化 | **反超**：LLM 工具参数本就是 JSON，再套一层 s-exp 装箱纯属冗余；QJsonObject 可变哈希 map 比 alist O(n) 扫描更适合要改要查的字典 | `include/pmt/pmt.h:45-85`；`pmt.cc:663-704`；我方 `llm_worker.h:69` | 不补；QJsonObject 已等价 PMT dict |
| PDU = cons(meta dict, 类型化载荷向量) | is_pdu = pair && dict(car) && uniform_vector(cdr)，头/体分离 | 工具结果是 QString/JSON，暂无大块二进制流 | **保留为未来模式**：将来做 IQ 帧/录制回放时用 (QVariantMap 头 + QByteArray 体) 信封，现在不建 | `pmt.cc:815-818`；`gr-pdu/lib/pdu_set_impl.cc:41-48` | 不建；留模式备查 |

---

## 汇总

- **补齐（W2 落地中，2 项）**：
  1. 窗 RMS 归一化——`power_spectrum.cpp:142` 硬编码 scale → 按 sqrt(mean(w²)) 归一，三窗切换电平对齐。
  2. AGC reset 首块均值快捕——`agc.cpp` reset 处用块首 N 样本平均幅度初始化 env_，消除首样本 cap→pin 跳变。
- **补齐候选（未立项，2 项）**：Harris 指标驱动抽头数（B 表）；有界槽背压（E 表，本轮 YAGNI，触发后落地）。
- **YAGNI 不补（带理由）**：TPB 一块一线程、双映射零拷贝环、forecast 二分回退、环缓冲 sizing 公式、Kaiser 窗、FFT/overlap-save 卷积、重采样 forecast 预算、agc3 抽取更新、互斥 setter、sqrt 包络逼近、FFTW 引擎、r2c 实数 FFT、16 种窗全量、自造 pub/sub、全套 PMT 类型系统、alist dict。
- **反超 / 已对齐**：单线程 pull 泵（OOM 约束下更合适）、幂次预抽取多级、AGC 包络跟随+块级前瞻混合拓扑（超 GR 四条路线）、QJsonObject 等价 PMT dict；history/尾状态、GCD 多相、换向器游标、isnormal 守卫、max_gain/限幅/lookahead、消息安全点串行抽干、端口名表均已对齐。
