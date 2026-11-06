"""Verify the exact native logger/enqueue ABI using PRIVATE ROM in Unicorn.

No USB, PHY timing or PD negotiation is simulated. Native ecfc/5f34/ec7c and
3f668/9344 execute; only the clock and RTOS event notification are stubbed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pdinfo.overlay207 import STOCK_HASH
from tools.check_native_arm import load_elf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    args = parser.parse_args()
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS
    from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3
    rom = args.stock.read_bytes()
    if hashlib.sha256(rom).hexdigest() != STOCK_HASH:
        raise ValueError('Only the exact qualified private ROM is accepted')
    cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    cpu.mem_map(0, 0x80000); cpu.mem_write(0x4000, rom)
    cpu.mem_map(0x1fff8000, 0x2f000)
    symbols = load_elf(cpu, (args.build / 'overlay207-offline.elf').read_bytes())
    cpu.mem_write(0x40a70, bytes.fromhex('00207047'))  # Native logger timestamp only.
    cpu.mem_write(0x4c888, bytes.fromhex('00207047'))  # RTOS notification only.
    ctx, info, capture, wire = 0x20000004, 0x20020000, 0x20021000, 0x20022000
    now = 0

    def call(address, *arguments):
        cpu.reg_write(UC_ARM_REG_SP, 0x20026000)
        cpu.reg_write(UC_ARM_REG_LR, 0x101)
        for reg, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), arguments):
            cpu.reg_write(reg, value)
        cpu.emu_start(address | 1, 0x100, timeout=3_000_000, count=1_000_000)
        if cpu.reg_read(UC_ARM_REG_PC) != 0x100 or cpu.reg_read(UC_ARM_REG_SP) != 0x20026000:
            raise AssertionError('Native function did not restore its stack/return')

    def tick(dt=0):
        nonlocal now
        now += dt
        before = bytes(cpu.mem_read(ctx + 0x77a, 2))
        call(symbols['kpd207_information_tick'], info, capture, now)
        if bytes(cpu.mem_read(ctx + 0x77a, 2)) != before:
            raise AssertionError('Observer altered the native USB consumer cursor')

    def log(raw, sop=0, tx=False):
        if isinstance(raw, str): raw = bytes.fromhex(raw)
        cpu.mem_write(wire, raw)
        call(0xecfc, ctx, wire, int(tx), sop)

    def state(): return bytes(cpu.mem_read(info + 2152, 8))
    def field(n):
        b = bytes(cpu.mem_read(info + (n - 2) * 268, 268))
        return b[266], b[:struct.unpack_from('<H', b, 260)[0]]

    # Baseline from idle; start with a counter about to roll over, independent
    # of the physical ring position (65536 is not a multiple of 320).
    cpu.mem_write(ctx + 0x780, struct.pack('<H', 65520))
    tick()
    cpu.mem_write(ctx + 0x29, b'\x24'); cpu.mem_write(capture + 179, b'\x04')
    tick(1)
    events = json.loads((ROOT / 'tests/fixtures/stock-pd32-events.json').read_text())
    for index in (0, 1):
        e = events[index]; log(e['wire_hex'], e['sop'], index == 0)
    tick(1)
    if field(8)[0] != 2 or field(8)[1] != bytes.fromhex(events[1]['wire_hex'])[2:]:
        raise AssertionError('Native circular logger lost cable identity across counter rollover')
    tick(2000)
    for type_, reply, target in ((24, 9, 2), (17, 10, 3), (18, 11, 5), (23, 12, 4), (6, 14, 7), (15, None, 9)):
        command = bytes(cpu.mem_read(ctx + 0x4c, 2))
        if command[0] & 31 != type_ or not cpu.mem_read(ctx + 0x27, 1)[0] & 5 or state()[0] != 3:
            raise AssertionError(f'Native console enqueue mismatch for type {type_}: {command.hex()}, {state().hex()}')
        if type_ == 6:
            if bytes(cpu.mem_read(ctx + 0x8c, 4)) != bytes(4): raise AssertionError('Target/Ref was not copied')
            log(events[13]['wire_hex'], tx=True)
        elif type_ == 15:
            if bytes(cpu.mem_read(ctx + 0x8c, 4)) != bytes.fromhex('01 a8 00 ff'): raise AssertionError('Identity request changed')
            log('8f 10 01 a8 00 ff', tx=True)
        else: log(struct.pack('<H', 0x80 | type_), tx=True)
        if reply is None: log('af 11 81 a8 00 ff')
        else: log(events[reply]['wire_hex'])
        # Model only the policy engine releasing its custom-command mailbox.
        cpu.mem_write(ctx + 0x27, b'\0')
        tick(5)
        if field(target)[0] != (3 if reply is None else 1):
            raise AssertionError(f'Native record response not correlated for field {target}: {field(target)}')
        tick(150)
    if state()[0] != 6 or state()[3] != 1:
        raise AssertionError('Information list did not finish')
    # Multiple physical ring wraps while polling; no dependence on the native
    # consuming reader or cumulative counter modulo 320.
    for _ in range(90):
        for _ in range(8): log('81 01')
        tick(1)
        actual = struct.unpack('<H', cpu.mem_read(ctx + 0x780, 2))[0]
        if struct.unpack('<H', cpu.mem_read(info + 2148, 2))[0] != actual:
            raise AssertionError('Independent ring cursor stopped following committed records')
    # A separate native cable-only operation must preserve the completed PD
    # fields. Returning to actual PD mode starts fresh with the native setting.
    completed = bytes(cpu.mem_read(info, 2144))
    cpu.mem_write(ctx, b'\x05')
    tick(1)
    log(events[1]['wire_hex'], 1)
    tick(1)
    if bytes(cpu.mem_read(info, 2144)) != completed or state()[0] != 0:
        raise AssertionError('Cable-only native mode changed the saved PD measurement')
    cpu.mem_write(ctx, b'\0'); cpu.mem_write(ctx + 3, b'\x02')
    tick(1)
    log(events[0]['wire_hex'], 1, True); log(events[1]['wire_hex'], 1)
    tick(1)
    if field(8)[0] != 12 or cpu.mem_read(info + 2191, 1)[0] != 2:
        raise AssertionError('Native e-Marker setting did not reach the QR field status')
    # A stalled GUI cannot silently call missing packets successful.
    cpu.mem_write(info + 2152, bytes([4, 0, 2, 0, 1, 1, 0, 1]))
    for _ in range(41): log('81 01')  # 328 bytes > ring capacity.
    tick(1)
    if field(2)[0] != 9 or state()[0] != 6 or state()[5]:
        raise AssertionError('Ring overrun did not end active request as invalid')
    print('Exact native ABI: six queued commands, five real response fixtures, cable identity, NAK, counter rollover, 90 ring wraps and overrun passed; no USB/PHY test.')


if __name__ == '__main__':
    main()
