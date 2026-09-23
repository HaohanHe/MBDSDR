# 50 — 集成测试审查 Part 2

**审查范围**
- `tests/test_full_integration.py` 第 701–1421 行（自 `test_code_editor_module` 尾部起，含 astronomy / ADS-B / SSTV / AMR / self_evolution / plugin / context&model / memory / FT8 / tool_callability / SSTV-Robot72 / RDS-CT / AX.25 / APRS / HDLC / KISS / AFSK 及 `main()`）
- `tests/test_full_integration_v2.py` 全文（907 行）

**方法**：逐行读真实测试代码 + 对照被测源码（`mbdsdr_ai/astronomy.py`、`amr.py`、`memory.py`、`rds_lite.py`、`self_evolution.py`、`plugin_system.py`、`adsb.py`、`ax25.py`）+ 运行时实测 AMR 特征维度。只读审查，未改代码。

---

## 0. 一句话结论

V1 尾部这一段是**全仓库质量最高的一段测试**（SSTV 跨实现往返 + 真实录音、ADS-B CRC 权威向量、AFSK 真音频往返都在这）；但它同时藏着一个**假绿报告**（V2 已与源码脱节、按现在源码跑必然红）和一个**纯手抄位运算、从不 import 被测模块的 RDS 空壳测试**。V2 不是 V1 的增强版，而是 V1 的**缩水子集**——它把 V1 里所有真正有鉴别力的往返测试全删了，且自己还带了两个会红的断言。

---

## 1. 总览统计

| 指标 | V1 (`test_full_integration.py`) | V2 (`test_full_integration_v2.py`) |
|---|---|---|
| 文件行数 | 1421 | 907 |
| `result.record(...)` 总条数 | 216 | 157 |
| 本审查段（V1 L701–1421）record 条数 | **119** | — |
| 报告宣称通过 / 总数 | 228 / 228 (100%) | 185 / 185 (100%) |
| 报告宣称耗时 | **129.37 s** | **0.46 s** |
| 报告新鲜度 | `tests/test_report.txt` | `tests/test_report_v2.txt` —— **陈旧，与当前源码不符** |
| `record(..., True)` 字面真断言（本范围） | 4 处（L1134, L1238 + L395/L431 在范围外） | 5 处（L367, L504, L507, L510, L840） |
| `is not None` / `isinstance` 弱断言（本范围） | 13 处（V1） / 26 处（V2） | — |

> 注：V1 报告 228 条 ≈ 全文件 216 条 record + 模块导入等；V2 报告 185 条。两报告均为历史产物，**未在当前源码上重跑验证**。

---

## 2. [真 bug] 实测可复现问题

### B1. V2 的 AMR 特征维度断言与源码脱节，按当前代码必然失败
`tests/test_full_integration_v2.py:693`
```python
result.record("特征维度 = 24", stats["n_features"] == 24)
```
`tests/test_full_integration_v2.py:702`
```python
result.record("特征提取（24维）", len(feature.to_list()) == 24)
```
**对照源码** `mbdsdr_ai/amr.py:292` `get_stats()` 硬编码 `"n_features": 25`；`AMRFeature.to_list()`（`amr.py:86-98`）实返 **25** 个字段（8 时域 + 9 频域 + 8 统计，含 `spectral_magnitude_cv`）。

**运行时实测**：
```
n_features from get_stats = 25
len(to_list()) = 25
```
→ V2 这两条断言现在跑必然 `✗`。`tests/test_report_v2.txt` 里的 185/185 全绿是**旧快照**（特征数还是 24 维时留下的），后来源码加了第 25 维特征，V2 没跟着改。**这是"实验室绿"假象的直接证据。**

对比 V1 对应断言 `test_full_integration.py:954` `stats["n_features"] == 25` —— V1 是对的。

### B2. V2 误用 `MemoryStore.add` 签名，把"内容"当成了"分类"
`tests/test_full_integration_v2.py:839`
```python
mem.add("测试记忆", "这是测试内容", metadata={"type": "test"})
```
**对照源码** `mbdsdr_ai/memory.py:92-99` 签名为
`add(content: str, category: str = "general", importance=0.5, tags=None, metadata=None)`。
第二位置参数是 `category`，不是 title。于是 V2 实际写入的是：`content="测试记忆"`, `category="这是测试内容"`。测试作者注释（L838）写"方法是 add 不是 write"，说明他以为签名是 `add(title, content)`。

测试之所以还能"绿"，是因为 L842 `mem.search("测试", limit=5)` 的子串匹配刚好命中 `content="测试记忆"`——**断言通过纯属巧合**，测试意图（存取"这是测试内容"）实际是错的。

V1 对应调用 `test_full_integration.py:1133` `mem.add("这是测试内容", category="general", metadata=...)` 是正确的。

### B3.（待真机确认）`test_tool_callability` 对所有必填字符串参数塞 `"README.md"`，有覆盖仓库文件风险
`tests/test_full_integration.py:1187-1191`
```python
args = {r: 1.0 if ... number else (1 if ... integer else (
        True if ... boolean else ([] if ... array else "README.md")))
        for r in (defn.get("required") or []) if r in props}
res = tr.call(name, args)
```
对 100+ 个工具 dry-call，凡 required 的 string/object 参数一律喂 `"README.md"`。若其中任何一个文件写入类工具按相对路径落盘，CWD 是仓库根目录，就会写 `./README.md`（该文件当前 0 字节，见 `ls`）。这是**测试副作用 / 隔离缺陷**，不是必然触发的崩溃，但属于"跑测试可能改仓库"的隐患。object 类型参数被塞进字符串也必然抛 TypeError，靠 L1196 兜住。

---

## 3. [空壳] 凑数 / 弱断言清单

### E1. `test_rds_ct_mjd` —— 从不 import 被测模块，整段是测试自己手抄的位运算
`tests/test_full_integration.py:1228-1241`
```python
def test_rds_ct_mjd(result: TestResult):
    """RDS CT 时钟：MJD 编解码往返 + 锚点日期（MJD 60000=2023-02-25）。"""
    from datetime import datetime, timedelta
    for mjd, hh, mm in [(60000,13,45), ...]:
        c = (1 << 15) | ((mjd >> 2) << 1)            # ← 测试自己的"编码器"
        d = (hh << 11) | (mm << 6) | ((mjd & 0x3) << 4) | 0
        dec_mjd = (((c & 0x7FFF) >> 1) << 2) | ((d >> 4) & 0x3)  # ← 测试自己的"解码器"
        assert dec_mjd == mjd
    dt = datetime(1858,11,17) + timedelta(days=60000)
    result.record("RDS CT MJD 往返", True)
    result.record("MJD 60000 = 2023-02-25", (dt.year,...)==(2023,2,25), ...)
```
- 全文件 grep `rds_lite` 仅命中 L1231 **一行注释**（"与 _parse_group 一致"），**没有任何 `import mbdsdr_ai.rds_lite` 或调用 `_parse_group` / `decode_rds`**。
- 测试里的"解码器"表达式与源码 `mbdsdr_ai/rds_lite.py:209` `mjd = (((c & 0x7FFF) >> 1) << 2) | ((d >> 4) & 0x3)` **逐字符相同**——是从源码手抄过来的副本。
- "MJD 60000=2023-02-25" 用的是测试自己写的 `datetime(1858,11,17)+timedelta`，与源码 `rds_lite.py:308` 同一算式的手抄副本。
- **后果**：若真把 `_parse_group` 的位序写反、或 MJD 低 2 位移错位置，这个测试照样绿。它证明的是"测试作者的逆运算自洽"，不是"RDS 解码器正确"。这是典型的**凑数往返（编解码犯同样错误也发现不了）**，且连"共错"都谈不上——它根本没调用解码器。
- 归类：**[空壳]**。建议改为：构造一组真实 RDS group bits 喂 `_parse_group`，断言 `out["ct_mjd"]/ct_hour/ct_minute`。

### E2. `result.record("写入记忆", True)` —— 字面真断言
- V1 `test_full_integration.py:1134`：`mem.add(...)` 不抛异常就 `record("写入记忆", True)`，从不验证写进去了。
- V2 `test_full_integration_v2.py:840`：同样字面 `True`。
- 归类：**[空壳]**。应断言 `len(mem.search("测试"))>=1` 或 `mem.get_stats()["total"]>=1`。

### E3. V2 调度器 / 子代理的"禁用/启用/删除/销毁"全是字面 True
`test_full_integration_v2.py:504,507,510,367`
```python
scheduler.disable_task(task_id);  result.record("禁用任务", True)
scheduler.enable_task(task_id);  result.record("启用任务", True)
scheduler.remove_task(task_id);   result.record("删除任务", True)
sm.destroy(sub_id);              result.record("销毁子代理", True)
```
调用后不检查任务是否真被删、状态是否真翻转。若 `disable_task` 是空函数也照样绿。归类：**[空壳]**。应 `assert len(scheduler.list_tasks())==0` 之类。

### E4. astronomy 赤道→地平只断言 `is not None`，且源码自带方位角方向 TODO
- V1 `test_full_integration.py:765-766` / V2 `test_full_integration_v2.py:658-659`：
  ```python
  result.record("赤道→地平", altaz.alt_deg is not None and altaz.az_deg is not None)
  result.record("大气质量计算", altaz.airmass is not None)
  ```
- **对照源码** `mbdsdr_ai/astronomy.py:290` 留有未决注释：
  ```python
  # 方位角：东为正（时角为正表示在西方？需要检查）
  ```
  源码作者自己都不确定方位角约定方向，而测试只查 `not None`——**方位角符号/方向写反也测不出来**。这正是任务里点名的"astronomy 只断言 `altaz.alt_deg is not None`、无已知恒星 benchmark"问题，且比已知更糟：源码处有未决方向歧义。
- GMST/LST 也只查 `0 <= x <= 2π`（V1 L757-759），范围框宽松到差几小时都过。
- 归类：**[弱断言/占位]**。建议补已知恒星锚点：例如固定 JD（如 J2000.0）下某亮星的 alt/az 理论值，或 `to_altaz` 后再 `to_equatorial` 往返容差。

### E5. AMR 分类只断言"预测值非 None"，从不断言"预测成了 FM"
- V1 `test_full_integration.py:971` / V2 `test_full_integration_v2.py:707`：
  ```python
  result.record("AMR 分类", result_data.predicted_modulation is not None)
  ```
  喂的是合成 FM 信号，但断言从不检查 `predicted_modulation == ModulationType.FM`。分类器把 FM 判成 NOISE 也绿。这是"跑通了"而非"分类对了"。归类：**[弱断言]**。

### E6. self_evolution 状态机 happy-path，"确认/应用"为构造性真
- V1 `test_full_integration.py:1004-1024` / V2 `test_full_integration_v2.py:735-750`：
  `propose→validate→evaluate→confirm→commit→apply` 一路走通，断言多为 `is not None`。
- **对照源码** `self_evolution.py:281-286`：`confirm(id, True)` 只要 proposal 存在就返回 True；`apply`（L339-342）只要 status∈{confirmed,committed} 就置 applied。测试里 `apply` 的 `target_type="config"` 不进 L346 的真实落盘分支，**没有任何可观测的持久化效果被断言**。归类：**[弱断言]**（状态机冒烟，非行为验证）。

### E7. plugin_system 加载空插件，`load_plugin` 恒返非 None
- V1 `test_full_integration.py:1074-1083` / V2 `test_full_integration_v2.py:787-794`：假插件 `__init__.py` 写 `def register(registry): pass`。
- **对照源码** `plugin_system.py:192-229`：`load_plugin` **从不调用 `register`**（注册发生在 `enable_plugin`，L244-249，且用 `register(tool_registry=..., hook_manager=..., subagent_manager=...)` 关键字调用）。测试只调 `load_plugin` 并断言 `loaded is not None`——而 `load_plugin` 在模块加载失败时也照样返回一个 `status=ERROR` 的 Plugin 对象（L220-226），**`is not None` 恒真**。
- 更隐蔽：假插件的 `register(registry)` 单参签名，若哪天有人补测 `enable_plugin`，会因源码按关键字传参而 TypeError。真正的 register/hook/subagent 接线路径**零覆盖**。
- 归类：**[空壳]**。

---

## 4. 测试真实性分级：哪些真验证了正确性

### A. 真·强测试（跨实现 / 权威向量 / 真实数据，值得保留）
1. **SSTV 自动识别 + Robot36/PD90 解码**（V1 `test_full_integration.py:840-939`）
   - 编码用**外部库 pysstv**（Robot36/MartinM1/PD90），解码用本仓库 `mbdsdr_ai.sstv_decoder`——跨实现往返，编码器与解码器不共享代码，共错概率低。
   - 断言解码后**三原色空间方向**（`dom(50)=R, dom(160)=G, dom(270)=B`，L881-883）——这是内容级断言，不是"行数非零"。
   - **真实 over-the-air 录音** `real_sstv.wav` 存在时跑（L903-909），断言判 grouped、解满 240 行——这是全仓库唯一接真实电波数据的测试，直接对冲"实验室绿真机红"。
   - 回归锁注释明确（L843：防退回"VIS 查不到静默回退 Martin M1"）。**本段最佳测试。**
2. **ADS-B CRC 权威向量**（V1 L818-821）：`8D406B902015A678D4D220AA4BDA` 是公开 Mode-S CRC 测试向量，断言 `mode_s_crc24(...) == 0`，独立于本仓库编解码实现。
3. **ADS-B 端到端帧**（V1 L805-816）：自建帧→调制→解调→断言 `icao=="780ABC"`、`callsign=="CCA123"`、`crc_ok`。虽编解码同模块，但有 ICAO/呼号内容断言 + 权威向量兜底，可信。
4. **AFSK 真音频往返**（V1 L1328-1347）：帧→`modem.modulate` 成音频→`modem.demodulate` 解回→断言 info 字段原文相等 + `fcs_valid`。走的是真音频波形，比纯字节往返强。
5. **KISS 转义往返**（V1 L1308-1325）：payload 含 FEND(0xC0)/FESC(0xDB) 边界字节，断言转义后解回原字节、端口号 2 一致。边界用例选得好。
6. **HDLC 位填充边界**（V1 L1286-1305）：用 `b"\xff\xff"`、`bytes(range(256))`、连续 5 个 1 的边界，往返断言。
7. **AX.25 / APRS 字段往返**（V1 L1244-1284）：呼号、info 字节、经纬度（容差 0.01°）内容级断言。

### B. 中等（同模块往返，有共错风险但有内容断言）
- FT8 8FSK 符号往返（V1 L1148-1173）：合成已知符号→解调→`>=0.85` 一致。但 L1171 用 `(b+d)%8` 吸收了 8 种相位旋转歧义——**若解调把音峰中心偏了一个 slot，测试仍按"旋转匹配"算对**，对中心频偏 bug 不敏感。建议收紧为 `d==0`。
- Robot72 结构往返（V1 L1202-1225）：手工拼标准时序轨迹→断言 success/行数/尺寸。是自构造自解码，属结构冒烟。
- Memory add→search（V1 L1133-1142）：内容级子串断言，但"写入"那条是字面 True。

### C. 凑数 / 空壳（见第 3 节）
RDS-CT（E1）、记忆"写入"字面真（E2）、调度器/子代理操作字面真（E3）、astronomy `not None`（E4）、AMR 分类 `not None`（E5）、self_evolution happy-path（E6）、plugin 空加载（E7）。

---

## 5. 覆盖缺口（本范围相关）

### 5.1 被测模块里"只导入不测行为"或"完全没测"
两版 `test_module_imports` 只 import 了 29 个模块（V1 L80-88 / V2 L69-77），而 `mbdsdr_ai/` 实际有 ~65 个 `.py`。下列模块**在两版测试里连 import 都没有**（即完全无覆盖）：

| 模块 | 说明 | 缺口 |
|---|---|---|
| `fst4_ldpc.py` | FST4 LDPC 译码 | 零覆盖（R2 其他审查项已点） |
| `cw_decoder.py` | CW 译码 | 零覆盖 |
| `noaa_apt_lite.py` / `meteor_sat.py` | 气象卫星 APT/Meteor | 零覆盖 |
| `gnss_monitor.py` | GNSS | 零覆盖 |
| `orbit.py` | 轨道 | 零覆盖（仅 `decoders.list_visible_satellites` 被 isinstance 冒烟） |
| `rds_lite.py` | RDS 解码 | **真实 `decode_rds`/`_parse_group` 零覆盖**（仅 E1 手抄位运算） |
| `wfm_stereo_lite.py` | 调频立体声 | 零覆盖 |
| `analog_demod.py` / `demod.py` | 模拟解调 | 仅 `dsp.fm/am/ssb/cw_demod` 冒烟 |
| `cfo.py` / `constellation.py` | 载波频偏 / 星座质量 | 零覆盖 |
| `digital_modes.py` | 数字模式分发 | 仅 ADS-B 一条 |
| `ft8_lite.py`（tone 检测外）/ `ft8_decode.py` / `ft8_ldpc.py` / `ft8_callsign.py` / `ft8_unpack.py` | FT8 完整链 | V1 只测了 `ft8_lite` 的音峰+8FSK 符号，**LDPC 译码、callsign 解包、报文校验全无** |
| `gimbal.py` / `hal.py` / `radio_control.py` / `time_sync.py` / `new_spacetime.py` | 云台/HAL/射频控制/时间同步 | 零覆盖 |
| `sweep.py` / `spectrum_sensing.py` / `signal_analysis.py` / `signal_quality.py` / `signal_spectrum.py` | 扫频/频谱感知/信号质量 | 零覆盖 |
| `skill_registry.py` / `openapi_integration.py` / `satdump_integration.py` / `frequency_manager.py` / `baseband_io.py` | 工具注册/外部集成 | 零覆盖 |
| `plugins/`（目录） | 真实插件 | 仅 E7 空插件冒烟 |

### 5.2 本范围内有行为测试但断言不到位的
astronomy（方位角方向/时间系统无锚点）、AMR（不验证分类类别）、self_evolution（不验证持久化）、plugin（不验证 enable/register 接线）、context/model（仅 `isinstance(list)`/`not None`）。

---

## 6. 测试隔离与状态泄漏

- **好的方面**：guardian / self_evolution / plugin / code_editor / memory 都用 `tempfile.TemporaryDirectory()` 隔离（V1 L993, L1055, L1128；V2 L431, L727, L775, L833），SSTV 临时 wav/png 末尾显式 `os.remove`（V1 L935-939）。
- **风险点**：
  1. 所有测试共享同一个 `agent`（`main()` 里构造一次，V1 L1362 / V2 L862）。`test_tool_callability`（V1 L1176-1199）对全部工具 dry-call，会改 `agent.sdr_manager` / `agent.frequency_manager` 等共享子系统状态；它排在最后，顺序敏感。
  2. **B3**：dry-call 给文件类工具喂 `"README.md"`，有写仓库根目录副作用。
  3. `real_sstv.wav` / `real_sstv_*.png` 是仓库内真实数据文件，V1 L903 按相对路径读——依赖 CWD 为仓库根，CI 换工作目录会静默跳过（`os.path.exists` 为 False 则整段不跑，**且不计失败**，掩盖覆盖缺失）。
  4. 无跨测试全局可变 fixture 污染（AMRClassifier、HookManager 等都在函数内新建）。

---

## 7. 速度

- 两文件均**无 `time.sleep`**（grep 仅命中 `sdr_demodulate` 的 `duration_s:0.1` 参数）。
- V1 报告 129 s：慢在 `test_sstv_auto_identification`——pysstv 编码 Robot36/MartinM1/PD90 三段 WAV（44.1kHz）+ `real_sstv.wav` 解码 + 逐行频率分析。属必要成本，但可考虑：MartinM1 只跑识别器不解整图（作者已自觉，L887 注释），可再缩小合成图尺寸或用 `pytest.mark.slow` 拆分。
- V2 报告 0.46 s：**异常地快**——因为 V2 删掉了所有 SSTV/数字模式往返测试（见第 8 节），只剩子系统冒烟。快但浅。且该 0.46 s 快照本身已过期（B1）。

---

## 8. V2 与 V1 的关系

**V2 是 V1 的严格子集（缩水版），不是并行的新版。**

V1 `main()` 调用 34 个测试函数（L1372-1405）；V2 `main()` 只调 23 个（L871-893）。V2 相对 V1 **删掉了以下 11 个测试函数**，其中恰好包含 V1 里**最有鉴别力**的那些：

| V1 独有、V2 删掉的测试 | 价值 |
|---|---|
| `test_digital_modes`（ADS-B 端到端 + CRC 权威向量，L795） | 强 |
| `test_sstv_auto_identification`（跨实现 + 真实录音，L840） | **最强** |
| `test_ft8_roundtrip`（L1148） | 中 |
| `test_tool_callability`（全工具不崩溃，L1176） | 冒烟 |
| `test_sstv_robot72_roundtrip`（L1202） | 中 |
| `test_rds_ct_mjd`（L1228） | 空壳（删了不可惜） |
| `test_ax25_roundtrip`（L1244） | 中强 |
| `test_aprs_position_roundtrip`（L1266） | 中 |
| `test_hdlc_bitstuff_roundtrip`（L1286） | 中强 |
| `test_kiss_roundtrip`（L1308） | 中强 |
| `test_afsk_modem_roundtrip`（L1328） | 强 |

- **V2 没有任何 V1 没有的新模块覆盖**。差异只是：V2 修了 V1 的若干 API 不匹配（注释里写明"方法是 create 不接受 name"、"Event 参数是 event_type 不是 type"、"ContextStats 是对象不是 dict"等），并在 `test_agent_init` 子系统清单里加了 `ar_projector`、去掉了 `sdr_manager`/`evolution`。
- **结论**：V2 是一次"为了跑绿而削足适履"的重写——它把跑不通的往返测试整个删掉，而不是修断言；同时自己引入了 B1（24 vs 25 维）和 B2（memory.add 签名）两个新错误。**现在仓库里同时维护两份 100% 绿的报告，但 V2 那份是假的。** 建议：要么废弃 V2、把 V2 的 API 修正回填进 V1；要么让 V2 直接复用 V1 的往返测试。

---

## 9. "实验室绿、真机红"风险点

1. **SSTV 是唯一接真实电波的测试**（V1 L903-909），且已证明有价值（真实录音判 grouped、合成判 per_line）。其余数字模式全是合成信号。
2. **ADS-B / FT8 / AX.25 / AFSK** 全部合成：无真实 1090MHz 捕获、无真实 FT8 音频、无真实 AFSK 音频。AFSK 调制解调同模块，真机频偏/多径下的鲁棒性无覆盖。
3. **AMR**：只喂理想 FM，不喂 AM/SSB/CW/FSK，也不验证分类类别——真机上的调制识别准确率零证据。
4. **astronomy**：用 `unix_to_jd()`（当前时刻）+ 无已知恒星锚点，源码方位角方向还挂着 TODO——真机指星会偏。
5. **真实数据文件按相对路径读**（`real_sstv.wav`），CWD 不对就静默跳过、不计失败——CI 里等于没测。

---

## 10. 优先级建议

| 优先级 | 项 | 动作 |
|---|---|---|
| P0 | B1 V2 AMR `==24` 断言（`test_full_integration_v2.py:693,702`） | 改回 `==25`，重跑 V2，删旧假报告 |
| P0 | E1 RDS-CT 空壳（`test_full_integration.py:1228-1241`） | 改为真喂 `_parse_group` bits，断言 ct_mjd/hour/minute |
| P0 | 废弃/合并 V2（第 8 节） | V2 不该作为独立"全绿"报告存在；把 API 修正回填 V1 |
| P1 | B2 V2 memory.add 误用（`test_full_integration_v2.py:839`） | 改为 `mem.add("这是测试内容", category="general", ...)` |
| P1 | E4 astronomy 方位角方向无锚点（源码 `astronomy.py:290` 有 TODO） | 固定 JD + 已知恒星 alt/az 基准值；先定方位角约定 |
| P1 | E5 AMR 不验证分类类别（`test_full_integration.py:971` / V2:707） | 断言 `predicted_modulation == ModulationType.FM` |
| P1 | E7 plugin 只 load 不 enable（`test_full_integration.py:1082`） | 补 `enable_plugin` + 断言工具/hook 真注册；修假插件 register 签名 |
| P2 | E2/E3 字面 True 断言（记忆写入、调度器禁用/启用/删除、子代理销毁） | 补状态翻转后的可观测断言 |
| P2 | B3 tool_callability 喂 `"README.md"`（`test_full_integration.py:1190`） | 改用临时目录 / 拒绝文件写入类工具 |
| P2 | FT8 8 重相位歧义（`test_full_integration.py:1171`） | 收紧为 `d==0` |
| P3 | 覆盖缺口（第 5.1 节） | fst4_ldpc / cw / noaa-meteor / gnss / orbit / rds 真解码 / wfm-stereo / cfo-constellation / ft8 LDPC-callsign |
| P3 | 真实数据文件相对路径 + 静默跳过 | 改绝对路径或跳过即标记 skipped 而非沉默 |

---

## 附：本范围弱断言 / 凑数测试速查表

**V1（L701-1421）**
- `test_full_integration.py:765,766` astronomy `not None`（E4）
- `test_full_integration.py:971` AMR 分类 `not None`（E5）
- `test_full_integration.py:1004,1012,1020,1034` self_evolution `not None`（E6）
- `test_full_integration.py:1083` plugin `loaded is not None`（E7）
- `test_full_integration.py:1104,1111,1117` context/model `not None`/isinstance
- `test_full_integration.py:1134` 记忆"写入"字面 `True`（E2）
- `test_full_integration.py:1228-1241` RDS-CT 手抄位运算空壳（E1）
- `test_full_integration.py:1171` FT8 8 重相位歧义容忍
- `test_full_integration.py:1190` tool_callability 喂 `"README.md"`（B3）

**V2（全文）**
- `test_full_integration_v2.py:367,504,507,510` 销毁/禁用/启用/删除字面 `True`（E3）
- `test_full_integration_v2.py:557,560,563` self_learning `not None`
- `test_full_integration_v2.py:693,702` AMR `==24` 错误断言（**B1，真 bug**）
- `test_full_integration_v2.py:839` memory.add 签名误用（**B2，真 bug**）
- `test_full_integration_v2.py:658,659,707,735,741,747,757,794,824` 同 V1 的 `not None` 弱断言
- `test_full_integration_v2.py:840` 记忆"写入"字面 `True`（E2）
