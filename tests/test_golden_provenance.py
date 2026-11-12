# SPDX-License-Identifier: MIT
"""golden / fixture 来历守卫（「自生成的东西不许叫验证」）。

三条硬约束：

1. **每个 golden / fixture 目录必须有 `provenance.json`**，逐文件声明
   `provenance`（三类之一）、`source_name`、`sha256`、`derivation`
   （生成命令或下载地址）、`date`；缺声明或声明不完整 → 红。
2. **自生成不得冒充外部真值**：`self-generated` / `synthetic-loopback`
   的文件不允许出现在「external truth」声明里；能力表里 `evidence=real`
   的行必须 `truth_source=external-truth`，矩阵文档「真实」行的「来历」
   必须是「外部真值」→ 否则红。
3. **自生成可以存在，但必须一眼认出**：目录级 `provenance` 字段存在，
   且 `tests/fixtures/decode_pipeline/manifest.json` 必须带
   `"provenance": "self-generated"`。

范围：`tests/golden`、`tests/fixtures`、`mbdsdr_ai/tests/fixtures`。
`tests/baselines`（UI 像素基线）不在范围内：它由 `scripts/ui_baseline.py`
重新生成、由 UI 线维护，SHA 每次改版都会变，钉 SHA 只会制造跨线假红。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

#: 三类来历，互斥。
PROVENANCE_TYPES = {"external-truth", "self-generated", "synthetic-loopback"}
SELF_GENERATED_TYPES = {"self-generated", "synthetic-loopback"}

#: 守卫扫描的根目录；目录名含 golden/fixtures 的都算资产目录。
SCAN_ROOTS = ("tests", "mbdsdr_ai/tests")

#: 明确排除的目录（理由见模块 docstring）。
EXCLUDED_DIRS = {"tests/baselines", "__pycache__"}

#: 声明文件本身不算资产。
DECLARATION = "provenance.json"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: 能力表里 evidence=real 的链，其来历必须是 external-truth；
#: 若它们在仓内有资产，资产声明也必须是 external-truth。
EXTERNAL_TRUTH_ASSETS = {
    "adsb": ["tests/golden/dump1090_modes1_reference.txt"],
    "ft8": [],  # WSJT-X 官方样本按需下载，不入仓
    "lrpt（LRPT 图像链）": [
        # 软比特来自 TU Berlin 真录；清单文件是仓内生成的元数据，故不列入
        "tests/golden/lrpt_meteor_real_soft_0.f32",
        "tests/golden/lrpt_meteor_real_soft_1.f32",
    ],
}


def _rel(path: str) -> str:
    return os.path.relpath(path, _ROOT).replace(os.sep, "/")


def _asset_dirs() -> list[str]:
    """找出所有「直接存放数据文件」的 golden/fixture 目录。"""
    dirs = []
    for top in SCAN_ROOTS:
        base = os.path.join(_ROOT, top)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
            rel = _rel(dirpath)
            if not (rel.startswith("tests/golden")
                    or "/fixtures" in rel
                    or rel.endswith("fixtures")):
                continue
            if any(f != DECLARATION and f != "__init__.py"
                   and not f.endswith(".pyc") for f in filenames):
                dirs.append(rel)
    return sorted(dirs)


def _asset_files(rel_dir: str) -> list[str]:
    """目录内应被声明的数据文件（直接子文件，不含声明文件本身）。"""
    path = os.path.join(_ROOT, rel_dir)
    return sorted(
        f for f in os.listdir(path)
        if os.path.isfile(os.path.join(path, f))
        and f != DECLARATION
        and not f.endswith(".pyc")
        and f != "__init__.py"
    )


def _load(rel_dir: str) -> dict:
    with open(os.path.join(_ROOT, rel_dir, DECLARATION), encoding="utf-8") as f:
        return json.load(f)


def _sha256(rel_file: str) -> str:
    """对 git blob 的原始字节取 SHA-256，不做换行归一。

    `git hash-object` 会按仓库的 clean 过滤器（如 core.autocrlf）把工作区
    内容归一成「将要入库的 blob」，再对 blob 原始字节取哈希。这样同一份
    资产在任何平台、任何检出方式下指纹一致；如果有人真的提交了不同的
    行尾字节，blob 会变，指纹也跟着变，仍然能抓住。
    """
    oid = subprocess.run(
        ["git", "hash-object", "--", rel_file],
        cwd=_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    blob = subprocess.run(
        ["git", "cat-file", "blob", oid],
        cwd=_ROOT, capture_output=True, check=True,
    ).stdout
    return hashlib.sha256(blob).hexdigest()


def _all_declarations() -> dict:
    """{相对文件路径: 声明条目}。"""
    registry = {}
    for rel_dir in _asset_dirs():
        data = _load(rel_dir)
        for name, entry in data.get("files", {}).items():
            registry[f"{rel_dir}/{name}"] = entry
    return registry


def _matrix_rows() -> list[dict]:
    """能力矩阵行（含 EXTRA_CHAINS）。"""
    import importlib.util
    script = os.path.join(_ROOT, "scripts", "decoder_capability_report.py")
    spec = importlib.util.spec_from_file_location("decoder_capability_report", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_rows()


def test_every_golden_dir_has_provenance() -> None:
    """缺声明直接红：每个 golden/fixture 目录都要有 provenance.json。"""
    missing = [
        rel for rel in _asset_dirs()
        if not os.path.exists(os.path.join(_ROOT, rel, DECLARATION))
    ]
    assert not missing, "以下 golden/fixture 目录缺少 provenance.json：\n" + "\n".join(missing)


def test_provenance_declares_every_file_with_sha() -> None:
    """声明必须覆盖目录内每个文件，且 SHA-256 与文件逐字节一致。"""
    problems = []
    for rel_dir in _asset_dirs():
        decl_path = os.path.join(_ROOT, rel_dir, DECLARATION)
        if not os.path.exists(decl_path):
            continue
        data = _load(rel_dir)
        files = data.get("files")
        assert isinstance(files, dict) and files, f"{rel_dir}/provenance.json 的 files 为空"
        actual = _asset_files(rel_dir)
        for name in actual:
            if name not in files:
                problems.append(f"{rel_dir}/{name} 未在 provenance.json 声明")
        for name in files:
            if name not in actual:
                problems.append(f"{rel_dir}/{name} 声明了但目录里没有该文件")
        for name, entry in files.items():
            rel_file = f"{rel_dir}/{name}"
            kind = entry.get("provenance")
            if kind not in PROVENANCE_TYPES:
                problems.append(f"{rel_file} 的 provenance={kind!r} 不在三类中")
            for key in ("source_name", "derivation", "date", "sha256"):
                if not str(entry.get(key, "")).strip():
                    problems.append(f"{rel_file} 缺少字段 {key}")
            if not _SHA256.match(str(entry.get("sha256", ""))):
                problems.append(f"{rel_file} 的 sha256 格式非法")
            if not _DATE.match(str(entry.get("date", ""))):
                problems.append(f"{rel_file} 的 date 不是 YYYY-MM-DD")
            if os.path.exists(os.path.join(_ROOT, rel_file)):
                if entry.get("sha256") != _sha256(rel_file):
                    problems.append(f"{rel_file} 的 sha256 与文件内容不一致")
        # 目录级 provenance 字段：允许 mixed，但必须存在（第三条硬约束）
        assert "provenance" in data, f"{rel_dir}/provenance.json 缺少目录级 provenance 字段"
    assert not problems, "provenance 声明不完整：\n" + "\n".join(problems)


def test_directory_provenance_field_values() -> None:
    """目录级 provenance 只能是三类之一或 mixed（mixed 表示逐文件不同）。"""
    bad = []
    for rel_dir in _asset_dirs():
        if not os.path.exists(os.path.join(_ROOT, rel_dir, DECLARATION)):
            continue
        value = _load(rel_dir).get("provenance")
        if value not in PROVENANCE_TYPES | {"mixed"}:
            bad.append(f"{rel_dir}: {value!r}")
    assert not bad, "目录级 provenance 非法：\n" + "\n".join(bad)


def test_self_generated_never_claimed_external() -> None:
    """自生成/回环的文件不得出现在任何 external-truth 声明里。"""
    registry = _all_declarations()
    violations = []
    for rel_file, entry in registry.items():
        if entry.get("provenance") in SELF_GENERATED_TYPES:
            # 外部真值声明：能力表 real 行点名的仓内资产
            for assets in EXTERNAL_TRUTH_ASSETS.values():
                if rel_file in assets:
                    violations.append(f"{rel_file} 是 {entry['provenance']} 却被列为 external-truth")
    assert not violations, "\n".join(violations)

    # 改坏就红：把 gps_lnav_golden 硬塞进外部真值清单，必须被抓出来
    fake = dict(EXTERNAL_TRUTH_ASSETS)
    fake["gps"] = ["tests/golden/gps_lnav_golden.json"]
    forged = [
        rel for rel, e in registry.items()
        if e.get("provenance") in SELF_GENERATED_TYPES
        and any(rel in assets for assets in fake.values())
    ]
    assert forged, "守卫失效：自生成资产混入 external-truth 清单时没有报红"


def test_capability_real_rows_are_external_truth() -> None:
    """能力表 evidence=real ⇒ truth_source=external-truth；点名资产同口径。"""
    registry = _all_declarations()
    problems = []
    for row in _matrix_rows():
        if row["truth"] != "real":
            continue
        if row["truth_source"] != "external-truth":
            problems.append(f"{row['mode']}: evidence=real 但 truth_source={row['truth_source']}")
        for asset in EXTERNAL_TRUTH_ASSETS.get(row["mode"], []):
            entry = registry.get(asset)
            if entry is None:
                problems.append(f"{row['mode']} 的外部真值资产未声明: {asset}")
            elif entry.get("provenance") != "external-truth":
                problems.append(
                    f"{row['mode']} 的外部真值资产 {asset} 实际是 {entry.get('provenance')}")
    assert not problems, "能力表把自生成证据当外部真值：\n" + "\n".join(problems)


def test_doc_real_rows_show_external_truth() -> None:
    """矩阵文档里「真实」行的「来历」列必须是「外部真值」。"""
    doc = os.path.join(_ROOT, "docs", "learn", "decoder-capability-matrix.md")
    with open(doc, encoding="utf-8") as f:
        lines = f.read().splitlines()
    bad = []
    for line in lines:
        if not line.startswith("| `") or "**真实**" not in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or cells[2] != "外部真值":
            bad.append(line[:120])
    assert not bad, "文档「真实」行的来历不是外部真值：\n" + "\n".join(bad)


def test_decode_pipeline_manifest_declares_self_generated() -> None:
    """自生成目录必须自报家门：manifest.json 里有 provenance 字段。"""
    path = os.path.join(_ROOT, "tests", "fixtures", "decode_pipeline", "manifest.json")
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest.get("provenance") == "self-generated", (
        "decode_pipeline/manifest.json 缺少 provenance: self-generated")


def test_loopback_chains_exist_and_are_not_called_external() -> None:
    """回环链登记的证据文件必须存在，且不得标 external-truth。"""
    import importlib.util
    script = os.path.join(_ROOT, "scripts", "decoder_capability_report.py")
    spec = importlib.util.spec_from_file_location("decoder_capability_report", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for item in mod.LOOPBACK_CHAINS:
        assert os.path.exists(os.path.join(_ROOT, item["evidence"])), \
            f"回环链证据文件不存在: {item['evidence']}"
        assert item["truth_source"] in SELF_GENERATED_TYPES, \
            f"{item['name']} 不得标为 {item['truth_source']}"


def test_known_self_generated_assets_are_labeled() -> None:
    """点名防止回退：GPS LNAV / AS TU1 / 跨语言向量必须自报 self-generated。"""
    registry = _all_declarations()
    must_be_self = [
        "tests/golden/gps_lnav_golden.json",
        "tests/golden/astu1_tm_synthetic_golden.json",
        "tests/golden/cross_decode_vectors.json",
        "tests/golden/gps_ca_prn_vectors.json",
        "tests/golden/lrpt_meteor_real_soft_manifest.json",
    ]
    for rel in must_be_self:
        entry = registry.get(rel)
        assert entry is not None, f"未声明: {rel}"
        assert entry["provenance"] == "self-generated", (
            f"{rel} 是自家编码器/脚本产出，必须标 self-generated，实际 {entry['provenance']}")
    # AIS / POCSAG / ACARS / NAVTEX 的合成往返不得混进「真实」
    for mode in ("ais", "pocsag", "acars", "navtex"):
        row = next(r for r in _matrix_rows() if r["mode"] == mode)
        assert row["truth"] != "real", f"{mode} 是合成往返，不得标真实"
        assert row["truth_source"] == "synthetic-loopback", (
            f"{mode} 的来历应为 synthetic-loopback，实际 {row['truth_source']}")
