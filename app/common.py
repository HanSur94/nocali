"""Platform helpers shared by the NoCali modules."""

import os
import subprocess
import sys

VERSION = "1.0.2"


def data_dir():
    """Per-user folder for settings, cleaned files and caches (override with NOCALI_DATA)."""
    if os.environ.get("NOCALI_DATA"):
        base = os.environ["NOCALI_DATA"]
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/NoCali")
    elif os.name == "nt":
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "NoCali")
    else:
        base = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "nocali")
    os.makedirs(base, exist_ok=True)
    return base


def open_url(url):
    """Open a URL (e.g. bambu-connect://...) with the system handler. Raises on failure."""
    if sys.platform == "darwin":
        subprocess.run(["open", url], check=True, capture_output=True)
    elif os.name == "nt":
        os.startfile(url)  # noqa: raises OSError when no handler is registered
    else:
        subprocess.run(["xdg-open", url], check=True, capture_output=True)
