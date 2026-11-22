"""Live camera for X1-series printers.

The X1 streams RTSPS (H.264) on port 322. Browsers can't play that, so ffmpeg converts it
to a low-rate MJPEG stream. ffmpeg only runs while someone is watching and stops after
IDLE_STOP seconds without viewers.
"""

import os
import shutil
import subprocess
import threading
import time

FPS = 4
WIDTH = 1280
IDLE_STOP = 20
EXTRA_PATHS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "C:\\ffmpeg\\bin"]


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for d in EXTRA_PATHS:
        for name in ("ffmpeg", "ffmpeg.exe"):
            p = os.path.join(d, name)
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
    return None


class Camera:
    def __init__(self, url_provider):
        self.url_provider = url_provider  # callable -> rtsps URL with credentials, or None
        self.cond = threading.Condition()
        self.frame = None
        self.frame_id = 0
        self.last_viewer = 0
        self.error = None
        self.proc = None
        self.thread = None

    def available(self):
        return find_ffmpeg() is not None

    def touch(self):
        """Called by every viewer; starts ffmpeg if needed."""
        with self.cond:
            self.last_viewer = time.time()
            if self.thread is None or not self.thread.is_alive():
                self.error = None
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()

    def wait_frame(self, last_id, timeout=10):
        self.touch()
        with self.cond:
            self.cond.wait_for(lambda: (self.frame is not None and self.frame_id != last_id) or self.error, timeout)
            return self.frame_id, self.frame

    def stop(self):
        with self.cond:
            self.last_viewer = 0
        p = self.proc
        if p and p.poll() is None:
            p.kill()

    def _run(self):
        exe = find_ffmpeg()
        url = self.url_provider()
        if not exe or not url:
            with self.cond:
                self.error = "ffmpeg not installed" if not exe else "camera not available"
                self.cond.notify_all()
            return
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-tls_verify", "0",
               "-i", url, "-an", "-vf", "fps=%d,scale='min(%d,iw)':-2" % (FPS, WIDTH),
               "-q:v", "6", "-f", "mjpeg", "pipe:1"]
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        except OSError as e:
            with self.cond:
                self.error = str(e)
                self.cond.notify_all()
            return
        watchdog = threading.Thread(target=self._watchdog, args=(self.proc,), daemon=True)
        watchdog.start()
        buf = b""
        try:
            while True:
                chunk = self.proc.stdout.read1(65536) if hasattr(self.proc.stdout, "read1") else self.proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                while True:  # split the MJPEG byte stream into JPEG frames
                    start = buf.find(b"\xff\xd8")
                    end = buf.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                    if start < 0 or end < 0:
                        if start > 0:
                            buf = buf[start:]
                        break
                    with self.cond:
                        self.frame = buf[start:end + 2]
                        self.frame_id += 1
                        self.cond.notify_all()
                    buf = buf[end + 2:]
        finally:
            rc = self.proc.wait()
            err = self.proc.stderr.read().decode(errors="ignore").strip() if self.proc.stderr else ""
            code = (self.url_provider() or "").split("bblp:")[-1].split("@")[0]
            if code:
                err = err.replace(code, "***")
            with self.cond:
                if rc not in (0, -9, 1) or (err and self.frame is None):
                    self.error = err.splitlines()[-1] if err else "camera stream ended"
                self.cond.notify_all()

    def _watchdog(self, proc):
        while proc.poll() is None:
            time.sleep(2)
            with self.cond:
                idle = time.time() - self.last_viewer > IDLE_STOP
            if idle:
                proc.kill()
                break
