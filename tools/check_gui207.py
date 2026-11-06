"""Exercise the QR GUI against an owner's private stock ROM/RAM snapshot.

Runs native LVGL allocation, objects and input groups in Unicorn. It never opens
USB or publishes snapshot bytes. RTOS, display flushing and PD are not modeled.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.check_native_arm import load_elf
from pdinfo import model, transfer


def check_qr_caption(frame, height, module_size):
    if len(frame) != 8 + height * 30:
        raise AssertionError('Unexpected indexed QR image size')
    rows = [int.from_bytes(frame[i:i + 30], 'big') for i in range(8, len(frame), 30)]
    caption = rows[-12:-2]
    if not all(caption) or any(rows[-2:]):
        raise AssertionError('Expected a 10-pixel caption with a 2-pixel bottom margin')
    caption_bits = 0
    for row in caption:
        caption_bits |= row
    if 240 - caption_bits.bit_length() != (caption_bits & -caption_bits).bit_length() - 1:
        raise AssertionError('QR caption is off center')
    qr_rows = [y for y, row in enumerate(rows[:-12]) if row]
    qr_bits = 0
    for y in qr_rows:
        qr_bits |= rows[y]
    left = 240 - qr_bits.bit_length()
    right = (qr_bits & -qr_bits).bit_length() - 1
    if not qr_rows or (240 - left - right, qr_rows[-1] - qr_rows[0] + 1) != (49 * module_size,) * 2:
        raise AssertionError('QR symbol dimensions changed')
    if min(left, right, qr_rows[0], height - 12 - qr_rows[-1] - 1) < 4 * module_size:
        raise AssertionError('QR symbol lost its four-module quiet zone')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--build', type=Path, default=ROOT / 'artifacts/overlay207')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--trace-stack', action='store_true', help='Slower native basic-block stack observation')
    parser.add_argument('--rng-mode', choices=('complete', 'timeout', 'busy', 'zero'), default='complete')
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / 'artifacts').resolve()):
        parser.error('Private output must stay inside ignored artifacts/')
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_BLOCK
    from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3
    rom = args.stock.read_bytes()
    if hashlib.sha256(rom).hexdigest() != 'c63057eaf08b8625e7ac2fc7270c06d75515e78fdbdd46080bdf4b696984ec67':
        raise ValueError('Exact qualified stock 2.0.7 ROM required')
    cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    cpu.mem_map(0, 0x80000)
    cpu.mem_write(0x4000, rom)
    cpu.mem_map(0x1fff8000, 0x2f000)
    metadata = json.loads((args.snapshot / 'gui-snapshot.json').read_text())
    ram = (args.snapshot / 'sram.bin').read_bytes()
    heap = (args.snapshot / 'lvgl-heap.bin').read_bytes()
    if (metadata['sram_base'], len(ram), metadata['lvgl_heap_base'], len(heap)) != (0x1fff8000, 0x2f000, 0x200091e0, 0x8000):
        raise ValueError('Snapshot layout differs from this qualified GUI observation')
    cpu.mem_write(0x1fff8000, ram)
    cpu.mem_write(0x200091e0, heap)
    cpu.mem_map(0x21000000, 0x10000)  # Isolated emulator stack, never device RAM.
    cpu.mem_map(0x42a70000, 0x1000)  # Read-only GPIO aliases in the callback.
    cpu.mem_map(0x40049000, 0x1000)  # Native reader's unlock-register writes.
    cpu.mem_map(0x40048000, 0x1000)  # Real native FCG0 helper, model register storage.
    cpu.mem_map(0x40041000, 0x1000)  # TRNG model (no claim of emulated entropy).
    initial_clock = 0xffffffff if args.rng_mode != 'busy' else 0xffffffff ^ (1 << 22)
    cpu.mem_write(0x40048000, struct.pack('<I', initial_clock))
    if args.rng_mode == 'busy': cpu.mem_write(0x40041000, struct.pack('<I', 1))
    symbols = load_elf(cpu, (args.build / 'overlay207-offline.elf').read_bytes())
    cpu.mem_write(0x39a18, bytes.fromhex('7047'))  # Only RTOS/display handler stub.
    cpu.mem_write(0x40a70, bytes.fromhex('00207047'))  # PD logger timestamp model.
    cpu.mem_write(0x4c888, bytes.fromhex('00207047'))  # PD mailbox RTOS notification model.
    cpu.mem_write(0x468c2, bytes.fromhex('00207047'))  # Hardware wake/orientation: awake.
    cpu.mem_write(0x1fffba0c, b'\0')
    recent, low_sp = [], [0x21010000]
    random_round = 0

    def trace(uc, address, size, user):
        recent.append(address)
        if len(recent) > 12: del recent[0]
        low_sp[0] = min(low_sp[0], uc.reg_read(UC_ARM_REG_SP))
    if args.trace_stack: cpu.hook_add(UC_HOOK_BLOCK, trace)

    def call(address, *arguments):
        nonlocal random_round
        cpu.reg_write(UC_ARM_REG_SP, 0x2100fff0)
        cpu.reg_write(UC_ARM_REG_LR, 0x101)
        for reg, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), arguments):
            cpu.reg_write(reg, value)
        for index, value in enumerate(arguments[4:]):
            cpu.mem_write(0x2100fff0 + index * 4, struct.pack('<I', value))
        try:
            cpu.emu_start(address | 1, 0x100, timeout=45_000_000, count=30_000_000)
        except Exception as error:
            raise RuntimeError(f'Native GUI stopped near {[hex(x) for x in recent]}') from error
        if cpu.reg_read(UC_ARM_REG_PC) != 0x100:
            raise RuntimeError(f'Native GUI did not return near {[hex(x) for x in recent]}')
        if cpu.reg_read(UC_ARM_REG_SP) != 0x2100fff0:
            raise AssertionError('Stack not restored')
        if args.rng_mode in ('complete', 'zero') and bytes(cpu.mem_read(0x40041000, 4)) == struct.pack('<I', 3):
            # A hardware completion between ticks; test values are deterministic.
            random_round += 1
            cpu.mem_write(0x40041000, struct.pack('<I', 1))
            values = (0, 0) if args.rng_mode == 'zero' else (0x12345600 + random_round, 0x89abcdef)
            cpu.mem_write(0x4004100c, struct.pack('<II', *values))
        return cpu.reg_read(UC_ARM_REG_R0)

    def word(address):
        return struct.unpack('<I', cpu.mem_read(address, 4))[0]

    def heap_free():
        address, free, largest = 0x200091e0, 0, 0
        while address < 0x200111dc:
            header = word(address)
            size = header >> 1
            if not header & 1: free += size; largest = max(largest, size)
            if size & 3 or address + 4 + size > 0x200111e0:
                raise AssertionError('Invalid native heap block chain')
            address += 4 + size
        return free, largest

    initial_free = heap_free()
    input_device = call(0x30ab4, 0)
    original_group = word(input_device + 0x5c)
    original_reader = word(input_device + 4)
    if original_reader != 0x24607: raise AssertionError('Expected qualified native keypad')
    cpu.mem_write(0x1fffba20, struct.pack('<I', 0))
    call(symbols['kpd207_gui_tick'])
    for tick in range(1, 13):
        cpu.mem_write(0x1fffba20, struct.pack('<I', tick * 10))
        call(symbols['kpd207_gui_tick'])
    expected_rounds = 11 if args.rng_mode in ('complete', 'zero') else 0
    expected_control = 1 if args.rng_mode == 'busy' else 0
    if random_round != expected_rounds or word(0x40048000) != initial_clock or word(0x40041000) != expected_control or word(0x40041004) != 0:
        raise AssertionError('TRNG warmup or register/clock restoration differs')
    layer = call(0x2a6fe, 0)
    reader = word(input_device + 4)
    if reader == original_reader: raise AssertionError('QR shortcut was not installed')
    if word(input_device + 0x5c) != original_group: raise AssertionError('Boot stole native keys')

    def buttons(keys=(), now=0):
        for key, address in ((27, 0x42a7021c), (10, 0x42a7022c), (20, 0x42a70220), (19, 0x42a70224)):
            cpu.mem_write(address, struct.pack('<I', int(key not in keys)))
        cpu.mem_write(0x1fffba20, struct.pack('<I', now & 0xffffffff))
        cpu.mem_write(0x21000000, b'\xa5' * 16)
        call(reader, input_device, 0x21000000)
        data = bytes(cpu.mem_read(0x21000000, 16))
        return struct.unpack_from('<I', data, 4)[0], data[14]

    # Real native GPIO reader, single-key forwarding and a cancelled short chord.
    for key in (27, 10, 20, 19):
        buttons()
        if buttons((key,)) != (key, 1): raise AssertionError('Single-key behavior changed')
    buttons()
    if buttons((20, 19), 100)[1] != 0: raise AssertionError('Chord leaked a native press')
    buttons((20, 19), 849)
    call(symbols['kpd207_gui_tick'])
    if word(input_device + 0x5c) != original_group: raise AssertionError('Short chord opened QR')
    if buttons((20,), 850)[1] != 0: raise AssertionError('Partial release leaked a press')
    buttons((), 851)
    # Wrap-safe hold timer: 749 ms does not open; 750 ms does.
    buttons((20, 19), 0xffffff00)
    buttons((20, 19), 0xffffff00 + 749)
    call(symbols['kpd207_gui_tick'])
    if word(input_device + 0x5c) != original_group: raise AssertionError('Hold opened early')
    buttons((20, 19), 0xffffff00 + 750)
    call(symbols['kpd207_gui_tick'])
    buttons((), 600)
    image = call(0x34cd0, layer, 0)
    source = call(0x30574, image)
    if bytes(cpu.mem_read(image + 0x10, 8)) != struct.pack('<4h', 0, 0, 239, 239):
        raise AssertionError('QR image does not cover the full display')
    header, size, pixels = struct.unpack('<III', cpu.mem_read(source, 12))
    if header != 7 | (240 << 10) | (240 << 21) or size != 7208:
        raise AssertionError('Expected 240x240 indexed image')
    if word(input_device + 0x5c) == original_group:
        raise AssertionError('Native keypad did not enter QR group')
    # No capture: it must be an informational screen, never a demo QR.
    frozen = pixels + 7212
    if cpu.mem_read(frozen + 2671, 1) != b'\0' or cpu.mem_read(frozen, 4) == b'KPD1':
        raise AssertionError('Empty screen contains an invented QR capture')
    call(0x2f4aa, word(input_device + 0x5c), 27)
    call(symbols['kpd207_gui_tick'])
    if args.rng_mode != 'complete':
        if word(input_device + 0x5c) != original_group: raise AssertionError('RNG failure lost native keys')
        print(f'TRNG {args.rng_mode}: bounded startup, original registers/clock, no invented QR, Exit and native keys passed.')
        return

    fixture = model.new_snapshot('km003c')
    collector = __import__('pdinfo.events', fromlist=['Collector']).Collector(fixture)
    for event in json.loads((ROOT / 'tests/fixtures/stock-pd32-events.json').read_text()): collector.feed(event)
    spr, epr = (bytes.fromhex(fixture['fields'][name]['raw']) for name in ('spr_pdos', 'epr_pdos'))
    context = 0x20000004
    cpu.mem_write(context, b'\0')  # Native PD mode.
    cpu.mem_write(context + 0x29, b'\x24')
    cpu.mem_write(context + 0x3d5, b'\x08\x02\x04')
    cpu.mem_write(context + 0x10c, struct.pack('<H', 0x51a1))
    cpu.mem_write(context + 0x52, struct.pack('<H', 0x8800 | len(epr)))
    cpu.mem_write(context + 0xf0, spr)
    cpu.mem_write(context + 0xa8, epr)
    for now in (1000, 1020):
        cpu.mem_write(0x1fffba20, struct.pack('<I', now))
        call(symbols['kpd207_gui_tick'])
    # End native detection, destroy its cache. Only the owned copy may survive.
    cpu.mem_write(context + 0x29, b'\0')
    cpu.mem_write(context + 0xa8, bytes([255]) * 44)
    cpu.mem_write(context + 0xf0, bytes([255]) * 28)
    call(symbols['kpd207_gui_tick'])
    buttons((20, 19), 1100); buttons((20, 19), 1850)
    call(symbols['kpd207_gui_tick']); buttons((), 1851)
    image = call(0x34cd0, layer, 0)
    source = call(0x30574, image)
    header, size, pixels = struct.unpack('<III', cpu.mem_read(source, 12))
    # Descriptor points to the screen_data pixels, before the frozen transfer.
    frozen = pixels + 7212
    binary_length = struct.unpack('<H', cpu.mem_read(frozen + 2668, 2))[0]
    decoded = transfer.unpack(bytes(cpu.mem_read(frozen, binary_length)))
    if decoded['origin'] != 'km003c' or decoded['fields']['spr_pdos']['raw'] != spr.hex().upper() or decoded['fields']['epr_pdos']['raw'] != epr.hex().upper():
        raise AssertionError('Owned native capture was not frozen into the QR transfer')
    for name in model.FIELDS:
        if decoded['fields'][name]['status'] != ('observed' if name in ('spr_pdos', 'epr_pdos') else 'not_requested'):
            raise AssertionError('Capture invented a response or request status')
    args.output.mkdir(parents=True, exist_ok=True)
    signal = word(image + 0x1c)
    # Three real PDO pages: forward/backward wrap, native decode, immutable copy.
    frames = []
    rasters = []
    decoder_free = []
    for key in (19, 19, 19, 20, 20):
        frames.append(bytes(cpu.mem_read(pixels, size)))
        if len(frames) <= 3:
            decoder = 0x21000100
            if call(0x2fe94, decoder, source, 0) != 1:
                raise AssertionError('Native indexed decoder refused the image')
            raster = bytearray()
            for y in range(240):
                if call(0x2ff72, decoder, 0, y, 240, 0x21001000) != 1:
                    raise AssertionError('Native indexed decoder failed a row')
                row = bytes(cpu.mem_read(0x21001000, 720))
                for x in range(240):
                    if row[x*3+2] != 255: raise AssertionError('Palette is not opaque')
                    raster.extend(row[x*3:x*3+2])
            call(0x2fdf8, decoder)
            decoder_free.append(heap_free()[0])
            rasters.append(raster)
        call(0x2f4aa, word(input_device + 0x5c), key)
        call(symbols['kpd207_gui_tick'])
    if frames[0] != frames[3] or frames[2] != frames[4] or len(set(frames[:3])) != 3:
        raise AssertionError('Page navigation or frozen transfer changed')
    if len(set(decoder_free)) != 1:
        raise AssertionError('Native indexed decoding leaks memory between pages')
    for index, frame in enumerate(frames[:3], 1):
        check_qr_caption(frame, 240, 4)
        (args.output / f'page-{index}.indexed').write_bytes(frame)
    urls = transfer.encode_pages(decoded, transfer.DEFAULT_IMPORT_URL)
    images = [dict(text=url, rgb565=base64.b64encode(raw).decode()) for url, raw in zip(urls, rasters)]
    subprocess.run(['node', str(ROOT / 'tests/check_native.cjs')], input=json.dumps(images), text=True, check=True)
    call(0x2f4aa, word(input_device + 0x5c), 27)
    call(symbols['kpd207_gui_tick'])
    if word(input_device + 0x5c) != original_group:
        raise AssertionError('Exit did not restore native input group')
    after_close = heap_free()
    for _ in range(5): call(symbols['kpd207_gui_tick'])
    if heap_free() != after_close:
        raise AssertionError('Closed screen reallocates or leaks memory')
    # Failed allocation must leave the native group usable and allow later retry.
    allocator = bytes(cpu.mem_read(0x32a94, 4))
    cpu.mem_write(0x32a94, bytes.fromhex('00207047'))
    cpu.ctl_remove_cache(0x32a94, 0x32a98)
    buttons((20, 19), 1000)
    buttons((20, 19), 1750)
    call(symbols['kpd207_gui_tick'])
    buttons((), 1751)
    if word(input_device + 0x5c) != original_group or heap_free() != after_close:
        raise AssertionError(f'Allocation failure: native group restored={word(input_device + 0x5c)==original_group}; free {heap_free()} vs {after_close}')
    cpu.mem_write(0x32a94, allocator)
    cpu.ctl_remove_cache(0x32a94, 0x32a98)
    for cycle in range(3):
        buttons((20, 19), 2000 + cycle * 1000)
        buttons((20, 19), 2750 + cycle * 1000)
        call(symbols['kpd207_gui_tick'])
        buttons((), 2751 + cycle * 1000)
        if word(input_device + 0x5c) == original_group: raise AssertionError('Reopen failed')
        call(0x2f4aa, word(input_device + 0x5c), 27)
        call(symbols['kpd207_gui_tick'])
        if word(input_device + 0x5c) != original_group or heap_free()[0] != after_close[0]:
            raise AssertionError('Repeated open/close leaked memory or input ownership')
    print('Native LVGL: owned SPR/EPR capture after cache destruction, all 3 QRs, navigation, Exit and idle passed.')
    print('Native keypad: individual keys, short chord, hold/timer wrap, allocation failure and 3 reopens passed.')
    if args.trace_stack: print(f'Observed stack depth {0x2100fff0-low_sp[0]} bytes.')
    print(f'Native heap free/largest: before {initial_free}, after Exit {after_close}.')

    # A second detection keeps the QR screen open while extra GETs run. The
    # waiting screen must not export changing bytes under one measurement ID.
    cpu.mem_write(context + 0x29, b'\x24')
    cpu.mem_write(context + 0x3d5, b'\x08\x03\x05')
    cpu.mem_write(context + 0xf0, spr); cpu.mem_write(context + 0xa8, epr)
    for now in (8000, 8020):
        cpu.mem_write(0x1fffba20, struct.pack('<I', now)); call(symbols['kpd207_gui_tick'])
    buttons((20, 19), 8100); buttons((20, 19), 8850)
    call(symbols['kpd207_gui_tick']); buttons((), 8851)
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    pixels = word(source + 8); frozen = pixels + 7212
    if cpu.mem_read(frozen + 2671, 1) != b'\0':
        raise AssertionError('In-progress information capture was exported prematurely')
    events = json.loads((ROOT / 'tests/fixtures/stock-pd32-events.json').read_text())
    def log(raw, sop=0, tx=False):
        if isinstance(raw, str): raw = bytes.fromhex(raw)
        cpu.mem_write(0x2100e000, raw)
        call(0xecfc, context, 0x2100e000, int(tx), sop)
    for index in (0, 1):
        event = events[index]; log(event['wire_hex'], event['sop'], index == 0)
    now = 10020
    cpu.mem_write(0x1fffba20, struct.pack('<I', now)); call(symbols['kpd207_gui_tick'])
    for typ, reply in ((24, 9), (17, 10), (18, 11), (23, 12), (6, 14), (15, None)):
        if cpu.mem_read(context + 0x4c, 1)[0] & 31 != typ:
            raise AssertionError('GUI did not enqueue the expected native request')
        if typ == 6: log(events[13]['wire_hex'], tx=True)
        elif typ == 15: log('8f 10 01 a8 00 ff', tx=True)
        else: log(struct.pack('<H', 0x80 | typ), tx=True)
        log('af 11 81 a8 00 ff' if reply is None else events[reply]['wire_hex'])
        cpu.mem_write(context + 0x27, b'\0')
        now += 5; cpu.mem_write(0x1fffba20, struct.pack('<I', now)); call(symbols['kpd207_gui_tick'])
        now += 150; cpu.mem_write(0x1fffba20, struct.pack('<I', now)); call(symbols['kpd207_gui_tick'])
    binary_length = struct.unpack('<H', cpu.mem_read(frozen + 2668, 2))[0]
    decoded = transfer.unpack(bytes(cpu.mem_read(frozen, binary_length)))
    if decoded['capture_status'] != 'completed' or any(decoded['fields'][name]['status'] != 'pass'
            for name in ('revision', 'source_cap_ext', 'source_status', 'source_info', 'manufacturer')):
        raise AssertionError('GUI did not replace its waiting screen with the complete information QR')
    if cpu.mem_read(frozen + 2670, 1)[0] != 5:
        raise AssertionError('Expected five pages for this reference information result')
    owned_id = decoded['measurement_id']
    owned_binary = bytes(cpu.mem_read(frozen, binary_length))
    # Actual cleanup regression: a new SPR-only receive during native Ready
    # must not remove EPR PDOs or advance the finished measurement's ID.
    cpu.mem_write(context + 0x3d5, b'\0\x04\x06')
    cpu.mem_write(context + 0x4e, struct.pack('<H', 0x51a1))
    cpu.mem_write(context + 0xa8, spr)
    for delta in (20, 40):
        cpu.mem_write(0x1fffba20, struct.pack('<I', now + delta)); call(symbols['kpd207_gui_tick'])
    # A non-PD mode with a stale Ready cache must preserve the PD capture.
    cpu.mem_write(context, b'\x04')
    cpu.mem_write(context + 0x3d6, b'\x08\x09')
    for delta in (60, 80):
        cpu.mem_write(0x1fffba20, struct.pack('<I', now + delta)); call(symbols['kpd207_gui_tick'])
    call(0x2f4aa, word(input_device + 0x5c), 27); call(symbols['kpd207_gui_tick'])
    cpu.mem_write(context + 0x29, b'\0'); call(symbols['kpd207_gui_tick'])
    buttons((20, 19), now + 100); buttons((20, 19), now + 850)
    call(symbols['kpd207_gui_tick']); buttons((), now + 851)
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    reopened = word(source + 8) + 7212
    if bytes(cpu.mem_read(reopened, binary_length)) != owned_binary:
        raise AssertionError('Information result changed after idle/reopen')
    if transfer.unpack(bytes(cpu.mem_read(reopened, binary_length)))['measurement_id'] != owned_id:
        raise AssertionError('Information result ID changed after idle/reopen')
    call(0x2f4aa, word(input_device + 0x5c), 27); call(symbols['kpd207_gui_tick'])
    if word(input_device + 0x5c) != original_group or heap_free()[0] != after_close[0]:
        raise AssertionError('Information screen leaked memory or native input ownership')
    # Exact result-page/table ABI observed in the stock e-Marker screen.
    old_modal = word(0x1fffacd8)
    modal = call(0x346b8, layer, 0); page = call(0x346b8, modal, 0)
    scroll = call(0x346b8, page, 0); table = call(0x346b8, scroll, 0)
    modal_signal, table_signal = word(modal + 0x1c), word(table + 0x1c)
    cpu.mem_write(call(0x34552, modal, 4), struct.pack('<I', page))
    cpu.mem_write(call(0x34552, page, 8), struct.pack('<II', 0, scroll))
    table_ext = call(0x34552, table, 8)
    cpu.mem_write(modal + 0x1c, struct.pack('<I', 0x3c9f9))
    cpu.mem_write(table + 0x1c, struct.pack('<I', 0x38f39))
    cpu.mem_write(page + 0x18, struct.pack('<I', 0x1db7f))
    cpu.mem_write(0x1fffacd8, struct.pack('<I', modal))
    cell_vector = 0x21002000
    cpu.mem_write(table_ext, struct.pack('<HHI', 2, 6, cell_vector))
    for index, name in enumerate(('VER', 'IdHead', 'CertStat', 'Product', 'Cable', 'SVID')):
        label = 0x21002100 + index * 48
        cpu.mem_write(label, b'\x01' + name.encode() + b'\0')
        cpu.mem_write(label + 24, b'\x010x0000 0000\0' if name == 'SVID' else b'\x010x00000000\0')
        cpu.mem_write(cell_vector + index * 8, struct.pack('<II', label, label + 24))
    def compact_urls(data, height=240):
        return [bytes(cpu.mem_read(data + 12 + height * 30 + 153 * i, 153)).split(b'\0', 1)[0].decode()
                for i in range(2)]

    def check_compact_qr(data, urls, height, module_size):
        frames = []
        for url in urls:
            frame = cpu.mem_read(data, 8 + height * 30)
            check_qr_caption(frame, height, module_size)
            raster = bytes(level for byte in frame[8:] for shift in range(7, -1, -1)
                           for level in [0 if byte & (1 << shift) else 255] * 2)
            raster += bytes([255]) * ((240 - height) * 240 * 2)
            frames.append(dict(text=url, rgb565=base64.b64encode(raster).decode()))
            call(0x2f4aa, word(input_device + 0x5c), 19)
            call(symbols['kpd207_gui_tick'])
        subprocess.run(['node', str(ROOT / 'tests/check_native.cjs')], input=json.dumps(frames), text=True, check=True)

    cpu.mem_write(context, b'\x05')
    buttons((20, 19), now + 900); buttons((20, 19), now + 1650)
    call(symbols['kpd207_gui_tick']); buttons((), now + 1651)
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    cable_data = word(source + 8)
    if cpu.mem_read(source + 42, 1) != b'\0':
        raise AssertionError('Zero Identity qualified a cable result')
    svid_cell = 0x21002100 + 5 * 48 + 24
    cpu.mem_write(svid_cell, b'\x010x8087-1e4e\0')
    call(symbols['kpd207_gui_tick'])
    if cpu.mem_read(source + 42, 1) != b'\0':
        raise AssertionError('Malformed SVID qualified a cable result')
    # Exact stock format observed for the EJ903W result with empty Identity.
    cpu.mem_write(svid_cell, b'\x010x8087 1e4e\0')
    call(symbols['kpd207_gui_tick'])
    if cpu.mem_read(source + 42, 1) != b'\x02':
        raise AssertionError('An SVID-only result did not update the open QR screen')
    partial_urls = compact_urls(cable_data)
    partial = transfer.decode_pages(partial_urls)
    if (partial['origin'] != 'km003c_cable' or partial['fields']['cable_identity'] !=
            dict(status='partial', raw='0' * 32 + '4E1E8780',
                 decoded=dict(vdos=['00000000'] * 4, svids=['8087', '1E4E']))):
        raise AssertionError('SVID-only QR misrepresented the result as cable Identity')
    cpu.mem_write(context, b'\x04')
    call(symbols['kpd207_gui_tick'])
    # Set a valid native result after a new scan, with PD cache still empty.
    cpu.mem_write(0x21002100 + 48 + 24, b'\x010x18000000\0')
    cpu.mem_write(0x21002100 + 4 * 48 + 24, b'\x010x00082650\0')
    call(symbols['kpd207_gui_tick'])
    if word(source + 2576) != 0x80871e4e or word(source + 2560) != 0:
        raise AssertionError('A stale table in non-cable mode replaced the saved cable result')
    call(0x2f4aa, word(input_device + 0x5c), 27); call(symbols['kpd207_gui_tick'])
    buttons((20, 19), now + 1660); buttons((20, 19), now + 2410)
    call(symbols['kpd207_gui_tick']); buttons((), now + 2411)
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    if compact_urls(word(source + 8)) != partial_urls:
        raise AssertionError('Leaving the cable test changed the retained SVID-only result')
    call(0x2f4aa, word(input_device + 0x5c), 27); call(symbols['kpd207_gui_tick'])
    cpu.mem_write(context, b'\x05')
    buttons((20, 19), now + 2500); buttons((20, 19), now + 3250)
    call(symbols['kpd207_gui_tick']); buttons((), now + 3251)
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    cable_data = word(source + 8)
    urls = compact_urls(cable_data)
    cable = transfer.decode_pages(urls)
    if (cable['origin'] != 'km003c_cable' or cable['measurement_id'] == owned_id
            or cable['measurement_id'] == partial['measurement_id']
            or cable['fields']['cable_identity']['raw'] != '00000018000000000000000050260800'
            or cable['fields']['spr_pdos']['status'] != 'not_requested'
            or cpu.mem_read(source + 42, 1) != b'\x02'):
        raise AssertionError('Valid standalone cable response did not produce an independent two-page QR')
    check_compact_qr(cable_data, urls, 240, 4)
    call(0x2f4aa, word(input_device + 0x5c), 27); call(symbols['kpd207_gui_tick'])
    buttons((20, 19), now + 3300); buttons((20, 19), now + 4050)
    call(symbols['kpd207_gui_tick']); buttons((), now + 4051)
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    if compact_urls(word(source + 8)) != urls:
        raise AssertionError('Reopen changed the standalone cable QR')
    call(0x2f4aa, word(input_device + 0x5c), 27); call(symbols['kpd207_gui_tick'])
    cpu.mem_write(0x1fffacd8, struct.pack('<I', old_modal))
    cpu.mem_write(modal + 0x1c, struct.pack('<I', modal_signal))
    cpu.mem_write(table + 0x1c, struct.pack('<I', table_signal))
    cpu.mem_write(page + 0x18, bytes(4))
    call(0x34a0a, modal)
    # A new real PD scan cannot adopt that cable-only cache. It needs a new
    # receive and two stable observations, then obtains a different ID.
    cpu.mem_write(context, b'\0')
    cpu.mem_write(context + 0x29, b'\x24')
    cpu.mem_write(0x1fffba20, struct.pack('<I', now + 4100)); call(symbols['kpd207_gui_tick'])
    if cpu.mem_read(source + 56 + 179, 1)[0] & 4 or cpu.mem_read(source + 248 + 2278, 1)[0]:
        raise AssertionError('New PD scan adopted a cable-only cache or old completed result')
    cpu.mem_write(context + 0x3d6, b'\x09\x0a')
    for delta in (4120, 4140):
        cpu.mem_write(0x1fffba20, struct.pack('<I', now + delta)); call(symbols['kpd207_gui_tick'])
    if not cpu.mem_read(source + 56 + 179, 1)[0] & 4 or bytes(cpu.mem_read(source + 56 + 156, 8)) == bytes.fromhex(owned_id):
        raise AssertionError('Repeated native PD scan did not capture a fresh measurement ID')
    # Match the slow source: a reset occurs during SPR, before the first GET.
    log('8d 01')
    call(symbols['kpd207_gui_tick'])
    if bytes(cpu.mem_read(source + 248 + 2152, 1)) != b'\x07':
        raise AssertionError('Reset during SPR did not return to waiting for EPR')
    buttons((20, 19), now + 4200); buttons((20, 19), now + 4950)
    call(symbols['kpd207_gui_tick']); buttons((), now + 4951)
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    late_data = word(source + 8)
    if cpu.mem_read(source + 42, 1) != b'\0':
        raise AssertionError('Opening during the EPR grace period exposed a partial QR')
    cpu.mem_write(context + 0x3d5, b'\x08\x09\x0b')
    cpu.mem_write(context + 0xf0, spr)
    cpu.mem_write(context + 0xa8, epr)
    for delta in (6000, 6020, 6269):
        cpu.mem_write(0x1fffba20, struct.pack('<I', now + delta))
        call(symbols['kpd207_gui_tick'])
        if cpu.mem_read(context + 0x27, 1)[0] & 5:
            raise AssertionError('The first GET was enqueued before EPR settled')
    cpu.mem_write(0x1fffba20, struct.pack('<I', now + 6270))
    call(symbols['kpd207_gui_tick'])
    if not cpu.mem_read(context + 0x27, 1)[0] & 5 or cpu.mem_read(context + 0x4c, 1)[0] & 31 != 24:
        raise AssertionError('The first GET was not enqueued after stable EPR')
    # Ending the native scan still exports the full PDO result.
    cpu.mem_write(context, b'\x04')
    cpu.mem_write(context + 0x29, b'\0')
    call(symbols['kpd207_gui_tick'])
    frozen = late_data + 7212
    size = struct.unpack('<H', cpu.mem_read(frozen + 2668, 2))[0]
    result = transfer.unpack(bytes(cpu.mem_read(frozen, size)))
    if result['fields']['epr_pdos'].get('raw') != epr.hex().upper():
        raise AssertionError('Late EPR did not reach the held result and open QR')
    cpu.mem_write(context, b'\x04')
    cpu.mem_write(context + 0x29, b'\0')
    call(0x2f4aa, word(input_device + 0x5c), 27); call(symbols['kpd207_gui_tick'])
    buttons((20, 19), now + 6300); buttons((20, 19), now + 7050)
    call(symbols['kpd207_gui_tick']); buttons((), now + 7051)
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    if transfer.unpack(bytes(cpu.mem_read(word(source + 8) + 7212, size))) != result:
        raise AssertionError('Late EPR or ID changed after the native scan ended')
    call(0x2f4aa, word(input_device + 0x5c), 27); call(symbols['kpd207_gui_tick'])
    # A live trigger screen had only 6,648 contiguous free bytes on the device.
    blocker = call(0x32a94, heap_free()[1] - 6648 - 4)
    if not blocker or not 6200 <= heap_free()[1] <= 6648:
        raise AssertionError(f'Fixture did not reproduce the trigger heap: {heap_free()}')
    # A settled native PPS contract, with the target and wire RDO copies equal.
    cpu.mem_write(context, b'\0')
    cpu.mem_write(context + 3, b'\x20')
    cpu.mem_write(context + 0x27, b'\0')
    cpu.mem_write(context + 0x29, b'\x24')
    cpu.mem_write(context + 0x3d5, b'\x00\x0a\x0c')
    cpu.mem_write(context + 0x4e, struct.pack('<H', 0x21a1))
    cpu.mem_write(context + 0xa8, struct.pack('<II', 0x0a11912c, 0xc9a43264))
    cpu.mem_write(context + 0x114, struct.pack('<II', 0x2301fe3c, 0x2301fe3c))
    if buttons((19,), now + 7080)[1] != 0 or buttons((), now + 7100) != (19, 1):
        raise AssertionError('A short trigger arrow tap was not forwarded once')
    if buttons((20,), now + 7120)[1] != 0:
        raise AssertionError('The first PPS shortcut arrow leaked into the native slider')
    if buttons((20, 19), now + 7200)[1] != 0:
        raise AssertionError('The second PPS shortcut arrow leaked into the native slider')
    # A native policy transition during the hold must not select the power QR.
    cpu.mem_write(context + 0x29, b'\x23')
    buttons((20, 19), now + 7950)
    call(symbols['kpd207_gui_tick']); buttons((), now + 7951)
    call(symbols['kpd207_gui_tick'])
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    if word(source + 4) != 5768 or cpu.mem_read(context + 0x27, 1)[0] & 5:
        raise AssertionError('Manual PPS did not wait for the native policy on the compact screen')
    cpu.mem_write(context + 0x29, b'\x24')
    call(symbols['kpd207_gui_tick'])
    call(symbols['kpd207_gui_tick'])
    if cpu.mem_read(context + 0x4c, 1)[0] & 31 != 20 or not cpu.mem_read(context + 0x27, 1)[0] & 5:
        raise AssertionError('Manual PPS did not enqueue Get_PPS_Status')
    log('94 00', tx=True); log('8c a1 04 00 fe 00 00 02 00 00')
    call(symbols['kpd207_gui_tick'])
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    header, image_bytes, pixels = struct.unpack('<III', cpu.mem_read(source, 12))
    if header != 7 | (240 << 10) | (192 << 21) or image_bytes != 5768:
        raise AssertionError('Manual PPS did not use the small image')
    urls = compact_urls(pixels, 192)
    manual = transfer.decode_pages(urls)
    if manual['origin'] != 'km003c_pps' or manual['fields']['pps_status']['raw'] != '3CFE0123FE000002':
        raise AssertionError('Manual PPS QR did not include the requested and reported values')
    check_compact_qr(pixels, urls, 192, 3)
    call(0x2f4aa, word(input_device + 0x5c), 27); call(symbols['kpd207_gui_tick'])
    if word(input_device + 0x5c) != original_group:
        raise AssertionError('Manual PPS Exit did not restore the trigger group')
    # On the device the focused stock slider and range outlive the PD cache.
    modal, modal_ext, focus_link, slider, owner, settings, label = (
        0x21001000 + i * 0x100 for i in range(7))
    old_modal = word(0x1fffacd8)
    old_focus = word(original_group + 0xc)
    for addr, value in ((0x1fffacd8, modal), (modal + 0x1c, 0x3c9f9), (modal + 0x24, modal_ext),
                        (modal_ext + 0x10, 0x3f0d5), (original_group + 0xc, focus_link),
                        (focus_link, slider), (slider + 0x18, 0x1b51b), (slider + 0x4c, owner),
                        (owner + 4, settings), (settings + 8, label)):
        cpu.mem_write(addr, struct.pack('<I', value))
    cpu.mem_write(context + 0x27, b'\0')
    cpu.mem_write(context + 3, b'\x62')
    cpu.mem_write(context + 0x29, b'\x1e')
    cpu.mem_write(context + 0x4e, bytes(2))
    cpu.mem_write(context + 0xa8, bytes(28))
    cpu.mem_write(context + 0x114, bytes(8))
    for tick, avs in ((7960, b'15.00-48.00V240W\0'), (7980, b'9-20V3.00A,5.00A\0')):
        cpu.mem_write(label, avs)
        if buttons((19,), now + tick) != (19, 1):
            raise AssertionError('An AVS slider tap was mistaken for PPS')
        buttons((), now + tick + 1)
    cpu.mem_write(label, b'5.00-21.00V 5.00A\0')
    buttons((20, 19), now + 8000); buttons((20, 19), now + 8750)
    call(symbols['kpd207_gui_tick']); buttons((), now + 8751)
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    if word(source + 4) != 5768:
        raise AssertionError('Focused PPS slider with an empty cache did not open the compact screen')
    pixels = word(source + 8)
    waiting_image = bytes(cpu.mem_read(pixels, 5768))
    buttons((), now + 11250); call(symbols['kpd207_gui_tick'])
    if bytes(cpu.mem_read(pixels, 5768)) == waiting_image or cpu.mem_read(context + 0x27, 1)[0] & 5:
        raise AssertionError('Unsettled PPS did not reach the no-contract screen without sending')
    call(0x2f4aa, word(input_device + 0x5c), 27); call(symbols['kpd207_gui_tick'])
    if word(input_device + 0x5c) != original_group:
        raise AssertionError('No-contract PPS screen did not restore the trigger group')
    cpu.mem_write(original_group + 0xc, struct.pack('<I', old_focus))
    cpu.mem_write(0x1fffacd8, struct.pack('<I', old_modal))
    call(0x32b80, blocker)
    print('Native GUI information: waiting screen, six native requests, five-page completion, retained ID, Exit and reopen passed.')
    print('Standalone e-Marker: rejects zero and out-of-mode result tables; exports a stable independent two-page QR.')
    print('Native mode isolation: a subsequent PD scan rejects stale cache and obtains a fresh ID.')
    print('Delayed EPR: keep waiting through a pre-request reset, enqueue after stable EPR, and keep PDOs after exit.')
    print('Manual PPS: native trigger cache, Get_PPS_Status, and compact two-page result.')


if __name__ == '__main__':
    main()
