# 星座覆盖与命名体系（IAU 88 座 + 中国星官）

> 结论先行：IAU 88 座已补齐（拉丁名/缩写/中文名/示意连线，88/88）；
> 中国星官最小可用集已交付（三垣 + 二十八宿，31 区域、287 星官、
> 2,910 颗成员星，其中 2,907 颗映射到 HYG、3 颗如实留空）。
> 两套体系分别存于两个数据文件、两个模块，名称空间不相交；可同时打开，
> 每层强制带 `system_id`/`display_name` 徽标。
> **星座连线不是 IAU 官方定义**：IAU 只定义 88 个星座的名称与边界，
> 连线图来自 Stellarium `modern_iau`（CC BY-SA 4.0，与 Sky & Telescope
> 合作并发布于 IAU 网站），仅作示意。未改 `desktop/`、`cpp/`、CMake、CI，
> 未联网，未凭记忆补任何星点。

---

## 1. 数据来源与许可

### 1.1 IAU 88 座

| 项 | 值 |
| --- | --- |
| 源 | Stellarium `skycultures/modern_iau/index.json` |
| 来源 URL | https://github.com/Stellarium/stellarium/tree/master/skycultures/modern_iau |
| 许可 | **CC BY-SA 4.0**（https://creativecommons.org/licenses/by-sa/4.0/） |
| 图形出处 | Stellarium 与 Sky & Telescope（Roger Sinnott、Rick Fienberg）合作制作，发布于 IAU 网站 |
| 源文件 SHA-256 | `6a5f5251be8d8965…`（完整值在数据文件 `meta.source_file_sha256`） |
| 随仓文件 | `mbdsdr_ai/data/constellations_iau.json`（约 544 KiB） |

**连线性质（必须如实标注）**：

- IAU 定义的是 **88 个星座的名称与天区边界**，不定义“把哪几颗星连起来”；
- 本文件的 `lines` 是传统/民间约定的示意图形，**不是 IAU 官方连线**；
- 数据文件 `meta.lines_are_iau_official = false`，
  `meta.lines_note` 明确写“不是 IAU 官方定义”；
- `lines` **不能**当作星座边界或科学坐标依据。

**中文名来源**：

- 旧版 27 座沿用 `mbdsdr_ai/data/constellation_culture.json` 的人工中文名/文化注记；
- 其余 61 座取自 Stellarium 中文翻译
  `po/stellarium/zh_CN.po`、`po/stellarium-skycultures/zh_CN.po`
  的 `msgctxt "IAU constellation name"` 条目；
- 翻译文件许可为 **GPL-2.0-or-later**；其中星座中文名是短事实性名称，
  本数据仅记录“来源/许可”，不从记忆补写。逐座来源见
  `constellations[].zh_source`。

### 1.2 中国星官（三垣二十八宿）

| 项 | 值 |
| --- | --- |
| 源 | Stellarium `skycultures/chinese/` |
| 来源 URL | https://github.com/Stellarium/stellarium/tree/master/skycultures/chinese |
| 许可 | **CC BY-SA 4.0** |
| 文化依据 | 源 `description.md`：依《仪象考成》（1756）及其续编（1844），参考伊世同《中西对照恒星图表 1950.0》 |
| 成员表 | `star_names.zh_CN.fab`（源文件 SHA-256 `11ed413bbf056228…`） |
| 连线表 | `index.json`（源文件 SHA-256 `28121629c86f4ba6…`） |
| 名称表 | `description.md`（源文件 SHA-256 `06130eb52fc52d1f…`） |
| 随仓文件 | `mbdsdr_ai/data/xingguan_chinese.json`（约 2.9 MiB） |

**星官边界**：源 `description.md` 明确说明——“Xingguans refer only to the
way stars are distributed, with no specific boundaries between
Xingguans”（星官只表示恒星分布，没有具体边界）。因此本数据
`boundary_source = null`，不提供星官边界多边形；`meta.boundary_note`
如实记录该说明。

### 1.3 恒星坐标与名称

- 源数据只给 **HIP 编号**；生成脚本按 **HIP → HYG v4.1 id 精确匹配**，
  并把 HYG 的 `proper` / Bayer-Flamsteed（`bf`）名写入每条星引用；
- HYG Database v4.1，许可 **CC BY-SA 4.0**；
- 随仓星表只含 V ≤ 9.0（83,479 颗）；因此另区分：
  - `mapped`：能在 HYG 全表找到；
  - `in_catalog`：同时在随仓 V ≤ 9.0 星表内（可直接绘制）；
  - 映射不到：`hyg_id = null`、坐标留空，并计入 `unmapped_count`。

---

## 2. 两套体系：分开存、分开显示、可同时打开

| 维度 | IAU 88 座 | 中国星官 |
| --- | --- | --- |
| 数据文件 | `data/constellations_iau.json` | `data/xingguan_chinese.json` |
| 模块 | `mbdsdr_ai/skyengine/constellations.py` | `mbdsdr_ai/skyengine/xingguan.py` |
| `system_id` | `iau_88` | `chinese_xingguan` |
| 显示名 | `IAU 88 星座` | `中国星官（三垣二十八宿）` |
| 徽标 `tag` | `IAU` | `中国星官` |
| 主键 | 三字母缩写（`Ori`、`UMa`…） | 中文区域名（`紫微垣`、`角宿`…） |
| 名称空间 | ASCII 三字母 | 中文，二者交集为空 |
| 边界 | IAU 官方边界多边形**未纳入本次交付** | 源明确“无具体边界” |

同时打开时用 `overlay_layers()`：

```python
from mbdsdr_ai.skyengine.constellations import overlay_layers

layers = overlay_layers()            # 默认两套都开
for layer in layers:
    print(layer.system_id, layer.tag, layer.display_name, len(layer.items))
```

输出：

```
iau_88            IAU       IAU 88 星座            88
chinese_xingguan  中国星官   中国星官（三垣二十八宿）   31
```

**渲染纪律**：界面必须为每层显示 `display_name` 或 `tag`；不得把
`Ori` 与 `角宿` 合并成同一个名称空间，也不得把两套连线画成同一种
“官方星座边界”。`overlay_layers()` 只并列返回，不做任何跨体系合并。

单独取用：

```python
# IAU
from mbdsdr_ai.skyengine import constellations as iau
con = iau.load_iau_constellations()["Ori"]
con.latin_name, con.zh_name, con.segment_count

# 中国星官
from mbdsdr_ai.skyengine import xingguan
r = xingguan.xingguan_region("紫微垣")
r.xingguan_count, r.star_count, r.unmapped_count
```

标签：

```python
iau.iau_constellation_label("Ori", with_system_tag=True)   # "[IAU] 猎户座 Orion"
xingguan.xingguan_label("角宿", with_system_tag=True)      # "[中国星官] 角宿"
```

---

## 3. 覆盖率

### 3.1 IAU 88 座

| 指标 | 数值 |
| --- | --- |
| 星座数 | **88 / 88** |
| 缩写 / 拉丁名 / 中文名唯一性 | 各 88 个，无重复 |
| 交付连线段数 | **842 段**（源 843 段） |
| 因端点不在 HYG 而裁掉 | **1 段** |
| 未映射端点 | **HIP 55203**（HYG v4.1 无此行） |
| 连线端点星（去重） | **744 颗**，全部在随仓 V ≤ 9.0 星表内 |
| 每座段数 | 1–31 段（源图形差异，不人为补齐） |
| 中文名 | 27 座来自现有文化 JSON，61 座来自 Stellarium 中文翻译 |

> 说明：`modern_iau` 的源图形里 HIP 55203 位于一个折线末端；生成脚本
> 在未映射端点处**断开折线并丢弃受影响段**，同时把该 HIP 写进
> `meta.unresolved_hips` 与 `meta.dropped_segment_count`。没有用邻近星
> 猜测替代，也没有把该端点“补”成别的星。

### 3.2 中国星官最小可用集

| 指标 | 数值 |
| --- | --- |
| 区域 | **31** = 三垣 3 + 二十八宿 28 |
| 星官（含垣墙） | **287** |
| 成员星（去重 HIP） | **2,910** |
| 能映射到 HYG v4.1 | **2,907** |
| 同时在随仓 V ≤ 9.0 星表 | **2,906** |
| 映射不到（如实留空） | **3** |
| 映射到 HYG 但比 9 等更深 | **1**（HIP 115481，`in_catalog=false`） |
| 有连线的星官 | **274 / 287**（13 个源数据未给连线，保持为空） |
| 区域级连线 | 181 段 |
| 星官级连线 | 1,012 段（与区域级可能有重复，未去重合并） |

**未映射清单（全部如实留空）**：

| HIP | 区域 | 中文星名（源） | 原因 |
| --- | --- | --- | --- |
| 55203 | 太微垣 | 三台六 | HYG v4.1 无此 HIP 行 |
| 78727 | 房宿 | 西咸一 | HYG v4.1 无此 HIP 行 |
| 115125 | 室宿 | 羽林军卅六 | HYG v4.1 无此 HIP 行 |

三条未映射记录在数据里均为：

```json
{"hip": 55203, "hyg_id": null, "proper": "", "bayer": "",
 "ra_deg": null, "dec_deg": null, "vmag": null,
 "con": "", "in_catalog": false, "mapped": false}
```

**口径说明**：

- 三垣的“所含恒星”= `star_names.zh_CN.fab` 中
  `###紫微垣/太微垣/天市垣` 分节下全部星官恒星的并集；
  绘制连线只使用左/右垣两条墙线（源数据里垣本身只有墙线），
  垣内其他星官的连线保存在各 `xingguan[].lines`。
- 二十八宿的“所含恒星”= 该宿 `##[X宿]` 分节下
  `#X宿` 及其附属星官的并集；`region.xingguan[0]` 即该宿自身。
- 星的 `zh_name`（如“角宿一”“勾陈一”）来自源 `star_names.zh_CN.fab`；
  同一 HIP 的多个中文名保存在 `alt_names`。不从记忆补星名。

---

## 4. 数据结构

### 4.1 `constellations_iau.json`

```jsonc
{
  "meta": {
    "schema": "mbdsdr.iau_constellations.v1",
    "system_id": "iau_88",
    "source": "Stellarium skyculture modern_iau",
    "source_url": "https://github.com/Stellarium/stellarium/tree/master/skycultures/modern_iau",
    "source_license": "CC BY-SA 4.0",
    "source_license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
    "source_file_sha256": "…",
    "lines_are_iau_official": false,
    "lines_note": "…不是 IAU 官方定义…",
    "boundary_source": null,
    "boundary_note": "官方边界多边形未纳入…",
    "constellation_count": 88,
    "segment_count": 842,
    "original_segment_count": 843,
    "dropped_segment_count": 1,
    "unresolved_hips": [55203],
    "data_sha256": "…"
  },
  "constellations": [
    {
      "abbr": "Ori", "latin_name": "Orion", "english_name": "Orion",
      "zh_name": "猎户座", "zh_source": "…",
      "lines": [[{星引用}, …], …],
      "stars": [{星引用}, …],
      "star_count": 8, "segment_count": 16,
      "anchor_hyg_id": 27973
    }
  ]
}
```

星引用字段：`hip / hyg_id / proper / bayer / ra_deg / dec_deg / vmag /
con / in_catalog`（J2000，度；`ra_deg` 已由源 CSV 的小时值 ×15）。

### 4.2 `xingguan_chinese.json`

```jsonc
{
  "meta": {
    "schema": "mbdsdr.chinese_xingguan.v1",
    "system_id": "chinese_xingguan",
    "region_count": 31, "yuan_count": 3, "xiu_count": 28,
    "xingguan_count": 287,
    "unique_star_hip_count": 2910,
    "mapped_count": 2907,
    "in_catalog_count": 2906,
    "unmapped_count": 3,
    "unmapped_hips": [55203, 78727, 115125],
    "mapping": "Exact HIP -> HYG v4.1 id match …",
    "boundary_source": null,
    "boundary_note": "…星官没有具体边界…",
    "source_files": {"index": {"sha256": "…"}, "star_names": …, "description": …},
    "data_sha256": "…"
  },
  "regions": [
    {
      "key": "紫微垣", "type": "yuan", "group": "三垣",
      "name_en": "Purple Forbidden Enclosure", "pinyin": "Zǐwěiyuán",
      "components": ["紫微右垣", "紫微左垣"],
      "xingguan_count": 39,
      "xingguan": [
        {
          "name_zh": "北极", "name_en": "...", "pinyin": "...",
          "stars": [{星引用}, …], "lines": [[…], …]
        }, …
      ],
      "stars": [{星引用}, …],
      "lines": [[…], …],
      "star_count": 320, "mapped_count": 320, "in_catalog_count": 320,
      "unmapped_count": 0, "unmapped_hips": []
    }, …
  ]
}
```

星引用额外字段：`zh_name / alt_names / mapped`；未映射时
`hyg_id/ra_deg/dec_deg/vmag = null`。

---

## 5. 接口

```python
from mbdsdr_ai.skyengine import constellations as iau
from mbdsdr_ai.skyengine import xingguan

# IAU
ds = iau.load_iau_constellations()            # 88 座，含 meta
con = ds["Ori"]
con.latin_name, con.zh_name, con.star_count
pairs = iau.iau_constellation_segments("Ori") # ((starA, starB), …)
anchor = iau.iau_constellation_anchor("Ori")

# 中国星官
xs = xingguan.load_xingguan()                 # 31 区域，含 meta
r = xingguan.xingguan_region("角宿")
r.xingguan_count, r.star_count, r.unmapped_hips
pairs = xingguan.xingguan_segments("角宿")     # 区域级连线
pairs2 = xingguan.xingguan_all_segments("角宿")# 区域级 + 星官级

# 两套体系并列
layers = iau.overlay_layers()                 # 每层带 system_id/tag
```

**加载守卫**：两份数据加载时都会校验 `meta` 必需字段（来源 URL、许可、
许可 URL、来源 SHA、数据 SHA…）并重算 `data_sha256`；缺失或篡改分别抛
`ConstellationMetadataError` / `ConstellationIntegrityError`，不静默降级。

---

## 6. 复现

```powershell
python -m mbdsdr_ai.skyengine.build_constellation_data `
    --stellarium D:/mbdsdr/reference/stellarium `
    --hyg-csv D:/mbdsdr/scratch/hygdata_v41.csv
```

脚本只读本地 Stellarium 参考数据与 HYG CSV，输出两份 JSON 并写入
来源/许可/源文件 SHA/数据 SHA。源参考数据与 HYG CSV 均**不随仓分发**。

---

## 7. 测试

新增 `mbdsdr_ai/tests/test_constellations_data.py`，共 **29 项**，覆盖：

- IAU 88 座数量、缩写/拉丁名/中文名唯一性；
- 每座连线端点都在随仓 HYG 星表内，且 HYG id / HIP / ra / dec / vmag 对拍；
- 未映射端点被裁掉并计数，连线方案注明“非 IAU 官方”；
- 中国星官 31 区域（三垣 3 + 二十八宿 28）、287 星官；
- 2,910 / 2,907 / 2,906 / 3 的覆盖率与未映射清单断言；
- 未映射星字段为空、无坐标、无 proper/Bayer，不凭记忆补星；
- 两套体系名称空间不相交、文化注记不串味、并列图层逐层带 tag；
- 两份数据 meta 来源/许可/SHA；缺失字段/篡改数据被拒绝；
- 本地 Stellarium 源文件 SHA 与实际文件对拍；
- 旧版 27 座 / 115 段桌面骨架保持兼容。

运行：

```powershell
python -m pytest mbdsdr_ai/tests/test_constellations_data.py -q   # 29 passed
```

---

## 8. 未验证 / 缺什么

1. **IAU 官方边界多边形未交付**：本数据只有名称与示意连线；`meta` 已
   明确 `boundary_source=null`、`lines` 不能当边界。若确实需要官方边界
   多边形，需要另行确认许可清晰的数据源（本机仅有 AGPL-3.0 的
   stellarium-web-engine 生成表，未擅自取用）。
2. **连线图形不是唯一标准**：Stellarium `modern_iau`、`modern`、
   `modern_st` 等天空文化的连线不同；本数据采用 `modern_iau`，不宣称
   唯一正确。中国星官同理，源数据本身是文化整理，非现代天文边界。
3. **中国星官只交付最小可用集**：三垣 + 二十八宿（31 区域、287 星官）；
   源数据还有“近南极天区”等区域，本次未纳入。
4. **13 个星官没有连线**：源 `index.json` 未给这些星官连线，本数据保持
   `lines=[]`，未用邻近星臆造；区域级主线仍可绘制。
5. **星官成员星包含“增星”**：`star_names.zh_CN.fab` 的 `#X宿` 分节包含
   该宿的增星（如角宿 17 颗），并不等于“传统主星 2 颗”；源表的
   `primary_count/added_count` 已保留在 `primary_star_count_source /
   added_star_count_source`，使用方需自行区分。
6. **映射口径**：源只给 HIP，本数据用 HIP 精确匹配 HYG，不是按中文星名
   模糊匹配；`proper/Bayer` 仅作展示/审计，不参与定位。HYG 全表与随仓
   V ≤ 9.0 表的差异已用 `mapped` / `in_catalog` 分开记录。
7. **未做自行（proper motion）传播**：坐标为 HYG 发布的 J2000 位置；
   近距高自行星在数十年尺度会有偏移，与 `docs/star-catalog.md` 的缺口
   一致。
8. **文化注记不完整**：IAU 仅旧版 27 座有 `constellation_culture.json`
   的人工注记；其余 61 座只有中文名。中国星官的文化说明在源
   `description.md` 中，本次只抽取名称/拼音/英文与成员，未把长篇文化
   叙述逐条搬进数据文件。
9. **UI 未接线**：`desktop/` 仍使用旧版 27 座亮星骨架；88 座与星官数据
   通过新接口提供，待界面线接入。未修改 `desktop/`、`cpp/`、CMake、CI。
10. **许可边界**：Stellarium 天空文化数据为 CC BY-SA 4.0；中文翻译文件为
    GPL-2.0-or-later；HYG 为 CC BY-SA 4.0；代码 MIT。再分发数据需保留
    署名与同许可，详见 §1。

---

## 9. 接入契约（天空线照此实现，无需回读源码）

本节是数据层对渲染/UI 线的**稳定契约**：字段名、坐标口径、分层阈值、
单帧预算与标签优先级。改动其中任何一项，必须同步本文件与
`mbdsdr_ai/tests/test_constellations_data.py`。

### 9.1 `constellation_layer()` / `overlay_layers()` 返回什么

```python
from mbdsdr_ai.skyengine import constellations as iau
from mbdsdr_ai.skyengine import xingguan

layer = iau.constellation_layer("iau_88")          # 或 "chinese_xingguan"
layers = iau.overlay_layers()                      # 默认两套；可传 (sid, …) 子集
```

`ConstellationLayer`（`@dataclass(frozen=True)`，两套体系同一个类）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `system_id` | `str` | `"iau_88"` / `"chinese_xingguan"` |
| `display_name` | `str` | 中文显示名，用于图例/面板标题 |
| `tag` | `str` | 徽标：`"IAU"` / `"中国星官"` |
| `source` / `source_license` | `str` | 来源与许可（必显示） |
| `items` | `tuple` | IAU：`IAUConstellation`；星官：`XingguanRegion` |
| `keys()` | `tuple[str, …]` | IAU 为三字母缩写，星官为区域键（`角宿`…） |

`overlay_layers()`：按 `(iau_88, chinese_xingguan)` 顺序返回列表，重复
`system_id` 去重，未知 `system_id` 抛 `KeyError`；**不做任何跨体系合并**。
`system_ids()` 返回固定二元组；`system_info(sid)` 返回
`system_id / display_name_zh / display_name_en / tag / source / source_url /
source_license / lines_are_iau_official / count`（星官额外带
`region_count / xingguan_count / unmapped_count`）。

**IAU `items`（`IAUConstellation`）**：`abbr, latin_name, english_name,
zh_name, zh_source, lines, stars, star_count, segment_count,
original_segment_count, dropped_segment_count, unresolved_hips,
anchor_hyg_id`；方法 `segments()`（展平成 `(starA, starB)` 元组）、
`anchor()`；属性 `label` = `"猎户座 Orion"`。

**星官 `items`（`XingguanRegion`）**：`key, type("yuan"|"xiu"), group,
name_zh, name_zh_source, name_en, pinyin, components, xingguan,
xingguan_count, stars, lines, segment_count, star_count,
unique_star_hip_count, mapped_count, in_catalog_count, unmapped_count,
unmapped_hips, unresolved_tokens`；方法 `segments()`（区域级：三垣=墙线，
宿=宿自身连线）、`all_segments()`（区域级 + 区域内星官级，按 HIP 对去重）。

**星引用 `ConstellationStar` / `XingguanStar`**：

| 字段 | IAU | 星官 | 说明 |
| --- | --- | --- | --- |
| `hip` | ✓ | ✓ | 源编号，主键级可追溯 |
| `hyg_id` | ✓ | ✓（可能 `None`） | HYG v4.1 id |
| `proper` / `bayer` | ✓ | ✓ | 显示名兜底链：`zh_name` → `proper` → `bayer` → `HIP xxx` |
| `ra_deg` / `dec_deg` | ✓ | ✓（可能 `None`） | **J2000，度** |
| `vmag` | ✓ | ✓（可能 `None`） | 视星等 |
| `con` | ✓ | ✓ | IAU 星座归属 |
| `in_catalog` | ✓ | ✓ | 是否在随仓 V ≤ 9.0 星表内 |
| `mapped` | — | ✓ | 是否映射到 HYG 全表 |
| `zh_name` / `alt_names` | — | ✓ | 源中文星名与别名 |

### 9.2 不变量（渲染方可直接依赖，均已由测试钉死）

1. `lines` 是**折线元组**：每条折线 ≥ 2 个端点；同一条折线内相邻点连一段。
2. IAU 88 座**全部连线端点** `in_catalog=True`（实测 744/744，无例外）。
3. 星官连线端点由加载守卫强制 `mapped=True` 且 `in_catalog=True`：
   未映射星（3 颗）**绝不会出现在 `lines` 里**，只出现在 `stars` 成员表。
4. 两套体系 `keys()` 交集为空：IAU 是三字母 ASCII，星官是中文区域名。
5. `lines` **不是**星座边界，不得用于点选归属/命中测试；`boundary_source`
   两套均为 `null`。
6. 对象不可变（`frozen` dataclass + tuple），可跨线程共享；
   `load_iau_constellations()` / `load_xingguan()` 为 `lru_cache(maxsize=1)`。

**缓存口径（实测）**：首次加载 IAU 21.3 ms、星官 122.0 ms（含 JSON 解析与
SHA 校验），命中缓存后约 0.001 ms。`xingguan_all_segments()` **每次重建**，
31 区域合计 0.26 ms/次——渲染方应在切换体系时预计算一次并缓存，不要每帧调。

### 9.3 单帧几何量（实测，非估算）

统计口径：`point instances` = 所有折线端点的**出现次数**；
`unique stars` = 去重后实际需投影的星数；`segments` = 相邻端点对。

| 图层 | 折线数 | point instances | segments | unique stars |
| --- | --- | --- | --- | --- |
| IAU 88 座 | 148 | 990 | 842 | 744 |
| 星官区域级（三垣墙线 + 28 宿） | 63 | 244 | 181 | — |
| 星官内部（287 星官，274 个有连线） | 361 | 1,373 | 1,012 | — |
| **全部同开（去重后）** | **572** | **2,607** | **1,854** | **1,466** |

> 1,854 = 842（IAU）+ 1,012（星官去重后）；区域级的 181 段全部与星官级
> 重叠，故不重复计入。星官成员共 2,910 颗，但只有 1,244 颗真正落在连线
> 端点上（其余是成员表里的星，不产生线段）。

**纯数据层每帧开销（Python，实测）**：

| 操作 | 规模 | 耗时 |
| --- | --- | --- |
| RA/Dec → 单位向量 | 2,607 个端点 | **1.39 ms** |
| 段法向量/点积循环 | 1,854 段 | **0.69 ms** |

即若在 Python 侧做完整坐标换算，约 **2.1 ms/帧**；渲染方若把这段放到
C++/GPU 着色器，可不计入 Python 预算。

**真实绘制开销（Qt 光栅 offscreen，实测）**：1280×720 `QImage`、
`QPainter` 抗锯齿、1 px 笔、每折线一条 `QPainterPath`，
含 RA/Dec→屏幕点换算：

| 档位 | 折线数 | 实测 |
| --- | --- | --- |
| T0 只画 IAU 88 座 | 148 | **4.65 ms** |
| T1 IAU + 三垣/28宿区域线 | 211 | **5.59 ms** |
| T2 全部（IAU + 区域 + 287 星官） | 572 | **7.67–9.66 ms**（两次运行，机器噪声） |

60 fps 预算 16.7 ms：T2 已占约一半，余量留给星点/标签/UI。
**超过 1080p、开粗笔/阴影或关闭抗锯齿都会改变该数字，需按目标机复测**。
标签绘制未实测（headless `drawText` 在本机崩溃），不编造毫秒数，只给
§9.5 的优先级与数量上限。

### 9.4 按 FOV 分层的建议阈值

以「星官内部连线」为主要成本开关（361 条折线 / 1,012 段）：

| FOV | 打开内容 | 实测/上限 |
| --- | --- | --- |
| ≥ 60°（全天/半球） | IAU 148 条折线 + 三垣墙线 + 28 宿自身连线 | T1 ≈ 5.6 ms |
| 25°–60° | T1 + **仅视场内**的星官内部连线（≤ 361 条，按视场裁剪） | 5.6–7.5 ms |
| 8°–25° | 全部 572 条折线（T2） | 7.7–9.7 ms |
| < 8° | 仅当前宿/垣及其相邻区域的星官；IAU 仍全开 | 随裁剪下降 |

补充：星点本身已由 `star_catalog.select_for_view()` 按视场/极限星等裁剪，
星座线不受该预算约束；建议把「视场裁剪」作用在**折线端点**上（两端都
出视场才跳过），避免逐段求交。

### 9.5 标签避让优先级（推荐，按此降级）

数量上限按档位：FOV ≥ 60° 最多 12 个；25°–60° 最多 24 个；8°–25°
最多 40 个；< 8° 最多 20 个。

| 优先级 | 对象 | 数量 | 锚点来源 | 备注 |
| --- | --- | --- | --- | --- |
| P0 | 体系徽标/图例 | 2 | `layer.tag` | 常驻，不可被避让掉 |
| P1 | 三垣 | 3 | 区域成员最亮星 | `type == "yuan"` |
| P2 | IAU 88 座 | 88 | `con.anchor()`（最亮连线端点） | 按 `vmag` 升序取前 N |
| P3 | 二十八宿 | 28 | 区域成员最亮星 | `type == "xiu"` |
| P4 | 星官 | 287 | 星官成员最亮星（跳过 `in_catalog=False`） | 仅当前宿/垣内 |

- 星官没有 `anchor` 字段，锚点须由渲染方从 `stars` 里按
  「`in_catalog=True` 且 `vmag` 最小」自算；全部不可绘制时**不画标签**。
- 避让顺序严格 P0→P4；同级按 `vmag` 升序、再按屏幕距离取远。
- 两套体系同开时，P2 与 P3/P4 的标签**必须带体系前缀**（见 §9.6），
  不允许同名合并或省前缀。

### 9.6 两套体系同开时 UI 必须怎么标

1. **每层一个徽标**：图例/面板标题显示 `layer.tag` + `layer.display_name`
  （`"IAU"` / `"中国星官"`）；只开一套时同样显示，不得省略。
2. **线条样式区分**：建议 IAU 细实线、星官虚线或异色；两套同时打开时
   必须让用户一眼看出哪条属于哪套。
3. **标签带前缀**：`iau_constellation_label(abbr, with_system_tag=True)`
   → `[IAU] 猎户座 Orion`；`xingguan_label(key, with_system_tag=True)`
   → `[中国星官] 角宿`。
4. **禁止的措辞**：不得把连线称为「官方星座边界」；IAU 层需可展示
   `source_license` 与「连线非官方」说明（数据 meta
   `lines_are_iau_official=false`）。
5. **切换语义**：切换体系只改 `overlay_layers()` 入参，不清空另一套的
   缓存；两套的 `system_id` 与名称空间永不相同，禁止读写对方的索引。

