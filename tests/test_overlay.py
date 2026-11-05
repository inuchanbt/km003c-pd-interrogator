import hashlib
import struct
import unittest
from unittest.mock import patch
import zlib

from pdinfo import overlay207 as o


def extension(stock_hash=None):
    data = bytearray(80)
    data[:8] = b'KPDX207\0'
    struct.pack_into('<III6I', data, 8, 1, len(data), 0, *[o.TAIL + 65] * 6)
    data[44:60] = bytes.fromhex(stock_hash or o.STOCK_HASH)[:16]
    struct.pack_into('<I', data, 16, zlib.crc32(data))
    return data


def seal(data):
    data[16:20] = bytes(4)
    struct.pack_into('<I', data, 16, zlib.crc32(data))
    return data


class OverlayTests(unittest.TestCase):
    def test_extension_header_crc_bounds_and_api_pointers(self):
        good = extension()
        self.assertEqual(o.extension_header(good)['gui_tick'], o.TAIL + 65)
        for offset in range(len(good)):
            bad = good.copy()
            bad[offset] ^= 1
            with self.assertRaises(ValueError):
                o.extension_header(bad)
        for offset in range(20, 44, 4):
            for value in (0, o.TAIL + 64, o.TAIL + len(good) + 1):
                bad = good.copy()
                struct.pack_into('<I', bad, offset, value)
                with self.assertRaisesRegex(ValueError, 'entry point'):
                    o.extension_header(seal(bad))

    def test_private_candidate_changes_only_the_declared_locations(self):
        # Synthetic filler, never a stock binary. Substitute only this test's
        # two hash identities; the production tool has no profile override.
        stock = bytearray(o.GATE - o.BASE)
        stock[o.GUI_ENTRY - o.BASE:o.GUI_ENTRY - o.BASE + 8] = o.ENTRY_BEFORE
        stock[o.GUI_LOOP - o.BASE:o.GUI_LOOP - o.BASE + 4] = o.LOOP_BEFORE
        stock[0x428:0x430] = b'\x12' * 8
        loader = b'unit test loader'
        stock_hash = hashlib.sha256(stock).hexdigest()
        tail = bytes(stock[0x70000 - o.BASE:]) + b'\xff' * (0x80000 - o.GATE)
        gate, addon = bytes.fromhex('00bf00bf'), extension(stock_hash)
        with patch.object(o, 'STOCK_HASH', stock_hash), patch.object(o, 'LOADER_HASH', hashlib.sha256(loader).hexdigest()):
            candidate, erase = o.make_candidate(bytes(stock), loader, tail, gate, addon)
            self.assertEqual(len(candidate), o.TAIL + len(addon) - o.BASE)
            allowed = set(range(0x428, 0x430)) | set(range(o.GUI_ENTRY - o.BASE, o.GUI_ENTRY - o.BASE + 8)) | set(range(o.GUI_LOOP - o.BASE, o.GUI_LOOP - o.BASE + 4))
            for n, value in enumerate(stock):
                if n not in allowed:
                    self.assertEqual(candidate[n], value)
            self.assertEqual(candidate[o.GATE - o.BASE:o.GATE - o.BASE + len(gate)], gate)
            self.assertEqual(candidate[o.TAIL - o.BASE:], addon)
            expected = bytearray(stock)
            expected[0x428:0x430] = b'\xff' * 8
            self.assertEqual(erase[:len(stock)], expected)
            self.assertEqual(erase[len(stock):], b'\xff' * (len(erase) - len(stock)))
            for address in (o.GATE, o.TAIL, 0x7ffff):
                bad = bytearray(tail)
                bad[address - 0x70000] = 0
                with self.assertRaisesRegex(ValueError, 'not erased'):
                    o.make_candidate(bytes(stock), loader, bad, gate, addon)
            with self.assertRaisesRegex(ValueError, 'overlap'):
                o.make_candidate(bytes(stock), loader, b'\xff' * 0x10000, gate, addon)
        with self.assertRaisesRegex(ValueError, 'Unrecognized stock'):
            o.make_candidate(bytes(stock), loader, tail, gate, addon)

    def test_thumb_branch_rejects_invalid_targets(self):
        for source, target in ((0, 1), (1, 2), (0x1000, 0x1001004), (0x2001004, 0x1000), (-2, 0), (0xfffffffc, 0)):
            with self.assertRaises(ValueError):
                o.branch_w(source, target)
        self.assertEqual(len(o.branch_w(o.GUI_ENTRY, o.GATE)), 4)


if __name__ == '__main__':
    unittest.main()
