# SPDX-License-Identifier: MIT
"""诊断页端到端验收：五类真实损坏 + 一条裸备份反证。

每一类都调用产品自己的构造/加载入口触发备份与理由写入，不手写
``.corrupt-*`` 假装损坏：

1. agent 配置 ``config.load_config()``
2. 书签 ``BookmarkManager()``
3. 布局 ``LayoutStore(...).list_presets()``
4. AI 记忆 ``MemoryStore()``
5. 设备占用登记 ``sdr_backend.get_active_owner()``

五类数据全部落在被 ``default_scan_directories`` 覆盖的 ``~/.mbdsdr`` 下，
随后走真实的 ``collect_diagnostics()`` 与 ``build_pages()`` 出表。
"""

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import mbdsdr_ai.bookmark_manager as bookmark_manager  # noqa: E402
import mbdsdr_ai.config as agent_config  # noqa: E402
import mbdsdr_ai.memory as memory_module  # noqa: E402
import mbdsdr_ai.sdr_backend as sdr_backend  # noqa: E402
import desktop.diagnostics_data as diag  # noqa: E402
from desktop import diagnostics_panel  # noqa: E402
from desktop import layout_store  # noqa: E402

#: 形状与真实 key 一致、但不可能有效的假 key；同时用作 home 目录名，
#: 让脱敏在路径维度也真的被触发一次。
FAKE_KEY = "sk-abcdef1234567890"

#: 类别 → (备份文件名, 理由里必须出现的步骤文案, 必须出现的异常类型)。
EXPECTED = (
    ("agent 配置", "config.json", "配置文件损坏或不可读", "JSONDecodeError"),
    ("书签", "bookmarks.json", "书签文件读取失败", "JSONDecodeError"),
    ("布局", "layouts.json", "布局文件读取失败", "解析失败"),
    ("AI 记忆", "memory.json", "记忆文件损坏", "解析失败"),
    ("设备占用登记", "device_owner.json", "JSON 读取/解析失败", "JSONDecodeError"),
)


def _prepare_corrupt_files(data_root):
    (data_root / "config.json").write_text(
        '{"api_key": "%s", "model": "m", ' % FAKE_KEY, encoding="utf-8")
    (data_root / "bookmarks.json").write_text(
        '{"bookmarks": [', encoding="utf-8")
    (data_root / "layouts.json").write_text('{"presets": ', encoding="utf-8")
    (data_root / "memory.json").write_text(
        '{"memories": [', encoding="utf-8")
    (data_root / "device_owner.json").write_text(
        '{"owners": ', encoding="utf-8")


def _trigger_product_backups(data_root):
    agent_config.load_config(str(data_root / "config.json"))
    bookmark_manager.BookmarkManager()
    layout_store.LayoutStore(str(data_root / "layouts.json")).list_presets()
    memory_module.MemoryStore()
    sdr_backend.get_active_owner("fake-device")


def _print_table(rows):
    print("\n| 类别 | 是否出现 | 理由可读 | 备份文件 |")
    print("|---|---|---|---|")
    for label, appeared, readable, name in rows:
        print(f"| {label} | {appeared} | {readable} | {name} |")
    print()


def test_five_real_corruptions_all_reach_diagnostics(tmp_path, monkeypatch):
    home = tmp_path / FAKE_KEY
    data_root = home / ".mbdsdr"
    data_root.mkdir(parents=True)

    # 把 ~ 指向临时目录，五类默认路径就都落在被扫描的 ~/.mbdsdr 下；
    # 设置目录也对齐到临时目录，避免扫到真实用户目录干扰总数。
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MBDSDR_SETTINGS_DIR", str(tmp_path / "settings"))
    monkeypatch.delenv(sdr_backend.OWNER_REGISTRY_ENV, raising=False)

    _prepare_corrupt_files(data_root)
    _trigger_product_backups(data_root)

    # 反证：没有理由文件的裸备份不说话，按设计口径不应出现。
    bare = data_root / "orphan.json.corrupt-20260101-000000"
    bare.write_text("{}", encoding="utf-8")

    data = diag.collect_diagnostics()
    blob = json.dumps(data, ensure_ascii=False)
    items = data["backups"]["items"]

    rows = []
    for label, filename, step, exc in EXPECTED:
        matches = [it for it in items
                   if os.path.basename(it["backup"]).startswith(filename + ".corrupt-")]
        assert len(matches) == 1, f"{label}：期望 1 条，实际 {len(matches)} 条"
        item = matches[0]
        reason = item["reason"]
        assert reason.strip(), f"{label}：理由为空"
        assert step in reason, f"{label}：理由里没有步骤文案 {step}"
        assert exc in reason, f"{label}：理由里没有异常类型 {exc}"
        rows.append((label, "出现", "可读", os.path.basename(item["backup"])))

    # 总数必须对得上：五类各一条，裸备份不计数。
    assert data["backups"]["total"] == 5, data["backups"]["total"]
    assert len(items) == 5
    assert "orphan.json" not in blob, "裸 .corrupt-* 被误计入"
    rows.append(("裸 .corrupt-*（无理由）", "未出现", "-", bare.name))

    # 脱敏：假 key 既在配置内容里，也在 home 路径里，任何出口都不能带出去。
    assert FAKE_KEY not in blob, "诊断数据结构里仍能拿到完整 key"
    assert "sk-***" in blob, "脱敏没有生效或标记丢失"

    # 面板展示层拿到的是同一份已脱敏数据，逐行可读且不带 key。
    pages = diagnostics_panel.build_pages(data)
    panel_blob = json.dumps(pages, ensure_ascii=False)
    assert FAKE_KEY not in panel_blob
    assert "orphan.json" not in panel_blob, "裸备份出现在了诊断页清单里"
    backup_titles = {row["title"] for row in pages["backups"]}
    for label, filename, _step, _exc in EXPECTED:
        assert any(filename in title for title in backup_titles), \
            f"{label}：面板上找不到对应备份行"

    _print_table(rows)
