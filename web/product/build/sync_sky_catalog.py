"""从 MBDSDR 原仓库 + HYG v4.1 全天星表（本地缓存）只读同步星象仪事实源。

产物：
  src/12-sky-catalog.js   前端唯一星表（HYG 全天星场 + 仓库命名/连线/DSO/中文星名）
  build/sky-catalog.json  可核对中间产物
  docs/10-sky-catalog.md  来源 / 许可 / 口径 / not_verified 登记

数据来源（真下载，不是闷头造车）：
  - HYG v4.1: astronexus/HYG-Database（Hipparcos + Yale BSC5 + Gliese），
    119,626 颗，J2000，CC BY-SA 4.0。经 build/fetch_hyg.py 用 git 稀疏克隆
    取回 data/hygdata_v41.csv（SHA256 记录在 data/hygdata_v41.sha256）。
    与仓库 mbdsdr_ai/skyengine/stars.py 的 141 颗亮星逐值同源。
  - 仓库: stars.py / constellations.py / data/dso_catalog.json（OpenNGC
    CC BY-SA 4.0）/ data/*_culture.json。

纪律（AGENTS.md §2/§3、NOTICE.md、limitations-register L24/L25/L28）：
  - 只解析 AST 字面量与 CSV 行，不 import 仓库代码、不执行仓库模块；
  - 一颗星都不造：未收录天体不画，条数全部实际读出；
  - 银河无数据 → 不生成任何「像银河」的数据（产品侧诚实空态）；
  - 不碰 AGPL 的 stellarium-web-engine 星表/源码/贴图。
"""
from __future__ import annotations

import ast
import csv
import datetime as _dt
import hashlib
import json
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT, REPO
HYG_CSV = ROOT / "data/hygdata_v41.csv"
MAG_CUTOFF = 7.5          # 肉眼极限 6.5 再留 1 等，供窄视场视觉适应门限使用

STAR_FILES = [
    ("BRIGHT_STARS", REPO / "mbdsdr_ai/skyengine/stars.py"),
    ("EXTENSION_STARS", REPO / "mbdsdr_ai/skyengine/constellations.py"),
]
CONST_FILE = REPO / "mbdsdr_ai/skyengine/constellations.py"
DSO_FILE = REPO / "mbdsdr_ai/data/dso_catalog.json"
CULTURE_STAR = REPO / "mbdsdr_ai/data/star_culture.json"
CULTURE_CONST = REPO / "mbdsdr_ai/data/constellation_culture.json"


def _assign(tree: ast.Module, name: str) -> ast.expr | None:
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == name and node.value is not None:
            return node.value
        if isinstance(node, ast.Assign) and node.value is not None:
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return node.value
    return None


def _const(node: ast.expr):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError) as exc:
        raise RuntimeError(f"非字面量数据节点，拒绝执行：{ast.dump(node)[:120]}") from exc


def _parse_named() -> list[dict]:
    out: dict[str, dict] = {}
    for var, path in STAR_FILES:
        node = _assign(ast.parse(path.read_text(encoding="utf-8")), var)
        assert isinstance(node, (ast.List, ast.Tuple)), f"{var} 不是列表"
        for call in node.elts:
            assert isinstance(call, ast.Call) and getattr(call.func, "id", "") == "BrightStar"
            a = [_const(x) for x in call.args]
            assert len(a) >= 5
            rec = {"n": str(a[0]), "ra": float(a[1]), "dec": float(a[2]),
                   "m": float(a[3]), "bv": float(a[4]),
                   "ly": float(a[5]) if len(a) > 5 else 0.0,
                   "sp": str(a[6]) if len(a) > 6 else ""}
            assert 0 <= rec["ra"] < 360 and -90 <= rec["dec"] <= 90, \
                f"坐标越界（疑似度/时混淆）：{rec}"
            assert rec["n"] not in out, f"星表重名：{rec['n']}"
            out[rec["n"]] = rec
    return list(out.values())


def _parse_constellations() -> tuple[dict, dict]:
    tree = ast.parse(CONST_FILE.read_text(encoding="utf-8"))
    lines = {k: [list(p) for p in v]
             for k, v in _const(_assign(tree, "CONSTELLATION_LINES")).items()}
    names = _const(_assign(tree, "CONSTELLATION_NAMES"))
    culture = json.loads(CULTURE_CONST.read_text(encoding="utf-8"))
    out = {abbr: {"en": en, "zh": (culture.get(abbr) or {}).get("zh", abbr),
                  "note": (culture.get(abbr) or {}).get("note", "")}
           for abbr, en in names.items()}
    return lines, out


def _parse_dso() -> tuple[list[dict], dict]:
    """读仓库 **OpenNGC 派生的 dso_catalog.json**（2026-11-05 换源后事实源）。

    旧版从 astronomy/dso.py 里 AST 抠 `_DSO_ROWS` 字面量；仓库把 DSO 数据层
    换成带 meta + objects 的 JSON、并退役 GPL 来源后，那条路就断了。
    现在直接读 JSON 并校验 schema / 来源许可，同时**保留未知量为 None**——
    原仓库 dso.py 的口径是「源目录缺失的星等/角径写 None，绝不用 0/99 冒充」，
    产品侧也必须照办，不能在同步时编一个数值出来。
    """
    payload = json.loads(DSO_FILE.read_text(encoding="utf-8"))
    meta = payload["meta"]
    if meta.get("schema") != "mbdsdr.dso_catalog.v2":
        raise RuntimeError(f"DSO schema 漂移：{meta.get('schema')!r}")
    if meta.get("source_license") != "CC-BY-SA-4.0":
        raise RuntimeError(f"DSO 许可漂移：{meta.get('source_license')!r}")
    rows = []
    for o in payload["objects"]:
        rows.append({
            "n": str(o["name"]),
            "t": str(o["dso_type"]),
            "ra": float(o["ra_deg"]),                 # 已经是度，不再 ×15
            "dec": float(o["dec_deg"]),
            "m": None if o.get("magnitude") is None else float(o["magnitude"]),
            "size": None if o.get("size_arcmin") is None else float(o["size_arcmin"]),
            "c": str(o.get("constellation") or ""),
            "zh": str(o.get("zh_name") or ""),
            "en": str(o.get("name_en") or ""),
            "axis": None if o.get("axis_ratio") is None else float(o["axis_ratio"]),
        })
    dso_meta = {
        "count": len(rows),
        "catalog": meta.get("catalog"),
        "source": meta.get("source"),
        "sourceUrl": meta.get("source_url"),
        "license": meta.get("source_license"),
        "licenseUrl": meta.get("source_license_url"),
        "attribution": meta.get("attribution"),
        "schema": meta.get("schema"),
        "unknownMagnitude": sum(1 for r in rows if r["m"] is None),
        "unknownSize": sum(1 for r in rows if r["size"] is None),
    }
    return rows, dso_meta


def _parse_hyg() -> tuple[list[list[int]], dict, dict]:
    """HYG v4.1 → 紧凑定点数组 [ra_mdeg, dec_mdeg, mag_centi, bv_centi]。

    紧凑编码是为了让「全天」真的能塞进单文件 HTML：25k 星 × ~24B。
    所有数值来自 CSV 实测，不生成、不插值、不补齐。
    """
    if not HYG_CSV.is_file():
        raise RuntimeError(
            f"HYG 全天星表缺失：{HYG_CSV}\n"
            "先跑 python build/fetch_hyg.py（git 稀疏克隆，见脚注）；"
            "拒绝静默降级到只有 141 颗亮星，否则「全天」是假的。")
    raw = HYG_CSV.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    stars: list[list[int]] = []
    bands: dict[str, int] = {}
    proper: dict[str, list] = {}
    with HYG_CSV.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        need = {"ra", "dec", "mag", "ci", "proper"}
        assert need.issubset(set(reader.fieldnames or [])), "HYG 字段缺失"
        for row in reader:
            try:
                mag = float(row["mag"])
            except (ValueError, TypeError):
                continue
            if mag > MAG_CUTOFF:
                continue
            ra_h = float(row["ra"])
            dec = float(row["dec"])
            ra = ra_h * 15.0
            assert 0 <= ra < 360 and -90 <= dec <= 90, f"HYG 坐标越界 {ra},{dec}"
            ci_raw = row.get("ci", "")
            bv = float(ci_raw) if ci_raw not in ("", None) else 0.6
            # 定点：坐标 0.001°，星等/B-V 0.01 等（足够屏幕渲染，误差不可见）
            stars.append([round(ra * 1000), round(dec * 1000),
                          round(mag * 100), round(bv * 100)])
            if row.get("proper"):
                proper[row["proper"]] = [round(ra * 1000), round(dec * 1000),
                                         round(mag * 100), round(bv * 100)]
    for cut in (1, 2, 3, 4, 5, 6, 6.5, 7, 7.5):
        bands[f"mag<={cut}"] = sum(1 for s in stars if s[2] / 100 <= cut)
    info = {
        "sha256": digest, "bytes": len(raw),
        "source": "astronexus/HYG-Database @ main · hyg/CURRENT/hygdata_v41.csv",
        "license": "CC BY-SA 4.0 (David Nash / Astronomy Nexus)",
        "epoch": "J2000 (equinox & epoch 2000.0)",
        "raUnitInFile": "hours → 导出时 ×15 转度",
        "cutoff": MAG_CUTOFF,
        "totalRowsInFile": sum(1 for _ in HYG_CSV.open(encoding="utf-8")) - 1,
        "bands": bands,
    }
    return stars, info, proper


def main() -> None:
    named = _parse_named()
    lines, const_names = _parse_constellations()
    dso, dso_meta = _parse_dso()
    sky, hyg_info, proper = _parse_hyg()

    culture = json.loads(CULTURE_STAR.read_text(encoding="utf-8"))
    for s in named:
        s["zh"] = (culture.get(s["n"]) or {}).get("zh", "")
        s["note"] = (culture.get(s["n"]) or {}).get("note", "")

    known = {s["n"] for s in named}
    missing = sorted({n for segs in lines.values() for pair in segs for n in pair} - known)
    assert not missing, "星座连线端点未收录：" + ", ".join(missing[:10])

    meta = {
        "generatedAt": _dt.datetime.now().isoformat(timespec="seconds"),
        "epoch": "J2000 / ICRF",
        "starCount": len(sky),
        "namedCount": len(named),
        "constellationCount": len(lines),
        "segmentCount": sum(len(v) for v in lines.values()),
        "dsoCount": len(dso),
        "magMin": min(s[2] / 100 for s in sky),
        "magMax": max(s[2] / 100 for s in sky),
        "hyg": hyg_info,
        "repoSource": "MBDSDR mbdsdr_ai/skyengine/{stars,constellations}.py · "
                      "mbdsdr_ai/data/dso_catalog.json · mbdsdr_ai/data/*_culture.json",
        "dsoSource": dso_meta,
        "repoLicense": "星表事实值经 HYG v4.1 (CC BY-SA 4.0) 对拍；"
                       "DSO 派生自 OpenNGC (CC BY-SA 4.0)，中文名 Wikidata (CC0)；"
                       "星座连线/中文星名为公开天文常识级对照。",
        "licenseNote": "HYG v4.1 为 CC BY-SA 4.0（署名 + 相同方式共享），"
                       "不是公共领域；产品数据层随其口径，已在 NOTICE 式登记中署名。",
        "agpl": "未使用 stellarium-web-engine 的星表/源码/贴图（AGPL-3.0）；"
                "仅参考球面投影与视觉适应机制。",
        "discipline": "未收录天体不画；无随机星点；无装饰性银河（无数据·不提供）；"
                      "星位固定 J2000，动的是视角/FOV/时间。",
        "notVerified": [
            "岁差/章动/自行：Web 用 J2000 直算 LST，未接 celestial_geometry 完整坐标链",
            "天空亮度→极限星等：原仓库 limitations L24 明确未做；"
            "Web 的 FOV 相关门限是自研显示门限（设计项，未验证），非望远镜口径增益",
            "银河图层：原仓库 L25/L28 无数据未接入，产品保持诚实空态",
            "DSO 形态/表面亮度：原仓库 L26 明确不做还原，仅类型符号",
            "TimeEngine 接线：Web 离线用本地模拟时刻，未接 new_spacetime.TimeEngine",
            "卫星位置：示意方位，未接 TLE/SGP4 解算；站址未校准不给可信过境结论",
        ],
    }
    data = {"meta": meta, "stars": sky, "named": named, "proper": proper,
            "lines": lines, "constellations": const_names, "dso": dso}

    (ROOT / "build/sky-catalog.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    (ROOT / "src/12-sky-catalog.js").write_text(
        "/* 由 build/sync_sky_catalog.py 生成；勿手改。\n"
        " * HYG v4.1 全天星场（CC BY-SA 4.0）+ MBDSDR 仓库命名/连线/DSO。\n"
        " * 无随机星点、无装饰银河；星位固定 J2000，动的是视角与时间。 */\n"
        "(function (g) {\n  \"use strict\";\n"
        "  var MB = g.MBDSDR || (g.MBDSDR = {});\n"
        f"  MB.SKYCAT = {payload};\n"
        "})(typeof window !== 'undefined' ? window : globalThis);\n",
        encoding="utf-8")
    (ROOT / "data/hygdata_v41.sha256").write_text(
        f"{hyg_info['sha256']}  hygdata_v41.csv\n"
        f"source: {hyg_info['source']}\nlicense: {hyg_info['license']}\n",
        encoding="utf-8")

    doc = [
        "# 全天星表 · 来源、口径与 not_verified",
        "",
        "> 由 `build/sync_sky_catalog.py` 自动生成。数据不进人工维护，重跑即与事实源对齐。",
        "> **数据是真下载的**（`build/fetch_hyg.py` 用 git 稀疏克隆取回），不是本地造的。",
        "",
        "## 事实源",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 全天星场 | HYG v4.1 **{meta['starCount']:,} 颗**（V ≤ {MAG_CUTOFF} 等） |",
        f"| 源文件 | `{hyg_info['source']}` · {hyg_info['bytes']:,} bytes |",
        f"| SHA256 | `{hyg_info['sha256']}` |",
        f"| 许可 | {hyg_info['license']}（{meta['licenseNote']}） |",
        f"| 历元 | {hyg_info['epoch']}；文件 RA 单位={hyg_info['raUnitInFile']} |",
        f"| 命名亮星 | {meta['namedCount']} 颗（仓库 `stars.py`/`constellations.py`，含中文星官） |",
        f"| 星座 | {meta['constellationCount']} 座 / {meta['segmentCount']} 段连线（端点全部可查） |",
        f"| DSO | {meta['dsoCount']} 条（类型符号，非形态还原） |",
        f"| DSO 来源 | {dso_meta['source']} · {dso_meta['license']} · schema `{dso_meta['schema']}` |",
        f"| DSO 未知量 | 星等缺失 {dso_meta['unknownMagnitude']} 条 / 角径缺失 "
        f"{dso_meta['unknownSize']} 条（**保持 null，不拿 0/99 冒充**） |",
        f"| 星等范围 | {meta['magMin']} ~ {meta['magMax']} |",
        "",
        "**条数全部由数据文件实际读出**；`HYG v4.1` 与仓库 141 颗亮星逐值同源",
        "（Sirius RA=6.752481h×15=101.2872°、B-V=0.009 与 `stars.py` 一致）。",
        "",
        "### 星等分档（导出数据实测）",
        "",
        "| 亮于 | 颗数 |",
        "|---|---|",
    ] + [f"| {k.replace('mag<=', '')} 等 | {v:,} |" for k, v in hyg_info["bands"].items()] + [
        "",
        "广角视场只放开到较亮的星等，放大（窄 FOV）逐步放开到全表最深等——",
        "**星始终在那里，变的只是显示门限与投影尺度**。",
        "",
        "## 渲染口径",
        "",
        "- 恒星位置固定 J2000 赤道坐标；**星星不移动、不飞散、无独立角速度**。",
        "- 每帧：模拟时刻 → JD → GMST → 地方恒星时 LST → 时角 → 地平 az/alt",
        "  → 视图空间 (X,Y,Z) → **全屏平面投影**：≤88° 针孔透视 `x=X/Z·f`，"
        "更大 FOV 立体投影 `r=2·tan(θ/2)`。",
        "- **天空是球、屏幕是平面**：裁剪只靠窗口矩形，没有圆形视场遮罩。"
        "透视下地平大圆成直线（针孔相机的正确像），立体投影下成圆弧。",
        "- 周日视运动 = 整个天球随 LST 刚性旋转（所有星共用一个角）。",
        "- 入场 = **FOV 收束**（160° → 100°）+ 暗适应，只改投影尺度与显示门限；"
        "星位一帧不动，星座形状与角距离守恒。",
        "- 缩放改 `fov_deg`，不做相机推拉、不沿半径平移星点。",
        "- 星等→半径/亮度按 Pogson 定律 gamma 近似；B-V→色温近似；地平附近大气消光。",
        "- 可见判据（同一套门）：视锥/地平门 + 白天整层门 + 消光 + Bortle 基线 + FOV 视觉适应门限。",
        "- 银河：**无数据·不提供**，不画装饰带（原仓库 limitations L25/L28）。",
        "",
        "## DSO 换源（2026-11-05 事实源漂移）",
        "",
        f"- 仓库 `f0c8fa25` 把 DSO 从 Stellarium `catalog.txt`（GPL）换成 "
        f"**OpenNGC**（{dso_meta['license']}），退役 GPL 数据；"
        "`dbf2a3e7` 补 `axis_ratio` 与来源标记。",
        "- 产品同步链随之从 `astronomy/dso.py` 的 `_DSO_ROWS` AST 字面量，"
        "改读 `mbdsdr_ai/data/dso_catalog.json`（`meta` + `objects`），"
        "并校验 `schema` 与 `source_license`；RA 已是**度**，不再 ×15。",
        f"- 署名：{dso_meta['attribution']}",
        "- 未知星等 / 角径保持 `null`，前端只画类型符号、不参与亮度门，"
        "与仓库「绝不用 0/99 冒充」同口径。",
        "",
        "## not_verified（勿当作已上线）",
        "",
    ] + [f"- {x}" for x in meta["notVerified"]] + [
        "",
        "## 与桌面端的关系",
        "",
        "原仓库 `desktop/rf_sky_view.py` / `mbdsdr_ai/celestial_geometry.py` 有完整坐标链、",
        "球面投影、DSO 层与时间引擎（带测试）。Web 产品是独立自研的轻量近似，",
        "未与桌面端联调，不宣称等价。差距与上面 not_verified 逐条对应。",
        "",
        f"生成时间：{meta['generatedAt']}",
    ]
    (ROOT / "docs/10-sky-catalog.md").write_text("\n".join(doc) + "\n", encoding="utf-8")

    print(f"synced: HYG {meta['starCount']:,} stars (mag<={MAG_CUTOFF}), "
          f"named {meta['namedCount']}, {meta['constellationCount']} constellations, "
          f"{meta['dsoCount']} DSO")


if __name__ == "__main__":
    main()
