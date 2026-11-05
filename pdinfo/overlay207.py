"""Offline, exact-build stock extension layout. No device or update protocol."""
import hashlib
import struct
import zlib

STOCK_HASH = 'c63057eaf08b8625e7ac2fc7270c06d75515e78fdbdd46080bdf4b696984ec67'
LOADER_HASH = '8e972bbd2abffa199d465082f473823372e32d4b4c86574664a976afd2797809'
BASE, GATE, TAIL, LIMIT = 0x4000, 0x738c0, 0x74000, 0x7c000
GUI_ENTRY, GUI_LOOP, GUI_HANDLER = 0x1ebb0, 0x1ec04, 0x39a19
ENTRY_BEFORE = bytes.fromhex('40f22424c4f2a724')
LOOP_BEFORE = bytes.fromhex('1af008ff')
LOOP_AFTER = bytes.fromhex('c04700bf')  # BLX R8; NOP


def branch_w(source, target):
    """Encode Thumb-2 B.W; target is an even instruction address."""
    delta = target - source - 4
    if not 0 <= source <= 0xfffffffa or not 0 <= target <= 0xfffffffe or source & 1 or target & 1 or not -(1 << 24) <= delta < (1 << 24):
        raise ValueError('Thumb branch target out of range or unaligned')
    value = delta & 0x1ffffff
    s, i1, i2 = (value >> 24) & 1, (value >> 23) & 1, (value >> 22) & 1
    first = 0xf000 | (s << 10) | ((value >> 12) & 1023)
    second = 0x9000 | ((1 ^ i1 ^ s) << 13) | ((1 ^ i2 ^ s) << 11) | ((value >> 1) & 2047)
    return struct.pack('<HH', first, second)


def extension_header(data):
    if len(data) < 68 or len(data) > LIMIT - TAIL or len(data) % 4:
        raise ValueError('Invalid extension size')
    if data[:8] != b'KPDX207\0' or struct.unpack_from('<II', data, 8) != (1, len(data)):
        raise ValueError('Invalid extension header')
    if data[44:60] != bytes.fromhex(STOCK_HASH)[:16] or data[60:64] != bytes(4):
        raise ValueError('Extension is for a different stock build')
    checked = bytearray(data)
    expected = struct.unpack_from('<I', data, 16)[0]
    checked[16:20] = bytes(4)
    if zlib.crc32(checked) != expected:
        raise ValueError('Extension CRC mismatch')
    pointers = struct.unpack_from('<6I', data, 20)
    if any(p & 1 == 0 or not TAIL + 64 <= p - 1 < TAIL + len(data) for p in pointers):
        raise ValueError('Extension entry point out of range')
    return dict(zip(('gui_tick', 'freeze', 'page_url', 'encode_page', 'qr_pack', 'qr_row'), pointers))


def make_candidate(stock, loader, internal_tail, gate, extension):
    if hashlib.sha256(stock).hexdigest() != STOCK_HASH or len(stock) != GATE - BASE:
        raise ValueError('Unrecognized stock application')
    if hashlib.sha256(loader).hexdigest() != LOADER_HASH:
        raise ValueError('Unrecognized HW2.3 loader')
    if len(internal_tail) != 0x10000 or internal_tail[:GATE - 0x70000] != stock[0x70000 - BASE:]:
        raise ValueError('Internal-tail backup does not overlap the same application')
    if internal_tail[GATE - 0x70000:] != b'\xff' * (0x80000 - GATE):
        raise ValueError('Proposed tail is not erased; no candidate may be generated')
    if not gate or len(gate) > TAIL - GATE or len(gate) % 4:
        raise ValueError('Invalid gate size')
    extension_header(extension)
    if stock[GUI_ENTRY - BASE:GUI_ENTRY - BASE + 8] != ENTRY_BEFORE or stock[GUI_LOOP - BASE:GUI_LOOP - BASE + 4] != LOOP_BEFORE:
        raise ValueError('Stock instruction profile mismatch')
    candidate = bytearray(stock + b'\xff' * (TAIL + len(extension) - BASE - len(stock)))
    candidate[0x428:0x430] = b'\xff' * 8  # Native updater would commit these.
    candidate[GUI_ENTRY - BASE:GUI_ENTRY - BASE + 8] = branch_w(GUI_ENTRY, GATE) + bytes.fromhex('00bf00bf')
    candidate[GUI_LOOP - BASE:GUI_LOOP - BASE + 4] = LOOP_AFTER
    candidate[GATE - BASE:GATE - BASE + len(gate)] = gate
    candidate[TAIL - BASE:] = extension
    # An erase pass followed by the original qualified restore container is
    # required to restore both stock metadata and the previously erased tail.
    erase_pass = bytearray(stock + b'\xff' * (len(candidate) - len(stock)))
    erase_pass[0x428:0x430] = b'\xff' * 8
    return bytes(candidate), bytes(erase_pass)
