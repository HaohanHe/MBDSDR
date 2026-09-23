# 22 · radio_control.py 无线电控制接口 / RadioCAT 审查（R2）

- 审查对象：`mbdsdr_ai/radio_control.py`（368 行）
- 交叉阅读：`mbdsdr_ai/sdr_tools.py:1905-2020`（工具注册）、`sdr_tools.py:4935-5083`（`_radio_*` 包装函数）、`mbdsdr_ai/hal.py`（SDR 接收链 set_frequency）、`desktop/main_window.py`（UI 接线）。
- 结论概览：**本文件名不副实**。文件头 docstring（`radio_control.py:1-7`）自述为「Morse 编解码与 CW 生成」，全文实际只含三块：Morse 编解码（43-210）、CW 音频生成/解码（77-210）、`RadioCAT` 串口外部电台 CAT 类（217-368）。**全文没有一行引用 sdr_backend / hal，没有增益、静噪、VFO A/B、频率步进、Qt 信号**。审查清单里假设的"频率/模式/增益/静噪下发到 sdr_backend"在本文件中根本不存在——SDR 接收链的频率/模式/增益实际由 `sdr_backend.py` + `sdr_tools.py` 承担，与 `RadioCAT` 是两条互不相干的控制通道。
- 最严重问题：`connect()` 把 ImportError 和任何异常都吞成"连接成功"（`radio_control.py:291-298`），配合 `set_frequency/set_mode/send_cw_text` 全部只改内存状态、不发字节，构成教科书级的「实验室绿、真机红」。

---

## 〇、文件真实结构 vs 审查清单的对照

| 审查期待 | 本文件实际情况 |
|---|---|
| 频率下发 sdr_backend | ❌ 无。`RadioCAT.set_frequency` 只写 `self._freq`（`:312`），与 sdr_backend 零交互 |
| 增益 / 静噪控制 | ❌ 整个文件无 `set_gain` / `set_squelch` 方法 |
| AM/FM/SSB/CW/Digital 模式切换 + 模式特定参数 | ⚠️ 仅字符串改名（`:322-325`），无带宽/shift/AGC 等任何模式参数 |
| 主/亚 VFO、A/B 切换、频率步进 | ❌ 无 VFO 字段、无步进概念 |
| 与 UI 的信号/回调接口 | ❌ `desktop/main_window.py` 全文无 `radio_*`/`RadioCAT`/`morse`/`PTT` 任何引用 |
| 与 sdr_backend/hal 重复/冲突 | ⚠️ 控制对象不同（外部电台 vs SDR 狗），无逻辑冲突但命名易混（见 §六） |

---

## 一、`RadioCAT.connect()` —— 「实验室绿、真机红」的根因

### [真bug] `connect()` 把所有失败路径都返回成功
- `radio_control.py:286-298`：
  - `:291-294` `ImportError`（实验室机器没装 pyserial）→ `self._connected = True; return True`；
  - `:295-298` 兜底 `except Exception`（真机端口错、波特率错、权限不足、设备未插）→ **同样** `self._connected = True; return True`。
- 后果：无论装没装 pyserial、端口对不对、线插没插，上层永远看到"已连接"。`sdr_tools.py:4982` 据此打印"状态: 已连接"，`mcp_server` 也回 success。真机调试时所有后续命令都"成功"但电台毫无反应，排查极困难。
- 这正是审查重点 #6 描述的现象在本文件的对应物：模拟后端（无 pyserial 或端口为空）"通过"，真机（真接一台 IC-7300）立刻哑火，但错误被完全吞掉。

### [建议] 假连接态下 `self._serial` 恒为 None
- 失败路径里 `self._serial` 未被赋值（`:288` 在异常前退出），保持 `None`。于是 `set_frequency`（`:313`）、`set_ptt`（`:333`）里的 `if self._serial:` 分支永远不进，所有命令退化为纯内存赋值——但 `_connected` 又是 True，自洽地骗过自己。

---

## 二、控制命令是否真的下发硬件 —— 几乎全是空壳

### [空壳] `set_frequency` 即使串口打开也只 `pass`
- `radio_control.py:310-316`：
  ```python
  self._freq = freq_hz            # :312 只改内存
  if self._serial:
      # 这里发送CI-V或Yaesu命令，简化为模拟   # :314
      pass                          # :315
  return True                     # :316 永远 True
  ```
- 注释自承"简化为模拟"。**即使 `pyserial` 真装了、端口真开了，也一个字节都不发**。CI-V 帧（`FE FE E0 03 FD 05 ...`）、Yaesu `IF`/Kenwood `FA` 命令全部缺位。
- 无频率范围校验、无回读确认、永远 `return True`。

### [空壳] `set_mode` 纯字符串映射，无模式特定参数
- `radio_control.py:245-249` `MODES` 表仅做改名（`DIG→DATA-USB`、`FMN→FM-N` 等）。
- `:322-325` `set_mode` 只调 `self.MODES.get(...)`，不写串口、不带滤波器带宽/中频偏移/AGC/带宽参数。
- SSB 收发带宽（≥2.4k/1.8k）、CW 窄带（≤500Hz）、AM 带宽（6-10k）、FM 去加重等模式特定参数**完全不存在**。

### [空壳] `send_cw_text` 只记录一行字符串
- `radio_control.py:346-355`：`:354` `self._last_cw = f"{text} ({wpm} WPM)"`，无任何 CI-V 文本键控帧、无侧音生成、无 PTT 联动。
- `:351` 守卫 `if not self._connected: return False` 在当前 `connect()` 实现下**是死代码**——`_connected` 恒为 True，永远走不到 False 分支。

### [占位] VFO A/B、频率步进、增益、静噪 —— 完全缺失
- 全文 grep 无 `vfo`/`step`/`gain`/`squelch`/`atten` 任何字段或方法。
- 而工具描述 `sdr_tools.py:1939` 对外宣称"电台自动切换到对应VFO"——**能力承诺与实现不符**，属误导性文档。

### [真bug] `set_ptt` 是唯一真正碰硬件的方法，但方式粗糙
- `radio_control.py:330-341`：靠 `self._serial.setRTS(True/False)`（`:336-338`）硬切 RTS 线。
- 对 RTS 直控 PTT 的简易电台可用；对 ICOM CI-V 系列必须发 `CV` 命令帧（0x1C），RTS 无效。类内 `RADIO_MODELS`（`:229-242`）列了 IC-705/7300/7610，却走不通它们的协议。
- 异常被 `:339-340` 静默吞掉；且不检查 `_connected`，假连接态下 `_serial is None` 时只改 `self._ptt` 内存。

---

## 三、与 sdr_backend 已知问题的传导关系

### [确认] 已知问题 #8（`set_frequency` 越界返回 False 被 try/except 吞掉）在本文件**不直接传导**
- `sdr_backend.py:105-106` 的越界 False 路径只存在于 SDR 狗接收链。`RadioCAT` 是独立的外部电台通道，**根本不调用 sdr_backend**，所以那条吞 False 的链到不了这里。
- 但本文件以更坏的方式重演了同一反模式：
  - `RadioCAT.set_frequency`（`radio_control.py:316`）**从不返回 False**，连"越界"概念都没有；
  - 上层 `_radio_set_frequency`（`sdr_tools.py:4997`）调用后**连返回值都不接**，直接打印"状态: 已设置"（`sdr_tools.py:5001`）；
  - 工具注册层 `sdr_tools.py:1947` 用 `lambda args: ToolResult(success=True, content=...)` **把 success 硬编码为 True**。
- 三层叠加 = 任何失败都不会暴露给 Agent/用户。`set_mode`（`:1961`）、`radio_ptt`（`:1975`）同款硬编码 True。

### [真bug] 未连接即调用的"幽灵电台"单例
- `sdr_tools.py:4941-4946` `_get_radio()` 懒加载 `RadioCAT()`（`port=""`）。
- 若 Agent 先调 `radio_set_frequency` 再调 `radio_connect`，拿到的是一个空端口的幽灵实例，照样返回 True 并打印"已设置"。
- `_radio_connect`（`sdr_tools.py:4972`）虽然新建实例替换单例，但 `:4973` `ok = radio.connect()` 恒为 True，`:4982` 永远显示"已连接"。

---

## 四、与 hal / sdr_backend 的关系

### [建议] 无重复控制逻辑，但命名通道易混
- `hal.py:95/253/360/673/688` 的 `set_frequency` 作用于 `self._active_backend`（SDR 狗 IQ 接收链）；`RadioCAT` 作用于串口外接收发信机。两者控制的是两台**不同硬件**，无逻辑冲突。
- 但对 Agent/用户而言，`radio_set_frequency`（外部电台）与 `sdr_set_frequency`（SDR 狗）名字几乎一样，实际频率域完全独立、无任何联动。建议在工具描述里显式区分"收发信机 CAT"与"SDR 接收机"，避免 Agent 调错通道。
- 本文件不读 `hal.py`，hal 也不读 `RadioCAT`——无双向耦合，属设计边界清晰。

---

## 五、与 UI 的接口

### [确认] UI 完全没有接 RadioCAT
- `desktop/main_window.py` grep `radio_|RadioCAT|morse|CAT|ptt|PTT` 零命中（仅 Qt 本身的 `QApplication` 误命中）。
- 即：桌面 UI 没有频率输入框、模式下拉、PTT 按钮、CW 面板连到这条链路。`RadioCAT` 的唯一出口是 Agent 工具注册表（`sdr_tools.py:1909-2019`），由 LLM 自主决定调用。
- 无 Qt signal/pyqtSlot、无回调、无状态广播。`RadioCAT.get_status()`（`:357-368`）虽返回字典，但没有任何 UI 订阅它。

---

## 六、Morse / CW 音频部分（43-210）—— 实际可用

这部分不在"无线电控制"审查重点内，但作为文件主体，顺带结论：

- `morse_encode` / `morse_decode`（`:43-74`）表驱动，ITU 码表齐全，可用。
- `text_to_cw_audio`（`:77-136`）PARIS 定时正确：点 1 单位、点划间隔 1、字母间补 2（共 3）、词间补 4（共 7），与 `morse_encode` 的 ` / ` 词间分隔配合无误。
- `cw_decode_from_audio`（`:139-210`）为包络 + 门限 + 固定 dot_samples 的启发式解码，**未对实测 dot 长度做自校准**，wpm 偏差大时解码率下降。
- [建议] `:51-54` 未知字符被静默丢弃（不报错不占位），长文本里丢字难以察觉。

---

## 七、发现汇总

| # | 级别 | 位置 | 问题 |
|---|---|---|---|
| 1 | 🔴 [真bug] | `radio_control.py:291-298` | `connect()` 吞 ImportError 与所有异常，恒返回"已连接"——实验室绿/真机红根因 |
| 2 | ⚪ [空壳] | `radio_control.py:310-316` | `set_frequency` 即使串口打开也只 `pass`，不发 CI-V/Yaesu 帧 |
| 3 | ⚪ [空壳] | `radio_control.py:322-325` | `set_mode` 纯字符串改名，无带宽/shift/AGC 等模式参数 |
| 4 | ⚪ [空壳] | `radio_control.py:346-355` | `send_cw_text` 仅记 `self._last_cw`，无键控帧；`_connected` 守卫是死代码 |
| 5 | 🟡 [占位] | `radio_control.py` 全文 | VFO A/B、频率步进、增益、静噪**完全不存在**；`sdr_tools.py:1939` 却对外承诺 VFO 自动切换 |
| 6 | 🔴 [真bug] | `radio_control.py:330-341` | `set_ptt` 唯一真硬件调用，但仅 RTS 硬切，对 CI-V 电台无效；异常被吞 |
| 7 | 🔴 [真bug] | `sdr_tools.py:4997,5001,1947,1961,1975` | 包装层丢弃返回值并硬编码 `ToolResult(success=True)`，失败永不外露 |
| 8 | 🔴 [真bug] | `sdr_tools.py:4941-4946` | 未 `radio_connect` 即可用空端口幽灵单例调频率/PTT，仍报成功 |
| 9 | 🟢 [建议] | `radio_control.py:268-277` | 无 pyserial 时 `list_ports` 伪造 `/dev/ttyUSB0` 等假端口 |
| 10 | 🟢 [建议] | `radio_control.py:300-308` + `sdr_tools.py:4976` | `disconnect()` 不重置 `_radio_instance`，断开后仍可对旧实例下发命令 |
| 11 | 🟢 [建议] | `radio_control.py:51-54` | 未知字符静默丢弃，CW 长文本易丢字 |
| 12 | ✅ [确认] | — | 已知问题 #8（sdr_backend 越界 False 被吞）不传导到本文件；但本文件以"恒 True"方式更彻底地掩盖失败 |
| 13 | ✅ [确认] | `desktop/main_window.py` | UI 与 RadioCAT 零接线，无信号/回调 |
| 14 | ✅ [确认] | `hal.py` vs `RadioCAT` | 控制不同硬件（SDR 狗 vs 外部电台），无逻辑冲突；仅命名易混 |

**一句话定性**：`radio_control.py` 的 `RadioCAT` 是一个"连接永远成功、命令永远成功、但除 PTT-RTS 外从不发任何字节"的模拟壳；它与 SDR 接收链（sdr_backend）并行存在、互不相干，审查清单中关于"频率/模式/增益/静噪下发到 sdr_backend"的预期在本文件中不成立——真正的 SDR 控制在 `sdr_backend.py`/`sdr_tools.py`，已由兄弟报告覆盖。
