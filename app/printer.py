"""Read-only live status for a Bambu Lab printer on the local network.

Talks MQTT 3.1.1 over TLS to the printer (port 8883, user "bblp", password = LAN access code)
with a tiny built-in client, so no extra packages are needed. It only subscribes to the
printer's report topic and asks for a full status push; it never sends print or motion commands.
"""

import json
import os
import socket
import ssl
import struct
import threading
import time

import hms
from common import data_dir

CONFIG_PATH = os.path.join(data_dir(), "printer.json")

# Stage names as used by Bambu Studio (DeviceManager.cpp, get_stage_string).
STAGES = [
    "Printing", "Auto bed leveling", "Heatbed preheating", "Vibration compensation", "Changing filament",
    "M400 pause", "Paused (filament ran out)", "Heating nozzle", "Calibrating dynamic flow",
    "Scanning bed surface", "Inspecting first layer", "Identifying build plate type",
    "Calibrating Micro Lidar", "Homing toolhead", "Cleaning nozzle tip", "Checking extruder temperature",
    "Paused by the user", "Pause (front cover fall off)", "Calibrating the micro lidar",
    "Calibrating flow ratio", "Pause (nozzle temperature malfunction)", "Pause (heatbed temperature malfunction)",
    "Filament unloading", "Pause (step loss)", "Filament loading", "Motor noise cancellation",
    "Pause (AMS offline)", "Pause (low speed of the heatbreak fan)", "Pause (chamber temperature control problem)",
    "Cooling chamber", "Pause (Gcode inserted by user)", "Motor noise showoff", "Pause (nozzle clumping)",
    "Pause (cutter error)", "Pause (first layer error)", "Pause (nozzle clog)", "Measuring motion precision",
    "Enhancing motion precision", "Measure motion accuracy", "Nozzle offset calibration",
    "High temperature auto bed leveling", "Auto Check: Quick Release Lever", "Auto Check: Door and Upper Cover",
    "Laser Calibration", "Auto Check: Platform", "Confirming BirdsEye Camera location",
    "Calibrating BirdsEye Camera", "Auto bed leveling - phase 1", "Auto bed leveling - phase 2",
    "Heating chamber", "Adjusting heatbed temperature", "Printing calibration lines", "Auto Check: Material",
    "Live View Camera Calibration", "Waiting for heatbed to reach target temperature",
    "Auto Check: Material Position", "Cutting Module Offset Calibration", "Measuring Surface",
    "Thermal preconditioning for first layer", "Homing Blade Holder", "Calibrating Camera Offset",
    "Calibrating Blade Holder Position", "Hotend Pick and Place Test", "Waiting for chamber temperature to equalize",
    "Preparing Hotend", "Calibrating nozzle clumping detection", "Purifying the chamber air",
    "Measuring Rotary Attachment", "Moving toolhead above purge chute", "Cooling down the nozzle",
    "Moving toolhead to bed center", "Active Arc Fitting", "Hotend Type Detection",
    "Build plate alignment detection", "Heatbed surface foreign object detection",
    "Heatbed underside foreign object detection", "Pre-extrusion before printing", "Preparing AMS",
]
SPEEDS = {1: "silent", 2: "standard", 3: "sport", 4: "ludicrous"}


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def discover(seconds=8):
    """Find Bambu printers on the LAN via SSDP. Returns [{ip, serial, name, model, mode}]."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    # Use a private port and ask the printers directly: Bambu Studio often holds UDP 2021
    # exclusively, but printers answer M-SEARCH to the sender's address.
    s.bind(("", 0))
    s.settimeout(0.5)
    probe = (b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1990\r\nMAN: "ssdp:discover"\r\n'
             b'MX: 3\r\nST: urn:bambulab-com:device:3dprinter:1\r\n\r\n')
    start = time.time()
    found, last_probe = {}, 0
    try:
        while time.time() < start + seconds:
            if time.time() - last_probe > 2:
                for dst in (("239.255.255.250", 1990), ("255.255.255.255", 1990), ("255.255.255.255", 2021)):
                    try:
                        s.sendto(probe, dst)
                    except OSError:
                        pass
                last_probe = time.time()
            if found and time.time() > start + 3:
                break
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                continue
            h = {}
            for line in data.decode(errors="ignore").split("\r\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    h[k.strip().lower()] = v.strip()
            serial = h.get("usn")
            if serial and "devmodel.bambu.com" in h:
                found[serial] = {"ip": h.get("location") or addr[0], "serial": serial,
                                 "name": h.get("devname.bambu.com", ""), "model": h.get("devmodel.bambu.com", ""),
                                 "mode": h.get("devconnect.bambu.com", "")}
    finally:
        s.close()
    return list(found.values())


# --- report helpers ------------------------------------------------------------------------

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fan_pct(v):
    """Fan speeds come as 0..15 steps."""
    n = _num(v)
    return None if n is None else int(round(n / 15.0 * 100))


def _color(hexstr):
    if not hexstr or len(hexstr) < 6:
        return None
    return "#" + hexstr[:6].upper()


def _tray(t, active):
    if not t or not t.get("tray_type"):
        return {"empty": True, "active": active}
    remain = t.get("remain")
    return {
        "empty": False, "active": active,
        "type": t.get("tray_type"), "name": t.get("tray_sub_brands") or "",
        "color": _color(t.get("tray_color")),
        "colors": [_color(c) for c in (t.get("cols") or []) if _color(c)],
        "remain": remain if isinstance(remain, int) and remain >= 0 and remain <= 100 and remain > 0 else None,
        "nozzle_min": _num(t.get("nozzle_temp_min")), "nozzle_max": _num(t.get("nozzle_temp_max")),
    }


def parse_ams(p):
    block = p.get("ams") if isinstance(p.get("ams"), dict) else {}
    try:
        tray_now = int(block.get("tray_now", 255))
    except (TypeError, ValueError):
        tray_now = 255
    units = []
    for u in block.get("ams") or []:
        try:
            uid = int(u.get("id", 0))
        except (TypeError, ValueError):
            continue
        trays = []
        by_id = {str(t.get("id")): t for t in u.get("tray") or []}
        for slot in range(4):
            trays.append(_tray(by_id.get(str(slot)), tray_now == uid * 4 + slot))
        hum_pct = _num(u.get("humidity_raw"))
        units.append({"id": uid, "label": "AMS " + "ABCD"[uid % 4] if uid < 4 else "AMS HT %d" % (uid - 127),
                      "humidity": int(hum_pct) if hum_pct is not None else None,
                      "humidity_level": _num(u.get("humidity")), "temp": _num(u.get("temp")), "trays": trays})
    ext = p.get("vt_tray") if isinstance(p.get("vt_tray"), dict) else None
    return {"units": units, "external": _tray(ext, tray_now == 254) if ext else None,
            "active": tray_now if tray_now < 254 else (254 if tray_now == 254 else None)}


# --- minimal MQTT 3.1.1 --------------------------------------------------------------------

def _str(s):
    b = s.encode()
    return struct.pack("!H", len(b)) + b


def _packet(ptype, body):
    n, rl = len(body), b""
    while True:
        byte, n = n % 128, n // 128
        rl += bytes([byte | (0x80 if n else 0)])
        if not n:
            break
    return bytes([ptype]) + rl + body


class _Conn:
    def __init__(self, sock):
        self.sock, self.buf = sock, b""

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("printer closed the connection")
            self.buf += chunk

    def read_packet(self):
        """Returns (type_byte, body) or raises socket.timeout if nothing is waiting."""
        if not self.buf:
            chunk = self.sock.recv(65536)  # may raise socket.timeout
            if not chunk:
                raise ConnectionError("printer closed the connection")
            self.buf += chunk
        mult, length, i = 1, 0, 1
        while True:
            self._need(i + 1)
            b = self.buf[i]
            length += (b & 0x7F) * mult
            mult *= 128
            i += 1
            if not b & 0x80:
                break
        self._need(i + length)
        ptype, body = self.buf[0], self.buf[i:i + length]
        self.buf = self.buf[i + length:]
        return ptype, body


class PrinterMonitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {}          # merged "print" object from the reports
        self.conn_status = "not configured"
        self.error = None
        self.last_update = None
        self._stop = threading.Event()
        self.cfg = load_config()

    # public -------------------------------------------------------------------------------
    def start(self):
        self.stop()
        self._stop = threading.Event()
        self.cfg = load_config()
        with self.lock:
            self.state, self.last_update, self.error = {}, None, None
            self.conn_status = "connecting" if self.configured() else "not configured"
        if self.configured():
            threading.Thread(target=self._run, args=(self._stop, dict(self.cfg)), daemon=True).start()

    def stop(self):
        self._stop.set()

    def configured(self):
        return all(self.cfg.get(k) for k in ("ip", "serial", "code"))

    def rtsp_url(self):
        """Camera URL (X1 series) including credentials, or None."""
        with self.lock:
            url = (self.state.get("ipcam") or {}).get("rtsp_url") if isinstance(self.state.get("ipcam"), dict) else None
        if not url or not url.startswith("rtsps://") or not self.configured():
            return None
        return url.replace("rtsps://", "rtsps://bblp:%s@" % self.cfg["code"], 1)

    def snapshot(self, lang="en"):
        with self.lock:
            p = dict(self.state)
            status, err, upd = self.conn_status, self.error, self.last_update
        stage = p.get("stg_cur")
        stage = stage if isinstance(stage, int) else None
        device = p.get("device") if isinstance(p.get("device"), dict) else {}
        chamber = p.get("chamber_temper")
        if chamber is None:
            chamber = ((device.get("ctc") or {}).get("info") or {}).get("temp")
        lights = {l.get("node"): l.get("mode") for l in p.get("lights_report") or [] if isinstance(l, dict)}
        ipcam = p.get("ipcam") if isinstance(p.get("ipcam"), dict) else {}
        return {
            "connection": status, "error": err,
            "age": round(time.time() - upd, 1) if upd else None,
            "printer": {"ip": self.cfg.get("ip"), "serial": self.cfg.get("serial"), "name": self.cfg.get("name")},
            "state": p.get("gcode_state"),
            "job": p.get("subtask_name") or p.get("gcode_file"),
            "percent": p.get("mc_percent"),
            "remaining_min": p.get("mc_remaining_time"),
            "layer": p.get("layer_num"), "total_layers": p.get("total_layer_num"),
            "stage_id": stage,
            "stage": (STAGES[stage] if stage is not None and 0 <= stage < len(STAGES) else
                      ("Stage %d" % stage if stage not in (None, -1, 255) else None)),
            "nozzle": _num(p.get("nozzle_temper")), "nozzle_target": _num(p.get("nozzle_target_temper")),
            "bed": _num(p.get("bed_temper")), "bed_target": _num(p.get("bed_target_temper")),
            "chamber": _num(chamber),
            "speed": SPEEDS.get(p.get("spd_lvl")), "speed_pct": p.get("spd_mag"),
            "fans": {"part": _fan_pct(p.get("cooling_fan_speed")), "aux": _fan_pct(p.get("big_fan1_speed")),
                     "chamber": _fan_pct(p.get("big_fan2_speed")), "heatbreak": _fan_pct(p.get("heatbreak_fan_speed"))},
            "wifi": p.get("wifi_signal"),
            "nozzle_diameter": p.get("nozzle_diameter"), "nozzle_type": p.get("nozzle_type"),
            "light": lights.get("chamber_light"),
            "camera": bool(ipcam.get("rtsp_url")) and ipcam.get("rtsp_url") != "disable",
            "ams": parse_ams(p),
            "hms": hms.describe_hms(p.get("hms"), lang),
            "print_error": hms.describe_error(p.get("print_error") or p.get("mc_print_error_code"), lang),
        }

    # internals ----------------------------------------------------------------------------
    def _set(self, status=None, error=None):
        with self.lock:
            if status is not None:
                self.conn_status = status
            self.error = error

    def _run(self, stop, cfg):
        backoff = 3
        while not stop.is_set():
            try:
                self._session(stop, cfg)
                backoff = 3
            except PermissionError as e:
                self._set("error", str(e))
                backoff = 60  # wrong access code: don't hammer the printer
            except Exception as e:
                self._set("reconnecting", "%s: %s" % (type(e).__name__, e))
            stop.wait(backoff)
            backoff = min(backoff * 2, 60)

    def _session(self, stop, cfg):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # the printer uses a self-signed certificate
        raw = socket.create_connection((cfg["ip"], cfg.get("port", 8883)), timeout=10)
        sock = ctx.wrap_socket(raw)
        try:
            conn = _Conn(sock)
            client_id = "nocali-%d" % (int(time.time()) % 100000)
            body = (_str("MQTT") + bytes([4, 0xC2]) + struct.pack("!H", 60) + _str(client_id)
                    + _str("bblp") + _str(cfg["code"]))
            sock.sendall(_packet(0x10, body))
            ptype, body = conn.read_packet()
            if ptype >> 4 != 2 or len(body) < 2:
                raise ConnectionError("unexpected reply from printer")
            if body[1] in (4, 5):
                raise PermissionError("Printer rejected the access code (check the LAN access code on the printer screen).")
            if body[1] != 0:
                raise ConnectionError("printer refused connection (code %d)" % body[1])

            serial = cfg["serial"]
            sock.sendall(_packet(0x82, struct.pack("!H", 1) + _str("device/%s/report" % serial) + b"\x00"))
            push = json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}).encode()
            sock.sendall(_packet(0x30, _str("device/%s/request" % serial) + push))
            self._set("connected", None)

            sock.settimeout(1.0)
            last_ping = time.time()
            while not stop.is_set():
                if time.time() - last_ping > 30:
                    sock.sendall(b"\xC0\x00")
                    last_ping = time.time()
                try:
                    ptype, body = conn.read_packet()
                except socket.timeout:
                    continue
                kind = ptype >> 4
                if kind == 3:  # PUBLISH
                    tlen = struct.unpack("!H", body[:2])[0]
                    off = 2 + tlen + (2 if (ptype >> 1) & 3 else 0)
                    self._on_message(body[off:])
                elif kind == 9 and body[-1:] == b"\x80":
                    raise ConnectionError("printer refused the status subscription (serial number wrong?)")
        finally:
            try:
                sock.sendall(b"\xE0\x00")
            except Exception:
                pass
            sock.close()

    def _on_message(self, payload):
        try:
            msg = json.loads(payload)
        except ValueError:
            return
        p = msg.get("print")
        if not isinstance(p, dict) or p.get("command") not in (None, "push_status"):
            return
        with self.lock:
            for k, v in p.items():
                if k == "ams" and isinstance(v, dict) and isinstance(self.state.get("ams"), dict):
                    merged = dict(self.state["ams"])  # P1/A1 send partial AMS updates
                    merged.update(v)
                    self.state["ams"] = merged
                else:
                    self.state[k] = v
            self.last_update = time.time()
            self.conn_status = "connected"
            self.error = None
