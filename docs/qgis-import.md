# 在 QGIS 中加载 MBDSDR 图层与样式

## 一键包（推荐）

数据与样式已落在 `D:\mbdsdr\output\` 后，可以直接打成可分发的 zip：

```powershell
python scripts/export_qgis_bundle.py
# 默认读 D:\mbdsdr\output，写 D:\mbdsdr\output\mbdsdr-qgis-bundle.zip
```

常用选项：

```powershell
python scripts/export_qgis_bundle.py --src D:\mbdsdr\output `
    --out D:\tmp\mbdsdr-qgis-bundle.zip   # 改输入/输出位置
python scripts/export_qgis_bundle.py --force   # 覆盖已存在的 zip
```

产物：

- `mbdsdr-qgis-bundle.zip`：扁平放 6 个图层文件（3 个 GeoJSON + 3 个同名
  QML）和 `README.txt`（导入步骤、来源/许可、生成时间）；
- `mbdsdr-qgis-bundle.json`：清单，含 zip 的 `size`/`sha256`、每个成员的
  `name`/`size`/`sha256` 与 `generated_utc`，可用于收发双方核对。

脚本不重新生成数据、不联网、只用标准库。缺任一输入文件会非零退出，列出
缺项并提示先跑 `scripts/export_geojson.py` 或
`scripts/export_qgis_style.py`；目标 zip 已存在时默认拒绝，`--force` 才覆盖。

本文说明如何把 `scripts/export_geojson.py` 导出的三个 GeoJSON 图层连同
`scripts/export_qgis_style.py` 生成的 QML 样式一起加载到 QGIS 3.x。

> **诚实边界：本机没有安装 QGIS。** 下面的 QML 只通过了 XML 合法性、
> 符号类型和字段存在性校验，**未在 QGIS 内实际渲染验证**。如果你在 QGIS
> 里看到明显走样（例如标注压点、填充过深），以 QGIS 的实际效果为准。

## 1. 产物

先生成数据与样式（在仓库根目录执行）：

```powershell
python scripts/export_geojson.py D:\mbdsdr\output\observing_sites.geojson
python scripts/export_geojson.py D:\mbdsdr\output\ground_track.geojson `
    --layer ground-track --tle experiments/satellite_rx/data/iss.tle `
    --sat ISS --start 2026-11-17T00:00:00Z
python scripts/export_geojson.py D:\mbdsdr\output\footprint.geojson `
    --layer footprint --tle experiments/satellite_rx/data/iss.tle `
    --sat ISS --start 2026-11-17T00:00:00Z

python scripts/export_qgis_style.py
python scripts/export_qgis_style.py --out D:\mbdsdr\output   # 显式指定目录
```

默认输出目录是仓库同级的 `output\`（即 `D:\mbdsdr\output\`）。样式文件与
GeoJSON 同名，QGIS 添加图层时会优先自动匹配同名 `.qml`。

| 图层 | GeoJSON | QML | 几何类型 |
| --- | --- | --- | --- |
| 观测台址与公众观星点 | `observing_sites.geojson` | `observing_sites.qml` | Point |
| 卫星地面轨迹 | `ground_track.geojson` | `ground_track.qml` | LineString（按日界线分段） |
| 卫星覆盖圈 | `footprint.geojson` | `footprint.qml` | Polygon；跨日界线时为 LineString；另含星下点 Point |

样式引用的字段全部来自真实导出（台址侧见
`mbdsdr_ai/astronomy/observing_sites.py`，轨迹/覆盖圈侧见
`mbdsdr_ai/ground_track.py` 与 `scripts/export_geojson.py`）：

- 点层：`access`（分类配色：`research` / `public` / `unknown`）、
  `name_zh`（标注）；
- 线层：无字段绑定，仅单符号描边；
- 面层：`role`（规则渲染：`footprint` 面填充、`footprint_boundary` 线描边、
  `footprint_subpoint` 点符号）。

坐标是几何本身，不存在 `lat` / `lon` 属性；海拔字段名是 `alt_m`
（官方页未给时为 `null`），**没有** `elevation` 字段。

## 2. 在 QGIS 中加载

1. 打开 QGIS 3.x，菜单 **图层 → 添加图层 → 添加矢量图层…**
   （Layer → Add Layer → Add Vector Layer）。
2. 源类型选 **文件**，编码选 **UTF-8**，点击「浏览」选中
   `observing_sites.geojson`，点「添加」。投影保持默认（数据是 WGS84 /
   RFC 7946），QGIS 会提示图层坐标系与工程不一致，按需选择动态投影。
3. 在左侧「图层」面板双击该图层，打开 **图层属性**（Layer Properties）。
4. 左侧选 **符号化**（Symbology）页。
5. 点击该页左下角的 **样式** 按钮 → **载入样式…**（Load Style…），
   选中同目录的 `observing_sites.qml`，确定。
6. 点「应用」/「确定」回到地图，即可看到按 `access` 分类的圆点与
   `name_zh` 标注。
7. 对 `ground_track.geojson`、`footprint.geojson` 重复第 1–6 步，
   分别载入同名 `.qml`。

若 QGIS 添加 GeoJSON 时已自动套用同名样式，第 3–6 步可跳过；建议仍打开
符号化页确认一次分类值和标注字段是否命中。

## 3. 已知限制

- **未在 QGIS 内渲染验证**：本机无 QGIS，仅验证了 QML 是合法 XML、
  符号类型（marker/line/fill）与字段名正确；默认字号、颜色、标注避让
  以 QGIS 实际渲染为准。
- **覆盖圈是混合几何**：多数时刻导出 `Polygon` + 星下点 `Point`；当覆盖圈
  跨日界线时，多边形退化为 `LineString` 分段边界（见
  `export_geojson.py:split_ring_at_dateline`）。因此 `footprint.qml` 用
  规则渲染按 `role` 分派，而不是单一面符号。
- **点层标注字体** 指定为 `Microsoft YaHei`，系统缺字体时 QGIS 会回退，
  可能影响中文显示宽度。
- 字段集若随产品代码变化，请重新运行 `scripts/export_qgis_style.py`；
  `tests/test_qgis_style.py` 会把 QML 引用的字段与真实 GeoJSON 属性对拍，
  字段不存在会直接失败。
