# 60 — 跨领域架构审查（循环导入 / 重复实现 / 依赖图 / 死代码 / 接口不一致 / 全局状态 / 错误处理 / 硬编码）

> 审查范围：`mbdsdr_ai/` 下 69 个 `.py` 文件（含 `__init__.py`）。
> 方法：AST 解析全部 import 关系（顶层 + 函数内延迟导入），grep 交叉验证，逐条读真实代码。
> 标注：**[真bug]** = 会导致错误行为；**[架构缺陷]** = 设计/可维护性问题；**[建议]** = 改进项。

---

## 1. 循环导入与延迟导入 workaround

### 1.1 [架构缺陷] `sdr_backend` ↔ `sdr_tools` 双向依赖
- `sdr_tools.py:34` 顶层：`from .sdr_backend import SDRBackendManager, SDRStatus`
- `sdr_backend.py:652` 函数内：`from mbdsdr_ai.sdr_tools import _get_gimbal_controller`（在 `_inject_gimbal_channel` 里）

这是一个真实的环：硬件后端层（sdr_backend）反向依赖工具层（sdr_tools）去拿全局云台控制器。目前靠延迟导入撑住，但方向是错的——应该由 sdr_tools 在连接成功后把云台控制器**注入** sdr_backend，而不是 sdr_backend 反过来 import sdr_tools。

### 1.2 [架构缺陷] `new_spacetime` → `decoders` → `orbit` 层级倒置
- `new_spacetime.py:378,459,711` 函数内 `from .decoders import compute_satellite_position, BUILTIN_TLE, SATELLITE_FREQUENCIES, list_visible_satellites`
- `decoders.py:81` 函数内 `from .orbit import fetch_tle`

名为 "new_spacetime" 的新模块不自己持有 TLE/轨道数据，反而回退去 `decoders.py` 里抓（decoders 本身又调 orbit）。TLE/频率表应该抽到独立的 `tle_data.py` 或 `sat_db.py`，让三层都依赖它，而不是 new_spacetime → decoders → orbit 的链式回灌。

### 1.3 [架构缺陷] 全仓 "双轨 import" workaround（包/裸路径双写）
每个函数内延迟导入都写成：
```python
try:
    from .new_spacetime import get_time_info
except ImportError:
    from new_spacetime import get_time_info
```
- `sdr_tools.py` 里这种成对 import 出现 **40+ 次**（见 `sdr_tools.py:4308/4310, 4336/4338, 4363/4365, 4374/4376, 4400/4402, 4420/4422, 4443/4445, 4466/4468, 4483/4485, 4533/4535, 4563/4565, 4593/4595, 4621/4623, 4665/4669, 4715/4717, 4747/4749, 4792/4794, 4815/4817, 4848/4850, 4881/4883, 4908/4910`）。
- 目的：兼容 `import mbdsdr_ai.xxx`（包模式）和 `cd mbdsdr_ai && python xxx.py`（裸脚本模式）两种跑法。
- 风险：`except ImportError` 会吞掉 `new_spacetime` 模块**内部**的 ImportError（例如 sgp4 没装），让错误被二次隐藏成"模块没找到"。应统一只用包导入，删掉裸路径 fallback。

---

## 2. 重复实现（在已知清单之外新发现的）

已知清单：卫星过境×4、GMST×2、SDR Manager×2、MockSDRBackend×2、NTP×2、云台×2。以下是**新增**：

### 2.1 [架构缺陷] `SatellitePass` 同名数据类存在 **3 份**，字段互不兼容
| 文件:行 | 字段风格 | 实际语义 |
|---|---|---|
| `astronomy.py:463` | `rise_time/set_time/max_alt_time: float`（Unix 时间戳） | 真正的"过境事件" |
| `decoders.py:103` | `name/elevation/azimuth/distance_km/doppler_hz/lat/lon/alt_km` | **其实是"当前位置快照"，不是过境** |
| `new_spacetime.py:338` | `rise_time: datetime`、`trajectory: List[(az,el)]`、`frequency_hz`、`max_doppler_hz` | 过境事件 + 轨迹 |

更糟：`__init__.py:51` 对外导出的是 `decoders.SatellitePass`（位置快照那个），名字却叫 "Pass"，调用方拿到的字段（elevation/azimuth/distance_km）和 astronomy/new_spacetime 版完全对不上。

### 2.2 [真bug] `descramble_ccdb` 在 `meteor_sat.py:339` 是空壳 stub
```python
def descramble_ccdb(bits):
    """CCDB解扰（CSSR标准）。"""
    # 简化演示
    return bits     # ← 直接返回未解扰的比特
```
而 `demod.py:448` 是真实现（多项式 `[1,5,7,8]` 的 LFSR）。两份同名函数，其中一个是 no-op。好在 grep 全仓，**这两个函数目前都没有任何调用方**（死代码），但一旦有人 `from meteor_sat import descramble_ccdb` 就会静默拿到未解扰数据。

### 2.3 [架构缺陷] `decode_sstv` 两份，签名不一致
- `decoders.py:524`：`(input_path, output_dir=None, mode="auto")` —— 输出是**目录**
- `sstv_decoder.py:737`：`(file_path, output_path=None, mode="auto")` —— 输出是**文件路径**
- `sdr_tools.py:3126-3143` 用 try/except 在两者间 fallback：先试新的，`"error" not in result` 才用，否则回落旧的。参数名都不一样（`input_path` vs `file_path`），调用方容易传错。

### 2.4 [架构缺陷] `ldpc_bp_decode` 在 `fst4_ldpc.py:39` 与 `ft8_ldpc.py:67` 双写
两个函数签名都是 `(llr: list[float], max_iter=25) -> (bits, iters)`，min-sum BP 算法骨架完全一样，只是校验矩阵不同（FST4 是 (240,101)，FT8 是 (174,91)）。应抽成 `bp_decode(llr, check_vars, var_checks, max_iter)`，两份只提供图数据。

### 2.5 [架构缺陷] GMST 公式双写（已知，但确认系数完全一致）
- `orbit.py:50` `_gmst_days(jd)`：系数 `67310.54841 + (876600*3600 + 8640184.812866)*t + 0.093104*t² - 6.2e-6*t³`
- `astronomy.py:204` `jd_to_gmst(jd)`：**同一组系数**
两者数值一致（不会算错），但是同一段数学写两遍，未来改一个忘改另一个就会出 bug。

### 2.6 [架构缺陷] 两套并行 SDR 后端抽象（比已知更严重）
不只是 "SDRManager ×2"，而是**两条完整继承树**：
- `sdr_backend.py`：抽象基类 `SDRBackend`（72 行）→ 派生 `MockSDRBackend`(324) / `RTLSDRBackend`(392) / `AISDRMiniBackend`(579) / `HackRFBackend`(760) / `USRPBackend`(845) / `FileIQBackend`(935)，由 `SDRBackendManager`(1078) 管理。
- `hal.py`：抽象基类 `SDRBackendBase(ABC)`(66) → 派生 `SoapySDRBackend`(128) / `MockSDRBackend`(335)，外加 `InstrumentBackend`(397)，由 `HardwareManager`(576) 管理。

`sdr_tools.py:4751,4797,4823` 里同时 `HardwareManager()` 拉设备列表，又用 `SDRBackendManager()` 做实际收发。两套都活着，都要维护，频率/采样率/增益参数在两边各定义一遍。

### 2.7 [架构缺陷] NTP 双实现（已知，补充不一致点）
- `time_sync.py:18` `ntp_offset(server, port, timeout) -> Dict`：失败时返回 `{"reachable": False, "error": ...}`，**不抛异常**。
- `new_spacetime.py:73` `get_ntp_time(server, timeout) -> Tuple[datetime, rtt]`：失败时 `raise NTPError(...)`。
同一个概念，一个返回 dict 吞错，一个抛异常。`sdr_tools.py:4336` 同时 import 了 `get_ntp_time, NTPError`，而 `agent.py:1098` 用的是 `ntp_offset`——上层工具里两条路径并存。

---

## 3. 模块依赖图

### 3.1 [架构缺陷] 中心节点（被依赖最多）
| 被依赖次数 | 模块 | 被谁依赖 |
|---|---|---|
| 4 | `sdr_backend` | `__init__`, `agent`, `decoders`(延迟), `sdr_tools` |
| 3 | `sdr_tools` | `__init__`, `agent`, `sdr_backend`(反向延迟) |
| 3 | `decoders` | `__init__`, `new_spacetime`(延迟), `sdr_tools` |
| 3 | `tool_registry` / `spectrum_processor` / `dsp` | `__init__`, `agent`, `sdr_tools` |

` sdr_backend` 是入度最高的节点，同时又是反向 import `sdr_tools` 的那个——它既是核心又是环的一环，改动风险最大。

### 3.2 [架构缺陷] `__init__.py`  eager import 29 个模块
`__init__.py:25-65` 在包加载时就 import 了 config / context_manager / model_manager / tool_registry / agent / memory / version_store / sandbox / self_evolution / guardian / workflow_engine / scheduler / sdr_backend / spectrum_processor / sdr_tools / dsp / decoders / hooks / subagents / pose / workflow_recorder / file_tracker / plugin_system / llm_judge / self_learning / orchestrator / code_editor / astronomy / amr。

后果：
- `import mbdsdr_ai` 一次性拉进 numpy、sgp4、所有 SDR 后端——启动慢。
- 任何一个模块（哪怕是没人用的 `demod.py`）在顶层 import 失败，整个包就崩。
- 想单独用 `mbdsdr_ai.orbit` 都得先付出整个 agent 的初始化代价。

### 3.3 [建议] 叶子节点（零入度）
AST 全图扫描后，真正零入度的只有：`demod.py`、`optional_deps.py`（`__init__.py` 是包入口不算）。其余看似"叶子"的模块（`time_sync`, `gimbal`, `hal`, `gnss_monitor`, `meteor_sat`, `satdump_integration`, `radio_control`, `openapi_integration`, `skill_registry`, `spectrum_sensing`, `signal_*`, `cfo`, `baseband_io`, `ax25`, `analog_demod`, `adsb`, `constellation`, `cw_decoder`, `fst4_ldpc`, `ft8_*`, `digital_modes`, `new_spacetime`, `orbit`, `gimbal` 等）其实都通过函数内延迟导入被 `sdr_tools.py` 或 `agent.py` 拉起来了——但这些延迟 import 只有在对应工具被调用时才触发。

---

## 4. 死代码 / 孤儿模块

### 4.1 [真bug] `demod.py` 整模块零引用
全仓 grep `from demod import` / `import demod` / `demod.xxx`（排除 `analog_demod`/`wfm_broadcast` 等同名前缀）：**0 命中**。

但 `demod.py` 里定义了一整套有用的 DSP：
- `rrc_filter`(22)、`matched_filter`(56)
- `CostasLoop`(77)、`GardnerTimingRecovery`(161)、`QPSKDemodulator`(243)、`ViterbiDecoder`(315)
- `descramble_ccdb`(448)、`descramble_nrz_m`(476)

这是成熟的数字解调链，却没人调。要么是遗漏了接线（应该被 `digital_modes.py` 或 `sdr_tools.py` 用起来），要么是历史遗留该删。

### 4.2 [架构缺陷] `optional_deps.py` 零外部引用
`check_optional_deps()` 只在 `optional_deps.py:56` 的 `__main__` 块里自己调自己。没有任何模块调用它来做降级。

### 4.3 [架构缺陷] 已定义但零调用的函数
- `descramble_ccdb`（`demod.py:448` 和 `meteor_sat.py:339`）：0 调用。
- `descramble_nrz_m`（`demod.py:476`）：0 调用。
- 详见 §2.2。

---

## 5. 接口不一致（单位 / 签名 / 返回值）

### 5.1 [真bug] `new_spacetime.predict_satellite_pass` 把 MHz 当 Hz 存
- `decoders.py:92-99` `SATELLITE_FREQUENCIES` 单位是 **MHz**（`137.620`, `145.800`），注释明确写 "卫星下行频率（MHz）"。
- `decoders.py:197` 正确使用：`freq_hz = SATELLITE_FREQUENCIES[satellite_name] * 1e6`。
- `orbit.py:174` `doppler_correction(nominal_freq_hz, ...)` 参数名带 `_hz`，内部按 Hz 用。
- `new_spacetime.py:386-387`：
  ```python
  if frequency_hz == 0 and satellite_name in SATELLITE_FREQUENCIES:
      frequency_hz = SATELLITE_FREQUENCIES[satellite_name]   # ← 没乘 1e6
  ```
  然后塞进 `SatellitePass.frequency_hz`（`new_spacetime.py:353`）。结果 NOAA 15 的频率字段是 `137.620`（Hz），比真实值小 **1e6 倍**。任何下游拿这个字段去算多普勒或调谐都会偏。

### 5.2 [架构缺陷] `frequency_manager.py:252` 用字符串猜单位
```python
hz = v * 1e6 if ("." in s or v < 100000) else v
```
用户输入 `137`（整数）→ 当 137 Hz；输入 `137.1` → 当 137.1 MHz。输入 `100000`（恰好边界）行为依赖 `<` 还是 `<=`。这种隐式规则应该让用户显式带单位（`137.1M` / `137100000`）。

### 5.3 [架构缺陷] 错误返回模式四套并存
| 模式 | 代表位置 | 语义 |
|---|---|---|
| `ToolResult(success, content, error=...)` | `tool_registry.py:24`，sdr_tools 全部 handler | 工具层 |
| `{"error": "..."}` dict | `decoders.py`（16 处）、`sstv_decoder.py`（7 处）、`orbit.py`、`baseband_io.py`、`gnss_monitor.py` | 解码/计算层 |
| `return None` 表示失败 | `orbit.compute_satellite_state`、`new_spacetime.predict_satellite_pass` | 轨道层 |
| `raise XxxError` | `new_spacetime.NTPError`(69) | 授时层 |

上层 handler 必须记住：调 decoders 要判 `"error" in result`，调 orbit 要判 `is None`，调 new_spacetime NTP 要 try/except，调 tool 直接拿 ToolResult。没有统一约定。

### 5.4 [建议] `Orchestrator` 与 `Scheduler` 方法名撞车
两个类都有 `add_task / get_task / list_tasks / execute / _execute_task / get_stats`，但语义完全不同（Orchestrator 是一次性任务规划；Scheduler 是持久化定时循环）。agent.py:141 和 agent.py:32 同时持有两个实例，读代码时极易混淆。建议改名（`Orchestrator.plan_and_run` / `Scheduler.schedule_task`）。

---

## 6. 全局可变状态 / 线程安全

### 6.1 [架构缺陷] `sdr_tools.py` 三个懒加载全局单例无锁
- `_pnt_engine`（`sdr_tools.py:4459`）→ `_get_pnt_engine()`(4462)
- `_radio_instance`（`sdr_tools.py:4939`）→ `_get_radio()`(4942)
- `_GIMBAL_CONTROLLER`（`sdr_tools.py:5826`）→ `_get_gimbal_controller()`(5829)

全部是 `if _x is None: _x = Xxx()` 的懒初始化，**没有 threading.Lock**。而 `scheduler.py:286` 起了后台线程跑 `_loop`，`sdr_backend.py:201` 也有录制线程。多线程同时首次访问单例会创建两个实例。

对比：`hooks.py:168`、`orchestrator.py:146`、`subagents.py:238`、`sdr_backend.py:602` 都正确用了 `threading.Lock()`——说明项目知道要加锁，但这三个单例漏了。

### 6.2 [架构缺陷] `sdr_backend.py:652` 跨模块写全局状态
`sdr_backend._inject_gimbal_channel` 直接 `gc.board.set_send_callable(lambda ...: self._send_mcp(...))`——硬件后端连 WS 成功后，反向改 sdr_tools 全局云台控制器的内部状态。这是 §1.1 循环依赖在运行时的体现：一个模块的连接事件会静默改另一个模块的全局对象。

---

## 7. 错误处理模式

### 7.1 [架构缺陷] 裸 `except Exception: pass` 吞异常热点
全仓统计（`except:` 后紧跟 `pass` 的位置数）：
- `sdr_tools.py`：11 处
- `sdr_backend.py`：8 处
- `agent.py`：6 处
- `sandbox.py` / `gimbal.py` / `self_learning.py` / `radio_control.py` / `hal.py` / `frequency_manager.py` / `ax25.py`：各 2-3 处

最危险的几处：
- `sdr_backend.py:1187`：`self.active_backend.disconnect()` 失败被 pass——断硬件失败无日志，下次 connect 可能状态错乱。
- `sdr_backend.py:481,680,799,885`：各种 `_sdr.close()` / `_ws.close()` / `_hackrf.close()` 失败都 pass，资源泄漏无感知。
- `gimbal.py:134`：socket 读 chunk 失败 pass，云台通信挂了不报错。
- `radio_control.py:305,339`：串口 close / setRTS 失败 pass。
- `agent.py:90`：`model_manager.fetch_models(force_refresh=True)` 失败 pass，模型列表静默不更新。

对比：`decoders.py:83` 的 `fetch_tle` 失败回退 builtin TLE 是**有意**的 fallback，可以接受；但上面这些资源/硬件类的 pass 应该至少 `logger.warning`。

---

## 8. 配置硬编码

### 8.1 [架构缺陷] 长春坐标散落在 6 个文件
| 位置 | 硬编码值 |
|---|---|
| `agent.py:118` | `declination=-9.0`（长春磁偏角） |
| `agent.py:955,996,1072` | `observer_lat=43.8` |
| `pose.py:217,416` | 文档注释"长春约 -9°" |
| `sdr_tools.py:888-889,954-955,3292-3293,3345-3346,3458` | `43.82, 125.32`（GPS 模拟数据也写死 250m 海拔、12 星、HDOP 0.8） |
| `workflow_engine.py:228-229` | `latitude:43.8, longitude:125.3` 写在工作流模板里 |

`config.py:36` 的 `AgentConfig` 数据类里**没有** observer_lat/lon/declination 字段。地面站位置应该是启动期配置项，而不是散落在工具默认值和工作流模板里。

### 8.2 [架构缺陷] NTP 服务器地址硬编码 4+ 处
- `time_sync.py:18` 默认 `ntp.aliyun.com`
- `new_spacetime.py:36-39` 列表 `[ntp.aliyun.com, ntp.tencent.com, cn.ntp.org.cn, pool.ntp.org]`
- `new_spacetime.py:73` 默认 `ntp.aliyun.com`
- `agent.py:1101` 默认 `ntp.aliyun.com`
- `sdr_tools.py:4340` 默认 `ntp.aliyun.com`

### 8.3 [架构缺陷] `~/.mbdsdr` 路径在 16 个模块里各自 os.path.expanduser
`code_editor.py, config.py, decoders.py, file_tracker.py, frequency_manager.py, guardian.py, memory.py, plugin_system.py, scheduler.py, sdr_tools.py, self_evolution.py, self_learning.py, version_store.py, workflow_engine.py, workflow_recorder.py`。没有一个集中的 `paths.py` 来管理数据根目录，想换数据目录（比如改到 `/var/lib/mbdsdr`）要改 16 个文件。

---

## 9. 其他跨模块问题

### 9.1 [真bug] `decoders.py:201` 多普勒计算恒为 0
```python
radial_velocity = 0  # km/s，简化为 0，实际需要速度向量
...
doppler = freq_hz * radial_velocity / c   # 永远是 0
```
但函数返回的 dict 里照样带 `doppler_hz` 字段（`decoders.py:285-286` 附近），调用方可能误以为拿到了真实多普勒。

### 9.2 [建议] `sdr_tools.py` 是 god object
文件 5900+ 行，注册了 38+ 个工具，内部再延迟 import 40+ 个模块。建议按类别拆成 `sdr_tools_device.py / sdr_tools_spectrum.py / sdr_tools_decode.py / sdr_tools_satellite.py / sdr_tools_gimbal.py`，由 `sdr_tools.py` 统一注册。

---

## 优先级建议

| 优先级 | 项 | 理由 |
|---|---|---|
| P0 | §5.1 new_spacetime MHz→Hz 漏乘 1e6 | 真 bug，频率偏 1e6 倍 |
| P0 | §4.1 demod.py 整模块死代码 | 要么接线要么删，否则维护两套解调链 |
| P0 | §2.2 meteor_sat.descramble_ccdb 空壳 | 一旦被误用静默解扰失败 |
| P1 | §1.1 sdr_backend ↔ sdr_tools 环 | 反向依赖，改硬件层要当心工具层 |
| P1 | §2.6 两套 SDR 后端继承树 | 长期维护成本翻倍 |
| P1 | §2.1 三份 SatellitePass 字段不兼容 | 包导出还导错了那个 |
| P1 | §6.1 三个无锁单例 | 后台线程首次访问可能双实例 |
| P2 | §1.3 双轨 import fallback | 吞内部 ImportError |
| P2 | §7.1 硬件资源类 except: pass | 资源泄漏无日志 |
| P2 | §8.1/8.2/8.3 硬编码 | 可移植性差 |
| P3 | §2.4/2.5/2.7/5.4 重复与命名 | 重构项 |
