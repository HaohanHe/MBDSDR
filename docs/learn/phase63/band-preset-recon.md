# SDR++ 机制侦察（第⑥批）：候选缺口现状

宁缺毋滥。先逐条 grep 确认现状，再选证据最硬的 1 个落地。

## 候选现状（file:line）

| 候选 | 现状 | 证据 |
|---|---|---|
| a) FFT 尺寸切换残留 | **已存在** | main_window.cpp:2562 `fftSizeRequested→setFftSize`；:4495/4934/5000 持久化+下发；engine 自己 clamp 1024..16M（:4115） |
| b) 频段预设表（频率+模式+带宽一键） | **缺口** | 只有带宽预设 bwCombo（main_window.cpp:149 kBwComboPresetsHz），无「频段→频率+模式+带宽」一键表；无 bandCombo/kBandTable |
| c) AGC 手动模式 UI | **已存在** | main_window.cpp:348-351 RTL/Tuner AGC 复选；:575 gainSlider_ + :431 onSetGain；engine onSetGain/setRtlAgc/setTunerAgc（spectrum_engine.h:213,397-398） |
| d) WFM 立体声解码 | **已存在** | dsp/wfm_stereo.cpp（19kHz pilot PLL :43-62）+ rds_decoder.cpp |

→ 唯一硬缺口 = **b)**。

## 落地：频段预设表（header-only 纯函数）

- `cpp/src/dsp/band_preset.h`：公开频率分配表（FM广播 87.5-108M/WFM、航空 118-136M/AM、
  中波 531-1602k/AM、20m/2m/70cm 业余段），每项 name+[low,high]+mode+bw；
  `bandPresetForFrequency(hz)` 命中返回预设，越界返回 nullopt（诚实不猜默认）。
  干净室：频率分配是公开 ITU/IARU band plan，非抄 SDR++ 代码。
- `cpp/tests/test_band_preset.cpp`：6 用例（表完整性/命中/越界 miss/边界 inclusive），
  QtTest，确定性，离线 moc 跑通 6 passed。

## 诚实边界

- 本轮只落「纯数据 + 查表」这一可测内核；UI 频段下拉一键跳转（选中→onSetCenterFreq+
  setDemodMode+带宽）未加——属 main_window 控件布局，为避免越界/并行冲突留待下批接线。
- 新测试目标 test_band_preset 待 CMake 统一加入（回报用户）。
