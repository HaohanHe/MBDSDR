# 交接单：2026-11-06 深夜 SDR 后端 / 回放链只读审计（本线未代改部分）

> 来源：Explore 只读审计（thoroughness: very thorough），本线逐条核过证据位置。
> 本线**已修并落库**的是 `bd2f8878`（配置/增益/布局三域）与下一提交
> （`sat_passes.py` 窗口硬上界 + 擦边补点、`skyengine/satellites.py` 升落二分）。
> 下列条目按「谁在途 / 谁需要产品决策」分组，不要重复报、也不要跨线代改。

## A. 必须交给「baseband_io.py 在途线」——P0

**`load_iq()` 把 cu8 录制静默按 cf32 解读，IQ 数据整体作废**

- 证据：`mbdsdr_ai/baseband_io.py:353-355`（`fmt` 白名单只有 wav/cs16/cf32，
  `.cu8` 与 sidecar `format:"cu8"` 一律回落 cf32）、`:43-50`、`:250`。
- 录制端确实会写 cu8：`mbdsdr_ai/sdr_backend.py:1127-1131`、`:1167`；
  桌面入口 `desktop/main_window.py:8714`。
- 现象：按 8 字节/样本解析 2 字节/样本的 cu8（2 MB 文件被读成 1/4 样本，
  数值全乱），无任何警告；用户「回放录制」得到垃圾频谱且不报错。
- 修法：把 cu8 纳入白名单，按 2 字节/样本 + `(x-127.5)/127.5` 解码；
  **未知格式应报错而不是回落 cf32**。
- 同文件 P2：无 sidecar 时 `baseband_io.py:350-351,368-370,374` 编造
  `sample_rate=2.4e6` / `center_freq=0` / `gain=0` 并据此算精确时长。
  建议 `sample_rate=None` + 时长 `None`，与本模块 `:340-342` 的纪律一致。

## B. 空闲文件、本线未做（留给下一轮或「操作逻辑线」）

| 级别 | 位置 | 一句话 |
|---|---|---|
| P1 | `mbdsdr_ai/rtltcp_client.py:477-499` | `center_freq` setter 不检查 `_send_cmd` 返回值就写 `self._center_freq`；`sdr_backend.py:2182-2195,2325-2335` 的三次「写后回读」读的正是这个缓存 → 断线时「调谐成功」是假的。 |
| P1 | `mbdsdr_ai/rtltcp_client.py:736-746` + `sdr_backend.py:429,2248` | 注释写明「吸附后写回 status 避免显示请求值」，但基类 `_resolve_applied_sample_rate()` 未覆写又写回请求值（2.000M vs 实为 2.048M），DSP `fs_in` 与真实流率不符。 |
| P1 | `sdr_backend.py:2733-2749,3564-3575` | HackRF/USRP 的 `connect()` 无初始化下发、无硬件回读，status 显示 98 MHz / 8 MS/s / 0 dB 全是软件默认值（RTL/RtlTcp/Pluto 已有初始化序列，见 `:1893-1931,4014-4017`）。 |
| P1 | `sdr_backend.py:4484-4514` | `FileIQBackend.connect()` 读失败全静默 `return False` 不写 `status.error`；录制端支持的 wav/csv 回放端不认，「录完即可回放」是断的。 |
| P1 | `sdr_backend.py:4772,4847-4880` | `SDRBackendManager` 设备表是启动时一次性快照，无 TTL；`sdr_list_devices` 永远返回旧表，热插拔后 MCP/agent 面连不上新设备（桌面走模块级实时枚举，不受影响）。 |
| P2 | `mbdsdr_ai/playback.py:524-525,580-585,625-647` | `set_sample_rate` 会校验拒绝，但 `set_frequency` 无条件接受并更新 `_freq`，`status.error` 仍为空 → 回放时调旋钮，频谱/解码按错误中心频率解释；docstring 承诺的告警没写。 |
| P2 | `mbdsdr_ai/sat_passes.py:647,674-677` | `predict_upcoming_passes(max_sats)` 按**过境条数**截取，与契约「前 N 颗卫星」不符；一颗多过境卫星会占满配额。应先按星分组取峰值前 N 颗再展开。 |
| P2 | `sdr_backend.py:279-290,603-611,1881-1888,2619` | `disconnect()` 只翻 connected/link_state，`rssi_db`/`snr_db`/`uptime`/`samples_read` 保留上次会话值；重连 `_samples_read` 不清零。 |
| P2 | `sdr_backend.py:2371-2404,3365-3388` | `set_agc`/`set_bandwidth` 子类覆写在硬件失败后不回滚 status（基类 `:311-331,412-435,460-477` 是回滚的，子类纪律不一致）。 |
| P2 | `sdr_backend.py:4642-4735,5320-5390` | `NetworkIQBackend` 完整实现却没进 `_discover()`/工厂；`connect()` 不设 `status.connected`，`get_status()` 新建 SDRStatus，`set_*` 返回 None 违反 bool 契约，断流尾数永久丢弃。 |
| P2 | `rtltcp_client.py:194-197` + `sdr_backend.py:609-616` | rtl_tcp 是单客户端（backlog=1），断连后无冷却立即重连会吃名额；真机基线见 `docs/learn/agent-onboarding-runbook.md` §3.5。 |

## C. 需要产品决策，别直接改

**TLE 过期门只接在 orbit 路径**：`mbdsdr_ai/tle_integrity.py:267` 的
`pass_eligible` 唯一门控在 `orbit.py:509`；`sat_passes.py:230-388`、
`sat_tracker.py:632-662` 与 `controller.py:1200` 完全没接，后者只把
tier/age_days 当标注透出（`:1229-1234`）。内置 TLE 抓取日
`sat_tracker.py:80` 是 2026-09-25，至今 42 天，桌面过境列表/MCP 仍输出
精确到秒的 AOS/LOS。

**不建议直接「拒绝输出」**：那会让过境列表一夜之间全空，而用户没有立刻
换 TLE 的手段。建议先做「时刻精度降级 + 显式过期角标」，等有自动 TLE
更新链路再上硬门。这条属于产品口径，先问用户。

## D. 验证过、确认没问题的项（不要重复查）

- 基类三个 setter 失败均回滚旧值并写 `status.error`：`sdr_backend.py:311-331,412-435,460-477`。
- RTL 本地 USB 写频是真下发 + 三次回读验证 + 失败置故障态；采样率吸附值
  有 `_resolve_applied_sample_rate` 正确回写：`sdr_backend.py:2180-2212,2248-2252`。
- RTL 调谐器枚举缓存 30s 成功 TTL / 5s 失败冷却，设备数变化即整表失效：
  `sdr_backend.py:1449-1452,1464-1469,1613-1616`。
- `sat_passes` 时间单位全链自洽（Time + 天、rr km/s→m/s、AU→km），无
  Unix/JD/分混用；2s 细采样的峰值误差 <0.001°，不必插值。
- `playback.py:322-347` 循环回绕正确、`sdr_backend.py:4525-4543` 最后一帧不丢；
  `IQPlayback.open()` 对 meta 损坏/空文件/截断/非 cf32 一律诚实失败并落 error_code。
- 站址为 None 时不拿 (0,0) 冒充：`sat_tracker.py:533-539,645-648`；
  TLE 新鲜度 tier/age/accuracy_risk 标注确实生成并透出。
- 录制 sidecar 的 `format/sample_rate/center_freq` 与 FileIQBackend 读取键名对应。
- 环形缓冲生产者/消费者/clear 同锁，GUI 换频时无数据竞争：`sdr_backend.py:1301,1341,1378-1383`。
