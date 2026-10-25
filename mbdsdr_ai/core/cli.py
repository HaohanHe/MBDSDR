# SPDX-License-Identifier: MIT
"""
MBDSDR Core - 命令行接口（argparse）
======================================

``python -m mbdsdr`` 即调用本模块。所有命令都转发给同一个
:class:`~mbdsdr_ai.core.controller.SDRController`，与 GUI / MCP 共用一套逻辑。

用法示例见 ``docs/cli_usage.md``。无设备时打印明确错误和建议，退出码非零。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import List, Optional, Sequence

from .controller import SDRController, VALID_MODES
from .errors import SDRUserError


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器（独立出来便于测试）。"""
    p = argparse.ArgumentParser(
        prog="mbdsdr",
        description="MBDSDR Headless 控制面命令行",
    )
    # 枚举 / 状态
    p.add_argument("--list-devices", action="store_true", help="枚举 SDR 设备")
    p.add_argument("--list-audio", action="store_true", help="枚举音频输出设备")
    p.add_argument("--status", action="store_true", help="打印完整系统状态")

    # 连接
    p.add_argument("--connect", metavar="DEVICE_ID", default=None,
                   help="连接设备（序列号 / driver:serial，或 'debug' 调试信号源）")

    # 接收参数
    p.add_argument("--freq", type=float, metavar="HZ", help="设置中心频率（Hz）")
    p.add_argument("--demod", metavar="MODE", help=f"解调模式：{','.join(VALID_MODES)}")
    p.add_argument("--gain", type=float, metavar="DB", help="设置增益（dB）")
    p.add_argument("--sample-rate", type=float, metavar="SR", help="设置采样率（Hz）")
    p.add_argument("--bandwidth", type=float, metavar="HZ", help="设置信道带宽（Hz）")
    p.add_argument("--squelch", type=float, metavar="DB", help="设置静噪门限（dB）")

    # 音频
    p.add_argument("--audio-out", type=int, metavar="INDEX", help="选择音频输出设备索引")
    p.add_argument("--start-audio", action="store_true", help="启动音频播放")

    # DSP
    p.add_argument("--spectrum", type=int, metavar="NFFT",
                   help="打印一帧功率谱（长度 NFFT）")

    # 扫描 / 解码 / 录制
    p.add_argument("--scan", type=float, nargs=3, metavar=("START", "STOP", "STEP"),
                   help="步进扫频并打印活动段")
    p.add_argument("--decode", metavar="TYPE",
                   help="启动流式解码器（adsb/rds 实时；其余模式见 --decode-file）")
    p.add_argument("--decode-file", metavar="PATH",
                   help="文件级解码，解锁 ft8/wspr/aprs/adsb/noaa_apt/sstv/pocsag/acars/meteor 全家")
    p.add_argument("--mode", metavar="MODE", default=None,
                   help="--decode-file 的模式（ft8/wspr/aprs/adsb/noaa_apt/sstv/pocsag/acars/meteor）")
    p.add_argument("--channel", metavar="CH", default=None,
                   help="--decode-file 的通道选择（noaa_apt: A/B/both，默认 both）")
    p.add_argument("--baud", type=int, metavar="BAUD", default=None,
                   help="--decode-file 的波特率（pocsag: 512/1200/2400，默认 512）")
    p.add_argument("--output-dir", metavar="DIR", default=None,
                   help="--decode-file 的输出目录（默认 ~/.mbdsdr/<mode>/）")
    p.add_argument("--tool", metavar="NAME",
                   help="调用注册工具（sdr_tools 工具面逃生门，含 60+ MCP 工具）")
    p.add_argument("--args", metavar="JSON", default="{}",
                   help="--tool 的 JSON 参数")
    p.add_argument("--record", metavar="PATH", help="开始 IQ 录制到 PATH（SigMF）")

    # 服务
    p.add_argument("--remote-port", type=int, metavar="PORT", help="启动 GQRX 风格远程控制")
    p.add_argument("--web-port", type=int, metavar="PORT", help="启动 Web 服务器")

    # 其它
    p.add_argument("--config", metavar="PATH", default=None, help="配置目录路径")
    p.add_argument("-v", "--verbose", action="store_true", help="打印调试日志")
    return p


def _print_devices(ctrl: SDRController) -> None:
    devs = ctrl.list_devices()
    if not devs:
        print("(未发现 SDR 设备)")
        return
    for d in devs:
        key = getattr(d, "device_key", getattr(d, "serial", str(d)))
        name = getattr(d, "name", str(d))
        print(f"  {key:30s}  {name}")


def _print_audio(ctrl: SDRController) -> None:
    outs = ctrl.list_audio_outputs()
    if not outs:
        print("(无可用音频输出设备；sounddevice 可能未安装或无声卡)")
        return
    for d in outs:
        print(f"  [{d.index}] {d.name}  ({d.channels} ch, {d.default_samplerate:.0f} Hz)")


def run(argv: Optional[Sequence[str]] = None,
        controller: Optional[SDRController] = None) -> int:
    """执行 CLI。返回进程退出码（0 成功，非零失败）。

    Parameters
    ----------
    argv :
        命令行参数（不含程序名）。None 时读 sys.argv[1:]。
    controller :
        可注入已构造的 controller（测试用）；不传则自建。
    """
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    ctrl = controller if controller is not None else SDRController(config_dir=args.config)

    # ---- 无需连接的只读命令 ----
    if args.list_devices:
        _print_devices(ctrl)
        return 0
    if args.list_audio:
        _print_audio(ctrl)
        return 0

    try:
        # ---- 连接 ----
        if args.connect:
            ctrl.connect(args.connect)
            print(f"已连接: {ctrl.get_status().device}")
        elif args.freq or args.demod or args.spectrum or args.scan \
                or args.decode or args.record or args.start_audio:
            # 需要后端的动作但未连接 -> 明确报错（不造假）
            print("错误: 未连接设备。", file=sys.stderr)
            print("建议: 先用 --connect <device_id> 连接真实设备，"
                  "或 --connect debug 使用内置调试信号源测试。",
                  file=sys.stderr)
            return 2

        # ---- 参数下发 ----
        if args.freq is not None:
            ctrl.set_frequency(args.freq)
            print(f"频率 -> {args.freq:.0f} Hz")
        if args.demod:
            ctrl.set_demod(args.demod)
            print(f"解调 -> {ctrl.get_demod()}")
        if args.gain is not None:
            ctrl.set_gain(args.gain)
            print(f"增益 -> {args.gain} dB")
        if args.sample_rate is not None:
            ctrl.set_sample_rate(args.sample_rate)
            print(f"采样率 -> {args.sample_rate:.0f} Hz")
        if args.bandwidth is not None:
            ctrl.set_bandwidth(args.bandwidth)
        if args.squelch is not None:
            ctrl.set_squelch(args.squelch)
        if args.audio_out is not None:
            ok = ctrl.set_audio_output(args.audio_out)
            print(f"音频输出设备 -> {args.audio_out} ({'ok' if ok else '失败'})")
        if args.start_audio:
            ok = ctrl.start_audio()
            print(f"音频播放 -> {'已启动' if ok else '不可用（无音频硬件）'}")

        # ---- 一次性数据动作 ----
        if args.spectrum is not None:
            spec = ctrl.read_spectrum(args.spectrum)
            if spec is None:
                print("错误: 无数据（未连接或后端无样本）。", file=sys.stderr)
                return 3
            print(f"频谱帧: nfft={spec.size}, "
                  f"mean={float(spec.mean()):.1f} dB, peak={float(spec.max()):.1f} dB")

        if args.scan:
            start, stop, step = args.scan
            handle = ctrl.start_scan(start, stop, step)
            # 轮询等待完成（最多 30s）
            for _ in range(300):
                time.sleep(0.1)
                if ctrl._scans[handle]["done"]:  # noqa: SLF001 - CLI 内部轮询
                    break
            segs = ctrl.get_scan_results(handle)
            print(f"扫频 {start:.0f}-{stop:.0f} Hz (step {step:.0f}): "
                  f"发现 {len(segs)} 个活动段")
            for s in segs[:20]:
                print(f"  {s['start_freq']/1e6:.3f}-{s['end_freq']/1e6:.3f} MHz "
                      f"peak={s['peak_freq']/1e6:.3f} MHz {s['peak_db']:.1f} dB "
                      f"[{s['kind']}]")

        if args.decode_file:
            # 文件级解码：解锁 decoders.py 的全家（ft8/wspr/aprs/adsb/noaa_apt/
            # sstv/pocsag/acars/meteor）。流式 --decode 只覆盖 IQ 流式解码器
            # （adsb/rds）；文件级走这里。参数经 --mode/--channel/--baud/
            # --sample-rate/--output-dir 透传到 decoders.py 三入口。
            import json as _json
            mode = (args.mode or "").lower()
            if not mode:
                low = args.decode_file.lower()
                if "apt" in low or "noaa" in low: mode = "noaa_apt"
                elif "sstv" in low: mode = "sstv"
                elif "ft8" in low: mode = "ft8"
                elif "wspr" in low: mode = "wspr"
                elif "pocsag" in low: mode = "pocsag"
                elif "acars" in low: mode = "acars"
                elif "meteor" in low or "lrpt" in low: mode = "meteor"
                elif "aprs" in low: mode = "aprs"
                elif "adsb" in low: mode = "adsb"
                else:
                    print("文件级解码需 --mode（ft8/wspr/aprs/adsb/noaa_apt/sstv/"
                          "pocsag/acars/meteor）", file=sys.stderr)
                    return 3
            # 解码采样率：--sample-rate 显式给；没给则按模式取默认
            # （noaa_apt 20800Hz 音频、数字模式 1MHz 基带）。
            dec_sr = args.sample_rate
            try:
                from ..decoders import decode_digital_mode, decode_noaa_apt, decode_sstv
                if mode == "noaa_apt":
                    kw = {"channel": (args.channel or "both")}
                    if dec_sr is not None:
                        kw["sample_rate"] = int(dec_sr)
                    if args.output_dir:
                        kw["output_dir"] = args.output_dir
                    res = decode_noaa_apt(args.decode_file, **kw)
                elif mode == "sstv":
                    kw = {}
                    if args.output_dir:
                        kw["output_dir"] = args.output_dir
                    res = decode_sstv(args.decode_file, **kw)
                else:
                    kw = {}
                    if dec_sr is not None:
                        kw["sample_rate"] = float(dec_sr)
                    if args.output_dir:
                        kw["output_dir"] = args.output_dir
                    if mode == "pocsag" and args.baud:
                        kw["baud"] = int(args.baud)
                    res = decode_digital_mode(args.decode_file, mode, **kw)
                print(_json.dumps(res, ensure_ascii=False, indent=2, default=str))
            except Exception as e:
                print(f"文件级解码失败: {e}", file=sys.stderr)
                return 5

        if args.tool:
            # 工具面逃生门：把 sdr_tools / 各模块 register_tool_registry 的
            # 60+ 工具（含 MCP 工具）从 CLI 直调。没有这个入口之前，
            # 这些工具只能通过 MCP 或 Agent 会话用——CLI 用户等于瞎子。
            import json as _json
            import importlib, pkgutil
            try:
                targs = _json.loads(args.args or "{}")
            except Exception as e:
                print(f"--args 不是合法 JSON: {e}", file=sys.stderr)
                return 3
            from .. import tool_registry as _tr
            reg = _tr.ToolRegistry()
            try:
                import mbdsdr_ai as _pkg
                for _m in pkgutil.iter_modules(_pkg.__path__):
                    try:
                        mod = importlib.import_module(f"mbdsdr_ai.{_m.name}")
                        fn = getattr(mod, "register_tool_registry", None)
                        if callable(fn):
                            fn(reg)
                    except Exception as e:
                        # 模块导入/注册失败不能无声跳过——否则工具面悄悄缺角
                        print(f"[warn] 工具注册跳过 mbdsdr_ai.{_m.name}: {e}",
                              file=sys.stderr)
                        continue
            except Exception as e:
                print(f"工具自动发现失败: {e}", file=sys.stderr)
                return 5
            # sdr_tools 的 60+ 工具挂在 agent.tool_registry——duck-type 假 agent
            # 收编（register_sdr_tools 只用 tool_registry/sdr_manager/spectrum/
            # freq_manager/_plans/_todos 这几个属性，不需要 LLM）。
            try:
                from ..sdr_tools import register_sdr_tools
                class _FakeAgent:
                    tool_registry = reg
                register_sdr_tools(_FakeAgent())
            except Exception as e:
                print(f"[warn] sdr_tools 收编失败（工具面将缺 sdr_* 工具）: {e}",
                      file=sys.stderr)
            names = reg.get_tool_names()
            if args.tool not in names:
                print(f"工具不存在: {args.tool}", file=sys.stderr)
                print(f"可用（{len(names)} 个）: {names[:40]}", file=sys.stderr)
                return 3
            res = reg.call(args.tool, targs)
            out = res.to_dict() if hasattr(res, "to_dict") else res
            print(_json.dumps(out, ensure_ascii=False, indent=2, default=str))

        if args.decode:
            handle = ctrl.start_decoder(args.decode)
            print(f"解码器 {args.decode} 已启动 ({handle})，监听 5 秒...")
            time.sleep(5.0)
            msgs = ctrl.get_decoder_messages(handle)
            print(f"收到 {len(msgs)} 条报文")
            for m in msgs[:10]:
                print("  ", m)
            ctrl.stop_decoder(handle)

        if args.record:
            res = ctrl.start_recording(args.record)
            if "error" in res:
                print(f"录制失败: {res}", file=sys.stderr)
                return 4
            print(f"录制中 -> {res['path']}（10 秒后自动停止）")
            time.sleep(10.0)
            done = ctrl.stop_recording()
            print(f"录制结束: {done['samples']} samples")

        # ---- 服务 ----
        if args.remote_port:
            res = ctrl.start_remote_control(args.remote_port)
            print(f"远程控制: {res}")
        if args.web_port:
            res = ctrl.start_web_server(args.web_port)
            print(f"Web 服务: {res}")

        # ---- 状态 ----
        if args.status:
            import json
            print(json.dumps(ctrl.get_status().to_dict(), ensure_ascii=False, indent=2))

        # ---- 长驻：若起了服务则保持运行 ----
        if args.remote_port or args.web_port:
            print("服务运行中，Ctrl+C 退出...")
            try:
                while True:
                    time.sleep(1.0)
            except KeyboardInterrupt:
                pass
            ctrl.shutdown()
            return 0

        ctrl.shutdown()
        return 0

    except SDRUserError as e:
        print(f"错误: {e.message}", file=sys.stderr)
        if e.suggestion:
            print(f"建议: {e.suggestion}", file=sys.stderr)
        return 1


def main() -> int:
    """``python -m mbdsdr`` 入口。"""
    return run()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
