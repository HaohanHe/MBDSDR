# P4：SSTV/SSDV 移植 C++ 评估与落地

> 日期：2026-10-02。活动窗口剩 6 天（10-08~10 航天七十载通联）。
> 基线：cpp ctest 101/101（offscreen，改前实测）。
> 范围：把 `mbdsdr_ai/sstv_decoder.py` 与 P2 在写的 `ssdv_decoder.py` 移植到 `cpp/src/dsp/`。
> 红线：MIT 干净室；只学公开协议/机制，不抄 GPL；未 commit/push。

## 0. 总结论（先给取舍）

**Python 先收到图是唯一正确取舍，C++ 全量移植明确列入活动后 backlog。**

- 全量移植估算 **SSTV ≈ 30–45 人时、SSDV ≈ 25–35 人时，合计 55–80 人时**，超过 6 天窗口，且会挤占 P1（onboard sstv 真机闭环）/ P2（ssdv_decoder.py）/ P3（活动手册）。
- Python 端已被真实录音验证（`real_sstv.wav` → PNG），弱信号启发式（周期 CV 门限、色差直流恢复、逐行/组首布局自识别）是对着 over-the-air 录音调出来的；盲搬到 C++ 再调参没有 REPL 迭代，代价以天计。
- 云内无硬件，C++ 侧只能用合成信号自测，无法替代真机联调。
- 本次只落地**小而高价值、确定性可测、与 Python KAT 交叉验证**的纯函数零件（见 §4），其余诚实列 backlog，不铺空壳。

## 1. 现状盘点（读码事实）

### 1.1 Python 参考
- `mbdsdr_ai/sstv_decoder.py`（1061 行，MIT）：
  - `_instantaneous_frequency` :197-247（过零率+线性插值瞬时频率，去直流）
  - `_freq_to_pixel` :250-253（1500=黑 / 2300=白，线性裁剪）
  - `_detect_vis_header` :256-319（1900Hz break、30ms 位、1100=1/1300=0、偶校验）
  - `_find_sync_markers` :330-401（1200Hz 同步段形态学 + 周期清洗 + 锚点外推）
  - `_identify_sstv_mode` :404-481（按脉宽/周期中位/CV 判 Martin/Scottie/Robot/PD，噪声门限）
  - `_decode_robot36` :510-639、`_decode_robot72` :642-695、`_decode_pd` :698-767（数据驱动像素时钟、两行组色度共享、全帧色差中值回 128）
  - 诚实失败：:888-895 自动识别不出来时返回 unknown，不兜底出垃圾图
- `mbdsdr_ai/fec.py`（MIT）：`ReedSolomon` :148-201（RS(255,223)，nsym=32/fcr=112/prim 0x187/CCSDS 0xFF 符号反转），BM+Chien+Forney 解码 :206 起。
- P2 在写的 `ssdv_decoder.py`：本次勘察时仓内尚不存在（只有 `_PHASE14_SPEC.md` 提及），设计以公开 SP5WWP SSDV 规范为准。

### 1.2 C++ 现有可复用零件
| 零件 | file:line | 对移植的价值 |
|---|---|---|
| GFSK 正交鉴频 + M&M 钟恢复 | `cpp/src/dsp/fsk_demod.h:28-69` | SSDV 数字物理层直接复用；SSTV 不需要 |
| Nuttall 窗 sinc 低通（带 tail） | `cpp/src/dsp/demod.h:44-55` | 鉴频后基带 LPF，两侧都用 |
| NFM 正交鉴频器 | `cpp/src/dsp/demod.h:77-96` | SSTV 音频前端（FM 语音信道）已有 |
| NCO + 有理重采样 channelizer | `cpp/src/dsp/channelizer.h:18-30,83-129` | 多普勒/速率精确对齐 |
| 字节层 CRC 循环范式 | `cpp/src/dsp/adsb_decoder.cpp:136-147`（`crc24` 位驱动多项式） | SSDV 包头/校验字节层的写法模板 |
| 有理重采样器 | `cpp/src/dsp/rational_resampler.h` | 48kHz SSTV 目标率对齐 |

## 2. 模块拆分与工作量估算（人时）

### 2.1 SSTV 移植（按依赖顺序）
| 模块 | 内容 | 估时 | 状态 |
|---|---|---|---|
| 音频前端接线 | channelizer → DemodNFM → 48k 音频（零件已有，串线+块流） | 2–4 | backlog |
| 瞬时频率（过零法） | 去直流/插值/区间赋频 | 2 | **已落地**（`sstv_vis.cpp`） |
| VIS 解码纯函数 | 位均值→码+偶校验 | 1 | **已落地** |
| 频率→像素映射 | 线性裁剪 | 0.5 | **已落地** |
| 同步段检测 + 制式识别 | 形态学标签、周期中位/CV、锚点外推——弱信号启发式重调 | 8–12 | backlog |
| 行解码器族 | Martin/Scottie/Robot36/72/PD，数据驱动像素时钟、色差直流恢复 | 12–16 | backlog |
| PNG 输出 | Qt QImage 写盘 | 2 | backlog |
| 块流适配 | Python 整文件处理 → C++ 分块有状态流 | 6–8 | backlog |
| **小计** | | **≈32–44** | |

### 2.2 SSDV 移植
| 模块 | 内容 | 估时 | 状态 |
|---|---|---|---|
| 256 字节包头解析 | flags/imageId/seq/width/jpegLen、FEC 分支长度校验 | 1.5 | **已落地** |
| RS(255,223) 编码器 | GF(256) 表 + 生成多项式 + 多项式除法（CCSDS 参数） | 2 | **已落地** |
| RS 解码器 | Berlekamp-Massey + Chien + Forney，KAT 对拍 Python | 8–10 | backlog |
| 按序重组 JPEG | 包序号重排、拼接 JPEG 流 | 3–4 | backlog |
| JPEG 出图 | 依赖 Qt/系统 JPEG 解码 | 2–3 | backlog |
| 物理层（4FSK/GMSK） | fsk_demod 现是 2-FSK；扩 4 电平判决 + 包同步 | 10–16 | backlog |
| **小计** | | **≈27–37** | |

## 3. 风险（活动时间窗视角）

1. **调参风险（最高）**：Python 弱信号门限（period_cv<0.35、min markers≥8、色差中值回 128）是对真实录音过拟合后又泛化的结果。C++ 盲移植后没有真机录音迭代，极易"合成信号全绿、真机斜条纹"。
2. **窗口风险**：55–80 人时 > 6 天；且 P1/P2/P3 是活动取证主线，C++ 不能分流人力。
3. **无硬件**：云内只能合成信号往返，C++ 侧新增代码也无法做真机验收。
4. **协议不确定性**：主办方频率/TLE/SSD V 物理层细节未发布；C++ 物理层现在写完也可能返工。
5. **机会成本**：每投 C++ 一小时，onboard 两模式联调就少一小时。

## 4. 本次落地项（确定性可测、KAT 对拍 Python）

干净室新写（仅 `cpp/src/dsp/`、`cpp/tests/`、`cpp/CMakeLists.txt` 追加）：

- `cpp/src/dsp/ssdv_packet.h` / `.cpp`
  - `parseSsdvHeader`（256 字节包 6 字节头解析 + 长度/FEC 分支校验）
  - `ssdvJpegPayload`（JPEG 载荷指针/长度切片）
  - `RsCcsds`：GF(256)（prim 0x187）+ RS(255,223) 编码器（fcr=112、0xFF 符号反转）
- `cpp/src/dsp/sstv_vis.h` / `.cpp`
  - `sstvFreqToPixel`、`sstvDecodeVisBits`（VIS 7 数据位+偶校验）
  - `sstvInstFreqZeroCrossing`（过零瞬时频率，去直流/线性插值；**未移植** Python 的 scipy 中值滤波，刻意保持极小、可测）
- 测试：`cpp/tests/test_ssdv_packet.cpp`、`cpp/tests/test_sstv_vis.cpp`
  - RS 编码器 parity 与 Python `fec.py` 输出逐字节一致（KAT：`a05a…7574`）
  - 全零消息 → 全零 parity（CCSDS KAT）
  - Robot36 VIS=0x08 解码 + 偶校验正反两例 + 带外位拒绝
  - 1200/1900Hz 合成正弦 → 过零法中值频率在 ±150Hz 内

## 5. Backlog（明确不本次做）

- RS(255,223) 解码器（收端纠错，活动期间由 Python P2 承担）。
- SSTV 同步段检测 + 制式识别状态机（弱信号启发式，待活动后用真机录音再调）。
- SSTV 行解码器族、块流适配、PNG 输出。
- SSDV 包重组 + JPEG 输出、4FSK/GMSK 物理层扩展。
- 以上均可在本次落地的字节层/纯函数基座上接续，不再重复造 GF 表与包头结构。

## 6. 验证

- 改前：`ctest` offscreen **101/101** 通过。
- 落地后：新增 `ssdv_packet`、`sstv_vis` 两测试通过；全量 ctest 数字见交付回报（应为 103/103）。
