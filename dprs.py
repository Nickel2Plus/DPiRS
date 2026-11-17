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

"""D-STAR slow data decoding and D-PRS line handling (no I/O, easy to test)."""

SCRAMBLER = (0x70, 0x4F, 0x93)
SYNC = bytes((0x55, 0x2D, 0x16))
NULL_ELEMENT = bytes((0x66,)) * 6

SLOW_GPS = 0x30
SLOW_TEXT = 0x40
SLOW_HEADER = 0x50

# Some radios (ID-52) put fixed data in the first super frame, which makes
# a few elements of the D-PRS line go missing. We try to rebuild the missing
# part (always the "CALL-SSID>TOCALL,DSTAR" section) and accept it ONLY if the
# CRC in the D-PRS line matches.
TOCALLS = (b"API52", b"API51", b"API31", b"API510", b"API880", b"API92",
           b"API80", b"API910", b"API705", b"API710", b"API30", b"API50")
SSIDS = [b""] + [b"-" + str(n).encode() for n in range(16)] + \
        [b"-" + bytes([c]) for c in range(65, 91)]


def crc16_x25(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def classify(line):
    """Return (kind, ok). kind: 'dprs' | 'nmea' | 'other'."""
    if line.startswith(b"$$CRC") and len(line) > 10 and line[9:10] == b",":
        try:
            want = int(line[5:9], 16)
        except ValueError:
            return "dprs", False
        return "dprs", crc16_x25(line[10:] + b"\r") == want
    if line.startswith(b"$") and b"*" in line:
        body, _, cs = line[1:].partition(b"*")
        x = 0
        for ch in body:
            x ^= ch
        return "nmea", cs[:2].upper() == b"%02X" % x
    return "other", False


def fill_gap(line, my, sfx):
    """Repair a D-PRS line containing \\x00 holes. Return None if impossible."""
    if not line.startswith(b"$$CRC") or line[9:10] != b",":
        return None
    try:
        want = int(line[5:9], 16)
    except ValueError:
        return None
    body = line[10:]
    i = body.find(b"\x00")
    j = body.rfind(b"\x00") + 1
    if i < 0 or body[i:j].count(b"\x00") != j - i:
        return None                      # holes must be one contiguous run
    prefix, suffix, gap_len = body[:i], body[j:], j - i
    call = my[:7].strip().encode("ascii", "ignore")
    tocalls = set(TOCALLS)
    s = sfx.strip()
    if len(s) >= 2:
        tocalls.add(b"API" + s[-2:].encode("ascii", "ignore"))
    for ssid in SSIDS:
        for to in tocalls:
            head = call + ssid + b">" + to + b",DSTAR"
            if len(head) != len(prefix) + gap_len or not head.startswith(prefix):
                continue
            cand = head + suffix
            if crc16_x25(cand + b"\r") == want:
                return line[:10] + cand
    return None


class SlowData:
    """Collects the slow data of one D-STAR transmission.

    on_line(line, repaired) is called for every complete GPS line.
    """

    def __init__(self, on_line, log=None):
        self.on_line = on_line
        self.log = log or (lambda msg: None)
        self.my = ""
        self.sfx = ""
        self.start()

    def start(self, my="", sfx=""):
        self.my, self.sfx = my, sfx
        self.half = None
        self.synced = False
        self.gps = bytearray()
        self.gaps = 0

    def lost(self):
        self.half = None

    def flush_rest(self):
        if self.gps:
            self.log("incomplete GPS data: %r" % bytes(self.gps).replace(b"\x00", b"?"))
            self.gps = bytearray()

    def add(self, frame):
        """frame: the 12 byte voice frame (9 byte AMBE + 3 byte slow data)."""
        raw = bytes(frame[9:12])
        if raw == SYNC:
            self.half = None
            self.synced = True
            return
        if not self.synced:
            return
        sd = bytes(b ^ s for b, s in zip(raw, SCRAMBLER))
        if self.half is None:
            self.half = sd
            return
        element = self.half + sd
        self.half = None
        self.element(element)

    def element(self, e):
        if e == NULL_ELEMENT:
            return
        t, n = e[0] & 0xF0, e[0] & 0x0F
        if t == SLOW_GPS:
            data = e[1:1 + min(n, 5)]
            if not self.gps and not data.startswith(b"$"):
                return                  # never start in the middle of a line
            self.gps += data
            self._take_lines()
        elif t in (SLOW_TEXT, SLOW_HEADER):
            return                      # message text / header copy: not needed
        else:                           # unknown element in the middle of GPS data
            if self.gps:
                if self.gaps < 4:
                    self.gps += b"\x00" * 5
                    self.gaps += 1
                else:
                    self.gps = bytearray()
                    self.gaps = 0
            self.log("element %s" % e.hex(" "))

    def _take_lines(self):
        while True:
            idx = next((i for i, c in enumerate(self.gps) if c in (0x0D, 0x0A)), -1)
            if idx < 0:
                return
            line = bytes(self.gps[:idx])
            del self.gps[:idx + 1]
            self.gaps = 0
            if line:
                self._finish(line)

    def _finish(self, line):
        repaired = False
        if b"\x00" in line:
            fixed = fill_gap(line, self.my, self.sfx)
            if fixed is None:
                self.log("GPS line with gaps could not be repaired (dropped)")
                return
            line, repaired = fixed, True
        self.on_line(line, repaired)
