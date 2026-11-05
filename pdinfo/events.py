"""Conservative packet collection; KM003C does not expose independent CRC checks."""
import struct

from .model import set_field

DATA_FIELDS = {1: 'spr_pdos', 11: 'source_info', 12: 'revision'}
EXT_FIELDS = {1: 'source_cap_ext', 2: 'source_status', 7: 'manufacturer',
              12: 'pps_status', 17: 'epr_pdos'}
MIN_LENGTH = {'source_cap_ext': 24, 'source_status': 7, 'manufacturer': 4,
              'pps_status': 4, 'epr_pdos': 4}


class Collector:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.chunks = {}
        self.closed = False
        self.stop_reason = None
        self.attached = False

    def feed(self, event):
        if self.closed:
            return None
        if event.get('kind') == 'connection' and event.get('message') in ('DISCONNECT', 'CONNECT'):
            if event.get('message') == 'CONNECT' and not self.attached:
                self.attached = True
                return None
            # A new attach ends this snapshot rather than mixing two sources.
            self.closed, self.stop_reason = True, 'connection_changed'
            return 'connection_changed'
        h = event.get('header')
        if event.get('kind') != 'pd' or type(h) is not int or not 0 <= h <= 65535:
            return None
        sop, count, kind = event.get('sop'), h >> 12 & 7, h & 31
        if sop == 0 and not count and kind == 13:
            self.closed, self.stop_reason = True, 'soft_reset'
            return 'soft_reset'  # A native/local reset also ends the probe transaction.
        if sop not in (0, 1) or (sop == 0 and not h & 0x100):
            return None  # Never mistake a locally transmitted request for a source reply.
        self.attached = True
        try:
            wire = bytes.fromhex(event.get('wire_hex', ''))
        except ValueError:
            return None
        if len(wire) != 2 + count * 4 or int.from_bytes(wire[:2], 'little') != h:
            return None
        if not count:
            return {4: 'rejected', 12: 'busy', 16: 'unsupported'}.get(kind) if sop == 0 else None
        payload = wire[2:]
        if h & 0x8000:
            if sop != 0 or kind not in EXT_FIELDS:
                return None
            name = EXT_FIELDS[kind]
            ext = int.from_bytes(payload[:2], 'little')
            total, request, number, chunked = ext & 511, bool(ext & 1024), ext >> 11 & 15, bool(ext & 32768)
            if request:
                return None
            if ext & 0x0200 or total > 260 or (not chunked and (number or total > 26)):
                set_field(self.snapshot, name, 'invalid')
                return 'invalid'
            needed = min(26, total - number * 26) if chunked else total
            if needed <= 0 or len(payload) < 2 + needed or len(payload) - 2 - needed > 3:
                set_field(self.snapshot, name, 'invalid')
                return 'invalid'
            fragment = payload[2:2 + needed]
            if not chunked or number == 0:
                old = self.chunks.get(name)
                if not chunked or old is None or old[0] != total or old[1].get(0) != fragment:
                    self.chunks[name] = (total, {0: fragment})
            elif name not in self.chunks or self.chunks[name][0] != total:
                set_field(self.snapshot, name, 'partial')
                return 'partial'
            else:
                old = self.chunks[name][1].get(number)
                if old is not None and old != fragment:
                    self.chunks.pop(name)
                    set_field(self.snapshot, name, 'invalid')
                    return 'invalid'
                self.chunks[name][1][number] = fragment
            fragments = self.chunks[name][1]
            expected = (total + 25) // 26 if chunked else 1
            if set(fragments) != set(range(expected)):
                set_field(self.snapshot, name, 'partial')
                return 'partial'
            raw = b''.join(fragments[i] for i in range(expected))
            if len(raw) < MIN_LENGTH[name] or (name == 'epr_pdos' and len(raw) % 4):
                set_field(self.snapshot, name, 'invalid')
                return 'invalid'
            set_field(self.snapshot, name, 'observed', raw)
            return name
        if sop == 0 and kind in DATA_FIELDS:
            name = DATA_FIELDS[kind]
            if name != 'spr_pdos' and count != 1:
                set_field(self.snapshot, name, 'invalid')
                return 'invalid'
            set_field(self.snapshot, name, 'observed', payload)
            return name
        if kind == 15 and count >= 1:
            vdm = int.from_bytes(payload[:4], 'little')
            if (vdm >> 16 == 0xFF00 and vdm & 0x8000 and vdm & 31 == 1
                    and vdm >> 6 & 3 == 1 and count >= (5 if sop else 4)):
                name = 'cable_identity' if sop else 'source_identity'
                set_field(self.snapshot, name, 'observed', payload)
                return name
        return None


def event_from_words(sop, header, objects, **extra):
    return dict(kind='pd', sop=sop, header=header,
                wire_hex=(struct.pack('<H', header) + b''.join(struct.pack('<I', x) for x in objects)).hex(),
                **extra)
