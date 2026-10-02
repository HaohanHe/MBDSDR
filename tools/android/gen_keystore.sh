#!/usr/bin/env bash
# =============================================================================
# gen_keystore.sh — 生成 MBDSDR Android release 签名 keystore
#
# 用法:
#   tools/android/gen_keystore.sh                 # 输出到 ~/keystores/mbdsdr.jks
#   tools/android/gen_keystore.sh /path/to/dir    # 输出到指定目录
#
# 安全红线（务必读）:
#   * 本脚本只在你本机执行；生成的 .jks 与你设的密码 = 你 App 的发布身份。
#   * keystore 一旦丢失/忘记密码，将永远无法给同一个 App 发更新（Google Play
#     以签名指纹识别 App）。请把 keystore 与密码离线备份到安全处（密码管理器/
#     加密 U 盘），不要只放一台机器。
#   * .jks / .keystore / key.properties 已被仓库 .gitignore 忽略，绝不入库；
#     本脚本也不会把密码写进任何仓库路径或日志回显到可见终端外。
#
# 退出码: 0 成功；非 0 失败（已打印原因）。
# =============================================================================
set -euo pipefail

KEYTOOL="$(command -v keytool || true)"
if [ -z "$KEYTOOL" ]; then
  echo "ERROR: 未找到 keytool。请先安装 JDK（需要 JDK 17+），并确认 keytool 在 PATH。" >&2
  exit 1
fi

# ---- 输出目录：$1 > $MBDSDR_KEYSTORE_DIR > ~/keystores ----
OUT_DIR="${1:-${MBDSDR_KEYSTORE_DIR:-$HOME/keystores}}"
KEYSTORE_FILE="$OUT_DIR/mbdsdr.jks"
ALIAS="mbdsdr"

mkdir -p "$OUT_DIR"

if [ -e "$KEYSTORE_FILE" ]; then
  echo "ERROR: $KEYSTORE_FILE 已存在。" >&2
  echo "       为避免覆盖你已有的发布身份，本脚本拒绝继续。" >&2
  echo "       若确实要重新生成，请先手动备份并删除旧文件。" >&2
  exit 2
fi

echo "=============================================================="
echo " 将生成 release keystore:"
echo "   路径   : $KEYSTORE_FILE"
echo "   alias  : $ALIAS"
echo "   算法   : RSA 2048, 有效期 10000 天（约 27 年）"
echo "=============================================================="
echo
echo "⚠  接下来要你设的密码，请务必离线记住/备份。"
echo "   终端输入密码时不会回显（这是正常的）。"
echo

# keytool 交互式会提示 keystore 密码与 key 密码；用 -dname 免交互式填证书主体。
# 这里让 keytool 自己交互两次（storepass / keypass），不把密码写进命令行参数
# （命令行会出现在 shell 历史与 /proc，不安全）。
keytool -genkeypair \
  -v \
  -keystore "$KEYSTORE_FILE" \
  -alias "$ALIAS" \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000 \
  -dname "CN=MBDSDR, OU=Mobile, O=MBDSDR, L=NA, ST=NA, C=CN"

chmod 600 "$KEYSTORE_FILE"

echo
echo "=============================================================="
echo " ✅ 生成成功: $KEYSTORE_FILE (权限 600)"
echo "=============================================================="
echo
echo "下一步：把下面这段填到 mobile/android/key.properties"
echo "（该文件已被 .gitignore 忽略，绝不入库；参考 key.properties.example）："
echo
echo "--- key.properties (开始) ---"
echo "storeFile=$KEYSTORE_FILE"
echo "storePassword=<你刚设的 keystore 密码>"
echo "keyAlias=$ALIAS"
echo "keyPassword=<你刚设的 key 密码>"
echo "--- key.properties (结束) ---"
echo
echo "提醒:"
echo "  * 用 'keytool -list -v -keystore $KEYSTORE_FILE' 可查看签名指纹(SHA-256)。"
echo "  * 正式上架前，把 SHA-256 指纹按各商店要求登记；本脚本不收集、不上传任何指纹。"
