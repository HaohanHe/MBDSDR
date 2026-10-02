#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# run_all_tests.sh — MBDSDR 一键测试入口
# ============================================================================
# 依次跑：
#   1) C++    : Qt offscreen 下 ctest（含 Qt 库路径）
#   2) Flutter: flutter test + flutter analyze（flutter 路径可配置）
#   3) Python : pytest 五个根目录（experiments/tests、mbdsdr_ai/tests、
#               tools/onboarding、tools/hw_selfcheck、tests/）
#
# 每项打印 [PASS]/[FAIL]/[SKIP]；缺依赖（无构建目录 / 无 flutter / 无 pytest /
# 无测试可收集）标 SKIP 而非失败；末尾汇总并按"是否有 FAIL"决定退出码。
#
# 可通过环境变量覆盖默认路径：
#   QT_LIB_DIR     C++ 链接用的 Qt6 lib 目录（默认 /home/user/Qt/6.8.2/gcc_64/lib）
#   EXTRA_LIB_DIR  追加到 LD_LIBRARY_PATH 的额外 lib（默认 $HOME/.local/lib）
#   FLUTTER_BIN    flutter 可执行文件（默认 /home/user/tools/flutter/bin/flutter）
#   CPP_BUILD_DIR  C++ 构建目录（默认 <root>/cpp/build）
#   MOBILE_DIR     Flutter 工程目录（默认 <root>/mobile）
#   PYTHON_BIN     python 解释器（默认 python3）
#   PYTEST_TIMEOUT  每个 pytest 根目录的墙钟秒数上限（默认 900=15 分钟）。
#                   超时判该根 FAIL（不是 SKIP），避免某个卡死的用例
#                   （无显示/网络长超时/空转）把整个一键脚本挂死。设 0 关闭。
#
# 注意：
#   - C++ ctest 需要系统 Qt6 的 .so，故仅在该段内把 QT_LIB_DIR 注入
#     LD_LIBRARY_PATH；Python 段使用 PySide6（自带 Qt），切勿注入该路径，
#     否则会触发 libQt6DBus ABI 符号冲突。
#   - 无硬件/无凭据的环境里，依赖硬件的用例由 pytest/Qt 自行 skip，不计失败。
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT" || exit 2

# ---- 可配置路径（环境变量优先）--------------------------------------------
QT_LIB_DIR="${QT_LIB_DIR:-/home/user/Qt/6.8.2/gcc_64/lib}"
EXTRA_LIB_DIR="${EXTRA_LIB_DIR:-$HOME/.local/lib}"
FLUTTER_BIN="${FLUTTER_BIN:-/home/user/tools/flutter/bin/flutter}"
CPP_BUILD_DIR="${CPP_BUILD_DIR:-$ROOT/cpp/build}"
MOBILE_DIR="${MOBILE_DIR:-$ROOT/mobile}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PYTEST_TIMEOUT="${PYTEST_TIMEOUT:-900}"   # 每个 pytest 根目录墙钟秒；0=不限制

LOG_DIR="$ROOT/scratch/test-logs"
mkdir -p "$LOG_DIR"

# ---- 结果计数与打印 --------------------------------------------------------
PASS=0; FAIL=0; SKIP=0

report() {
    # $1=PASS|FAIL|SKIP  $2=项名  $3=详情
    case "$1" in
        PASS) PASS=$((PASS + 1)) ;;
        FAIL) FAIL=$((FAIL + 1)) ;;
        SKIP) SKIP=$((SKIP + 1)) ;;
    esac
    printf '[%-4s] %-26s %s\n' "$1" "$2" "$3"
}

hr() { printf '%s\n' "----------------------------------------------------------------------"; }

echo "======================================================================"
echo " MBDSDR 一键测试  (root=$ROOT)"
echo "======================================================================"

# ===========================================================================
# 1) C++ —— Qt offscreen ctest
# ===========================================================================
hr; echo "[ 1/3 ] C++ (Qt offscreen ctest)"; hr
if [ ! -f "$CPP_BUILD_DIR/CMakeCache.txt" ]; then
    report SKIP "cpp_ctest" "无构建目录 $CPP_BUILD_DIR（先 cmake configure 构建，见 cpp/ 文档）"
elif [ ! -d "$QT_LIB_DIR" ]; then
    report SKIP "cpp_ctest" "Qt 库目录不存在: $QT_LIB_DIR（用 QT_LIB_DIR 覆盖）"
elif ! command -v ctest >/dev/null 2>&1; then
    report SKIP "cpp_ctest" "未找到 ctest（用 PATH 或 QT 环境提供）"
else
    log="$LOG_DIR/cpp_ctest.log"
    echo "       日志: $log"
    # 仅本段注入系统 Qt 的 LD_LIBRARY_PATH
    LD_LIBRARY_PATH="$QT_LIB_DIR:$EXTRA_LIB_DIR" QT_QPA_PLATFORM=offscreen \
        ctest --test-dir "$CPP_BUILD_DIR" --output-on-failure >"$log" 2>&1
    rc=$?
    summary="$(grep -E '[0-9]+% tests? passed' "$log" | tail -1)"
    [ -z "$summary" ] && summary="(见日志, rc=$rc)"
    if [ "$rc" -eq 0 ]; then
        report PASS "cpp_ctest" "$summary"
    else
        report FAIL "cpp_ctest" "$summary (rc=$rc)"
    fi
fi

# ===========================================================================
# 2) Flutter —— test + analyze
# ===========================================================================
hr; echo "[ 2/3 ] Flutter (test + analyze)"; hr
if [ ! -x "$FLUTTER_BIN" ]; then
    report SKIP "flutter_test" "未找到 flutter: $FLUTTER_BIN（用 FLUTTER_BIN 覆盖）"
    report SKIP "flutter_analyze" "未找到 flutter: $FLUTTER_BIN（用 FLUTTER_BIN 覆盖）"
elif [ ! -d "$MOBILE_DIR" ]; then
    report SKIP "flutter_test" "无 Flutter 工程目录: $MOBILE_DIR"
    report SKIP "flutter_analyze" "无 Flutter 工程目录: $MOBILE_DIR"
else
    # ---- flutter test ----
    log="$LOG_DIR/flutter_test.log"
    echo "       日志: $log"
    ( cd "$MOBILE_DIR" && "$FLUTTER_BIN" test ) >"$log" 2>&1
    rc=$?
    summary="$(grep -E 'All tests passed|Some tests failed|[0-9]+ (tests?|passed|failed)' "$log" | tail -1)"
    [ -z "$summary" ] && summary="(见日志, rc=$rc)"
    if [ "$rc" -eq 0 ]; then
        report PASS "flutter_test" "$summary"
    else
        report FAIL "flutter_test" "$summary (rc=$rc)"
    fi

    # ---- flutter analyze ----
    log="$LOG_DIR/flutter_analyze.log"
    echo "       日志: $log"
    ( cd "$MOBILE_DIR" && "$FLUTTER_BIN" analyze ) >"$log" 2>&1
    rc=$?
    summary="$(tail -n 1 "$log")"
    [ -z "$summary" ] && summary="(见日志, rc=$rc)"
    if [ "$rc" -eq 0 ]; then
        report PASS "flutter_analyze" "$summary"
    else
        report FAIL "flutter_analyze" "$summary (rc=$rc)"
    fi
fi

# ===========================================================================
# 3) Python —— pytest（五个根目录，逐根报告）
#    注意：不注入系统 Qt 的 LD_LIBRARY_PATH（PySide6 自带 Qt）。
# ===========================================================================
hr; echo "[ 3/3 ] Python (pytest)"; hr
if ! "$PYTHON_BIN" -c 'import pytest' >/dev/null 2>&1; then
    report SKIP "python_pytest" "$PYTHON_BIN 无 pytest（pip install pytest）"
else
    for d in experiments/tests mbdsdr_ai/tests tools/onboarding tools/hw_selfcheck tests; do
        if [ ! -d "$ROOT/$d" ]; then
            report SKIP "pytest:$d" "目录不存在: $d"
            continue
        fi
        log="$LOG_DIR/pytest_$(echo "$d" | tr '/' '_').log"
        echo "       日志: $log  ($d)"
        # exit codes: 0=全部通过  1=有用例失败  2=被中断  5=未收集到任何用例
        #            124=被外层 timeout 墙钟杀掉（卡死用例）
        if [ "$PYTEST_TIMEOUT" -gt 0 ] 2>/dev/null && command -v timeout >/dev/null 2>&1; then
            QT_QPA_PLATFORM=offscreen timeout -k 20 "$PYTEST_TIMEOUT" \
                "$PYTHON_BIN" -m pytest -q "$ROOT/$d" >"$log" 2>&1
        else
            QT_QPA_PLATFORM=offscreen \
                "$PYTHON_BIN" -m pytest -q "$ROOT/$d" >"$log" 2>&1
        fi
        rc=$?
        summary="$(grep -E 'passed|failed|error|no tests ran' "$log" | tail -1)"
        [ -z "$summary" ] && summary="(见日志, rc=$rc)"
        case "$rc" in
            0) report PASS "pytest:$d" "$summary" ;;
            5) report SKIP "pytest:$d" "未收集到用例 (rc=5): $summary" ;;
            124) report FAIL "pytest:$d" "墙钟超时 ${PYTEST_TIMEOUT}s（疑卡死用例，见日志）: $summary" ;;
            *) report FAIL "pytest:$d" "$summary (rc=$rc)" ;;
        esac
    done
fi

# ===========================================================================
# 汇总
# ===========================================================================
hr
echo " 汇总: PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP"
hr
if [ "$FAIL" -gt 0 ]; then
    echo "结果: 存在 FAIL，请查看上方各项与 scratch/test-logs/ 日志。"
    exit 1
fi
echo "结果: 无 FAIL（PASS=通过，SKIP=缺依赖/无硬件，均不算失败）。"
exit 0
