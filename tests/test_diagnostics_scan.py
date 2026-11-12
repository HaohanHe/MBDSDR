#!/usr/bin/env python3
"""诊断包导出闸的反证：命中必须拒、干净必须放、输出不得含值。"""
from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.util
import io
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "diagnostics_scan.py"
FAKE_PAT = "ghp_" + "a" * 36
RANDOM_B64 = base64.b64encode(
    hashlib.sha256(b"mbdsdr-diagnostics-probe").digest()
).decode()


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("diagnostics_scan", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["diagnostics_scan"] = module
    spec.loader.exec_module(module)
    return module


def run_scan(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *[str(p) for p in paths]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
    )


def test_bundle_with_fake_pat_log_is_blocked(tmp_path: Path) -> None:
    bundle = tmp_path / "diag"
    (bundle / "logs").mkdir(parents=True)
    (bundle / "logs" / "session.log").write_text(
        f"2026-11-13 startup ok\nauthorization: {FAKE_PAT}\n", encoding="utf-8"
    )

    result = run_scan(bundle)
    text = result.stdout + result.stderr
    assert result.returncode == 1, text
    assert "session.log" in text and "github-pat-classic" in text, text
    assert FAKE_PAT not in text, "导出闸的输出不得回显值"


def test_bundle_with_api_key_json_is_blocked(tmp_path: Path) -> None:
    bundle = tmp_path / "diag"
    bundle.mkdir()
    (bundle / "config.json").write_text(
        '{"api_key": "%s"}\n' % RANDOM_B64, encoding="utf-8"
    )

    result = run_scan(bundle)
    text = result.stdout + result.stderr
    assert result.returncode == 1, text
    assert "config.json" in text and "high-entropy-string" in text, text
    assert RANDOM_B64 not in text, text


def test_clean_bundle_passes_without_echoing_values(tmp_path: Path) -> None:
    bundle = tmp_path / "diag"
    (bundle / "logs").mkdir(parents=True)
    (bundle / "logs" / "session.log").write_text(
        "2026-11-13 startup ok\nsatellite count=12\n", encoding="utf-8"
    )
    (bundle / "config.json").write_text('{"api_key": ""}\n', encoding="utf-8")

    result = run_scan(bundle)
    text = result.stdout + result.stderr
    assert result.returncode == 0, text
    assert "EXPORT-BLOCK" not in text, text
    assert "命中=0" in text, text


def test_zip_members_are_scanned(tmp_path: Path) -> None:
    bundle = tmp_path / "diag.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("logs/session.log", f"auth {FAKE_PAT}\n")
        archive.writestr("notes.txt", "clean\n")
    bundle.write_bytes(buffer.getvalue())

    result = run_scan(bundle)
    text = result.stdout + result.stderr
    assert result.returncode == 1, text
    assert "diag.zip!logs/session.log" in text, text
    assert FAKE_PAT not in text, text


def test_gzip_log_is_decompressed_and_scanned(tmp_path: Path) -> None:
    bundle = tmp_path / "diag"
    bundle.mkdir()
    with gzip.open(bundle / "session.log.gz", "wb") as handle:
        handle.write(f"auth {FAKE_PAT}\n".encode())

    result = run_scan(bundle)
    text = result.stdout + result.stderr
    assert result.returncode == 1, text
    assert "session.log" in text and "github-pat-classic" in text, text
    assert FAKE_PAT not in text, text


def test_crash_dump_utf16_strings_are_scanned(tmp_path: Path) -> None:
    bundle = tmp_path / "diag"
    bundle.mkdir()
    # 转储里的字符串：NUL 密度高 → 二进制路径；UTF-16LE 视图必须能看见。
    (bundle / "crash.dmp").write_bytes(
        b"\x00\x01\x02" + ("token=" + FAKE_PAT).encode("utf-16-le") + b"\x00" * 32
    )

    result = run_scan(bundle)
    text = result.stdout + result.stderr
    assert result.returncode == 1, text
    assert "crash.dmp" in text and "github-pat-classic" in text, text
    assert FAKE_PAT not in text, text


def test_assert_exportable_raises_and_names_files(tmp_path: Path) -> None:
    module = _load()
    bundle = tmp_path / "diag"
    bundle.mkdir()
    (bundle / "session.log").write_text(FAKE_PAT + "\n", encoding="utf-8")

    with pytest.raises(module.ExportBlocked) as excinfo:
        module.assert_exportable([str(bundle)])
    assert "session.log" in str(excinfo.value)
    assert FAKE_PAT not in str(excinfo.value)

    (bundle / "session.log").unlink()
    assert not module.assert_exportable([str(bundle)]).blocked
