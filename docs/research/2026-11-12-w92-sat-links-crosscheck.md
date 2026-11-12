# W92 卫星发射机机器数据源：人工 8 星对照

日期：2026-11-12
范围：`mbdsdr_ai/data/sat_links.json` 人工目录 8 星 9 条下行 vs SatNOGS DB 机器发射机库。
结论先说：**9/9 人工链路在机器库精确命中，频率偏差 0 Hz；8/8 卫星在机器库都有记录；没有「机器库根本没有」的星。** 机器状态与高频边界存在缺口，逐条写在下面；人工频率一条未改。

原始件（E1）：`docs/evidence/satnogs-transmitters-20261112.json`，SHA-256 `8fa80f66a465e780f0f334c335c955a0b4958b807ae7af043cc4658477923d73`，抓取于 `2026-11-12T10:58:24Z`，8 颗星共 83 条发射机记录，逐星原样保存 `response`，未裁剪。

## 1. 数据源与 schema

### 1.1 数据源（本次实测可通）

- 接口：`https://db.satnogs.org/api/transmitters/?format=json&satellite__norad_cat_id=<NORAD>`，无需认证。
- 本次 8 颗星 8 次请求全部 HTTP 200，0 次失败；83 条记录。
- 参考实现：SDRangel `f4exb/sdrangel@1ec7cea`，`plugins/feature/satellitetracker/satnogs.h:28-41` 的发射机字段与 `:49-52` 的拉取入口。
- 机器记录字段：`uuid/description/uplink_low/uplink_high/downlink_low/downlink_high/mode/baud/status/service/updated`。**没有独立带宽字段**；`downlink_low` 与 `downlink_high` 是频率边界，本次多数记录两端相等（单点），部分记录 `downlink_high` 为 `null`。

### 1.2 落盘缓存

- 路径：`MBDSDR_CACHE_DIR/sat_links_machine.json`；未设该环境变量时为 `~/.mbdsdr/cache/sat_links_machine.json`。
- 缓存 schema：`mbdsdr sat_links machine cache v1`；顶层含 `_source/_source_url/fetched_at/fetched_at_utc/satellites/failures`，每星含 `norad_cat_id/key/satnogs_sat_id/fetched_at/state/transmitters`。
- 写盘为临时文件 + `os.replace` 原子替换；**本轮全部目标失败时不写盘，旧缓存原样保留**。
- 运行时只读缓存，零网络；无缓存/断网/缓存结构不符都回退人工目录，不把目录变成空表。

### 1.3 链路 schema 的可选增量

人工字段 `direction/freq_hz/mode/baud/note` 全部保持原义。机器侧只作为可选增量挂在链路上：

| 字段 | 含义 | 来源 |
|---|---|---|
| `low_hz` | 下行频率下边界 | `downlink_low` |
| `high_hz` | 下行频率上边界；源为 `null` 时**不填不猜** | `downlink_high` |
| `status` | `active/inactive`，观测驱动，非开关真值 | `status` |
| `service` | 业务类别 | `service` |
| `source` | 本链路增量的来源，固定 `satnogs` | 本模块 |
| `machine` | `uuid/mode/baud/updated/description`，供回查 | 源字段 |

条目级新增 `norad_cat_id`（机器身份）与 `_machine`（数据龄、匹配数、`conflicts`、`machine_only`、`last_refresh_failure`）。人工文件不写机器频点，机器值也不覆盖 `freq_hz/mode/baud/note`；频率有偏差时只记 `_machine.conflicts`。

## 2. 人工 9 条链路逐条对照（原始件：`docs/evidence/satnogs-transmitters-20261112.json`，SHA-256 同上，抓取于 2026-11-12T10:58:24Z）

| 人工键 | 卫星 | 人工频点/体制 | 机器命中记录 | 机器边界 | 机器体制/baud | 机器状态 | 结论 |
|---|---|---|---|---|---|---|---|
| `noaa_15` | NOAA-15 | 137.620000 MHz / apt | APT | 137.620000 MHz（高低相等） | APT / 未给 | inactive | 频率精确一致 |
| `noaa_18` | NOAA-18 | 137.912500 MHz / apt | APT Downlink | 137.912500 MHz（高低相等） | APT / 未给 | inactive | 频率精确一致；同频另有 FSK「APT IQ Recording」active，按体制配到 APT |
| `noaa_19` | NOAA-19 | 137.100000 MHz / apt | APT Downlink | 137.100000 MHz（高低相等） | APT / 未给 | inactive | 频率精确一致 |
| `meteor_m_n2` | METEOR-M 2 | 137.100000 MHz / lrpt 72000 | LRPT 1 | 137.100000 MHz（高低相等） | LRPT / 72000 | active | 频率精确一致；机器另有 LRPT 2 137.900000 MHz inactive，未采用 |
| `iss_zarya` | ISS | 145.800000 MHz / nfm | Mode V/V FM (crew R2+3) | 145.800000 MHz，高边界未给 | FM / 未给 | active | 频率精确一致；高边界未取到 |
| `iss_zarya` | ISS | 145.825000 MHz / packet 9600 | Mode V APRS | 145.825000 MHz，高边界未给 | AFSK / 未给 | active | 频率精确一致；机器同频另有 APRS inactive 记录 |
| `funcube_1` | FUNCUBE-1 | 145.935000 MHz / bpsk 1200 | BPSK Telemetry | 145.935000 MHz（高低相等） | BPSK / 1200 | active | 频率精确一致 |
| `ao91_fox1b` | AO-91 | 145.960000 MHz / fm | Mode U/V FM Voice | 145.960000 MHz，高边界未给 | FM / 未给 | active | 频率精确一致；同频另有 DUV TLM active |
| `so_50` | SO-50 | 436.795000 MHz / fm | Mode V/U FM Voice CTCSS 67.0 Hz | 436.795000 MHz，高边界未给 | FM / 未给 | active | 频率精确一致 |

汇总：人工 9 条链路，机器精确命中 9 条，频率偏差 0 Hz，`conflicts` 0 条；8/8 卫星在机器库都有发射机记录。

## 3. 对不上的地方：逐条原因

频率层面没有对不上的条目。以下是不一致或缺口，按原因分类：

1. **NOAA-15/18/19 的 APT 机器状态为 `inactive`（3 条）。** SatNOGS 的 `status` 由观测/维护记录驱动，表示该条目最近是否被观测使用，不等于发射机已关闭；NOAA APT 仍是标准下行。**不据此改人工频点，也不把状态写成「不可用」。**
2. **NOAA-18 同频两条：`APT Downlink` inactive 与 `APT IQ Recording` active（FSK）。** 后者是观测站的 IQ 记录条目，不是 APT 下行体制。匹配规则改为「体制一致优先、active 次之」，人工 `apt` 链路配到 `APT Downlink`，不因另一条 active 而改体制。
3. **4 条链路机器 `downlink_high` 为 `null`：ISS 145.800000 MHz、ISS 145.825000 MHz、AO-91 145.960000 MHz、SO-50 436.795000 MHz。** 如实记「高边界未取到」，只保留单点；不把 `null` 当 0，不猜带宽。`identify_by_freq` 对这 4 条退回人工频点 ± 容差。
4. **体制命名差异，不算频率不一致。** 人工 `apt/nfm/packet/bpsk/lrpt/fm` 与机器 `APT/FM/AFSK/BPSK/LRPT` 大小写或叫法不同；只在同频多条时用于匹配排序，人工 `mode` 原样保留。
5. **METEOR-M 2 的 137.900000 MHz（LRPT 2，inactive）没有进人工目录。** 人工注记已说明信道历史在 137.100000/137.900000 MHz 间调整过；机器数据确认 137.100000 MHz（LRPT 1）为 active，支持人工当前取值，不改。
6. **没有「机器库根本没有」的星。** 8 颗星在机器库均有记录；人工 9 条链路也都有对应机器条目。

## 4. 机器库有、人工目录没有：74 条（不自动并入）

逐星机器独有条目数（原始件同上）：

| 卫星 | 机器独有条目数 | 机器总条目 | 代表条目（频率/体制/状态） |
|---|---|---|---|
| NOAA-15 | 6 | 7 | 137.350000 MHz DSB active；137.500000 MHz APT inactive；1702.500000 MHz HRPT active |
| NOAA-18 | 7 | 8 | 137.350000 MHz DSB active；137.912500 MHz FSK active（IQ 记录）；1707.000000 MHz HRPT active |
| NOAA-19 | 7 | 8 | 137.932000 MHz FM active；137.770000 MHz DSB active；1698.000000 MHz HRPT active |
| METEOR-M 2 | 2 | 3 | 1700.000000 MHz HRPT active；137.900000 MHz LRPT inactive |
| ISS | 48 | 50 | 437.800000 MHz SSTV active；437.800000 MHz FM 中继 active；145.800000 MHz SSTV active；S 波段多条 |
| FUNCUBE-1 | 2 | 3 | 145.950000–145.970000 MHz USB 线性转发器 active；145.815000 MHz CW active |
| AO-91 | 1 | 2 | 145.960000 MHz DUV TLM active |
| SO-50 | 1 | 2 | 149.025000 MHz FM TLM active |

处理方式：只通过 `sat_links.machine_only_links()` 只读暴露，**不写回人工目录、不自动注入 `identify_by_freq`**。理由：

- 其中有 137.100000–437.800000 MHz 之外的 S/C/Ku 波段、Soyuz/SpaceX/EMU 等非「听星」业务，直接并入会把目录变成噪声；
- 有历史/临时条目（如 ISS 的 `Tomsk-TPU-120`、`Tanusha FM`、`TEMP Off-Frequency SSTV`），需要逐条人工裁定；
- FUNCUBE-1 的 145.950000–145.970000 MHz 线性转发器与 SO-50 的 149.025000 MHz TLM 是本次最值得后续人工评估的两条增量，但 W92 不擅自扩表。

## 5. 诚实边界

- 本次 8 次请求全部成功；若失败，报告与状态文本会写「未取到」并保留人工目录，不写空表。离线模式（`MBDSDR_OFFLINE=1`）不发请求，状态显示「离线模式」。
- 发射机接口不返回卫星名；缓存里的 `satnogs_name` 留空，不以人工 `norad_name` 冒充机器字段。NORAD 身份只做键。
- `downlink_high` 为空就写「高边界未取到」，没有用 `downlink_low` 补齐成假区间。
- 机器 `status` 只作参考，不当作发射机开关真值，也不据此删改人工条目。

## 6. 复跑

- 离线复算（读已提交快照，不联网）：`python -m pytest mbdsdr_ai/tests/test_sat_links_machine.py -q`，其中 `test_snapshot_crosscheck_matches_report_numbers` 钉扎 9/9 命中、0 冲突、74 条机器独有。
- 现场重拉（会写缓存）：`python -c "from mbdsdr_ai import sat_links as S; print(S.refresh_machine_links())"`。
- 人工目录本身未改频点；本次仅新增 `norad_cat_id` 与 `_machine` 元说明，见 `mbdsdr_ai/data/sat_links.json`。
