"""Remove the X1-series vibration (resonance) calibration from Bambu Lab G-code.

The start G-code runs the resonance sweep with M970 / M970.3 (shake an axis) and M974
(fit the result). These lines are commented out; M975 S1 (use stored compensation) stays.
"""

import hashlib
import io
import re
import struct
import zipfile
import zlib

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
    if not new_content:
        return data, removed
    try:
        return replace_in_zip(data, new_content), removed
    except ValueError:
        # unusual archive (zip64 directory, comments ...): fall back to a plain rewrite
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as dst:
            for info in src.infolist():
                dst.writestr(info, new_content.get(info.filename, src.read(info.filename)))
        return out.getvalue(), removed


def replace_in_zip(data, replacements):
    """Replace some entries of a ZIP while keeping everything else byte-identical.

    Bambu Studio writes every entry with a data descriptor (flag 0x8: CRC and sizes are 0 in
    the local header and follow the data) and some with a zip64 extra field. Bambu Connect
    only imports archives in exactly that shape, so unchanged entries are copied verbatim
    and replaced ones are re-encoded with the same header layout.
    """
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0 or len(data) - eocd != 22:  # archive comment or no EOCD
        raise ValueError("unsupported zip")
    (_, disk, cd_disk, n_disk, n_total, cd_size, cd_off, _clen) = struct.unpack("<IHHHHIIH", data[eocd:eocd + 22])
    if disk or cd_disk or n_disk != n_total or 0xFFFFFFFF in (cd_size, cd_off) or 0xFFFF == n_total:
        raise ValueError("unsupported zip")

    # parse the central directory
    entries, pos = [], cd_off
    for _ in range(n_total):
        if data[pos:pos + 4] != b"PK\x01\x02":
            raise ValueError("bad central directory")
        fields = list(struct.unpack("<IHHHHHHIIIHHHHHII", data[pos:pos + 46]))
        nlen, elen, clen = fields[10], fields[11], fields[12]
        name_raw = data[pos + 46:pos + 46 + nlen]
        rec_end = pos + 46 + nlen + elen + clen
        if 0xFFFFFFFF in (fields[8], fields[9], fields[16]):
            raise ValueError("zip64 entry")
        name = name_raw.decode("utf-8" if fields[3] & 0x800 else "cp437")
        entries.append({"name": name, "cd": fields, "cd_tail": data[pos + 46:rec_end], "offset": fields[16]})
        pos = rec_end

    # each entry's raw bytes run from its local header to the next entry (or the directory)
    by_offset = sorted(entries, key=lambda e: e["offset"])
    for i, e in enumerate(by_offset):
        e["end"] = by_offset[i + 1]["offset"] if i + 1 < len(by_offset) else cd_off

    out = bytearray()
    for e in by_offset:
        start = e["offset"]
        e["new_offset"] = len(out)
        if e["name"] not in replacements:
            out += data[start:e["end"]]
            continue
        lh = list(struct.unpack("<IHHHHHIIIHH", data[start:start + 30]))
        flag, method = lh[2], lh[3]
        name_extra = data[start + 30:start + 30 + lh[9] + lh[10]]
        raw = replacements[e["name"]]
        crc = zlib.crc32(raw) & 0xFFFFFFFF
        if method == 8:
            comp = zlib.compressobj(6, zlib.DEFLATED, -15)
            payload = comp.compress(raw) + comp.flush()
        elif method == 0:
            payload = raw
        else:
            raise ValueError("unsupported compression")
        if len(payload) >= 0xFFFFFFFF or len(raw) >= 0xFFFFFFFF:
            raise ValueError("entry too large")
        if flag & 0x8:  # data descriptor, like Bambu Studio
            lh[6], lh[7], lh[8] = 0, 0, 0
            out += struct.pack("<IHHHHHIIIHH", *lh) + name_extra + payload
            out += struct.pack("<IIII", 0x08074B50, crc, len(payload), len(raw))
        else:
            lh[6], lh[7], lh[8] = crc, len(payload), len(raw)
            out += struct.pack("<IHHHHHIIIHH", *lh) + name_extra + payload
        e["cd"][7], e["cd"][8], e["cd"][9] = crc, len(payload), len(raw)

    new_cd_off = len(out)
    for e in entries:  # central directory in its original order
        f = list(e["cd"])
        f[16] = e["new_offset"]
        out += struct.pack("<IHHHHHHIIIHHHHHII", *f) + e["cd_tail"]
    new_cd_size = len(out) - new_cd_off
    out += struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, n_total, n_total, new_cd_size, new_cd_off, 0)
    return bytes(out)


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
