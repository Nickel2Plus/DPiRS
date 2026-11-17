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

"""Forward verified D-PRS packets to APRS-IS as an i-Gate (background thread)."""
import queue
import re
import select
import socket
import threading
import time

from i18n import tr

VERSION = "0.1.0"
DEFAULT_SERVER = ("asia.aprs2.net", 14580)
CALL_RE = re.compile(r"^[A-Z0-9]{3,7}(-[A-Z0-9]{1,2})?$")
BAD_PATH = ("NOGATE", "RFONLY", "TCPIP", "TCPXX")


def aprs_passcode(callsign):
    call = callsign.split("-")[0].upper()
    h = 0x73E2
    for i in range(0, len(call), 2):
        h ^= ord(call[i]) << 8
        if i + 1 < len(call):
            h ^= ord(call[i + 1])
    return h & 0x7FFF


class AprsGate:
    def __init__(self, call, send=False, server=DEFAULT_SERVER, passcode=None,
                 dupe_sec=30, min_interval=10, beacon=None, log=print):
        """beacon: None or (packet_string, interval_seconds)."""
        self.call = call.upper()
        self.send = send
        self.server = server
        self.passcode = passcode if passcode is not None else aprs_passcode(self.call)
        self.dupe_sec = dupe_sec
        self.min_interval = min_interval
        self.beacon = beacon
        self.log = log
        self.sock = None
        self.seen = {}          # (source, body) -> time
        self.last_src = {}      # source -> time
        self.last_tx = 0.0
        self.retry_at = 0.0
        self.backoff = 5
        self.q = queue.Queue(maxsize=100)
        self._stop = threading.Event()
        self._thread = None

    # ---- called from the receive loop ---------------------------------
    def convert(self, tnc2):
        """Return (packet, reason). packet is None when the packet is dropped."""
        try:
            s = tnc2.decode("ascii")
        except UnicodeDecodeError:
            return None, tr("非ASCII", "non-ASCII")
        head, sep, payload = s.partition(":")
        src, gt, path = head.partition(">")
        if not sep or not gt or not payload:
            return None, tr("形式不正", "malformed")
        src = src.upper()
        if not CALL_RE.match(src):
            return None, tr("送信元コール不正: %s", "invalid source call: %s") % src
        if payload.startswith("}"):
            return None, tr("サードパーティ形式", "third-party format")
        if any(b in path.upper() for b in BAD_PATH):
            return None, tr("NOGATE等のパス", "NOGATE/RFONLY path")
        now = time.monotonic()
        body = payload
        if len(payload) >= 8 and payload[0] in "/@" and payload[7] in "hz/":
            body = payload[8:]                  # ignore the time stamp
        key = (src, body)
        if now - self.seen.get(key, float("-inf")) < self.dupe_sec:
            return None, tr("重複", "duplicate")
        if now - self.last_src.get(src, float("-inf")) < self.min_interval:
            return None, tr("レート制限", "rate limited")
        self.seen[key] = now
        self.last_src[src] = now
        for k in [k for k, t in self.seen.items() if now - t > 300]:
            del self.seen[k]
        for k in [k for k, t in self.last_src.items() if now - t > 300]:
            del self.last_src[k]
        return "%s>%s,qAR,%s:%s" % (src, path, self.call, payload), ""

    def handle(self, tnc2):
        pkt, why = self.convert(tnc2)
        if pkt is None:
            self.log(tr("  [gate] 破棄: %s", "  [gate] dropped: %s") % why)
            return
        if not self.send:
            self.log("  [gate] DRY-RUN: " + pkt)
            return
        try:
            self.q.put_nowait(pkt)
        except queue.Full:
            self.log(tr("  [gate] 送信キューが満杯のため破棄", "  [gate] queue full, dropped"))

    # ---- background thread ------------------------------------------
    def start(self):
        if self.send and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="aprs-is", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(3)
        self._close()

    def _run(self):
        next_beacon = time.monotonic() + 5 if self.beacon else None
        while not self._stop.is_set():
            try:
                pkt = self.q.get(timeout=1.0)
            except queue.Empty:
                pkt = None
            items = [pkt] if pkt else []
            now = time.monotonic()
            if self.beacon and now >= next_beacon:
                items.append(self.beacon[0])
                next_beacon = now + self.beacon[1]
            for p in items:
                self._send(p)
            self._maintain()

    def _send(self, pkt):
        now = time.monotonic()
        if self.sock is None:
            if now < self.retry_at:
                self.log(tr("  [gate] 接続待ちのため破棄: ", "  [gate] offline, dropped: ") + pkt)
                return
            try:
                self._connect()
                self.backoff = 5
            except (OSError, RuntimeError) as e:
                self.log(tr("  [gate] 接続失敗: %s", "  [gate] connect failed: %s") % e)
                self._close()
                self.retry_at = time.monotonic() + self.backoff
                self.backoff = min(self.backoff * 2, 60)
                return
        try:
            self.sock.sendall((pkt + "\r\n").encode("ascii"))
            self.last_tx = time.monotonic()
            self.log(tr("  [gate] 送信: ", "  [gate] sent: ") + pkt)
        except OSError as e:
            self.log(tr("  [gate] 送信失敗: %s", "  [gate] send failed: %s") % e)
            self._close()
            self.retry_at = time.monotonic() + 2

    def _connect(self):
        s = socket.create_connection(self.server, timeout=10)
        s.settimeout(10)
        login = "user %s pass %s vers DPiRS %s\r\n" % (self.call, self.passcode, VERSION)
        s.sendall(login.encode("ascii"))
        buf, t0 = b"", time.monotonic()
        while b"logresp" not in buf and time.monotonic() - t0 < 10:
            chunk = s.recv(512)
            if not chunk:
                break
            buf += chunk
        resp = buf.decode("ascii", "replace")
        self.log("  [gate] APRS-IS: " + resp.strip().replace("\r\n", " | "))
        if "logresp" not in resp or "unverified" in resp:
            s.close()
            raise RuntimeError(tr("ログインが検証されませんでした（コールサイン/パスコードを確認）",
                                  "login was not verified (check callsign/passcode)"))
        self.sock = s
        self.last_tx = time.monotonic()

    def _maintain(self):
        if not self.sock:
            return
        try:
            r, _, _ = select.select([self.sock], [], [], 0)
            if r and not self.sock.recv(4096):
                self.log(tr("  [gate] サーバーが切断しました", "  [gate] server closed the connection"))
                self._close()
                return
            if time.monotonic() - self.last_tx > 20:
                self.sock.sendall(b"# DPiRS keepalive\r\n")
                self.last_tx = time.monotonic()
        except BlockingIOError:
            pass
        except OSError:
            self._close()

    def _close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
