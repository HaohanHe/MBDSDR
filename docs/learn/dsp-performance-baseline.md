# DSP 性能基线（P6）

本文件只记录**可复跑、可核验**的接收链路性能基线：吞吐、延迟/抖动、长跑
资源水位、热点归因与不可优化边界。所有数字都是合成信号 + 无硬件 + offscreen
环境下的实测值；换机器、换采样率、换模式都会变，请按文末命令重测，不要
把这里的数值当承诺。

## 1. 结论

1. **峰值负载不掉样本**：0.96 / 1.2 / 2.4 MS/s 三档下，端到端可持续速率
   **低负载**实测 **9.37–9.57 / 9.65–9.82 / 9.27–9.68 MS/s**（12 逻辑核、
   系统 CPU 19–33%、bench 1.43–1.60 核；每档 3 轮）。相对标称的倍数同样给
   区间：**9.76–9.97x / 8.04–8.18x / 3.86–4.03x**——**倍数只在同条件内成立，
   不能跨负载比**；同一份代码在高负载下三档只有 4.0–4.3 MS/s（旧记的
   4.25 / 4.02 / 4.28 就是那一档，见 §5）。最慢环节 WFMReceiver.process
   余量 **6.74–7.27x**（2.4 MS/s 档，本轮三轮）。落盘字节误差 **≤0.07%**
   （口径见下），音频与落盘最大写出空档 ≤ 11 ms，无残留线程。
2. **延迟在个位数毫秒**：音频通路 P50 4.2 / 4.8 / 4.15 ms、P99 7.5 / 8.1 /
   7.75 ms；瀑布（含 Qt queued 排队）P99 7.9 / 8.4 / 8.05 ms；抖动
   （P99−P50）3.3–3.6 ms。全部远低于阈值（音频 P99 50 ms、瀑布 80 ms、
   抖动 30 ms、写出空档 0.15 s）。
3. **30 分钟等价长跑无增长**：`--minutes 3 --speed 10`（墙钟 3 分钟即
   30 分钟音频量）判据全过，RSS 增长 0.25 MB（峰值/p95 均 197.44 MB），
   线程趋势 −1.17、跨度 [30, 33]，句柄 406→398，GC 跟踪对象增长 14.8、
   峰值 110,517，落盘 2,764,963,840 B（期望 2,764,800,000 B），最大数据洞
   0.05 s。
4. **热点**：优化掉两处纯 Python/纯 numpy 热点（FFT 后 512 bin 最大值抽取、
   VFO 每块重算旋转子）；剩余前三热点全部落在外部 C 扩展（scipy
   `upfirdn` / numpy `correlate` / `angle`）与其包装开销内，纯 Python 侧
   已无可摘的低垂果实。

**落盘字节误差的口径（0.08% 与 0.03% 不是回归）**：`recorded_bytes` 只写满块
（16384 样本 / 131,072 B），末尾不满一块的在 `close()` 时丢弃，所以误差恒为
**未落盘块数 × 16384 / 总产出样本**，是块粒度量化。本轮 3 轮 × 3 档共 9 个读数
为 0.03–0.07%（1–2 块，总产出 4,600–4,900 万样本）；旧码 `06e42a69` / `2ff06b3a`
同条件也是 0.03–0.04%（同样 1–2 块）。旧记的 0.08% 对应 2 块 / 约 4,100 万样本，
**同一机制、不同轮次**，不是口径变了、也不是实现变差。逐次块数与产出量见
`docs/perf-evidence/2026-11-12/dsp-throughput-2p4ms-head-run{1,2,3}.json` 的
`e2e.recorded_blocks` / `e2e.produced_samples`。

## 2. 复跑

```powershell
$env:QT_QPA_PLATFORM="offscreen"; $env:MBDSDR_NULL_AUDIO="1"; $env:MBDSDR_NO_HW="1"

# 吞吐 + 热点 + 信号分发（三档速率，约 1 分钟）
python scripts/bench_dsp_throughput.py --profile
# CI 快速档（单速率 1 s，无硬件可跑）
python scripts/bench_dsp_throughput.py --quick

# 延迟与抖动（三档速率，每档 10 s）
python scripts/bench_latency_jitter.py
python scripts/bench_latency_jitter.py --quick

# 长跑资源（墙钟 3 分钟 = 30 分钟音频量）
python scripts/soak_receive_chain.py --minutes 3 --speed 10 --sr 192000
```

机器可读结果：`output/bench/dsp_throughput_<ts>.json`、
`output/bench/latency_jitter_<ts>.json`、`output/soak/soak_rx_<ts>.jsonl`
（后者逐 10 s 一行样本，末行是 verdict）。`output/` 不入库，证据需现场
重跑生成；落盘产物（`.cf32`）只用于验证字节数，验证后可删。

## 3. 测量方法

- **吞吐微基准**：单线程逐环节计时，block = 16384 样本，每环节预热后取
  中位数；`RTF = (block/sr) / 单块耗时`，RTF > 1 即该环节快于实时。计时
  对象全是生产代码（`SpectrumDataGenerator.push_iq`、`VFO.process`、
  `WFMReceiver.process`、cf32 交织写盘），不是替身。
- **端到端可持续速率**：真 `ReceivePipeline`（reader → splitter →
  FFT / WFM 解调 / 落盘 三线程），喂**不定速**合成 IQ，让消费者自然反压，
  统计「实际被消费的样本数 / 墙钟」。低于标称采样率即意味着峰值负载下
  掉样本。
- **延迟**：合成源按样本时钟限速，首个 `read` 的墙钟记为 `t0`，流内第 i
  个样本时间戳 `t0 + i/sr`。音频通路按「第 k 次 write 对应输入块 k」换算
  输入下标，避免累计音频样本漂移；瀑布通路经 `frame_ready` queued 信号
  投到主线程记录到达时刻，含 Qt 排队但不含 QPainter 像素绘制。
- **长跑**：每 10 s 采进程 RSS / 线程数 / Win32 句柄 / 落盘字节 / GC 跟踪
  对象数与回收计数。趋势哨戒口径与 P4 长跑一致（末 6 样本均值 − 首 6 样本
  均值），只追加「对象数趋势 ≤ 5000」，不另立第二套标准。

## 4. 阈值与钉扎

| 判据 | 值 | 位置 |
| --- | --- | --- |
| 每环节最小 RTF | 1.15 | `scripts/bench_dsp_throughput.py` |
| 端到端最小 RTF | 1.00 | 同上 |
| 落盘相邻块最大空档 | 0.25 s | 同上 |
| 音频 P99 | 50 ms | `scripts/bench_latency_jitter.py` |
| 瀑布 P99 | 80 ms | 同上 |
| 抖动 P99−P50 | 30 ms | 同上 |
| 音频写出最大空档 | 0.15 s | 同上 |
| 统计样本下限 | 200 | 同上 |
| RSS 增长 / 线程趋势 / 跨度 / 句柄 / 落盘带 / 数据洞 | 32 MB / +2 / 4 / 64 / ±25% / 5 s | `scripts/soak_receive_chain.py` |
| GC 跟踪对象增长 | 5000 | 同上 |

钉扎测试（阈值只能收紧，测试即门禁）：

- `tests/test_bench_dsp_throughput.py`：阈值、环节集合、quick 报告结构、
  信号分发开销、热点条目形态、端到端判定边界；
- `tests/test_bench_latency_jitter.py`：阈值、判定边界与成因提示、真流水线
  4 s 报告结构；
- `tests/test_soak_receive_chain.py`：P4 阈值不被放宽、全好样本通过、
  各单项违规各自判 False、`--speed` 语义与 GC 采样结构；
- `tests/test_dsp_perf_optimizations.py`：下面两处优化的**逐元素等价**守卫。

## 5. 热点归因与优化前后

`--profile` 对一次三线程端到端跑做 `cProfile`（按 tottime 排序，模块 +
函数级）：

| # | 模块 / 函数 | tottime 占比 | 归属 |
| --- | --- | --- | --- |
| 1 | `_upfirdn.py` / `apply_filter` | 29.2% | scipy C 扩展 |
| 2 | numpy `correlate` | 12.7% | numpy C 扩展（FIR 相关） |
| 3 | `dsp.py` / `VFO.process` | 6.7% | 本项目 Python（已优化旋转子缓存） |
| 4 | `BufferedWriter.write` | 3.4% | I/O |
| 5 | numpy `angle` | 3.0% | numpy C（正交鉴频） |

已做掉的两处（同机、同方法对比）：

| 优化 | 位置 | 前 | 后 | 备注 |
| --- | --- | --- | --- | --- |
| FFT 后 512 bin 抽取改为 reshape+max | `desktop/spectrum_widget.py` `_resample_max` | 单块 0.747 ms | 0.065 ms | 0.96 MS/s 档，约 −91%；不可整除时保留原循环。**前后均为旧时点，本轮未复跑** |
| 窗函数按 (名称, 长度) 缓存 | 同上 `_window` | 每块重算 | 命中即复用 | 换窗/换长度自动失效 |
| VFO 旋转子按 (块长, 频偏) 缓存 | `mbdsdr_ai/dsp.py` `VFO.process` | 0.751 ms | 0.467 ms | 约 −38%。**前后均为旧时点，本轮未复跑** |

> 上表两处微基准的「前 / 后」都是旧时点读数，本轮没有复跑，**不构成「本轮核过」**。
> 本轮同条件可复跑的当前值在 `rates[].stages.*.median_ms`（例：0.96 MS/s 档
> `fft_2048` 0.196 ms、`vfo_nbfm` 0.494 ms），指标定义与表中各行不完全相同，
> 不要直接相减。

**端到端可持续速率为啥不在这张表里**：文档旧记「3.75 → 6.83 MS/s（1.8x 提速）」，
本轮在独立 worktree 里把旧码 `06e42a69`（2026-11-06 01:41，正是那批数字被测出来时的
代码）用**同一条命令、同一台机器**复跑，低负载下得到 **9.54 MS/s**；HEAD 三轮
9.27 / 9.61 / 9.68，11-11 perf 批的父提交 `2ff06b3a` 为 8.98。两边的
`scripts/bench_dsp_throughput.py` 字节一致（`git diff 06e42a69 HEAD -- scripts/bench_dsp_throughput.py`
为空，20,602 B），口径没有漂移。**所以 3.75 / 4.28 / 6.83 的差别来自机器上并行跑着
别的活，不是代码变快**——「优化前后」这个因果关系不成立，故从表中撤下，改按条件记区间：
低负载 **9.0–9.9 MS/s**（3.74–4.03x），高负载可低至 3.9。

本轮条件：12 逻辑核，系统 CPU 19–33%（测量前后各采一次），bench 进程 1.43–1.60 核。
证据（已入库）：`docs/perf-evidence/2026-11-12/dsp-throughput-2p4ms-head-run1.json`（同 `-run2`、`-run3`）、
`docs/perf-evidence/2026-11-12/dsp-throughput-2p4ms-oldcode-06e42a69.json`、
`docs/perf-evidence/2026-11-12/dsp-throughput-2p4ms-oldcode-2ff06b3a.json`。
负载口径见 `docs/evidence-standard.md` §2 第 10 条。

**边界（不能优化，如实标注）**：`upfirdn.apply_filter`、`correlate`、
`angle`、`lfilter`、`resample_poly` 都是外部 C 实现，Python 侧只能减少
调用次数或传更短数组，改不动内核；把它们换成自写 FIR 只会更慢。VFO 的
剩余耗时主要是 scipy 重采样与相位乘加，已被 numpy 向量化到实际上限。

## 6. 线程切换与 UI 刷新开销

- **跨线程信号**：`bench_signal_overhead` 用 QObject Emitter 从工作线程发
  queued `Signal` 到主线程槽，2000 次实测 **2.76 µs/信号**（旧文写的 2.7 是四舍五入；
  字段 `signal_dispatch.per_signal_us` 见
  `docs/perf-evidence/2026-11-12/dsp-throughput-2p4ms-head-run3.json`，三轮 2.76–3.14 µs，
  上限约 36 万信号/秒）；
  按生产最坏帧率（2.4 MS/s × 16384 样本块 ≈ 146.5 帧/秒）折算
  事件循环占用 **0.04%**，不构成瓶颈。
- **UI 重绘节流**：瀑布/频谱不由每帧信号直接触发绘制，而由主窗口统一的
  33 ms（约 30 fps）重绘定时器合并（`desktop/main_window.py`
  `_ensure_repaint_timer`，属另一条线，本段未改动）。因此帧率升高只增加
  轻量状态更新，不线性增加绘制成本。
- **GIL**：端到端 CPU 占用 **166–190%**（相对单核；JSON `rates[].e2e.cpu_utilization_pct`，
  HEAD 三轮 × 三档 165.6–190.2%，旧码 `06e42a69` 同条件 170.8–186.6%），说明
  FFT/解调/I/O 三线程确有并行，未退化成单线程串行。另有一套全程进程采样口径
  （1.52 核 ≈ 152%，含微基准的单线程段），窗口不同，别与本行混用。旧记的
  130–145% 没有同时段负载记录，按 `docs/evidence-standard.md` §2 第 10 条不作基线。

## 7. 未验证 / 已知边界

- 未接真声卡：音频通路测的是写出调用侧延迟，**设备缓冲与驱动调度未计**
  （真机需按声卡周期另加数十毫秒，属设备参数而非链路缺陷）。
- 未测真实 QPainter 绘制成本：瀑布/频谱的像素级绘制在长跑与延迟测量中
  都只做了「信号到达 + 离屏 grab 冒烟」，真机高 DPI 下的绘制开销另计。
- 未接真实 SDR/天线：全部输入为合成 IQ；真实设备的 DMA 抖动、USB 丢包、
  驱动重连瞬态不在本基线内。
- `cProfile` 会给被测进程加约 2–3 倍开销，只用于**相对排序**，报告里的
  绝对耗时取自无 profile 的微基准。
- 本机实测值随后台负载波动：**低负载**（12 逻辑核、系统 CPU 19–33%、bench 1.43–1.60 核）
  端到端可持续速率 **9.0–9.9 MS/s**；**高负载**下同一代码同一命令可低到 3.9 MS/s
  （区间与条件见 §5，原始件 `docs/perf-evidence/2026-11-12/dsp-throughput-2p4ms-head-run3.json`；
  负载口径见 `docs/evidence-standard.md` §2 第 10 条）。阈值
  与结论都按「余量 ≥ 1.15x」判断，不以单次绝对值为准。
