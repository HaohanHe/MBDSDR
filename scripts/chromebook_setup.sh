#!/usr/bin/env bash
# MBDSDR — Chromebook / Crostini 一键就绪脚本
# ============================================
# 解决"系统认了 RTL-SDR，但 SDR++ / rtl_test 不认"的经典三连：
#   1) DVB-T 内核驱动(dvb_usb_rtl28xxu)抢走了设备 → blacklist + rmmod
#   2) 没装 librtlsdr(rtl-sdr)            → apt 安装
#   3) 普通用户无 USB 权限                 → udev 规则
#   4) USB 没在 Chrome 端授权给 Linux(Crostini 不能自己做，需人工)
#
# 用法（在 Crostini / Debian 终端里）：
#   bash scripts/chromebook_setup.sh
# 然后插上 RTL-SDR，再跑：
#   python3 scripts/rtl_selfcheck.py
#
# 幂等：可重复运行。不需要 sudo 密码之外的交互。
set -u

BLUE="\033[1;34m"; GREEN="\033[1;32m"; YELLOW="\033[1;33m"; RED="\033[1;31m"; RST="\033[0m"
say(){ echo -e "${BLUE}== $*${RST}"; }
ok(){   echo -e "${GREEN}[PASS] $*${RST}"; }
warn(){ echo -e "${YELLOW}[WARN] $*${RST}"; }
bad(){  echo -e "${RED}[FAIL] $*${RST}"; }

echo "=============================================="
echo " MBDSDR Chromebook/Crostini RTL-SDR 就绪脚本"
echo "=============================================="

# --- 0) 环境判断 ---
if [ ! -e /dev/.cros_workload_manager ] && [ ! -d /usr/share/cros ] && ! grep -qi "chromebook\|crostini\|termina" /proc/version 2>/dev/null; then
  warn "看起来不是 Chromebook Crostini 环境，仍按 Debian 流程继续。"
fi
if [ "$(id -u)" -ne 0 ]; then
  say "需要 sudo 来装包/写内核与 udev 规则。"
  SUDO="sudo"
else
  SUDO=""
fi

# --- 1) 装 librtlsdr + 工具 ---
say "安装 librtlsdr (rtl-sdr) 与诊断工具"
$SUDO apt-get update -y -qq >/dev/null 2>&1 || true
if apt-cache policy rtl-sdr >/dev/null 2>&1; then
  $SUDO apt-get install -y rtl-sdr 2>/dev/null && ok "rtl-sdr (librtlsdr) 已安装" || bad "rtl-sdr 安装失败"
else
  warn "apt 源里没有 rtl-sdr 包，尝试 librtlsdr-dev / rtl-utils"
  $SUDO apt-get install -y librtlsdr-dev rtl-utils 2>/dev/null && ok "已安装 librtlsdr 开发包与 rtl-utils" || bad "装不上 librtlsdr，请手动: sudo apt install rtl-sdr"
fi

# --- 2) blacklist 抢走设备的 DVB-T 内核驱动 ---
say "屏蔽 DVB-T 电视内核驱动(否则它抢走 RTL2832U)"
MODCONF=/etc/modprobe.d/blacklist-rtl-sdr.conf
$SUDO tee "$MODCONF" >/dev/null <<'EOF'
# MBDSDR: 禁止 DVB-T 电视驱动抢占 RTL2832U
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2832_sdr
install dvb_usb_rtl28xxu /bin/true
EOF
ok "已写 $MODCONF"
# 立刻卸载已加载的抢占驱动
$SUDO rmmod dvb_usb_rtl28xxu 2>/dev/null && warn "已卸载占用设备的 dvb_usb_rtl28xxu" || true
$SUDO rmmod rtl2832_sdr 2>/dev/null || true

# --- 3) udev 权限：普通用户可访问 USB ---
say "写 udev 规则：plugdev 组可访问 RTL-SDR"
UDEV=/etc/udev/rules.d/20-rtlsdr.rules
$SUDO tee "$UDEV" >/dev/null <<'EOF'
# RTL2832U 系列 (0bda:2832/2838/283a...)
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="283a", GROUP="plugdev", MODE="0660"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="283b", GROUP="plugdev", MODE="0660"
EOF
ok "已写 $UDEV"
$SUDO udevadm control --reload-rules 2>/dev/null && $SUDO udevadm trigger 2>/dev/null || true
# 把当前用户加进 plugdev
if groups "$(whoami)" | grep -q plugdev; then
  ok "当前用户已在 plugdev 组"
else
  $SUDO usermod -aG plugdev "$(whoami)" && warn "已把 $(whoami) 加入 plugdev 组（重开终端生效）" || true
fi

# --- 4) USB 透传提示（这步必须人在 Chrome 做）---
echo ""
say "接下来这一步必须你在 Chrome 端做（Crostini 自己做不了）"
cat <<'GUIDE'
  1. 把 RTL-SDR 棒插到 Chromebook USB 口
  2. Chrome 地址栏打开: chrome://usb-internals
     或: 右上角时钟 → 设置 → 高级 → 开发者 → Linux → USB
     或更简单: 在文件/终端窗口右上角有个 "Linux(Debian)" 图标旁的 USB 授权入口
  3. 在 "USB 设备" 列表里找到:
       RTL2832U  (Vendor 0bda, Product 2838/2832/283a)
  4. 点 "Connect/连接" 授权给 Linux (Crostini)
     —— 必须看到它出现在下面 lsusb 里，否则 SDR++ 永远不认
GUIDE

# --- 5) 现在检测 ---
echo ""
say "检测当前 USB 可见设备"
if command -v lsusb >/dev/null 2>&1; then
  lsusb 2>/dev/null | grep -iE "0bda|realtek|rtl" && ok "看到 RTL2832U！USB 透传成功" || bad "lsusb 里没有 0bda —— 回到第 4 步授权 USB"
else
  warn "没装 usbutils，无法 lsusb。装上: sudo apt install usbutils"
fi

echo ""
say "检查 rtl_test 能否打开设备"
if command -v rtl_test >/dev/null 2>&1; then
  timeout 3 rtl_test 2>&1 | head -8 || true
else
  warn "还没有 rtl_test（rtl-sdr 包未装好）。重开终端后再试，或跑: sudo apt install rtl-sdr"
fi

echo ""
echo "=============================================="
ok "就绪脚本跑完。现在插好棒、授权 USB，然后执行："
echo "    python3 scripts/rtl_selfcheck.py"
echo "=============================================="
