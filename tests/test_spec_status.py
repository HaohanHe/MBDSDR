# SPDX-License-Identifier: MIT
"""Task 115：spec 状态报告（``scripts/spec_status.py``）。

用临时样例 md 钉住真正会坑人的地方：

1. 勾选计数与按 ``##`` 分域归并正确（含缩进 SubTask、``[X]``）；
2. ``not_verified`` / ``not verified`` 的出现次数与逐行条目正确，
   一行两次与一行一次的差别不被抹平；
3. 缺 ``tasks.md`` / ``checklist.md`` 时非零退出并指名缺失文件，且不产出报告；
4. 生成的 Markdown 含总览表、未完成项与 not_verified 清单，数字与解析一致。
"""

import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import spec_status  # noqa: E402

TASKS = """# Tasks

> 图例：`[x]` 表示已落地。

## D1 设备与信号链

- [x] Task 1: alpha
  - [x] SubTask 1.1: beta
  - [ ] SubTask 1.2: gamma
- [ ] Task 2: delta

## D2 数字解码

- [X] Task 3: epsilon（not_verified 说明）

## Task Dependencies

- Task 1 依赖 Task 2
"""

CHECKLIST = """# Checklist

> `not_verified` 不算完成。

## 0. 跨域不变量

- [x] 不变量一

## D1 设备与信号链

- [x] 证据一
- [ ] 真机项——`not_verified`（无硬件）
- [x] 合成项（真实空口 not_verified）

## D2 数字解码

- [x] 解码器存在
- [x] 两种写法 not_verified 与 not verified

## 生命周期门

- [ ] 退役：本轮无退役

## 完成判定

- 代码级条目全部勾选；`not_verified` 保留：真机项
"""


def _write(tmp_path, tasks=TASKS, checklist=CHECKLIST):
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir(exist_ok=True)
    if tasks is not None:
        (spec_dir / "tasks.md").write_text(tasks, encoding="utf-8")
    if checklist is not None:
        (spec_dir / "checklist.md").write_text(checklist, encoding="utf-8")
    return spec_dir


def _domains(report):
    return {record["key"]: record for record in report["domains"]}


class TestCountsAndGroups:
    def test_totals_cover_nested_subtasks(self, tmp_path):
        spec_dir = _write(tmp_path)
        report = spec_status.build_report(spec_dir / "tasks.md",
                                          spec_dir / "checklist.md")
        assert report["totals"]["tasks"] == {
            "checked": 3, "unchecked": 2, "total": 5}
        assert report["totals"]["checklist"] == {
            "checked": 5, "unchecked": 2, "total": 7}

    def test_grouping_by_domain(self, tmp_path):
        spec_dir = _write(tmp_path)
        report = spec_status.build_report(spec_dir / "tasks.md",
                                          spec_dir / "checklist.md")
        domains = _domains(report)
        assert [record["key"] for record in report["domains"]] == [
            "0", "D1", "D2", "lifecycle", "dod", "preamble"]

        assert domains["D1"]["tasks"] == {
            "checked": 2, "unchecked": 2, "total": 4}
        assert domains["D1"]["checklist"] == {
            "checked": 2, "unchecked": 1, "total": 3}
        assert domains["D1"]["title"] == "D1 设备与信号链"
        assert domains["D2"]["tasks"] == {
            "checked": 1, "unchecked": 0, "total": 1}
        assert domains["0"]["checklist"] == {
            "checked": 1, "unchecked": 0, "total": 1}
        assert domains["lifecycle"]["checklist"] == {
            "checked": 0, "unchecked": 1, "total": 1}
        assert domains["dod"]["checklist"]["total"] == 0

    def test_sections_without_checkboxes_are_omitted(self, tmp_path):
        spec_dir = _write(tmp_path)
        report = spec_status.build_report(spec_dir / "tasks.md",
                                          spec_dir / "checklist.md")
        titles = [record["title"] for record in report["domains"]]
        assert not any("Task Dependencies" in title for title in titles)

    def test_incomplete_lists_unchecked_with_lines(self, tmp_path):
        spec_dir = _write(tmp_path)
        report = spec_status.build_report(spec_dir / "tasks.md",
                                          spec_dir / "checklist.md")
        texts = [item["text"] for item in report["incomplete"]]
        assert "Task 2: delta" in texts
        assert "SubTask 1.2: gamma" in texts
        assert "退役：本轮无退役" in texts
        assert "Task 1: alpha" not in texts
        entry = next(item for item in report["incomplete"]
                     if item["text"] == "Task 2: delta")
        assert entry["source"] == "tasks"
        assert entry["domain"] == "D1 设备与信号链"
        assert entry["line"] == 10


class TestNotVerified:
    def test_occurrences_and_lines_differ(self, tmp_path):
        spec_dir = _write(tmp_path)
        report = spec_status.build_report(spec_dir / "tasks.md",
                                          spec_dir / "checklist.md")
        assert report["not_verified"]["occurrences"] == 7
        assert len(report["not_verified"]["items"]) == 6
        assert report["totals"]["not_verified"] == {
            "occurrences": 7, "lines": 6}

    def test_items_keep_state_and_source(self, tmp_path):
        spec_dir = _write(tmp_path)
        report = spec_status.build_report(spec_dir / "tasks.md",
                                          spec_dir / "checklist.md")
        items = report["not_verified"]["items"]
        unchecked = next(item for item in items
                         if "真机项" in item["text"])
        assert unchecked["source"] == "checklist"
        assert unchecked["checked"] is False
        assert "无硬件" in unchecked["text"]
        prose = next(item for item in items if "代码级条目" in item["text"])
        assert prose["checked"] is None
        assert prose["domain"] == "完成判定"
        both = next(item for item in items if "两种写法" in item["text"])
        assert both["checked"] is True

    def test_regex_matches_underscore_and_space(self):
        assert len(spec_status.NOT_VERIFIED_RE.findall(
            "not_verified / not verified / NOT VERIFIED")) == 3


class TestMissingInputs:
    def test_missing_checklist_exits_nonzero(self, tmp_path, capsys):
        spec_dir = _write(tmp_path, checklist=None)
        out = tmp_path / "status.md"
        rc = spec_status.main(["--spec-dir", str(spec_dir), "--out", str(out)])
        captured = capsys.readouterr()
        assert rc == 2
        assert "checklist.md" in captured.err
        assert "tasks.md" not in captured.err
        assert not out.exists()

    def test_missing_directory_lists_both_files(self, tmp_path, capsys):
        rc = spec_status.main(["--spec-dir", str(tmp_path / "nowhere"),
                               "--out", str(tmp_path / "status.md")])
        err = capsys.readouterr().err
        assert rc == 2
        assert "tasks.md" in err and "checklist.md" in err


class TestMarkdownOutput:
    def test_report_contains_overview_and_incomplete(self, tmp_path):
        spec_dir = _write(tmp_path)
        out = tmp_path / "docs" / "spec-status.md"
        rc = spec_status.main(["--spec-dir", str(spec_dir), "--out", str(out)])
        assert rc == 0

        text = out.read_text(encoding="utf-8")
        assert "| 域 | tasks 完成 | checklist 完成 | not_verified |" in text
        assert "| D1 设备与信号链 | 2/4 | 2/3 | 2 |" in text
        assert "| **合计** | **3/5** | **5/7** | **7** |" in text
        assert "## 未完成项（4）" in text
        assert "Task 2: delta" in text
        assert "## not_verified 清单（6 行 / 7 次）" in text
        assert "生成时间：" in text

    def test_json_report_shape(self, tmp_path, capsys):
        spec_dir = _write(tmp_path)
        rc = spec_status.main(["--spec-dir", str(spec_dir), "--json"])
        report = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert report["totals"]["tasks"]["total"] == 5
        assert report["totals"]["checklist"]["total"] == 7
        assert report["not_verified"]["occurrences"] == 7
        assert "generated_at" in report
        assert report["sources"]["tasks"]["path"].endswith("tasks.md")


class TestRealSpec:
    def test_real_spec_totals_match_independent_count(self):
        report = spec_status.build_report()
        for label, filename in (("tasks", "tasks.md"),
                                ("checklist", "checklist.md")):
            text = (spec_status.SPEC_DIR / filename).read_text(
                encoding="utf-8-sig")
            # 独立重算：只认行首（可缩进）的 "- [x]" / "- [ ]"
            checked = 0
            total = 0
            for line in text.splitlines():
                stripped = line.lstrip()
                if not stripped.startswith("- ["):
                    continue
                mark = stripped[3]
                if stripped[4:5] != "]" or mark not in " xX":
                    continue
                total += 1
                checked += mark.lower() == "x"
            assert report["totals"][label]["total"] == total
            assert report["totals"][label]["checked"] == checked
        assert report["totals"]["not_verified"]["occurrences"] > 0


class TestNotVerifiedReasons:
    """R4 同口径：每条 not_verified / 未完成项都要写「为什么没验」。

    `scripts/check_red_lifecycle.py` 的 R4 只看行内有没有原因措辞；报告是
    自动生成的镜像，漏写原因会让整个 docs 门永久红。这里钉住生成器一定补。
    """

    def test_reason_helper_only_fills_when_missing(self):
        assert spec_status._nv_reason("缺硬件，未做夜空验证") == ""
        assert "缺" in spec_status._nv_reason("无硬件，未做夜空验证")
        assert "流程项" in spec_status._nv_reason(
            "SubTask 7.2: 无真机时标 `not_verified`")
        assert "说明性引用" in spec_status._nv_reason(
            "> `not_verified` 不算完成。")
        assert "源行未写原因" in spec_status._nv_reason("alpha beta gamma")

    def test_every_rendered_bullet_carries_a_reason(self):
        """渲染后的每一行都要能过 R4（含源行的原因，或生成器补的说明）。"""
        import re
        reason_re = re.compile(
            r"缺|阻塞|原因|因为|无法|尚未|待|才能|未具备|不可复现|无环境|没有|不满足")
        report = spec_status.build_report()
        md = spec_status.render_markdown(report, "2026-01-01 00:00:00+08:00")
        bullets = [ln for ln in md.splitlines()
                   if ln.startswith("- `") and "第 " in ln]
        assert bullets
        for line in bullets:
            assert reason_re.search(line), f"这条没写原因，R4 会红：{line}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))