"""Raw-first schema, independent of a device's PHY or host transport."""
from datetime import datetime, timezone
import struct
import uuid

FIELDS = ('spr_pdos', 'epr_pdos', 'revision', 'source_cap_ext', 'source_info',
          'source_status', 'pps_status', 'manufacturer', 'cable_identity', 'source_identity')
STATES = ('not_requested', 'pass', 'observed', 'unsupported', 'no_response',
          'tx_failed', 'rejected', 'busy', 'reset', 'invalid', 'partial', 'unknown', 'emulation_enabled')
ORIGINS = ('km003c', 'ch32h417', 'demo', 'km003c_cable')
CAPTURE_STATES = ('unknown', 'completed', 'failed', 'interrupted', 'imported')


def new_snapshot(origin, measurement_id=None):
    if origin not in ORIGINS:
        raise ValueError('Unknown origin')
    return dict(schema='pdinfo/1', measurement_id=measurement_id or uuid.uuid4().hex[:16],
                origin=origin, capture_status='unknown', captured_at=datetime.now(timezone.utc).isoformat(),
                fields={name: dict(status='not_requested') for name in FIELDS})


def words(raw):
    if len(raw) % 4:
        raise ValueError('Object data is not a multiple of four bytes')
    return list(struct.unpack('<' + 'I' * (len(raw) // 4), raw))


def decode_cable(vdos, origin):
    """Decode Cable VDO 1 from a Discover Identity ACK or KM003C's result table."""
    headerless = origin == 'km003c_cable' and len(vdos) == 4
    if headerless:
        ident, _, product, cable = vdos
        major = 1
    elif 5 <= len(vdos) <= 7 and vdos[0] & 0xFFFF87DF == 0xFF008041:
        _, ident, _, product, cable, *_ = vdos
        major = vdos[0] >> 13 & 3
    else:
        return None
    kind = {3: 'passive', 4: 'active'}.get(ident >> 27 & 7)
    if not kind or not cable or major > 1:
        return None
    legacy = major == 0
    version = None if legacy else cable >> 21 & 7
    if not legacy and version not in ((0,) if kind == 'passive' else (1, 2, 3)):
        return None
    vbus = kind == 'passive' or bool(cable & 0x10)
    voltage_code = None if legacy else cable >> 9 & 3
    # PD 3.x treats the retired 30/40 V encodings as 20 V.
    voltage = None if legacy or not vbus else (50000 if voltage_code == 3 else 20000)
    current_code = cable >> 5 & 3
    current = {1: 3000, 2: 5000}.get(current_code) if vbus else None
    epr = False
    if not legacy and cable & 0x20000:
        epr = True if voltage == 50000 and current == 5000 else None
    return dict(kind=kind, vid=f'{ident & 65535:04X}', pid=f'{product >> 16:04X}',
                layout='pd3_assumed' if headerless else 'pd2' if legacy else 'pd3',
                vdo_version_code=version, vbus_through=vbus, max_vbus_mv=voltage,
                voltage_code=voltage_code, current_code=current_code, current_ma=current,
                epr_capable=epr, speed_code=cable & 7)


def decode(name, raw, origin=None):
    """Only confirmed layouts; unknown/reserved values remain raw."""
    if name in ('spr_pdos', 'epr_pdos'):
        result = []
        for index, pdo in enumerate(words(raw), 1):
            row = dict(index=index, raw=f'{pdo:08X}', type=pdo >> 30)
            if pdo == 0:
                row['kind'] = 'reserved'
            elif pdo >> 30 == 0:
                row.update(kind='fixed', voltage_mv=(pdo >> 10 & 1023) * 50,
                           current_ma=(pdo & 1023) * 10)
            elif pdo >> 30 == 3 and (pdo >> 28 & 3) == 0:
                row.update(kind='pps', min_mv=(pdo >> 8 & 255) * 100,
                           max_mv=(pdo >> 17 & 255) * 100, current_ma=(pdo & 127) * 50)
            elif pdo >> 30 == 3 and (pdo >> 28 & 3) == 1:
                row.update(kind='epr_avs', min_mv=(pdo >> 8 & 255) * 100,
                           max_mv=(pdo >> 17 & 511) * 100, pdp_w=pdo & 255,
                           peak_current_code=pdo >> 26 & 3)
            elif pdo >> 30 == 3 and (pdo >> 28 & 3) == 2:
                row.update(kind='spr_avs', max_current_9v_15v_ma=(pdo >> 10 & 1023) * 10,
                           max_current_15v_20v_ma=(pdo & 1023) * 10,
                           peak_current_code=pdo >> 26 & 3)
            else:
                row['kind'] = 'raw'
            result.append(row)
        return result
    if name == 'revision' and len(raw) == 4:
        value = int.from_bytes(raw, 'little')
        return dict(pd_revision=f'{value >> 28}.{value >> 24 & 15}',
                    pd_spec_version=f'{value >> 20 & 15}.{value >> 16 & 15}',
                    reserved=value & 65535)
    if name == 'source_info' and len(raw) == 4:
        return dict(port_reported_pdp_w=raw[0], port_present_pdp_w=raw[1],
                    port_maximum_pdp_w=raw[2], port_type=(raw[3] >> 7),
                    reserved=raw[3] & 127)
    if name == 'source_cap_ext' and len(raw) >= 24:
        return dict(vid=f'{int.from_bytes(raw[:2], "little"):04X}',
                    pid=f'{int.from_bytes(raw[2:4], "little"):04X}',
                    xid=f'{int.from_bytes(raw[4:8], "little"):08X}',
                    fw_version=raw[8], hw_version=raw[9], spr_pdp_w=raw[23],
                    epr_pdp_w=raw[24] if len(raw) >= 25 else None)
    if name == 'manufacturer' and len(raw) >= 4:
        return dict(vid=f'{int.from_bytes(raw[:2], "little"):04X}',
                    pid=f'{int.from_bytes(raw[2:4], "little"):04X}',
                    name=raw[4:].split(b'\0', 1)[0].decode('utf-8', errors='replace').rstrip(' '))
    if name in ('cable_identity', 'source_identity'):
        vdos = words(raw)
        result = dict(vdos=[f'{v:08X}' for v in vdos])
        if name == 'cable_identity':
            cable = decode_cable(vdos, origin)
            if cable is not None:
                result['cable'] = cable
        return result
    # Status/PPS Status stay raw until unknown-value semantics are implemented.
    return None


def set_field(snapshot, name, status, raw=None):
    if name not in FIELDS or status not in STATES:
        raise ValueError('Unknown field/status')
    field = dict(status=status)
    if raw is not None:
        if len(raw) > 260:
            raise ValueError('Field exceeds PD extended data limit')
        field['raw'] = raw.hex().upper()
        value = decode(name, raw, snapshot['origin'])
        if value is not None:
            field['decoded'] = value
    snapshot['fields'][name] = field


def validate(snapshot):
    if snapshot.get('schema') != 'pdinfo/1' or snapshot.get('origin') not in ORIGINS:
        raise ValueError('Unsupported snapshot schema/origin')
    if snapshot.get('capture_status') not in CAPTURE_STATES:
        raise ValueError('Unknown capture status')
    mid = snapshot.get('measurement_id', '')
    if len(mid) != 16 or any(c not in '0123456789abcdef' for c in mid):
        raise ValueError('measurement_id must be 16 lowercase hexadecimal digits')
    fields = snapshot.get('fields')
    if not isinstance(fields, dict) or set(fields) != set(FIELDS):
        raise ValueError('Snapshot must contain every known field')
    for name, field in fields.items():
        if not isinstance(field, dict) or field.get('status') not in STATES:
            raise ValueError('Unknown field status')
        if 'raw' in field:
            raw = bytes.fromhex(field['raw'])
            if len(raw) > 260:
                raise ValueError('Field exceeds PD extended data limit')
            decode(name, raw)
