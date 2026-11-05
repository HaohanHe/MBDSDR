# SPDX-License-Identifier: MIT
"""数据文件许可合规检查的可执行测试（`scripts/check_data_licenses.py`）。

判据：
- 真实仓库扫描必须零违规；B1 后白名单必须为空（任何 GPL 数据 = 禁入）；
- 反证：在 MIT 主库路径 ``mbdsdr_ai/`` 下临时放一个 GPL 源数据文件，
  检查必须报违规（再清理，不留残渣）；GPL-2.0 级同样必须红；
- 同理，GPL 文件放在路径外只算提示，不应判违规（谁主张谁举证）；
- 误报守卫：base64 数据里偶然出现的裸 ``GPL`` 三个字母不算许可声明
  （历史实测：``tests/golden/dvbs2_ldpc_vectors.json`` 的 base64 段命中）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_data_licenses.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_data_licenses", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_data_licenses"] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _fake_repo(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _paths(result) -> set:
    return {item["path"] for item in result["violations"]}


# ── 真实仓库 ─────────────────────────────────────────────────────────

def test_repo_scan_has_no_violations():
    result = checker.scan(REPO_ROOT)
    assert result["violations"] == []
    assert result["scanned"] > 0


def test_whitelist_is_empty_after_b1():
    """B1 决策后不允许按文件豁免：空表即禁入，防止悄悄加回。"""
    assert checker.WHITELIST == {}


def test_whitelist_entries_are_justified_and_exist():
    for rel, reason in checker.WHITELIST.items():
        assert reason.strip(), f"白名单 {rel} 缺理由"
        assert (REPO_ROOT / rel).is_file(), f"白名单 {rel} 不在仓库里"
    whitelisted = {item["path"] for item in checker.scan(REPO_ROOT)["warnings"]
                   if item.get("whitelisted")}
    assert whitelisted <= set(checker.WHITELIST)


# ── 反证：MIT 路径下的 GPL 数据必须红 ───────────────────────────────

def test_gpl_file_under_mit_package_is_violation(tmp_path):
    root = _fake_repo(tmp_path, {
        "mbdsdr_ai/data/bad.json": '{"meta": {"source_license": "GPL-3.0"}}',
        "mbdsdr_ai/data/ok.json": '{"meta": {"source_license": "CC0-1.0"}}',
    })
    result = checker.scan(root)
    assert _paths(result) == {"mbdsdr_ai/data/bad.json"}


def test_gpl2_data_under_mit_package_is_violation_without_exemption(tmp_path):
    """守卫：mbdsdr_ai/ 下出现 GPL-2.0 级数据文件，未豁免即红。"""
    root = _fake_repo(tmp_path, {
        "mbdsdr_ai/data/constellations_iau.json":
            '{"meta": {"name_source_license": "GPL-2.0-or-later"}}',
    })
    assert checker.WHITELIST == {}
    assert _paths(checker.scan(root)) == {
        "mbdsdr_ai/data/constellations_iau.json"}


def test_gpl_file_outside_mit_package_is_only_warning(tmp_path):
    root = _fake_repo(tmp_path, {
        "tests/golden/vendor.json": '{"license": "GPL-3.0-or-later"}',
    })
    result = checker.scan(root)
    assert result["violations"] == []
    assert {item["path"] for item in result["warnings"]} == {
        "tests/golden/vendor.json"}


def test_probe_writes_then_removes_temporary_file():
    """脚本自带反证开关：写 GPL 探针 -> 必须红 -> 清理干净。"""
    assert checker.main(["--root", str(REPO_ROOT), "--probe"]) == 0
    assert not (REPO_ROOT / "mbdsdr_ai" / "data" / "_gpl_probe.json").exists()


def test_probe_inside_tmp_repo(tmp_path):
    root = _fake_repo(tmp_path, {"mbdsdr_ai/data/base.json": "{}"})
    assert checker._probe(root) == 0
    assert not (root / "mbdsdr_ai" / "data" / "_gpl_probe.json").exists()


# ── 判定精度 ─────────────────────────────────────────────────────────

def test_bare_gpl_substring_in_payload_is_not_a_license(tmp_path):
    """base64/长串里的裸 GPL 不是许可声明，不得误报。"""
    root = _fake_repo(tmp_path, {
        "mbdsdr_ai/data/vector.json":
            '{"payload": "LUWfgch4rP4vMDjlRhsGPLQ8/fAG6Nottxuw2vAe6RG4JWNder"}',
    })
    result = checker.scan(root)
    assert result["violations"] == [] and result["warnings"] == []


def test_license_declaration_forms_are_detected(tmp_path):
    root = _fake_repo(tmp_path, {
        "mbdsdr_ai/data/a.json": '{"source_license": "GPLv2"}',
        "mbdsdr_ai/data/b.json": '{"note": "GNU General Public License v3"}',
        "mbdsdr_ai/data/c.json": '{"license": "GPL"}',
        "mbdsdr_ai/data/d.json": "# SPDX-License-Identifier: GPL-3.0-only",
    })
    assert _paths(checker.scan(root)) == {
        "mbdsdr_ai/data/a.json", "mbdsdr_ai/data/b.json",
        "mbdsdr_ai/data/c.json", "mbdsdr_ai/data/d.json"}


def test_whitelist_suppresses_violation(tmp_path):
    root = _fake_repo(tmp_path, {
        "mbdsdr_ai/data/short_names.json":
            '{"meta": {"name_source_license": "GPL-2.0-or-later"}}',
    })
    original = dict(checker.WHITELIST)
    checker.WHITELIST.clear()
    checker.WHITELIST["mbdsdr_ai/data/short_names.json"] = "事实性短名，无版权文本"
    try:
        result = checker.scan(root)
    finally:
        checker.WHITELIST.clear()
        checker.WHITELIST.update(original)
    assert result["violations"] == []
    assert result["warnings"][0]["whitelisted"] is True
    assert result["warnings"][0]["reason"] == "事实性短名，无版权文本"


def test_empty_whitelist_reason_is_config_error(tmp_path):
    root = _fake_repo(tmp_path, {"mbdsdr_ai/data/x.json": '{"license": "GPL"}'})
    original = dict(checker.WHITELIST)
    checker.WHITELIST.clear()
    checker.WHITELIST["mbdsdr_ai/data/x.json"] = "   "
    try:
        with __import__("pytest").raises(checker.ScanError, match="缺理由"):
            checker.scan(root)
    finally:
        checker.WHITELIST.clear()
        checker.WHITELIST.update(original)


def test_build_directories_are_skipped(tmp_path):
    root = _fake_repo(tmp_path, {
        "mbdsdr_ai/build-local/AutogenInfo.json": '{"license": "GPL"}',
        "mbdsdr_ai/build/AutogenInfo.json": '{"license": "GPL"}',
        "mbdsdr_ai/data/real.json": '{"license": "GPL"}',
        ".tmp-tool-results/commit_msg.txt": '{"license": "GPL"}',
    })
    result = checker.scan(root)
    assert _paths(result) == {"mbdsdr_ai/data/real.json"}
    assert {item["path"] for item in result["warnings"]} == set()


def test_npz_meta_is_scanned(tmp_path):
    np = __import__("pytest").importorskip("numpy")
    root = tmp_path
    (root / "mbdsdr_ai" / "data").mkdir(parents=True)
    np.savez(root / "mbdsdr_ai" / "data" / "cat.npz",
             meta=np.array(json.dumps({"source_license": "GPL-3.0"})))
    result = checker.scan(root)
    assert _paths(result) == {"mbdsdr_ai/data/cat.npz"}


# ── 逐条来源与许可证（--list）────────────────────────────────────────

def test_declarations_extract_license_and_source():
    decl = checker._declarations(
        '{"meta": {"source": "OpenNGC", "source_license": "CC-BY-SA-4.0",'
        ' "source_license_url": "https://example.invalid"}}')
    assert decl["license"] == "CC-BY-SA-4.0"
    assert decl["source"] == "OpenNGC"


def test_declarations_ignore_non_json():
    assert checker._declarations("Name;Type\nNGC1;G\n") == {}


def test_provenance_override_for_silent_csv(tmp_path):
    root = _fake_repo(tmp_path, {
        "mbdsdr_ai/tests/fixtures/openngc/NGC.csv": "Name;Type\nNGC1;G\n",
    })
    files = checker.scan(root, include_provenance=True)["files"]
    assert files[0]["license"] == "CC BY-SA 4.0"
    assert files[0]["source"] == "OpenNGC NGC.csv"


def test_list_mode_reports_shipped_data_provenance(capsys):
    assert checker.main(["--root", str(REPO_ROOT), "--list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    by_path = {item["path"]: item for item in payload["files"]}
    assert by_path["mbdsdr_ai/data/constellations_iau.json"]["license"] == \
        "CC BY-SA 4.0"
    assert by_path["mbdsdr_ai/data/constellation_names_cc0.json"] == {
        "path": "mbdsdr_ai/data/constellation_names_cc0.json",
        "license": "CC0-1.0",
        "source": "Wikidata",
        "in_mit_package": True,
    }
    assert by_path["mbdsdr_ai/data/full_sky_hyg_v41.npz"]["source"].startswith(
        "HYG")
    assert by_path["mbdsdr_ai/data/dso_catalog.json"]["source"] == "OpenNGC"
    assert "mbdsdr_ai/README.md" not in by_path      # 非数据扩展名不扫
    assert by_path["mbdsdr_ai/tests/fixtures/openngc/addendum.csv"] \
        ["license"] == "CC BY-SA 4.0"


# ── CLI 契约 ─────────────────────────────────────────────────────────

def test_cli_exit_codes(tmp_path, capsys):
    red = _fake_repo(tmp_path / "red", {
        "mbdsdr_ai/data/bad.json": '{"license": "GPL"}',
    })
    assert checker.main(["--root", str(red)]) == 1
    capsys.readouterr()

    green = _fake_repo(tmp_path / "green", {
        "mbdsdr_ai/data/ok.json": '{"license": "MIT"}',
    })
    assert checker.main(["--root", str(green)]) == 0
    capsys.readouterr()

    broken = _fake_repo(tmp_path / "broken", {
        "mbdsdr_ai/data/broken.npz": "not a zip",
    })
    assert checker.main(["--root", str(broken)]) == 2
    capsys.readouterr()


def test_cli_json_output_is_machine_readable(tmp_path, capsys):
    root = _fake_repo(tmp_path, {"mbdsdr_ai/data/ok.json": '{"license": "MIT"}'})
    assert checker.main(["--root", str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) >= {"root", "scanned", "hits", "violations", "warnings"}
    assert payload["violations"] == []
