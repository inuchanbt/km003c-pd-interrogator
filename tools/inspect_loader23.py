"""Offline evidence for this exact HW2.3 loader; never opens USB.

Executes native scatter-load, flash range routines and the application jump.
Flash-ready/cache registers are a simplified model, NOT a hardware test or a
simulation of USB authentication, encryption, update completion or power loss.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pdinfo.overlay207 import LOADER_HASH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('loader', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/loader23-analysis')
    args = parser.parse_args()
    image = args.loader.read_bytes()
    if len(image) != 0x3ffc or hashlib.sha256(image).hexdigest() != LOADER_HASH:
        parser.error('This tool requires the exact qualified HW2.3 loader backup (address 4)')
    if not args.output.resolve().is_relative_to((ROOT / 'artifacts').resolve()) or args.output.exists():
        parser.error('Choose a NEW private directory inside ignored artifacts/')
    from unicorn import (Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS,
                         UC_HOOK_MEM_WRITE, UC_HOOK_MEM_READ, UC_HOOK_CODE)
    from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS

    def machine():
        cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        cpu.mem_map(0, 0x80000)
        cpu.mem_write(0, b'\xff' * 0x80000)
        cpu.mem_write(4, image)
        cpu.mem_map(0x1fff8000, 0x2f000)
        cpu.reg_write(UC_ARM_REG_SP, 0x20020000)
        cpu.emu_start(0xfd, 0x114, timeout=2_000_000, count=1_000_000)
        if cpu.reg_read(UC_ARM_REG_PC) != 0x114:
            raise AssertionError('Native loader scatter-load did not finish')
        return cpu

    def call(cpu, function, *arguments):
        cpu.reg_write(UC_ARM_REG_SP, 0x20020000)
        cpu.reg_write(UC_ARM_REG_LR, 0x801)
        for register, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2), arguments):
            cpu.reg_write(register, value)
        cpu.emu_start(function | 1, 0x800, timeout=2_000_000, count=1_000_000)
        if cpu.reg_read(UC_ARM_REG_PC) != 0x800:
            raise AssertionError('Native flash function did not return')
        return cpu.reg_read(UC_ARM_REG_R0)

    erase_cases = []
    for start, end, expected in [(0x4000, 0x74ff4, 0), (0x4000, 0x76000, 0),
                                  (0x74000, 0x76000, 0), (0x4000, 0x78000, 0),
                                  (0x76000, 0x78000, 0), (0x3fff, 0x78000, 1), (0x4000, 0x80000, 1)]:
        cpu = machine()
        # Model only always-ready EFM flags, cache bit-band storage and control.
        cpu.mem_map(0x40010000, 0x1000)
        cpu.mem_map(0x42000000, 0x1000000)
        cpu.mem_write(0x40010410, struct.pack('<I', 0x110))
        touched = []

        def flash_write(uc, access, address, size, value, user):
            if address < 0x80000:
                if not 0x4000 <= address < 0x7c000:
                    raise AssertionError('Native routine attempted a protected flash write')
                mode = struct.unpack('<I', uc.mem_read(0x4001040c, 4))[0] >> 4 & 7
                if mode != 4 or value != 0x12345678 or size != 4:
                    raise AssertionError('Unexpected native erase operation')
                touched.append(address)
        cpu.hook_add(UC_HOOK_MEM_WRITE, flash_write)
        result = call(cpu, 0x3f0, start, end)
        if result != expected:
            raise AssertionError('Unexpected native erase range result')
        want = list(range(start, (end + 0x1fff) & ~0x1fff, 0x2000)) if expected == 0 else []
        if touched != want:
            raise AssertionError('Native erase sector coverage differs')
        erase_cases.append(dict(start=hex(start), end=hex(end), result=result,
                                sectors=len(touched), last_sector=hex(touched[-1]) if touched else None))

    cpu = machine()
    cpu.mem_map(0x40010000, 0x1000)
    cpu.mem_map(0x42000000, 0x1000000)
    cpu.mem_write(0x40010410, struct.pack('<I', 0x110))
    sample = bytes(range(32))
    cpu.mem_write(0x20010000, sample)
    if call(cpu, 0x460, 0x74000, 0x74020, 0x20010000) != 0 or bytes(cpu.mem_read(0x74000, 32)) != sample:
        raise AssertionError('Native programming routine did not write the modeled extension bytes')

    jump_cases = []
    for size, stack, valid in [(0x6f8c0, 0x1fffd260, True), (0x70ff4, 0x1fffd260, True),
                               (0xffffffff, 0x1fffd260, True), (0x70ff4, 0x1fff8000, False),
                               (0x70ff4, 0x20020000, False)]:
        cpu = machine()
        cpu.mem_map(0x200f0000, 0x1000)
        cpu.mem_map(0xe000e000, 0x1000)
        cpu.mem_write(0x4000, struct.pack('<II', stack, 0x4295))
        cpu.mem_write(0x4428, struct.pack('<I', size))
        reads = []
        cpu.hook_add(UC_HOOK_MEM_READ, lambda uc, access, address, length, value, user: reads.append(address))
        cpu.hook_add(UC_HOOK_CODE, lambda uc, address, length, user: uc.emu_stop() if address == 0x20fe else None)
        # Start AFTER physical-button selection, on the normal application path.
        cpu.emu_start(0x20c9, 0x4294, count=1000)
        jumped = cpu.reg_read(UC_ARM_REG_PC) == 0x4294
        if jumped != valid or 0x4428 in reads:
            raise AssertionError('Native startup differs from the inspected SP-only acceptance path')
        jump_cases.append(dict(committed_size=hex(size), stack=hex(stack), jumped=jumped))

    report = dict(loader_sha256=LOADER_HASH, hardware='2.3',
                  scatter=dict(entry='0xfc', stop='0x114'),
                  erase_entry='0x3f0 -> RAM 0x1fff8010', program_entry='0x460 -> RAM 0x1fff80ac',
                  erase_cases=erase_cases, modeled_programming_bytes=32, jump_cases=jump_cases,
                  startup_note='At 0x20c8 normal path, committed size is not read; SP bounds gate the jump.',
                  limits='Offline simplified peripheral model only. No USB transfer, hardware programming, native CRC/AES or interruption behavior validated.')
    args.output.mkdir(parents=True)
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    sections = [(0x20a0, 0x2118), (0x3ba4, 0x3c32), (0x3c40, 0x3cca), (0x19cc, 0x19f4)]
    lines = []
    for begin, end in sections:
        lines += [f'{i.address:08x}: {i.mnemonic:9s} {i.op_str}' for i in md.disasm(image[begin - 4:end - 4], begin)] + ['']
    (args.output / 'selected.asm').write_text('\n'.join(lines))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
