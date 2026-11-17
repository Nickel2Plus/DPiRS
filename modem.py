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

"""Minimal MMDVM modem serial protocol (receive-only use).

Only GET_VERSION / GET_STATUS / SET_CONFIG / SET_FREQ may ever be written to the
modem. Every other command (including all transmit data) is refused in code,
so this program cannot transmit.
"""
import time

FRAME_START = 0xE0
CMD_GET_VERSION = 0x00
CMD_GET_STATUS = 0x01
CMD_SET_CONFIG = 0x02
CMD_SET_FREQ = 0x04
CMD_DSTAR_HEADER = 0x10
CMD_DSTAR_DATA = 0x11
CMD_DSTAR_LOST = 0x12
CMD_DSTAR_EOT = 0x13
CMD_ACK = 0x70
CMD_NAK = 0x7F

ALLOWED_COMMANDS = frozenset((CMD_GET_VERSION, CMD_GET_STATUS,
                              CMD_SET_CONFIG, CMD_SET_FREQ))


def build_set_config(rx_invert=False):
    b = bytearray(26)
    b[0], b[1], b[2] = FRAME_START, 26, CMD_SET_CONFIG
    b[3] = 0x80 | (0x01 if rx_invert else 0)   # simplex (+ RX invert)
    b[4] = 0x01                                  # D-STAR only
    b[5] = 10                                    # TX delay (unused)
    b[6] = 0                                     # MODE_IDLE
    b[7] = 128                                   # RX level
    b[8] = 128                                   # CW ID level (unused)
    b[11] = 128
    b[12] = 0                                    # D-STAR TX level = 0
    b[16] = 128                                  # TX DC offset
    b[17] = 128                                  # RX DC offset
    return bytes(b)


def build_set_freq(rx_hz, tx_hz=None, rf_level_pct=0, pocsag_hz=433000000):
    if tx_hz is None:
        tx_hz = rx_hz                            # never used: no transmit data is sent
    b = bytearray(17)
    b[0], b[1], b[2], b[3] = FRAME_START, 17, CMD_SET_FREQ, 0
    b[4:8] = rx_hz.to_bytes(4, "little")
    b[8:12] = tx_hz.to_bytes(4, "little")
    b[12] = int(rf_level_pct * 2.55 + 0.5)       # RF level 0
    b[13:17] = pocsag_hz.to_bytes(4, "little")
    return bytes(b)


def parse_header(p):
    """p: payload of a DSTAR_HEADER frame -> dict with the callsign fields."""
    def g(a, b):
        return bytes(p[a:b]).decode("ascii", "replace")
    if len(p) < 39:
        return None
    return {"rpt2": g(3, 11), "rpt1": g(11, 19), "ur": g(19, 27),
            "my": g(27, 35), "sfx": g(35, 39)}


class Modem:
    def __init__(self, ser):
        self.ser = ser

    @classmethod
    def open(cls, port, baudrate=115200):
        import serial                               # imported lazily
        ser = serial.Serial(port, baudrate, timeout=0.2)
        ser.reset_input_buffer()
        return cls(ser)

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def _write(self, data):
        if len(data) < 3 or data[0] != FRAME_START or data[2] not in ALLOWED_COMMANDS:
            raise RuntimeError("refusing to send command 0x%02x (receive-only)" % data[2])
        self.ser.write(data)

    def read_frame(self, timeout=1.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            b = self.ser.read(1)
            if not b or b[0] != FRAME_START:
                continue
            ln = self.ser.read(1)
            if not ln or ln[0] < 3:
                continue
            rest = self.ser.read(ln[0] - 2)
            return bytes([FRAME_START, ln[0]]) + rest
        return None

    def command(self, data, timeout=1.0):
        """Send a setup command and wait for ACK/NAK. Return (ok, frame)."""
        self._write(data)
        f = None
        for _ in range(20):
            f = self.read_frame(timeout)
            if f is None:
                break
            if f[2] == CMD_ACK:
                return True, f
            if f[2] == CMD_NAK:
                return False, f
        return False, f

    def configure(self, rx_hz, rx_invert=False):
        ok, f = self.command(build_set_config(rx_invert))
        if not ok:
            return False, "SET_CONFIG", f
        ok, f = self.command(build_set_freq(rx_hz))
        if not ok:
            return False, "SET_FREQ", f
        return True, "", None

    def poll_status(self):
        self._write(bytes([FRAME_START, 0x03, CMD_GET_STATUS]))

    def get_version(self, timeout=1.5):
        self._write(bytes([FRAME_START, 0x03, CMD_GET_VERSION]))
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            f = self.read_frame(0.5)
            if f and f[2] == CMD_GET_VERSION:
                return f
        return None
