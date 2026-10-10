# FT8 收尾轮：运行时全链路接线（detector→LLR→decode）

HEAD=73382c5。干净室自写 MIT，规范为 Python `mbdsdr_ai/ft8_modem.py` +
`ft8_codec.py:335`。

## 1. 全链路接线 file:line

| 组件 | file:line |
|---|---|
| 检测器 LLR 提取（58 数据符号 × 8-tone 能量 → 174 LLR，逆格雷分组） | `cpp/src/dsp/ft8_detector.cc:103` |
| 逆格雷表 kInvGray={0,1,3,2,6,4,5,7}（镜像 np.argsort(gray_map)） | `ft8_detector.cc:104` |
| lastLlr174() 读回 | `ft8_detector.h:45` |
| 引擎 processFt8Window（检测→LLR→codec.decode→ft8Decoded_） | `spectrum_engine.cpp:306` |
| Ft8Codec 成员 + ft8Decoded_ 字符串 | `spectrum_engine.h:606` |
| ft8DecodedText() 读回 | `spectrum_engine.cpp:302` |

**LLR 等价实现对照**（Python `llrs_from_tone_energies` :335）：
- 58 数据符号（帧内 7..35 与 43..71）× 8 音能量；
- 逆格雷 inv[t]=承载 3-bit；bit=0 LSB → s*3+2，bit=2 MSB → s*3；
- LLR = log(p0/p1)，正=bit0 更可能；确定性无 RNG。

## 2. 确定性 e2e 结论（test_ft8_e2e，5 passed）

Python 固定 seed（42）合成真实编码帧（K1ABC/K2DEF/EM12，LDPC 编码码字，
0.3σ 噪声）落盘 `ft8_e2e_iq.raw`（180000 复样本）→ C++ processFt8Window：
- decodesSyntheticFrame：检出 + decoded_text == "K1ABC K2DEF EM12"（逐字段精确匹配）；
- pureNoiseHonestEmpty：零输入 → active=false、decoded_text 空（诚实空态）；
- disabledHonestEmpty：未启用 → 空态。

**关键修正**：初版逆格雷表误为 {0,1,3,2,6,7,5,4}，38 bit 错；对齐
np.argsort(gray_map)={0,1,3,2,6,4,5,7} 后 round-trip 通过。

## 3. UI 徽标/快照结论（降级说明）

本轮弹性 token 用尽，**未做 UI FT8 开关/徽标**（main_window checkbox +
ctcss badge 先例未接入）；MBD_FT8 快照未拍。tokens.h 键 kSettingsKeyFt8...
留下一步。诚实标注：当前 UI 面无 FT8 控件，仅工具/引擎层可读。

## 4. 金集 92 保持确认

无新工具；get_ft8_status 仅扩 `decoded_text` 字段（step4 已加）。
- test_tool_registry 8、test_agent 37、test_control_hub 32、test_control_http 全绿；
- mobile catalog 53 不变。

## 5. ctest 新槽

- ft8_e2e（新增）；ft8 三套件合计：detector 5 + codec 6 + e2e 5 = 16 全绿。

## 6. 诚实未完成项

- vfo_manager 真实 12k 窄带喂数（当前触发路径为测试注入 / processFt8Window 直调）；
- UI 开关 + 状态徽标 + 快照；
- 时隙对齐（15s 多帧窗）、SIC；
- 与 wsjtx 真实弱信号链路不直接类比（本轮为合成闭环，固定 seed 噪声）。
