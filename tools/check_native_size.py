"""Link a Cortex-M4 size probe, with no stock binary or device access."""
import argparse
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def section_sizes(elf):
    """Count allocated ELF32 sections, as text/data/bss, without an extra tool."""
    image = elf.read_bytes()
    if image[:7] != b'\x7fELF\x01\x01\x01':
        raise ValueError('Expected little-endian ELF32')
    shoff = struct.unpack_from('<I', image, 32)[0]
    shsize, shnum = struct.unpack_from('<HH', image, 46)
    sizes = dict(text=0, data=0, bss=0)
    for index in range(shnum):
        _, kind, flags, _, _, length, _, _, _, _ = struct.unpack_from('<10I', image, shoff + index * shsize)
        if flags & 2:  # SHF_ALLOC
            category = 'bss' if kind == 8 else 'data' if flags & 1 else 'text'
            sizes[category] += length
    return sizes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clang', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/native-size')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    common = [str(args.clang), '--no-default-config', '--target=arm-none-eabi',
              '-mcpu=cortex-m4', '-mthumb', '-std=c11', '-Oz', '-DNDEBUG',
              '-ffreestanding', '-fno-builtin', '-ffunction-sections', '-fdata-sections',
              '-fstack-usage', '-Wall', '-Wextra', '-Werror', '-pedantic',
              '-I', str(ROOT / 'native/freestanding/include'), '-I', str(ROOT / 'native')]
    objects = []
    for source in ('native/kpd.c', 'native/vendor/qrcodegen/qrcodegen.c', 'native/runtime.c', 'native/sizecheck/runtime.c'):
        obj = args.output / ('probe.o' if source == 'native/sizecheck/runtime.c' else Path(source).stem + '.o')
        subprocess.run([*common, '-c', str(ROOT / source), '-o', str(obj)], check=True)
        objects.append(obj)
    elf = args.output / 'sizecheck-only.elf'
    subprocess.run([*common, '-nostdlib', '-fuse-ld=lld',
                    '-Wl,--gc-sections', '-Wl,--entry=kpd_sizecheck', '-Wl,-Ttext=0x10000',
                    '-Wl,-Map=' + str(args.output / 'sizecheck.map'),
                    *map(str, objects), '-o', str(elf)], check=True)
    print(' '.join(f'{name}={length}' for name, length in section_sizes(elf).items()))
    print('Offline size check only; this ELF is not a KM003C firmware image.')


if __name__ == '__main__':
    main()
