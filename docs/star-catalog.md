# 全天星表与视场 LOD（`star_catalog`）

> 本篇结论未经 commit 取证，引用前须复核（判据见 `scripts/verify_claims.py`；补齐四元组 `<结论> — <hash> — <path>:<line> — <改动后字面量>` 后可摘除本行）。

> 结论先行：本机已有完整可用的 **HYG Database v4.1** 源表；已生成随仓
> 分发的衍生星表 **83,479 颗（V ≤ 9.0）**，许可 **CC BY-SA 4.0**。
> 星体是固定的天球数据，动的是视场；`mbdsdr_ai/skyengine/star_catalog.py`
> 提供「给定视场 + Bortle + 时刻/站点 → 本次该画的星集合」的稳定接口。
> 加载即校验来源与数据 SHA；请求超过 9.0 等会显式标记；wheel/sdist 已包含
> 星表。未改 `desktop/`，未联网，未生成任何假星。

---

## 1. 数据来源、许可与规模

### 1.1 源表

| 项 | 值 |
| --- | --- |
| 名称 | HYG Database v4.1 |
| 作者/维护 | David Nash / Astronomy Nexus |
| 来源 | https://github.com/astronexus/HYG-Database |
| 许可 | **CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/） |
| 本机文件 | `D:\mbdsdr\scratch\hygdata_v41.csv`（33 MB，**不随仓分发**） |
| 原始行数 | 119,626 行（含 `id=0` 的 Sol / 太阳） |

### 1.2 随仓衍生表

| 项 | 值 |
| --- | --- |
| 文件 | `mbdsdr_ai/data/full_sky_hyg_v41.npz`（约 1.9 MiB） |
| 星数 | **83,479** |
| 筛选 | 剔除 Sol，保留 V ≤ **9.0**（见 §3 的口径） |
| 坐标系 | J2000/ICRS（HYG 原始发布坐标，未做自行传播） |
| 许可 | 衍生数据同样按 **CC BY-SA 4.0** 分发；代码仍为 MIT |
| 来源指纹 | `meta.source_file_sha256` 记录源 CSV 的 SHA-256 |
| 数据指纹 | `meta.data_sha256` 记录全部星表数组的规范化 SHA-256 |
| 覆盖声明 | **本表只覆盖 V ≤ 9.0**；更深星需 Gaia DR3 / Tycho-2，不在表内 |

**归属声明（数据文件 `meta` 键内同样保存）**：

> HYG Database v4.1 by David Nash / Astronomy Nexus, licensed under
> CC BY-SA 4.0. This file is a derived subset.

### 1.3 为什么不是“假星”

- 每一行都来自 HYG v4.1 的真实记录，带唯一 `hyg_id`；`id=0` 的 Sol 被显式剔除；
- 生成脚本 `mbdsdr_ai/skyengine/build_full_sky_catalog.py` 可复现（见 §5）；
- 测试用 `mbdsdr_ai/tests/fixtures/hyg_v41_reference_sample.csv` 的 14 颗
  HYG 原始行逐字段对拍（`test_known_stars_match_hyg_reference_sample`）；
- 本机存在 HYG 源 CSV 时，`test_catalog_matches_local_hyg_source_when_available`
  对**全部 83,479 行**逐行核验 id 集合与 ra/dec/mag，确保不多、不少、不改值
  （CI/无源环境自动 skip）；
- 文件缺失时抛 `FileNotFoundError`，**不静默回退到任何内置假表**。

### 1.4 数据完整性守卫（加载即校验）

生成时对全部数组做规范化 SHA-256（键名排序 + dtype + shape + 原始字节），
写入 `meta.data_sha256`；`load_catalog()` 每次加载都会：

1. 校验 `meta` 存在且包含 source/许可/URL/`source_file_sha256`/`data_sha256`
   等必需字段；缺失 → **`CatalogMetadataError`**（拒绝静默加载）；
2. 重算数组 SHA-256，与 `meta.data_sha256` 比对；不一致 →
   **`CatalogIntegrityError`**（防止手改数据却不改 meta）；
3. 校验 `meta.star_count` 与 `vmag` 长度一致。

对应测试：`test_data_digest_matches_meta`、`test_tampered_catalog_rejected`、
`test_missing_required_meta_rejected`（参数化覆盖 URL/许可/两个 SHA）。
若本机有 HYG 源 CSV，`test_catalog_matches_local_hyg_source_when_available`
还会重新计算源文件 SHA-256，与 `meta.source_file_sha256` 核对。

新增测试共 **32 项**（星表/守卫 29 + 打包 3），全部通过（2026-11-13 于 `97fc5fc8` 复跑相关星表文件 `tests/test_sky_catalog_sync.py tests/test_sky_full_catalog.py` → 15 passed；原 32 项的逐项映射未留存，按文件级复核）。

### 1.5 字段

npz 键（数组均按 `vmag` 升序，除 `dec_order` 外）：

| 键 | 类型 | 含义 |
| --- | --- | --- |
| `hyg_id` | int32 | HYG v4.1 行号（唯一，>0） |
| `hip` | int32 | Hipparcos 号；缺省 -1 |
| `proper` | Unicode | IAU proper name；空串=无名 |
| `ra_deg` | float32 | J2000 赤经（度，0..360） |
| `dec_deg` | float32 | J2000 赤纬（度） |
| `vmag` | float32 | V 目视星等 |
| `bv` | float32 | B-V 色指数；NaN=未知 |
| `dist_pc` | float32 | 距离（秒差距）；NaN=未知 |
| `spect` | Unicode | HYG 光谱型；空串=未知 |
| `con` | Unicode | 星座缩写 |
| `dec_order` | int32 | 按 Dec 升序的索引（视锥裁剪用） |
| `mag_bin_edges` | float32 | 0.5 等分档边界（-1.5..9.0） |
| `mag_bin_start` | int32 | 每档在升序数组中的起始偏移 |
| `meta` | Unicode(JSON) | 来源/许可/指纹/单位/生成时间 |

---

## 2. 坐标链：只复用，不另造

`star_catalog` 不实现第二套天文坐标。批量路径用与
`mbdsdr_ai/celestial_geometry.py` **完全相同的矩阵**：

```
M = R_altaz(jd_ut1, 站点) @ N(jd_tt) @ P(jd_tt)
v_altaz = M @ v_j2000
```

- `P`：`precession_matrix`（IAU 2006/P03 岁差）
- `N`：`nutation_matrix`（IAU 2000B 章动）
- `R_altaz`：`GroundStation.equatorial_to_altaz_matrix`（GMST + 站点旋转）
- 批量只是 `v_j2000 @ M.T`；测试逐星与既有单星 `j2000_to_altaz` 对拍
  （`test_selection_uses_existing_coordinate_chain`，误差 < 1e-6°）。

地平约定与既有代码一致：`+x=北, +y=东, +z=天底`，`alt<0` 不画。

---

## 3. 极限星等：同一条基线 + 有界视场增益 + 逐星消光

### 3.1 基线（与 `stars.star_draw` 完全同一公式）

```python
naked_eye_limit_mag(bortle) = 6.5 - 0.35 * (int(bortle) - 3)
```

以 **Bortle 3 = 6.5 等**为基线，每档 -0.35 等，`1..9` 全程单调：
Bortle 1 = 7.2、2 = 6.85、3 = 6.5、9 = 4.4。旧实现用
`max(0, bortle - 3)` 截断，把 Bortle 1/2/3 夹成同一个 6.5 等，三档星数
完全相同（实测 1695 = 1695 = 1695），暗空段光害设置失效；2026-11-11 按
P1-46 放开为同一线性律（不是第二套阈值，仍是这一条公式）。

### 3.2 视场深度增益（LOD 经验式，非光度学模型）

```python
detail_gain_mag(fov) = clamp(0.5 * log2(30 / fov), 0, 1.5)   # fov 单位：度
limit_magnitude(fov, bortle) = min(9.0, naked_eye_limit_mag(bortle) + detail_gain_mag(fov))
```

| 视场 | 深度增益 | Bortle 3 极限 |
| --- | --- | --- |
| 120° | 0.0 | 6.5 |
| 30° | 0.0 | 6.5 |
| 15° | 0.50 | 7.0 |
| 5° | 1.29 | 7.79 |
| ≤2.8° | 1.50（封顶） | 8.0 |

解释：宽视场满屏时保持裸眼基线，不会“凭空冒星”；放大后像素张角变小、
暗星不再挤成一团，才逐层露出更暗的星。**这是渲染 LOD 规则**，不是
测光模型；需要别的口径时显式传 `limit_mag=` 即可完全绕过。

### 3.3 大气消光（逐星，同 `stars.extinction_delta_mag`）

```python
eff_mag = vmag + k / sin(alt)     # k=0.2，Δm 上限 3.5，alt 下限 0.5°
```

矢量化实现只复用 `stars` 里的常量 `EXTINCTION_K / _EXTINCTION_MAX_DM /
_EXTINCTION_MIN_ALT`；测试逐点锁定与标量函数相等
（`test_extinction_vector_matches_scalar`）。

因此：

```
天顶极限 = limit_magnitude(fov, bortle)
某仰角等效极限 = 天顶极限 - Δm(alt)   # effective_limit_mag()
```

星体进入结果的条件是 `alt >= -0.6`、在视锥内、`eff_mag <= 天顶极限`。

> 地平门为什么是 **−0.6°** 而不是 0°（2026-11-06 改）：绘制层按**视高度**落笔，
> 地平线处大气折射约 34′，几何高度 0° 附近那一圈星在屏幕上仍在地平线之上；
> 旧门在 0° 就把它们裁掉，地平线上会出现一条「无星带」。DSO 层一直是 −0.6°，
> 两层现在同口径（`star_catalog._HORIZON_GATE_DEG`）。

### 3.4 视锥与预算

- 视锥半角 = `fov / 2 + margin_deg`（默认余量 1°，给星点/标签边缘留空间）；
- 先用 J2000 赤纬带 `|dec - dec_center| <= r` 裁剪，再算精确角距；
- `max_count`（默认 20,000）是硬预算：超出时**保留有效星等最亮的 N 颗**，
  返回 `truncated=True` 与截断前 `available`，**不随机丢星**。

---

## 4. 分层与接口

### 4.1 分层

- 全表按 `vmag` 升序存盘；
- 0.5 等一档：`mag_bin_edges` + `mag_bin_start`，`-1.5 … 9.0` 共 21 档；
- `count_below(m)` 用二分，等价于“取前 N 颗”；
- 另存 `dec_order` 支持视锥快速裁剪。

### 4.2 稳定签名（界面层直接调用）

```python
from mbdsdr_ai.skyengine.star_catalog import (
    limit_magnitude, get_catalog, select_for_view, StarSelection,
)

sel: StarSelection = select_for_view(
    station,              # celestial_geometry.GroundStation
    jd_ut1,               # UT1 儒略日（与既有时间链一致）
    center_az, center_alt,# 视场中心（度）
    fov_deg,              # 视场直径（度）
    bortle=3,             # 与 star_draw 同一口径
    limit_mag=None,       # None=按视场/Bortle 推导；可显式覆盖
    max_count=20000,      # 预算
    margin_deg=1.0,
    jd_tt=None,           # None=取 jd_ut1（与既有链一致）
)
```

`StarSelection` 是 numpy 数组（按 **有效星等从暗到亮** 排序，亮星后画）：

| 字段 | 含义 |
| --- | --- |
| `hyg_id / hip / name / con / spect` | 标识与文本 |
| `ra_deg / dec_deg` | J2000（度） |
| `az_deg / alt_deg` | 该时刻地平坐标（度） |
| `vmag / eff_mag` | 视星等 / 叠加消光后的有效星等 |
| `bv / dist_pc` | B-V（NaN=未知）/ 距离（NaN=未知） |
| `fov_deg / limit_mag / bortle` | 本次口径截图；`limit_mag` 为实际生效值（已夹断） |
| `requested_limit_mag` | 调用方请求的极限星等（未夹断） |
| `beyond_catalog_limit` | 请求 > 9.0 时为 `True`，界面应提示“更深星需 Gaia/Tycho-2” |
| `available / returned / truncated` | 截断前命中数 / 返回数 / 是否降级 |

其他接口：

```python
limit_magnitude(fov_deg, bortle=3) -> float
extinction_delta_mag_array(alt_deg) -> np.ndarray
catalog.count_below(mag) -> int
catalog.magnitude_bins() -> [(lo, hi, count), ...]
catalog.find_by_name("Sirius") -> CatalogStar | None
```

### 4.3 超过星表深度（诚实降级）

本表覆盖 **V ≤ 9.0**。若界面或调用方请求 `limit_mag > 9.0`
（例如未来放到更深的仪器模式），`select_for_view` 不会假装有数据：

- `limit_mag` 夹到 `9.0`（实际选择口径）；
- `requested_limit_mag` 保留原请求值；
- `beyond_catalog_limit=True`，界面层应显示“当前星表仅到 9 等，
  更深星需 Gaia/Tycho-2”，而不是让用户以为“天上就这么多星”。

测试 `test_beyond_catalog_limit_is_explicit` 锁定该行为。

### 4.4 与现有绘制层的接线（供界面层后续使用）

```python
sel = select_for_view(gs, jd, view_state.center_az, view_state.center_alt,
                      view_state.fov_deg, bortle=self._bortle)
for i in range(len(sel)):
    r, bright, bloom = star_draw(sel.eff_mag[i], self._bortle)
    if r <= 0:
        continue
    rgb = bv_to_rgb(sel.bv[i])
    # 投影 sel.az_deg[i], sel.alt_deg[i] -> 屏幕
```

注意：`eff_mag` 已经含消光，**不要再**调用一次 `extinction_delta_mag`。
本次任务只交付数据层，`desktop/rf_sky_view.py` 未改动。

---

## 5. 复现生成

源 CSV 不在仓库内（33 MB，且许可要求署名/同许可）。有本地 HYG v4.1 时：

```powershell
python -m mbdsdr_ai.skyengine.build_full_sky_catalog `
    --source D:/mbdsdr/scratch/hygdata_v41.csv
```

输出默认写到 `mbdsdr_ai/data/full_sky_hyg_v41.npz`。脚本会：
剔除 Sol → 过滤 `mag <= 9.0` → 单位换算 → 按 mag 排序 → 计算分档与
`dec_order` → 计算数据 SHA-256 → 写入含来源/许可/源文件 SHA-256/
数据 SHA-256 的 `meta`。

---

## 6. 性能实测

环境：Windows / CPython + numpy 2.4.6，单线程；站点 (116.4°E, 39.9°N)，
`jd_ut1=2460000.5`，Bortle 3。取数为 `select_for_view` 全流程
（赤纬带裁剪 + 坐标变换 + 消光 + 过滤 + 排序）；排序按同等规模的随机
数组单独测量。

> 证据等级 E4：本段数字未验证，引用前须复核（判据见 docs/evidence-standard.md）。
| 场景 | 命中星数 | 取数中位 | 排序中位 | 30fps 结论 |
| --- | --- | --- | --- | --- |
| 120°，alt=45°，常态极限 6.5 | **1,230** | **2.5 ms** | 0.05 ms（n≈1.2k） | 远低于 33 ms |
| 120°，天顶，常态极限 6.5 | 1,604 | 2.5 ms | 0.07 ms（n≈1.6k） | 远低于 33 ms |
| 60°，天顶，常态极限 6.5 | 536 | 1.3 ms | — | 远低于 33 ms |
| 120°，天顶，**显式拉满 9.0 等** | 18,573 | 20.4 ms | 1.15 ms（n≈18.6k） | 接近但未超 33 ms |
| 180°，天顶，**显式拉满 9.0 等** | 27,582 | 29.3 ms | 2.36 ms（n≈27.6k） | 逼近 33 ms |

星表冷加载：**约 32 ms**（83,479 行，1.9 MiB npz；含 SHA-256 完整性重算），
之后进程内单例复用。

**结论**：常态路径（120°、裸眼极限）约 **2–3 ms/帧**，不是 30fps 瓶颈；
瓶颈出现在“显式拉到 9 等 + 180°”的极端组合，耗时主要在批量坐标变换与
候选过滤，而非排序。真正的上限还会来自 Qt 逐星绘制（数千个椭圆/辉光），
那是界面层的事，本次未测、也不在本模块职责内。若以后要放宽到 9 等
全视场，建议按屏幕瓦片/空间分块再裁剪，或降低 `max_count` 预算。

---

## 7. 未验证 / 缺什么

1. **深度**：HYG v4.1 是亮星/依巴谷汇编，mag ≳ 9 的完备性有限，不能当作
   深空巡天表。当前上限 9.0 等；请求更深时接口用
   `beyond_catalog_limit=True` 明确标记（§4.3）。若真要更深星场，需
   Gaia DR3 / Tycho-2 等来源（体积、许可与转换成本另议，本次未下载）。
2. **自行（proper motion）——未做，登记为缺口**：本表只存/只用 HYG 发布的
   J2000 位置，**没有存 `pmra/pmdec`，也没有按观测历元传播**；近距高自行星
   （如 Barnard's Star）在数十年尺度会有可见偏移。HYG 源 CSV 确实带
   `pmra/pmdec` 字段，但各子库（Hipparcos、Gliese 等）精度和历元不齐
   （Hipparcos 约 1 mas/yr 量级，汇编来源参差）。若以后要做，必须同时
   存 `pmra/pmdec` 及其不确定度/历元，并做 J2000→观测历元的传播与误差
   预算；本次不做，不悄悄忽略。
3. **距离/光谱型**：HYG 各来源口径不一，未知一律 NaN/空串；未做统一
   光度校准，不应用作测光结论。
4. **双星/聚星**：HYG 中分量的独立行会被当作独立星点绘制，未做系统合并。
5. **消光/折射**：沿用项目既有 `k/sin(alt)` 等厚大气近似，无气团曲率、
   折射抬升、地平雾霾模型；低空极限与真实观测可能有偏差。
6. **视场增益**是渲染 LOD 经验式，不是物理测光；不同 Bortle 的基线也沿用
   `star_draw` 的既有简化（Bortle ≤ 3 都是 6.5）。
7. **UI 接线**未做：`desktop/rf_sky_view.py` 仍走旧亮星子集；
   本模块只提供稳定接口与测试，实际替换由界面线负责。
8. **性能**只在本机 CPython 上实测，未在低配机/打包环境复测；Qt 绘制
   成本未计入。
9. **许可合规**：随仓 npz 是 HYG 的衍生数据（CC BY-SA 4.0），再分发需保留
   署名与同许可；代码（MIT）与数据的许可边界已在本文件 §1 写明。
10. **打包**：已修。`pyproject.toml` 的 `package-data` 现含 `data/*.npz`；
    测试会真构建 wheel 与 sdist，并在压缩包内断言星表存在
    （`test_star_catalog_packaging.py`）。
