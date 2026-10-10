# LRPT 第②步：卷积内码 + RS 外码（落地对照）

> 日期：2026-10-10  作者：MBDSDR 工程轮（phase63）
> 配套：`weather-sat-digital-study.md`（机制笔记）、`lrpt-step1.md`（第①步解调+同步）
> 本步范围：Viterbi K7 r1/2 软判决译码 + CCSDS 解扰 + RS(255,223)×4 交织解码。

---

## 1. 落地文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `mbdsdr_ai/lrpt_fec.py` | ~280 | Viterbi + CADU 解码链（干净室 MIT，纯 NumPy） |
| `mbdsdr_ai/tests/test_lrpt_fec.py` | ~160 | 12 个确定性 pytest |

---

## 2. 机制落地对照（笔记 file:line → 实现 file:line）

### 2.1 Viterbi K=7 r=1/2

| 机制（笔记证据） | 实现位置 | 自述 |
|-----------------|---------|------|
| 多项式 {79,109}（viterbi27.h:8） | `lrpt_fec.py:48-49` `CCSDS_POLY1=79, CCSDS_POLY2=109` | CCSDS 公开域，octal 117/155 |
| 64 状态 trellis | `lrpt_fec.py:55` `NUM_STATES=64` | 2^(K-1) |
| 预计算转移表 | `lrpt_fec.py:86-110` `_build_transitions()` | next_state[s,b] + output[s,b,(c0,c1)] |
| 分支度量（软判决） | `lrpt_fec.py:174-178` | reward = (2·c0−1)·s0 + (2·c1−1)·s1；期望 c=1 时正奖励 |
| ACS（add-compare-select） | `lrpt_fec.py:179-181` | 每状态保留最大度量路径 |
| 回溯 | `lrpt_fec.py:190-197` | 从最大度量状态反向追溯 |
| 尾比特归零（K-1=6） | `lrpt_fec.py:67, 200` | 编码端补 6 个 0，解码端去掉最后 6 位 |

### 2.2 CCSDS 解扰

| 机制（笔记证据） | 实现位置 | 自述 |
|-----------------|---------|------|
| PN x^8+x^7+x^5+x^3+1 init 0xff | `fec.py:28-61` `CCSDS_PN` 255 字节表 | 复用本仓既有 MIT 实现 |
| 解扰 = 自逆（XOR） | `fec.py:343-348` `Scrambler.scramble()` | 查表异或，周期 255 字节 |
| 1020 字节数据段解扰 | `lrpt_fec.py:256` `decoder.decode_cadu()` | 跳过 4 字节 ASM，对 1020 字节解扰 |

### 2.3 RS(255,223)×4 交织

| 机制（笔记证据） | 实现位置 | 自述 |
|-----------------|---------|------|
| RS(255,223) fcr=112 index=11 roots=32 | `fec.py:166` `ReedSolomon(nsym=32, fcr=112, prim=1)` | 复用本仓既有 MIT 实现 |
| 4 路交织（reedsolomon.cpp:145） | `lrpt_fec.py:236-244` `_deinterleave_4()` | out[ii] = data[ii·4 + pos] |
| 每块可纠 16 字节 | `fec.py:154` 注释 + 测试实测 | t = nsym/2 = 16 |
| 锁帧判据 = 4 块全可纠 | `lrpt_fec.py:269-273` | 任一块 nerrors=-1 → 返回空数据（诚实空态） |

---

## 3. libcorrect 许可结论

**结论：不引入 libcorrect，直接复用本仓既有 `fec.ReedSolomon`（MIT）。**

理由：
1. 本仓 `mbdsdr_ai/fec.py` 已有纯 Python MIT 实现的 RS(255,223)（Berlekamp-Massey + Chien + Forney），覆盖 CCSDS 参数（fcr=112, prim=1, ccsds_invert=True）。
2. libcorrect 是 MIT 许可（公开可查），但引入外部依赖对原型阶段不必要——纯 Python 已足够闭环。
3. 若后续性能需要，再评估切换到 libcorrect（C 加速）或保持纯 Python。

---

## 4. 测试结论（pytest 12/12 通过）

```
12 passed, 1 warning in 6.26s
```

| 测试类 | 用例数 | 结论 |
|--------|-------|------|
| TestViterbi | 4 | 多项式常量正确；无噪 round-trip 完美；20 bit 翻转全纠；确定性可复现 |
| TestCaduDecode | 5 | 全链无噪 round-trip；ASM 正确恢复；100 翻转全链恢复；RS 16 字节上限实测；17 字节不可纠诚实返回 -1 |
| TestHonestEmpty | 3 | 过短输入报错；不可纠返回空数据 |

### 纠错能力实测

| 编码位翻转数 | Viterbi 后剩余错误 | RS 纠错 | 结果 |
|-------------|-------------------|---------|------|
| 0 | 0 字节 | 0/块 | 完美 |
| 100 | ~0 字节 | 0/块 | 完美 |
| 500 | 1 字节（1 块） | 1/块 | 完美 |
| 1000 | ~12 字节 | 12/块 | 完美 |
| 2000 | >16 字节（2 块） | 2 块失败 | 不可恢复 |
| ≥4000 | 大量错误 | 部分块失败 | 不可恢复 |

**门限**：Viterbi + RS 联合可纠约 1000/16396 ≈ 6% 编码位错误率（≈ SNR 3-5 dB 量级）。

---

## 5. 诚实边界

1. **硬判决 Viterbi（±1.0）**：本轮用硬判决做软输入（±1.0），未利用第①轮解调器的幅度信息。软判决（3-bit 量化）可获 ~2 dB 增益，留第③步。
2. **无 RRC 脉冲成形**：合成端用矩形脉冲，与真实 LRPT 的 RRC α=0.6 有差距；这影响频偏/时偏容忍度，不影响 FEC 正确性验证。
3. **Viterbi 回溯深度 96**：默认 96 符号（≈15×K），对短帧足够；长帧可加深。
4. **RS 对偶基**：本仓 `fec.py` 用 `ccsds_invert=True`（字节级 ^=0xFF）等效 CCSDS 对偶基变换，与 SatDump 机制一致但实现路径不同。
5. **纯噪声空态**：全噪声 CADU 不一定所有块都失败（概率事件），但只要有一块失败就返回空数据——诚实不假装成功。

---

## 6. 红线与纪律

- **干净室**：lrpt_fec.py 自写 MIT，未复制 SatDump/goestools 代码；file:line 仅作机制证据。
- **复用既有 MIT**：RS + 解扰直接用本仓 `fec.py`（已 MIT），不引入 GPL。
- **不预置呼号**：占位 payload 用 PRNG。
- **不发射**：纯合成闭环。
- **不 git add/commit/push**：仅写文件。
- **隔离文件勿动**：未碰 `cpp/*`、`ui_diag_freeze.cpp`、`cpp/scratch/*`。
- **无 cpp 改动** → 双构型基线 D98/R98 139/139 保持有效（如实标注）。

---

## 7. 第③步衔接

第③步目标：**端到端 IQ 闭环（第①轮解调 → 第②轮 Viterbi/RS）+ 软判决升级**。

衔接点：
1. **解调器输出软 I/Q**：第①轮 `LrptDemodulator` 输出硬判决 bits；第③步改为输出符号级软值（|I|, |Q|），喂 Viterbi 做 3-bit 量化软判决。
2. **帧同步 → Viterbi 流水线**：同步定位后，从同步位置取 N 个符号 → 软 I/Q → Viterbi → 解扰 → RS → CADU。
3. **端到端 IQ 测试**：`LrptModulator.modulate()` → 注入频偏/时偏/AWGN → `LrptDemodulator.process()` 升级返回软符号 → `LrptCaduDecoder.decode_viterbi_to_bytes()` → 校验 payload。
4. **BER vs Eb/N0 曲线**：接入 `experiments/common/runner.py` FixedSeed + Wilson CI，画 Viterbi 后 BER 随 SNR 变化。
5. **三通道工具**：`set_lrpt` / `get_lrpt_status`（落地路径 §2.3），诚实空态。
