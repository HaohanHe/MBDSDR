# -*- coding: utf-8 -*-
"""AGENTS.md §3 IIR 数值稳定性合规守卫 + 高采样率鲁棒性回归（#110）。

§3 明文：窄带/高阶 IIR 一律 butter(output="sos")+sosfilt，禁用 ba 系数 lfilter。
历史事故（2026-10-28）：wfm_stereo_lite 5 阶 butter @1.024MHz / 19k±800Hz
（归一化带宽 1.5e-3）ba 系数数值爆炸 → pilot_band**2 溢出 inf → pilot_snr_db=nan；
小幅度合成 fixture 永远测不到，只有真实大动态信号能咬出来。

本文件两层：
  1) 合规守卫（AST）：rds_lite/dsp 的窄带/高阶 butter 必须 output="sos" 且走
     sosfilt——mutation 回 ba（去掉 output="sos" / 改回 lfilter）即红。
  2) 鲁棒性回归：2.048MHz（ba 实测退化 td≈1.5e-4 的率）大动态输入下，解调/解码
     输出有限且正确（sos 不爆炸）。确定性：固定 rng 种子，不绑墙钟/网络/硬件。
"""
import ast
import inspect
import textwrap
import unittest
from pathlib import Path

import numpy as np

import mbdsdr_ai.dsp as D
import mbdsdr_ai.rds_lite as R


def _butter_calls(tree):
    """AST 里所有 butter(...) 调用节点。"""
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if name == "butter":
                yield n


# (标签, 取 butter-AST 的源对象)
_SOS_TARGETS = [
    ("rds_decode_mpx", R.rds_decode_mpx),
    ("rds_decode_from_wfm", D.rds_decode_from_wfm),
    ("audio_to_playback", D.audio_to_playback),
    ("wfm_broadcast_demod", D.wfm_broadcast_demod),
    ("WFMReceiver.__init__", D.WFMReceiver.__init__),
]


class TestSection3SosCompliance(unittest.TestCase):
    """§3 合规守卫：窄带/高阶 butter 一律 sos。mutation 回 ba 即红。"""

    def test_all_narrowband_highorder_butter_use_sos(self):
        for label, obj in _SOS_TARGETS:
            src = textwrap.dedent(inspect.getsource(obj))
            calls = list(_butter_calls(ast.parse(src)))
            self.assertTrue(calls, f"{label}: 未找到 butter 调用")
            for c in calls:
                kw = {k.arg: k.value for k in c.keywords}
                self.assertIn(
                    "output", kw,
                    f"{label}: butter 缺 output=（ba 形式违反 §3，"
                    "高采样率/大动态下数值爆炸）")
                v = kw["output"]
                self.assertTrue(
                    isinstance(v, ast.Constant) and v.value == "sos",
                    f"{label}: butter output 非 'sos'")

    def test_sosfilt_used(self):
        # 滤波必须走 sosfilt（WFMReceiver 的 sosfilt 在 process 里，取整类源）。
        for label, obj in _SOS_TARGETS:
            if label == "WFMReceiver.__init__":
                src = inspect.getsource(D.WFMReceiver)
            else:
                src = inspect.getsource(obj)
            self.assertIn("sosfilt", src, f"{label}: 未用 sosfilt（疑似回退 ba lfilter）")


class TestHighRateRobustness(unittest.TestCase):
    """2.048MHz 大动态：sos 输出有限且正确（ba 在此率实测退化/可爆炸）。"""

    def test_rds_mpx_roundtrip_at_2048k(self):
        # 高速率（ba 退化率）端到端：精确 pi/ps 仍解出。
        bits = []
        for seg, ch in enumerate(["MB", "DS", "DR", "  "]):
            bits += R.build_0a_group(pi=0xDDEE, seg=seg, chars2=ch, pty=3)
        fs = 2048000
        mpx = R.synthesize_rds_mpx([bits], fs=fs, amplitude=0.4,
                                   noise_std=0.002, rng=np.random.default_rng(5))
        res = R.rds_decode_mpx(mpx, fs)
        self.assertTrue(res["rds_present"])
        self.assertEqual(res["pi_hex"], "DDEE")
        self.assertEqual(res["ps"], "MBDSDR")

    def test_rds_from_wfm_high_rate_large_dynamic_finite(self):
        fs = 2048000
        n = int(fs * 0.1)
        t = np.arange(n) / fs
        rng = np.random.default_rng(2026)
        # 大动态 FM IQ（幅度 1e6）：57kHz 调制副载波，鉴频后含 57kHz 分量。
        x = (1e6 * np.exp(1j * 0.5 * np.sin(2 * np.pi * 57000 * t))
             + 1e3 * (rng.standard_normal(n) + 1j * rng.standard_normal(n)))
        res = D.rds_decode_from_wfm(x, fs)
        self.assertIsNotNone(res["carrier_db"])
        self.assertTrue(np.isfinite(res["carrier_db"]),
                        "carrier_db 非有限——ba 数值爆炸回归")
        # 57kHz 副载波落在 55-59k 带通内 → 应检测到（实测 -3.09 dB）。
        self.assertTrue(bool(res["rds_present"]))

    def test_audio_to_playback_high_rate_large_dynamic_finite(self):
        fs = 2048000
        n = int(fs * 0.1)
        t = np.arange(n) / fs
        x = 1e6 * np.sin(2 * np.pi * 1000 * t)  # 大动态 1kHz 音
        out = D.audio_to_playback(x, in_sr=fs, cutoff_hz=4500.0, out_sr=48000)
        self.assertTrue(out.size > 0)
        self.assertTrue(np.all(np.isfinite(out)), "audio_to_playback 输出非有限")
        self.assertGreater(float(np.sqrt(np.mean(out ** 2))), 1e-4)

    def test_wfm_receiver_high_rate_streaming_finite(self):
        fs = 2048000
        rx = D.WFMReceiver(fs, audio_sr=48000)
        # sos 流式 zi 形状必须是 (n_sections, 2)，区别于 lfilter 的 (order,)。
        self.assertEqual(rx._zi_lp.ndim, 2)
        self.assertEqual(rx._zi_lp.shape[1], 2)
        rng = np.random.default_rng(7)
        n = 16384
        t = np.arange(n) / fs
        x = (1e6 * np.exp(1j * 0.5 * np.sin(2 * np.pi * 1000 * t))
             + 1e3 * rng.standard_normal(n))
        out = rx.process(x)
        self.assertTrue(np.all(np.isfinite(out)), "WFMReceiver 输出非有限")
        # 连续两块 zi 不重置（流式契约）。
        out2 = rx.process(x)
        self.assertTrue(np.all(np.isfinite(out2)))


class TestSection3RepoWide(unittest.TestCase):
    """§3 全仓钉扎：mbdsdr_ai 所有生产模块的 butter 必须 output="sos"。

    把 #110 的 5 函数守卫扩成整个包——历史 nan 事故在 wfm_stereo_lite（不在
    5 函数里），只有全仓守卫才能在任何模块回退 ba 时咬红。遵循 AGENTS.md
    「跨语言一致性用钉扎测试锁死，不许靠口头约定」的精神。

    唯一豁免：acars_decoder.ACARSMDemod.discriminate 的 2 阶 butter+filtfilt
    （实测 ba-vs-sos relRMS≈1e-14，机器精度相同、低阶、改它只增金样风险）。
    豁免自 policing：① 阶数必须是 ≤2 的字面量（不能悄悄豁免高阶）；
    ② 豁免集必须被真实消费（acars 若改 sos，陈旧豁免项即红，逼成对更新）。
    范围排除 tests/（测试夹具合法用小幅度 ba）。
    """

    # (相对路径, 叶子函数名) —— 用叶子名而非全 qualname，抗类重命名。
    _EXEMPT = {("acars_decoder.py", "discriminate")}

    @staticmethod
    def _production_files():
        import mbdsdr_ai
        root = Path(mbdsdr_ai.__file__).resolve().parent
        for p in sorted(root.rglob("*.py")):
            if "tests" in p.relative_to(root).parts:
                continue
            yield root, p

    class _ButterFinder(ast.NodeVisitor):
        """带函数/类名栈的 butter 调用收集器。"""

        def __init__(self):
            self.stack = []
            self.calls = []  # (qualname, Call node)

        def _push(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = _push
        visit_AsyncFunctionDef = _push
        visit_ClassDef = _push

        def visit_Call(self, node):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "butter":
                self.calls.append((".".join(self.stack), node))
            self.generic_visit(node)

    @staticmethod
    def _order_literal(node):
        """butter 的阶数 N（args[0] 或关键字 N），仅当是 int 字面量才返回。"""
        a = node.args[0] if node.args else {
            k.arg: k.value for k in node.keywords}.get("N")
        if isinstance(a, ast.Constant) and isinstance(a.value, int):
            return a.value
        return None

    def test_repo_wide_butter_must_be_sos(self):
        violations = []
        matched_exempt = set()
        total = 0
        for root, p in self._production_files():
            rel = p.relative_to(root).as_posix()
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            finder = self._ButterFinder()
            finder.visit(tree)
            for func, node in finder.calls:
                total += 1
                kw = {k.arg: k.value for k in node.keywords}
                out = kw.get("output")
                if isinstance(out, ast.Constant) and out.value == "sos":
                    continue
                leaf = func.split(".")[-1] if func else "<module>"
                key = (rel, leaf)
                if key in self._EXEMPT:
                    order = self._order_literal(node)
                    self.assertIsNotNone(
                        order, f"{rel}:{func} 豁免项阶数非字面量，无法自证低阶")
                    self.assertLessEqual(
                        order, 2,
                        f"{rel}:{func} §3 豁免只允许 ≤2 阶，实际 {order} 阶")
                    matched_exempt.add(key)
                else:
                    violations.append(
                        f"{rel}:{node.lineno} {func or '<module>'} "
                        "butter 非 sos（§3 违规：窄带/高阶 ba 数值爆炸）")
        self.assertGreater(total, 0, "未扫到任何 butter 调用——守卫 walk 失效")
        self.assertEqual(
            violations, [],
            "§3 全仓违规:\n" + "\n".join(violations))
        self.assertEqual(
            matched_exempt, set(self._EXEMPT),
            f"豁免集有陈旧项未被消费（应成对删除）: "
            f"{set(self._EXEMPT) - matched_exempt}")


if __name__ == "__main__":
    unittest.main()