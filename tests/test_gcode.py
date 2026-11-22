import hashlib
import io
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from gcode import patch_gcode, process  # noqa: E402

SAMPLE = """;===== mech mode fast check============================
G1 Z5 F1200
M970.3 Q1 A7 B30 C80  H15 K0
M974 Q1 S2 P0
M975 S1
;===== mech mode sweep =====
M1002 judge_flag mech_mode_flag
M622 J1
    M970 Q1 A7 B10 C125 K0
    m970 q0 a9 b10 c125 h20 k0
    M974 Q0 S2 P0
    M975 S1
M623
G1 X10 ; M970 in a comment stays
M9700 X1
"""


def make_3mf(gcode, upper=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("3D/3dmodel.model", "<model/>")
        z.writestr("Metadata/plate_1.gcode", gcode)
        md5 = hashlib.md5(gcode.encode()).hexdigest()
        z.writestr("Metadata/plate_1.gcode.md5", md5.upper() if upper else md5)
        z.writestr("Metadata/plate_2.gcode", "G28\n")
    return buf.getvalue()


class PatchGcode(unittest.TestCase):
    def test_removes_calibration_only(self):
        out, removed = patch_gcode(SAMPLE)
        self.assertEqual(len(removed), 5)
        self.assertIn("; [NoCali] M970.3 Q1", out)
        self.assertIn("    ; [NoCali] M970 Q1 A7 B10", out)
        self.assertIn("    ; [NoCali] m970 q0", out)
        self.assertEqual(out.count("M975 S1"), 2)
        self.assertIn("G1 X10 ; M970 in a comment stays", out)
        self.assertIn("\nM9700 X1", out)

    def test_keep_fast_check(self):
        _, removed = patch_gcode(SAMPLE, remove_fast_check=False)
        self.assertEqual(len(removed), 3)

    def test_crlf(self):
        out, removed = patch_gcode(SAMPLE.replace("\n", "\r\n"))
        self.assertEqual(len(removed), 5)
        self.assertIn("; [NoCali] M974 Q1 S2 P0\r\n", out)

    def test_idempotent(self):
        once, _ = patch_gcode(SAMPLE)
        twice, removed = patch_gcode(once)
        self.assertEqual(once, twice)
        self.assertEqual(removed, [])


class Process3mf(unittest.TestCase):
    def check(self, upper):
        out, name, display, removed = process("Cube.gcode.3mf", make_3mf(SAMPLE, upper))
        self.assertEqual((name, display, len(removed)), ("Cube.gcode.3mf", "Cube", 5))
        z = zipfile.ZipFile(io.BytesIO(out))
        g = z.read("Metadata/plate_1.gcode")
        md5 = z.read("Metadata/plate_1.gcode.md5").decode()
        expect = hashlib.md5(g).hexdigest()
        self.assertEqual(md5, expect.upper() if upper else expect)
        self.assertEqual(z.read("Metadata/plate_2.gcode"), b"G28\n")
        self.assertEqual(z.namelist(), ["3D/3dmodel.model", "Metadata/plate_1.gcode",
                                        "Metadata/plate_1.gcode.md5", "Metadata/plate_2.gcode"])
        self.assertTrue(all(i.compress_type == zipfile.ZIP_DEFLATED for i in z.infolist()))

    def test_md5_upper(self):
        self.check(True)

    def test_md5_lower(self):
        self.check(False)

    def test_bambu_layout_is_kept(self):
        """Bambu Studio archives use data descriptors (flag 0x8); Bambu Connect needs that layout
        and all untouched entries byte-identical."""
        class Unseekable(io.RawIOBase):
            def __init__(self):
                self.buf = io.BytesIO()

            def writable(self):
                return True

            def write(self, b):
                return self.buf.write(b)

        sink = Unseekable()
        with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", "<Types/>")
            z.writestr("Metadata/plate_1.gcode.md5", hashlib.md5(SAMPLE.encode()).hexdigest().upper())
            z.writestr("Metadata/plate_1.gcode", SAMPLE)
            z.writestr("Metadata/plate_1.png", b"\x89PNG" + bytes(range(256)) * 20)
        src = sink.buf.getvalue()
        self.assertTrue(all(i.flag_bits & 0x8 for i in zipfile.ZipFile(io.BytesIO(src)).infolist()))

        out, _, _, removed = process("Cube.gcode.3mf", src)
        self.assertEqual(len(removed), 5)
        o, n = zipfile.ZipFile(io.BytesIO(src)), zipfile.ZipFile(io.BytesIO(out))
        self.assertIsNone(n.testzip())
        self.assertEqual(o.namelist(), n.namelist())
        for a, b in zip(o.infolist(), n.infolist()):
            self.assertEqual(a.flag_bits, b.flag_bits)
            if a.filename in ("[Content_Types].xml", "Metadata/plate_1.png"):
                raw = lambda d, i: d[i.header_offset:i.header_offset + 30 + len(i.filename) + i.compress_size + 16]
                self.assertEqual(raw(src, a), raw(out, b))
        g = n.read("Metadata/plate_1.gcode")
        self.assertIn(b"; [NoCali] M974", g)
        self.assertEqual(n.read("Metadata/plate_1.gcode.md5").decode(), hashlib.md5(g).hexdigest().upper())

    def test_nothing_to_remove_returns_original_bytes(self):
        src = make_3mf("G28\nM975 S1\n")
        out, _, _, removed = process("Cube.gcode.3mf", src)
        self.assertEqual((out, removed), (src, []))

    def test_plain_gcode(self):
        out, name, display, removed = process("a.gcode", SAMPLE.encode())
        self.assertEqual((name, display, len(removed)), ("a.gcode", "a", 5))

    def test_unsliced_3mf(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("3D/3dmodel.model", "<model/>")
        with self.assertRaises(ValueError):
            process("model.3mf", buf.getvalue())

    def test_wrong_type(self):
        with self.assertRaises(ValueError):
            process("model.stl", b"solid")


if __name__ == "__main__":
    unittest.main()
