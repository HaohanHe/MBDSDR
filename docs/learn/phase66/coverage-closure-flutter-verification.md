# 零覆盖缺口清零 + Flutter 端首验 + 双解释器平权（phase66）

> 日期：2026-10-19  作者：MBDSDR 工程轮
> HEAD：本地 `main` = origin/main @ b0073ea（phase65 收尾）之上追加 2 笔（测试+缺陷修复 / 本报告）
> 性质：交付/复验报告。所有数字均为当日沙箱实测快照；不吹不贬，非能力标语。

## 1. 范围与触发

承接 phase65（云端再整合 + 3.11 兼容清扫）后的三项遗留：

1. 第三端（Flutter mobile）自建仓以来**从未实测**——沙箱无 Flutter SDK；
2. 覆盖扫描暴露 **7 个产品模块零行为测试**（R2 报告"95 个零覆盖"严重过期，
   合并口径实测仅 7 个，本轮全部补齐）；
3. AGENTS.md 基线 915 passed 需随新测试上档，且此前发现的套件基础设施
   缺陷（acceptance_run.sh STAMP 撞车）待修待归档。

## 2. Flutter SDK 安装与 mobile 端首验

沙箱网络分区照旧：`github.com`（git 协议）超时，GCS 直连 450KB/s（1.58GB
tarball 要约 58 分钟）。实测 Flutter 官方中国镜像
`storage.flutter-io.cn` 达 **17.3MB/s**（快 38 倍），一分钟下完。

| 项 | 值 |
|---|---|
| SDK | `/opt/flutter-3.47.7`（stable，framework abaf9c5237） |
| Dart | 3.13.5 |
| mobile/ 约束 | `>=3.9.0 <4.0.0` ✓ 满足 |
| 踩坑 | tarball 解压后 `flutter --version` 会 `git fetch --tags`（github.com 超时即 fatal）→ 手动 `git fetch --tags origin` 重试连上后本地解析 |

**mobile 端首验结果（搁置多年后首次真跑）**：

- `flutter pub get`：成功；
- `flutter test`：**384 passed / 0 failed**（58 个测试文件，52s）；
- `flutter analyze`：**No issues found**。

第三端从"未实测"变为全绿。phase65 报告中"未实测"的表述以此为准更新。

## 3. 覆盖扫描方法论（含一次数据污染教训）

三套件**各自独立干净跑**（不复用 `.coverage`，禁 `--cov-append`），
JSON 级合并（语句并集 / 执行行并集）：

| 套件 | 解释器 | 覆盖 JSON |
|---|---|---|
| mbdsdr_ai/tests | 3.11 + 3.12 | cov_ai_final.json |
| tests/ 根（含桌面 UI） | 3.12 | cov_root.json |
| tools/ | 3.11 | cov_tools.json |

**教训（归档，防再犯）**：pytest-cov 无 `--cov-append` 的跑会**覆盖**
`.coverage` 数据文件。曾因根套件覆盖跑（无 append）冲掉数据，之后 append 跑
生成的 JSON 实际只含最后两次数据——据此产出的"95 个零覆盖"名单大面积失真
（如 `test_vna_trace_math.py` 明明存在却被列零覆盖）。覆盖结论必须以
**独立干净数据 + JSON 级合并**为准，不得采信任何 append 链上的汇总数。

## 4. 本轮实证发现并修复的两个真缺陷

### 4.1 mobile_server.compute_passes：kwarg 名不匹配致过境计算恒空

```python
return orbit.visible_satellites(lat, lon, min_el=0)
# 实际签名：visible_satellites(observer_lat, observer_lon, observer_alt=0.0,
#                            min_elevation=0.0, satellite_type="all")
```

`min_el` 触发 TypeError，被 `except Exception` 吞掉只记 debug 日志——
**卫星过境推送恒返回空列表，整条链路是死代码**。实证：修复前
`compute_passes(39.9, 116.4)` → `[]`（log: "unexpected keyword argument
'min_el'"）；修复后 → 真实 1 颗可见卫星（含 azimuth/elevation/range 等字段）。
已改 `min_elevation=0` 并加回归测试 `test_compute_passes_uses_min_elevation_kwarg`
（spy 钉死调用 kwarg，拼错即失败）。同文件其余调用点检查无同类问题
（agent.py 的 `min_el` 是传给 `predict_passes` 的局部变量，位置传参，无碍）。

### 4.2 acceptance_run.sh：秒级 STAMP 撞车致顺序依赖测试必败

`STAMP="$(date +%Y%m%d_%H%M%S)"` 在测试连跑（亚秒级 subprocess）下撞车：
多个 run 共享同一 `LOG_DIR`，`raw.jsonl` 跨 run 累积，后一个 run 读到前一个
的步骤——`test_event_single_mode_sstv` **全套件跑必败、单跑必过**即此根因。
修法：纳秒 + PID 双保险
`STAMP="$(date +%Y%m%d_%H%M%S)_$(date +%N)_$$"`。修复后 tools/ 全套件
**132 passed / 4 skipped / 0 failed**（4 个 skip 为无硬件诚实 skip）。

## 5. 新增 126 项行为测试（8 文件，全部离线确定性）

| 文件 | 测试数 | 钉死的行为要点 |
|---|---|---|
| `test_cfo.py` | 18 | FFT 粗估 bin 级精度；Kay 精估 <1Hz；`search_hz` 抗强邻台；短输入返 0；correct_cfo 峰值回零；低 SNR 相干性门限稳健回退（`fine_applied is False`）；ppm 折算；参数化护栏 |
| `test_position_service.py` | 14 | 时钟注入；TTL 边界（严格大于才清）；轨迹 max_track 修剪；订阅者异常容错；GeoJSON `[lon,lat]` 坐标序；LineString ≥2 点才出；8×200 线程并发冒烟 |
| `test_optional_deps.py` | 6 | 五依赖全覆盖；available 与真实 import 能力一致（发行名→模块名映射，pyserial→serial）；SoapySDR 版本固定报 `unknown`；print_deps_status 冒烟 |
| `test_mimo.py` | 9 | 互相关约定实证：b 落后 a d 样本 → `time_delay_s = -d/fs`；`b = a·exp(jθ)` → `phase_diff_deg = -θ`（共轭口径）；无设备 `read_stream` 返 `{}` 不造假；不等长按 min 对齐 |
| `test_feature_manager.py` | 13 | 注册/重名覆盖；start 未知名返 False；状态机 idle→running→idle；start 抛异常置 error（不吞）；broadcast 只推运行中且故障隔离；describe 字段 |
| `test_module_graph.py` | 23 | 流读写校验；空 complex64 诚实空态；SoapySource 无 device_info 拒绝启动（拒绝伪造数据）；带宽钳位；去加重 RC 瞬态/状态连续；WFM 参数钉扎 SDR++ 标定值（250k/150k/50µs/19k 导频/57k RDS）；拓扑序 source 优先；单模块失败不拖垮全图 |
| `test_sdrpp_decoders.py` | 20 | 公开标准常量（RDS 1187.5bps/57kHz、APT 2400Hz/4160Hz/0.5s、POCSAG ±4.5kHz/0x7CD215D8/16/1200）；三解码器错误契约；POCSAG 汉明同步（≤4bit 容差、5bit 拒绝、512 窗、位置为窗起始下标）；注册表四工具；file handler 三路径 + spy 钉死数据/采样率透传 |
| `test_mobile_server.py` | 23 | 连接管理（未知连接 discard 幂等）；死客户端即时清除；ensure_ascii=False 中文不转义；坏 JSON 跳过；流中断 finally 注销；telemetry fix 局部更新（None 忽略）/heading 缺字段保旧；chat 回执；compute_passes kwarg 回归钉；pointing_loop 无 fix 不推/有 fix 按 interval 推；**真实 websockets 服务器+客户端 127.0.0.1 端到端握手** |

目标模块覆盖（mbdsdr_ai 套件，3.11 终版）：

| 模块 | 覆盖 | 模块 | 覆盖 |
|---|---|---|---|
| `cfo.py` | 76/77 = 98.7% | `feature_manager.py` | 85/88 = 96.6% |
| `position_service.py` | 72/72 = 100% | `module_graph.py` | 253/301 = 84.1% |
| `optional_deps.py` | 37/45 = 82.2% | `sdrpp_decoders.py` | 76/80 = 95.0% |
| `mimo.py` | 55/55 = 100% | `mobile_server.py` | 72/78 = 92.3% |

未覆盖行均为 `__main__`/`main()` 入口块（sdrpp_decoders 228-232、
mobile_server 111-115/119）。**合并口径总覆盖 69.0% → 74.5%**
（49905/66955）；零覆盖文件仅剩 3 个且均非产品逻辑：
`__main__.py`（入口）、`tests/exp_phase51_robustness.py` 与
`tests/exp_phase54_envelope.py`（一次性实验脚本）。

## 6. 全量复验矩阵（当日实测）

| 面 | 命令 | 结果 |
|---|---|---|
| mbdsdr_ai（3.11） | `pytest mbdsdr_ai/tests -q` | **1041 passed / 5 skipped / 0 failed** |
| mbdsdr_ai（3.12） | 同上 | **1041 passed / 5 skipped / 0 failed**（双解释器同数） |
| tests/ 根（3.12） | `pytest tests -q` | exit=0 零失败（phase65 基线 1490/4；mobile_server 变更不被根套件引用） |
| tools/ | `tools/acceptance_run.sh` 全套件 | 132 passed / 4 skipped / 0 failed（STAMP 修复后） |
| mobile | `flutter test` + `flutter analyze` | 384/384 全绿 + 零 issue |
| C++ | ctest | **本轮未触碰 C++，未重跑**（如实标注，不冒充复验） |

AGENTS.md 基线已随提交上档：915 → **1041 passed / 5 skipped**。

## 7. 测试侧发现并自修的问题

- `test_optional_deps.py` 首版在 3.12 失败：`_actually_importable` 直接
  `import pyserial`（发行名），而模块名是 `serial`——3.11 上因未安装碰巧
  双 False 通过，3.12 安装后现形。已加发行名→模块名映射表并将捕获放宽为
  `Exception`（导入期任何异常都算不可用，口径更诚实）。
- `test_mobile_server.py` 流中断替身首版逻辑错（单消息时循环不进第二次
  迭代，`raise_at` 永不触发）→ 改为"消息发完即断流"，符合真实连接语义。
- `test_module_graph.py` 清删了多余的 `_is_abstract` 防御分支
  （DemodModule 非抽象类，直接实例化即可）。

## 8. 遗留与下一步

- **PAT 轮换**：本会话推送通道多次使用用户 PAT（Git Data API 重放），
  明文暴露过若干次，**提醒用户尽快轮换**；
- mobile 端全绿结果已归档于此，phase65 报告相关表述以本节为准；
- 剩余低覆盖模块（单套件 <50% 的 111 个中有产品逻辑者，如 ysf_demod、
  dmr_demod、sstv_decoder 等大件）留待后续按需补齐，不在本轮承诺范围；
- C++ 面无改动，ctest 维持 phase65 结论。
