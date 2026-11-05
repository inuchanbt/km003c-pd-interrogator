"""Check native PDO getters against a PRIVATE stock-2.0.7 RAM snapshot.

No hardware access. Retained bytes after native PD exit are reported as stale;
this diagnostic does not turn them into a live measurement or issue PD requests.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pdinfo.events import Collector
from pdinfo.model import new_snapshot
from pdinfo.overlay207 import STOCK_HASH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compare-fixture', action='store_true', help='Assert equality to the recorded reference source')
    args = parser.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to((ROOT / 'artifacts').resolve()):
        parser.error('Private report must be a NEW file inside ignored artifacts/')
    rom = args.stock.read_bytes()
    ram = (args.snapshot / 'sram.bin').read_bytes()
    if hashlib.sha256(rom).hexdigest() != STOCK_HASH or len(ram) != 0x2f000:
        parser.error('Exact qualified ROM and complete private SRAM snapshot required')
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS
    from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0
    cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    cpu.mem_map(0, 0x80000)
    cpu.mem_write(0x4000, rom)
    cpu.mem_map(0x1fff8000, 0x2f000)
    cpu.mem_write(0x1fff8000, ram)
    cpu.mem_map(0x21000000, 0x1000)

    def call(address):
        cpu.reg_write(UC_ARM_REG_SP, 0x21001000)
        cpu.reg_write(UC_ARM_REG_LR, 0x101)
        cpu.emu_start(address | 1, 0x100, timeout=1_000_000, count=1000)
        if cpu.reg_read(UC_ARM_REG_PC) != 0x100: raise AssertionError('Native getter failed to return')
        return cpu.reg_read(UC_ARM_REG_R0)

    context = 0x20000004
    data = ram[context - 0x1fff8000:context - 0x1fff8000 + 0x400]
    native_pointer, native_count = call(0x3ee30), call(0x3ee46)
    saved_pointer, saved_count = call(0x3ee6e), call(0x3ee78)
    epr = bool(data[0x3d5] & 8)
    count = (struct.unpack_from('<H', data, 0x52)[0] >> 2 & 127) if epr else (data[0x4f] >> 4 & 7)
    saved = data[0x10d] >> 4 & 7
    if (native_pointer, native_count, saved_pointer, saved_count) != (context + 0xa8, min(count, 11), context + 0xf0, saved):
        raise AssertionError('Native PDO getter semantics differ from the proposed layout')
    header = struct.unpack_from('<H', data, 0x10c if epr else 0x4e)[0]
    spr_count = saved if epr else count
    spr_offset = 0xf0 if epr else 0xa8
    spr = data[spr_offset:spr_offset + 4 * spr_count]
    epr_data = data[0xa8:0xa8 + 4 * count] if epr else None
    valid = (0 < spr_count <= 7 and header & 0x801f == 1
             and (not epr or 7 < count <= 11))
    # 0x24 is the ONE active native PDM state observed in this experiment.
    # Other nonzero states have not been qualified by this diagnostic.
    observed_active_state = data[0x29] == 0x24
    report = dict(schema=1, usb_access=False, atomic_snapshot=False,
                  native_getters_match=True, native_state=hex(data[0x29]),
                  observed_active_state=observed_active_state,
                  cache_shape_valid=valid, may_be_retained=not observed_active_state,
                  spr_pdos_raw=spr.hex().upper(), epr_pdos_raw=epr_data.hex().upper() if epr_data else None,
                  spr_count=spr_count, current_count=count, epr_flag=epr,
                  diagnostic_only=True, live_capture_exported=False)
    if args.compare_fixture:
        fixture = new_snapshot('km003c')
        collector = Collector(fixture)
        for event in json.loads((ROOT / 'tests/fixtures/stock-pd32-events.json').read_text()): collector.feed(event)
        if spr.hex().upper() != fixture['fields']['spr_pdos']['raw'] or (epr_data or b'').hex().upper() != fixture['fields']['epr_pdos']['raw']:
            raise AssertionError('Native cached PDOs differ from reference wire data')
        report['reference_wire_pdos_match'] = True
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('native_getters_match', 'native_state', 'spr_count', 'current_count', 'may_be_retained')}))


if __name__ == '__main__':
    main()
