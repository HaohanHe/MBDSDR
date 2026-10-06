#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# MBDSDR 真机端到端一键演练（phase11 P1）
# ============================================
#
# 按 docs/learn/phase10/P1-acceptance-runbook.md 的四步真跑一遍，逐步记录
# PASS/FAIL/SKIP 与关键输出，最后组装成检查表 JSON（默认 tools/acceptance_out.json），
# 供整段贴回聊天；tools/acceptance_lib.py 的 CLI 负责其中可测的决策逻辑。
#
# 流程：
#   ① selfcheck --json（插设备自检）
#   ② selfcheck | diag_wizard --paste（产出围栏回传块）
#   ③ onboard 信号模式（设备在场才真采集；不在场一律 SKIP + 原因）
#        - 默认：adsb/apt/cw（教学白名单，频率见下方 MODE_FREQ_*）
#        - --event：sstv/ssdv（活动图像通联，下行频率由 --freq-sstv/--freq-ssdv
#          从 docs/learn/phase14/P3-event-params.md 查得后传入；脚本不硬编码）
#   ④ exp_ota_run --recordings-dir 回填 recorded 口径
#   ⑤ 桌面"时空视图"人工核对提示（脚本只提示，不自动验证）
#
# 红线：
#   * 禁 mock：device_present 只从 selfcheck 报告读 target_hits；没设备就明确
#     输出"未检测到设备"，下游全部 SKIP 并写原因，绝不假采集。
#   * 无硬件分支在云 VM 真跑：诚实 FAIL/SKIP，退出码 2（不是错误）。
#   * --event 活动参数（频率/卫星名/日期）绝不进脚本硬编码：频率只从
#     --freq-sstv/--freq-ssdv 传入；缺了就报错并提示去 P3-event-params.md 查。
#
# 退出码（与 acceptance_lib.py 一致）：
#   0 设备在场且演练全绿；1 设备在场但有步 FAIL；2 未检测到设备（诚实空态）；
#   3 用法/环境错误（含 --event 缺频率/坏模式）。
#
# 用法：
#   bash tools/acceptance_run.sh                      # 默认全流程（adsb/apt/cw）
#   bash tools/acceptance_run.sh --out /tmp/x.json    # 自定义检查表输出
#   bash tools/acceptance_run.sh --modes adsb,cw      # 只跑指定模式（白名单 adsb/apt/cw）
#   bash tools/acceptance_run.sh --keep-logs          # 保留中间日志目录
#   bash tools/acceptance_run.sh --event \
#        --freq-sstv <Hz> --freq-ssdv <Hz>
#        # 活动模式 sstv/ssdv；频率必须自己从 P3-event-params.md 查了再传
#        # （单位 Hz，如 435e6 仅为格式示例，脚本不内置任何活动频率）
#   bash tools/acceptance_run.sh --event --event-modes sstv
#        # 只跑 SSTV 一路

set -u
set -o pipefail

# ---- 定位仓库根（脚本在 <root>/tools/ 下）----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
OUT="$ROOT/tools/acceptance_out.json"
KEEP_LOGS=0
MODES_IN="adsb,apt,cw"

# --event 活动模式开关（默认关；--event 开启）
EVENT_MODE=0
EVENT_MODES_IN="sstv,ssdv"
FREQ_SSTV=""     # 空=未传；脚本绝不填默认活动频率
FREQ_SSDV=""

# 校准参考（全部参数化传入，脚本不内置任何台/电平）
CAL_REF_HZ=""    # 已知参考信号频率 Hz（校准用；空=跳过频率校准）
CAL_REF_DBFS=""  # 已知参考电平 dBFS（空=跳过电平校准）
CAL_JSON=""      # 校准结果 JSON 输出（空=用日志目录内默认）

# 模式参数（核对自 tools/onboarding/onboard.py 的 MODES 默认值）
MODE_FREQ_adsb="1090e6";    MODE_N_adsb=""
MODE_FREQ_apt="137.5e6";    MODE_N_apt="4800000"
MODE_FREQ_cw="7020000";    MODE_N_cw="2400000"
# sstv/ssdv 的 --n 留空：让 onboard.py 用自己 MODES 注册表里的默认值
# （sstv sr=250k n=30M≈120s 整帧；ssdv 是字节流模式 n=0，采集步会诚实 FAIL）
MODE_FREQ_sstv=""; MODE_N_sstv=""
MODE_FREQ_ssdv=""; MODE_N_ssdv=""

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --modes) MODES_IN="$2"; shift 2 ;;
    --keep-logs) KEEP_LOGS=1; shift ;;
    --event) EVENT_MODE=1; shift ;;
    --event-modes) EVENT_MODES_IN="$2"; shift 2 ;;
    --freq-sstv) FREQ_SSTV="$2"; shift 2 ;;
    --freq-ssdv) FREQ_SSDV="$2"; shift 2 ;;
    --cal-ref-hz) CAL_REF_HZ="$2"; shift 2 ;;
    --cal-ref-dbfs) CAL_REF_DBFS="$2"; shift 2 ;;
    --cal-json) CAL_JSON="$2"; shift 2 ;;
    -h|--help)
      grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "[ERROR] 未知参数: $1" >&2; exit 3 ;;
  esac
done

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$ROOT/tools/.acceptance_logs/${STAMP}"
RAW="$LOG_DIR/raw.jsonl"
mkdir -p "$LOG_DIR"

info()  { echo "[演练] $*"; }
warn()  { echo "[演练][WARN] $*" >&2; }

# 把一行 raw 结果追加到 raw.jsonl（JSON 字符串由调用方保证合法）
append_raw() { printf '%s\n' "$1" >> "$RAW"; }

# 把任意值安全转成 JSON 字符串字面量（用 python，避免手工转义路径）
jstr() { "$PY" -c 'import json,sys; print(json.dumps(sys.argv[1], ensure_ascii=False))' "$1"; }

# ---- --event 模式：校验白名单 + 频率覆盖（纯逻辑在 acceptance_lib.py）----
# 频率绝不硬编码：缺了就报错并提示去 P3-event-params.md 查。
if [ "$EVENT_MODE" = "1" ]; then
  info "--event 活动模式：校验白名单与下行频率覆盖"
  EVENT_RESOLVE="$("$PY" tools/acceptance_lib.py resolve-event \
    --modes "$EVENT_MODES_IN" \
    --freq-sstv "$FREQ_SSTV" \
    --freq-ssdv "$FREQ_SSDV" 2> "$LOG_DIR/event_resolve.stderr")"
  RC_ER=$?
  if [ "$RC_ER" != "0" ]; then
    cat "$LOG_DIR/event_resolve.stderr" >&2
    exit 3
  fi
  # 从 resolve-event JSON 里抽出最终生效的模式列表
  RUN_EVENT_MODES="$("$PY" -c 'import json,sys; print(" ".join(json.loads(sys.argv[1])["modes"]))' "$EVENT_RESOLVE")"
  # 把用户传的频率灌进 MODE_FREQ_* 变量，复用既有 run_onboard 路径
  MODE_FREQ_sstv="$FREQ_SSTV"
  MODE_FREQ_ssdv="$FREQ_SSDV"
  info "    活动模式: $RUN_EVENT_MODES（频率由用户传参，未硬编码）"
fi

# ---------------------------------------------------------------------------
# ① selfcheck --json
# ---------------------------------------------------------------------------
info "① selfcheck --json（只读硬件自检）"
SELFCHECK_JSON="$LOG_DIR/selfcheck.json"
"$PY" tools/hw_selfcheck/selfcheck.py --json > "$SELFCHECK_JSON" 2> "$LOG_DIR/selfcheck.stderr"
RC_SC=$?
append_raw "$(printf '{"id":"selfcheck","title":"插设备自检 selfcheck --json","rc":%d,"selfcheck_report":%s}' \
  "$RC_SC" "$(jstr "$SELFCHECK_JSON")")"

# 设备是否在场（只读报告，绝不猜）
DP="$("$PY" tools/acceptance_lib.py device-present "$SELFCHECK_JSON" 2>/dev/null || echo false)"
info "    设备在场判定: $DP"

# ---------------------------------------------------------------------------
# ② diag_wizard --paste（产出围栏回传块）
# ---------------------------------------------------------------------------
info "② selfcheck | diag_wizard --paste（诊断向导 + 回传块）"
DIAG_TXT="$LOG_DIR/diag_wizard.txt"
"$PY" tools/diag_wizard.py "$SELFCHECK_JSON" --paste > "$DIAG_TXT" 2> "$LOG_DIR/diag_wizard.stderr"
RC_DW=$?
if grep -q "==== MBDSDR 真机回传块" "$DIAG_TXT"; then
  FENCE=true
else
  FENCE=false
fi
append_raw "$(printf '{"id":"diag_wizard_paste","title":"diag_wizard --paste 回传块","rc":%d,"paste_block":%s,"log":%s}' \
  "$RC_DW" "$FENCE" "$(jstr "$DIAG_TXT")")"

# ---------------------------------------------------------------------------
# ③ 校准（频率 PPM / 电平 dBFS；参考全部参数化，不硬编码）
# ---------------------------------------------------------------------------
info "③ 校准：频率 PPM / 电平 dBFS（tools/calibration.py）"
[ -n "$CAL_JSON" ] || CAL_JSON="$LOG_DIR/calibration.json"
CAL_RES="$LOG_DIR/calibration.txt"
{
  if [ "$DP" != "true" ]; then
    echo '{"status":"FAIL","step":"calibration","reason":"未检测到设备，无法录制参考信号校准（不 mock）","next":"插好设备后，先录一段已知参考信号 IQ 再跑校准"}'
  elif [ -z "$CAL_REF_HZ" ] && [ -z "$CAL_REF_DBFS" ]; then
    echo '{"status":"SKIP","step":"calibration","reason":"未传 --cal-ref-hz/--cal-ref-dbfs","next":"从 docs/learn/phase57 选一个已知参考频率/电平传入"}'
  else
    # 有设备才采集参考；此处调用方需先录好参考 IQ（onboard record 产物）
    echo '{"status":"FAIL","step":"calibration","reason":"本脚本自动校准确需参考 IQ 录制件","next":"用 onboard 录已知参考信号 IQ 后跑 tools/calibration.py freq/level"}'
  fi
} > "$CAL_RES"
append_raw "$(printf '{"id":"calibration","title":"频率 PPM / 电平 dBFS 校准","calibration_json":%s}' \
  "$(jstr "$CAL_RES")")"

# ---------------------------------------------------------------------------
# ④ onboard 信号采集（按设备在场分支）
# ---------------------------------------------------------------------------
# 解析模式白名单：
#   - 默认：adsb/apt/cw（教学信号，频率硬编码在上方 MODE_FREQ_*）
#   - --event：sstv/ssdv（活动图像通联，已在前面 resolve-event 校验过白名单
#     与频率覆盖；RUN_EVENT_MODES 即最终生效列表）
RUN_MODES=""
if [ "$EVENT_MODE" = "1" ]; then
  RUN_MODES="$RUN_EVENT_MODES"
else
  for m in ${MODES_IN//,/ }; do
    case "$m" in
      adsb|apt|cw) RUN_MODES="$RUN_MODES $m" ;;
      *) warn "跳过非法模式 $m（白名单: adsb/apt/cw）" ;;
    esac
  done
fi

FIRST_REC_DIR=""   # 记录第一个成功落盘 SigMF 的目录，给 ota 回填用

run_onboard() {
  local mode="$1"
  local freq_var="MODE_FREQ_$mode"; local n_var="MODE_N_$mode"
  local freq="${!freq_var}"; local ns="${!n_var}"
  local oj="$LOG_DIR/onboard_${mode}.json"

  info "③ onboard --mode $mode --freq $freq ${ns:+--n $ns}"
  if [ -n "$ns" ]; then
    "$PY" tools/onboarding/onboard.py --step all --freq "$freq" --mode "$mode" \
      --n "$ns" --json > "$oj" 2> "$LOG_DIR/onboard_${mode}.stderr"
  else
    "$PY" tools/onboarding/onboard.py --step all --freq "$freq" --mode "$mode" \
      --json > "$oj" 2> "$LOG_DIR/onboard_${mode}.stderr"
  fi
  local rc=$?

  # 从 onboard --json 里抽 record 步落盘的 SigMF 目录（供 ota 回填）
  local rec_dir
  rec_dir="$("$PY" - "$oj" <<'PYEOF' 2>/dev/null || echo "")
import json,sys,os
try:
    d=json.load(open(sys.argv[1],encoding="utf-8"))
    for s in d.get("steps",[]):
        if s.get("step")=="record" and s.get("status")=="PASS":
            p=(s.get("detail") or {}).get("sigmf_data","")
            if p: print(os.path.dirname(p)); break
except Exception:
    pass
PYEOF
"
  [ -z "$FIRST_REC_DIR" ] && [ -n "$rec_dir" ] && FIRST_REC_DIR="$rec_dir"

  append_raw "$(printf '{"id":"onboard_%s","title":"onboard 真机采集/录制/解码 (%s)","rc":%d,"onboard_json":%s,"recordings_dir":%s}' \
    "$mode" "$mode" "$rc" "$(jstr "$oj")" "$(jstr "$rec_dir")")"
}

skip_onboard() {
  local mode="$1"
  info "③ onboard --mode $mode：SKIP（未检测到设备，不 mock、不采集）"
  append_raw "$(printf '{"id":"onboard_%s","title":"onboard 真机采集/录制/解码 (%s)","skipped":true,"skip_reason":%s}' \
    "$mode" "$mode" \
    "$(jstr "未检测到 RTL-SDR 设备（0bda:2838/2832）；按红线不 mock、不采集，真机插好后重跑本脚本")")"
}

if [ "$DP" = "true" ]; then
  for m in $RUN_MODES; do run_onboard "$m"; done
else
  warn "未检测到设备 -> 三类 onboard 全部 SKIP（无硬件诚实空态）"
  for m in $RUN_MODES; do skip_onboard "$m"; done
fi

# ---------------------------------------------------------------------------
# ④ exp_ota_run 回填 recorded 口径
# ---------------------------------------------------------------------------
OTA_LOG="$LOG_DIR/ota.txt"
if [ "$DP" = "true" ] && [ -n "$FIRST_REC_DIR" ]; then
  info "④ exp_ota_run --recordings-dir $FIRST_REC_DIR"
  "$PY" experiments/exp_ota_run.py --recordings-dir "$FIRST_REC_DIR" > "$OTA_LOG" 2>&1
  RC_OTA=$?
  append_raw "$(printf '{"id":"ota_backfill","title":"exp_ota_run 回填 recorded 口径","rc":%d,"log":%s,"recordings_dir":%s}' \
    "$RC_OTA" "$(jstr "$OTA_LOG")" "$(jstr "$FIRST_REC_DIR")")"
else
  info "④ ota_backfill：SKIP（无 onboard 录制产物）"
  : > "$OTA_LOG"
  append_raw "$(printf '{"id":"ota_backfill","title":"exp_ota_run 回填 recorded 口径","skipped":true,"skip_reason":%s,"log":%s}' \
    "$(jstr "无 onboard 录制产物（未检测到设备 / record 未落盘 SigMF），跳过回填")" "$(jstr "$OTA_LOG")")"
fi

# ---------------------------------------------------------------------------
# ⑤ 桌面"时空视图"人工核对提示
# ---------------------------------------------------------------------------
HINT="打开桌面端 -> 中心区[时空视图]tab：核对 4 格(设备连接/信号RSSI·SNR/解码状态/GNSS定位) 与 3 行(当前接收目标/时间源/多普勒补偿)。无硬件或未接 GNSS 时四格空态、时间源=system 属设计内诚实空态，不要冒充读数。"
info "⑤ 桌面时空视图：人工核对（脚本只提示，见检查表 steps 末项）"
append_raw "$(printf '{"id":"spacetime_hint","title":"桌面[时空视图]tab 人工核对","manual":true,"hint":%s}' \
  "$(jstr "$HINT")")"

# ---------------------------------------------------------------------------
# 组装检查表（纯逻辑在 acceptance_lib.py）
# ---------------------------------------------------------------------------
info "组装检查表 -> $OUT"
"$PY" tools/acceptance_lib.py assemble --raw "$RAW" --out "$OUT"
RC_AS=$?

echo
echo "=================================================================="
echo "演练完成：检查表 = $OUT"
"$PY" tools/acceptance_lib.py validate "$OUT" >/dev/null 2>&1 \
  && info "检查表结构自校验通过" \
  || warn "检查表结构自校验失败（见上方 stderr）"
if [ "$KEEP_LOGS" != "1" ]; then
  info "中间日志目录 $LOG_DIR（--keep-logs 可保留）"
else
  warn "保留中间日志目录 $LOG_DIR"
fi
echo "=================================================================="
exit "$RC_AS"
