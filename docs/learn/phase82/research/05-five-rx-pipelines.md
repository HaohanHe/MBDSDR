# 参考对标研究 05 — 五条接收流水线横向对比（goestools / noaa-apt / xrit-rx / meteor_demod / SigMF）

> 研究报告（只读，未修改 `/workspace/MBDSDR` 任何代码）
> 研究对象：
> - `/workspace/reference/goestools`（goestools，C++17，MIT）— GOES LRIT/HRIT 接收与解码流水线
> - `/workspace/reference/noaa-apt`（noaa-apt，Rust，GPL-3.0）— NOAA APT 模拟气象图解码
> - `/workspace/reference/xrit-rx`（xrit-rx，Python，MIT）— GK-2A xRIT 数字气象图接收
> - `/workspace/reference/meteor_demod/meteor_demod-master`（meteor_demod，C，MIT）— Meteor-M2 LRPT OQPSK/QPSK 解调
> - `/workspace/reference/sigmf`（SigMF 规范，CC-BY-4.0）— 射频录制元数据 Schema
>
> 参照物：`/workspace/MBDSDR`（C++17/Qt6 无线电接收机）
> 生成日期：2026-10-26
> 报告类型：**流水线架构/解调机制横向对标**，带 `相对路径:行号` 源码引用，不抄代码清单。

---

## 结论 TL;DR

1. **五条流水线在「同步字处理」上的策略可以统一归纳为一个三元组：`定位 → 锁定 → 降本`**。goestools 用 `correlate()` 全帧扫描取最大相关（`decoder/correlator.cc:37-76`），一旦 `lock_` 就把搜索窗口缩到 sync word 本身（`decoder/packetizer.cc:53-78`）；noaa-apt 用归一化互相关峰值 + 最小间距筛（`src/decode.rs:204-257`）；meteor_demod 干脆把「锁定」交给 PLL 内部的 `pll_get_locked()`（`dsp/pll.c:25`）；MBDSDR 的 `lrpt_demod.cc:13` 与 `apt_decoder.cpp` 各自实现了一遍，但**没有沉淀成可复用的「锁/失锁状态机」抽象**——这是本组最值得抄的第一块。

2. **「软判决 → 硬判决 → 字节流」的转换点各家位置不同**。goestools 的 `quantize.cc:32` 把 127 阶软比特发给 `soft_bit_publisher`，再由 `decoder.cc` 的 `QueueReader` 桥接给 `Packetizer`（`decoder.cc:13-60`）；meteor_demod 的 `demod_qpsk/demod_oqpsk` 直接返回符号后写 soft 文件（`demod.c:25-80`）；MBDSDR 的 `lrpt_demod.cc:125-126` 已经是「硬位流 → 软 ±1 → viterbi」。**结论：MBDSDR 的 LRPT 路径已经接近 goestools 的信息保真度，但缺「软比特统计/直方图」诊断接口**。

3. **CCSDS 传输层解包（VCDU → M_PDU → CP_PDU → TP_File → S_PDU → xRIT）是 xrit-rx 与 goestools 共享的骨架**，只是实现语言不同。xrit-rx 的 `ccsds.py:13-110`（VCDU/M_PDU）与 `demuxer.py:169-328`（Channel → handle_CPPDU → TP_File）几乎是逐字段复刻了 goestools 的 `assembler/vcdu.h` / `transport_pdu.h` / `session_pdu.h`。**MBDSDR 的 `lrpt_fec.cc:192-205`（`lrptDecodeCadu`）只做到了 CADU 级，没有往上走 VCDU/TP_File**——若要做 xRIT 级产品，需要把 xrit-rx 的分层搬过来（或直接复用 goestools 的 `assembler/`）。

4. **APT 与 LRPT 是「模拟」与「数字」两条完全不同的路，但「同步帧」这一步是同构的**。noaa-apt 的 `find_sync`（`decode.rs:204`）与 MBDSDR `apt_decoder.cpp` 的 7 脉冲同步都是「相关峰 + 最小间距 + 连续丢峰重锁」；差异在 noaa-apt **离线整段相关**、MBDSDR **流式滑窗相关**。两者的「保留一份 correlation 向量供 `export_steps` 导出」（`context.rs:318-326`）是 MBDSDR 目前完全缺的**调试钩子**——APT 解调失败时无从诊断。

5. **SigMF 是这五条流水线里唯一的「元数据契约」，MBDSDR 的 recorder 有两处已知偏离**（`core:frequency` 误放 global、`core:num_samples` 非标、`annotations` 恒空），具体见 §6。**这不影响内部闭环（file_source 读自己写的 meta 没问题），但会破坏「跨工具互操作」**（用 GNU Radio/SigMF 查看器打开 MBDSDR 录制会缺字段）。

---

## 1. goestools — GOES LRIT/HRIT 全链流水线

### 1.1 同步字：4 个编码同步字 + 相关取最大

`decoder/correlator.cc:10-21` 定义了 4 个 64-bit 编码同步字（LRIT/HRIT 各两个相位）：

```cpp
const uint64_t encodedSyncWords[4] = {
  // LRIT sync words
  0x035d49c24ff2686b,
  0xfca2b63db00d9794,
  // HRIT sync words
  0x03b10b02f33d2076,
  0xdafef4fd0cc2df89,
};
```

`correlate()`（`correlator.cc:37-76`）对整个 bit 流做滑窗 Hamming 相关（`64 - popcount(tmp ^ word)`），返回 4 个候选中的最大值及对应位置/相位：

```cpp
for (unsigned i = 0; i < len; i++) {
    tmp = (tmp << 1) | (data[i] & 0x80) >> 7;
    if (i < (encodedSyncWordBits - 1)) continue;
    for (unsigned j = 0; j < 4; j++) {
      auto v = 64 - __builtin_popcount(tmp ^ encodedSyncWords[j]);
      if (v > max[j]) { max[j] = v; pos[j] = i - (encodedSyncWordBits - 1); }
    }
}
```

**关键设计**：不做阈值判断，只取 argmax，把「是否可信」交给上层 `Packetizer` 的 lock 状态机。

### 1.2 锁定策略：先全帧扫，锁后只看 sync window

`decoder/packetizer.cc:53-90` 的锁定逻辑是本组最精致的一处：

```cpp
// If there is a frame lock, only run correlation detector against
// the sync word itself. This will ensure that we catch phase
// flips that happen so quickly that bit errors can be corrected.
if (lock_ && (syncType_ == LRIT_PHASE_000 || syncType_ == LRIT_PHASE_180)) {
  const auto skip = encodedFramePreludeBits;
  auto prevSyncType = syncType_;
  correlate(&buf_[skip], encodedSyncWordBits, nullptr, &syncType_);
  if (syncType_ != prevSyncType) {
    std::cerr << "Phase flip detected ..." << std::endl;
  }
}
```

**两个反直觉但正确的决定**：
- **锁后不全帧相关**：注释明确写了「帧内可能有随机序列相关性比我们锁的位置还高，会导致不必要的丢包」（`packetizer.cc:62-66`）。宁可等包损坏再重锁。
- **LRIT 才做 phase flip 检测**，HRIT 不做（`packetizer.cc:72-73`）——HRIT 没有这个相位歧义。

### 1.3 软判决 → 解码链

`goesrecv/demodulator.cc:90-123` 串了 5 级 `Queue<...>`：

```
source → AGC → Costas → RRC → ClockRecovery → Quantize → softBitsQueue_
```

`quantize.cc:13-32` 把复符号实部乘 127 量化成 `int8_t` 软比特：

```cpp
for (size_t i = 0; i < nsamples; i++) {
    int8_t v = rinput[i].real() * 127.0f;
    routput.push_back(v);
}
```

`decoder.cc:13-60` 的 `QueueReader` 把软比特队列桥接成 `read(2)` 风格接口喂给 `Packetizer`：

```cpp
virtual size_t read(void* buf, size_t count) {
    // ... Acquire new read buffer if we don't already have one
    //     memcpy; return buffer if exhausted
}
```

**这个「队列 → read(2) 适配器」是 C++ 流水线与 C 风格解码库对接的标准做法**，MBDSDR 若接 `libcorrect` 等 C 库可直接抄。

### 1.4 RS / Viterbi / 解扰

- `viterbi.h:43-47` 用 `correct_convolutional_decode_soft`（libcorrect SSE 或标量两路）。
- `derandomizer.cc:22-31` 是 CCSDS 标准 PN（`x^8+x^7+x^5+x^3+1`，init 0xff，周期 1020 字节）查表 XOR。
- `reed_solomon.h:22` 用 `correct_reed_solomon*`，`reed_solomon.cc:25-43` 做了 **dual-basis ↔ conventional** 双向查表（CCSDS Annex A），`reed_solomon.cc:69-87` 在 4 路交织块上逐块解 RS。**MBDSDR 的 `lrpt_fec.cc:110-114` 已经等价实现了（`ccsds_invert` + 4 路解交织 + FCR=112）**，两者算法一致，只是 goestools 借了 libcorrect、MBDSDR 自己写了 GF。

### 1.5 统计/诊断钩子

`goesrecv/stats_publisher.h:7-16` 把 demodulator 状态（gain、频偏、samples-per-symbol）以 JSON 发到 TCP：

```cpp
class StatsPublisher : public Publisher {
  static std::unique_ptr<StatsPublisher> create(const std::vector<std::string>& endpoints);
  void publish(const std::string& str);
};
```

配置在 `etc/goesrecv.conf:38-48`（`[demodulator.stats_publisher] bind = "tcp://0.0.0.0:6001"`）。**这是「实时诊断旁路」的范本**——MBDSDR 的 SpectrumEngine 目前只有 `emit aptImageReady(...)` 这类 Qt 信号，没有「把状态发到进程外」的能力。

---

## 2. noaa-apt — NOAA APT 模拟气象图

### 2.1 常量与工作率

`src/decode.rs:14-35` 是 APT 行结构的唯一真源：

```rust
pub const FINAL_RATE: u32 = 4160;
pub const PX_SYNC_FRAME: u32 = 39;
pub const PX_SPACE_DATA: u32 = 47;
pub const PX_CHANNEL_IMAGE_DATA: u32 = 909;
pub const PX_TELEMETRY_DATA: u32 = 45;
pub const PX_PER_CHANNEL: u32 = 1040;
pub const PX_PER_ROW: u32 = 2080;
```

**MBDSDR 的 `dsp/apt_decoder.h:23-36` 与这套常量完全一致**（注释里也标注了对齐来源），常量层面无偏离。

### 2.2 解码主流程

`src/decode.rs:43-155` 是 6 步流水：

1. **重采样到 work_rate**（`decode.rs:63-69`）：`filters::LowpassDcRemoval`，cutout 配 `resample_cutout`，过渡带 `resample_delta_freq`。
2. **AM 解调**（`decode.rs:84`）：`dsp::demodulate(context, &signal, Freq::hz(CARRIER_FREQ, work_rate))`——见 §2.3。
3. **二次低通**（`decode.rs:90-97`）：cutout = `FINAL_RATE / work_rate`（归一化 π 弧度），delta_w = cutout/5。
4. **同步**（`decode.rs:105-145`）：`find_sync` → 重排列对齐 → `truncate` 到行整数倍。
5. **重采样到 4160**（`decode.rs:151-153`）：`NoFilter`（因为前面已滤波）。
6. 出图（`noaa_apt.rs:180-191`）：灰度 → RGBA → 可选直方图均衡。

### 2.3 AM 解调：相位不变的包络检波

`src/dsp.rs:350-379` 的 `demodulate` 是一段精巧的无三角函数实现：

```rust
let phi = 2. * carrier_freq.get_rad();
let cosphi2 = phi.cos() * 2.;
let sinphi = phi.sin();
for i in 1..signal.len() {
    curr = signal[i];
    curr_sq = signal[i].powi(2);
    output[i] = (prev_sq + curr_sq - (prev * curr * cosphi2)).sqrt() / sinphi;
    prev = curr; prev_sq = curr_sq;
}
```

**几何解释**：`|x[n] - x[n-1]·e^{jω}|` 的模长展开，`sqrt` 后即包络，**对 NCO 初相不敏感**。MBDSDR 的 `apt_decoder.cpp:162-168` 用的是传统 I/Q 双臂 NCO + 低通 + 取模——两者等价，但 noaa-apt 省了两个 FIR。

### 2.4 同步：find_sync 的三段论

`src/decode.rs:204-257`：

```rust
fn find_sync(context, signal, work_rate) -> Result<Vec<usize>> {
    let guard = generate_sync_frame(work_rate)?;   // 7 脉冲模板
    let mut peaks: Vec<(usize, f32)> = vec![(0, 0.)];
    let min_distance = samples_per_work_row * 8 / 10;   // 0.8 行
    // 滑窗互相关
    for i in 0..signal.len() - guard.len() {
        let mut corr: f32 = 0.;
        for j in 0..guard.len() {
            match guard[j] { 1 => corr += signal[i+j], -1 => corr -= signal[i+j], _ => unreachable!() }
        }
        if i - peaks.last().0 > min_distance {
            while i / samples_per_work_row > peaks.len() { peaks.push((i, corr)); }
        } else if corr > peaks.last().1 {
            peaks.pop(); peaks.push((i, corr));
        }
    }
    if context.export_steps {
        context.step(Step::signal("sync_correlation", &correlation, None))?;
    }
}
```

**三个值得抄的设计**：
1. **`min_distance = 0.8 行`** 而非 `1.0 行`——留 20% 余量容忍行同步抖动。
2. **`while` 补峰**（`decode.rs:237-239`）：信号太长而峰太少时，按行号比例补 (i, corr) 占位，**而不是报错丢弃**——这是「诚实降级」而非「诚实失败」的范本。
3. **`context.export_steps` 把 correlation 向量导出**（`decode.rs:245-247`）——调试时可离线画相关曲线，**MBDSDR 的 `apt_decoder.cpp` 完全没有这个钩子**。

### 2.5 可选禁用同步

`src/config.rs:337-345` 的 `--no-sync` 选项：

```rust
.add_option(&["--no-sync"], argparse::StoreFalse,
    "Disable syncing, useful when the sync frames are noisy and the syncing attempts do more harm than good.");
```

`decode.rs:133-143` 的 `else` 分支把信号 `truncate` 到行整数倍并导出空 `sync_correlation` 步骤。**这个「关掉可能是最优」的设计是 noaa-apt 的成熟度体现**——噪声大时同步反而会把行边界搞乱。

### 2.6 遥测楔形与对比度

`src/telemetry.rs:30-66` 读 8 像素均值 → 16 楔形，1-9 号楔形取两帧平均：

```rust
let values_a: Signal = (1..=16).map(|wedge|
    if wedge <= 9 {
        (means_a[wedge - 1] + means_a[wedge + 16 - 1]) / 2.
    } else {
        means_a[wedge - 1]
    }
).collect();
```

`noaa_apt.rs:142-154` 的 `Contrast::Telemetry` 用 wedge 9/8 定标：

```rust
let low = telemetry.get_wedge_value(9, None);
let high = telemetry.get_wedge_value(8, None);
```

**MBDSDR 的 `apt_decoder.h:81-83` 只暴露了 `telemetryWedgeA()` 的原始 45 像素**，没有 wedge 分段/平均/定标逻辑——若要做「与 noaa-apt 等价的对比度调整」，需要补 `wedge 8/9 提取 + 两帧平均`。

---

## 3. xrit-rx — GK-2A xRIT 数字气象图

### 3.1 CCSDS 分层：VCDU → M_PDU → CP_PDU → TP_File → S_PDU → xRIT

`src/ccsds.py:13-110` 是 VCDU 与 M_PDU 的解包：

```python
class VCDU:
    def parse(self):
        header = self.data[:6]
        self.VER = self.tools.get_bits_int(header, 0, 2, 48)
        self.SCID = self.tools.get_bits_int(header, 2, 8, 48)
        self.VCID = self.tools.get_bits_int(header, 10, 6, 48)
        self.COUNTER = self.tools.get_bits_int(header, 16, 24, 48)
        self.REPLAY = self.tools.get_bits_int(header, 40, 1, 48)
        self.SPARE = self.tools.get_bits_int(header, 41, 7, 48)
        self.MPDU = self.data[6:]

class M_PDU:
    def parse(self):
        header = self.data[:2]
        self.POINTER = self.tools.get_bits_int(header, 5, 11, 16)   # First Pointer Header
        self.HEADER = self.POINTER != 2047   # 0x07FF
        self.PACKET = self.data[2:]
```

**`POINTER` 是分包边界指针**：`2047` 表示「本 M_PDU 全是续段」，否则指向新 CP_PDU 头在 `PACKET` 内的偏移。`demuxer.py:200-224` 的 `Channel.data_in` 用它切分：

```python
if mpdu.HEADER:
    if self.cTPFile is None and mpdu.POINTER == 0:
        self.cCPPDU = CCSDS.CP_PDU(mpdu.PACKET)
    else:
        if mpdu.POINTER != 0:
            preptr = mpdu.PACKET[:mpdu.POINTER]
        else:
            preptr = b''
        try:
            lenok, crcok = self.cCPPDU.finish(preptr, self.config.lut)
            self.handle_CPPDU(self.cCPPDU)
        except AttributeError:
            print("NO CP_PDU TO FINISH (DROPPED PACKETS?)")
        postptr = mpdu.PACKET[mpdu.POINTER:]
        self.cCPPDU = CCSDS.CP_PDU(postptr)
```

**「CP_PDU 跨 M_PDU 边界」是这套协议最易错的地方**，xrit-rx 的 `try/except AttributeError` 是「丢包时的诚实空态」而非崩溃。

### 3.2 传输文件组装与长度校验

`ccsds.py:281-325` 的 `TP_File`：

```python
def finish(self, data):
    self.append(data)
    plen = len(self.PAYLOAD)
    return plen == self.LENGTH   # lenok
```

`demuxer.py:328-372` 的 `handle_CPPDU` 在 `LAST` 段时校验长度并丢弃不完整的文件：

```python
elif cppdu.SEQ == cppdu.Sequence.LAST:
    lenok = self.cTPFile.finish(cppdu.PAYLOAD[:-2])
    if lenok:
        spdu = CCSDS.S_PDU(self.cTPFile.PAYLOAD, self.config.keys)
        self.handle_xRIT(spdu)
    elif not lenok:
        diff = ac - ex
        print("SKIPPING FILE DUE TO DROPPED PACKETS")
```

**「长度不符即丢整个文件」而非「尽力拼」**——比 goestools 的 `SessionPDU::finish()`（`session_pdu.h:44-47`）更激进，因为 xRIT 文件结构不能容忍残缺。

### 3.3 解密与 xRIT 头解析

`ccsds.py:360-460` 的 `S_PDU` 用 DES-ECB 解密，密钥索引在 Key 头（type 7）：

```python
def decrypt(self):
    decoder = DES.new(self.key, DES.MODE_ECB)
    decData = decoder.decrypt(self.dataField)
    self.PLAINTEXT = self.headerField + decData
```

`ccsds.py:463-510` 的 `xRIT` 解析头到 Annotation Text 头（type 4）取文件名：

```python
while nextHeader != 4:
    offset += self.get_header_len(offset)
    nextHeader = self.get_next_header(offset)
athLen = self.get_header_len(offset)
self.FILE_NAME = self.data[offset + 3 : offset + athLen].decode('utf-8')
```

### 3.4 产品层：多段图像拼接

`products.py:143-235` 的 `MultiSegmentImage`：

```python
def add(self, xrit):
    chan = xrit.FILE_NAME.split("_")[3]
    num = int(xrit.FILE_NAME.split(".")[0][-2:])
    self.images[chan][num] = img
    self.counter += 1
    total_segs = { "LRIT": 10, "HRIT": 50 }
    if self.counter == total_segs[self.config.downlink]: self.complete = True

def save(self):
    for c in self.images:
        img = Image.new("RGB", self.get_res(c))
        for s in self.images[c]:
            height = self.images[c][s].size[1]
            offset = height * (s - 1)
            img.paste(self.images[c][s], (0, offset))
        img.save(channel_path, format='JPEG', subsampling=0, quality=100)
```

**「段号 → 垂直偏移」的 `offset = height * (s - 1)` 是多段图像的标准做法**，MBDSDR 若做 GOES-R ABI 分段产品可直接复用。

### 3.5 丢包检测：VCDU continuity

`demuxer.py:281-300` 的 `Channel.continuity`：

```python
def continuity(self, vcdu):
    if self.counter != -1:
        if self.counter == 16777215 and vcdu.COUNTER == 0:
            self.counter = vcdu.COUNTER; return   # 计数器回绕
        diff = vcdu.COUNTER - self.counter - 1
        if diff > 0:
            print(f"DROPPED {diff} PACKETS")
    self.counter = vcdu.COUNTER
```

**24-bit 计数器回绕单独处理**（`16777215 → 0`）——这种边界细节是「工程成熟度」的标尺。

---

## 4. meteor_demod — Meteor-M2 LRPT OQPSK/QPSK 解调

### 4.1 一条命令的极简架构

`main.c:130-260` 是「解析参数 → 开 WAV → `demod_init` → 起线程 → TUI/批处理」的直线流程，无配置文件、无插件。`demod.h:8-13` 是全部卫星参数：

```c
#define RRC_ALPHA 0.6
#define SYM_RATE 72000.0
#define RRC_ORDER 32
#define INTERP_FACTOR 5
#define SYM_BW 0.00005
#define PLL_BW 1
```

**「卫星参数集中在 6 个宏」是 meteor_demod 的可读性优势**——MBDSDR 的 LRPT 参数散在 `lrpt_demod.cc`/`lrpt_fec.cc` 多处常量。

### 4.2 解调：RRC + AGC + PLL + 定时

`demod.c:8-21` 的 `demod_init` 把 5 个 DSP 组件串起来：

```c
void demod_init(float pll_bw, float sym_bw, int samplerate, int symrate,
                int interp_factor, int rrc_order, int oqpsk, float freq_max) {
    const int multiplier = oqpsk ? 1 : 2;
    pll_init(2*M_PI*pll_bw/(multiplier * symrate), oqpsk, freq_max);
    timing_init(2*M_PI*symrate/(samplerate*interp_factor), sym_bw/interp_factor);
    filter_init_rrc(&_rrc_filter, rrc_order, (float)samplerate/symrate, RRC_ALPHA, interp_factor);
}
```

**OQPSK 的 `multiplier = 1`（每符号 2 采样）、QPSK 的 `multiplier = 2`（每符号 1 采样）**——这一行决定了 PLL 带宽归一化，是 OQPSK/QPSK 共用一条 PLL 的关键技巧。

`demod.c:46-80` 的 `demod_qpsk`/`demod_oqpsk` 都是「RRC → AGC → PLL 混频 → 定时 → 更新估计」的循环，差异只在 OQPSK 分 I/Q 两路交错：

```c
case 1:  /* Intersample */
    inphase = pll_mix_i(out);     /* 只关心 I */
    break;
case 2:  /* Actual sample */
    quad = pll_mix_q(out);        /* 只关心 Q */
    *sample = inphase + I*quad;
    retime(*sample);
    pll_update_estimate(crealf(*sample), cimagf(*sample));
    ret = 1;
```

### 4.3 PLL：二阶环 + tanh LUT 鉴相

`dsp/pll.c:25-41` 的 `pll_init`：

```c
void pll_init(float bw, int oqpsk, float freq_max) {
    for (i = 0; i < (int)LEN(_lut_tanh); i++)
        _lut_tanh[i] = (float)tanh(i - 16);   // 32 项 tanh LUT
    update_alpha_beta(M_1_SQRT2, bw);         // 阻尼 = 1/√2
}
```

`pll.c:59-72` 的 `pll_mix` 用 `fast_sin/fast_cos`（`dsp/sincos.c`）做 NCO 混频，`_phase += _freq` 推进。**「tanh LUT 鉴相 + 阻尼 1/√2 的 α-β 二阶环」是低成本载波恢复的经典配方**，MBDSDR 的 `lrpt_demod.cc` 用的是「CFO 扫描 + 前馈」（无 PLL）——两者是不同取舍（前馈无环路延迟但需预知频偏范围）。

### 4.4 定时恢复：Mueller-Muller

`dsp/timing.c:15-33` 的 `timing_init` + `advance_timeslot`：

```c
void timing_init(float sym_freq, float bw) {
    _freq = sym_freq;
    _center_freq = _freq;
    _freq_max_dev = _freq / (1 << FREQ_DEV_EXP);   // FREQ_DEV_EXP = 12
    update_alpha_beta(1, bw);                       // 阻尼 = 1
}
int advance_timeslot() {
    _phase += _freq;
    return _phase >= 2 * (float)M_PI;
}
```

**`freq_max_dev = freq / 4096` 的偏差钳位**（`FREQ_DEV_EXP = 12`）防止定时环发散——这种「参数自保护」在 MBDSDR 的 `lrpt_demod.cc` 中没有对应物（CFO 扫描范围是硬编码的）。

### 4.5 TUI 诊断：星座图 + 锁状态

`main.c:232-235` 实时打印：

```c
freq_hz = pll_get_freq()*symrate/(2*M_PI)*(demod == demod_oqpsk ? 2 : 1);
rate_hz = mm_omega()*(samplerate*interp_factor)/(2*M_PI);
message("(%5.1f%%) Carrier: %+7.1f Hz, Symbol rate: %.1f Hz, Locked: %s", ...);
```

`tui_update_pll(...)` + `tui_draw_constellation(_symbols_ring, LEN(_symbols_ring))`（`main.c:251-252`）把星座图喂给 ncurses。**「解调器状态 + 星座图」的双视图是解调器调试的最小充分集**。

---

## 5. SigMF — 射频录制元数据契约

### 5.1 根结构：三数组必填

`sigmf-schema.json:8-11`：

```json
"required": ["global", "captures", "annotations"]
```

**`annotations` 是必填的（可为空数组）**——这是 SigMF 与「随意 JSON sidecar」的本质区别。

### 5.2 global 字段

`sigmf-schema.json:13-15` 的 description：「applicable to the entire Dataset ... minimally necessary to open and parse the Dataset file」。必填仅两项（`sigmf-schema.json:20-23`）：

```json
"required": ["core:datatype", "core:version"]
```

常用可选字段（`sigmf-schema.json:25-130`）：`core:sample_rate`、`core:num_channels`、`core:hw`、`core:author`、`core:recorder`、`core:license`、`core:geolocation`（GeoJSON Point）、`core:sha512`（数据文件哈希，`sigmf-schema.json:101-105`）、`core:offset`（多文件录制的首样本索引，`sigmf-schema.json:89-99`）。

**注意：SigMF 1.2.6 没有 `core:num_samples` 字段**——样本数应由 `.sigmf-data` 文件大小 ÷ `core:datatype` 字宽推导。

### 5.3 captures 语义

`sigmf-schema.json:184`：

> The `captures` Object is an array of capture segment objects ... MUST be sorted by the value of each capture segment's `core:sample_start` key, ascending. ... Each Segment MUST contain a `core:sample_start` key/value pair ... The fields that are described within a Capture Segment are scoped to that Segment only and need to be explicitly declared again if they are valid in subsequent Segments.

字段（`sigmf-schema.json:194-230`）：`core:sample_start`（必填）、`core:datetime`（ISO-8601 UTC）、`core:frequency`、`core:global_index`（硬件样本计数器，用于检测丢样）、`core:header_bytes`、`core:geolocation`。

**`core:global_index` 是检测「硬件丢样」的关键**：录制的第 N 个样本可能不是硬件计数的第 N 个（`sigmf-schema.json:220` 的示例正是这个场景）。

### 5.4 annotations 语义

`sigmf-schema.json:268`：

> The `annotations` Object is an array of annotation segment objects that describe anything regarding the signal data not part of the Captures and Global objects. It MUST be sorted by the value of each Annotation Segment's `core:sample_start` key, ascending. ... There is no limit to the number of annotations that can apply to the same group of samples. If two annotations have the same `sample_start`, there is no defined ordering between them. If `sample_count` is not provided, it SHOULD be assumed that the annotation applies from `sample_start` through the end of the corresponding capture, in all other cases `sample_count` MUST be provided.

字段（`sigmf-schema.json:275-320`）：`core:sample_start`（必填）、`core:sample_count`、`core:freq_lower_edge`/`core:freq_upper_edge`（**成对提供，只给一个不合法**，`sigmf-schema.json:291`）、`core:label`（≤20 字符，`sigmf-schema.json:303`）、`core:comment`、`core:generator`、`core:uuid`。

### 5.5 扩展机制

`sigmf-schema.json:148-149` 的 `core:extensions` 数组，每项 `{name, version, optional}`：

```json
"core:extensions": [
  {"name": "extension-01", "version": "0.0.5", "optional": true},
  {"name": "extension-02", "version": "1.2.3", "optional": false}
]
```

`optional: false` 表示「应用必须支持本扩展才能解析」（`sigmf-schema.json:149`）。仓库自带 5 个扩展（`sigmf/extensions/`）：

| 扩展 | 作用域 | 用途 |
|---|---|---|
| `antenna` | global | 天线增益/方向图 |
| `capture_details` | captures + annotations | 静态/动态 IQ 采集参数（`acq_scale_factor`、`attenuation`、`SNRdB`） |
| `signal` | annotations | 通信信号属性（`mod_class`、`standard`、`channel_bw`、`emitter`） |
| `spatial` | global + captures | 天线阵列几何与测向（`bearing`、`cartesian_point`） |
| `traceability` | global + annotations | 元数据变更溯源（`last_modified`、`revision`、`origin`） |

`collection-schema.json` 还定义了多录制聚合（`core:streams` 数组指向多个 SigMF 录制）。

### 5.6 MBDSDR 现状 vs SigMF 契约

`cpp/src/dsp/recorder.cpp:91-112` 当前写法：

```cpp
global["core:datatype"] = "cf32_le";
global["core:sample_rate"] = sampleRate_;
global["core:version"] = "1.0.0";
global["core:num_channels"] = 1;
global["core:frequency"] = centerFreq_;     // ⚠ 误放 global
global["core:hw"] = segHardware_;
global["core:author"] = "MBDSDR";
global["core:num_samples"] = 0;             // ⚠ 非标字段

QJsonObject capture;
capture["core:sample_start"] = 0;
capture["core:frequency"] = centerFreq_;    // ✅ 正确位置
capture["core:datetime"] = ...;
capture["mbdsdr:gain_db"] = segGainDb_;     // 私有命名空间，合法

meta_["annotations"] = QJsonArray();        // ⚠ 恒空
```

**三处偏离**：

1. `core:frequency` 出现在 `global`（`recorder.cpp:95`）——SigMF 中 `core:frequency` 是 **captures 字段**（`sigmf-schema.json:202`），不是 global 字段。重复写不会报错但会误导消费方。
2. `core:num_samples`（`recorder.cpp:98`）不是 SigMF 1.2.6 字段。`file_source.cpp:53` 读它来算 `totalSamples_`——**MBDSDR 内部闭环自洽，但外部工具会忽略**。规范做法是文件大小推导。
3. `annotations` 恒空（`recorder.cpp:111`）——合法（`sigmf-schema.json:268` 允许空数组），但浪费了「标注通道」的表达力。理想做法是把 VFO/解调模式/丢样区间写成 annotation。

`tools/onboarding/onboard.py:432-453` 的 Python 侧是**同一套偏离的复刻**（`core:num_samples` 在 `:449`、`annotations: []` 在 `:453`）——修的时候两边要同步。

---

## 6. 横向对比

### 6.1 同步/锁定机制

| 项目 | 同步字 | 相关方式 | 锁定判据 | 失锁重锁 |
|---|---|---|---|---|
| goestools | 4 个 64-bit 编码同步字（LRIT/HRIT × 2 相位）`correlator.cc:10-21` | 全帧滑窗 Hamming，取 argmax `correlator.cc:37-76` | `Packetizer` lock 状态机；锁后只看 sync window `packetizer.cc:53-78` | 包损坏才重锁；LRIT 检测 phase flip |
| noaa-apt | 7 脉冲 APT 同步帧 `decode.rs:171-201` | 滑窗互相关 + 最小间距 0.8 行 `decode.rs:218-243` | 峰值数 ≥5 `decode.rs:111` | `--no-sync` 可整体禁用 `config.rs:337-345` |
| xrit-rx | 无 RF 同步（吃 VCDU 字节流） | — | VCDU continuity counter `demuxer.py:281-300` | 计数器回绕单独处理 |
| meteor_demod | 无帧同步（符号级） | — | `pll_get_locked()` 内部判据 `pll.c:25` | `_locked_once` 标志；TUI 显示 |
| MBDSDR LRPT | `kSyncWord = 0xFCA2B63DB00D9794`（= goestools LRIT_PHASE_180）`lrpt_demod.cc:13` | 8 个相位假设 Hamming 扫描 `lrpt_demod.cc:56-95` | `bestH <= 4` 阈值 `lrpt_demod.cc:95` | 每块重扫 |
| MBDSDR APT | 7 脉冲模板 `apt_decoder.cpp` | 归一化 Pearson 滑窗相关 | `kAcquireCorr=0.45` 上升沿 + `kAcquireFall=0.27` 下降沿提交 `apt_decoder.cpp:25-28` | `kMaxMisses=3` 连续丢峰重锁 `apt_decoder.cpp:29` |

**结论**：MBDSDR 的 APT 同步阈值设计（上升/下降双阈值 + 连续丢峰）比 noaa-apt 的「峰数 ≥5」更稳健，但**缺 correlation 向量导出**（noaa-apt 的 `context.step(Step::signal("sync_correlation", ...))`）。

### 6.2 解调架构

| 项目 | 载波恢复 | 定时恢复 | 成形滤波 | 软判决 |
|---|---|---|---|---|
| goestools | Costas 环 `costas.cc` | ClockRecovery（Mueller-Muller）`clock_recovery.cc:212-233` | RRC `rrc.cc` | 127 阶 int8 `quantize.cc:24-27` |
| meteor_demod | 二阶 PLL + tanh LUT `pll.c:25-72` | 二阶定时环 + 频偏钳位 `timing.c:15-33` | RRC α=0.6 `demod.c:14` | 符号直写 soft 文件 |
| MBDSDR LRPT | CFO 扫描 + 前馈 `lrpt_demod.cc:56-95` | 符号索引对齐（无环路） | 无独立 RRC | 硬位 → ±1 软 `lrpt_demod.cc:125-126` |
| MBDSDR APT | I/Q NCO + 低通 + 取模 `apt_decoder.cpp:162-168` | 分数累加器 4160 px/s `apt_decoder.cpp:8` | 自设计窗函数 sinc `apt_decoder.cpp:31-35` | 包络（无软判决） |

**结论**：MBDSDR LRPT 的「前馈 CFO 扫描」适合**已知频偏范围的离线/短块**场景，但对**长时间漂移**（卫星多普勒）不如 goestools 的 Costas 环。若做实时 LRPT，建议补 PLL 路径。

### 6.3 传输层解包

| 层级 | goestools | xrit-rx | MBDSDR |
|---|---|---|---|
| CADU/ASM | `decoder.cc` + `derandomizer.cc` | （上游给 VCDU） | `lrpt_fec.cc:192-205` `lrptDecodeCadu` |
| VCDU | `assembler/vcdu.h:10-30` | `ccsds.py:13-78` | ❌ 无 |
| M_PDU | `assembler/virtual_channel.h` | `ccsds.py:81-110` | ❌ 无 |
| CP_PDU / TP_File | `assembler/transport_pdu.h:8-45` | `ccsds.py:121-325` | ❌ 无 |
| Session_PDU / S_PDU | `assembler/session_pdu.h:36-110`（含 SZIP 解压） | `ccsds.py:360-460`（含 DES 解密） | ❌ 无 |
| 文件 / 产品 | `lrit/lrit.h` + `goesproc/handler_*.h` | `ccsds.py:463-510` `xRIT` + `products.py` | ❌ 无 |

**结论**：MBDSDR 只做到 CADU，往上任何一层都是空白。若要做 xRIT/LRIT 产品，**建议直接移植 xrit-rx 的 `ccsds.py`（Python，MIT）或 goestools 的 `assembler/`（C++，MIT）**，不要从零写。

### 6.4 元数据契约

| 项目 | 元数据格式 | 样本数字段 | 标注通道 | 扩展机制 |
|---|---|---|---|---|
| SigMF | JSON（`global`/`captures`/`annotations`） | 文件大小推导 | `annotations[]`（无上限） | `core:extensions[]` + 5 个官方扩展 |
| MBDSDR recorder | JSON（偏离 SigMF 三处，§5.6） | `core:num_samples`（非标） | 恒空 | 无 |
| MBDSDR onboard.py | 同上（Python 复刻） | 同上 | 同上 | 无 |
| goestools | 无独立元数据（LRIT 自带头） | — | — | — |
| xrit-rx | xRIT 头（CCSDS 标准） | `TP_File.LENGTH` | — | — |
| noaa-apt | `context.step()` 导出 WAV/Filter 中间产物 | — | `Step::signal` 步骤即标注 | `export_steps` 开关 |

---

## 7. 给 MBDSDR 的落地建议

> 按「性价比 = 收益 / 改动面」排序，≤10 条，带 `文件:行号`。

### 建议 1（高收益 / 小改动）：给 `AptDecoder` 加 correlation 向量导出钩子

noaa-apt 的 `context.step(Step::signal("sync_correlation", &correlation, None))`（`src/decode.rs:245-247`）在同步失败时可离线画相关曲线定位问题。MBDSDR 的 `apt_decoder.cpp` 当前只有 `lastSyncCorrelation()` 单标量（`apt_decoder.h:78`）。

**落地**：在 `AptDecoder` 加 `std::vector<float> lastCorrelationTrace() const`，滑窗相关时按 N 采样抽稀保存最近 K 个相关值；`weather_panel.cpp` 可选显示。改动面 ~30 行。

### 建议 2（高收益 / 小改动）：把 APT 同步的「最小间距」参数化

noaa-apt 的 `min_distance = 0.8 行`（`src/decode.rs:217`）是经验值。MBDSDR `apt_decoder.cpp` 的 `nextCheckSample_` 是硬编码一整行（`apt_decoder.cpp:283`）。

**落地**：把「行检查窗口」从 `1.0 行` 放宽到 `[0.7, 1.3] 行` 的滑窗，容忍行同步抖动。改动面 ~15 行。

### 建议 3（高收益 / 中改动）：补 SigMF 三处偏离（§5.6）

`recorder.cpp:95`（`core:frequency` 误放 global）、`:98`（`core:num_samples` 非标）、`:111`（`annotations` 恒空）；`onboard.py:442-453` 同步修。

**落地**：
1. 删 `global["core:frequency"]`（`recorder.cpp:95`），只保留 `capture["core:frequency"]`。
2. `core:num_samples` 改名为 `mbdsdr:num_samples`（私有命名空间，合法）或删掉、`file_source.cpp:53` 改为文件大小推导。
3. `annotations` 至少写一条 `{core:sample_start: 0, core:sample_count: <n>, core:label: "<mode>"}`。
4. `onboard.py:442-453` 同步三处。

改动面 ~50 行（C++）+ ~20 行（Python）+ 测试更新。

### 建议 4（中收益 / 小改动）：给 `RecordingLibrary` 加 SigMF 扫描

`ui/recording_library.cpp:52` 只扫 `*.wav`，但 recorder 写的是 `*.sigmf-data` + `*.sigmf-meta`（`recorder.cpp:60-61`）——**录制产物在库里看不见**。

**落地**：`entryInfoList({"*.wav", "*.sigmf-data"})`，SigMF 条目读 `.sigmf-meta` 填 `RecordingEntry`。改动面 ~40 行。

### 建议 5（中收益 / 中改动）：抽出「软判决统计」诊断接口

goestools 的 `stats_publisher.h`（JSON over TCP）与 meteor_demod 的 TUI 星座图（`main.c:251-252`）是两种诊断形态。MBDSDR 的 `lrpt_demod.cc:125` 生成软 ±1 后直接进 Viterbi，中间无统计。

**落地**：`LrptDemod` 加 `struct SoftStats { double mean, var; int clipped; }`，每块回填；`main_window.cpp` 的 LRPT 面板显示。改动面 ~60 行。

### 建议 6（中收益 / 大改动）：把「同步/锁定状态机」抽成可复用抽象

goestools `packetizer.cc:53-90`、noaa-apt `decode.rs:204-257`、MBDSDR `apt_decoder.cpp`/`lrpt_demod.cc` 四处都是「定位 → 锁定 → 降本」三元组，但各自实现。

**落地**：新建 `cpp/src/dsp/sync_lock.h` 模板类 `SyncLock<WordT>`，提供 `locate()` / `locked()` / `onMiss()` 三个虚接口；APT/LRPT 各自子类化。改动面 ~200 行（含测试），收益是未来 M17/CW 等同步可复用。

### 建议 7（低收益 / 中改动）：补 `--no-sync` 等价的「禁用同步」开关

noaa-apt 的 `config.rs:337-345` 在同步帧噪声大时允许跳过同步。MBDSDR `apt_decoder.cpp` 总是尝试同步（`kAcquireCorr=0.45`）。

**落地**：`AptDecoder::setSyncEnabled(bool)`，禁用时直接按固定行长切片。改动面 ~25 行。

### 建议 8（低收益 / 大改动）：接 xrit-rx/goestools 的 CCSDS 传输层

MBDSDR `lrpt_fec.cc:192-205` 停在 CADU。若要做 GOES LRIT/HRIT 产品，需 VCDU/M_PDU/CP_PDU/TP_File/S_PDU/xRIT 六层。

**落地**：移植 xrit-rx 的 `ccsds.py`（MIT，~600 行 Python）到 `tools/xrit/`，或移植 goestools `assembler/`（MIT，~400 行 C++）到 `cpp/src/ccsds/`。改动面大，**建议按需启动，不要预先做**。

### 建议 9（低收益 / 小改动）：LRPT 参数集中到一张表

meteor_demod 的 `demod.h:8-13` 把 6 个卫星参数集中在宏。MBDSDR `lrpt_demod.cc`/`lrpt_fec.cc` 的常量散在多处（`kSyncWord`、`kSyncBits`、`Kk=223`、`NSYM=32`、`FCR=112`）。

**落地**：新建 `cpp/src/dsp/lrpt_params.h` 集中声明，两个 .cc 引用。改动面 ~20 行。

### 建议 10（低收益 / 中改动）：把 APT 遥测楔形做完整定标

noaa-apt 的 `telemetry.rs:30-66`（8 像素均值 → 16 楔形 → 1-9 号两帧平均）+ `noaa_apt.rs:142-154`（wedge 8/9 定标）。MBDSDR `apt_decoder.h:81-83` 只暴露原始 45 像素 `wedgeA_`。

**落地**：`AptDecoder` 加 `std::array<float,16> telemetryWedges() const`（含两帧平均），`weather_panel.cpp` 用 wedge 8/9 做对比度调整。改动面 ~80 行。

---

## 8. 一句话收束

这五个项目是**同一个问题域（把射频变可用数据）的五种切法**：goestools 切「实时流水线 + 诊断旁路」，noaa-apt 切「离线逐步可导出」，xrit-rx 切「协议分层可换实现」，meteor_demod 切「极简一条命令」，SigMF 切「元数据可互操作」。MBDSDR 目前最接近 meteor_demod（解调核心自研）+ goestools（有诊断信号），最缺 noaa-apt 的「中间产物导出」与 SigMF 的「元数据契约严守」。**优先补建议 1/3/4（小改动高收益）**，建议 6/8 按需启动。
