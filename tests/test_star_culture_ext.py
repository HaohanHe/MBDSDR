# SPDX-License-Identifier: MIT
"""
tests/test_star_culture_ext.py
==============================
验证文化层扩员（star_culture.json 对 EXT 亮星的中国星名覆盖）:

  1. JSON 合法、键值非空;
  2. 关键星名（北斗/心宿/南斗/五车/南门二等）收录且 zh 正确;
  3. 完整性: 每个 culture 键都能解析到合并星表（防键名打错）;
  4. culture_label 行为: 有中文用中文, 无收录回退西名不编造;
  5. 信息卡标题走文化名（Merak -> 天璇）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_star_culture_ext.py -v
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from mbdsdr_ai.skyengine import stars as _stars  # noqa: E402
from mbdsdr_ai.skyengine import constellations as _constel  # noqa: E402
from rf_sky_view import RFSkyView  # noqa: E402

EXPECTED = {
    "Merak": "天璇", "Phecda": "天玑", "Megrez": "天权",
    "Kochab": "北极二", "Dschubba": "房宿三", "Lesath": "尾宿九",
    "Nunki": "斗宿四", "Kaus Media": "箕宿三", "Rigil Kent": "南门二",
    "Menkent": "库楼三", "Delta Cru": "十字架四", "Izar": "招摇",
    "Seginus": "玄戈", "Rasalhague": "候", "Rasalgethi": "帝座",
    "Zosma": "西上相", "Chertan": "太微右垣一", "Alcyone": "昴宿六",
    "Mintaka": "参宿一", "Saiph": "参宿六", "Zeta Tau": "天关",
    "Caph": "王良一", "Schedar": "王良四", "Alderamin": "天钩五",
    "Almach": "天大将军一", "Mirfak": "天船三", "Menkalinan": "五车三",
    "Iota Aur": "五车一", "Theta Aur": "五车四", "Epsilon Aur": "五车五",
    "Sadalsuud": "虚宿一", "Sadalmelik": "危宿一", "Markab": "室宿一",
    "Scheat": "室宿二", "Algenib": "壁宿一", "Enif": "危宿三",
    "Homam": "雷电一", "Sadr": "天津一", "Sheliak": "渐台二",
    "Sulafat": "渐台三", "Albireo": "辇道增七", "Graffias": "房宿四",
    "Sigma Sco": "心宿一", "Tau Sco": "心宿三", "Epsilon Sco": "尾宿二",
    "Mu1 Sco": "尾宿一", "Pi Sco": "房宿一", "Kaus Borealis": "斗宿一",
    "Ascella": "斗宿三", "Albaldah": "建三", "Gienah Cyg": "天津九",
    "Fawaris": "天津二", "Deneb Algedi": "垒壁阵四", "Dabih": "垒壁阵三",
    "Mirzam": "军市一", "Gomeisa": "南河二", "Mebsuta": "井宿五",
    "Wasat": "天樽二", "Adhafera": "轩辕十一", "Epsilon Leo": "轩辕九",
    "Vindemiatrix": "东次将", "Porrima": "东上相",
    "Zubenelgenubi": "氐宿一", "Zubeneschamali": "氐宿四",
    "Gamma Cas": "策", "Ruchbah": "阁道三", "Gamma Per": "天船二",
    "Kornephoros": "天市右垣五", "Cebalrai": "宗正一",
    "Sabik": "天市左垣十一", "Skat": "虚宿三",
}


class TestCultureExt:
    def test_expected_zh_names(self):
        for name, zh in EXPECTED.items():
            c = _stars.star_culture(name)
            assert c is not None, f"{name} 缺文化注记"
            assert c["zh"] == zh, f"{name}: {c['zh']} != {zh}"

    def test_culture_keys_resolve_to_catalog(self):
        path = os.path.join(_ROOT, "mbdsdr_ai", "data", "star_culture.json")
        raw = json.load(open(path, encoding="utf-8"))
        idx = _constel.merged_star_index()
        for k in raw:
            if k.startswith("_"):
                continue
            assert k in idx, f"culture 键 {k!r} 不在星表（键名打错?）"

    def test_label_fallback_honest(self):
        assert _stars.culture_label("Merak") == "天璇"
        assert _stars.culture_label("No Such Star") == "No Such Star"

    def test_info_card_title_uses_culture(self):
        v = RFSkyView()
        v.set_observer(40.0, 116.0, 0.0)
        v.on_body_picked({"kind": "star", "key": "Merak"})
        assert (v._body_info or {}).get("title") == "天璇"
        assert "北斗" in str((v._body_info or {}).get("desc"))
        v.on_object_picked(None)
