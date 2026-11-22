#!/usr/bin/env python3
"""NoCali helper.

* strips the X1-series vibration calibration from Bambu G-code and hands the cleaned file
  to Bambu Connect,
* shows live printer status (read-only) and the camera.

Serves its web page on http://localhost:7311 only. Standard library only (Python 3.8+).
"""

import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import camera  # noqa: E402
import printer  # noqa: E402
from common import VERSION, data_dir, open_url  # noqa: E402
from gcode import process  # noqa: E402

PORT = int(os.environ.get("NOCALI_PORT", "7311"))
OUT_DIR = os.path.join(data_dir(), "ready")
KEEP_DAYS = 7
MAX_BYTES = 500 * 1024 * 1024

MONITOR = printer.PrinterMonitor()
CAMERA = camera.Camera(MONITOR.rtsp_url)


def cleanup_old():
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in os.listdir(OUT_DIR):
        p = os.path.join(OUT_DIR, f)
        if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
            os.remove(p)


def send_to_bambu_connect(path, name):
    url = "bambu-connect://import-file?path=%s&name=%s&version=1.0.0" % (quote(path, safe=""), quote(name, safe=""))
    open_url(url)
    return url


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "/status" in (args[0] if args else ""):
            return  # polled every 2 s; keep the log readable
        sys.stderr.write("NoCali: " + fmt % args + "\n")

    def _host_ok(self):
        # Block DNS rebinding: only answer to localhost names.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ("localhost", "127.0.0.1", "[::1]")

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    # GET ----------------------------------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"error": "forbidden"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/health":
            return self._json(200, {"ok": True, "version": VERSION})
        if u.path == "/status":
            snap = MONITOR.snapshot(q.get("lang", ["en"])[0][:2])
            snap["camera_ffmpeg"] = CAMERA.available()
            return self._json(200, snap)
        if u.path == "/config":
            c = MONITOR.cfg
            return self._json(200, {"ip": c.get("ip", ""), "serial": c.get("serial", ""),
                                    "name": c.get("name", ""), "hasCode": bool(c.get("code"))})
        if u.path == "/discover":
            try:
                return self._json(200, {"printers": printer.discover()})
            except OSError as e:
                return self._json(500, {"error": "Discovery failed: %s" % e})
        if u.path == "/download":
            name = os.path.basename(q.get("f", [""])[0])
            p = os.path.join(OUT_DIR, name)
            if not name or not os.path.isfile(p):
                return self._json(404, {"error": "not found"})
            with open(p, "rb") as f:
                return self._send(200, f.read(), "application/octet-stream",
                                  {"Content-Disposition": 'attachment; filename="%s"' % name.replace('"', "")})
        if u.path == "/camera.jpg":
            _, frame = CAMERA.wait_frame(-1 if CAMERA.frame is None else CAMERA.frame_id - 1, timeout=15)
            if frame is None:
                return self._json(503, {"error": CAMERA.error or "no camera image"})
            return self._send(200, frame, "image/jpeg")
        if u.path == "/camera.mjpg":
            return self._mjpeg()
        self._json(404, {"error": "not found"})

    def _mjpeg(self):
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        last = -1
        try:
            while True:
                last, frame = CAMERA.wait_frame(last, timeout=15)
                if frame is None:
                    if CAMERA.error:
                        break
                    continue
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame))
                self.wfile.write(frame + b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    # POST ---------------------------------------------------------------------------------
    def do_POST(self):
        # Non-simple content types force a CORS preflight, which is never answered,
        # so other websites cannot post here.
        u = urlparse(self.path)
        ctype = self.headers.get("Content-Type")
        if not self._host_ok() or ctype not in ("application/octet-stream", "application/json"):
            return self._json(403, {"error": "forbidden"})
        if u.path == "/config" and ctype == "application/json":
            return self._save_config()
        if u.path != "/process" or ctype != "application/octet-stream":
            return self._json(404, {"error": "not found"})
        q = parse_qs(u.query)
        filename = os.path.basename(q.get("name", [""])[0])
        send = q.get("send", ["0"])[0] == "1"
        fast = q.get("fast", ["1"])[0] == "1"
        length = int(self.headers.get("Content-Length") or 0)
        if not filename or length <= 0 or length > MAX_BYTES:
            return self._json(400, {"error": "missing or too large file"})
        data = self.rfile.read(length)
        try:
            out, out_name, display, removed = process(filename, data, fast)
        except Exception as e:
            return self._json(400, {"error": str(e)})
        os.makedirs(OUT_DIR, exist_ok=True)
        cleanup_old()
        out_path = os.path.join(OUT_DIR, out_name)
        with open(out_path, "wb") as f:
            f.write(out)
        sent, send_error = False, None
        if send:
            try:
                send_to_bambu_connect(out_path, display)
                sent = True
            except Exception:
                send_error = "Could not open Bambu Connect. Is it installed?"
        self._json(200, {"outName": out_name, "path": out_path, "sent": sent, "sendError": send_error,
                         "removed": [{"where": w, "code": c} for w, c in removed]})

    def _save_config(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(min(length, 10000)) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad request"})
        if body.get("clear"):
            printer.save_config({})
            CAMERA.stop()
            MONITOR.start()
            return self._json(200, {"ok": True})
        cfg = dict(MONITOR.cfg)
        for k in ("ip", "serial", "name"):
            if k in body:
                cfg[k] = str(body[k]).strip()
        if str(body.get("code", "")).strip():  # empty = keep the stored code
            cfg["code"] = str(body["code"]).strip()
        if (not re.fullmatch(r"[0-9A-Za-z.\-]+", cfg.get("ip", ""))
                or not re.fullmatch(r"[0-9A-Za-z]+", cfg.get("serial", ""))):
            return self._json(400, {"error": "Please enter the printer's IP address and serial number."})
        if not cfg.get("code"):
            return self._json(400, {"error": "Please enter the LAN access code."})
        printer.save_config(cfg)
        CAMERA.stop()
        MONITOR.start()
        return self._json(200, {"ok": True})


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    MONITOR.start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    print("NoCali %s running on http://localhost:%d" % (VERSION, PORT), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
