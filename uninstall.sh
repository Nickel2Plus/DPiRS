#!/usr/bin/env bash
# Remove the dpirs service and the boot settings added by install.sh.
# config.ini and the program files are left in place.
set -euo pipefail

TEST="${DPIRS_TEST:-}"
UNIT_DIR="${DPIRS_UNIT_DIR:-/etc/systemd/system}"
case "${DPIRS_LANG:-${LANG:-}}" in ja*) JA=1 ;; *) JA=0 ;; esac
say() { if [ "$JA" = 1 ]; then printf '%s\n' "$1"; else printf '%s\n' "$2"; fi; }
as_root() { if [ -n "$TEST" ]; then "$@"; else sudo "$@"; fi; }
sys_cmd() { if [ -n "$TEST" ]; then echo "[test] $*"; else sudo "$@"; fi; }

if [ -n "${DPIRS_BOOT_DIR:-}" ]; then BOOT="$DPIRS_BOOT_DIR"
elif [ -d /boot/firmware ]; then BOOT=/boot/firmware
else BOOT=/boot; fi

sys_cmd systemctl disable --now dpirs.service 2>/dev/null || true
as_root rm -f "$UNIT_DIR/dpirs.service"
sys_cmd systemctl daemon-reload

CFG="$BOOT/config.txt"
if [ -f "$CFG" ] && grep -q '^# >>> dpirs' "$CFG"; then
  tmp="$(mktemp)"
  sed '/^# >>> dpirs/,/^# <<< dpirs/d' "$CFG" > "$tmp"
  as_root cp -p "$CFG" "$CFG.dpirs-uninstall.bak"
  as_root cp "$tmp" "$CFG"; rm -f "$tmp"
  say "$CFG から dpirs のブロックを削除しました" "removed the dpirs block from $CFG"
fi
sys_cmd systemctl unmask serial-getty@ttyAMA0.service 2>/dev/null || true

say "完了。UART設定を元に戻すには再起動してください（cmdline.txt は install.sh が作ったバックアップ *.dpirs-*.bak から戻せます）。" \
    "Done. Reboot to revert the UART settings (cmdline.txt can be restored from the *.dpirs-*.bak backup made by install.sh)."
