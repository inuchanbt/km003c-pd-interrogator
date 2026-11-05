"""Compile and exercise the same portable C that will run on the MCU."""
import base64
import ctypes as C
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

from pdinfo import model, transfer
from pdinfo.events import Collector

ROOT = Path(__file__).resolve().parents[1]
U8 = C.c_uint8


class Field(C.Structure):
    _fields_ = [('raw', C.POINTER(U8)), ('length', C.c_uint16),
                ('status', U8), ('present', C.c_bool)]


class Snapshot(C.Structure):
    _fields_ = [('id', U8 * 8), ('origin', U8), ('capture_status', U8), ('fields', Field * 10)]


class Transfer(C.Structure):
    _fields_ = [('binary', U8 * 2664), ('crc', C.c_uint32), ('length', C.c_uint16),
                ('pages', U8), ('ready', C.c_bool)]


class Workspace(C.Structure):
    _fields_ = [('url', C.c_char * 153), ('temp', U8 * 302), ('qr', U8 * 302), ('ready', C.c_bool)]


def native_snapshot(snapshot):
    result = Snapshot()
    result.id[:] = bytes.fromhex(snapshot['measurement_id'])
    result.origin = model.ORIGINS.index(snapshot['origin'])
    result.capture_status = model.CAPTURE_STATES.index(snapshot['capture_status'])
    keep = []
    for index, name in enumerate(model.FIELDS):
        f = snapshot['fields'][name]
        raw = bytes.fromhex(f.get('raw', ''))
        buf = (U8 * len(raw))(*raw)
        keep.append(buf)
        result.fields[index] = Field(buf, len(raw), model.STATES.index(f['status']), 'raw' in f)
    return result, keep


class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = os.environ.get('KPD_CC') or shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
        if not cc:
            raise RuntimeError('Native checks need a C11 compiler; set KPD_CC to its executable path')
        (ROOT / 'artifacts').mkdir(exist_ok=True)
        cls.folder = tempfile.TemporaryDirectory(dir=ROOT / 'artifacts')
        suffix = '.dll' if sys.platform == 'win32' else '.so'
        binary = Path(cls.folder.name) / ('kpd' + suffix)
        args = [cc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-pedantic', '-shared']
        args += ['-Wl,--export-all-symbols'] if sys.platform == 'win32' else ['-fPIC']
        args += ['-I', str(ROOT / 'native'), str(ROOT / 'native/kpd.c'),
                 str(ROOT / 'native/vendor/qrcodegen/qrcodegen.c'), '-o', str(binary)]
        compiled = subprocess.run(args, capture_output=True, text=True)
        if compiled.returncode:
            raise RuntimeError(f'Native C compile failed:\n{compiled.stdout}\n{compiled.stderr}')
        cls.dll = C.CDLL(str(binary))
        signatures = {
            'kpd_freeze': [C.POINTER(Snapshot), C.POINTER(Transfer)],
            'kpd_page_url': [C.POINTER(Transfer), C.c_uint, C.c_char_p],
            'kpd_encode_page': [C.POINTER(Transfer), C.c_uint, C.POINTER(Workspace)],
            'kpd_qr_pack': [C.POINTER(Workspace), C.POINTER(U8)],
            'kpd_qr_row': [C.POINTER(Workspace), C.c_uint, C.POINTER(U8)],
        }
        for name, types in signatures.items():
            function = getattr(cls.dll, name)
            function.argtypes, function.restype = types, C.c_bool

    @classmethod
    def tearDownClass(cls):
        # Windows holds a loaded DLL open until process exit. Temp artifacts are ignored.
        if sys.platform != 'win32':
            cls.folder.cleanup()
        else:
            cls.folder._finalizer.detach()

    def freeze(self, snapshot):
        source, keep = native_snapshot(snapshot)
        target = Transfer()
        self.assertTrue(self.dll.kpd_freeze(C.byref(source), C.byref(target)))
        self.assertEqual(bytes(target.binary[:target.length]), transfer.pack(snapshot))
        return target

    def test_binary_urls_and_frozen_copy_match_host(self):
        rng = random.Random(1729)
        for index in range(30):
            snap = model.new_snapshot(model.ORIGINS[index % 3], rng.randbytes(8).hex())
            snap['capture_status'] = model.CAPTURE_STATES[index % 5]
            for name in model.FIELDS:
                length = rng.randrange(261)
                if name in ('spr_pdos', 'epr_pdos', 'cable_identity', 'source_identity'):
                    length -= length % 4
                model.set_field(snap, name, rng.choice(model.STATES), rng.randbytes(length) if index % 4 else None)
            source, keep = native_snapshot(snap)
            frozen = Transfer()
            self.assertTrue(self.dll.kpd_freeze(C.byref(source), C.byref(frozen)))
            # Receiver can replace every original field without changing this transfer.
            C.memset(C.byref(source), 0, C.sizeof(source))
            for buf in keep:
                C.memset(buf, 0, C.sizeof(buf))
            self.assertEqual(bytes(frozen.binary[:frozen.length]), transfer.pack(snap))
            expected = transfer.encode_pages(snap, transfer.DEFAULT_IMPORT_URL)
            self.assertEqual(frozen.pages, len(expected))
            for page, text in enumerate(expected, 1):
                out = C.create_string_buffer(153)
                self.assertTrue(self.dll.kpd_page_url(C.byref(frozen), page, out))
                self.assertEqual(out.value.decode(), text)
                self.assertLessEqual(len(out.value), 152)

    def test_bad_input_and_stale_qr_are_rejected(self):
        snap, keep = native_snapshot(model.new_snapshot('demo', '0123456789abcdef'))
        frozen = Transfer()
        for index, attribute, value in [(0, 'status', 12), (0, 'length', 261),
                                         (1, 'length', 3), (3, 'length', 1)]:
            old = getattr(snap.fields[index], attribute)
            setattr(snap.fields[index], attribute, value)
            self.assertFalse(self.dll.kpd_freeze(C.byref(snap), C.byref(frozen)))
            self.assertFalse(frozen.ready)
            setattr(snap.fields[index], attribute, old)
        snap.fields[3] = Field(None, 1, 1, True)
        self.assertFalse(self.dll.kpd_freeze(C.byref(snap), C.byref(frozen)))
        snap.fields[3] = Field(None, 0, 1, True)  # explicitly empty is valid
        self.assertTrue(self.dll.kpd_freeze(C.byref(snap), C.byref(frozen)))
        work, row = Workspace(), (U8 * 480)()
        self.assertTrue(self.dll.kpd_encode_page(C.byref(frozen), 1, C.byref(work)))
        self.assertFalse(self.dll.kpd_qr_row(C.byref(work), 240, row))
        for page in (0, frozen.pages + 1, 0xffffffff):
            self.assertFalse(self.dll.kpd_encode_page(C.byref(frozen), page, C.byref(work)))
            self.assertEqual(work.url, b'')
            self.assertFalse(self.dll.kpd_qr_row(C.byref(work), 0, row))
        for origin, status in ((3, 0), (0, 5)):
            snap.origin, snap.capture_status = origin, status
            self.assertFalse(self.dll.kpd_freeze(C.byref(snap), C.byref(frozen)))

    def test_native_qr_rows_decode_in_phone(self):
        snap = model.new_snapshot('demo', 'ffffffff00000000')
        snap['capture_status'] = 'interrupted'
        for name in model.FIELDS:
            model.set_field(snap, name, 'partial', bytes(range(256)) + b'\x00\xff\x80\x01')
        actual = model.new_snapshot('km003c', '0000000000000006')
        actual['capture_status'] = 'imported'
        collector = Collector(actual)
        for event in json.loads((ROOT / 'tests/fixtures/stock-pd32-events.json').read_text()):
            collector.feed(event)
        snapshots = [snap, actual]
        images = []
        for snapshot in snapshots:
            frozen = self.freeze(snapshot)
            expected = transfer.encode_pages(snapshot, transfer.DEFAULT_IMPORT_URL)
            work, packed, row = Workspace(), (U8 * 301)(), (U8 * 480)()
            self.assertEqual(frozen.pages, len(expected))
            for page in (1, len(expected)):
                self.assertTrue(self.dll.kpd_encode_page(C.byref(frozen), page, C.byref(work)))
                self.assertEqual(work.url.decode(), expected[page - 1])
                self.assertTrue(self.dll.kpd_qr_pack(C.byref(work), packed))
                raster = bytearray()
                for y in range(240):
                    self.assertTrue(self.dll.kpd_qr_row(C.byref(work), y, row))
                    raster.extend(row)
                    for x in range(240):
                        bit = ((y - 22) // 4) * 49 + (x - 22) // 4
                        dark = 22 <= x < 218 and 22 <= y < 218 and bool(packed[bit // 8] & (128 >> (bit % 8)))
                        self.assertEqual(bytes(row[x * 2:x * 2 + 2]), b'\x00\x00' if dark else b'\xff\xff')
                self.assertEqual(packed[-1] & 127, 0)
                images.append(dict(text=expected[page - 1], rgb565=base64.b64encode(raster).decode()))
        result = subprocess.run(['node', str(ROOT / 'tests/check_native.cjs')],
                                input=json.dumps(images), capture_output=True, text=True, check=True)
        self.assertIn('4 native C rasters decoded', result.stdout)


if __name__ == '__main__':
    unittest.main()
