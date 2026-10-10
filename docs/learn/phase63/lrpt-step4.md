# LRPT 线 A 第④轮：C++ FEC 解码链移植 + 引擎真实驱动

HEAD=28a7fbb。干净室自写 MIT，规范 `mbdsdr_ai/lrpt_fec.py` + `fec.py`。

## 1. 落地 file:line

| 组件 | file:line |
|---|---|
| Viterbi K7 r1/2 {79,109} 软判决译码 | `cpp/src/dsp/lrpt_fec.cc:60` |
| CCSDS 字节解扰（PN 周期 255） | `cpp/src/dsp/lrpt_fec.cc:99` |
| RS(255,223) fcr=112 prim=1 ccsds_invert（BM+Chien+Forney） | `cpp/src/dsp/lrpt_fec.cc:107` |
| CADU 解码（跳 4B ASM→解扰→4 路解交织→RS×4） | `cpp/src/dsp/lrpt_fec.cc:205` |
| 引擎真实驱动 feedLrptCadu | `cpp/src/dsp/spectrum_engine.cpp:302` |
| lrptSyncLocked/lrptDecodedFrames 读回 | `cpp/src/dsp/spectrum_engine.h:122` |

## 2. 跨语言 round-trip 结论（test_lrpt_cpp 6 passed）

Python 基准（seed=123 payload → RS 编码 → 4 路交织 → CCSDS 加扰 → ASM）生成
确定性 1024B CADU；C++ `lrptDecodeCadu` 解出 **892B payload 与 Python 基准
逐字节一致**（first8 `cd e9 f3 03 99 9c ae ae`，match=1，`QCOMPARE(*d, exp)`）。

## 3. 引擎真实读数

`feedLrptCadu`：启用后合法 CADU → `lrptSyncLocked()=true`、`lrptDecodedFrames()=1`；
未启用 / RS 不可纠 → 诚实空态（false/0，绝不编造）。

## 4. ctest

`lrpt_cpp` 新槽（6 passed）。金集 94 不动（无新工具）；mobile catalog 53 不变。

## 5. 诚实未完成项

- **QPSK 解调前段未移植**：本轮只移植 FEC 后段（Viterbi 字节流→payload），
  未做 QPSK 4 次 CFO + 定时 + 硬判决、64-bit ASM 帧同步搜索（IQ→CADU 字节流）。
  当前跨语言钉扎是 **CADU 字节流→payload**，非 IQ→payload 全链。
- 采样率映射（72k/12k 域）与真实 vfo 喂数接入点未做（feed 入口已留）。
