"""Remove the X1-series vibration (resonance) calibration from Bambu Lab G-code.

The start G-code runs the resonance sweep with M970 / M970.3 (shake an axis) and M974
(fit the result). These lines are commented out; M975 S1 (use stored compensation) stays.
"""

import hashlib
import io
import re
import zipfile

CALI_RE = re.compile(r"^(\s*)(M97[04](?:\.\d+)?)(?=\s|;|$)", re.I)
HDR_RE = re.compile(r"^\s*;\s*=+\s*(.*?)\s*=*\s*$")
MARK = "; [NoCali] "


def patch_gcode(text, remove_fast_check=True):
    """Comment out M970 / M970.3 / M974. Returns (new_text, [(line_no, code), ...])."""
    lines = text.split("\n")
    removed, section = [], ""
    for i, line in enumerate(lines):
        m = HDR_RE.match(line)
        if m and m.group(1):
            section = m.group(1).lower()
        if not CALI_RE.match(line):
            continue
        if not remove_fast_check and "fast check" in section:
            continue
        removed.append((i + 1, line.strip()))
        lines[i] = CALI_RE.sub(lambda mm: mm.group(1) + MARK + mm.group(2), line, count=1)
    return "\n".join(lines), removed


def patch_3mf(data, remove_fast_check=True):
    """Patch every Metadata/plate_N.gcode in a .gcode.3mf and fix its .md5 file."""
    src = zipfile.ZipFile(io.BytesIO(data))
    names = src.namelist()
    plates = [n for n in names if re.fullmatch(r"Metadata/plate_\d+\.gcode", n, re.I)]
    if not plates:
        raise ValueError('No sliced G-code inside. In Bambu Studio use "Export plate sliced file" (.gcode.3mf).')
    new_content, removed = {}, []
    for p in plates:
        text, rem = patch_gcode(src.read(p).decode("utf-8"), remove_fast_check)
        if not rem:
            continue
        plate_no = re.search(r"plate_(\d+)", p).group(1)
        removed += [("plate %s line %d" % (plate_no, ln), code) for ln, code in rem]
        raw = text.encode("utf-8")
        new_content[p] = raw
        md5_name = p + ".md5"
        if md5_name in names:
            old = src.read(md5_name).decode().strip()
            digest = hashlib.md5(raw).hexdigest()
            new_content[md5_name] = (digest if old == old.lower() else digest.upper()).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            dst.writestr(info, new_content.get(info.filename, src.read(info.filename)))
    return out.getvalue(), removed


def process(filename, data, remove_fast_check=True):
    """Returns (bytes, output_filename, display_name, [(where, code), ...])."""
    lower = filename.lower()
    if lower.endswith(".3mf"):
        out, removed = patch_3mf(data, remove_fast_check)
        base = re.sub(r"(\.gcode)?\.3mf$", "", filename, flags=re.I)
        return out, base + ".gcode.3mf", base, removed
    if lower.endswith(".gcode"):
        text, removed = patch_gcode(data.decode("utf-8"), remove_fast_check)
        base = filename[:-6]
        return text.encode("utf-8"), base + ".gcode", base, [("line %d" % l, c) for l, c in removed]
    raise ValueError("Only .gcode or .gcode.3mf files are supported.")
