"""Tests the MQTT client and report parsing against a simulated printer (TLS + MQTT)."""

import json
import os
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
os.environ.setdefault("XDG_DATA_HOME", tempfile.mkdtemp())
import printer  # noqa: E402

REPORT = {"print": {
    "command": "push_status", "gcode_state": "RUNNING", "mc_percent": 42, "mc_remaining_time": 30,
    "layer_num": 10, "total_layer_num": 100, "subtask_name": "Benchy", "stg_cur": 3,
    "nozzle_temper": 219.5, "nozzle_target_temper": 220, "bed_temper": 55, "bed_target_temper": 55,
    "device": {"ctc": {"info": {"temp": 34}}}, "spd_lvl": 3, "spd_mag": 124,
    "cooling_fan_speed": "15", "big_fan1_speed": "0", "big_fan2_speed": "10", "heatbreak_fan_speed": "15",
    "wifi_signal": "-50dBm", "nozzle_diameter": "0.4", "lights_report": [{"node": "chamber_light", "mode": "on"}],
    "ipcam": {"rtsp_url": "rtsps://10.0.0.5:322/streaming/live/1"},
    "hms": [{"attr": 0x07032200, "code": 0x00020021}],
    "ams": {"tray_now": "1", "ams": [{"id": "0", "humidity": "4", "humidity_raw": "21", "temp": "25.0", "tray": [
        {"id": "0", "tray_type": "PLA", "tray_color": "FF0000FF", "remain": 80},
        {"id": "1", "tray_type": "PETG", "tray_color": "FFFFFFFF", "remain": -1},
        {"id": "2"},
        {"id": "3", "tray_type": "ABS", "tray_color": "000000FF", "remain": 10, "cols": ["000000FF", "FFFFFFFF"]}]}]},
    "vt_tray": {"id": "254", "tray_type": "TPU", "tray_color": "161616FF"},
}}


def _str(s):
    b = s.encode()
    return struct.pack("!H", len(b)) + b


def have_openssl():
    return shutil.which("openssl") is not None


@unittest.skipUnless(have_openssl(), "openssl needed to create a test certificate")
class FakePrinterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        key, crt = os.path.join(cls.tmp, "k.pem"), os.path.join(cls.tmp, "c.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt,
                        "-days", "1", "-subj", "/CN=fake"], check=True, capture_output=True)
        cls.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.ctx.load_cert_chain(crt, key)
        cls.srv = socket.socket()
        cls.srv.bind(("127.0.0.1", 0))
        cls.srv.listen()
        cls.port = cls.srv.getsockname()[1]
        threading.Thread(target=cls.serve, daemon=True).start()
        printer.CONFIG_PATH = os.path.join(cls.tmp, "printer.json")

    @classmethod
    def serve(cls):
        while True:
            c, _ = cls.srv.accept()
            threading.Thread(target=cls.handle, args=(c,), daemon=True).start()

    @classmethod
    def handle(cls, raw):
        c = cls.ctx.wrap_socket(raw, server_side=True)
        ok = b"goodcode" in c.recv(4096)
        c.sendall(b"\x20\x02\x00" + (b"\x00" if ok else b"\x05"))
        if not ok:
            c.close()
            return
        c.recv(4096)
        c.sendall(b"\x90\x03\x00\x01\x00")
        body = _str("device/SER/report") + json.dumps(REPORT).encode()
        n, rl = len(body), b""
        while True:
            byte, n = n % 128, n // 128
            rl += bytes([byte | (0x80 if n else 0)])
            if not n:
                break
        try:
            while True:
                c.sendall(b"\x30" + rl + body)
                time.sleep(0.3)
        except OSError:
            pass

    def monitor(self, code):
        printer.save_config({"ip": "127.0.0.1", "port": self.port, "serial": "SER", "code": code, "name": "Fake"})
        m = printer.PrinterMonitor()
        m.start()
        for _ in range(30):
            time.sleep(0.2)
            s = m.snapshot()
            if s["age"] is not None or s["connection"] == "error":
                break
        m.stop()
        return m, s

    def test_status(self):
        m, s = self.monitor("goodcode")
        self.assertEqual(s["connection"], "connected")
        self.assertEqual((s["state"], s["percent"], s["layer"], s["total_layers"]), ("RUNNING", 42, 10, 100))
        self.assertEqual(s["stage"], "Vibration compensation")
        self.assertEqual((s["chamber"], s["speed"], s["speed_pct"]), (34, "sport", 124))
        self.assertEqual(s["fans"], {"part": 100, "aux": 0, "chamber": 67, "heatbreak": 100})
        self.assertEqual((s["wifi"], s["light"], s["camera"]), ("-50dBm", "on", True))
        self.assertEqual(s["hms"][0]["code"], "0703_2200_0002_0021")
        self.assertEqual(s["hms"][0]["level"], 2)
        u = s["ams"]["units"][0]
        self.assertEqual((u["label"], u["humidity"], u["temp"]), ("AMS A", 21, 25.0))
        self.assertEqual([t.get("type") for t in u["trays"]], ["PLA", "PETG", None, "ABS"])
        self.assertEqual([t["active"] for t in u["trays"]], [False, True, False, False])
        self.assertEqual((u["trays"][0]["remain"], u["trays"][1]["remain"]), (80, None))
        self.assertEqual(u["trays"][3]["colors"], ["#000000", "#FFFFFF"])
        self.assertEqual(s["ams"]["external"]["type"], "TPU")
        self.assertEqual(m.rtsp_url(), "rtsps://bblp:goodcode@10.0.0.5:322/streaming/live/1")

    def test_wrong_code(self):
        _, s = self.monitor("badcode1")
        self.assertEqual(s["connection"], "error")
        self.assertIn("access code", s["error"])


if __name__ == "__main__":
    unittest.main()
