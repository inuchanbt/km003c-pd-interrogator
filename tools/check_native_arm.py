"""Run the cross-compiled portable C in an ARM emulator, without hardware.

Requires unicorn==2.1.4. Input is a synthetic size probe or --overlay stock-free
extension ELF, never a stock/DFU image. Also checks decoded RGB565 rows.
"""
import argparse
import base64
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pdinfo import model, transfer
from pdinfo.events import Collector


def load_elf(cpu, image):
    if image[:7] != b'\x7fELF\x01\x01\x01' or struct.unpack_from('<H', image, 18)[0] != 40:
        raise ValueError('Expected little-endian ELF32 ARM size probe')
    phoff, shoff = struct.unpack_from('<II', image, 28)
    phsize, phnum, shsize, shnum = struct.unpack_from('<HHHH', image, 42)
    for index in range(phnum):
        kind, offset, address, _, length, _, _, _ = struct.unpack_from('<8I', image, phoff + index * phsize)
        if kind == 1:
            cpu.mem_write(address, image[offset:offset + length])
    sections = [struct.unpack_from('<10I', image, shoff + i * shsize) for i in range(shnum)]
    symbols = {}
    for section in sections:
        if section[1] != 2:  # SHT_SYMTAB
            continue
        strings_section = sections[section[6]]
        strings = image[strings_section[4]:strings_section[4] + strings_section[5]]
        for offset in range(section[4], section[4] + section[5], section[9]):
            name, value, _, _, _, _ = struct.unpack_from('<IIIBBH', image, offset)
            name = strings[name:strings.index(b'\0', name)].decode()
            symbols[name] = value
    return symbols


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('elf', type=Path)
    parser.add_argument('--overlay', action='store_true', help='Check the stock-free extension ELF instead')
    args = parser.parse_args()
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS
    from unicorn.arm_const import (UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC,
                                  UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)
    cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    cpu.mem_map(0, 0x100000)
    cpu.mem_map(0x1fff8000, 0x2f000)
    symbols = load_elf(cpu, args.elf.read_bytes())
    marker = 'kpd207_header' if args.overlay else 'kpd_sizecheck'
    if marker not in symbols:
        raise ValueError('Expected synthetic size probe or explicitly selected stock-free overlay')

    def call(name, *arguments):
        cpu.reg_write(UC_ARM_REG_SP, 0x20026000)
        cpu.reg_write(UC_ARM_REG_LR, 0x801)
        for register, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2), arguments):
            cpu.reg_write(register, value)
        cpu.emu_start(symbols[name] | 1, 0x800, timeout=5_000_000, count=10_000_000)
        if cpu.reg_read(UC_ARM_REG_PC) != 0x800 or cpu.reg_read(UC_ARM_REG_R0) != 1:
            raise RuntimeError(f'ARM function did not return success: {name}')

    snapshot = model.new_snapshot('km003c', '0000000000000006')
    snapshot['capture_status'] = 'imported'
    collector = Collector(snapshot)
    for event in json.loads((ROOT / 'tests/fixtures/stock-pd32-events.json').read_text()):
        collector.feed(event)
    source, payload, frozen, work, row, packed = (0x20000000, 0x20000100, 0x20002000,
                                                 0x20003000, 0x20004000, 0x20004200)
    # AAPCS32: fields start at offset 12, each field view is 8 bytes.
    data = bytearray(bytes.fromhex(snapshot['measurement_id']) + bytes([0, 4, 0, 0]))
    for name in model.FIELDS:
        field = snapshot['fields'][name]
        raw = bytes.fromhex(field.get('raw', ''))
        cpu.mem_write(payload, raw)
        data.extend(struct.pack('<IHBB', payload, len(raw), model.STATES.index(field['status']), 'raw' in field))
        payload += max(1, len(raw))
    cpu.mem_write(source, bytes(data))
    call('kpd_freeze', source, frozen)
    expected_binary = transfer.pack(snapshot)
    if bytes(cpu.mem_read(frozen, len(expected_binary))) != expected_binary:
        raise AssertionError('ARM TLV differs from host')
    images = []
    for page, expected in enumerate(transfer.encode_pages(snapshot, transfer.DEFAULT_IMPORT_URL), 1):
        call('kpd_encode_page', frozen, page, work)
        actual = bytes(cpu.mem_read(work, 153)).split(b'\0', 1)[0].decode()
        if actual != expected:
            raise AssertionError('ARM URL differs from host')
        call('kpd_qr_pack', work, packed)
        raster = bytearray()
        for y in range(240):
            call('kpd_qr_row', work, y, row)
            raster.extend(cpu.mem_read(row, 480))
        images.append(dict(text=actual, rgb565=base64.b64encode(raster).decode()))
    subprocess.run(['node', str(ROOT / 'tests/check_native.cjs')], input=json.dumps(images),
                   text=True, check=True)
    print(f'ARM Thumb code: TLV and all {len(images)} URLs match Python; QR rows decode.')
    print('Emulation only; no board timing, bus arbitration or PD scheduling validated.')


if __name__ == '__main__':
    main()
