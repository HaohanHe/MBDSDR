# SPDX-License-Identifier: MIT
"""R7 发射（含安全闸）—— 只验门语义，绝不发波。

判据（自动断言，无需发射硬件）：
  * 未武装时模型自报 arm_manual=True → 拒绝，码 gate_arm_manual_untrusted
  * 外部通道签发一次性 token → 放行一次（本脚本**不调用任何发射后端**）
  * token 复用 → gate_token_used；动作不符 → gate_action_mismatch
  * TTL 过期 → gate_expired；撤销后 → gate_revoked
  * 每次判定写入 JSONL 审计，记录可解析
人眼/外部项：真实射频发射（需持证发射设备与频率授权）——UNVERIFIED，不在本脚本范围。
"""
from __future__ import annotations

import json
import os

import common


def run() -> dict:
    out = common.artifact_dir("r7_tx_gate")
    audit_path = os.path.join(out, "tx_audit.jsonl")
    os.environ["MBDSDR_TX_AUDIT_PATH"] = audit_path

    import mbdsdr_ai.tx_guard as tg
    tg.reset_tx_audit(audit_path)

    tool = "sdr_transmit_cw"
    args = {"freq_hz": 145_000_000, "duration_s": 1.0}
    action = tg.action_digest(tool, args)
    checks: list[dict] = []
    verdicts: dict = {}

    # 1) 模型自报 arm_manual 不构成确认
    gate = tg.TxGate()
    d = gate.authorize(arm_manual=True, action=action, tool=tool)
    verdicts["arm_manual"] = {"allowed": d.allowed, "code": d.code}
    checks.append(common.check("arm_manual_rejected",
                               (not d.allowed) and d.code == tg.GATE_MANUAL_UNTRUSTED,
                               f"allowed={d.allowed} code={d.code}"))

    # 2) 未武装 + 伪造 token
    gate = tg.TxGate()
    d = gate.authorize(token="deadbeef" * 4, action=action, tool=tool)
    verdicts["bogus_token"] = {"allowed": d.allowed, "code": d.code}
    checks.append(common.check("bogus_token_rejected", not d.allowed,
                               f"code={d.code}"))

    # 3) 外部通道签发 + 正确动作 → 放行一次（不调用发射后端）
    gate = tg.TxGate(default_ttl_s=5.0)
    token = gate.arm(action=action, note="acceptance-test", source="hw_acceptance")
    d = gate.authorize(token=token, action=action, tool=tool, source="hw_acceptance")
    verdicts["armed_ok"] = {"allowed": d.allowed, "code": d.code}
    checks.append(common.check("armed_allows_once", d.allowed and d.code == tg.GATE_OK,
                               f"allowed={d.allowed} code={d.code}"))

    # 4) 复用同一 token
    d = gate.authorize(token=token, action=action, tool=tool)
    verdicts["token_reuse"] = {"allowed": d.allowed, "code": d.code}
    checks.append(common.check("token_single_use",
                               (not d.allowed) and d.code == tg.GATE_TOKEN_USED,
                               f"code={d.code}"))

    # 5) 动作摘要不符
    gate = tg.TxGate(default_ttl_s=5.0)
    token2 = gate.arm(action=action, note="acceptance-test", source="hw_acceptance")
    other = tg.action_digest(tool, {"freq_hz": 433_920_000, "duration_s": 1.0})
    d = gate.authorize(token=token2, action=other, tool=tool)
    verdicts["action_mismatch"] = {"allowed": d.allowed, "code": d.code}
    checks.append(common.check("action_mismatch_rejected",
                               (not d.allowed) and d.code == tg.GATE_ACTION_MISMATCH,
                               f"code={d.code}"))

    # 6) TTL 过期（注入 now，不等待真实时间）
    gate = tg.TxGate(default_ttl_s=0.05)
    token3 = gate.arm(ttl_s=0.05, action=action, now=1000.0)
    d = gate.authorize(token=token3, action=action, now=1000.1)
    verdicts["expired"] = {"allowed": d.allowed, "code": d.code}
    checks.append(common.check("expired_rejected",
                               (not d.allowed) and d.code == tg.GATE_EXPIRED,
                               f"code={d.code}"))

    # 7) 撤销后拒绝
    gate = tg.TxGate(default_ttl_s=60.0)
    token4 = gate.arm(action=action, source="hw_acceptance")
    gate.revoke(reason="acceptance-test revoke")
    d = gate.authorize(token=token4, action=action)
    verdicts["revoked"] = {"allowed": d.allowed, "code": d.code}
    checks.append(common.check("revoked_rejected",
                               (not d.allowed) and d.code == tg.GATE_REVOKED,
                               f"code={d.code}"))

    # 8) 审计落盘可解析
    lines = 0
    parse_ok = True
    if os.path.exists(audit_path):
        with open(audit_path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                lines += 1
                try:
                    json.loads(ln)
                except Exception:
                    parse_ok = False
    checks.append(common.check("audit_written", lines >= 4, f"{lines} 条 JSONL"))
    checks.append(common.check("audit_parseable", parse_ok, "逐条 JSON 可解析"))

    metrics = {
        "verdicts": verdicts,
        "audit_lines": lines,
        "audit_path": audit_path,
        "rf_emitted": False,
        "note": "只走门判定，未调用任何 TX 后端；真实射频发射未验证。",
    }
    status = common.PASS if all(c["ok"] for c in checks if not c["skipped"]) else common.FAIL
    return common.result(
        "R7", "发射（含安全闸）", status, metrics, checks,
        common.USER_MSG["tx_not_authorized"],
        ["真实射频发射（需持证设备与频率授权）——未验证，不在自动套件范围"], False,
        [audit_path],
        "门语义全部自动断言；本环未产生任何射频能量。RTL-SDR 本身也只能收不能发。")


if __name__ == "__main__":
    args = common.common_args("R7 发射安全闸")
    out = common.artifact_dir("r7_tx_gate")
    os.environ["MBDSDR_TX_AUDIT_PATH"] = os.path.join(out, "tx_audit.jsonl")
    res = common.guarded("R7", "发射（含安全闸）", run, device_required=False,
                         human_required=["真实射频发射"])
    raise SystemExit(common.emit(res, out))
