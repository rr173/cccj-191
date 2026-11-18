"""Package integrity: truncation, cycles, bad references, unfinished
builds must all fail; a copied package must open identically."""

import os
import shutil
import unittest

from helpers import (
    DictTestCase, build_dict, read_package_parts, write_package_parts,
    mutate_arc_target,
)

from dicttool import packformat as pf
from dicttool.api import Dictionary
from dicttool.errors import (
    BadReferenceError,
    CyclicGraphError,
    IncompleteBuildError,
    PackageError,
    TruncatedPackageError,
)
from dicttool.reader import Package


class TestIntegrity(DictTestCase):
    def setUp(self):
        super().setUp()
        self.rows = [("ab", 9), ("cb", 100), ("abc", 1), ("z", 2)]
        self.pkg, _ = build_dict(self.tmp, self.rows)

    def test_truncated_file_fails(self):
        size = os.path.getsize(self.pkg)
        for cut in (size - 1, size - pf.FOOTER_LEN, size // 2, 20):
            bad = os.path.join(self.tmp, f"cut{cut}.mdawg")
            with open(self.pkg, "rb") as f, open(bad, "wb") as g:
                g.write(f.read(cut))
            with self.assertRaises((TruncatedPackageError, IncompleteBuildError,
                                    PackageError)):
                Package.open(bad)

    def test_missing_footer_is_unfinished_build(self):
        bad = os.path.join(self.tmp, "unfinished.mdawg")
        with open(self.pkg, "rb") as f, open(bad, "wb") as g:
            g.write(f.read(os.path.getsize(self.pkg) - pf.FOOTER_LEN))
        with self.assertRaises((TruncatedPackageError, IncompleteBuildError)):
            Package.open(bad)

    def test_bit_corruption_fails_digest(self):
        header, sections = read_package_parts(self.pkg)
        sections["entries"][0] ^= 0xFF  # flip a byte, keep old hashes
        bad = os.path.join(self.tmp, "corrupt.mdawg")
        # write parts back WITHOUT recomputing hashes: reuse original file
        # and flip the byte in place instead
        shutil.copy(self.pkg, bad)
        off = header["sections"]["entries"]["offset"]
        with open(bad, "r+b") as f:
            f.seek(off)
            b = f.read(1)
            f.seek(off)
            f.write(bytes([b[0] ^ 0xFF]))
        with self.assertRaises(IncompleteBuildError):
            Package.open(bad)

    def test_cycle_fails(self):
        header, sections = read_package_parts(self.pkg)
        # point arc 0 back at the start state -> cycle
        mutate_arc_target(header, sections, 0, header["start_node"])
        bad = os.path.join(self.tmp, "cycle.mdawg")
        write_package_parts(bad, header, sections)
        with self.assertRaises(CyclicGraphError):
            Package.open(bad)

    def test_self_loop_fails(self):
        header, sections = read_package_parts(self.pkg)
        # find an arc of the start state and point it at the start state
        mutate_arc_target(header, sections, 0, header["start_node"])
        bad = os.path.join(self.tmp, "selfloop.mdawg")
        write_package_parts(bad, header, sections)
        with self.assertRaises(CyclicGraphError):
            Package.open(bad)

    def test_bad_arc_target_fails(self):
        header, sections = read_package_parts(self.pkg)
        mutate_arc_target(header, sections, 0, header["node_count"] + 5)
        bad = os.path.join(self.tmp, "badref.mdawg")
        write_package_parts(bad, header, sections)
        with self.assertRaises(BadReferenceError):
            Package.open(bad)

    def test_bad_magic_fails(self):
        bad = os.path.join(self.tmp, "badmagic.mdawg")
        shutil.copy(self.pkg, bad)
        with open(bad, "r+b") as f:
            f.write(b"XXXXXXXX")
        with self.assertRaises(PackageError):
            Package.open(bad)

    def test_copied_package_is_self_contained(self):
        copy = os.path.join(self.tmp, "sub", "copy.mdawg")
        os.makedirs(os.path.dirname(copy))
        shutil.copy(self.pkg, copy)
        with Dictionary.open(copy) as d:
            self.assertEqual(d.prefix("ab", 5), [("ab", 9), ("abc", 1)])
            self.assertEqual(d.suggest("ab", 1, 1), [("ab", 9, 0)])

    def test_reopen_many_times(self):
        for _ in range(5):
            with Dictionary.open(self.pkg) as d:
                self.assertEqual(d.word_count, 4)


if __name__ == "__main__":
    unittest.main()
