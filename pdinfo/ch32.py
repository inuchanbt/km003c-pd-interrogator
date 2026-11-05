"""Import one CH32 @PD1 session without guessing missing wire data."""
import struct
import re

from .model import new_snapshot, set_field
from .events import Collector

PROBES = {'Get_Revision': 'revision', 'Get_Source_Cap_Extended': 'source_cap_ext',
          'Get_Status': 'source_status', 'Get_Source_Info': 'source_info',
          'Get_PPS_Status': 'pps_status', 'Get_Manufacturer_Info': 'manufacturer'}
PROBES.update(revision='revision', source_cap_ext='source_cap_ext', status='source_status',
              source_info='source_info', pps_status='pps_status', manufacturer_info='manufacturer')
STATUS = {'pass': 'pass', 'unsupported': 'unsupported', 'no_response': 'no_response',
          'tx_failed': 'tx_failed', 'rejected': 'rejected', 'busy': 'busy',
          'invalid': 'invalid', 'pending': 'partial', 'unknown': 'unknown',
          'fail': 'invalid', 'na': 'not_requested'}


def import_log(text, session=None):
    records, timeline, boot = [], [], 0
    for line in text.splitlines():
        start = line.find('@PD1,')
        if start < 0:
            timeline.append((boot, line, None))
            continue
        row = dict(part.split('=', 1) for part in line[start + 5:].strip().split(',') if '=' in part)
        if 'type' in row and 'session' in row:
            if row['type'] == 'boot':
                boot += 1
                timeline.append((boot, line, row))
                continue
            records.append(row)
            timeline.append((boot, line, row))
    used_boots = {segment for segment, _, row in timeline if row and row['type'] != 'boot'}
    if len(used_boots) > 1:
        raise ValueError('Log spans multiple boots; split it by boot before importing to avoid reused session IDs')
    sessions = sorted({r['session'] for r in records})
    if session is None:
        if len(sessions) != 1:
            raise ValueError('Choose --session explicitly for a log containing zero or multiple sessions')
        session = sessions[0]
    session = str(session)
    if session not in sessions:
        raise ValueError('Session not found')
    snapshot = new_snapshot('ch32h417')
    snapshot['capture_status'] = 'imported'
    snapshot['imported_at'] = snapshot['captured_at']
    snapshot['captured_at'] = None
    snapshot['imported_session'] = session
    snapshot['legacy_records'] = [r for r in records if r['session'] == session]
    pdos = {'spr_pdo': {}, 'epr_pdo': {}}
    counts = {}
    for row in snapshot['legacy_records']:
        kind = row['type']
        if kind in ('spr', 'epr'):
            pdos[kind + '_pdo'].clear()
            set_field(snapshot, 'spr_pdos' if kind == 'spr' else 'epr_pdos', 'partial')
            if (key := ('pdo_count' if kind == 'spr' else 'object_count')) in row:
                counts[kind + '_pdo'] = int(row[key])
        elif kind in pdos:
            index, value = int(row['index']), int(row['raw'], 16)
            if not 1 <= index <= (7 if kind == 'spr_pdo' else 13) or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError('Invalid PDO index/value')
            pdos[kind][index] = value
        elif kind in ('revision', 'source_info') and 'raw' in row:
            set_field(snapshot, kind, 'observed', struct.pack('<I', int(row['raw'], 16)))
        elif kind == 'source_status' and 'raw' in row:
            set_field(snapshot, kind, 'observed', bytes.fromhex(row['raw']))
        elif kind in ('source_cap_ext', 'manufacturer', 'cable'):
            name = 'cable_identity' if kind == 'cable' else kind
            if 'raw' not in snapshot['fields'][name]:
                set_field(snapshot, name, 'partial')
        elif kind == 'probe' and row.get('name') in PROBES:
            name = PROBES[row['name']]
            field = snapshot['fields'][name]
            status = STATUS.get(row.get('status'), 'unknown')
            set_field(snapshot, name, status, bytes.fromhex(field['raw']) if 'raw' in field else None)
    for kind, values in pdos.items():
        if not values:
            continue
        name = 'spr_pdos' if kind == 'spr_pdo' else 'epr_pdos'
        if set(values) != set(range(1, counts.get(kind, max(values)) + 1)):
            set_field(snapshot, name, 'partial')
        else:
            set_field(snapshot, name, 'observed', b''.join(struct.pack('<I', values[i]) for i in sorted(values)))
    # Complete RX dumps from the CH32's SOP diagnostic path are authoritative.
    # Their SOP designation comes from firmware logging context, not from the dump.
    collector = Collector(snapshot)
    active_session = session if len(sessions) == 1 else None
    pending_ext = None
    for _, line, row in timeline:
        if row:
            active_session = None if row['type'] in ('boot', 'detach') else row['session']
        if active_session != session:
            pending_ext = None
            continue
        if line.strip().startswith('TX probe:'):
            pending_ext = None
        if 'EXT payload bytes:' in line and pending_ext:
            pairs = re.findall(r'\[(\d+)\]=([0-9A-Fa-f]{2})\b', line)
            name, length = pending_ext
            if [int(i) for i, _ in pairs] == list(range(length)):
                set_field(snapshot, name, 'observed', bytes(int(value, 16) for _, value in pairs))
            pending_ext = None
        match = re.fullmatch(r'\s*(?:DATA|EXT(?: CHUNK1)?) RAW8:\s*((?:[0-9A-Fa-f]{2}\s*)+)\s*', line)
        if not match:
            continue
        raw = bytes.fromhex(match[1])
        if len(raw) < 2:
            continue
        header = int.from_bytes(raw[:2], 'little')
        pending_ext = None
        if header & 0x8100 == 0x8100 and len(raw) >= 4:
            from .events import EXT_FIELDS, MIN_LENGTH
            ext = int.from_bytes(raw[2:4], 'little')
            name, length = EXT_FIELDS.get(header & 31), ext & 511
            if name and not ext & 0xFE00 and MIN_LENGTH[name] <= length <= 260:
                pending_ext = (name, length)
        expected = 2 + 4 * (header >> 12 & 7)
        if len(raw) < expected:
            continue
        collector.feed(dict(kind='pd', sop=0, header=header, wire_hex=raw[:expected].hex()))
    # A successful probe without wire bytes is retained as such, never fabricated.
    for row in snapshot['legacy_records']:
        if row['type'] == 'probe' and row.get('name') in PROBES:
            name = PROBES[row['name']]
            field = snapshot['fields'][name]
            status = STATUS.get(row.get('status'), 'unknown')
            set_field(snapshot, name, status, bytes.fromhex(field['raw']) if 'raw' in field else None)
    snapshot['notes'] = ['Legacy records are retained in JSON. QR carries only exact raw fields; '
                         'fields without full raw bytes are not synthesized. SOP for DATA/EXT RAW8 '
                         'is assigned from the CH32 firmware logging context.',
                         'Revision version is the PD specification version, not the USB bus version. '
                         'Source Info contains PDP ratings, not temperature/input type.']
    return snapshot
