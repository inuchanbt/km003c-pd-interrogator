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
    symbols = load_elf(cpu, (args.build / 'overlay207-offline.elf').read_bytes())
    cpu.mem_write(0x39a18, bytes.fromhex('7047'))  # Only RTOS/display handler stub.
    cpu.mem_write(0x468c2, bytes.fromhex('00207047'))  # Hardware wake/orientation: awake.
    cpu.mem_write(0x1fffba0c, b'\0')
    recent, low_sp = [], [0x21010000]

    def trace(uc, address, size, user):
        recent.append(address)
        if len(recent) > 12: del recent[0]
        low_sp[0] = min(low_sp[0], uc.reg_read(UC_ARM_REG_SP))
    if args.trace_stack: cpu.hook_add(UC_HOOK_BLOCK, trace)

    def call(address, *arguments):
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
    call(symbols['kpd207_gui_tick'])
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
    args.output.mkdir(parents=True, exist_ok=True)
    signal = word(image + 0x1c)
    # Two-page demo: stable frames, right/left wrap and Exit ownership restoration.
    frames = []
    rasters = []
    decoder_free = []
    for key in (19, 20, 20, 19):
        frames.append(bytes(cpu.mem_read(pixels, size)))
        if len(frames) <= 2:
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
    if frames[0] != frames[2] or frames[1] != frames[3] or frames[0] == frames[1]:
        raise AssertionError('Page navigation or frozen transfer changed')
    if decoder_free[0] != decoder_free[1]:
        raise AssertionError('Native indexed decoding leaks memory between pages')
    for index, frame in enumerate(frames[:2], 1):
        (args.output / f'page-{index}.indexed').write_bytes(frame)
    demo = model.new_snapshot('demo', '44454d4f00000001')
    urls = transfer.encode_pages(demo, transfer.DEFAULT_IMPORT_URL)
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
    print('Native LVGL QR open, indexed decoder, page navigation, Exit and idle passed.')
    print('Native keypad: individual keys, short chord, hold/timer wrap, allocation failure and 3 reopens passed.')
    if args.trace_stack: print(f'Observed stack depth {0x2100fff0-low_sp[0]} bytes.')
    print(f'Native heap free/largest: before {initial_free}, after Exit {after_close}.')


if __name__ == '__main__':
    main()
