# SDR++/GNU Radio 信道化与解调移植 — 差距审计与修正报告

## 1. 差距审计表（上游 file:line vs 我们 file:line vs 状态）

| 模式/模块 | 上游实现（sdrpp） | 原实现（mbdsdr_ai） | 状态 | 修正后 |
|---|---|---|---|---|
| **信道化 Xlating FIR** | `core/src/dsp/channel/rx_vfo.h:89-100,119-120`：NCO混频→有理抽取→FIR低通(截止=BW/2,Nuttall窗)；`frequency_xlator.h:43-48` 相位累加NCO | `analog_demod.py:354-358,415-427`：NCO混频(有连续相位)→Hanning LPF，**无抽取** | ⚠️ 部分 | **新建 channelizer.py**：NCO→4x抽取→Nuttall窗LPF |
| **AM 包络检波** | `core/dsp/demod/am.h:109,110,116`：abs(IQ)→DCBlock→LPF(BW/2)；IF=15k `am.h:76` | `analog_demod.py:431-433`：abs(x)-mean | ✅ 基本对 | **新建 demod_am.py**：abs→样本级DCBlocker→LPF |
| **USB/LSB** | `core/dsp/demod/ssb.h:79,82,106-116`：xlator(USB=+BW/2,LSB=-BW/2)→取实部；`usb.h:34`/`lsb.h:33` | `analog_demod.py:443-448,123-124`：BFO=±1500(硬编码非BW/2)，**每块相位重置** | ⚠️ 真区分但有bug | **新建 demod_ssb.py**：运行相位累加NCO，USB=+BW/2、LSB=-BW/2 |
| **NFM 鉴频** | `core/dsp/demod/quadrature.h:40-44`：wrap(∠z[n]-∠z[n-1])/dev；`fm.h:30` dev=BW/2；去加重 `deephasis.h:60-93` | `analog_demod.py:434-442`：相位差分(对)，去加重(对) | ✅ 对 | **新建 demod_nfm.py**：相邻样本差鉴频+RC IIR去加重 |
| **WFM 立体声** | `core/dsp/demod/broadcast_fm.h:146-190`：鉴频→pilot带通18.75-19.25k→PLL→共轭×2再生38k→L=(M+S),R=(M-S)；群延迟补偿`lprDelay:47-48` | `wfm_stereo_lite.py:106-111` 真立体声；但主链 `analog_demod.py:434` 走mono分支 | ⚠️ 割裂 | **新建 demod_wfm.py**：真立体声+群延迟补偿 |
| **CW BFO 差拍** | `core/dsp/demod/cw.h:58-60`：xlator(tone)→取实部；tone=800 `cw.h:107` | `analog_demod.py:443-448`：BFO=700乘积检波(对)，相位重置 | ⚠️ 对但相位bug | **新建 demod_cw.py**：运行相位累加BFO |
| **AGC** | `core/dsp/loop/agc.h:82-107` attack/decay包络；GQRX CAgc滑动峰值窗 | `gqrx_receiver.py:178-247` GqrxAGC：真attack/decay+hang+knee+15ms延迟线 | ✅ 真 | 直接复用 |
| **静噪顺序** | gqrx `nbrx.cpp:77-79`：filter→sql→agc→demod | `analog_demod.py:360-381`：滤波→静噪→AGC→解调 | ✅ 对 | 直接复用 |

## 2. 关键算法修正（上游 file:line → 新文件 file:line）

- **信道化**：`rx_vfo.h:89-100` → `channelizer.py:XlatingFIR.process`
- **AM**：`am.h:109,116` → `demod_am.py:DemodAM.process`
- **SSB**：`ssb.h:106-116` → `demod_ssb.py:DemodSSB.__init__`（translation ±BW/2）
- **NFM**：`quadrature.h:40-44` → `demod_nfm.py:DemodNFM.process`（相邻样本差）
- **WFM**：`broadcast_fm.h:146-190` → `demod_wfm.py:DemodWFM.process`（pilot→38k再生→L/R矩阵+群延迟补偿）
- **CW**：`cw.h:58-60` → `demod_cw.py:DemodCW.process`
- **去加重**：`deephasis.h:60-93` → `demod_nfm.py:DeemphasisIIR`

## 3. USB/LSB 真区分证明

上游 `ssb.h:106-116`：USB translation=+BW/2，LSB translation=-BW/2（相反方向）。
新实现 `demod_ssb.py`：`DemodSSB('usb').translation == +1400`，`DemodSSB('lsb').translation == -1400`。

测试 `test_demod_usb_vs_lsb_isolation`：
- USB信号（f=-400Hz）→ USB解调@1kHz=57dB，LSB解调@1kHz=-49dB（隔离 **106dB**，要求>20dB）
- LSB信号（f=+400Hz）→ 对称，同样 106dB 隔离
- 不是改标签：两模式喂同信号，错模式把音频搬到带宽外被LPF滤除

## 4. WFM 真立体声证明

`test_demod_wfm_stereo_separation`：注入 L=1kHz, R=2kHz, pilot 19kHz。
- L声道@1kHz=68.7dB，@2kHz=-21.8dB（分离 ~90dB）
- R声道@2kHz=68.7dB，@1kHz=-21.8dB
- 流程：pilot带通→Hilbert相位→cos(2θ)再生38k→mpx·cos(2θ)取差信号→L=(M+D),R=(M-D)

## 5. 测试结果

- 新增 13 个确定性单测：`tests/test_dsp_demod_channelizer.py`(4)、`test_dsp_demod_modes.py`(7)、`test_dsp_agc.py`(2) — **全绿**
- 全量 pytest：**840 passed, 0 failed**（原有 774 + 新增 13 + 子测试），5m13s，零回归
- 红线遵守：只改 `mbdsdr_ai/` 内核与 `tests/`，未碰 desktop/mobile UI；合成信号仅用于单测
