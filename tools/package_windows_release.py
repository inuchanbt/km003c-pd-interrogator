"""Package explicitly named, stock-free files for the Windows release."""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = 'km003c-pd-interrogator-windows-x64.zip'
PROJECT_FILES = (
    'LICENSE', 'THIRD_PARTY_NOTICES.md', 'docs/INSTALL.md', 'docs/INSTALL.ja.md',
    'native-host/README.md', 'native-host/README.ja.md',
    'pdinfo/__init__.py', 'pdinfo/overlay207.py', 'tools/prepare_overlay207.py',
)


def dependency_notices(metadata_files):
    files, inventory = {}, {}
    for metadata in metadata_files:
        for package in json.loads(metadata.read_text(encoding='utf-8'))['packages']:
            folder = Path(package['manifest_path']).parent
            name = f"{package['name']}-{package['version']}"
            inventory[name] = package['license'] or package['license_file'] or 'See included notices'
            for item in folder.iterdir():
                if item.is_file() and item.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'COPYRIGHT', 'NOTICE')):
                    files[f'licenses/{name}/{item.name}'] = item
            if package['license_file']:
                item = folder / package['license_file']
                files[f'licenses/{name}/{item.name}'] = item
    return files, inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executables', type=Path, required=True)
    parser.add_argument('--overlay', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True, action='append')
    parser.add_argument('--runtime-license', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    files = {name: ROOT / name for name in PROJECT_FILES}
    for name in ('km003c-overlay-host.exe', 'km003c-backup.exe'):
        files[name] = args.executables / name
    for name in ('gate.bin', 'extension.bin', 'build.json'):
        files[f'overlay/{name}'] = args.overlay / name
    files['licenses/km003c-backup-0.1.0/LICENSE'] = ROOT / '.deps/native-host/backup/LICENSE'
    for name in ('LICENSE-APACHE', 'LICENSE-MIT'):
        files[f'licenses/km003c-rs/{name}'] = ROOT / '.deps/native-host/km003c-rs' / name
    notices, inventory = dependency_notices(args.metadata)
    files.update(notices)
    for index, source in enumerate(args.runtime_license, 1):
        files[f'licenses/toolchain/{index}-{source.name}'] = source

    report = json.loads(files['overlay/build.json'].read_text(encoding='utf-8'))
    extension = files['overlay/extension.bin'].read_bytes()
    if (len(extension) != report['extension_bytes'] or len(extension) > 0x4000
            or hashlib.sha256(extension).hexdigest() != report['extension_sha256']
            or files['overlay/gate.bin'].stat().st_size != report['gate_bytes']):
        parser.error('Overlay does not match the build report or the dedicated update range')

    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / ARCHIVE
    with ZipFile(archive, 'x', compression=ZIP_DEFLATED, compresslevel=9) as zip_file:
        zip_file.writestr('START-HERE.md', '# KM003C PD Interrogator for Windows\n\n'
                          '[English installation guide](docs/INSTALL.md) · [日本語](docs/INSTALL.ja.md)\n')
        zip_file.writestr('licenses/cargo-inventory.json', json.dumps(inventory, indent=2, sort_keys=True) + '\n')
        for name, source in sorted(files.items()):
            zip_file.writestr(name, source.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(archive)


if __name__ == '__main__':
    main()
