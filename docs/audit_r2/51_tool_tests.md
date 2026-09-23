# 51. 工具测试文件审查（第二轮深度审查）

> 审查范围：`tests/tool_selftest.py`、`tests/tool_health_check.py`、`tests/tool_param_smoke.py`、`tests/smoke_live_tools.py`、`tests/test_infra_units.py`、`tests/ft8_roundtrip.py`
> 审查方式：只读，逐行阅读真实代码 + 实际运行 + 对照 `mbdsdr_ai/sdr_tools.py` / `sdr_backend.py` / `sandbox.py` / `radio_control.py` 实现交叉验证。

---

## 0. 结论速览

| 维度 | 结论 |
|---|---|
| 工具总数（实测） | **250 个**已注册工具（`register_sdr_tools` 注册 126 个 + Agent 内置 124 个） |
| 被"调用过"的工具 | 250/250（selftest + health_check 全量无参/最小参调用） |
| 被"正确性验证"的工具 | **≈ 0 个**（无任何一个注册工具的输出被断言为"期望值"） |
| 真正有断言质量的测试 | 仅 1 处：`smoke_live_tools.py` 的 AX.25 模块级往返（**不走工具注册表**） |
| CI 门禁效力 | selftest 与 smoke_live_tools 有非零退出码；health_check / param_smoke **恒 return 0** |
| 真机 vs 实验室 | 全部 6 个测试在 mock/合成数据下通过；**无任何真实硬件/真实空口信号断言** |

**一句话结论：这 6 个文件是"工具存在性/不崩溃体检"，不是"工具正确性测试"。** 它们能抓住"工具一调就 traceback"，但抓不住"工具返回了看似正常、实则算错/空壳/未装依赖的结果"——而后者恰恰是 SDR 工具最常见的失败模式。

---

## 1. 工具自测 `tool_selftest.py`（123 行）

### 1.1 它做了什么
遍历全部 250 个工具，按 JSON Schema 只填 required 参数（`_min_args`，L62-71），用 `_safe_default` 给泛化值（L38-59），调用后按文本分类 ok/friendly/crash。

### 1.2 审查发现

- **[真bug] 只测"不崩"，不测对错。** L99-105：判定逻辑只有 `res.success` / 文本是否含友好提示词 / 是否含 traceback。没有任何一处把返回值与"期望输出"比对。一个工具把 100 MHz 算成 1 GHz、把 FCS 校验算反，只要 `success=True` 就进 ok。
- **[真bug] FRIENDLY_HINTS 过度宽泛，会把正常报错误判为"友好"。** L23-29 的提示词含 `"需要"`、`"已设置"`、`"已切换"`、`"可选"`、`"不是一个"`、`"未知的"`、`"没有"`、`"不能为空"`、`"必须"`。这些词在合法的业务校验报错里高频出现。例如某工具抛 `ValueError("frequency_hz 必须在设备范围内")`，因含"必须"被判为 friendly（非崩溃），从而漏掉一个本应暴露的参数边界 bug。
- **[建议] `_safe_default` 给的值对射频工具是物理上无意义的。** L50-51 给所有 freq 类参数填 `100_000_000.0`（100 MHz），gain 填 20，sample_rate 填 2.4 MHz。这些值从未被断言"设置成功后读回一致"——`sdr_set_frequency` 在 mock 后端下永远返回 True，无法发现"写进去没生效"。
- **[占位] 没有边界值/异常路径测试。** 只喂 required，不喂越界（freq<0、gain 超过 max_gain、空数组、错误枚举值）。越界路径在真实硬件上最容易崩，这里完全不覆盖。
- 正向：L119 退出码在 crash 时非零，可作 CI 门禁——这是 6 个文件里少数真能拦住"崩溃"的机制。

---

## 2. 健康检查 `tool_health_check.py`（82 行）

### 2.1 它做了什么
L57 对每个工具**无参调用** `reg.call(name, {})`，按文本分到 OK/NEED_ARG/NEED_HW/TODO/CRASH/OTHER。

### 2.2 审查发现

- **[空壳] 这是"注册检查"不是"健康检查"。** L55 注释自承："尝试无参调用；缺参也算健康"。无参调用下，绝大多数工具必然返回"缺少必填参数"，于是落进 NEED_ARG。脚本 docstring（`tool_param_smoke.py` L6）也承认："tool_health_check.py 只做无参调用，102 个工具停在 NEED_ARG"。即它对 ~102 个工具根本没触达业务逻辑。
- **[真bug] classify 靠脆弱的中文/英文子串匹配。** L26、L30-37：靠 `"未连接"`/`"not implemented"`/`"todo"` 等子串分类。一个正常解码结果正文里若出现"未连接"字样（极少见但可能）会误判；反之，一个真崩溃但异常消息是英文短串不含 "traceback" 也会漏判。
- **[真bug] L40 `if res.success and txt.strip(): return "OK"`** ——只要 success=True 且文本非空就算 OK，不管内容是真数据还是一句"已连接 mock 设备"的占位回执。
- **[真bug] L78 `return 0`** ——无论 CRASH 桶里有多少工具，退出码恒为 0，不能进 CI。

---

## 3. 参数冒烟 `tool_param_smoke.py`（278 行）

### 3.1 它做了什么
从每个工具 JSON Schema 读 required 参数，用巨型 `value_for()`（L35-169）按参数名语义造一个值，再真调一次；连 mock 设备（L219），预建 `/tmp` 文件（L224-226）。

### 3.2 审查发现

- **[真bug·最严重] "OK=87" 严重注水：未装解码器也判 OK。**
  实测 `tests/tool_param_smoke_result.json` 中：
  - `sdr_decode_ft8` → cat=OK，snippet：`"=== FT8 解码 === ... 状态: 未检测到 FT8 解码工具，建议安装 wsjtx"`
  - `sdr_decode_aprs` → cat=OK，snippet：`"未检测到 APRS 解码工具，建议安装 direwolf"`
  - `sdr_decode_adsb` → cat=OK，snippet：`"未检测到 ADS-B 解码工具，建议安装 dump1090"`

  根因在 L187：`if success and len(stripped) > 150: return "OK"`。解码工具的 handler（`sdr_tools.py` L843/L858/L874）一律 `ToolResult(success=True, content=...)`，即使内部"未检测到解码器"也返回 success=True；错误提示文本 >150 字就被当成"真出数据"。**即：机器上没装 wsjtx/direwolf/dump1090，这三个核心解码工具照样"绿灯"。**
- **[真bug] 无任何正确性断言。** 整个文件没有一处 `assert`。它只是把工具跑一遍并打印分类，L274 `return 0` 恒成功。它是"诊断脚本"，不是"测试"。
- **[空壳] 无边界值测试。** `value_for()` 对每个参数只给一个"合理默认"（freq=100 MHz、gain=20、count=10…）。不测：freq=0 / 负值 / 超过频率范围、gain>max_gain、空数组、非法枚举、`iq_samples=[]`、超长字符串。参数越界在真机上最常导致驱动崩溃，这里零覆盖。
- **[空壳] L236-237 跳过全部无参工具。** `if not required: continue` ——110 个无参工具（实测 no_required=110）在此脚本完全不跑，只靠 health_check 的空参调用兜底。
- **[建议] L55-56 对 `subagent_id/recording_id` 故意喂 `"__nonexistent_resource__"`**，预期被业务校验拦下——这是少数有意识的负例，但只覆盖"资源不存在"一条路径。
- 正向：比 health_check 进了一步（真填 required 参数），并落盘 JSON 供下一轮补值（L270-272）。

---

## 4. 实时工具冒烟 `smoke_live_tools.py`（109 行）

### 4.1 它做了什么
名义上叫"live"，但 docstring L9 明确："不依赖外部 SDR 硬件、不依赖网络 LLM"。L73 连 `device_id="mock"`。

### 4.2 审查发现

- **[真bug] "live" 名不副实：全程 mock + 合成数据，无真实硬件验证。** L73 `sdr_connect {"device_id":"mock"}`；频谱/扫频/干扰检测全部跑在 `MockSDRBackend`（`sdr_backend.py` L324）合成的 1 kHz 频偏载波 + 高斯噪声上。真实 RTL-SDR 的驱动失败、IQ 直流偏置、时钟频偏、天线失配等问题这里 100% 捕获不到。
- **[空壳] Morse 测试的 placeholder 兜底（L56-58）。**
  ```python
  morse_txt = getattr(radio_control, "morse_encode")("BI4MIB") if hasattr(...) else None
  if morse_txt is None:
      morse_txt = " -.. .. .--."  # placeholder
  check(..., isinstance(morse_txt, str) and "-" in morse_txt, ...)
  ```
  当前 `radio_control.morse_encode` 确实存在（`radio_control.py:43`），兜底是死代码；但一旦该函数被删/改名，测试不会红，反而用含 `-` 的占位串照样 PASS。断言 `"-" in morse_txt` 对占位串恒真。
- **[真bug] 其余断言全是弱子串/bool 检查：**
  - L76-77：频谱分析只查 `"peak"/"峰值"/"MHz"` 在文本里——频谱峰值算错位置也能过。
  - L80：`check("ai_sweep 扫出点", bool(sw))` ——对象非空即过。
  - L82：`"4.0" in str(z) or "99.7" in str(z)` ——魔法数字匹配，"99.7" 来历不明。
  - L87：`check("interference 真实返回", bool(it))` ——bool() 非空即过。
  - L92：`"NOAA" in str(sky) or "仰角" in str(sky)` ——子串。
  - L97：`"AX.25" in str(enc) or "hex" in str(enc).lower() or "帧" in str(enc)` ——子串。
- **[建议·唯一真测试] L36-50 AX.25/AFSK 往返是全文件唯一有断言质量的部分：** 调制→解调→断言 `fcs_valid and source=="BI4MIB"`，还跨 22050/44100/48000 三档采样率做了降采样链路测试。但它**直接调 `ax25` 模块，不走工具注册表**——注册的 `sdr_encode_ax25`/`sdr_decode_ax25` 工具并未被往返验证。
- L69 硬编码 `n >= 190`：只数工具个数，个数够就过，与正确性无关。

---

## 5. FT8 往返 `ft8_roundtrip.py`（65 行）

### 5.1 它做了什么
用 `np.sin(2πft)` 直接合成 79 个已知 8FSK 音调（L32-37），调 `detect_ft8_tone_center` + `demodulate_8fsk`，比对音调索引。

### 5.2 审查发现

- **[真bug·系统性偏移被掩盖] L47-53 遍历 8 个循环偏移取最佳匹配。**
  ```python
  for d in range(8):
      m = sum(1 for a,b in zip(got,exp) if a == (b+d)%8)
  ```
  实测本次运行：`最优循环偏移=1, 匹配=71/79 (89.87%)`。这意味着解调器有一个**固定 +1 音调的系统偏移**——若去掉循环容差直接比对，正确率会掉到很低。测试用"找最佳相位"把这个偏移 bug 吸收掉了。真机上这种固定偏移会让 LDPC 输入全错，却在实验室绿灯。
- **[真bug·门限过松] L59 `assert acc >= 0.85`** ——79 个符号允许错 12 个（15% 错误率）仍 PASS。对一个 Goertzel 硬判决、无噪合成信号，这个裕度异常大，说明作者已知抖动严重却只靠门限放行。
- **[建议] 不是"编解码共犯"型凑数，但只验符号层。** 与"编码→解码断言相等"不同：这里的发送端是 `np.sin`（数学上正确），不是项目自己的 FT8 编码器，所以**不会**出现"编码器和解码器犯同样错误也通过"。但 docstring L5 自承"LDPC 译码还原呼号仍需 jt9/wsjtx，这里只验证符号层"——即呼号/网格位的端到端正确性**零覆盖**。
- **[建议] 无 SNR 扫描。** 固定高 SNR（合成无噪）一次过，不测低 SNR/多音串扰/时间偏移下的鲁棒性。

---

## 6. 基础设施单测 `test_infra_units.py`（113 行）

覆盖模块：`sandbox`（安全沙箱）、`sdr_backend`（内存状态机）、`hal`（设备抽象）。共 7 个测试函数，实测全部 PASS。

### 6.1 审查发现

- **[真bug·空断言] `test_sandbox_runs_safe_math`（L36-42）不校验计算结果。** 执行 `x = 2 + 3 * 4`，只断言 `r.success`，**从不读 `r.output`/`r.stdout` 去确认 x==14**。沙箱若把算式算错、或模板吞掉了用户代码但仍返回 success=True，测试照样绿。对照 sandbox 实现（`sandbox.py` L227）：`success = proc.returncode==0 and (output is None or output.get("success",True))`——`output is None` 时直接判成功，即"没算出结果"也算成功。
- **[空壳] `test_hal_base_not_instantiable`（L91-98）恒真。**
  ```python
  try:
      SDRBackendBase()
  except TypeError:
      return True
  return True   # ← 无异常也 return True
  ```
  当前 `SDRBackendBase` 确是 ABC（`hal.py` L66，7 个 `@abstractmethod`），实例化抛 TypeError，所以走第一支。但**若未来有人去掉 ABCMeta，让它变成普通类，本测试不会失败**——它对"list_devices 是抽象"这个注释里的承诺没有任何断言。属于"今天碰巧过、明天降级也过"的 vacuous test。
- **[建议] `test_backend_demod_modes`（L66-75）只断言 `set_demod(m)` 返回 True，不读回确认 `status.demod_mode==m`。** 与 L47-63 的 `test_backend_state_set_get`（真的 set→get 比对频率/采样率/增益，带容差）相比，解调模式这组是半吊子。
- 正向（真有断言质量）：
  - `test_sandbox_blocks_dangerous_import`（L15-23）、`test_sandbox_blocks_eval_exec_open`（L26-33）：对 10 条危险代码逐条断言 `success=False` 且 error 含"拦截"——这是 6 个文件里最扎实的负例测试。
  - `test_backend_state_set_get`（L47-63）：频率/采样率/增益/AGC 的 set→get 往返，带数值容差，真在验证状态机。
  - `test_hal_device_info_contract`（L80-88）： trivial 但真实。

---

## 7. "实验室绿、真机红"专项判定

| 测试 | 实验室(mock/合成) | 真机/真实信号会怎样 |
|---|---|---|
| tool_selftest | 全绿（mock 后端 accept 一切） | 真 RTL-SDR 下 `sdr_connect`/`sdr_set_frequency` 可能因驱动/范围失败，暴露率更高 |
| tool_health_check | 全绿 | 同上 |
| tool_param_smoke | OK=87（含未装解码器的解码工具） | 真机/真实录制下，未装 wsjtx/direwolf 仍"OK"是误报；真信号解码路径从未被验证 |
| smoke_live_tools | 全 mock，绿 | 真频谱/真干扰/真卫星过境数据与 mock 合成数据分布完全不同，子串断言在真机下无意义 |
| ft8_roundtrip | 绿（89.87%，含 +1 偏移） | 真 FT8 信号有时间偏移、多径、低 SNR；固定 +1 偏移会被 LDPC 放大成译码失败 |
| test_infra_units | 绿 | 沙箱/状态机是纯软件，与硬件无关，这部分"实验室=真机" |

**核心风险：** mock 后端（`MockSDRBackend`）对 set_frequency/set_gain/set_sample_rate **全部返回 True 且不报错**（`sdr_backend.py` L101-126 基类逻辑，mock 未覆写 set 系列）。因此所有"设置类"工具在实验室永远绿，真机的"超量程/驱动拒绝"路径零覆盖。

---

## 8. 工具测试覆盖率统计

实测：注册工具总数 = **250**（`register_sdr_tools` 126 个 + Agent 内置 124 个）。

| 测试 | 触达工具数 | 触达方式 | 有正确性断言？ |
|---|---|---|---|
| tool_selftest | 250/250 | 最小 required 参数调用 | 否（只分 ok/friendly/crash） |
| tool_health_check | 250/250 | **空参 {}** 调用 | 否 |
| tool_param_smoke | ~101/140 有参工具（实测 JSON 101 条；110 个无参工具被 L237 跳过） | required 参数 + mock 设备 | 否（success+长度>150 即 OK） |
| smoke_live_tools | **7 个**（sdr_connect/sdr_spectrum_analyze/sdr_ai_sweep/sdr_spectrum_zoom/signal_detect_interference/sdr_satellite_sky_view/sdr_aprs_encode） | reg.call 实调 | 弱（子串/bool，见 §4.2） |
| ft8_roundtrip | **0 个工具**（测 `ft8_lite` 模块函数） | 直接函数调用 | 中（但容忍循环偏移+85%门限） |
| test_infra_units | **0 个工具**（测 sandbox/backend/hal 类） | 直接实例化 | 中-强（沙箱拦截、状态 set/get） |

**真正"输出被断言为期望值"的注册工具数：0 / 250。**
唯一达到"正确性往返验证"标准的是 `smoke_live_tools.py` L36-50 的 AX.25 模块级测试——但它不经过工具注册表，覆盖的是 `ax25.py`，不是 `sdr_encode_ax25`/`sdr_decode_ax25` 这两个注册工具。

---

## 9. 空壳 / 占位清单

| 位置 | 类型 | 说明 |
|---|---|---|
| `test_infra_units.py:91-98` | [空壳] | `test_hal_base_not_instantiable` 无异常分支也 `return True`，恒真 |
| `smoke_live_tools.py:56-58` | [占位] | morse 函数缺失时用含 `-` 的占位串兜底，断言恒过 |
| `test_infra_units.py:36-42` | [空壳] | safe_math 执行后不读输出，不验 x==14 |
| `tool_param_smoke.py:274` | [占位] | 恒 `return 0`，CRASH/TODO 再多也不 fail |
| `tool_health_check.py:78` | [占位] | 恒 `return 0` |
| `tool_param_smoke.py:187` | [真bug] | `success and len>150` 即 OK，把"未装解码器"误判为 OK |

---

## 10. 修复建议（按优先级）

1. **[高] 给解码类工具补"正例"测试：** 用仓库已有的 `syn_robot36.wav`、`real_sstv.wav`、`syn_robot36.npy` 等真实录制文件，断言 `sdr_decode_ft8/aprs/adsb/sstv` 能解出已知呼号/图像，而不是只看 success=True。
2. **[高] param_smoke 的 OK 判定收紧：** 把 L187 的"success+长度>150"改为"success 且文本不含 未实现/未检测到/建议安装/unavailable"，否则未装依赖的工具不该算 OK。
3. **[高] ft8_roundtrip 去掉循环偏移容差（或单独留一个容差用例）：** 直接 `assert got == exp`，把实测到的 +1 音调偏移 bug 暴露出来再修。
4. **[中] test_infra_units：** safe_math 断言 `r.output` 含 14；`test_hal_base_not_instantiable` 改为断言 `SDRBackendBase.__abstractmethods__` 非空；demod_modes 加读回比对。
5. **[中] 补边界值：** 对 set_frequency/set_gain 喂超量程值，断言返回 False；对解码工具喂空文件/非 wav，断言不崩且报明确错。
6. **[低] smoke_live_tools 的弱断言改为精确值：** 如 zoom(factor=4) 后中心频率/带宽应有确定变化，而非 `"4.0" in str(z)`。
