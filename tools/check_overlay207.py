"""Emulate the real overlay gate and patched call sites, with native GUI stubs.

Checks callback fallback, task registers, startup return and native call-through.
No stock binary is needed, no MMIO is mapped, and no USB is opened.
"""
import argparse
from pathlib import Path
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pdinfo import overlay207 as o
from tools.check_native_arm import load_elf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build', type=Path)
    args = parser.parse_args()
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC,
                                  UC_ARM_REG_R0, UC_ARM_REG_R4, UC_ARM_REG_R5,
                                  UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8,
                                  UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11)
    image = (args.build / 'overlay207-offline.elf').read_bytes()
    gate, extension = [(args.build / name).read_bytes() for name in ('gate.bin', 'extension.bin')]
    header = o.extension_header(extension)
    variants = [('valid', extension, header['gui_tick']), ('missing', b'\xff' * len(extension), o.GUI_HANDLER)]
    # Every header word and the final code byte must be protected by the guard.
    for offset in [*range(0, 64, 4), len(extension) - 1]:
        bad = bytearray(extension)
        bad[offset] ^= 1
        variants.append((f'bitflip-{offset}', bad, o.GUI_HANDLER))
    for length in (0, 64, 65, 0xffffffff, 0x8004):
        bad = bytearray(extension)
        struct.pack_into('<I', bad, 12, length)
        variants.append((f'length-{length}', bad, o.GUI_HANDLER))
    for entry in (0, o.TAIL + 64, o.TAIL + len(extension) + 1, 0x39a19):
        bad = bytearray(extension)
        struct.pack_into('<I', bad, 20, entry)
        bad[16:20] = bytes(4)
        struct.pack_into('<I', bad, 16, zlib.crc32(bad))
        variants.append((f'entry-{entry}', bad, o.GUI_HANDLER))

    for name, tail, expected in variants:
        cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        cpu.mem_map(0, 0x80000)
        cpu.mem_write(0, b'\xff' * 0x80000)
        cpu.mem_map(0x1fff8000, 0x2f000)
        symbols = load_elf(cpu, image)
        if symbols['kpd207_task_gate'] != o.GATE + 1:
            raise AssertionError('Gate entry moved from the patch profile')
        cpu.mem_write(o.GATE, gate)
        cpu.mem_write(o.TAIL, bytes(tail))
        cpu.mem_write(o.GUI_ENTRY, o.branch_w(o.GUI_ENTRY, o.GATE) + bytes.fromhex('00bf00bf'))
        cpu.mem_write(o.GUI_LOOP, o.LOOP_AFTER)
        # Native LVGL handler is a stub; only overlay/control flow is executed.
        cpu.mem_write(o.GUI_HANDLER - 1, bytes.fromhex('7047'))  # BX LR
        stack, argument = 0x20026000, 0x12345678
        cpu.reg_write(UC_ARM_REG_SP, stack)
        cpu.reg_write(UC_ARM_REG_LR, 0x101)
        cpu.reg_write(UC_ARM_REG_R0, argument)
        saved = (UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11)
        for reg in saved:
            cpu.reg_write(reg, 0x10203040 + reg)
        cpu.emu_start(o.GUI_ENTRY | 1, o.GUI_ENTRY + 8, timeout=5_000_000, count=2_000_000)
        if cpu.reg_read(UC_ARM_REG_PC) != o.GUI_ENTRY + 8:
            raise AssertionError(f'{name}: did not resume original GUI initialization')
        for reg, value in ((UC_ARM_REG_R8, expected), (UC_ARM_REG_R0, argument),
                           (UC_ARM_REG_SP, stack), (UC_ARM_REG_LR, 0x101),
                           (UC_ARM_REG_R4, 0x42a70224)):
            if cpu.reg_read(reg) != value:
                raise AssertionError(f'{name}: task ABI/register mismatch: {reg}')
        for reg in saved:
            if cpu.reg_read(reg) != 0x10203040 + reg:
                raise AssertionError(f'{name}: callee-saved register changed')
        calls = []
        cpu.hook_add(UC_HOOK_CODE, lambda uc, address, size, user: calls.append(address)
                     if address == o.GUI_HANDLER - 1 else None)
        for tick in range(3):
            cpu.emu_start(o.GUI_LOOP | 1, o.GUI_LOOP + 4, timeout=1_000_000, count=10000)
            if cpu.reg_read(UC_ARM_REG_PC) != o.GUI_LOOP + 4 or cpu.reg_read(UC_ARM_REG_R8) != expected:
                raise AssertionError(f'{name}: tick did not return or callback register changed')
            if cpu.reg_read(UC_ARM_REG_SP) != stack:
                raise AssertionError(f'{name}: tick leaked stack')
            for reg in saved:
                if cpu.reg_read(reg) != 0x10203040 + reg:
                    raise AssertionError(f'{name}: native tick changed a saved register')
        if len(calls) != 3:
            raise AssertionError(f'{name}: native handler must execute exactly once per tick')
    print(f'{len(variants)} ARM gate cases: valid/fallback, task ABI, 3 native GUI ticks each passed.')
    print('Native GUI/RTOS initialization, PD timing and physical hardware are not emulated here.')


if __name__ == '__main__':
    main()
