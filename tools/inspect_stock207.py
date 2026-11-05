"""Recover the exact stock 2.0.7 scatter-loaded code offline. Never opens USB.

Optional analysis dependencies: unicorn==2.1.4 and capstone==5.0.7.
The supplied firmware must be the owner's private installed application backup.
All generated binary/disassembly evidence stays in the ignored artifacts folder.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
IMAGE_HASH = 'c63057eaf08b8625e7ac2fc7270c06d75515e78fdbdd46080bdf4b696984ec67'
BASE, SRAM, SRAM_SIZE = 0x4000, 0x1fff8000, 0x2f000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('firmware', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/stock207-analysis')
    args = parser.parse_args()
    image = args.firmware.read_bytes()
    if hashlib.sha256(image).hexdigest() != IMAGE_HASH:
        parser.error('Unrecognized image: this analysis only supports the verified installed 2.0.7 build')
    if not args.output.resolve().is_relative_to((ROOT / 'artifacts').resolve()):
        parser.error('Keep private stock-derived output inside the ignored artifacts directory')
    if args.output.exists():
        parser.error('Output exists; choose a new directory')

    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_PROT_ALL, UC_PROT_READ, UC_PROT_EXEC
    from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_PC

    table_begin, table_end = struct.unpack_from('<II', image, 0x4c70 - BASE)
    entries = [struct.unpack_from('<IIII', image, addr - BASE)
               for addr in range(table_begin, table_end, 16)]
    cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    cpu.mem_map(0, 0x80000, UC_PROT_ALL)
    cpu.mem_write(BASE, image)
    cpu.mem_protect(0, 0x80000, UC_PROT_READ | UC_PROT_EXEC)
    cpu.mem_map(SRAM, SRAM_SIZE)
    cpu.reg_write(UC_ARM_REG_SP, struct.unpack_from('<I', image)[0])
    # Execute only native copy/decompression/zero-init. Stop BEFORE main/OS/MMIO.
    cpu.emu_start(0x4c49, 0x4c6a, timeout=5_000_000, count=2_000_000)
    if cpu.reg_read(UC_ARM_REG_PC) != 0x4c6a:
        raise RuntimeError('Scatter-load did not reach its expected exit')
    ram = bytes(cpu.mem_read(SRAM, SRAM_SIZE))
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)

    def disassemble(data, address):
        return [f'{i.address:08x}: {i.bytes.hex():10s} {i.mnemonic:9s} {i.op_str}'
                for i in md.disasm(data, address)]

    report = dict(
        image_sha256=IMAGE_HASH, application_base=hex(BASE), application_length=len(image),
        application_end=hex(BASE + len(image)), qualified_application_limit='0x74000 (exclusive)',
        remaining_application_bytes=0x74000 - BASE - len(image),
        emulation=dict(entry='0x4c48', stop_before_main='0x4c6a', mmio_mapped=False),
        scatter=[dict(source=hex(src), destination=hex(dst), length=length, helper=hex(helper))
                 for src, dst, length, helper in entries],
        ram_code_sha256=hashlib.sha256(ram[:0x7a0]).hexdigest(),
        gui=dict(task_entry='0x1ebb0', task_create_call='0x1100e',
                 loop='0x1ec04', handler_candidate='0x39a18', delay_call='0x4cffc', delay_argument=10,
                 pendsv_entry=hex(struct.unpack_from('<I', image, 14 * 4)[0] & ~1),
                 r8_context='Saved at 0x438a and restored at 0x43a8 with R4-R11'),
        display=dict(ram_flush_candidate='0x1fff8000', pixel_loop='0x1fff8110',
                     mutex_object='0x1fffb4c4', acquire_candidate='0x4cbe6',
                     release_candidate='0x4ccec', completion_candidate='0x2a696',
                     signature='inferred LVGL flush: (display_driver*, area*, RGB565*)'),
        limits='Static/emulated evidence only. No stock UI hook, firmware patch or hardware validation.',
    )
    args.output.mkdir(parents=True)
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    (args.output / 'ram.bin').write_bytes(ram)
    sections = [
        ('display flush', ram[:0x184], SRAM),
        ('RAM-to-flash veneers', ram[0x744:0x7a0], SRAM + 0x744),
        ('GUI task creation', image[0x10f98 - BASE:0x11016 - BASE], 0x10f98),
        ('GUI task', image[0x1ebb0 - BASE:0x1ec14 - BASE], 0x1ebb0),
        ('PendSV task register save/restore', image[0x4376 - BASE:0x43c2 - BASE], 0x4376),
    ]
    lines = []
    for title, data, address in sections:
        lines += [title, *disassemble(data, address), '']
    (args.output / 'selected.asm').write_text('\n'.join(lines))
    print(f'Exact stock build recognized; native scatter-load completed before main.')
    print(f'Application remainder: {report["remaining_application_bytes"]} bytes; private report: {args.output}')


if __name__ == '__main__':
    main()
