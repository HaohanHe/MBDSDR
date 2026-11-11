# SPDX-License-Identifier: MIT
"""回放「10 秒截断」根因取证（W37 前置证据，不改产品代码）。

背景
----
2.4 MS/s 录音回放恒在约 10 s 结束。开工 W37（D-9 裁定 B：最小可回滚）之前，
必须先把三条候选假设逐个排掉，并用实测定位真正的截断点：

  H1 录音缓冲区长度  —— 录制侧只留住 N 个样本，盘上文件本身就短；
  H2 回放定时器/分块 —— QTimer + 50 ms 分块逻辑在 10 s 处停住；
  H3 解码路径输入上限 —— 解调/解码链只吃前 10 s 输入，之后不再出声。

另加一条「形态判定」探针 H4：把采样率当变量扫 4 档，看截断秒数是否随
1/采样率 变化。若截断由固定样本数决定，2.4M→10.0 s、1.024M→23.44 s、
0.48M→50.0 s、3.2M→7.5 s；若是「固定 10 秒」则四档应都停在 10 s。

每个探针都打印「若成立应看到什么 / 实测看到什么」，最后给结论。所有探针跑
产品真实代码路径（真实 `MainWindow._record_write_chunk`、真实
`_begin_replay_from_rec` + 真实 50 ms QTimer、真实 `baseband_io.load_iq`）；
只有音频设备用一个「只计数的假声卡」替身（offscreen 无音频设备），
以及把 `QMessageBox.information` 换成捕获函数（否则 offscreen 下会阻塞）。

未覆盖项（勿误读为「已排除」）
----------------------------
录制链路的乒乓缓冲（`PingPongStream`，4 个块）在高负载下可能随机丢样。
那是「不定位置的样本缺口」，不是「定长 10 s 截断」形态，本脚本**未测**，
H1 的排除结论也不覆盖它。

用法
----
    python scripts/repro/repro_playback_10s.py [--keep] [--workdir DIR]

退出码：0 = 五条判定全部给出；1 = 有探针无法判定（会注明原因）。
大文件（约 0.2 GB + 0.4 GB 稀疏）默认跑完即删，`--keep` 保留现场。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

# offscreen：本探针不涉及星相仪字体渲染，headless 即可。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="回放 10 秒截断根因取证")
    ap.add_argument("--workdir", default=os.path.join(
        os.path.dirname(_ROOT), "output", "tmp", "repro-playback-10s"))
    ap.add_argument("--keep", action="store_true", help="保留临时 IQ 文件")
    return ap.parse_args()


def _isolate_profile(home: str) -> None:
    """把 HOME/USERPROFILE/APPDATA/LOCALAPPDATA 全指向临时目录。

    MainWindow() 会读真实用户配置（DesktopSettings / QSettings / layout_store），
    本脚本必须在导入 desktop 模块之前完成重定向，绝不碰用户真实配置。
    """
    os.makedirs(home, exist_ok=True)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = home


def _print_probe(alias: str, title: str, expect: str, seen: str, verdict: str) -> None:
    print()
    print(f"[{alias}] {title}")
    print(f"  若成立应看到: {expect}")
    print(f"  实测看到:     {seen}")
    print(f"  判定:         {verdict}")


# ----------------------------------------------------------------------
# H1：录音写入路径是否只保留有限长度
# ----------------------------------------------------------------------
def probe_record_writer(workdir: str) -> bool:
    import numpy as np

    from main_window import MainWindow

    path = os.path.join(workdir, "h1_record_cap.cf32")
    total = 26_000_000          # 刻意超过回放上限 24,000,000
    chunk = 1_000_000

    class _Self:
        """只有写盘方法需要的四个属性；方法体是产品真实实现。"""

        _recording = True
        _record_samples_total = 0
        _record_stream_fh = None

        @staticmethod
        def _report_record_abort(msg: str) -> None:
            raise AssertionError(f"录音写盘被中止: {msg}")

    obj = _Self()
    started = time.monotonic()
    with open(path, "wb") as fh:
        obj._record_stream_fh = fh
        for start in range(0, total, chunk):
            n = min(chunk, total - start)
            MainWindow._record_write_chunk(obj, np.zeros(n, np.complex64))
    write_s = time.monotonic() - started

    size = os.path.getsize(path)
    written_samples = size // 8
    static_hits = [
        (i + 1, ln.strip())
        for i, ln in enumerate(
            open(os.path.join(_ROOT, "desktop", "main_window.py"),
                 encoding="utf-8").read().splitlines())
        if "REPLAY_MAX_SAMPLES" in ln
    ]

    print()
    print("  录制写盘静态证据（main_window.py 中 REPLAY_MAX_SAMPLES 的全部引用）：")
    for lineno, text in static_hits:
        print(f"    :{lineno}  {text}")

    ok = (written_samples == total and obj._record_samples_total == total)
    _print_probe(
        "H1", "录音缓冲区/文件长度是否在 24M 样本处截断",
        f"从真实 _record_write_chunk 喂 {total:,} 样本后，盘上只有更少样本",
        f"盘上 {written_samples:,} 样本（{size:,} 字节），"
        f"累计计数 {obj._record_samples_total:,}，耗时 {write_s:.1f}s",
        "排除（录制侧无样本上限；REPLAY_MAX_SAMPLES 只出现在回放读盘）"
        if ok else "成立/可疑（写入数 < 喂入数）",
    )
    return ok


# ----------------------------------------------------------------------
# H4：采样率扫描——截断点是「固定样本数」还是「固定时长」
# ----------------------------------------------------------------------
def probe_rate_sweep(workdir: str) -> bool:
    import numpy as np

    import main_window as mw
    from mbdsdr_ai import baseband_io

    rates = [480_000.0, 1_024_000.0, 2_400_000.0, 3_200_000.0]
    file_secs = 60.0
    cap = int(mw.REPLAY_MAX_SAMPLES)
    rows = []
    ok = True
    for sr in rates:
        path = os.path.join(workdir, f"h4_{int(sr)}.cf32")
        total = int(sr * file_secs)          # 四档都 > cap，保证都会截断
        with open(path, "wb") as fh:
            fh.truncate(total * 8)
        rec = baseband_io.load_iq(path, sample_rate=sr,
                                  max_samples=cap, chunk_samples=1 << 20)
        loaded = int(np.asarray(rec["iq"]).size)
        loaded_s = loaded / sr
        expected = cap / sr
        hit = (loaded == cap and bool(rec.get("truncated"))
               and abs(loaded_s - expected) < 0.01)
        ok = ok and hit
        rows.append((sr, total, loaded, expected, loaded_s,
                     bool(rec.get("truncated")), hit))
        del rec
        os.remove(path)

    print()
    print(f"  文件统一 {file_secs:.0f} s，四档均大于上限 {cap:,} 样本；"
          "截断秒数应≈cap/sr")
    for sr, total, loaded, expected, loaded_s, tr, hit in rows:
        print(f"    {sr / 1e6:6.3f} MS/s -> 文件 {total:>12,} 样本 / "
              f"{total / sr:5.1f}s，载入 {loaded:>10,} / {loaded_s:5.2f}s "
              f"(期望 {expected:5.2f}s)，truncated={tr}  "
              f"{'OK' if hit else 'MISMATCH'}")
    _print_probe(
        "H4", "截断由固定样本数还是固定 10 s 决定",
        "截断秒数恒定 ≈10 s（四档都应停在同一时长）",
        "四档截断秒数 = " + " / ".join(f"{r[4]:.2f}s" for r in rows)
        + f"，载入样本数均 = {cap:,}",
        "固定样本数（截断时长按 1/采样率 变化）"
        if ok else "不成立（读数不符）",
    )
    return ok


# ----------------------------------------------------------------------
# H0 + H2 + H3：真实回放链路
# ----------------------------------------------------------------------
def probe_replay(workdir: str) -> tuple:
    """返回 (H0_ok, H2_ok, H3_ok_or_None, 环境备注)。"""
    import numpy as np
    from PySide6.QtWidgets import QApplication

    import main_window as mw

    app = QApplication.instance() or QApplication(sys.argv)
    win = mw.MainWindow()

    # ---- H0：真实 load_iq + REPLAY_MAX_SAMPLES 读 20 s 文件 ----
    sr_file = 2_400_000.0
    file_samples = int(sr_file * 20)
    long_path = os.path.join(workdir, "h0_long_20s.cf32")
    with open(long_path, "wb") as fh:
        fh.truncate(file_samples * 8)     # 稀疏文件：只占元数据，读为零

    from mbdsdr_ai import baseband_io

    rec = baseband_io.load_iq(
        long_path, sample_rate=sr_file,
        max_samples=mw.REPLAY_MAX_SAMPLES, chunk_samples=1 << 20)
    loaded = int(np.asarray(rec["iq"]).size)
    loaded_s = loaded / sr_file
    file_s = int(rec.get("file_samples") or 0) / sr_file
    truncated = bool(rec.get("truncated"))

    # 真实 UI 说明路径：_begin_replay_from_rec 起播前会弹「只载入前 N 个」
    captured: list = []
    orig_info = mw.QMessageBox.information
    mw.QMessageBox.information = staticmethod(
        lambda parent, title, text, *a, **k: captured.append((title, text)))
    try:
        win._begin_replay_from_rec(rec, long_path)
        announced_total = int(win._replay_total)
    finally:
        mw.QMessageBox.information = orig_info
        win._stop_replay()

    text = captured[0][1] if captured else ""
    h0_ok = (truncated and loaded == int(mw.REPLAY_MAX_SAMPLES)
             and abs(loaded_s - 10.0) < 0.01)
    _print_probe(
        "H0", "真截断点：load_iq 的 REPLAY_MAX_SAMPLES（24,000,000 样本）",
        "20 s / 2.4 MS/s 文件被读成恰好 24,000,000 样本 = 10.0 s，且界面先说明",
        f"file_samples={rec.get('file_samples'):,}（{file_s:.1f}s），"
        f"载入 {loaded:,}（{loaded_s:.2f}s），truncated={truncated}，"
        f"起播 total={announced_total:,}；界面说明={'有' if text else '无'}"
        f"{'：' + text[:48].replace(chr(10), ' ') + '…' if text else ''}",
        "成立（这是 10 s 的来源）" if h0_ok else "不成立（读数不符）",
    )

    # ---- H2/H3：真实 50 ms QTimer 跑完 12 s / 480 kHz 文件 ----
    class _FakePlayer:
        available = True

        def __init__(self) -> None:
            self.written = 0
            self.muted = False

        def start(self) -> bool:
            return True

        def stop(self) -> None:
            return None

        def close(self) -> None:
            return None

        def set_muted(self, muted: bool) -> None:
            self.muted = bool(muted)

        def write(self, audio) -> bool:
            self.written += int(len(audio))
            return True

    fake = _FakePlayer()
    win._audio_player = fake

    audio_box = {"samples": 0}
    orig_tap = win._tap_demod_audio

    def _tap(audio_in):
        audio_box["samples"] += int(len(audio_in))
        return orig_tap(audio_in)

    win._tap_demod_audio = _tap

    snap: dict = {}
    orig_stop = win._stop_replay

    def _stop():
        snap.setdefault("pos", int(win._replay_pos))
        snap.setdefault("total", int(win._replay_total))
        orig_stop()

    win._stop_replay = _stop

    sr = 480_000.0
    secs = 12.0
    n = int(sr * secs)
    t = np.arange(n) / sr
    rng = np.random.default_rng(20261111)
    tone = 0.3 * np.exp(2j * np.pi * 20_000 * t)
    noise = 0.01 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    iq = (tone + noise).astype(np.complex64)
    rec12 = {
        "iq": iq, "sample_rate": sr, "center_freq_hz": 100e6,
        "duration_s": secs, "samples": n, "file_samples": n,
        "truncated": False,
    }

    t0 = time.monotonic()
    win._begin_replay_from_rec(rec12, "<synthetic-12s.cf32>")
    max_pos = 0
    while time.monotonic() - t0 < secs + 20.0:
        app.processEvents()
        max_pos = max(max_pos, int(win._replay_pos))
        if win._replay_timer is None:
            break
        time.sleep(0.01)
    elapsed = time.monotonic() - t0
    completed = win._replay_timer is None
    if not completed:
        win._stop_replay()      # 超时兜底：走 wrapper，仍会留下 pos/total 快照

    pos = snap.get("pos", max_pos)
    total = snap.get("total", n)
    h2_ok = completed and pos >= total and elapsed >= 11.0
    _print_probe(
        "H2", "回放定时器/分块是否在 10 s 处停住",
        "12 s 文件播到 10 s 即停（elapsed≈10 s 且 pos<total）",
        f"completed={completed}，elapsed={elapsed:.2f}s，"
        f"pos={pos:,}/{total:,} 样本（{pos / sr:.2f}s），"
        f"块长 {int(sr * 0.05):,} 样本 × 20 拍/s",
        "排除（播满 12 s 且位置追平载入总量；停止条件是样本耗尽，不是拍数）"
        if h2_ok else "成立/可疑（未追平或提前停）",
    )

    audio_s = audio_box["samples"] / 48000.0
    h3_ok = None
    if audio_box["samples"] <= 0:
        verdict = "无法判定（解调链未产出音频，需带声卡实机复测）"
    elif abs(audio_s - secs) <= secs * 0.1:
        h3_ok = True
        verdict = "排除（48 kHz 输出样本数≈输入时长，链上没有 10 s 输入上限）"
    else:
        h3_ok = False
        verdict = f"成立/可疑（输出 {audio_s:.2f}s 与输入 {secs:.1f}s 不符）"
    _print_probe(
        "H3", "解码/解调路径是否只消费前 10 s 输入",
        "12 s 输入只产出约 10 s 音频（48 kHz 样本数≈480,000）",
        f"_tap_demod_audio 累计 {audio_box['samples']:,} 样本 = {audio_s:.2f}s，"
        f"假声卡收到 {fake.written:,} 样本，解调模式={getattr(win, '_last_demod_mode', '?')}",
        verdict,
    )

    win.close()
    env_note = (f"环境备注：12 s 样本在 offscreen 回放耗时 {elapsed:.2f}s"
                f"（{elapsed / secs:.2f}× 实时，逐拍 FFT 成本所致），"
                "与截断无关，实时性另账。")
    return h0_ok, h2_ok, h3_ok, env_note


def main() -> int:
    args = _parse_args()
    workdir = os.path.abspath(args.workdir)
    os.makedirs(workdir, exist_ok=True)
    _isolate_profile(os.path.join(workdir, "home"))

    # QSettings / layout_store 一并重定向（与 tests/conftest.py isolated_profile 同口径）
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope,
                          os.path.join(workdir, "home", "qsettings"))
    import layout_store as ls
    ls.reset_layout_store_singleton()
    ls._default_store = ls.LayoutStore(  # noqa: SLF001 - 与测试夹具同口径
        os.path.join(workdir, "home", "layouts.json"))

    print("=" * 78)
    print("回放 10 秒截断根因取证")
    print(f"工作目录: {workdir}")
    print(f"隔离 profile: {os.environ['HOME']}")
    print("=" * 78)

    h1_ok = probe_record_writer(workdir)
    h4_ok = probe_rate_sweep(workdir)
    h0_ok, h2_ok, h3_ok, env_note = probe_replay(workdir)

    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)
    print(f"  H1 录音缓冲区长度      : {'排除' if h1_ok else '未排除'}")
    print(f"  H2 回放定时器/分块     : {'排除' if h2_ok else '未排除'}")
    print(f"  H3 解码路径输入上限    : "
          f"{'排除' if h3_ok else ('未判定' if h3_ok is None else '未排除')}")
    print(f"  H4 截断形态            : "
          f"{'固定样本数（随 1/采样率 变化，不是固定 10 s）' if h4_ok else '与固定样本数不符，需重查'}")
    print(f"  H0 实际根因            : "
          f"{'REPLAY_MAX_SAMPLES=24,000,000 样本读盘上限（2.4 MS/s → 10.0 s）' if h0_ok else '未复现，需重查'}")
    print()
    print(f"  {env_note}")
    print()
    print("  W37 验收口径提醒：当前 _replay_total 记的是「本次载入样本数」，")
    print("  不是文件样本数；截断时「回放完成」并不代表播放位置到达文件末尾。")

    if not args.keep:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
        print("\n临时文件已清理（--keep 可保留现场）。")

    return 0 if (h1_ok and h4_ok and h0_ok and h2_ok
                 and h3_ok is not None) else 1


if __name__ == "__main__":
    sys.exit(main())
