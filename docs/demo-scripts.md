# M1 教学链路演示脚本（三条）

> 对应 PRD `docs/PRD.md` 第 975 行出口条件第 4 条：三条演示脚本留存
> （有硬件 / 无硬件 / 断网），每条 12 分钟内。
>
> 入口：`scripts/demo_chain.py`。只读本地数据，nohw/offline 档不发起网络请求；
> hw 档只做设备枚举，不打开设备。

## 三条命令

```powershell
# 无硬件档：无 SDR、无 AI key
python scripts/demo_chain.py --mode nohw

# 断网档：等价于先设 MBDSDR_OFFLINE=1 再运行
python scripts/demo_chain.py --mode offline

# 有硬件档：先枚举 SDR；缺设备时如实退出 1
python scripts/demo_chain.py --mode hw
```

退出码与 `scripts/sdr_diag.py` / `scripts/system_diag.py` 一致：

* `0`：演示流程走完（nohw / offline 的正常出口，hw 有设备时同样为 0）；
* `1`：hw 档缺设备，等价 `sdr_diag.EXIT_NO_DEVICE`，不自创退出码。

无设备环境想验证 hw 档的诚实出口，可显式关闭硬件枚举：

```powershell
$env:MBDSDR_NO_HW = "1"
python scripts/demo_chain.py --mode hw
```

## 12 分钟节奏表

每一步都打印：标题、看哪个界面/面板、期望看到什么、缺条件时的诚实空态、
证据（命令或文件）。预计分钟数为脚本内写死的口径，实际耗时通常远小于表值。

| 步 | 内容 | nohw | offline | hw |
| --- | --- | --- | --- | --- |
| 1 | 时间引擎与站址：JD/GMST、站址来源与空态 | 1.0 | 1.0 | 1.0 |
| 2 | 星表/星官：HYG 星数与 6.5 等内星数、中国星官区域数 | 0.5 | 0.5 | 0.5 |
| 3 | 过境与多普勒：缓存 TLE 的 SGP4 过境与视线速度 | 1.0 | 1.0 | 1.0 |
| 4 | 选星→调谐：NORAD/COSPAR、公开下行频点、指向；无 SDR 降级说明 | 1.0 | 1.0 | 1.5 |
| 5 | 录音/回放：录制目录与随仓真实 OTA 样本哈希校验；无 SDR 说明 | 1.0 | 1.0 | 1.5 |
| 6 | AI 协商：无 key 返回 `api_key_not_set`，离线返回 `llm_offline` | 1.0 | 1.0 | 1.0 |
| 7 | 断网行为：联网工具显式拒绝、本地能力可用 | 1.0 | 1.0 | 1.0 |
| 8 | 收尾检查：步骤数、预计/实际耗时、诚实边界、退出码 | 0.5 | 0.5 | 0.5 |
| 合计 |  | 7.0 | 7.0 | 8.0 |

三条都低于 12 分钟上限；表内分钟数就是脚本头部打印的「预计总时长」口径。

## 每步看哪里

| 步 | 界面/面板 | 期望看到 |
| --- | --- | --- |
| 1 | 天空视图顶部时间条 + 设置页「站址」 | UTC 与墙钟口径、站址来源 |
| 2 | 天空视图 + 星卡/查询面板 | 星表星数、星官区域数可读 |
| 3 | 过境面板 + 卫星信息卡 | UTC 升/中/落、最高仰角、TLE 历元与新鲜度 |
| 4 | 卫星信息卡 + 接收/调谐面板 + 状态卡 | NORAD/COSPAR、公开下行频点、指向；无 SDR 保持未连接空态 |
| 5 | 录制中心 + 回放面板 | 录制目录文件、回放素材哈希校验 |
| 6 | AI 面板 + 工具协商记录 | 有 key 可协商；无 key 明确拒绝 |
| 7 | 设置页网络开关 + AI 面板 + 星表/过境面板 | 联网工具拒绝原因、本地能力可用 |
| 8 | 终端输出 + 本文件节奏表 | 8 步齐全、耗时 ≤12 分钟、退出码 0 |

## 数据口径

* 时间：`mbdsdr_ai/skyengine/jtime.py`，JD 与 IAU 1982 GMST 现算。
* 星表：`mbdsdr_ai/data/full_sky_hyg_v41.npz`，星数来自星表本身。
* 星官：`mbdsdr_ai/data/xingguan_chinese.json`，区域数由数据校验器读出。
* 过境：`mbdsdr_ai/orbit.py` 的 SGP4 传播，TLE 只读本地缓存
  （`MBDSDR_TLE_CACHE_DIR` 可覆盖），不联网补数。
* 频点：`mbdsdr_ai/data/sat_links.json` 的公开下行目录，按 NORAD 编号匹配。
* 回放：`tests/fixtures/real_fm_88m1/` 的真实 OTA 样本，与
  `provenance.json` 里的 sha256 对照。
* 断网：`mbdsdr_ai/net_policy.py`、`mbdsdr_ai/local_ai_probe.py` 与
  `mbdsdr_ai/agent.py` 的 `web_fetch_url` 拒绝分支。

## 诚实边界

* 本机无 GNSS、无相机时，依赖真机定位或相机叠加的界面只能演示空态；
  演示脚本不会编造本站坐标或传感器读数。
* 无 SDR 档的「调谐」「录音」是降级说明，不构成有硬件演示；脚本不会
  声称调谐成功或录到样本。
* 无 TLE 缓存时不计算过境，打印「无 TLE」并说明未联网；不拿内置合成
  TLE 或示例坐标顶替。
* 无 AI key 时返回 `api_key_not_set`；断网档返回 `llm_offline`，两条都
  不发请求。
* 有硬件档当前未在真机 SDR 上真跑（本机无设备）；`hw` 档只做设备枚举
  与操作口径，缺设备时按 `EXIT_NO_DEVICE=1` 退出。

## 复跑

```powershell
python -m pytest tests/test_demo_chain.py -q
python scripts/demo_chain.py --mode nohw
python scripts/demo_chain.py --mode offline
$env:MBDSDR_NO_HW = "1"; python scripts/demo_chain.py --mode hw
```
