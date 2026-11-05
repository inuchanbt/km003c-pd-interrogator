"""Build a stock-free, offline HW2.3/stock-2.0.7 integration probe.

Outputs gate and extension binaries, not a firmware update container.
No USB, private firmware or device identity is read by this command.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zlib

from check_native_size import section_sizes

ROOT = Path(__file__).resolve().parents[1]


def allocated_sections(image):
    shoff = struct.unpack_from('<I', image, 32)[0]
    shsize, shnum = struct.unpack_from('<HH', image, 46)
    for index in range(shnum):
        _, kind, flags, address, offset, length, _, _, _, _ = struct.unpack_from('<10I', image, shoff + index * shsize)
        if flags & 2 and length:
            if kind == 8:
                raise ValueError('Probe must not allocate unreserved SRAM')
            yield address, image[offset:offset + length]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clang', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/overlay207')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    common = [str(args.clang), '--no-default-config', '--target=arm-none-eabi',
              '-mcpu=cortex-m4', '-mthumb', '-Oz', '-DNDEBUG', '-ffreestanding',
              '-fno-builtin', '-ffunction-sections', '-fdata-sections',
              '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'native'),
              '-I', str(ROOT / 'native/freestanding/include')]
    sources = [('native/overlay207/gate.S', 'gate_entry', False),
               ('native/overlay207/gate.c', 'gate', False),
               ('native/overlay207/entry.c', 'entry', True),
               ('native/overlay207/gui.c', 'gui', True),
               ('native/overlay207/capture.c', 'capture', True),
               ('native/overlay207/acquire.c', 'acquire', True),
               ('native/kpd.c', 'kpd', True), ('native/runtime.c', 'runtime', True),
               ('native/vendor/qrcodegen/qrcodegen.c', 'qrcodegen', True)]
    objects = []
    for source, name, lto in sources:
        obj = args.output / (name + '.o')
        subprocess.run([*common, *(['-flto'] if lto else []), '-c', str(ROOT / source), '-o', str(obj)], check=True)
        objects.append(str(obj))
    elf = args.output / 'overlay207-offline.elf'
    # Keep compiler-generated runtime references alive through LTO codegen.
    runtime = ['memcpy', 'memset', 'memmove', '__aeabi_memclr4', '__aeabi_memcpy', '__aeabi_memcpy4']
    subprocess.run([*common, '-flto', '-nostdlib', '-fuse-ld=lld', '-Wl,--gc-sections',
                    '-Wl,-T,' + str(ROOT / 'native/overlay207/layout.ld'),
                    '-Wl,-Map=' + str(args.output / 'overlay207.map'),
                    *['-Wl,--undefined=' + name for name in runtime], *objects, '-o', str(elf)], check=True)
    sections = list(allocated_sections(elf.read_bytes()))

    def region(begin, end):
        chosen = [(address, data) for address, data in sections if begin <= address < end]
        last = max(address + len(data) for address, data in chosen)
        if last > end:
            raise ValueError('Section exceeds assigned region')
        data = bytearray(b'\xff' * ((last - begin + 3) & ~3))
        for address, chunk in chosen:
            data[address - begin:address - begin + len(chunk)] = chunk
        return data

    gate, tail = region(0x738c0, 0x74000), region(0x74000, 0x7c000)
    struct.pack_into('<II', tail, 12, len(tail), 0)
    struct.pack_into('<I', tail, 16, zlib.crc32(tail))
    for name, data in [('gate.bin', gate), ('extension.bin', tail)]:
        (args.output / name).write_bytes(data)
    report = dict(schema=1, purpose='native owned PDO capture and QR GUI; no additional GET requests or installer',
                  gate_address='0x738c0', gate_bytes=len(gate),
                  extension_address='0x74000', extension_bytes=len(tail),
                  extension_sha256=hashlib.sha256(tail).hexdigest(),
                  gui_callback=hex(struct.unpack_from('<I', tail, 20)[0]),
                  mutable_globals=0, elf_sections=section_sizes(elf))
    (args.output / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
