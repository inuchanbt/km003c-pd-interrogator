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
    frozen = pixels - 2672
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
    # Descriptor points to the screen_data pixels, after the frozen transfer.
    frozen = pixels - 2672
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
    pixels = word(source + 8); frozen = pixels - 2672
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
    reopened = word(source + 8) - 2672
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
    cpu.mem_write(table_ext, struct.pack('<HHI', 2, 5, cell_vector))
    for index, name in enumerate(('VER', 'IdHead', 'CertStat', 'Product', 'Cable')):
        label = 0x21002100 + index * 48
        cpu.mem_write(label, b'\x01' + name.encode() + b'\0')
        cpu.mem_write(label + 24, b'\x010x00000000\0')
        cpu.mem_write(cell_vector + index * 8, struct.pack('<II', label, label + 24))
    cpu.mem_write(context, b'\x05')
    buttons((20, 19), now + 900); buttons((20, 19), now + 1650)
    call(symbols['kpd207_gui_tick']); buttons((), now + 1651)
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    cable_data = word(source + 8) - 2672
    if cpu.mem_read(cable_data + 2671, 1) != b'\0':
        raise AssertionError('Zero Identity qualified a cable result')
    cpu.mem_write(context, b'\x04')
    call(symbols['kpd207_gui_tick'])
    # Set a valid native result after a new scan, with PD cache still empty.
    cpu.mem_write(0x21002100 + 48 + 24, b'\x010x18000000\0')
    cpu.mem_write(0x21002100 + 4 * 48 + 24, b'\x010x00082650\0')
    call(0x2f4aa, word(input_device + 0x5c), 27); call(symbols['kpd207_gui_tick'])
    buttons((20, 19), now + 1700); buttons((20, 19), now + 2450)
    call(symbols['kpd207_gui_tick']); buttons((), now + 2451)
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    if word(source + 8) == 0 or bytes(cpu.mem_read(word(source + 8) - 2672, binary_length)) != owned_binary:
        raise AssertionError('A stale table in non-cable mode replaced the PD result')
    call(0x2f4aa, word(input_device + 0x5c), 27); call(symbols['kpd207_gui_tick'])
    cpu.mem_write(context, b'\x05')
    buttons((20, 19), now + 2500); buttons((20, 19), now + 3250)
    call(symbols['kpd207_gui_tick']); buttons((), now + 3251)
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    cable_data = word(source + 8) - 2672
    call(symbols['kpd207_gui_tick'])
    cable_length = struct.unpack('<H', cpu.mem_read(cable_data + 2668, 2))[0]
    cable_binary = bytes(cpu.mem_read(cable_data, cable_length))
    cable = transfer.unpack(cable_binary)
    if (cable['origin'] != 'km003c_cable' or cable['measurement_id'] == owned_id
            or cable['fields']['cable_identity']['raw'] != '00000018000000000000000050260800'
            or cable['fields']['spr_pdos']['status'] != 'not_requested'
            or cpu.mem_read(cable_data + 2670, 1) != b'\x02'):
        raise AssertionError('Valid standalone cable response did not produce an independent two-page QR')
    call(0x2f4aa, word(input_device + 0x5c), 27); call(symbols['kpd207_gui_tick'])
    buttons((20, 19), now + 3300); buttons((20, 19), now + 4050)
    call(symbols['kpd207_gui_tick']); buttons((), now + 4051)
    image = call(0x34cd0, layer, 0); source = call(0x30574, image)
    reopened_cable = word(source + 8) - 2672
    if bytes(cpu.mem_read(reopened_cable, cable_length)) != cable_binary:
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
    print('Native GUI information: waiting screen, six native requests, five-page completion, retained ID, Exit and reopen passed.')
    print('Standalone e-Marker: rejects zero and out-of-mode result tables; exports a stable independent two-page QR.')
    print('Native mode isolation: a subsequent PD scan rejects stale cache and obtains a fresh ID.')


if __name__ == '__main__':
    main()
