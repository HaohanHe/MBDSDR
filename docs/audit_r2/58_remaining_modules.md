# R2-58 剩余模块审查报告

> 审查范围：此前 57 个子 agent 未专门覆盖的模块
> - `mbdsdr_ai/satdump_integration.py`（240 行）
> - `mbdsdr_ai/scheduler.py`（337 行）
> - `mbdsdr_ai/meteor_sat.py` LRO/轨道力学段
> - `mbdsdr_ai/__init__.py`
> - `skills/`、`scripts/`
> - 孤儿代码 / 重复实现 / 死代码 / 循环 import / MGRS 全仓扫描
>
> 审查方式：只读，逐行读真实代码 + 全仓 grep 调用关系。
> 分级：[真bug] / [空壳] / [占位] / [建议]。

---

## 一、satdump_integration.py

### [真bug] live 模式超时后谎称"后台运行中"
**位置：`mbdsdr_ai/satdump_integration.py:99-112`**

```python
result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, ...)
...
except subprocess.TimeoutExpired:
    return {
        "success": True,
        "message": "接收已启动（后台运行中）",
        "command": ' '.join(cmd),
    }
```

`subprocess.run(timeout=...)` 超时后会 **SIGKILL 子进程并回收**，并不是把它丢到后台。返回值却声称 `"success": True` + "后台运行中"。MCP 工具层（`sdr_tools.py:2154`）再把这个结果包成 `ToolResult(success=True, ...)` 回给 AI/用户，于是 AI 会以为接收已经在后台跑起来了——实际上进程已经被杀，IQ 数据一个字节都不会落盘。这是"假成功"，比直接报错更危险。

### [真bug] live 模式 30s 超时对真实卫星过境必然触发
**位置：`mbdsdr_ai/satdump_integration.py:99`**

注释自己写了 `# 30秒后超时（实际接收需要更长时间）`。NOAA/Meteor 极轨过境 5–15 分钟，GEO LRPT 是连续流。`timeout=30` 意味着任何一次真实 live 调用都必然在 30s 后走到上面那个 `TimeoutExpired` 分支——也就是 live 模式 **100% 返回假成功，从来不真正接收**。该工具在当前实现下对真实接收链路是失效的。

### [真bug] find_satdump 漏掉 PermissionError
**位置：`mbdsdr_ai/satdump_integration.py:33-42`**

```python
try:
    result = subprocess.run([path, "--help"], capture_output=True, timeout=5)
    ...
except (FileNotFoundError, subprocess.TimeoutExpired):
    continue
```

已验证：`PermissionError` **不是** `FileNotFoundError` 的子类。当 `SATDUMP_PATHS` 中某个绝对路径（如 `/usr/bin/satdump`）文件存在但缺执行位时，`execve` 返回 EACCES，Python 抛 `PermissionError`，这个 except 接不住，会一路冒泡把 `find_satdump` / `check_satdump_installed` / `satdump_live` / `satdump_process` 全部炸掉。应改为 `except OSError`。

### [真bug/安全] satellite 参数无白名单，存在参数注入面
**位置：`mbdsdr_ai/satdump_integration.py:85-92`、`130-135`；schema 在 `mbdsdr_ai/sdr_tools.py:2148`、`2163`**

`cmd = [satdump_path, "live", satellite, str(frequency), output_dir, ...]` 用 list 形式 + `shell=False`，**没有 shell 命令注入**。但 `satellite` 是用户/MCP 自由字符串，没有跟本文件已有的 `SATDUMP_SATELLITES` 字典做 key 校验，schema 里也只是 `"type": "string"` 没加 enum。若传入以 `-` 开头的值（如 `--help`、`-c`、`-v`），会被 satdump 解释成自己的命令行开关——这是**参数注入**（不是 shell 注入），可导致非预期行为。`input_file` / `output_dir` 同理未做存在性/越权校验。

### [空壳] 整模块依赖外部 SatDump 二进制，常态下恒为"未安装"
**位置：全模块**

`find_satdump()` 在绝大多数开发/CI/平板环境里返回 `None`，于是 `satdump_live` / `satdump_process` 永远走到 `"SatDump 未安装"` 分支。但 `sdr_tools.py:2141-2170` 仍然把它们注册成一等 MCP 工具，AI 看到工具描述会以为能真收真解。实际是"有壳无瓤"。

### [建议] compose_cloud_image 盲目取第一张图
**位置：`mbdsdr_ai/satdump_integration.py:210-216`**

`images = list(...glob("**/*.png")) + list(...glob("**/*.jpg"))` 后直接 `Image.open(images[0])`。SatDump 输出目录里通常有 channel/equ/color/preview 多张图，取 glob 顺序第一张不保证是合成云图，可能取到单通道或预览图。

### [建议] find_satdump 探测启发式过宽
**位置：`mbdsdr_ai/satdump_integration.py:39`**

`if result.returncode == 0 or b"satdump" in result.stderr.lower()`——任何二进制只要 stderr 里恰好出现 "satdump" 字样就被误判为已安装。

---

## 二、scheduler.py

### [真bug] cron 类型任务永远只更新一次 next_run，之后每 30s 空转狂触发
**位置：`mbdsdr_ai/scheduler.py:265-272`**

```python
# 计算下次执行时间
if task.schedule_type == "once":
    task.enabled = False
elif task.schedule_type == "interval":
    task.next_run = now + task.interval_seconds
# cron 类型暂不支持复杂解析，按 interval 处理
```

注释写着"cron 类型暂不支持复杂解析，按 interval 处理"，**但代码里根本没有 cron 分支**。`schedule_type == "cron"` 的任务执行完后 `next_run` 保持旧值（仍 ≤ now），于是下一次 `_loop` 醒来（30s 后）`task.next_run > now` 判断为 False，**再次执行**——然后再次不更新 next_run……cron 任务会被每 30 秒重复触发，直到进程退出。这是死循环式误触发，不是"暂不支持"。

### [空壳] cron_expression 字段从不解析
**位置：`mbdsdr_ai/scheduler.py:40`、`143`、`167`**

`cron_expression` 被存进 dataclass、持久化到 JSON，但全文件没有任何一处读它去算下一次触发时间。用户填了 `"0 2 * * *"` 之类的表达式会被完全忽略。

### [建议] 无任务执行锁，tick() 公开可重入
**位置：`mbdsdr_ai/scheduler.py:243-277`、`295-302`**

`_loop` 单线程跑 tick 本身不会自重叠，但 `tick()` 是 public 方法，文档字符串里甚至教用户手动 `scheduler.tick()`。外部线程若与 `_loop` 并发调 tick，`self.tasks` 字典、`run_count`/`success_count` 计数、`_save()` 写文件全是竞态。应加 `threading.Lock`。

### [建议] 无错误重试 / 退避
**位置：`mbdsdr_ai/scheduler.py:228-241`**

`_execute_task` 捕获异常后只 `print` 并返回 False，外层无论成败都照样把 `next_run` 推到 `now + interval_seconds`。一次瞬时失败（SDR 被占、TLE 网络抖动）要等整整一个周期才会再试，无立即重试、无指数退避、无死信队列。

### [建议] enable_task 对 once 任务忽略 run_at
**位置：`mbdsdr_ai/scheduler.py:199`**

```python
task.next_run = time.time() + task.interval_seconds
```

once 任务在到点前被 disable 再 enable，应该回到 `run_at`，这里却被改成"现在 + interval"，一次性任务的调度语义被改写。

---

## 三、meteor_sat.py（LRO / 轨道力学）

### [真bug] EKF 观测量纲错误：把多普勒(Hz)当成 vz(m/s) 直接观测
**位置：`mbdsdr_ai/meteor_sat.py:543-544`、`551`**

```python
H = np.zeros((1, n))
H[0, 3:6] = [0, 0, 1]   # 注释写"观测z方向速度"
...
innovation = y - H @ x_pred   # y 是多普勒 Hz，H@x_pred 是 vz m/s
```

`observations` 在 `sdr_tools.py:5283` 生成时是 `doppler_shift(...)`，单位 **Hz**；而 `H` 选的是状态向量第 6 维 `vz`，单位 **m/s**。卡尔曼增益 `K = P H^T (H P H^T + R)^-1` 里 `R` 在调用处（`sdr_tools.py:5294`）按 Hz² 构造，但观测矩阵按 m/s 量纲取值——两者量纲不一致，滤波器并不在"拟合多普勒"，只是把 `vz` 往一个 Hz 数值上硬拽。这不是真正的多普勒定轨，跑出来的"最终位置误差"数字没有物理意义。

### [空壳] EKF 状态转移用匀速模型，完全忽略月球引力
**位置：`mbdsdr_ai/meteor_sat.py:532-537`**

```python
F = np.eye(n)
F[0, 3] = dt; F[1, 4] = dt; F[2, 5] = dt
x_pred = F @ x
```

LRO 绕月做开普勒运动，加速度 ≈ `MOON_MU/r²` ≈ 1.6 m/s²，10s 间隔下速度变化 16 m/s，完全不可忽略。匀速 F 意味着预测步每步都在累积轨道误差，EKF 必然发散。

### [空壳] 四个信号处理函数全是演示桩，且全仓零调用
**位置：`mbdsdr_ai/meteor_sat.py:306-324`、`327-336`、`339-346`、`349-378`、`385-400`**

- `qpsk_demodulate`：只是 `iq[::sps]` 抽取，注释自承"实际需要 Costas 环"，无载波/位同步。
- `viterbi_decode_demo`：`return bits[:len(bits)//2]`，把比特砍一半，**没有任何 Viterbi 算法**。
- `descramble_ccdb`：`return bits`，原样返回，**没有 CCDB 多项式**。
- `compose_visible_image`：`np.mgrid` 生成径向渐变 + 噪声，完全忽略入参 `cadu_data`。

grep 全仓（排除 `.sessions/` 和本文件）：这四个函数 + `extract_cadu` **没有任何外部 import 或调用**。它们是孤儿空壳。

### [重复实现不一致] descramble_ccdb 在两个文件里行为相反
**位置：`mbdsdr_ai/meteor_sat.py:339` vs `mbdsdr_ai/demod.py:448`**

- `demod.py:448` 的 `descramble_ccdb` 是**真实现**：按 `x^8+x^7+x^5+x^3+1` 多项式逐比特异或移位。
- `meteor_sat.py:339` 的同名函数是 **no-op**：直接 `return bits`。

两处同名同语义但行为不一致，谁 import 到哪个版本取决于路径，是典型的"双份实现漂移"。meteor_sat 那份应删除或改为 `from .demod import descramble_ccdb`。

### [建议] LRO NORAD 编号疑似错误
**位置：`mbdsdr_ai/meteor_sat.py:248`**

`norad_id=37349`。LRO（Lunar Reconnaissance Orbiter，2009 年发射）实际 NORAD catalog number 是 **35248**。37349 对不上；后续若接 `orbit.py:fetch_tle(catnr)` 按这个编号拉 TLE 会取错星。

### [建议] ASM 同步字为自造 64 位，非 CCSDS 标准
**位置：`mbdsdr_ai/meteor_sat.py:356-359`**

`extract_cadu` 里的 ASM 是 64 位自造 pattern。真实 CCSDS TM ASM 是 32 位 `0x1ACFFC1D`。且 `np.array_equal` 要求逐比特全等，无容错，实际信号有 BER 时一帧都搜不到。

### [占位] LROOrbit 轨道模型无 RAAN/近地点幅角
**位置：`mbdsdr_ai/meteor_sat.py:441-454`**

`position()` 只是绕 x 轴做了一个倾角旋转，把轨道面"贴"在升交点=x 轴上，没有升交点赤经、近地点幅角、偏心率。velocity 推导与 position 自洽（已逐项验算），所以不是数学错误，但只是极简化演示，不能用于真实 LRO 定轨。

---

## 四、__init__.py

### [建议] `__all__` 与实际 import 严重不一致
**位置：`mbdsdr_ai/__init__.py:27-65`（import） vs `68-92`（`__all__`）**

文件顶层 import 了 ~40 个名字（SDRBackendManager、SpectrumProcessor、register_sdr_tools、整组 dsp 函数、整组 decoders、HookManager、SubagentManager、pose 全家、workflow_recorder、file_tracker、plugin_system、llm_judge、self_learning、orchestrator、code_editor、astronomy、amr……），但 `__all__` 只列了 23 个。后果：`from mbdsdr_ai import *` 会静默丢掉后 17+ 个公共符号；用户按 `__all__` 做 tab-complete 也看不到它们。

### [建议] 核心能力模块未在包级导出
`satdump_integration`、`meteor_sat`、`orbit`、`gimbal`、`gnss_monitor`、`frequency_manager` 等都没有在 `__init__.py` re-export，外部必须 `from mbdsdr_ai.satdump_integration import ...` 深路径导入，与文档字符串宣称的 `from mbdsdr_ai import MBDSDRAgent` 简洁用法不一致。

---

## 五、scripts/

### [真bug] rtl_selfcheck.py 的 `--branch` 参数是死参数
**位置：`scripts/rtl_selfcheck.py:236` 定义 vs `138` 硬编码**

```python
ap.add_argument("--branch", type=str, default="q", help="HF 直采分支 i/q")   # line 236
...
ds = backend.set_direct_sampling("q")   # line 138，硬编码，从不读 args.branch
```

用户传 `--branch i` 期望切到 I 路直采，实际代码永远写死 `"q"`，参数被静默忽略。

### [建议] rtl_fm_listen.py 采集循环无最大重试
**位置：`scripts/rtl_fm_listen.py:53-59`**

```python
while got < n:
    part = be.read_samples(...)
    if part is None or len(part) == 0:
        time.sleep(0.02); continue
```

设备掉线/读取出错时 `read_samples` 持续返回 None，这个循环会空转永不退出，没有重试上限也没有总超时。

---

## 六、skills/ 目录

`skills/find-interference/SKILL.md`、`skills/sat-track/SKILL.md`、`skills/sstv-decode/SKILL.md` 均为 Markdown 流程描述，无 Python 代码。

引用的工具名（`sdr_sweep_scan`、`sdr_spectrum_find_signals`、`sdr_decode_noaa_apt`、`cfo`、`astronomy`）在 `sdr_tools.py` / `cfo.py` / `astronomy.py` 中均能找到对应实现，未发现"技能里写了但代码里没有"的空引用。无 bug。

---

## 七、跨模块专项扫描

### 循环 import
`satdump_integration.py` / `scheduler.py` / `meteor_sat.py` 顶层只 import stdlib + numpy + typing，**不反向依赖 mbdsdr_ai 内部模块**。
- `scheduler` 被 `agent.py:32` 和 `__init__.py:38` 顶层 import；
- `meteor_sat` / `satdump_integration` 只在 `sdr_tools.py` 函数体内 lazy import（如 `5092`、`5184`、`5328`、`5367`），不在模块顶层。

**未发现这三个模块参与的循环 import。** lazy import 的副作用是：模块级 import 错误被推迟到工具首次调用时才爆，`mbdsdr_ai` 包 import 阶段不会暴露依赖缺失。

### 孤儿代码（全仓零调用）
- `meteor_sat.qpsk_demodulate` / `viterbi_decode_demo` / `descramble_ccdb(本文件版)` / `extract_cadu` / `compose_visible_image`：全仓零外部调用。
- `satdump_integration.find_satdump` 仅被同文件内 `check_satdump_installed` / `satdump_live` / `satdump_process` 调用，属于内部辅助，可接受。
- `scheduler.ScheduledTask.from_dict` / `to_dict` 仅在 `_load` / `_save` 内部用，正常。

### 死代码
- 三个目标模块内**未发现** `if False:` / `if 0:` 包裹块。
- 三个目标模块内**未发现** import 后未使用的符号（已逐个核对 `Path` / `field` / `Tuple` 均有使用）。
- `scripts/rtl_selfcheck.py:236` 的 `--branch` 是死参数（见上）。

### MGRS / UTM
**全仓 grep（`mgrs` / `military grid` / `utm zone` / `epsg:326xx` / `latlon to meters`）零命中。**

项目坐标栈现状：
- `orbit.py:61` 有 `geodetic_to_ecef`（lat/lon/alt → ECEF）；
- `pose.py:55-71` 有 `GPSData.latitude/longitude`、`ARProjector`；
- `astronomy.py` 有赤道坐标/地平坐标换算。

**没有任何 MGRS（Military Grid Reference System）或 UTM 格网参考实现**。如果产品定位包含 GIS 标注 / 地图叠层（pose.py ARProjector、sat-track skill 提到"GIS 标注可叠加在天空图上"），MGRS 是缺失能力——当前只能给十进制度，给不出"54S VB 12345 67890"这种战场/野外常用格网坐标。列为[缺失/建议]。

---

## 八、问题清单汇总

| # | 级别 | 位置 | 一句话 |
|---|------|------|--------|
| 1 | 真bug | satdump_integration.py:107-112 | live 超时后假报"后台运行中"，实际进程已被 kill |
| 2 | 真bug | satdump_integration.py:99 | live timeout=30s 对真实过境必然触发，从不真正接收 |
| 3 | 真bug | satdump_integration.py:33-42 | find_satdump 漏接 PermissionError，EACCES 时整个模块崩 |
| 4 | 真bug/安全 | satdump_integration.py:85-92 | satellite 无白名单，存在参数注入面（-开头值） |
| 5 | 空壳 | satdump_integration.py 全模块 | 依赖外部 SatDump，常态恒为"未安装"却仍注册为 MCP 工具 |
| 6 | 真bug | scheduler.py:268-270 | cron 任务不更新 next_run，每 30s 空转狂触发 |
| 7 | 空壳 | scheduler.py:40,167 | cron_expression 存了从不解析 |
| 8 | 建议 | scheduler.py:243 | tick() 公开可重入，无锁，与 _loop 并发竞态 |
| 9 | 建议 | scheduler.py:228 | 无重试/退避，瞬时失败要等满周期 |
| 10 | 真bug | meteor_sat.py:543-544 | EKF 把多普勒(Hz)当 vz(m/s) 观测，量纲错误 |
| 11 | 空壳 | meteor_sat.py:532 | EKF 用匀速 F，忽略月球引力，必然发散 |
| 12 | 空壳 | meteor_sat.py:306/336/346/385 | QPSK/Viterbi/CCDB/图像合成四个函数全是桩 |
| 13 | 孤儿 | meteor_sat.py:306/327/349/385 | 上述函数全仓零外部调用 |
| 14 | 重复漂移 | meteor_sat.py:339 vs demod.py:448 | descramble_ccdb 一处 no-op 一处真实现 |
| 15 | 建议/数据 | meteor_sat.py:248 | LRO norad_id=37349 疑似错误（实际 35248） |
| 16 | 建议 | __init__.py:68-92 | __all__ 只列 23 个，import 了 ~40 个，import * 丢符号 |
| 17 | 真bug | scripts/rtl_selfcheck.py:236 vs 138 | --branch 参数被硬编码 "q" 覆盖，死参数 |
| 18 | 建议 | scripts/rtl_fm_listen.py:53 | read_samples 持续空时无最大重试/总超时 |
| 19 | 缺失 | 全仓 | 无 MGRS / UTM 实现，坐标只有 lat/lon + ECEF |

**本轮新增真 bug 共 7 个（#1/#2/#3/#4/#6/#10/#17），空壳 5 个，孤儿/重复/缺失 4 个。** 未重复此前 57 个子 agent 已报告的问题。
