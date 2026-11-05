"""Create a PRIVATE, offline stock patch candidate and rollback erase pass.

Not a flasher/container packager. The generic updater rejects these images;
the exact-build experimental profile is provided separately by native-host/.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pdinfo import overlay207 as o


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', required=True, type=Path)
    parser.add_argument('--build', type=Path, default=ROOT / 'artifacts/overlay207')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/overlay207-private')
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / 'artifacts').resolve()) or args.output.exists():
        parser.error('Private output must be a NEW directory inside ignored artifacts/')
    manifest = json.loads((args.stock / 'manifest.json').read_text())
    identity = manifest['identity']
    if [identity.get(key) for key in ('model', 'hardware', 'firmware')] != ['KM003C', '2.3', '2.0.7']:
        parser.error('Unexpected qualified backup identity')
    inputs = []
    for name in ('stock-app.bin', 'loader-00000004-00003fff.bin', 'internal-tail-00070000.bin'):
        data = (args.stock / name).read_bytes()
        record = next(row for row in manifest['regions'] if row['file'] == name)
        if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256'] or record['matching_reads'] < 2:
            parser.error(f'Backup manifest mismatch: {name}')
        inputs.append(data)
    gate, extension = [(args.build / name).read_bytes() for name in ('gate.bin', 'extension.bin')]
    candidate, erase_pass = o.make_candidate(*inputs, gate, extension)
    args.output.mkdir(parents=True)
    for name, data in [('candidate-uncommitted.bin', candidate), ('rollback-erase-pass-uncommitted.bin', erase_pass)]:
        (args.output / name).write_bytes(data)
    report = dict(schema=1, status='offline raw candidate; use the exact-build native-host profile for packaging',
                  stock_sha256=o.STOCK_HASH, loader_sha256=o.LOADER_HASH,
                  application_base=hex(o.BASE), candidate_end=hex(o.BASE + len(candidate)),
                  candidate_bytes=len(candidate), erase_end=hex((o.BASE + len(candidate) + 0x1fff) & ~0x1fff),
                  candidate_sha256=hashlib.sha256(candidate).hexdigest(),
                  original_code_patches=[dict(address=hex(o.GUI_ENTRY), bytes=8), dict(address=hex(o.GUI_LOOP), bytes=4)],
                  metadata_uncommitted='size/CRC only, 0x4428..0x442f',
                  gate_bytes=len(gate), extension_bytes=len(extension), mutable_globals=0,
                  rollback='In DFU: erase-pass image, verify cleared extension, then original qualified restore container; full readback required',
                  generic_updater_supported=False, requires_hw23_profile=True, usb_access=False)
    (args.output / 'plan.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
