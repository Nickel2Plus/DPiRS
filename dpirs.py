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

"""DPiRS - receive-only D-STAR D-PRS i-Gate for MMDVM_HS (ADF7021) modems.

Anyone who transmits D-STAR DV (with D-PRS GPS data) on the configured
frequency is picked up, and the position is forwarded to APRS-IS.
The program never transmits: only setup/status commands are written to the modem.
"""
import argparse
import configparser
import os
import re
import signal
import sys
import time

import i18n
from i18n import tr
from aprs_gate import AprsGate, VERSION, DEFAULT_SERVER
from dprs import SlowData, classify
from modem import (Modem, CMD_GET_STATUS, CMD_DSTAR_HEADER, CMD_DSTAR_DATA,
                   CMD_DSTAR_LOST, CMD_DSTAR_EOT, CMD_ACK, CMD_NAK, parse_header)

EX_CONFIG = 78
EX_TEMPFAIL = 75      # modem silent: systemd shows TEMPFAIL and restarts us
HERE = os.path.dirname(os.path.abspath(__file__))
IGATE_CALL_RE = re.compile(r"^[A-Z0-9]{3,7}(-([1-9]|1[0-5]))?$")


class ConfigError(Exception):
    pass


def make_log():
    tty = sys.stdout.isatty()

    def log(msg):
        if tty:
            msg = time.strftime("%H:%M:%S ") + msg
        print(msg, flush=True)
    return log


# ---------------------------------------------------------------- config ---
class Config:
    def __init__(self, path):
        cp = configparser.ConfigParser(inline_comment_prefixes=None, interpolation=None)
        if not cp.read(path, encoding="utf-8"):
            raise ConfigError(tr("設定ファイルを読めません: %s", "cannot read config file: %s") % path)
        self.cp = cp
        try:
            self.language = cp.get("general", "language", fallback="auto")
            self.callsign = cp.get("station", "callsign").strip().upper()
            self.serial_port = cp.get("modem", "serial_port", fallback="/dev/serial0").strip()
            self.baudrate = cp.getint("modem", "baudrate", fallback=115200)
            self.rx_frequency = cp.getint("modem", "rx_frequency", fallback=438010000)
            self.frequency_offset = cp.getint("modem", "frequency_offset", fallback=0)
            self.rx_invert = cp.getboolean("modem", "rx_invert", fallback=False)
            self.send = cp.getboolean("aprs_is", "send", fallback=False)
            self.server = (cp.get("aprs_is", "server", fallback=DEFAULT_SERVER[0]).strip(),
                           cp.getint("aprs_is", "server_port", fallback=DEFAULT_SERVER[1]))
            pc = cp.get("aprs_is", "passcode", fallback="").strip()
            self.passcode = int(pc) if pc else None
            self.dupe_seconds = cp.getint("filter", "dupe_seconds", fallback=30)
            self.min_interval = cp.getint("filter", "min_interval", fallback=10)
            self.beacon_enabled = cp.getboolean("beacon", "enabled", fallback=False)
            self.latitude = cp.getfloat("beacon", "latitude", fallback=0.0)
            self.longitude = cp.getfloat("beacon", "longitude", fallback=0.0)
            self.beacon_comment = cp.get("beacon", "comment", fallback="D-STAR DPRS RX-only iGate")
            self.beacon_minutes = cp.getint("beacon", "interval_minutes", fallback=30)
        except (configparser.Error, ValueError) as e:
            raise ConfigError(tr("設定ファイルの書式エラー: %s", "config file syntax error: %s") % e)

    def validate(self):
        if self.callsign.startswith("NOCALL"):
            raise ConfigError(tr("config.ini の [station] callsign を自局のコールサインに変更してください",
                                 "set [station] callsign in config.ini to your own callsign"))
        if not IGATE_CALL_RE.match(self.callsign):
            raise ConfigError(tr("コールサインの形式が不正です（例: JA1ABC-10）: %s",
                                 "invalid callsign format (e.g. JA1ABC-10): %s") % self.callsign)
        if not (400_000_000 <= self.rx_frequency <= 480_000_000):
            raise ConfigError(tr("rx_frequency は Hz 単位で指定してください（例: 438010000）",
                                 "rx_frequency must be in Hz (e.g. 438010000)"))
        if self.beacon_enabled and self.latitude == 0.0 and self.longitude == 0.0:
            raise ConfigError(tr("[beacon] latitude/longitude を設定してください",
                                 "set [beacon] latitude/longitude"))
        if self.beacon_enabled and self.beacon_minutes < 10:
            raise ConfigError(tr("[beacon] interval_minutes は10以上にしてください",
                                 "[beacon] interval_minutes must be 10 or more"))


def aprs_lat(d):
    total = round(abs(d) * 6000)
    return "%02d%02d.%02d%s" % (total // 6000, (total % 6000) // 100, total % 100,
                                "N" if d >= 0 else "S")


def aprs_lon(d):
    total = round(abs(d) * 6000)
    return "%03d%02d.%02d%s" % (total // 6000, (total % 6000) // 100, total % 100,
                                "E" if d >= 0 else "W")


def beacon_packet(cfg):
    # symbol: overlay 'R' + '&'  = receive-only i-Gate
    return "%s>APZDPR,TCPIP*:!%sR%s&%s" % (cfg.callsign, aprs_lat(cfg.latitude),
                                          aprs_lon(cfg.longitude), cfg.beacon_comment)


# ------------------------------------------------------------- main loop ---
def run_loop(modem, slow, log, verbose=False, watchdog_sec=15):
    last_poll, last_status = float("-inf"), time.monotonic()
    last_state = None
    try:
        while True:
            f = modem.read_frame(0.1)
            now = time.monotonic()
            if f:
                cmd, p = f[2], f[3:]
                if cmd == CMD_GET_STATUS:
                    last_status = now
                    st = bytes(p[0:3])
                    if verbose and st != last_state:
                        log("STATUS modes/state/flags = " + st.hex(" "))
                    last_state = st
                elif cmd == CMD_DSTAR_HEADER:
                    h = parse_header(p)
                    if h:
                        slow.start(h["my"], h["sfx"])
                        log("RX MY=%r/%r UR=%r RPT1=%r RPT2=%r" %
                            (h["my"], h["sfx"], h["ur"], h["rpt1"], h["rpt2"]))
                    else:
                        slow.start()
                elif cmd == CMD_DSTAR_DATA:
                    if len(p) >= 12:
                        slow.add(p[:12])
                elif cmd == CMD_DSTAR_LOST:
                    log("RX lost")
                    slow.lost()
                elif cmd == CMD_DSTAR_EOT:
                    slow.flush_rest()
                elif cmd in (CMD_ACK, CMD_NAK):
                    pass
                elif verbose:
                    log("other frame: " + f.hex(" "))
            if now - last_poll >= 0.25:
                modem.poll_status()
                last_poll = now
            if now - last_status > watchdog_sec:
                log(tr("モデムからの応答がありません。終了します（systemdが再起動します）",
                       "no response from the modem; exiting (systemd will restart)"))
                return EX_TEMPFAIL
    except KeyboardInterrupt:
        return 0


def _term(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    ap = argparse.ArgumentParser(description="receive-only D-STAR D-PRS i-Gate")
    ap.add_argument("-c", "--config", default=os.path.join(HERE, "config.ini"))
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="do not send to APRS-IS even if send = yes")
    ap.add_argument("--version", action="version", version="DPiRS " + VERSION)
    args = ap.parse_args(argv)

    log = make_log()
    try:
        cfg = Config(args.config)
        i18n.set_lang(cfg.language)
        cfg.validate()
    except ConfigError as e:
        print("ERROR:", e, file=sys.stderr)
        return EX_CONFIG

    send = cfg.send and not args.dry_run
    beacon = None
    if cfg.beacon_enabled:
        beacon = (beacon_packet(cfg), cfg.beacon_minutes * 60)
    gate = AprsGate(cfg.callsign, send=send, server=cfg.server, passcode=cfg.passcode,
                    dupe_sec=cfg.dupe_seconds, min_interval=cfg.min_interval,
                    beacon=beacon, log=log)

    def on_line(line, repaired):
        kind, ok = classify(line)
        text = line.decode("ascii", "replace")
        if repaired:
            log(tr("GPS 欠落を補完（CRCで検証済み）", "GPS gap repaired (verified by CRC)"))
        if kind == "dprs":
            log("D-PRS [%s]: %s" % ("CRC OK" if ok else "CRC NG", text))
            if ok:
                gate.handle(line[10:])
        elif kind == "nmea":
            log(tr("NMEA形式（未対応のため送出しません）: %s", "NMEA format (not supported, not gated): %s") % text)
        elif args.verbose:
            log("GPS line: " + text)

    slow = SlowData(on_line, log=(log if args.verbose else None))

    signal.signal(signal.SIGTERM, _term)
    try:
        modem = Modem.open(cfg.serial_port, cfg.baudrate)
    except ModuleNotFoundError:
        log(tr("pyserial が必要です: sudo apt install python3-serial",
               "pyserial is required: sudo apt install python3-serial"))
        return 1
    except Exception as e:
        log(tr("シリアルポートを開けません（%s）: %s\n  python3 check.py で原因を調べてください",
               "cannot open the serial port (%s): %s\n  run python3 check.py to find the cause")
            % (cfg.serial_port, e))
        return 1
    try:
        ok, what, f = modem.configure(cfg.rx_frequency + cfg.frequency_offset, cfg.rx_invert)
        if not ok:
            log(tr("モデムの設定に失敗しました（%s）: %s", "modem setup failed (%s): %s")
                % (what, f.hex(" ") if f else "no response"))
            return 1
        log(tr("受信待機中: %.4f MHz / D-STAR / APRS-IS送出: %s",
               "listening: %.4f MHz / D-STAR / APRS-IS forwarding: %s")
            % ((cfg.rx_frequency + cfg.frequency_offset) / 1e6,
               (tr("有効", "ON") if send else tr("無効（ドライラン）", "OFF (dry run)"))))
        gate.start()
        return run_loop(modem, slow, log, verbose=args.verbose)
    except OSError as e:
        log(tr("シリアル通信エラー: %s", "serial communication error: %s") % e)
        return 1
    finally:
        gate.stop()
        modem.close()


if __name__ == "__main__":
    sys.exit(main())
