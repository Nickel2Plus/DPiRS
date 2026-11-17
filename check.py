#!/usr/bin/env python3
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

"""Diagnose the setup: config, serial port, modem / service state, network."""
import argparse
import os
import re
import socket
import subprocess
import sys

import i18n
from i18n import tr

HERE = os.path.dirname(os.path.abspath(__file__))
problems = 0

# Markers of the log lines written by dpirs.py (both languages)
LISTEN_MARKS = ("受信待機中", "listening:")
ERROR_MARKS = ("モデムからの応答がありません", "no response from the modem",
               "シリアルポートを開けません", "cannot open the serial port",
               "モデムの設定に失敗", "modem setup failed",
               "シリアル通信エラー", "serial communication error",
               "pyserial が必要", "pyserial is required", "ERROR:")
# journalctl -o short:  "Nov 14 00:01:47 host python3[1568]: message"
JOURNAL_RE = re.compile(r"^(\w{3}\s+\d+\s+[\d:]{8})\s+\S+\s+([^\[\s:]+)\[\d+\]:\s?(.*)$")


def analyze_journal(lines):
    """Summarise `journalctl -o short` lines of the dpirs service.

    Returns a dict:
      state    'listening' | 'error' | 'unknown'  (decided by the newest marker line)
      message  the log message that decided the state
      errors   number of error lines seen (recovered or not)
      last_rx  '<time> <message>' of the newest reception, or None
    """
    state, message, errors, last_rx = "unknown", "", 0, None
    for line in lines:
        m = JOURNAL_RE.match(line)
        if not m or m.group(2) == "systemd":
            continue
        when, text = m.group(1), m.group(3).strip()
        if any(k in text for k in LISTEN_MARKS):
            state, message = "listening", text
        elif any(k in text for k in ERROR_MARKS):
            state, message = "error", text
            errors += 1
        elif text.startswith("RX MY="):
            last_rx = "%s %s" % (when, text)
    return {"state": state, "message": message, "errors": errors, "last_rx": last_rx}


def ok(msg):
    print("  [ OK ] " + msg)


def warn(msg, hint=None):
    print("  [WARN] " + msg)
    if hint:
        print("         -> " + hint)


def ng(msg, hint=None):
    global problems
    problems += 1
    print("  [ NG ] " + msg)
    if hint:
        print("         -> " + hint)


def sh(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def version_text(frame):
    """Readable text of a GET_VERSION reply (E0 len 00 <protocol> <firmware text...>)."""
    return " ".join(bytes(c if 32 <= c < 127 else 32 for c in frame[4:]).decode("ascii").split())


def service_state():
    return sh("systemctl", "is-active", "dpirs.service")


def journal_lines(n=300):
    out = sh("journalctl", "-u", "dpirs.service", "-b", "-n", str(n), "--no-pager", "-o", "short")
    return out.splitlines()


def check_service_log():
    """The service owns the serial port, so judge from its log. Return True if verified."""
    lines = journal_lines()
    res = analyze_journal(lines) if lines else None
    if res is None:
        warn(tr("サービスのログを読めませんでした", "could not read the service log"),
             tr("journalctl -u dpirs -n 30 で確認するか、sudo systemctl stop dpirs のあとで check.py を再実行してください",
                "run journalctl -u dpirs -n 30, or stop the service (sudo systemctl stop dpirs) and run check.py again"))
        return False
    if res["state"] == "listening":
        ok(tr("サービスは受信待機中です: %s", "the service is listening: %s") % res["message"])
        if res["errors"]:
            warn(tr("直近のログに異常終了の記録が %d 件あります（現在は回復しています）",
                    "%d error(s) in the recent log (recovered since)") % res["errors"],
                 tr("起動直後だけなら問題ありません。繰り返す場合は journalctl -u dpirs -n 50 で内容を確認してください",
                    "harmless if it only happened at startup; if it repeats, check journalctl -u dpirs -n 50"))
        if res["last_rx"]:
            ok(tr("最後の受信: %s", "last reception: %s") % res["last_rx"])
        else:
            warn(tr("まだ何も受信していません", "nothing has been received yet"),
                 tr("無線機で送信してテストしてください（DVモード・設定した周波数）",
                    "transmit with a radio to test (DV mode, the configured frequency)"))
        return True
    if res["state"] == "error":
        ng(tr("サービスのログに異常があります: %s", "the service log shows a problem: %s") % res["message"],
           tr("journalctl -u dpirs -n 30 で詳細を確認してください", "see journalctl -u dpirs -n 30 for details"))
        return False
    warn(tr("直近のログに受信待機の記録がありません", "no 'listening' entry in the recent log"),
         tr("サービスを再起動して確認: sudo systemctl restart dpirs",
            "restart the service and check again: sudo systemctl restart dpirs"))
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", default=os.path.join(HERE, "config.ini"))
    args = ap.parse_args()

    # 1. config ---------------------------------------------------------
    print(tr("1. 設定ファイル", "1. Configuration"))
    port, baud, server = "/dev/serial0", 115200, ("asia.aprs2.net", 14580)
    try:
        import dpirs
        cfg = dpirs.Config(args.config)
        i18n.set_lang(cfg.language)
        port, baud, server = cfg.serial_port, cfg.baudrate, cfg.server
        try:
            cfg.validate()
            ok(tr("config.ini は正常です（コールサイン %s）", "config.ini is valid (callsign %s)") % cfg.callsign)
        except dpirs.ConfigError as e:
            ng(str(e), tr("config.ini を編集してください", "edit config.ini"))
        if not cfg.send:
            warn(tr("[aprs_is] send = no（ドライランです。APRS-ISには送出されません）",
                    "[aprs_is] send = no (dry run: nothing is sent to APRS-IS)"),
                 tr("送出するには send = yes にして、サービスを再起動します",
                    "set send = yes and restart the service to forward"))
    except Exception as e:
        ng(tr("設定ファイルを読めません: %s", "cannot read config: %s") % e,
           tr("cp config.example.ini config.ini", "cp config.example.ini config.ini"))

    # 2. serial port ------------------------------------------------------
    print(tr("2. シリアルポート (%s)", "2. Serial port (%s)") % port)
    if not os.path.exists(port):
        ng(tr("%s がありません", "%s does not exist") % port,
           tr("GPIO接続なら install.sh を実行して再起動してください。USB接続なら serial_port = /dev/ttyACM0 にします",
              "for a GPIO hat run install.sh and reboot; for a USB modem use serial_port = /dev/ttyACM0"))
    else:
        real = os.path.realpath(port)
        ok(tr("%s が存在します (→ %s)", "%s exists (-> %s)") % (port, real))
        if os.access(port, os.R_OK | os.W_OK):
            ok(tr("読み書きできます", "read/write access OK"))
        else:
            ng(tr("読み書きの権限がありません", "no read/write permission"),
               tr("sudo usermod -aG dialout $USER を実行して、再ログインしてください",
                  "run: sudo usermod -aG dialout $USER  and log in again"))
        if "ttyS0" in real:
            ng(tr("mini UART (ttyS0) に割り当てられています。MMDVMには使えません",
                  "mapped to the mini UART (ttyS0), which is not usable for MMDVM"),
               tr("install.sh を実行して再起動してください（Bluetoothを切り離します）",
                  "run install.sh and reboot (it moves Bluetooth off the UART)"))

    # 3. things that block the port -------------------------------------
    print(tr("3. ポートを使っている可能性のあるもの", "3. Things that may be using the port"))
    cmdline = ""
    for p in ("/boot/firmware/cmdline.txt", "/boot/cmdline.txt"):
        if os.path.exists(p):
            cmdline = open(p).read()
            break
    if cmdline:
        if "console=serial0" in cmdline or "console=ttyAMA0" in cmdline or "console=ttyS0" in cmdline:
            ng(tr("cmdline.txt にシリアルコンソールの指定が残っています",
                  "cmdline.txt still has a serial console"),
               tr("install.sh を実行して再起動してください", "run install.sh and reboot"))
        else:
            ok(tr("cmdline.txt にシリアルコンソールの指定はありません", "no serial console in cmdline.txt"))
    for unit in ("serial-getty@ttyAMA0", "serial-getty@ttyS0", "mmdvmhost", "ircddbgateway", "dstargateway"):
        if sh("systemctl", "is-active", unit + ".service") == "active":
            ng(tr("%s が動作中です", "%s is running") % unit,
               tr("sudo systemctl disable --now %s", "sudo systemctl disable --now %s") % unit)
    state = service_state()
    running = state in ("active", "activating", "reloading")
    if state == "active":
        ok(tr("dpirs サービスは動作中です", "the dpirs service is running"))
    elif state == "activating":
        warn(tr("dpirs サービスは再起動待ちです", "the dpirs service is waiting to restart"),
             tr("journalctl -u dpirs -n 30 で原因を確認してください", "see journalctl -u dpirs -n 30"))
    elif state == "failed":
        res = analyze_journal(journal_lines())
        reason = (" — " + res["message"]) if res["state"] == "error" else ""
        ng(tr("dpirs サービスが失敗状態です", "the dpirs service has failed") + reason,
           tr("journalctl -u dpirs -n 30 で原因を確認してください", "see journalctl -u dpirs -n 30"))
    else:
        ok(tr("dpirs サービスは動いていません（モデムを直接テストできます）",
              "the dpirs service is not running (the modem can be tested directly)"))

    # 4. modem ------------------------------------------------------------
    print(tr("4. モデムの応答", "4. Modem response"))
    verified = False
    if running:
        verified = check_service_log()
    elif not os.path.exists(port):
        warn(tr("スキップ", "skipped"))
    else:
        try:
            from modem import Modem
            m = Modem.open(port, baud)
            f = m.get_version()
            m.close()
            if f is None:
                ng(tr("GET_VERSION に応答がありません", "no response to GET_VERSION"),
                   tr("配線・ボードの装着、UART設定（再起動したか）を確認してください",
                      "check the hat is seated, and that UART setup was applied (reboot)"))
            else:
                ok(tr("応答あり: %s", "response: %s") % version_text(f))
                verified = True
        except Exception as e:
            ng(tr("モデムと通信できません: %s", "cannot talk to the modem: %s") % e)

    # 5. network ----------------------------------------------------------
    print(tr("5. APRS-ISサーバーへの接続", "5. APRS-IS server"))
    try:
        socket.create_connection(server, timeout=5).close()
        ok(tr("%s:%d に接続できます", "can connect to %s:%d") % server)
    except OSError as e:
        ng(tr("%s:%d に接続できません: %s", "cannot connect to %s:%d: %s") % (server + (e,)),
           tr("インターネット接続を確認してください", "check the internet connection"))

    print()
    if problems:
        print(tr("問題が %d 件見つかりました。上の [ NG ] を確認してください。",
                 "%d problem(s) found. See the [ NG ] lines above.") % problems)
        return 1
    if verified:
        print(tr("問題は見つかりませんでした。", "No problems found."))
    else:
        print(tr("明らかな問題は見つかりませんでしたが、モデムの応答は確認できていません。",
                 "No obvious problems, but the modem response was not verified."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
