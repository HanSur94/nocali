"""Run NoCali against a simulated printer (for screenshots and UI work).

    python3 tests/demo.py        then open http://localhost:7399
"""

import json
import os
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["NOCALI_DATA"] = tempfile.mkdtemp()
os.environ.setdefault("NOCALI_PORT", "7399")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "app"))

import test_printer  # noqa: E402

test_printer.REPORT["print"].update({"stg_cur": 0, "mc_percent": 63, "mc_remaining_time": 47, "layer_num": 126,
                                     "total_layer_num": 200, "subtask_name": "Benchy", "spd_lvl": 2, "spd_mag": 100,
                                     "hms": [], "ipcam": {}})
test_printer.FakePrinterTest.setUpClass()
fp = test_printer.FakePrinterTest

import printer  # noqa: E402
import server  # noqa: E402

printer.CONFIG_PATH = os.path.join(os.environ["NOCALI_DATA"], "printer.json")
printer.save_config({"ip": "127.0.0.1", "port": fp.port, "serial": "SER", "code": "goodcode", "name": "X1 Carbon"})
server.MONITOR.start()
threading.Thread(target=server.main, daemon=True).start()
print("Demo on http://localhost:%s" % os.environ["NOCALI_PORT"])
threading.Event().wait()
