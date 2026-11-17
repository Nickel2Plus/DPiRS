#!/usr/bin/env bash
# DPiRS installer for Raspberry Pi OS (Bookworm or later)
#
#   ./install.sh                         interactive
#   ./install.sh --gpio|--usb            how the modem is connected
#   ./install.sh --callsign JA1ABC-10    your callsign (+SSID) for APRS-IS
#   ./install.sh -y                      no questions (use defaults)
#
# What it does: installs python3-serial, frees the Raspberry Pi UART for the modem
# (GPIO hat only), adds you to the dialout group, creates config.ini and registers
# a systemd service. Boot files are backed up before they are changed.

# Copyright (C) 2026 JQ7EAH (GitHub: Nickel2Plus)
#
# This file is part of DPiRS.
#
# DPiRS is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# DPiRS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
# SPDX-License-Identifier: GPL-3.0-only

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST="${DPIRS_TEST:-}"            # test mode: no system changes (used by the tests)
UNIT_DIR="${DPIRS_UNIT_DIR:-/etc/systemd/system}"
case "${DPIRS_LANG:-${LANG:-}}" in ja*) JA=1 ;; *) JA=0 ;; esac

say() { if [ "$JA" = 1 ]; then printf '%s\n' "$1"; else printf '%s\n' "$2"; fi; }
die() { say "エラー: $1" "ERROR: $2" >&2; exit 1; }

as_root() { if [ -n "$TEST" ]; then "$@"; else sudo "$@"; fi; }
sys_cmd() { if [ -n "$TEST" ]; then echo "[test] $*"; else sudo "$@"; fi; }

MODE=""; CALL=""; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --gpio) MODE=gpio ;;
    --usb) MODE=usb ;;
    --callsign) CALL="${2:-}"; shift ;;
    -y|--yes) YES=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "不明なオプション: $1" "unknown option: $1" ;;
  esac
  shift
done

if [ "$(id -u)" -eq 0 ] && [ -z "$TEST" ]; then
  die "root ではなく、普通のユーザーで実行してください（必要な所で sudo を使います）" \
      "run this as a normal user, not root (sudo is used where needed)"
fi
RUN_USER="${USER:-$(id -un)}"

ask() {  # ask "ja" "en" default -> sets REPLY
  local def="$3"
  if [ "$YES" = 1 ]; then REPLY="$def"; return; fi
  if [ "$JA" = 1 ]; then read -r -p "$1 " REPLY; else read -r -p "$2 " REPLY; fi
  REPLY="${REPLY:-$def}"
}

# ---- 0. environment -------------------------------------------------------
MODEL="${DPIRS_MODEL:-}"
if [ -z "$MODEL" ] && [ -r /proc/device-tree/model ]; then MODEL="$(tr -d '\0' < /proc/device-tree/model)"; fi
CODENAME="$(. /etc/os-release 2>/dev/null && echo "${VERSION_CODENAME:-unknown}")"
say "== DPiRS インストーラ ==" "== DPiRS installer =="
say "機種: ${MODEL:-不明}   OS: $CODENAME" "Model: ${MODEL:-unknown}   OS: $CODENAME"
case "$CODENAME" in
  bookworm|trixie) ;;
  *) say "注意: Raspberry Pi OS Bookworm 以降でテストしています（このOS: $CODENAME）" \
         "Note: tested on Raspberry Pi OS Bookworm or later (this OS: $CODENAME)" ;;
esac

if [ -n "${DPIRS_BOOT_DIR:-}" ]; then BOOT="$DPIRS_BOOT_DIR"
elif [ -d /boot/firmware ]; then BOOT=/boot/firmware
else BOOT=/boot; fi

# ---- 1. how is the modem connected ------------------------------------------
if [ -z "$MODE" ]; then
  say "モデムの接続方法は？  1) GPIO（ホットスポットボードを載せる）  2) USB" \
      "How is the modem connected?  1) GPIO (hat on the header)  2) USB"
  ask "番号 [1]:" "number [1]:" 1
  case "$REPLY" in 2) MODE=usb ;; *) MODE=gpio ;; esac
fi

REBOOT=0
stamp="$(date +%Y%m%d-%H%M%S)"
backup() { as_root cp -p "$1" "$1.dpirs-$stamp.bak"; say "  バックアップ: $1.dpirs-$stamp.bak" "  backup: $1.dpirs-$stamp.bak"; }

# ---- 2. packages ------------------------------------------------------------------------
say "[1/5] 必要なパッケージ" "[1/5] Packages"
if ! python3 -c 'import serial' 2>/dev/null; then
  sys_cmd apt-get update
  sys_cmd apt-get install -y python3-serial
else
  say "  python3-serial は導入済み" "  python3-serial is already installed"
fi

# ---- 3. UART (GPIO only) ----------------------------------------------------------------------------
PORT=/dev/serial0
say "[2/5] シリアルポート" "[2/5] Serial port"
if [ "$MODE" = usb ]; then
  PORT=/dev/ttyACM0
  say "  USBモデム: UART設定は不要です（$PORT を使います）" "  USB modem: no UART setup needed (using $PORT)"
else
  OVERLAY=""
  case "$MODEL" in
    *"Raspberry Pi 3"*|*"Raspberry Pi Zero"*) OVERLAY=pi3-disable-bt ;;
    *"Raspberry Pi 4"*|*"Raspberry Pi 400"*|*"Compute Module 4"*) OVERLAY=disable-bt ;;
  esac
  if [ -z "$OVERLAY" ]; then
    say "  この機種（${MODEL:-不明}）のUART自動設定には未対応です。README の手動設定を参照してください。" \
        "  Automatic UART setup is not supported for this model (${MODEL:-unknown}). See the manual steps in the README."
  else
    CFG="$BOOT/config.txt"; CMD="$BOOT/cmdline.txt"
    [ -f "$CFG" ] && [ -f "$CMD" ] || die "$CFG / $CMD が見つかりません" "$CFG / $CMD not found"

    # config.txt: managed block (idempotent)
    tmp="$(mktemp)"
    sed '/^# >>> dpirs/,/^# <<< dpirs/d' "$CFG" > "$tmp"
    [ -z "$(tail -c1 "$tmp")" ] || echo >> "$tmp"
    block=""
    grep -Eq '^[[:space:]]*enable_uart=1' "$tmp" || block="${block}enable_uart=1"$'\n'
    grep -Eq '^[[:space:]]*dtoverlay=(pi3-)?disable-bt' "$tmp" || block="${block}dtoverlay=${OVERLAY}"$'\n'
    if [ -n "$block" ]; then
      { echo "# >>> dpirs (managed block) >>>"; echo "[all]"; printf '%s' "$block"; echo "# <<< dpirs <<<"; } >> "$tmp"
    fi
    if ! cmp -s "$tmp" "$CFG"; then
      backup "$CFG"; as_root cp "$tmp" "$CFG"; REBOOT=1
      say "  $CFG を更新しました（UART有効化・Bluetooth切り離し）" "  updated $CFG (UART enabled, Bluetooth moved off the UART)"
    else
      say "  $CFG は設定済み" "  $CFG already configured"
    fi
    rm -f "$tmp"

    # cmdline.txt: remove the serial console
    tmp="$(mktemp)"
    sed -E -e 's/ ?console=(serial0|ttyAMA0|ttyS0),[0-9]+//g' -e 's/^ +//' "$CMD" > "$tmp"
    if ! cmp -s "$tmp" "$CMD"; then
      backup "$CMD"; as_root cp "$tmp" "$CMD"; REBOOT=1
      say "  $CMD からシリアルコンソールの指定を削除しました" "  removed the serial console from $CMD"
    else
      say "  $CMD は設定済み" "  $CMD already configured"
    fi
    rm -f "$tmp"

    # services that would grab the port
    sys_cmd systemctl disable --now serial-getty@ttyAMA0.service 2>/dev/null || true
    sys_cmd systemctl mask serial-getty@ttyAMA0.service 2>/dev/null || true
    sys_cmd systemctl disable --now serial-getty@ttyS0.service 2>/dev/null || true
    sys_cmd systemctl disable --now hciuart.service 2>/dev/null || true
  fi
fi

if ! id -nG "$RUN_USER" | grep -qw dialout; then
  sys_cmd usermod -aG dialout "$RUN_USER"
  say "  $RUN_USER を dialout グループに追加しました" "  added $RUN_USER to the dialout group"
fi

# ---- 4. config.ini ---------------------------------------------------------------------------------------------
say "[3/5] 設定ファイル" "[3/5] Configuration"
CONF="$DIR/config.ini"
if [ ! -f "$CONF" ]; then
  cp "$DIR/config.example.ini" "$CONF"
  if [ -z "$CALL" ]; then
    ask "あなたのコールサイン+SSID（例: JA1ABC-10）。後で編集も可:" \
        "Your callsign + SSID (e.g. JA1ABC-10). You can edit it later:" ""
    CALL="$REPLY"
  fi
  CALL="$(echo "$CALL" | tr '[:lower:]' '[:upper:]')"
  if [[ "$CALL" =~ ^[A-Z0-9]{3,7}(-([1-9]|1[0-5]))?$ ]] && [[ "$CALL" != NOCALL* ]]; then
    sed -i -E "s|^callsign *=.*|callsign = $CALL|" "$CONF"
    say "  コールサイン: $CALL" "  callsign: $CALL"
  else
    say "  コールサインが未設定です。後で $CONF を編集してください。" \
        "  Callsign not set. Edit $CONF later."
    CALL=""
  fi
  sed -i -E "s|^serial_port *=.*|serial_port = $PORT|" "$CONF"
  say "  $CONF を作成しました（send = no: まずはドライランです）" \
      "  created $CONF (send = no: dry run first)"

  # optional: announce this i-Gate itself on the APRS map
  say "" ""
  say "このi-Gate局自身を、APRSの地図に表示しますか？" "Show this i-Gate station itself on the APRS map?"
  say "  ※ 表示すると、入力した座標が aprs.fi などで【公開】されます。" \
      "  NOTE: if you do, the coordinates you enter become PUBLIC (aprs.fi, etc.)."
  say "  ※ 自宅の場合は、正確な位置ではなく近くの別の地点（小数第2位まで ≒ 約1km）をおすすめします。" \
      "  NOTE: for a home station, an approximate point nearby (2 decimals = about 1 km) is recommended."
  say "  ※ 実際に表示されるのは send = yes にしたあとです（ドライラン中は送出されません）。" \
      "  NOTE: it is only actually sent once send = yes (nothing is sent in dry-run mode)."
  ask "表示する? [y/N]:" "show it? [y/N]:" n
  case "$REPLY" in
    y|Y|yes|YES)
      ask "緯度（十進の度、例 35.68、南緯は負）:" "latitude (decimal degrees, e.g. 35.68; south is negative):" ""
      LAT="$REPLY"
      ask "経度（十進の度、例 139.77、西経は負）:" "longitude (decimal degrees, e.g. 139.77; west is negative):" ""
      LON="$REPLY"
      if [[ "$LAT" =~ ^-?[0-9]+(\.[0-9]+)?$ ]] && [[ "$LON" =~ ^-?[0-9]+(\.[0-9]+)?$ ]] \
         && awk -v a="$LAT" -v o="$LON" 'BEGIN{exit !(a>=-90 && a<=90 && o>=-180 && o<=180 && !(a==0 && o==0))}'; then
        sed -i -E -e '/^\[beacon\]/,$ s|^enabled *=.*|enabled = yes|' \
                  -e "/^\[beacon\]/,\$ s|^latitude *=.*|latitude = $LAT|" \
                  -e "/^\[beacon\]/,\$ s|^longitude *=.*|longitude = $LON|" "$CONF"
        say "  自局位置を設定しました（$LAT, $LON）。変更は $CONF の [beacon] で行えます。" \
            "  station position set ($LAT, $LON). Change it in [beacon] of $CONF."
      else
        say "  座標が正しくないため、自局位置の表示は設定しませんでした（後で $CONF の [beacon] で設定できます）。" \
            "  invalid coordinates: the station position was not set (you can set it later in [beacon] of $CONF)."
      fi ;;
    *) say "  自局位置は表示しません（後で $CONF の [beacon] で設定できます）" \
           "  the station position will not be shown (you can enable it later in [beacon] of $CONF)" ;;
  esac
else
  say "  $CONF は既にあるので変更しません" "  $CONF already exists, left unchanged"
  CALL="$(sed -n -E 's/^callsign *= *([^ ]+).*/\1/p' "$CONF" | head -n1)"
  case "$CALL" in NOCALL*|"") CALL="" ;; esac
fi

# ---- 5. systemd service ------------------------------------------------------------------------------
say "[4/5] systemd サービス" "[4/5] systemd service"
tmp="$(mktemp)"
sed -e "s|@USER@|$RUN_USER|g" -e "s|@DIR@|$DIR|g" "$DIR/dpirs.service.in" > "$tmp"
as_root cp "$tmp" "$UNIT_DIR/dpirs.service"; rm -f "$tmp"
sys_cmd systemctl daemon-reload
sys_cmd systemctl enable dpirs.service
if [ "$REBOOT" = 0 ] && [ -n "$CALL" ]; then
  sys_cmd systemctl restart dpirs.service
  say "  サービスを起動しました" "  service started"
fi

# ---- 6. done --------------------------------------------------------------------------------------------------------
say "[5/5] 完了" "[5/5] Done"
echo
if [ "$REBOOT" = 1 ]; then
  say "★ UART設定を反映するため、再起動してください:  sudo reboot" \
      "★ Reboot to apply the UART settings:  sudo reboot"
fi
[ -n "$CALL" ] || say "★ $CONF の callsign を自局のコールサインに変更してください" \
                      "★ Set your callsign in $CONF"
say "次の手順:" "Next steps:"
say "  1. python3 $DIR/check.py              （診断）" "  1. python3 $DIR/check.py              (diagnose)"
say "  2. journalctl -u dpirs -f          （ログを見る。無線機で送信してテスト）" \
    "  2. journalctl -u dpirs -f          (watch the log; transmit with a radio to test)"
say "  3. 動作を確認できたら $CONF の send = yes にして、sudo systemctl restart dpirs" \
    "  3. when it works, set send = yes in $CONF and run: sudo systemctl restart dpirs"
say "     ※ send = yes にすると、受信した位置が aprs.fi などで公開されます" \
    "     NOTE: with send = yes, received positions become public (e.g. on aprs.fi)"
