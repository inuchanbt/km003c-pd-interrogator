"""Link a Cortex-M4 size probe, with no stock binary or device access."""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


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
              '-I', str(ROOT / 'native/sizecheck/include'), '-I', str(ROOT / 'native')]
    objects = []
    for source in ('native/kpd.c', 'native/vendor/qrcodegen/qrcodegen.c', 'native/sizecheck/runtime.c'):
        obj = args.output / (Path(source).stem + '.o')
        subprocess.run([*common, '-c', str(ROOT / source), '-o', str(obj)], check=True)
        objects.append(obj)
    elf = args.output / 'sizecheck-only.elf'
    subprocess.run([*common, '-nostdlib', '-fuse-ld=lld',
                    '-Wl,--gc-sections', '-Wl,--entry=kpd_sizecheck', '-Wl,-Ttext=0x10000',
                    '-Wl,-Map=' + str(args.output / 'sizecheck.map'),
                    *map(str, objects), '-o', str(elf)], check=True)
    size = args.clang.parent / ('llvm-size.exe' if args.clang.suffix == '.exe' else 'llvm-size')
    subprocess.run([str(size), str(elf)], check=True)
    print('Offline size check only; this ELF is not a KM003C firmware image.')


if __name__ == '__main__':
    main()
