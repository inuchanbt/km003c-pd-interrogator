"""Validate an owned PDO result in a PRIVATE, non-atomic GUI RAM snapshot.

No USB access. Input must come from native-host inspect-gui on the qualified
capture build. Never publish the complete RAM, heap, or generated report.
"""
import argparse
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pdinfo import model, transfer
from pdinfo.events import Collector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--expect-empty', action='store_true')
    parser.add_argument('--compare-fixture', action='store_true')
    parser.add_argument('--require-current', action='store_true', help='Require opened QR to equal the latest owned capture')
    parser.add_argument('--information', action='store_true', help='Decode the information-build anchor ABI (2160 bytes at +248)')
    parser.add_argument('--request-trace', action='store_true', help='Read the extra 32-byte TX diagnostic in the revised information build')
    parser.add_argument('--completed-pdos', action='store_true', help='Read the completion-time PDO/ID copy in the final information build')
    args = parser.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to((ROOT / 'artifacts').resolve()):
        parser.error('Report must be a NEW file inside ignored artifacts/')
    meta = json.loads((args.snapshot / 'gui-snapshot.json').read_text())
    ram = bytearray((args.snapshot / 'sram.bin').read_bytes())
    heap = (args.snapshot / 'lvgl-heap.bin').read_bytes()
    if (meta['sram_base'], len(ram), meta['lvgl_heap_base'], len(heap)) != (0x1fff8000, 0x2f000, 0x200091e0, 0x8000):
        raise ValueError('Unsupported private snapshot layout')
    ram[0x111e0:0x191e0] = heap  # 0x200091e0 - SRAM base

    def read(address, size):
        offset = address - 0x1fff8000
        if offset < 0 or offset + size > len(ram): raise ValueError('Pointer outside private SRAM')
        return bytes(ram[offset:offset + size])

    def word(address):
        return struct.unpack('<I', read(address, 4))[0]

    layer = word(word(0x1fffaef8) + 0x58)
    obj = word(layer + 8)
    state = None
    for _ in range(1024):
        if not obj: break
        if 0x74000 < word(obj + 0x1c) < 0x78000 and read(obj + 0x34, 1)[0] & 16:
            state = word(obj + 0x24)
            break
        obj = word(obj + 84)
    if state is None or not 0x200091e4 <= state <= 0x200111e0 - 248:
        raise ValueError('Capture-build anchor not found')
    capture = read(state + 56, 180)
    flags = capture[179]
    rng_phase, discarded = read(state + 244, 2)
    if not flags & 8 or rng_phase != 2 or discarded != 11:
        raise AssertionError('Native RNG seed/restoration did not complete')
    held = read(state + 248 + 2192, 88) if args.completed_pdos else None
    if held and held[86] > 1: raise AssertionError('Invalid completion-copy flag')
    ready = bool(flags & 4) or bool(held and held[86])
    if ready == args.expect_empty: raise AssertionError('Unexpected owned capture readiness')
    report = dict(schema=1, usb_access=False, atomic_snapshot=False,
                  seed_ready=True, rng_restored=True, capture_ready=ready)
    if ready:
        result = held[:78] if held and held[86] else capture[78:156]
        spr_len, epr_len = result[4:6]
        if not 0 < spr_len <= 28 or spr_len % 4 or epr_len > 44 or epr_len % 4:
            raise AssertionError('Owned PDO lengths outside supported bounds')
        identifier = held[78:86] if held and held[86] else capture[156:164]
        snapshot = model.new_snapshot('km003c', identifier.hex())
        if held: report['completion_pdos_held'] = bool(held[86])
        model.set_field(snapshot, 'spr_pdos', 'observed', result[6:6 + spr_len])
        if epr_len: model.set_field(snapshot, 'epr_pdos', 'observed', result[34:34 + epr_len])
        snapshot['captured_at'] = None
        if args.information:
            info = read(state + 248, 2160)
            if info[2155] >= len(model.CAPTURE_STATES): raise AssertionError('Invalid capture status')
            snapshot['capture_status'] = model.CAPTURE_STATES[info[2155]]
            for index, name in enumerate(model.FIELDS[2:]):
                f = info[index * 268:(index + 1) * 268]
                length = struct.unpack_from('<H', f, 260)[0]
                status, present = f[266:268]
                if length > 260 or status >= len(model.STATES) or present > 1 or (length and not present):
                    raise AssertionError('Invalid owned information field')
                model.set_field(snapshot, name, model.STATES[status], f[:length] if present else None)
            report['information_stage'] = info[2152]
            report['information_attached'] = bool(info[2157])
            report['field_states'] = {name: f['status'] for name, f in snapshot['fields'].items()}
            if args.request_trace:
                diag = read(state + 248 + 2160, 32)
                if diag[0] > 30: raise AssertionError('Invalid TX trace length')
                report['last_native_tx'] = diag[1:1 + diag[0]].hex()
                if diag[31]: report['native_cable_emulation'] = diag[31]
        report.update(snapshot=snapshot, spr_count=spr_len // 4, epr_count=epr_len // 4)
        if args.compare_fixture:
            expected = model.new_snapshot('km003c')
            collector = Collector(expected)
            for event in json.loads((ROOT / 'tests/fixtures/stock-pd32-events.json').read_text()): collector.feed(event)
            for field in ('spr_pdos', 'epr_pdos'):
                if snapshot['fields'][field].get('raw') != expected['fields'][field].get('raw'):
                    raise AssertionError('Owned PDO bytes differ from reference wire data')
            report['reference_wire_pdos_match'] = True
        data = word(state + 36)
        if data and data == word(state + 8):  # Current GUI stores the pixels before the payload.
            compact = read(state + 43, 1)[0] or read(data + 7209, 1)[0]
            data = 0 if compact else data + 7212
        if data and read(data + 2671, 1)[0]:
            length = struct.unpack('<H', read(data + 2668, 2))[0]
            if not 14 <= length <= 2664: raise AssertionError('Invalid frozen transfer length')
            frozen = transfer.unpack(read(data, length))
            report['frozen_transfer_matches_owned'] = frozen == snapshot
            if args.require_current and frozen != snapshot:
                raise AssertionError('Opened QR differs from latest owned capture')
            report['frozen_snapshot'] = frozen
            report['pages'] = len(transfer.encode_pages(frozen, transfer.DEFAULT_IMPORT_URL))
        elif data:
            report['waiting_screen'] = True
            if args.require_current: raise AssertionError('QR transfer is not frozen yet')
        # The native source cache may now differ. It is deliberately not used.
        report['native_phase'] = read(0x2000002d, 1)[0]
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('snapshot', 'frozen_snapshot')}))


if __name__ == '__main__':
    main()
