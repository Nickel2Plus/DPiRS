"""Tests for the service-log analysis in check.py and the modem watchdog.

Run:  python3 -m unittest discover -s tests -v
"""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import check                     # noqa: E402
import dpirs                  # noqa: E402
import dprs                      # noqa: E402
import modem as modem_mod        # noqa: E402

START = "Nov 14 00:00:10 host systemd[1]: Started dpirs.service - dpirs."
LISTEN = "Nov 14 00:00:10 host python3[1211]: listening: 438.0100 MHz / D-STAR / APRS-IS forwarding: OFF (dry run)"
SILENT = "Nov 14 00:00:30 host python3[1211]: no response from the modem; exiting (systemd will restart)"
EXITED = "Nov 14 00:00:30 host systemd[1]: dpirs.service: Main process exited, code=exited, status=75/TEMPFAIL"
RESTART = "Nov 14 00:00:35 host systemd[1]: dpirs.service: Scheduled restart job, restart counter is at 1."
LISTEN2 = "Nov 14 00:00:36 host python3[1568]: listening: 438.0100 MHz / D-STAR / APRS-IS forwarding: OFF (dry run)"
RX = "Nov 14 00:05:47 host python3[1568]: RX MY='N0CALL A'/'ID52' UR='CQCQCQ  ' RPT1='DIRECT  ' RPT2='DIRECT  '"


class TestJournal(unittest.TestCase):
    def test_listening_without_reception(self):
        r = check.analyze_journal([START, LISTEN])
        self.assertEqual(r["state"], "listening")
        self.assertEqual(r["errors"], 0)
        self.assertIsNone(r["last_rx"])
        self.assertTrue(r["message"].startswith("listening:"))

    def test_recovered_after_modem_silence(self):
        r = check.analyze_journal([START, LISTEN, SILENT, EXITED, RESTART, LISTEN2, RX])
        self.assertEqual(r["state"], "listening")      # the newest marker is "listening"
        self.assertEqual(r["errors"], 1)
        self.assertEqual(r["last_rx"], "Nov 14 00:05:47 RX MY='N0CALL A'/'ID52' UR='CQCQCQ  ' "
                                       "RPT1='DIRECT  ' RPT2='DIRECT  '")

    def test_still_failing(self):
        r = check.analyze_journal([START, LISTEN, SILENT, EXITED])
        self.assertEqual(r["state"], "error")
        self.assertIn("no response from the modem", r["message"])

    def test_cannot_open_port(self):
        r = check.analyze_journal([
            START,
            "Nov 14 00:00:11 host python3[7]: cannot open the serial port (/dev/serial0): "
            "[Errno 2] No such file or directory",
            "Nov 14 00:00:11 host python3[7]:   run python3 check.py to find the cause",
        ])
        self.assertEqual(r["state"], "error")

    def test_japanese_messages(self):
        r = check.analyze_journal([
            "Nov 14 00:00:10 host python3[1]: 受信待機中: 438.0100 MHz / D-STAR / APRS-IS送出: 有効",
            "Nov 14 00:00:30 host python3[1]: モデムからの応答がありません。終了します（systemdが再起動します）",
        ])
        self.assertEqual(r["state"], "error")
        r = check.analyze_journal([
            "Nov 14 00:00:30 host python3[1]: モデムからの応答がありません。終了します（systemdが再起動します）",
            "Nov 14 00:00:36 host python3[2]: 受信待機中: 438.0100 MHz / D-STAR / APRS-IS送出: 有効",
        ])
        self.assertEqual(r["state"], "listening")
        self.assertEqual(r["errors"], 1)

    def test_unrelated_and_empty_input(self):
        self.assertEqual(check.analyze_journal([])["state"], "unknown")
        r = check.analyze_journal([START, "-- Boot 1234 --", "garbage"])
        self.assertEqual(r["state"], "unknown")


class SilentSerial:
    """A modem that never answers (read() times out and returns nothing)."""

    def read(self, n=1):
        return b""

    def write(self, data):
        pass

    def close(self):
        pass


class TestWatchdog(unittest.TestCase):
    def test_silent_modem_exits_with_tempfail(self):
        logs = []
        m = modem_mod.Modem(SilentSerial())
        slow = dprs.SlowData(lambda line, repaired: None)
        rc = dpirs.run_loop(m, slow, logs.append, watchdog_sec=0.3)
        self.assertEqual(rc, dpirs.EX_TEMPFAIL)
        self.assertEqual(rc, 75)
        self.assertTrue(any("modem" in l or "モデム" in l for l in logs))


class BufSerial:
    """Serial stand-in that returns prepared bytes, then nothing."""

    def __init__(self, data):
        self.buf = bytearray(data)
        self.written = []

    def read(self, n=1):
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def write(self, data):
        self.written.append(bytes(data))

    def close(self):
        pass


class TestVersion(unittest.TestCase):
    def test_get_version_reply_is_readable(self):
        # same layout as a real MMDVM_HS_Hat reply: E0 len 00 <protocol> <text> 00 <id digits>
        body = (b"\x01" + b"MMDVM_HS_Hat-v1.5.2 20201108 14.7456MHz ADF7021 FW by CA6JAU GitID #89daa20"
                + b"\x00" + b"0123456789")
        frame = bytes([0xE0, 3 + len(body), 0x00]) + body
        noise = b"\x13\x00\x7f"                   # stray bytes before the frame must be skipped
        ser = BufSerial(noise + frame)
        m = modem_mod.Modem(ser)
        f = m.get_version()
        self.assertEqual(f, frame)
        self.assertEqual(ser.written, [bytes([0xE0, 0x03, 0x00])])
        text = check.version_text(f)
        self.assertIn("MMDVM_HS_Hat-v1.5.2", text)
        self.assertIn("ADF7021", text)
        self.assertNotIn("  ", text)


class TestClock(unittest.TestCase):
    """The Pi has no RTC: NTP steps the wall clock shortly after boot."""

    def test_wall_clock_jump_does_not_trip_watchdog(self):
        # modem answers STATUS every time; the wall clock jumps forward mid-run
        status = bytes([0xE0, 0x06, 0x01, 0, 0, 0])

        class Ser(BufSerial):
            def read(self, n=1):
                if not self.buf:
                    self.buf += status
                return super().read(n)

        real = time.time
        state = {"calls": 0}

        def jumping():
            state["calls"] += 1
            return real() + (1_000_000 if state["calls"] > 20 else 0)

        logs = []
        m = modem_mod.Modem(Ser(b""))
        slow = dprs.SlowData(lambda line, repaired: None)
        with mock.patch("time.time", jumping):
            t0 = real()
            # run for about 0.5 s then stop through KeyboardInterrupt
            orig = m.read_frame

            def rf(timeout=1.0):
                if real() - t0 > 0.5:
                    raise KeyboardInterrupt
                return orig(timeout)
            m.read_frame = rf
            rc = dpirs.run_loop(m, slow, logs.append, watchdog_sec=15)
        self.assertEqual(rc, 0, logs)

    def test_early_uptime_is_not_a_duplicate(self):
        import aprs_gate
        g = aprs_gate.AprsGate("TEST1-10", send=False, log=lambda m: None)
        with mock.patch("time.monotonic", return_value=2.0), mock.patch("time.time", return_value=2.0):
            line = b"N0CALL-A>API52,DSTAR*:!3500.00N/13500.00E>test"
            pkt, why = g.convert(line)
        self.assertIsNotNone(pkt, why)


if __name__ == "__main__":
    unittest.main()
