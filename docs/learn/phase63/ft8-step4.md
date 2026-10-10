# FT8 第④步落地：C++ 解码层移植 + 读回接线

HEAD=21cf42d。干净室自写 MIT，规范为 Python `mbdsdr_ai/ft8_codec.py`。

## 1. 解码层 file:line

| 组件 | file:line |
|---|---|
| Ft8Decoded / Ft8Codec 接口 | `cpp/src/dsp/ft8_codec.h:14`/`:22` |
| H 矩阵 kMN[174][3]（公开协议结构，自写加载） | `cpp/src/dsp/ft8_codec.cc:14` |
| CRC14 poly 0x6757（重算+比较，补零位） | `ft8_codec.cc:69` checkCrc14 |
| tanh BP（log-domain，per-edge r 消息，CRC14 早停，give-up → nullopt） | `ft8_codec.cc:85` decode |
| 77-bit unpack（i5bit=1 标准消息，call/grid/report） | `ft8_codec.cc:48` unpack28 + decode 内 field |

**BP 选择**：log-domain tanh BP（与 Python 原型一致），maxIter=50，
give-up 准则 ncnt>=5 && it>=10 && ncheck>15。正 LLR=bit0，硬判决 cw=(llr+tov<0)。

## 2. 确定性 round-trip 结论

`cpp/tests/test_ft8_codec.cpp`（6 passed，LLR 烤入 `ft8_known_llr.txt`）：
- crc14KnownVector：已知 91 bit（K1ABC/K2DEF/EM12）CRC 通过；
- decodesKnownCodeword：Python 固定 seed 生成 ±5 LLR → C++ decode →
  from="K1ABC" to="K2DEF" exchange="EM12" report=false（逐字段精确匹配）；
- noiseLlrHonestEmpty：全零 LLR → nullopt（诚实空态）；
- corruptedCodewordFails：翻转前 20 bit → 不恢复原帧（字段不符或 nullopt）。

## 3. 引擎/读回接线

- `spectrum_engine.h:125` ft8DecodedText() 访问器；
- `spectrum_engine.cpp:302` ft8DecodedText() 返回空串（诚实空态：detector→LLR→codec
  运行时接线未启用前不编造帧文本）；
- `agent_tools.cpp:970` get_ft8_status 新增 `decoded_text` 字段（空串）。
- **金集 92 保持**：test_tool_registry 8、test_agent 37、test_control_hub 32、
  test_control_http 全绿（0 failed），无新工具，mobile catalog 53 不变。

## 4. 真实喂数接入点/限制（诚实声明）

- 当前检测层由测试直接注入 12kS/s 复基带；vfo_manager→Ft8Detector 的运行时
  窄带抽取链未接（IF 率/带宽约束下检测窗口完整性待验）。
- detector 输出 Ft8Candidate（频偏/时偏/syncQuality/candidate_count）；
  8-tone 能量→LLR 提取→codec.decode 的运行时链路**本轮未启用**（ft8DecodedText
  返回空串即诚实标注）。Python 原型 `llrs_from_tone_energies`（ft8_codec.py:335）
  已就绪，作下一步接线基准。

## 5. 快照/UI

本轮未做 UI FT8 开关/徽标（弹性 token 用尽）；快照 MBD_FT8 未拍。
tokens.h 键 kSettingsKeyFt8... 留下一步。

## 6. 诚实未完成项

- detector→LLR→codec 运行时接线（ft8DecodedText 非空化）；
- vfo_manager 真实 12k 窄带喂数；
- UI 开关 + 状态徽标（镜像 ctcss badge）；
- 时隙对齐（15s 多帧窗）、SIC；
- 与 wsjtx 真实弱信号链路不直接类比（本轮为合成闭环）。
