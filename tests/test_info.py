"""Exercise native GET sequencing with real wire fixtures and failure paths."""
import ctypes as C
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

from pdinfo import model, transfer
from tests.test_native import CacheCapture, Transfer, U8, cache_sample

ROOT = Path(__file__).resolve().parents[1]


class InfoField(C.Structure):
    _fields_ = [('raw', U8 * 260), ('length', C.c_uint16), ('total', C.c_uint16),
                ('chunks', C.c_uint16), ('status', U8), ('present', U8)]


class Info(C.Structure):
    _fields_ = [('fields', InfoField * 8), ('since', C.c_uint32),
                ('ring_total', C.c_uint16), ('ring_position', C.c_uint16),
                ('stage', U8), ('step', U8), ('current', U8), ('capture_status', U8),
                ('ring_ready', C.c_bool), ('attached', C.c_bool), ('pps', C.c_bool), ('started', C.c_bool),
                ('tx_length', U8), ('tx_wire', U8 * 30)]


def record(wire, sop=0, tx=False):
    if isinstance(wire, str):
        wire = bytes.fromhex(wire)
    return bytes([(0xc0 if tx else 0x80) | (len(wire) + 5)]) + bytes(4) + bytes([sop]) + wire


class InfoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = os.environ.get('KPD_CC') or shutil.which('cc') or shutil.which('clang')
        if not cc:
            raise RuntimeError('KPD_CC must name a C11 compiler')
        (ROOT / 'artifacts').mkdir(exist_ok=True)
        cls.folder = tempfile.TemporaryDirectory(dir=ROOT / 'artifacts')
        binary = Path(cls.folder.name) / ('info.dll' if sys.platform == 'win32' else 'info.so')
        args = [cc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-pedantic', '-shared']
        args += ['-Wl,--export-all-symbols'] if sys.platform == 'win32' else ['-fPIC']
        args += ['-I', str(ROOT / 'native'), str(ROOT / 'native/overlay207/info.c'),
                 str(ROOT / 'native/overlay207/capture.c'), str(ROOT / 'native/kpd.c'),
                 str(ROOT / 'native/vendor/qrcodegen/qrcodegen.c'), '-o', str(binary)]
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        cls.dll = C.CDLL(str(binary))
        for name, types in {
            'kpd207_info_begin': [C.POINTER(Info)],
            'kpd207_info_stop': [C.POINTER(Info), U8],
            'kpd207_info_tick': [C.POINTER(Info), C.c_bool, C.c_bool, C.c_uint32],
            'kpd207_info_command': [C.POINTER(Info), C.POINTER(U8)],
            'kpd207_info_sent': [C.POINTER(Info), C.c_uint32],
            'kpd207_info_records': [C.POINTER(Info), C.POINTER(U8), C.c_uint, C.c_uint32],
            'kpd207_info_freeze': [C.POINTER(CacheCapture), C.POINTER(Info), C.POINTER(Transfer)],
        }.items():
            getattr(cls.dll, name).argtypes = types
        cls.dll.kpd207_info_records.restype = C.c_bool
        cls.dll.kpd207_info_freeze.restype = C.c_bool
        cls.events = json.loads((ROOT / 'tests/fixtures/stock-pd32-events.json').read_text())

    @classmethod
    def tearDownClass(cls):
        # Windows keeps the DLL mapped for this process; don't delete its folder
        # until all references are released, matching the existing native tests.
        if sys.platform == 'win32':
            import _ctypes
            _ctypes.FreeLibrary(cls.dll._handle)
        cls.folder.cleanup()

    def setUp(self):
        self.info = Info()
        self.now = 0
        self.dll.kpd207_info_begin(C.byref(self.info))

    def tick(self, dt=0, ready=True, pps=False):
        self.now = (self.now + dt) & 0xffffffff
        self.dll.kpd207_info_tick(C.byref(self.info), ready, pps, self.now)

    def feed(self, wire, sop=0, tx=False):
        raw = record(wire, sop, tx)
        return self.dll.kpd207_info_records(C.byref(self.info), (U8 * len(raw)).from_buffer_copy(raw), len(raw), self.now)

    def event(self, index):
        e = self.events[index]
        self.assertTrue(self.feed(e['wire_hex'], e['sop'], not bool(e['header'] & 0x100)))

    def command(self, expected):
        out = (U8 * 7)()
        length = self.dll.kpd207_info_command(C.byref(self.info), out)
        self.assertEqual(bytes(out[:length]).hex(), expected)
        self.dll.kpd207_info_sent(C.byref(self.info), self.now)
        wire = bytes(out[1:length])
        if expected == '00869000000000':
            wire = bytes.fromhex(self.events[13]['wire_hex'])  # Native uses Chunked=1, chunk 0.
        self.assertTrue(self.feed(wire, tx=True))
        self.assertEqual(self.info.stage, 4)

    def raw(self, name):
        f = self.info.fields[model.FIELDS.index(name) - 2]
        return bytes(f.raw[:f.length]), model.STATES[f.status]

    def start(self, pps=False):
        self.tick(pps=pps)
        self.tick(1999, pps=pps)
        self.assertEqual(self.info.stage, 1)
        self.tick(1, pps=pps)
        self.assertEqual(self.info.stage, 2)

    def test_real_source_fields_and_completed_result_roundtrip(self):
        self.event(0); self.event(1)
        self.start()
        for cmd, reply, name in [
            ('009800', 9, 'revision'), ('009100', 10, 'source_cap_ext'),
            ('009200', 11, 'source_status'), ('009700', 12, 'source_info'),
            ('00869000000000', 14, 'manufacturer'),
        ]:
            self.command(cmd); self.event(reply)
            self.assertEqual(self.raw(name)[1], 'pass')
            self.tick(150)
        self.command('008f1001a800ff')
        self.feed('af 11 81 a8 00 ff')  # Discover Identity NAK from source.
        self.tick(150)
        self.assertEqual((self.info.stage, self.info.capture_status), (6, 1))
        self.assertEqual(self.raw('source_identity')[1], 'unsupported')
        self.assertEqual(self.raw('pps_status')[1], 'not_requested')
        self.assertEqual(self.raw('cable_identity')[1], 'observed')
        c = CacheCapture(flags=4)
        c.id[:] = bytes.fromhex('1122334455667788')
        c.result = cache_sample()
        frozen = Transfer()
        self.assertTrue(self.dll.kpd207_info_freeze(C.byref(c), C.byref(self.info), C.byref(frozen)))
        decoded = transfer.unpack(bytes(frozen.binary[:frozen.length]))
        self.assertEqual(decoded['capture_status'], 'completed')
        for name in model.FIELDS[2:]:
            self.assertEqual(decoded['fields'][name]['status'], self.raw(name)[1])
        self.assertEqual(decoded['fields']['revision']['raw'], '00001132')
        self.assertEqual(decoded['fields']['source_cap_ext']['decoded']['vid'], '32AC')

    def test_no_source_reply_or_no_native_tx_stops_following_requests(self):
        for witness, expected in [(False, 'tx_failed'), (True, 'no_response')]:
            self.setUp(); self.start()
            if witness:
                self.command('009800')
            else:
                self.dll.kpd207_info_sent(C.byref(self.info), self.now)
            self.tick(1200)
            self.assertEqual(self.raw('revision')[1], expected)
            self.assertEqual(self.raw('source_info')[1], 'not_requested')
            self.assertEqual((self.info.stage, self.info.capture_status), (6, 2))

    def test_unsolicited_reply_cannot_pass_without_tx_witness(self):
        self.start()
        self.dll.kpd207_info_sent(C.byref(self.info), self.now)
        self.event(9)
        self.assertEqual(self.raw('revision')[1], 'observed')
        self.assertEqual(self.info.stage, 3)
        self.tick(1200)
        self.assertEqual(self.raw('revision')[1], 'tx_failed')

    def test_unsupported_busy_and_reject_are_correlated_only_to_active_request(self):
        for type_, status in [(16, 'unsupported'), (12, 'busy'), (4, 'rejected')]:
            self.setUp(); self.start(); self.command('009800')
            self.feed(struct.pack('<H', 0x180 | type_))
            self.assertEqual(self.raw('revision')[1], status)
            self.tick(150)
            self.assertEqual(self.info.current, 3)

    def test_chunked_manufacturer_duplicate_and_missing_fragments(self):
        self.info.current = 7; self.info.stage = 2
        self.command('00869000000000')
        raw = bytes(range(52))
        def chunk(number, content):
            return struct.pack('<HH', 0xf187, 0x8000 | number << 11 | 52) + content
        self.feed(chunk(0, raw[:26])); self.feed(chunk(0, raw[:26]))
        self.assertEqual(self.raw('manufacturer')[1], 'partial')
        self.feed(chunk(1, raw[26:]))
        self.assertEqual(self.raw('manufacturer'), (raw, 'pass'))
        self.setUp(); self.info.current = 7; self.info.stage = 2
        self.command('00869000000000'); self.feed(chunk(1, raw[26:])); self.tick(1200)
        self.assertEqual(self.raw('manufacturer')[1], 'partial')

    def test_invalid_size_duplicate_and_wrong_native_payload_stop(self):
        self.start(); self.command('009800')
        self.feed('ac 21 00 00 11 32 00 00 00 00')
        self.assertEqual((self.raw('revision')[1], self.info.stage), ('invalid', 6))
        self.setUp(); self.info.current = 7; self.info.stage = 2
        self.dll.kpd207_info_sent(C.byref(self.info), 0)
        self.feed('86 a2 04 00 01 00 00 00 00 00', tx=True)
        self.assertEqual((self.raw('manufacturer')[1], self.info.stage), ('tx_failed', 6))

    def test_reset_disconnect_lifecycle_and_timer_rollover(self):
        self.now = 0xfffffff0; self.start(); self.command('009800')
        self.feed('8d 01')
        self.assertEqual((self.raw('revision')[1], self.info.capture_status), ('reset', 3))
        self.dll.kpd207_info_begin(C.byref(self.info))
        self.assertEqual(self.raw('revision')[1], 'not_requested')
        self.start(); self.command('009800')
        b = (U8 * 6)(0x45, 0, 0, 0, 0, 0x22)
        self.assertTrue(self.dll.kpd207_info_records(C.byref(self.info), b, 6, self.now))
        self.assertEqual(self.raw('revision')[1], 'reset')

    def test_pps_is_requested_only_for_an_existing_pps_contract(self):
        for allowed in (False, True):
            self.setUp(); self.info.pps = allowed; self.info.step = 5; self.info.stage = 5
            self.tick(150)
            self.assertEqual(self.info.stage, 2 if allowed else 6)
            if allowed:
                self.command('009400')
                self.feed('8c a1 04 00 ff ff ff ff 00 00')
                self.assertEqual(self.raw('pps_status'), (b'\xff' * 4, 'pass'))

    def test_record_boundaries_and_maximum_extended_size(self):
        self.assertFalse(self.dll.kpd207_info_records(C.byref(self.info), (U8 * 1)(0xff), 1, 0))
        self.info.current = 7; self.info.stage = 2; self.command('00869000000000')
        expected = bytes(range(256)) + bytes(4)
        for number in range(10):
            self.feed(struct.pack('<HH', 0xf187, 0x8000 | number << 11 | 260) + expected[number * 26:(number + 1) * 26])
        self.assertEqual(self.raw('manufacturer'), (expected, 'pass'))

    def test_missing_pdos_or_leaving_ready_state_cannot_wait_forever(self):
        for ready_first in (False, True):
            self.setUp(); self.tick(ready=ready_first); self.tick(6000, ready=False)
            self.assertEqual((self.info.stage, self.info.capture_status), (6, 2))
            self.assertTrue(all(f.status == 0 for f in self.info.fields))

    def test_manufacturer_tx_accepts_chunked_and_unchunked_target_ref_only(self):
        for ext in (2, 4, 0x8002, 0x8004, 0x8404, 0x8804, 0x8204):
            self.setUp(); self.info.current = 7; self.info.stage = 2
            self.dll.kpd207_info_sent(C.byref(self.info), 0)
            w = struct.pack('<HH', 0xa086, ext) + bytes(6)
            self.feed(w, tx=True)
            self.assertEqual(self.info.stage, 4 if ext in (2, 4, 0x8002, 0x8004) else 6)
            self.assertEqual(bytes(self.info.tx_wire[:self.info.tx_length]), w)


if __name__ == '__main__':
    unittest.main()
