"""Compare exact stock and overlay scatter-load output offline, before main.

Inputs are private firmware files. Requires Unicorn 2.1.4. Never opens USB.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pdinfo import overlay207 as o


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock',required=True,type=Path)
    parser.add_argument('--candidate',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT/'artifacts').resolve()) or args.output.exists():
        parser.error('Output must be a new file in ignored artifacts/')
    stock,candidate=args.stock.read_bytes(),args.candidate.read_bytes()
    if hashlib.sha256(stock).hexdigest()!=o.STOCK_HASH:
        parser.error('Unrecognized stock build')
    o.extension_header(candidate[o.TAIL-o.BASE:])
    from unicorn import Uc,UC_ARCH_ARM,UC_MODE_THUMB,UC_MODE_MCLASS,UC_PROT_READ,UC_PROT_EXEC
    from unicorn.arm_const import UC_ARM_REG_SP,UC_ARM_REG_PC
    results=[]
    for image in (stock,candidate):
        cpu=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
        cpu.mem_map(0,0x80000);cpu.mem_write(o.BASE,image)
        cpu.mem_protect(0,0x80000,UC_PROT_READ|UC_PROT_EXEC)
        cpu.mem_map(0x1fff8000,0x2f000)
        cpu.reg_write(UC_ARM_REG_SP,struct.unpack_from('<I',image)[0])
        cpu.emu_start(0x4c49,0x4c6a,count=2_000_000,timeout=5_000_000)
        if cpu.reg_read(UC_ARM_REG_PC)!=0x4c6a:
            raise RuntimeError('Native scatter-load did not stop before main')
        results.append(bytes(cpu.mem_read(0x1fff8000,0x2f000)))
    if results[0]!=results[1]:
        raise RuntimeError('Overlay changes native initialized SRAM')
    report=dict(schema=1,usb_access=False,stop_before_main='0x4c6a',full_sram_equal=True,
                sram_bytes=len(results[0]),sram_sha256=hashlib.sha256(results[0]).hexdigest(),
                candidate_sha256=hashlib.sha256(candidate).hexdigest())
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print('Native startup: stock and overlay produce identical full SRAM before main. No hardware accessed.')


if __name__=='__main__':
    main()
