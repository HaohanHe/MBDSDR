# 数据清点：星表分层 / DSO 完整性 / 许可合规

本文是数据侧的三本硬账。每个数字都标出来源文件与符号；口径与参数写死在
下面，照第 5 节的命令可以复算。任何一项对不上，按 bug 处理，不靠印象。

---

## 1. 星表分层一致性（HYG v4.1 全天星表）

### 1.1 固定测量条件

| 项 | 值 | 来源 |
|---|---|---|
| 观测站 | 北纬 40°、东经 116°、海拔 0 m | `GroundStation(116.0, 40.0, 0.0)` |
| 时刻（UT1） | 2026-09-27 12:00 UTC → JD 2461311.0 | `mbdsdr_ai/sky_interaction.py` 坐标链 |
| 视场中心 | az = 0°、alt = 90°（天顶） | `ViewState(center_az=0.0, center_alt=90.0, fov_deg=…)` |
| 画布 | 700 × 700 px | 仅影响边缘裁剪的 ±1~2 颗（见 1.4） |
| 投影 | `PerspectiveProjection`（专业模式默认）。全天模式另用 `StereographicProjection`，见 1.4 | `desktop/rf_sky_view.py:1734` / `:1742` |
| 大气消光 | `stars.EXTINCTION_K` 默认，逐星按仰角算 | `mbdsdr_ai/skyengine/stars.py` |
| 单帧预算 | `max_count = STAR_MAX_DRAW = 20000` | `desktop/rf_sky_view.py:3696`、`:3875` |

星等分箱边界：`(-∞, 2) [2,4) [4,6) [6,7) [7,+∞)`，左闭右开。

### 1.2 三数对照（每个星等箱一行）

| 星等箱 | 数据文件 | 加载后 | 120°/B3 | 120°/B8 | 160°/B3 | 160°/B8 |
|---|---|---|---|---|---|---|
| < 2 | 50 | 50 | 4 | 4 | 11 | 11 |
| 2–4 | 470 | 470 | 115 | 115 | 178 | 174 |
| 4–6 | 4,499 | 4,499 | 1,159 | 100 | 1,671 | 112 |
| 6–7 | 10,439 | 10,439 | 392 | 0 | 405 | 0 |
| > 7 | 68,021 | 68,021 | 0 | 0 | 0 | 0 |
| **合计** | **83,479** | **83,479** | **1,670** | **219** | **2,265** | **297** |

选择集 `returned` 为 1,672 / 219 / 2,271 / 298；落笔扣减即屏幕边缘裁剪
2 / 0 / 6 / 1（原因见 1.4），四组 `truncated` 均为 `False`。

**结论：加载层零去重、零截断、零星等筛选。** 数据与加载逐箱相等，
`npz["meta"].star_count = StarCatalog.count = 83,479`。

### 1.3 数字来源与落笔链路

- 数据文件条数：`mbdsdr_ai/data/full_sky_hyg_v41.npz` 的 `npz["vmag"]` 数组
  （`np.histogram(npz["vmag"], bins=[-inf,2,4,6,7,inf])`）。
- 星表加载后条数：`mbdsdr_ai/skyengine/star_catalog.py` 的
  `StarCatalog.vmag`（`load_catalog()` 返回的实例属性，不是方法）；
  合计对照 `StarCatalog.count`。

- 选择集：`mbdsdr_ai/skyengine/star_catalog.py::select_for_view`，内部由
  `desktop/rf_sky_view.py::_star_selection`（`:3677`）以
  `bortle=…`、`max_count=STAR_MAX_DRAW` 调用。
- 落笔过滤（按顺序，全部在 `desktop/rf_sky_view.py::_draw_stars` `:3806-3821`）：
  1. `window._display_alt_many(sel.alt_deg)`（`:4406`）——显示层折射后的视高度；
  2. `_sky_to_screen_many`（`:3483`）→ `sky_interaction.sky_to_screen_many`；
  3. `alt_draw < 0` 跳过（`:3810`）；
  4. `px/py` 非有限跳过（`:3813`）；
  5. 画布外 `(-4.0 … width+4.0, -4.0 … height+4.0)` 跳过（`:3815`）；
  6. `r <= 0` 跳过（`:3820`，半径来自 `_star_visual_many` `:3730`）。
- 星等分箱依据 `StarSelection.vmag`（原始星表星等），不是 `eff_mag`；
  `eff_mag` 只用于半径/透明度。

### 1.4 差值归因

| 差值 | 数量 | 原因 | 证据 |
|---|---|---|---|
| 数据 → 加载 | 0 | 无 | 1.2 逐箱相等；`meta.star_count` = `count` |
| 加载 → 选择集 | 由视场与极限星等决定 | **极限星等筛掉暗星**，不是去重 | `limit_magnitude(fov, bortle)` |
| 选择集 → 落笔 | 2 / 0 / 6 / 1 | **仅屏幕边缘裁剪** | `_draw_stars` 第 5 步 |

- 极限星等：B3 = 6.500，B8 = 4.750（`StarSelection.limit_mag`）。
  `>7` 整箱、`6–7` 在 B8 下整箱为 0，都是**极限星等**筛掉，
  不是截断、不是去重。
- `StarSelection.truncated` 四组均为 `False`，`available == returned`
  （1,672 / 219 / 2,271 / 298），说明单帧预算 20,000 远未触顶；
  因此差值不可能来自 `max_count`。
- 落笔过滤中 `below_horizon = 0`（选择集已按几何 alt ≥ 0 裁剪，
  天顶视场全覆盖），`non_finite = 0`，`r <= 0 = 0`；唯一扣减是边缘裁剪。
- 边缘裁剪是**投影与画布形状的函数**：同一组星用全天模式的
  `StereographicProjection` 时，160°/B3 为 2,271 颗、160°/B8 为 298 颗
  （裁剪 0）。换画布/换投影只挪动这 0~6 颗，不改变分层结论。
- 视场只影响「哪些星在锥内」与「极限星等」（`detail_gain_mag` /
  `effective_limit_mag`）；数据层不因 FOV 改变星表内容。

---

## 2. DSO 数据完整性（随仓 OpenNGC 副本）

源文件：

- `mbdsdr_ai/tests/fixtures/openngc/NGC.csv`（13,971 行 = 1 表头 + 13,970 条）
- `mbdsdr_ai/tests/fixtures/openngc/addendum.csv`（65 行 = 1 表头 + 64 条）

合计 **14,034 条**。UTF-8 BOM、分号分隔、32 列。字节已由
`.gitattributes` 的 `mbdsdr_ai/tests/fixtures/** -text` 冻结，避免
checkout 时 CRLF 改写导致 SHA 分叉。

### 2.1 完整性计数

| 项 | 条数 | 判定 | 出处 |
|---|---|---|---|
| 总条目 | 14,034 | 两文件数据行相加 | `NGC.csv` + `addendum.csv` |
| 有坐标 | 14,027 | `RA` 与 `Dec` 均非空 | 同上 |
| 有轴比 | 11,096 | `MajAx` 与 `MinAx` 均 > 0 | 同上 |
| 有星等 | 11,519 | `V-Mag` 或 `B-Mag` 非空 | 同上 |
| 星系 | 10,522 | `Type == "G"` | 同上 |
| 星团 | 1,001 | `GCl + OCl + *Ass + Cl+N` | 同上 |
| 星云 | 234 | `Neb + HII + RfN + EmN + SNR` | 同上 |
| 行星状星云 | 130 | `Type == "PN"` | 同上 |

口径说明：

- 四类互斥分组，`PN` 不计入「星云」；`Dup`（652 条）、`*`（547 条）、
  `Other`（419 条）、`**`（244 条）、`GPair`（231 条）等不进入四类。
- 「有轴比」是按源文件的 `MajAx`/`MinAx` 两列同时齐全来算；
  进 DSO 目录后只有 51 条落在 117 条目录内（见 `docs/dso-catalog.md`）。

原始 `Type` 分布（14,034 条）：

`G` 10,522 · `OCl` 663 · `Dup` 652 · `*` 547 · `Other` 419 · `**` 244 ·
`GPair` 231 · `GCl` 208 · `PN` 130 · `Neb` 94 · `HII` 83 · `Cl+N` 67 ·
`*Ass` 63 · `RfN` 38 · `GTrpl` 26 · `GGroup` 13 · `SNR` 11 · `NonEx` 10 ·
`EmN` 8 · `Nova` 3 · `DrkN` 2。

### 2.2 字段缺失统计

源文件 14,034 条逐列缺失（空字符串计缺；`Name`/`Type` 零缺失）：

| 字段 | 缺条数 | 后果 |
|---|---|---|
| RA / Dec / Const | 7 / 7 / 7 | 无法定位；7 条全是 `NonEx`（不存在的天体），不进入任何可用天体集合 |
| MajAx | 1,959 | 无长轴 → 不能算轴比、不能按视尺寸分级 |
| MinAx | 2,938 | 无短轴 → 轴比未知（比 `MajAx` 多缺 979 条），绘制退化为圆 |
| B-Mag / V-Mag | 2,693 / 9,784 | 两层都缺的 2,515 条无法按亮度排序/推荐；有星等的共 11,519 条 |
| M / NGC / IC | 13,924 / 13,126 / 13,574 | 仅 110 / 908 / 460 条带对应编号，其余只能用 `Name` 检索 |
| Common names | 13,882 | 仅 152 条有英文俗名，俗名搜索覆盖有限 |

进入 117 条随包目录后（`mbdsdr_ai/data/dso_catalog.json`）：

| 字段 | 缺条数 | 后果 | 缺的条目 |
|---|---|---|---|
| `magnitude` | 0 | 已补齐（`16f075f3` — `mbdsdr_ai/data/dso_catalog.json:44` — `heasarc_messier`）：HEASARC `MESSIER` 表 `vmag`（V 波段），逐条 `magnitude_source=heasarc_messier` | — |
| `size_arcmin` | 0 | 已补齐（`16f075f3` — `mbdsdr_ai/data/dso_catalog.json:54` — `lynga`）：M40←WDS 双星分离 0.887′、M73←Lynga `VII/92A` 2.8′ | — |
| `size_minor_arcmin` | 66 | 无短轴，不能画椭圆 | 与 `axis_ratio` 同一批 |
| `axis_ratio` | 66 | 按圆形绘制；查询/推荐不读该字段（有钉扎测试） | 30 GCl / 27 OCl / 5 PN / 1 Neb / 1 `**` / 1 Other / 1 RfN |
| `zh_name` | 83 | 界面回退 `name_en`；中文名覆盖 34/117 | — |
| `aliases_en` | 107 | 仅 10 条有英文别名，别名搜索覆盖有限 | — |

- `size_source` 分布：`catalog` 115 / `wds` 1 / `lynga` 1（合计 117）。
- 坐标实测域：RA `[10.0920°, 351.2017°]`、Dec `[−69.1009°, +69.6794°]`，
  全部落在 RA ∈ [0°, 360°)、Dec ∈ [−90°, 90°]，无越界、无非有限值
  （实测值，随星表更新需重算）。

补字段口径：**只填 OpenNGC 为 `null` 的字段**，原值来自公开星表快照
`mbdsdr_ai/data/dso_supplement.json`（含逐条原始行、`fetched_at`、
`payload_sha256`，由 `scripts/fetch_dso_supplement.py` 生成）；不插值、
不估算。已有值与补充值冲突时生成器直接报错，绝不静默覆盖。

上表数字由 `test_openngc_field_gap_counts` 与
`test_catalog_field_gap_counts` 钉扎；数据一变测试先红，文档随后更新。

### 2.3 列名契约

两份文件的表头逐字相同、顺序固定，共 32 列：

```
Name, Type, RA, Dec, Const, MajAx, MinAx, PosAng, B-Mag, V-Mag, J-Mag,
H-Mag, K-Mag, SurfBr, Hubble, Pax, Pm-RA, Pm-Dec, RadVel, Redshift,
Cstar U-Mag, Cstar B-Mag, Cstar V-Mag, M, NGC, IC, Cstar Names,
Identifiers, Common names, NED notes, OpenNGC notes, Sources
```

测试实际读取的 13 列：

```
Name, Type, RA, Dec, Const, MajAx, MinAx, B-Mag, V-Mag, M, NGC, IC,
Common names
```

钉扎测试：`mbdsdr_ai/tests/test_dso_catalog.py`
`test_openngc_header_contract`（表头全等）与
`test_test_used_columns_exist_in_source_headers`（测试列 ⊆ 真实列）。
上游改列名/改顺序 → 先在这两条红，不会等到 30 抽样对拍报 `KeyError`。

### 2.4 M102 固定映射

源文件里 `M102` 只有一行，在 `addendum.csv`：

| 字段 | 值 |
|---|---|
| `Name` | `M102` |
| `Type` | `Dup` |
| `M` | `101`（OpenNGC 认为它是 M101 的重复条目） |
| `RA` / `Dec` | `14:03:12.54` / `+54:20:56.2` |
| `Const` | `UMa` |
| `NGC` | 空 |

`NGC.csv` 中不存在 `M == 102` 的非 `Dup` 行。本项目按历史约定把
`M102` 映射到 `NGC 5866`（`dso_catalog.json` 中 `source_name == "NGC5866"`、
JSON `cross_ids == {"ngc": 5866, "ic": null, "m": 102}`；API 层
`DSOEntry.cross_ids == (("ngc", 5866), ("m", 102))`），条目数保持 117。

钉扎测试：`test_m102_raw_addendum_record`（原始行字段）与
`test_m102_convention_pinned`（目录映射 + `meta.m102_convention`）。

---

## 3. 许可合规的可执行检查

### 3.1 规则

- 扫描仓库内所有数据文件（`.json .csv .tsv .npz .npy .dat .txt .xml
  .yaml .yml`；`.npz` 只读 `meta` 数组）。
- 只认**许可声明**形态：`GPL-3.0` / `GPLv2` / `GNU General Public License` /
  `"license": "GPL"` / `SPDX-License-Identifier: GPL…`。
  不认裸 `GPL` 子串（base64 测试向量里会偶然出现这三个字母，那是误报）。
- **MIT 主库路径** = `mbdsdr_ai/`。该路径下的 GPL 声明 = **违规**（退出码 1）；
  路径外的同类声明只作提示（`--verbose` 打印），不影响退出码。
- CC BY-SA **不判红**：主库的 HYG 星表、OpenNGC 目录、星座连线本身就是
  CC BY-SA 4.0 并已在 `NOTICE.md` 署名。若一并禁止，会与「可分发」结论
  自相矛盾。脚本用 `--list` 把每条数据的许可证列出来，CC BY-SA 同样可见。
- 构建目录（`build`/`build-local`/`.tmp*`/`dist`/`__pycache__` 等）与测试生成的
  `mbdsdr_ai/artifacts/` 不扫。
- **B1 后白名单为空（空表 = 禁入）**：`mbdsdr_ai/` 下出现 GPL 级数据即红，
  不再提供按文件豁免通道。
- 现状核对（2026-11-06）：`mbdsdr_ai/` 下 **0 条** GPL 条目（星座中文名
  已迁 CC0）。`--list` 里仍有 2 条路径外提示
  `tests/golden/dvbs2_bch_vectors.json`、`tests/golden/dvbs2_ldpc_vectors.json`
  的 `GPL-3.0-or-later`：它们是仓外运行 GPL 上游工具生成的参考向量，
  `NOTICE.md` 已登记，不随 MIT 包分发，按上面的规则只作提示、不判红。

脚本：`scripts/check_data_licenses.py`；测试：`tests/test_data_licenses.py`。

### 3.1a 逐条来源与许可证（`--list`）

```
python scripts/check_data_licenses.py --list
```

输出每个数据文件的 `license=` 与 `source=`（从 JSON 的 `*license*` /
`source*` 键提取；`.npz` 读 `meta`；无内嵌声明的 CSV 用已知出处表）：

| 文件 | license | source |
|---|---|---|
| `mbdsdr_ai/data/full_sky_hyg_v41.npz` | CC BY-SA 4.0 | HYG Database v4.1 |
| `mbdsdr_ai/data/dso_catalog.json` | CC-BY-SA-4.0 | OpenNGC |
| `mbdsdr_ai/data/constellations_iau.json` | CC BY-SA 4.0（连线；中文名 Wikidata CC0，见 `docs/constellations.md` §1.1.1） | Stellarium skyculture modern_iau |
| `mbdsdr_ai/data/constellation_names_cc0.json` | CC0-1.0 | Wikidata |
| `mbdsdr_ai/data/moon_albedo_lola_2ppd.npz` | Public Domain (U.S. Government Work) | NASA PDS LRO-L-LOLA-4-GDR-V1.0（`LDAM_4_FLOAT` 法向反照率，1064 nm） |
| `mbdsdr_ai/tests/fixtures/openngc/NGC.csv` | CC BY-SA 4.0 | OpenNGC NGC.csv |
| `mbdsdr_ai/tests/fixtures/openngc/addendum.csv` | CC BY-SA 4.0 | OpenNGC addendum.csv |
| `mbdsdr_ai/tests/fixtures/hyg_v41_reference_sample.csv` | CC BY-SA 4.0 | HYG Database v4.1 sample |

`--list --json` 机器可读，`files[]` 每项带 `path/license/source/in_mit_package`。

### 3.2 白名单（B1 后清空 = 禁入）

| 路径 | 理由 |
|---|---|
| （空） | — |

**2026-11 B1 决策**：星座中文名与连线分开处理。连线保留 Stellarium
`modern_iau`（CC BY-SA 4.0，署名保留）；61 个中文名（约 300 字节，原取自
Stellarium `zh_CN.po`，GPL-2.0-or-later）替换为 Wikidata 标签（CC0-1.0）的
固定快照 `mbdsdr_ai/data/constellation_names_cc0.json`（88/88，文件 SHA-256
`75597803…`）。逐座对拍后 7 项仅简繁字形差异、可见文案逐字不变。因此不需要、
也不再保留任何白名单条目；空理由仍视为配置错误（`ScanError`，退出码 2）。

A（保留 GPL 名 + 文档豁免）未采用：豁免是政策承诺而非法律结论，且会让
`mbdsdr_ai/` 长期存在 GPL 级数据。B2（连线一起换）未采用：连线本就不是 GPL，
替换对 GPL 问题零收益、视觉回归面最大。详见 `LICENSING.md` §4c。

### 3.3 反证（必须红）

```powershell
python scripts/check_data_licenses.py --check   # 绿：0
python scripts/check_data_licenses.py --probe   # 临时写 GPL 探针 -> 必须红 -> 自动清理
```

`--probe` 在 `mbdsdr_ai/data/_gpl_probe.json` 写入
`{"meta": {"source_license": "GPL-3.0-or-later"}}`，扫描后必须命中，
`finally` 删除探针。通过时打印 `PROBE RED OK`，退出码 0；
未命中打印 `PROBE FAILED`，退出码 2。

当前实测（2026-11-06，B1 后）：

```
scanned data files: 383
合规检查通过
```

`scanned data files` 随仓内数据文件增减浮动（其它并行改动线新增 `.json`
即加一），它只用来确认扫描器真的遍历了全仓，不是固定基线；判据是末行
「合规检查通过」与退出码 0。

`--probe` 输出 `PROBE RED OK: GPL 探针被检出并拦截`，退出码 0（探针工作正常）。

手工反证：在 `mbdsdr_ai/data/_gpl_red_probe.json` 写入
`{"meta": {"source": "red-probe", "source_license": "GPL-3.0-or-later"}}`
后运行，必须且实际得到：

```
合规检查失败：1 个 GPL 数据文件在 mbdsdr_ai/ 下（B1 后无豁免机制）
scanned data files: 384
  VIOLATION: mbdsdr_ai/data/_gpl_red_probe.json (GPL / GPL-3.0-or-later)
```

退出码 1；删除探针后复跑恢复「合规检查通过 / 退出码 0」，
`mbdsdr_ai/data/` 无残留。

---

## 3a. 非星表配置数据（声明 vs 实测）

`mbdsdr_ai/data/` 除星表外还有 6 份配置表（卫星链路、卫星剖面、帧布局、
DVB-S2 BCH/LDPC 表、月面反照率），另有 `desktop/bandplan.json` 频段表。
下面是逐文件实测值与文档/内嵌声明的对拍。

| 文件 | 声明 | 实测 | 差 |
|---|---|---|---|
| `sat_links.json` | 无条数声明；每条带 `source`/`culture`，只含下行 | 8 星 / 9 条链路，全部 `direction=downlink`；卫星级 `source` 8/8、`culture` 8/8；频点 137.1–436.795 MHz | 0 |
| `sat_profiles.json` | 7 剖面，逐条带 `source` | 7 剖面，`source` 7/7；`baud` 缺 5、`coding` 缺 5（DVB-S2/模拟体制本就不适用） | 0 |
| `frame_layouts.json` | 无条数声明；逐条 `honesty`；`_note` 解释 `len=-1` 与位级字段 | 5 顶层 = 示例 1（`astu_1_tm_example`，非真实星）+ 通用协议帧 2（`ccsds_tc_classic` 基座 / `ccsds_tc_segmented` 派生）+ 星上遥测 2（`funcube1_ao73_frame` 基座 / `funcube1_ao73_fitter` 派生）；派生共 2 条；`source` 3/5（既有 2 条无 `source`，按红线冻结）；字段字节/位段不重叠；声明 CRC 的布局均可解析 | 0（W91 星覆盖 1 颗 = 达下限，其余待公开规范；NOAA KLM 手册 37.4MB 本环境实测 1.8KB/s，5 分钟时限内不可取得；**规范未给示例帧，FUNcube 布局无法端到端验证，仅通过 schema 校验**） |
| `dvbs2_bch.json` | 21 模式码 | normal 11 + short 10；short 的 9/10 标 `unavailable` 并带原因（表 5b 全 NA、表 7b 无 q 条目） | 0 |
| `dvbs2_ldpc_tables.json` | 21 张表 + `source_files.sha256` | 21 = normal 11 + short 10；`n>k>0`、`q>0` 全部成立；短帧缺 9/10 与 BCH 一致 | 0（外部源不可复核，见下） |
| `moon_albedo_lola_2ppd.npz` | LRO-L-LOLA-4-GDR-V1.0，Public Domain | `albedo` (360,720) float32，范围 [0.1359, 0.4716] | 0 |
| `desktop/bandplan.json` | 文档无条数声明 | 30 条 bands | — |

口径要点：

1. **`frame_layouts.json` 的 `len=-1` 是哨兵值**，含义为「从该 `offset` 到
   帧尾的剩余全部字节」，不是长度未知。当前解释器
   （`mbdsdr_ai/verify_chain.py::extract_fields`）只展开 `name=data` 的字段，
   其它字段名写 `-1` 会按越界报错。实例：`ccsds_tc_classic` 的 `data`
   （`offset=5`）从第 6 字节取到帧尾，因此**含帧尾 2 字节 FECF**，取纯数据
   需去掉末 2 字节。该口径写在文件 `_note` 里，并由
   `test_negative_len_sentinel_is_documented` 钉住：新增 `len=-1` 字段而
   `_note` 不解释即红。
2. **`dvbs2_ldpc_tables.json` 的 `source_files.sha256` 是生成时记录，
   外部源文件（`ldpc_normal.json` / `ldpc_short.json`）不随仓分发**：
   本仓只能验证表内容自洽（21 张 = normal 11 + short 10、`n>k>0` 且
   `q>0`）以及与 BCH 表的短帧缺 9/10 一致；**不能验证与上游同源**。
   将来把源文件入库、或让生成脚本在构建时校验，再升级为可复核。
3. **同频不是重复**：`sat_links.json` 的 137.100 MHz 同属 NOAA-19（APT）与
   METEOR-M N2（LRPT）；`sat_profiles.json` 的 1697 MHz 同属 FY-4A 与
   FY-4B（LRIT）。`identify_by_freq` 返回完整命中列表，**只取第一条的调用方
   会漏**；`mbdsdr_ai/tests/test_sat_links.py::test_identify_by_freq_hits_and_empty`
   钉住「同频共存，诚实并列，不强行裁决」。
4. **`bandplan.json` 的 30 条与「38 项」不是同一口径**：38 是
   `tests/test_spectrum_bandplan.py` 的**用例数**（实测 collect 与 run 均 38），
   与频段条数无关；`bandplan.json` 历史只有 19 条（`4d23308f`）→ 30 条
   （`a72996fb`）两档，从未有过 38 条。因此两边都不过期，是两件事被混称。
5. **位级字段口径**：声明 `bit_len` 的字段走 `bit_offset`（以帧首为 0、
   MSB-first）+ `bit_len` 的位级路径，`type` 须为 `uint`；`bit_offset +
   bit_len` 超出载荷位数必须报错，**绝不截断或补零**；未声明 `bit_len`
   的字段仍走原字节路径，旧布局行为不变。`funcube1_ao73_frame` 的 60 个
   位字段 + 1 个载荷字段按 AMSAT-UK《FUNcube-1 Downlink Data》§4 表逐段
   累加（头 8bit + 实时遥测 440bit + 载荷 200 字节 = 2048bit），源 PDF 的
   URL/SHA-256/抓取时间写在条目 `source` 里；文档未给换算系数，故只输出
   原始计数，单位为空。
6. **FUNcube 布局的验证边界**：AMSAT-UK《FUNcube-1 Downlink Data》全文
   只有位宽表，**没有示例帧字节，也没有字段取值示例**（§6「Fitter Message
   Slot 9 data」为「To be added」），因此该布局**无法用规范样例做端到端
   验证，仅通过 schema 校验**。测试里的 256 字节帧是字段抽取机制的用例，
   不是规范样例，不能当验证证据。此结论同步写在 `funcube1_ao73_frame` 与
   `funcube1_ao73_fitter` 的 `honesty` 字段中。
7. **验证等级口径（机器字段）**：每个顶层布局必填 `verification`，只允许
   `schema_only`（仅过结构校验）/ `spec_example`（用规范示例数据逐字段
   对上）/ `ota`（真实接收数据验证过）；`spec_example` 与 `ota` 必须同时
   给 `verification_evidence` 证据文件路径，否则加载即报错。`honesty`
   保留为人读说明，`verification` 才是可机检口径。当前 5 条布局全部为
   `schema_only`——没有任何一条做过规范示例逐字段对拍或 OTA 验证。

## 3b. Web 产品生成产物（`web/product/build/*.json`）

四个 JSON 都有生成器，不是手写产物；触发链在 `web/product/run_all.py:27-31`
（`web/product/build/build.py:118-121` 也会重跑前两个）。审计过程只读：
重跑后已按 sha256 逐文件恢复原字节，产物本身未改。

| 产物 | 生成器 | 输入 |
|---|---|---|
| `repo-facts.json` | `build/sync_from_repo.py` | `mbdsdr_ai/core/mcp_server.py`、`tool_registry.py`、`desktop/tokens.py`、`cpp/src/core/tokens.h`、`station.py` 等源码 |
| `sky-catalog.json` | `build/sync_sky_catalog.py` | `web/product/data/hygdata_v41.csv` + `mbdsdr_ai/skyengine/{stars,constellations}.py` + `mbdsdr_ai/data/dso_catalog.json` + `*_culture.json` |
| `sat-tracks.json` | `build/sync_sat_tracks.py` | `mbdsdr_ai/sat_tracker.py::_BUILTIN_TLES`（6 条）+ sgp4 2.27 + `celestial_geometry.py` |
| `astro-golden.json` | `build/gen_astro_golden.py` | `mbdsdr_ai/celestial_geometry.py`（岁差 / 章动 / GMST） |

**生成器输入 → 产物 → 文档**（2026-11-06 实测，只列关键字段；
`03`/`06`/`07`/`09`/`10` 分别指 `web/product/docs/03-architecture.md`、`web/product/docs/06-compliance.md`、
`web/product/docs/07-roadmap.md`、`web/product/docs/09-repo-alignment.md`、`web/product/docs/10-sky-catalog.md`）：

| 字段 | 产物 | 输入复核 | 文档声明 |
|---|---|---|---|
| 全天星 | 25,792（V ≤ 7.5） | HYG CSV 逐行复点：119,626 行中 25,792 行 | 一致（`03:159`、`03:176`、`06:41`、`07:66`、`README:89`、`10:10`、`10:37`） |
| 命名星 | 141 | `stars.py` | 一致（`03:176`、`10:15`） |
| 星座 / 连线 | 27 座 / 115 段 | `constellations.py::CONSTELLATION_LINES` 27 组 | 一致（`03:160`、`03:176`、`07:70`、`10:16`） |
| DSO | 117 | `dso_catalog.json.objects` 117 条 | 一致（`03:176`、`07:70`、`10:17`） |
| HYG SHA | `eb086d46…` | CSV 实测 sha256 与 `data/hygdata_v41.sha256` 三方同值 | 一致 |
| 工具域 | 14（冻结 10 + Python 扩展 4） | `mcp_server.py` 两个 domain 汇总 dict | **文档无声明**（红线「不硬编码能力数字」，`09:107`；仅 `VERIFY-REPORT.md:6` 自动写出） |
| 工具→域 | 82 | `mcp_server.py::_TOOL_DOMAIN` 82 条 | 文档无声明（同上） |
| 写工具模式 | 82 | `tool_registry.py::WRITE_TOOL_NAME_PATTERNS` 82 条 | 一致（`09:79`，自动生成页）；手写页无声明 |
| ControlHub 端口 | 50732 | `cpp/src/core/tokens.h:1422` | 一致（`09:62` 自动生成；`wiki/faq.md:43`） |
| 时间倍率 | 5 档 | 仓库时间引擎常量 | 文档无声明（仅自动报告 `VERIFY-REPORT.md:7`） |
| 默认站点 | 0.0, 0.0 / trusted=false | `station.py` 守卫 | 一致（`09:105`） |
| 卫星轨迹 | 6 星 × 481 采样 @ 90 s | `_BUILTIN_TLES` 6 条；481 = 2×240+1 | 文档无声明（仅自动报告 `VERIFY-REPORT.md:18`） |
| 天文金标准 | 24 例 | `celestial_geometry.py` | 文档无声明（仅自动报告 `VERIFY-REPORT.md:19`） |

**产物是否被手改**：`git log -p` 逐提交看，产物变更只随生成器或输入变化。
未发现「只改产物、不改脚本/输入」的手改。

**单独记一条事故形态**：`16f075f3` 曾把一份**落后于输入的旧产物**入库
（`generatedAt` 00:42:04、attribution 缺 supplement 段），`a5d062e8` 已撤回。
「产物落后于输入」是生成物最容易复发的事故：脚本没改、输入改了、产物忘了重跑，
常规代码走查看不出来，只有重跑比对能发现。

**长期守卫（`--check`，返回码非 0 即漂移）**：

```powershell
python web/product/tests/verify_build_json.py --check
```

脚本先把 10 个输出按 sha256 备份到 `.build-json-backup/`（manifest + 原字节 +
`READY` 哨兵，目录已 gitignore）→ 重跑四个生成器（与 `run_all.py:27-31`
同一条链）→ 把豁免字段替换为占位符后**逐字节比对** → `finally` 按备份写回并
校验 sha。豁免字段只有：`repo-facts.json` 的 `generatedAt` / `repo.head`、
`sky-catalog.json` 的 `meta.generatedAt`，以及自动生成文档里的
「生成时间 / 仓库基线」（同一戳的文本形态）。返回码 `0` = 一致，
`1` = 生成器失败或漂移，`2` = 备份/恢复失败；**不联网**，星表读本地
`data/hygdata_v41.csv`。
**进程级恢复**：若中途被 kill（`finally` 不会执行），备份目录会连同 `READY`
留在磁盘；下次运行会先自动恢复再继续检查，也可手工执行
`python web/product/tests/verify_build_json.py --restore`。
2026-11-06 实测：正向 10/10 一致，本机约 2 秒；手工往 `sat-tracks.json`
注入一个空格 → 返回 1 并打印 DRIFT；`--simulate-crash`（仅手工显式传参，
`--check` 路径不会触发）在第一个生成器后硬退出 → 返回 137、3 个文件已变脏、
`READY` 在位；`--restore` 后 10 个文件 sha 全部回到原值、备份目录清空；
再跑 `--check` 也能先自动恢复。
`web/product/tests/verify_product.py` 另钉内容红线：`starCount>10000`、HYG 许可/SHA、
`notVerified` 条数、`satCount`/`count` 下限、`caseCount≥6` 且事实源为
`celestial_geometry.py`。`build/astro-golden.json` 与
`build/gen_astro_golden.py` 已作为同一笔提交 `f4ef0d13` 入库（两者必须同进
同出，缺一复现链断裂），入库后重跑产物字节一致。

**天文框架跨语言对拍（JS ↔ Python）**：

```powershell
node web/product/tests/astro_selftest.js
```

纯 Node（无 npm 依赖、不联网、不需要浏览器）。它与 Python 生成器读**同一份**
`build/astro-golden.json`；样本是 `gen_astro_golden.py` 里固定的
6 JD × 4 站 × 6 个 RA/Dec 参考方向 = 24 case / 144 向量
（**固定参考方向，不是 HYG 星表行抽样**；两侧吃同一批输入，不存在各挑各的
样本）。断言：GMST、P/N/Mpn/Rf/M/Pinv 与 dpsi/deps/eps，以及
horizon / dateHorizon / ofDate / temeToJ2000 四类向量；容差为矩阵 `1e-11`、
向量 `1e-10`、GMST `1e-8` 度；另钉章动 `|dpsi| ∈ (0.1″, 30″)` 与总矩阵保长
（`1e-10`）。2026-11-06 实测 `worst 3.33e-16 (horizon 2461500/40.7 ra=300[2])`
→ **一致**，距向量容差还有约 5 个数量级。前端与 selftest 已作为同一笔提交
`3d0735e5` 入库。

**现行状态（2026-11-06，不是待办）**：这条对拍目前是**手动运行**
（`node web/product/tests/astro_selftest.js`），因为 `run_all.py` /
`scaffold.py` / `index.html` / `verify_product.py` 四处接线在工作区仍是别线
在途（`M`），HEAD 里没有。解除条件：这四处随所属线落库后，改由 `run_all.py`
的第 4 步（金标准）与第 10 步（selftest）驱动，本节改为「全链自动」；接线细节与
验收输出见 `docs/pending-patches/2026-11-06-handoff-astro-wiring.md`。

## 3c. 生成物漂移守卫盘点（2026-11-06）

判定口径：只看「生成器或输入发生变更、产物没重跑」这类漂移能否被现有守卫抓到；
手改产物后再跑生成器不算守卫覆盖。

| 生成物 | 生成器 / 触发 | 现有守卫 | 缺口 | 风险 |
|---|---|---|---|---|
| `web/product/build/*.json` + `src/05`、`src/12`、`src/13`、`docs/09`、`docs/10`、`data/hyg*.sha256` | `sync_from_repo.py` / `sync_sky_catalog.py` / `sync_sat_tracks.py` / `gen_astro_golden.py`；`run_all.py` 第 1–4 步 | `tests/verify_build_json.py --check`：重跑→归一→逐字节，含备份/恢复 | 无 | 低 |
| `web/product/index.html` | `build/scaffold.py`（第 5 步） | `verify_product.py` 只查脚本顺序 / DOM id | 手改或未重跑无字节比对 | 中 |
| `web/product/src/70-docs-data.js`、`dist/index.html` | `build/build.py`（第 7 步） | `verify_product.py` 只查 dist 存在 + DOM | 文档改了没重 build → 70 数据陈旧 | 中 |
| `web/product/docs/wiki/capabilities/*`、`glossary`、`faq`、`scenarios`、`README.en/ja`、`08` | `build/gen_docs.py`（第 6 步） | `verify_product.py` 只校能力页集合==事实源、断链 | 正文漂移无守卫 | 中高 |
| `docs/learn/decoder-capability-matrix.md` | `scripts/decoder_capability_report.py --write` | `tests/test_decoder_capability_matrix.py` 逐字比对 | 无；当前工作区因未提交输入改动而红 | 低 |
| `docs/settings-inventory.md` | `scripts/settings_inventory.py --write` | `--check` + 测试 | 无；当前因桌面线在途输入而红 | 低-中 |
| `docs/mcp_tools.md` 生成块 | `scripts/gen_mcp_tools_doc.py` | `--check` + `tests/test_mcp_tools_doc.py`（2026-11-06 新增） | 已补 | 低 |
| `mbdsdr_ai/data/xingguan_chinese.json` | `build_constellation_data.py`（需 `D:\mbdsdr\reference\stellarium`） | `meta.source_files.*.sha256` 快照钉 + `test_xingguan_source_snapshot_pins_frozen`；reference 在机时另有 `test_source_files_sha_traceable` | 不能离线重跑；上游快照变了只能在 reference 在机时发现 | 中 |
| `mbdsdr_ai/data/xingguan_star_names_zh.json` | `build_xingguan_star_names.py`（输入：上述文件 + `star_names.zh_CN.fab`） | `meta.source_files.members.sha256` 对拍随仓输入（`test_xingguan_star_names_members_pin_matches_repo_input`）+ payload 摘要 + 快照钉 | 见下方残余风险 | 中 |
| `mbdsdr_ai/data/dso_catalog.json` 等 | `build_dso_catalog.py`（需 `reference/openngc`） | 源 SHA + payload 摘要自洽（`test_dso_catalog.py`） | 不能离线重跑 | 中 |
| `mbdsdr_ai/data/full_sky_hyg_v41.npz` 等 | 对应 builder | `test_star_catalog.py`：源 SHA + 摘要 + 篡改拒绝 | 不能重跑 | 中 |
| `docs/perf-evidence/*.json` | `scripts/perf_probe_deep.py` 等（手动测量） | **无，且不应加重跑守卫**：机器/工具链相关，**证据快照，不可确定性复现** | 不适用 | 低 |
| `docs/source-pin-ratchet-baseline.json` | `scripts/audit_source_text_pins.py` 快照 | `tests/test_source_pin_ratchet.py` 棘轮 | 只抓增长，不抓下降 | 低 |

**挂起项（接手条件写死）**：`gen_docs.py` 正文漂移守卫：待 `scaffold.py` /
`gen_docs.py` / `index.html` / docs 四处接线落库后补，接手条件=`git status`
对这四个路径为空且 `run_all.py` 已驱动。接线线在途，别抢。

## 3d. 生成物对账统一入口（2026-11-13）

§3c 判的是「有没有守卫」，本节回答「跑一次就知道现在同步没有」。统一入口
`python scripts/check_generated_artifacts.py`：`--list` 看登记表与豁免理由，
`--changed` 只查输入有改动的生成物，`--json` 给闸门解析。

- 判据是**重跑生成器、比对产物**（`--check` 形态；只能整篇写盘的走
  「快照→重跑→比对→还原」；`docs/error-catalog.md` 无独立脚本，按 `ERRORS`
  重建表行逐行对），不是人眼看内容对不对；
- 自动闸门挂在 `scripts/release_check.py` 的 `gen_reconcile` 步骤（发版前，
  约 3 秒）；**不挂 pre-commit**——钩子变慢会诱发 `--no-verify`（既有裁定）；
- 受查 6 项：shortcuts / mcp_tools / settings_inventory / incident_card_anchors /
  decoder_matrix / error_catalog；豁免 6 项（运行时审计报告、星文化快照、
  perf 证据、棘轮基线、时点报告、前端文档构建链），理由逐条写在脚本 `EXEMPT`；
- 首跑实测（HEAD `9c287c66`）：**1 个生成物不同步**——`docs/error-catalog.md`
  有 5 行（`bad_field` / `bad_param` / `bad_parameter` / `invalid_arguments` /
  `missing_required_params`）的文案与建议落后于 `ERRORS`，其余 5 项一致。
  这 5 行已按模块重建，复查全绿。

**xingguan 源快照钉逐项**（本轮补的是「离线可验的那一段」，不是重跑守卫）：

| 产物 | 生成器 | 输入源 | 现文件里的可校验字段 | 缺什么钉 | 补钉后能拦哪类漂移 |
|---|---|---|---|---|---|
| `xingguan_chinese.json` | `build_constellation_data.py` | `reference/stellarium/skycultures/chinese/{index.json, star_names.zh_CN.fab, description.md}` + HYG v4.1 | `meta.source_files.{index,star_names,description}.sha256`、`meta.data_sha256`、`region_count` / `yuan_count` / `xiu_count` / 各 mapped 计数 | 上游快照 SHA 只写在产物里，没有随仓常量锁 | 静默改写产物内的快照 SHA、改动 payload（`data_sha256`）、reference 在机时上游文件换版 |
| `xingguan_star_names_zh.json` | `build_xingguan_star_names.py` | 上述 `xingguan_chinese.json` + `star_names.zh_CN.fab` | `meta.source_file_sha256`、`meta.source_files.members.sha256`、`meta.data_sha256`、计数字段 | `members` 钉此前只记录、无测试对拍（本轮补 `test_xingguan_star_names_members_pin_matches_repo_input`） | **区域输入已变、names 没重跑**（离线可验）、payload 被改、计数与成员集合不一致（loader 抛 `ConstellationIntegrityError`） |

**反证（2026-11-06 实测，三项）**：

- S1 改一个区域数值、names 不重跑 → **红**（`xingguan_chinese.json 已变但 xingguan_star_names_zh.json 未重跑`）。
- S3 静默改写产物里的上游快照 SHA → **红**（`description 源快照钉被改动`）。
- S2 同时改输入 + `members` 钉 + `data_sha256`、但不重跑 payload → **绿**。这是**残余风险**：
  纯 SHA 钉无法区分「一整套钉都一致地改」与「真的重跑」；只有「重跑生成器并逐字节比对」能拦。
  本线不宣称覆盖 S2；若以后要拦，只能加重跑守卫（需 `reference/` 在机）。
- 条件覆盖：`test_source_files_sha_traceable` 在 `reference/` 缺失时 skip（只锁产物内记录值，不验上游实体）。

复跑：

```powershell
python -m pytest mbdsdr_ai/tests/test_constellations_data.py -q -k "source_snapshot_pins or members_pin"
```

`docs/perf-evidence/*.json` 是带时间/机器/工具链条件的**证据快照**（`conditions` 里写明
CPU、Qt offscreen、同机负载），不同次运行数值本就不同。**不要**给它加重跑/逐字节守卫，
否则只会对合法波动报警；验收时只引用，不重算。

---

## 4. 已知边界

- 星表覆盖只到 V ≤ 9.0（`CATALOG_DEPTH_MAG`）。`select_for_view` 收到更深
  请求时夹到 9.0 并置 `beyond_catalog_limit=True`，本表四组均未触发。
- 落笔数是几何量，受投影/画布影响（1.4）。它不是「星表被删条」的证据。
- OpenNGC 的 `Dup` 条目（652 条）保留在副本里以维持与上游逐字节一致；
  进入 117 条目录时不作为独立天体。
- 许可检查覆盖数据文件，不覆盖源代码注释中的「学机制」引用；
  后者由 `NOTICE.md` 与代码审查负责。

---

## 5. 复现命令

```powershell
# 1) 星表分层（数据/加载）
python -m pytest mbdsdr_ai/tests/test_star_catalog.py -q
# 2) DSO 完整性 + 列名契约 + M102
python -m pytest mbdsdr_ai/tests/test_dso_catalog.py -q
# 3) 许可检查（绿 + 逐条来源 + 反证红）
python scripts/check_data_licenses.py
python scripts/check_data_licenses.py --list
python scripts/check_data_licenses.py --probe
python -m pytest tests/test_data_licenses.py -q
# 3a) 帧布局哨兵口径 + 链路同频并列
python -m pytest mbdsdr_ai/tests/test_sat_profile.py mbdsdr_ai/tests/test_sat_links.py -q
# 3b) Web 产物复现：重跑生成器，豁免 generatedAt / repo.head 后逐字节比对
python web/product/tests/verify_build_json.py --check
git diff --stat -- web/product/build
```
