#!/usr/bin/env python3
"""Bambu Studio / OrcaSlicer post-processing script.

Process settings > Others > Post-processing Scripts, e.g.
  macOS/Linux:  /usr/bin/python3 "/path/to/nocali_post.py";
  Windows:      "C:\\Path\\To\\python.exe" "C:\\Path\\To\\nocali_post.py";
The slicer appends the path of the temporary G-code file as the last argument.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gcode import patch_gcode  # noqa: E402


def main(argv):
    if len(argv) < 2 or not os.path.isfile(argv[-1]):
        sys.stderr.write("NoCali: G-code file not found\n")
        return 1
    path = argv[-1]
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    new_text, removed = patch_gcode(text)
    if removed:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
    print("NoCali: %d calibration command(s) disabled" % len(removed))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
