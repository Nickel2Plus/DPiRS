"""Tests with synthetic data only (no radio, no network access to the internet).

Run:  python3 -m unittest discover -s tests -v
"""
import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import dprs                      # noqa: E402
import modem as modem_mod        # noqa: E402
import dpirs                  # noqa: E402
from aprs_gate import AprsGate, aprs_passcode   # noqa: E402

SCR = dprs.SCRAMBLER


def scramble(e):
    return bytes(b ^ SCR[i % 3] for i, b in enumerate(e))


def mk_line(body):
    """body: b'CALL>TOCALL,DSTAR*:...' -> complete D-PRS line (without CR)."""
    crc = dprs.crc16_x25(body + b"\r")
    return b"$$CRC%04X," % crc + body


def elements_for(line, gps_type=0x30):
    data = line + b"\r"
    out = []
    for i in range(0, len(data), 5):
        c = data[i:i + 5]
        out.append(bytes([gps_type | len(c)]) + c + b"\x66" * (5 - len(c)))
    return out


def voice_frames(elements, total_sf=4):
    """Build 12 byte voice frames from slow data elements (10 per super frame)."""
    nul = b"\x66" * 6
    els = list(elements)
    frames = []
    for _ in range(total_sf):
        frames.append(b"\xAA" * 9 + dprs.SYNC)
        for _ in range(10):
            e = els.pop(0) if els else nul
            sd = scramble(e)
            frames.append(b"\xAA" * 9 + sd[:3])
            frames.append(b"\xAA" * 9 + sd[3:])
    return frames


def collect(frames, my="", sfx=""):
    got = []
    s = dprs.SlowData(lambda line, repaired: got.append((line, repaired)))
    s.start(my, sfx)
    for f in frames:
        s.add(f)
    return got


class TestDecode(unittest.TestCase):
    BODY = b"N0CALL-A>API52,DSTAR*:/123004h3500.00N/13500.00E[/A=000000"

    def test_clean_line(self):
        line = mk_line(self.BODY)
        got = collect(voice_frames(elements_for(line) * 2))
        self.assertEqual(got[0], (line, False))
        self.assertEqual(dprs.classify(line), ("dprs", True))

    def test_id52_style_gap_is_repaired_by_crc(self):
        line = mk_line(self.BODY)
        els = elements_for(line)
        # elements 3..5 are replaced by the radio's fixed data (types 0x90/0x10)
        els[3:6] = [bytes([0x98, 1, 2, 3, 4, 5]), bytes([0x98, 6, 7, 8, 9, 10]),
                    bytes([0x18, 11, 12, 13, 14, 15])]
        got = collect(voice_frames(els), my="N0CALL A", sfx="ID52")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0], (line, True))

    def test_gap_with_wrong_crc_is_dropped(self):
        line = mk_line(self.BODY)
        bad = line[:5] + b"0000" + line[9:]          # corrupt the CRC
        els = elements_for(bad)
        els[3:6] = [bytes([0x98] + [0] * 5)] * 3
        got = collect(voice_frames(els), my="N0CALL A", sfx="ID52")
        self.assertEqual(got, [])

    def test_no_start_in_middle_of_line(self):
        els = elements_for(mk_line(self.BODY))[2:]    # starts mid-line
        self.assertEqual(collect(voice_frames(els)), [])

    def test_ic7100_style_short_transmission(self):
        # IC-7100: no SSID in the D-PRS source, message text interleaved, line complete in ~0.7 s
        line = mk_line(b"N0CALL>API710,DSTAR*:!3500.00N/13500.00E>/IC7100M")
        els = elements_for(line)
        text = [bytes([0x40 | i]) + b"QSOxx"[:5] for i in range(4)]
        mixed = []
        for i in range(4):                     # text elements alternate with GPS ones
            mixed += [text[i], els.pop(0)]
        mixed += els
        got = collect(voice_frames(mixed, total_sf=2), my="N0CALL A", sfx="7100")
        self.assertEqual(got, [(line, False)])

    def test_nmea_checksum(self):
        s = b"GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W"
        x = 0
        for c in s:
            x ^= c
        self.assertEqual(dprs.classify(b"$" + s + b"*%02X" % x), ("nmea", True))
        self.assertEqual(dprs.classify(b"$" + s + b"*00"), ("nmea", False))


class TestGate(unittest.TestCase):
    def test_passcode(self):
        self.assertEqual(aprs_passcode("N0CALL"), 13023)     # well known value

    def make(self, **kw):
        return AprsGate("TEST1-10", log=lambda m: None, **kw)

    def test_convert_and_dedupe(self):
        g = self.make()
        pkt, _ = g.convert(b"N0CALL-A>API52,DSTAR*:/123004h3500.00N/13500.00E[/A=0")
        self.assertEqual(pkt, "N0CALL-A>API52,DSTAR*,qAR,TEST1-10:/123004h3500.00N/13500.00E[/A=0")
        # same position, only the time stamp differs -> duplicate
        pkt2, why = g.convert(b"N0CALL-A>API52,DSTAR*:/123014h3500.00N/13500.00E[/A=0")
        self.assertIsNone(pkt2)

    def test_no_time_format_keeps_position(self):
        g = self.make(min_interval=0)
        a, _ = g.convert(b"N0CALL>API31,DSTAR*:!3500.00N/13500.00E[/")
        b, _ = g.convert(b"N0CALL>API31,DSTAR*:!3510.00N/13500.00E[/")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)          # different latitude must not be a "duplicate"

    def test_rate_limit(self):
        g = self.make(min_interval=10)
        self.assertIsNotNone(g.convert(b"N0CALL>API31,DSTAR*:!3500.00N/13500.00E[/")[0])
        self.assertIsNone(g.convert(b"N0CALL>API31,DSTAR*:!3510.00N/13500.00E[/")[0])
        self.assertIsNotNone(g.convert(b"N1CALL>API31,DSTAR*:!3510.00N/13500.00E[/")[0])

    def test_rejects(self):
        g = self.make()
        self.assertIsNone(g.convert(b"N0CALL>API31,NOGATE:!3500.00N/13500.00E[/")[0])
        self.assertIsNone(g.convert(b"bad call>API31,DSTAR*:!3500.00N/13500.00E[/")[0])
        self.assertIsNone(g.convert(b"N0CALL>API31,DSTAR*:}X>Y:z")[0])
        self.assertIsNone(g.convert(b"no colon here")[0])

    def test_forward_to_fake_aprs_is(self):
        received = []
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def serve():
            c, _ = srv.accept()
            c.sendall(b"# fake aprsd\r\n")
            data = b""
            while b"\n" not in data:
                data += c.recv(256)
            received.append(data.decode().strip())
            c.sendall(b"# logresp TEST1-10 verified, server FAKE\r\n")
            c.settimeout(9)
            buf = b""
            try:
                while buf.count(b"\n") < 2:
                    d = c.recv(1024)
                    if not d:
                        break
                    buf += d
            except socket.timeout:
                pass
            received.extend(buf.decode().splitlines())
            c.close()

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        beacon = ("TEST1-10>APZDPR,TCPIP*:!3500.00NR13500.00E&test", 3600)
        g = self.make(send=True, server=("127.0.0.1", port), beacon=beacon)
        g.start()
        g.handle(b"N0CALL>API31,DSTAR*:!3500.00N/13500.00E[/")
        t.join(12)
        g.stop()
        srv.close()
        self.assertTrue(received[0].startswith("user TEST1-10 pass "), received)
        body = "\n".join(received[1:])
        self.assertIn("N0CALL>API31,DSTAR*,qAR,TEST1-10:!3500.00N/13500.00E[/", body)
        self.assertIn("TEST1-10>APZDPR,TCPIP*:!3500.00NR13500.00E&test", body)


class FakeSerial:
    """Serial port stand-in. Records everything written to the 'modem'."""

    def __init__(self, data):
        self.buf = bytearray(data)
        self.written = []

    def read(self, n=1):
        if not self.buf:
            raise KeyboardInterrupt      # ends run_loop
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def write(self, data):
        self.written.append(bytes(data))

    def close(self):
        pass


def frame(cmd, payload=b""):
    return bytes([0xE0, 3 + len(payload), cmd]) + payload


class TestEndToEnd(unittest.TestCase):
    def test_receive_only_pipeline(self):
        body = b"N0CALL>API31,DSTAR*:!3500.00N/13500.00E[/"
        line = mk_line(body)
        hdr = (b"\x00\x00\x00" + b"DIRECT  " + b"DIRECT  " + b"CQCQCQ  " + b"N0CALL  " + b"ID31"
               + b"\x00\x00")
        data = frame(0x70, b"\x02") + frame(0x70, b"\x04") + frame(0x10, hdr)
        for f in voice_frames(elements_for(line)):
            data += frame(0x11, f)
        data += frame(0x13)

        fake = FakeSerial(data)
        m = modem_mod.Modem(fake)
        ok, what, _ = m.configure(438010000)
        self.assertTrue(ok, what)

        forwarded = []
        logs = []

        class G:
            def handle(self, tnc2):
                forwarded.append(tnc2)

        def on_line(l, repaired):
            kind, good = dprs.classify(l)
            if kind == "dprs" and good:
                G().handle(l[10:])

        slow = dprs.SlowData(on_line)
        rc = dpirs.run_loop(m, slow, logs.append)
        self.assertEqual(rc, 0)
        self.assertEqual(forwarded, [body])

        # receive-only guarantee: only SET_CONFIG, SET_FREQ and GET_STATUS were written
        cmds = {w[2] for w in fake.written}
        self.assertTrue(cmds <= {0x01, 0x02, 0x04}, cmds)
        cfg = [w for w in fake.written if w[2] == 0x02][0]
        self.assertEqual(cfg[12], 0)                 # D-STAR TX level 0
        freq = [w for w in fake.written if w[2] == 0x04][0]
        self.assertEqual(int.from_bytes(freq[4:8], "little"), 438010000)
        self.assertEqual(freq[12], 0)                # RF level 0

    def test_modem_refuses_transmit_commands(self):
        m = modem_mod.Modem(FakeSerial(b""))
        with self.assertRaises(RuntimeError):
            m._write(bytes([0xE0, 0x0F, 0x11] + [0] * 12))     # DSTAR_DATA (TX)

    def test_beacon_format(self):
        self.assertEqual(dpirs.aprs_lat(35.0), "3500.00N")
        self.assertEqual(dpirs.aprs_lon(-135.5), "13530.00W")
        self.assertEqual(dpirs.aprs_lat(-0.0166666), "0001.00S")


if __name__ == "__main__":
    unittest.main()
