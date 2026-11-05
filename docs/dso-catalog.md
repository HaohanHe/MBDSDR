# 深空天体目录（DSO Catalog）

> 本文档说明 `mbdsdr_ai/data/dso_catalog.json` 的来源、许可、字段缺失口径、
> M102 约定与中文名策略。数据生成器为
> `mbdsdr_ai/astronomy/build_dso_catalog.py`，加载与校验在
> `mbdsdr_ai/astronomy/dso.py`。

## 1. 来源与许可

- **数据源**：OpenNGC `NGC.csv` + `addendum.csv`（作者 Mattia Verga 及贡献者）。
- **源地址**：https://github.com/mattiaverga/OpenNGC
- **许可**：**CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/）。
- **中文名**：Wikidata 标签（CC0 1.0），按 `P528`（星表编号）匹配；
  取不到且英文常用名可意译者自译，逐条标注 `zh_name_source="自译"`；
  仍取不到留空并计数。
- **缺失字段补充**（`mbdsdr_ai/data/dso_supplement.json`，逐字段记出处）：
  OpenNGC 原始行缺星等/角径时，从公开星表按原值补齐，**不做插值或估算**：

  | 字段 | 条数 | 编号 | 来源 | 出处 |
  | --- | --- | --- | --- | --- |
  | 视星等（V） | 7 | M22/M35/M62/M69/M70/M71/M107 | HEASARC `MESSIER` 表 | 据 Sky Catalogue 2000.0 Vol. 2 表 III（`1985skca.book.....H`） |
  | 长轴 | 1 | M40 | USNO `WDS` J12222+5805 | 最近一次观测双星分离 53.2″（2017）/60=0.887′ |
  | 长轴 | 1 | M73 | Lynga 开放星团表 `VII/92A` | `angular_diameter=2.8′`（`1987CoORo...16....1L`） |

  每条记录同时写入 `magnitude_source` / `size_source`（`catalog` = OpenNGC，
  否则为补充来源 id，`none` = 仍为空）；`dso.py` 对「有值却标 none」
  「空值却标来源」直接抛错。快照含 `fetched_at`、逐条 `catalog_row`
  原始行与 `payload_sha256`，消费方离线读取，禁止运行时联网。
- **数据快照**（SHA-256，写入 `meta.source_files`）：

| 源文件 | 字节 | 数据行 | SHA-256 |
| --- | --- | --- | --- |
| `NGC.csv` | 3,942,946 | 13,970 | `54abea0b57b0a103831bfc7a8e3a99a23acfbf4bb581fcf3d8af539cbb826c84` |
| `addendum.csv` | 17,496 | 64 | `c93f983dda6780b76b7b2e9271a856a892627710148a982ee98101a54c642fdd` |
| `dso_zh_wikidata.json`（Wikidata 快照，CC0） | — | 34 条 | `809168bbe75964558b5ace2102d39cb644841cea5c313506d1bd301085f3611d` |
| `dso_supplement.json`（补字段快照） | — | 9 条 | 见 `meta.source_files.supplement.sha256` |

- **产物**：117 条（Messier 1–110 全量 + 7 个常用亮 NGC：NGC 253/869/884/
  2070/5139/7000/7293），`meta.generated_at` 与 `meta.data_sha256` 随生成更新。

### 复现命令

```bash
curl -L -o reference/openngc/NGC.csv https://cdn.jsdelivr.net/gh/mattiaverga/OpenNGC@master/database_files/NGC.csv
curl -L -o reference/openngc/addendum.csv https://cdn.jsdelivr.net/gh/mattiaverga/OpenNGC@master/database_files/addendum.csv
python -m mbdsdr_ai.astronomy.build_dso_catalog --fetch-zh
python scripts/fetch_dso_supplement.py
python -m mbdsdr_ai.astronomy.build_dso_catalog --opengnc reference/openngc
```

`--fetch-zh` 只在需要刷新 Wikidata 快照时使用；`scripts/fetch_dso_supplement.py`
只在刷新补字段快照时使用（`--check` 可离线校验快照 SHA 与出处完整性）。
日常重建离线读取 `mbdsdr_ai/data/dso_zh_wikidata.json` 与
`mbdsdr_ai/data/dso_supplement.json`，不联网。

### 旧版已废弃

**旧版 `dso_catalog.json` 含 GPL 来源数据、已废弃。** 具体为
`a3aadd8` 及更早由 Stellarium `nebulae/default/catalog.txt`、
`names.dat` 与 `po/stellarium-sky/zh_CN.po` 生成的数据，许可为
GPL-2.0-or-later，不再随产品分发，也不在仓库当前数据中引用。
当前 `meta` 不含 GPL 字样；`dso.py` 的 `_check_meta` 一旦发现 meta 出现
`GPL` 会直接抛错。

## 2. M102 约定（钉死）

**OpenNGC 将 M102 记为重复条目（Dup，M=101）；本项目按历史约定取
NGC 5866，故条目数保持 117。**

- OpenNGC `addendum.csv` 行：`Name=M102, Type=Dup, M=101`；
- 本项目解析时固定映射到 `NGC.csv` 的 `NGC5866` 行（坐标/星等/角径/
  类型全部取该行，不复用 Dup 行）；
- 因此 Messier 仍为 110 条、总条目仍为 117 条；
- `meta.m102_convention` 记录 `resolved_to="NGC 5866"`、
  `entry_count=117`、`messier_count=110`；
- `mbdsdr_ai/tests/test_dso_catalog.py::test_m102_convention_pinned`
  与 `tests/test_dso.py::test_m102_maps_to_ngc5866` 钉住该约定；
  `dso.py` 的 `_check_entry` 也会拒绝 `M102` 指向 5866 以外的条目。
  **禁止改成 116 条。**

## 3. 缺字段一律留空并计数，不补造

源目录没有的字段写 JSON `null`，API 侧为 `None`，视图显示「未知」
（`—`），**不得用 `0` 或 `99` 冒充**。`dso.py` 对 `99` 星等与 `0` 尺寸
直接判非法；重复编号、SHA 篡改同样抛错。

「补齐」只允许从公开星表取**原值**（快照见 §1），并逐条写出处；
不做插值、不按类别均值填、不把双星分离之外的尺寸推算当实测。

| 字段 | 缺失数 | 缺失清单 |
| --- | --- | --- |
| 视星等 `magnitude` | **0** | （已补齐：HEASARC `MESSIER`） |
| 长轴 `size_arcmin` | **0** | （已补齐：M40←WDS、M73←Lynga） |
| 短轴 `size_minor_arcmin` | **66** | 见 `meta.missing.size_minor_arcmin.designations` |
| 英文常用名 `name_en` | **81** | 见 `meta.missing.name_en.designations` |
| 中文名 `zh_name` | **83** | 见 `meta.missing.zh_name.designations` |

- 星等口径：优先 OpenNGC `V-Mag`，缺失时用 `B-Mag` 并以
  `magnitude_band` 标注；仅当两者皆缺且补充快照无值时写 `null` 且
  `magnitude_band=""`。实际分布：`V=116`、`B=1`（NGC 7000 = 4.0，仅有 B
  波段），无空值。
- **M40（Winnecke 4）**：OpenNGC 为双星 `**`，无 `MajAx/MinAx`；长轴取
  WDS 最近一次观测的双星分离 **53.2″ = 0.887′**（`size_source="wds"`），
  短轴仍为 `null`（双星没有「短轴」，不做推算）。渲染类型 `double_star`。
  常用名列为空，故 `name_en`/`zh_name` 也为空。
- **M73（NGC 6994）**：OpenNGC 为 `Other`（星群），无尺寸；长轴取 Lynga
  `VII/92A` 的 `angular_diameter=2.8′`（`size_source="lynga"`）。渲染类型
  按 `cluster` 映射。
- **7 个无星等天体**：M22/M35/M62/M69/M70/M71/M107 从 HEASARC `MESSIER`
  表取 `vmag`（V 波段，`magnitude_source="heasarc_messier"`）。
- **轴比 `axis_ratio`**：仅当长轴与短轴都有时由数据层给出
  `axis_ratio = size_minor_arcmin / size_arcmin`，并标
  `axis_ratio_source="catalog"`（51 条）；缺短轴一律 `axis_ratio=null` +
  `axis_ratio_source="unknown"`（66 条）。**数据层不产生 `estimated`**：
  估计是视图的事，必须单独标注「估计」，不能让默认 0.55 看起来像实测。

## 4. 中文名：Wikidata（CC0）优先，自译逐条标注

- 只取 Wikidata `zh` / `zh-cn` / `zh-hans` / `zh-hant` 标签，拒绝纯编号标签；
  字符级繁→简转写，不改词。
- 自译仅限语义明确的英文常用名，**不做音译、不拿拼音/英译中冒充中文名**；
  逐条写 `zh_name_source="自译"` 与逐条依据。
- **绝对不使用 GPL 的 `zh_CN.po` 译文**；当前数据中不存在任何该类来源。
- 覆盖：**34 / 117**（Wikidata 29 + 自译 5），其余 83 条留空并计数。

| 编号 | 中文名 | 来源 |
| --- | --- | --- |
| M11 | 野鸭星团 | 自译（Wild Duck Cluster） |
| M13 | 武仙座球状星团 | 自译（Hercules Globular Cluster） |
| M17 | 天鹅星云 | 自译（Swan Nebula） |
| M99 | 后发座风车星系 | 自译（Coma Pinwheel） |
| NGC2070 | 蜘蛛星云 | 自译（Tarantula Nebula） |

其余 29 条为 Wikidata 标签（`zh_name_source="Wikidata"`，
`zh_name_ref` 含 QID 与 `P528` 编号）：M1、M6、M7、M8、M16、M20、M24、
M27、M31、M33、M42、M44、M45、M51、M57、M63、M64、M76、M81、M82、M83、
M87、M97、M101、M104、NGC253、NGC5139、NGC7000、NGC7293。

## 5. 与旧手写表的偏差（换源后）

对照 `a3aadd8^:mbdsdr_ai/astronomy/dso.py` 的 `_DSO_ROWS`（117 行手写表），
按坐标 `>1e-6°`、星等 `>1e-6`、尺寸 `>1e-6′` 判“不同”：

- **117 / 117 条至少一个字段不同**；
- 坐标：117 条全有差异，`>0.01°` 112 条、`>0.1°` 95 条、**`>1°` 49 条**；
  最差 M102 13.59°、M74 13.24°、M108 11.64°、M56 10.75°、M95 10.10°、
  M72 9.91°、M96 9.87°、M44 9.68°、M19 9.53°、M79 8.19°
  （旧表坐标多为四舍五入值，M102 的 RA 本身抄错约 1h）；
- 星等：`>0.01` 79 条、`>0.5` 24 条、`>1.0` 7 条；7 条新源无星等（旧表有旧值）；
- 尺寸：`>0.05′` 108 条、`>1′` 86 条、`>10′` 21 条；2 条新源无尺寸；
- 星座不同 3 条：M102 `UMa→Dra`（NGC 5866 在 Draco）、
  NGC 869/884 `Cas→Per`。

> 结论：旧手写表不是可继续沿用的“参考真值”；换源后应以 OpenNGC 为唯一
> 事实源，旧表仅作历史记录。

## 6. CC BY-SA 4.0 署名与放置（待协调落地）

CC BY-SA 4.0 要求**署名**且**改编数据库以同许可分发**。本文件即为该许可
的落地说明；仓库级 NOTICE 尚待维护者协调后再改，以下为建议文案：

### 建议署名文案（英文，可直接放入 NOTICE / 关于框）

> Deep-sky object data derived from **OpenNGC**
> (https://github.com/mattiaverga/OpenNGC) by Mattia Verga and contributors,
> licensed under **CC BY-SA 4.0**
> (https://creativecommons.org/licenses/by-sa/4.0/).
> This derived catalog (`mbdsdr_ai/data/dso_catalog.json`) is distributed
> under the same license, CC BY-SA 4.0.
> Chinese names are Wikidata labels (CC0 1.0) matched by P528 catalog
> number; entries without a Wikidata label are marked as self-translated
> (自译) or left empty.

### 放置位置

1. **`NOTICE.md`**（仓库级，需维护者协调后由维护者添加）：
   完整署名 + 许可链接 + “本派生数据库以 CC BY-SA 4.0 分发”。
2. **本文档**（`docs/dso-catalog.md`）：已含来源、许可、SHA 与复现命令。
3. **产品「关于」框 / 状态卡**：一行简短署名
   `DSO data: OpenNGC (CC BY-SA 4.0); names: Wikidata (CC0)`，
   并链接到本文档。
4. **`mbdsdr_ai/data/dso_catalog.json` 的 `meta.attribution`**：机器可读
   署名（已写入，随数据分发）。

> `NOTICE.md` / `LICENSING.md` 等全仓文件**未在本轮改动**，待维护者协调
> 后再统一落位。

## 7. 类型映射与渲染

原始目录类型保留在 `catalog_type`，视图渲染类型在 `dso_type`：

| OpenNGC 类型 | 渲染类型 | 条数 |
| --- | --- | --- |
| `G` | `galaxy` | 41 |
| `GCl` / `OCl` / `*Ass` / `Other` | `cluster` | 60 |
| `PN` / `Neb` / `HII` / `RfN` | `nebula` | 13 |
| `Cl+N` | `cluster_nebula` | 1 |
| `SNR` | `supernova_remnant` | 1 |
| `**` | `double_star` | 1 |

`cross_ids` 一律取 OpenNGC `Name` 中的编号；`NGC`/`IC` 交叉列在 Dup 行
附近可能与 `Name` 互换（如 `NGC6523` 行写 `NGC=6533`），故不采用。
