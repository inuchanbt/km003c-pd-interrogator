"""Raw-first schema, independent of a device's PHY or host transport."""
from datetime import datetime, timezone
import struct
import uuid

FIELDS = ('spr_pdos', 'epr_pdos', 'revision', 'source_cap_ext', 'source_info',
          'source_status', 'pps_status', 'manufacturer', 'cable_identity', 'source_identity')
STATES = ('not_requested', 'pass', 'observed', 'unsupported', 'no_response',
          'tx_failed', 'rejected', 'busy', 'reset', 'invalid', 'partial', 'unknown', 'emulation_enabled')
ORIGINS = ('km003c', 'ch32h417', 'demo', 'km003c_cable', 'km003c_pps')
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


def status_flags(value, labels, reserved_mask):
    flags = {label: bool(value & (2 << i)) for i, label in enumerate(labels)}
    flags['reserved'] = value & reserved_mask
    return flags


def decode_source_status(raw):
    # KPD1 stores the SDB without its Extended Header or declared Data Size.
    if len(raw) < 6:
        return None
    temp, inputs, batteries, events, thermal, power = raw[:6]
    external, battery = bool(inputs & 2), bool(inputs & 8)
    result = dict(
        internal_temperature_code=temp, internal_temperature_c=temp if temp >= 2 else None,
        present_input=dict(external=external, external_type=('ac' if inputs & 4 else 'dc') if external else None,
                           internal_battery=battery, internal_non_battery=bool(inputs & 16),
                           reserved=inputs & (0xE1 if external else 0xE5)),
        present_battery_input=dict(raw=batteries, active=[i for i in range(8) if batteries & (1 << i)]
                                   if battery else None, reserved=0 if battery else batteries),
        events=status_flags(events, ('ocp', 'otp', 'ovp', 'pps_current_limit'), 0xE1),
        temperature_status=dict(code=thermal >> 1 & 3, reserved=thermal & 0xF9),
        power_status=status_flags(power, ('cable', 'other_ports', 'external_power', 'events', 'temperature'), 0xC1),
    )
    if len(raw) >= 7:
        state = raw[6]
        result['power_state_change'] = dict(state_code=state & 7, indicator_code=state >> 3 & 7,
                                            reserved=state & 0xC0)
    if len(raw) > 7:
        result['trailing_hex'] = raw[7:].hex().upper()
    return result


def decode_source_identity(vdos):
    if not 4 <= len(vdos) <= 7 or vdos[0] & 0xFFFF87FF != 0xFF008041:
        return None
    vdm, ident, xid, product, *extra = vdos
    major, minor = vdm >> 13 & 3, vdm >> 11 & 3
    version = {(0, 0): '1.0', (1, 0): '2.0', (1, 1): '2.1'}.get((major, minor))
    modern = major == 1 and version is not None
    ufp, dfp = ident >> 27 & 7, ident >> 23 & 7
    ufp_types = {0: 'none', 1: 'hub', 2: 'peripheral'}
    if modern:
        ufp_types[3] = 'psd'
    elif version == '1.0':
        ufp_types[5] = 'legacy_ama'
    ufp_name = ufp_types.get(ufp) if version else None
    dfp_name = {0: 'none', 1: 'hub', 2: 'host', 3: 'power_brick'}.get(dfp) if modern else None
    types = []
    if modern:
        if ufp in (1, 2) and dfp in (1, 2, 3):
            if len(extra) == 3 and extra[1] == 0:
                types = ['ufp', 'pad', 'dfp']
        elif ufp in (1, 2) and dfp == 0:
            types = ['ufp']
        elif ufp == 0 and dfp in (1, 2, 3):
            types = ['dfp']
    type_vdos = []
    for i, vdo in enumerate(extra):
        kind = types[i] if i < len(types) else 'unknown'
        item = dict(index=i + 5, raw=f'{vdo:08X}', kind=kind)
        if kind in ('ufp', 'dfp'):
            code = vdo >> 29
            item['version_code'] = code
            if kind == 'ufp' and code == 3:
                item.update(version='1.3', device_capability_code=vdo >> 24 & 15,
                            alternate_mode_code=vdo >> 3 & 7, speed_code=vdo & 7,
                            reserved=f'{vdo & 0x10FFFFC0:08X}')
            if kind == 'dfp' and code == 2:
                item.update(version='1.2', host_capability_code=vdo >> 24 & 7,
                            port_number=vdo & 31, reserved=f'{vdo & 0x18FFFFE0:08X}')
        type_vdos.append(item)
    return dict(vdm=dict(svid='FF00', structured=True, command='discover_identity',
                         response='ack', version=version, major_code=major, minor_code=minor),
                vid=f'{ident & 65535:04X}', pid=f'{product >> 16:04X}', xid=f'{xid:08X}',
                bcd_device=f'{product & 65535:04X}', usb_host_capable=bool(ident & (1 << 31)),
                usb_device_capable=bool(ident & (1 << 30)), modal_operation=bool(ident & (1 << 26)),
                ufp_type_code=ufp, ufp_type=ufp_name, dfp_type_code=dfp if modern else None,
                dfp_type=dfp_name, connector_type_code=ident >> 21 & 3 if modern else None,
                id_header_reserved=f'{ident & (0x001F0000 if modern else 0x03FF0000):08X}'
                if version is not None else None, product_type_vdos=type_vdos)


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
    termination = cable >> 11 & 3
    vconn = ({0: False, 1: True} if kind == 'passive' else {2: True, 3: True}).get(termination)
    result = dict(kind=kind, vid=f'{ident & 65535:04X}', pid=f'{product >> 16:04X}',
                  layout='pd3_assumed' if headerless else 'pd2' if legacy else 'pd3',
                  vdo_version_code=version, vbus_through=vbus, max_vbus_mv=voltage,
                  voltage_code=voltage_code, current_code=current_code, current_ma=current,
                  epr_capable=epr, speed_code=cable & 7, hw_version=cable >> 28,
                  fw_version=cable >> 24 & 15, latency_code=cable >> 13 & 15,
                  termination_code=termination, vconn_required=vconn, connector_type_code=cable >> 18 & 3)
    if kind == 'active' and version == 3:
        result.update(sbu_supported=not bool(cable & 0x100),
                      sbu_active=bool(cable & 0x80) if not cable & 0x100 else None,
                      sop_double_prime_present=bool(cable & 8))
    return result


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
                           current_ma=(pdo & 1023) * 10, peak_current_code=pdo >> 20 & 3)
                first_5v = index == 1 and row['voltage_mv'] == 5000
                row['reserved'] = f'{pdo & (0x00400000 if first_5v else 0x3FC00000):08X}'
                if first_5v:
                    row['capabilities'] = dict(
                        dual_role_power=bool(pdo & (1 << 29)), usb_suspend_supported=bool(pdo & (1 << 28)),
                        unconstrained_power=bool(pdo & (1 << 27)), usb_communications_capable=bool(pdo & (1 << 26)),
                        dual_role_data=bool(pdo & (1 << 25)), unchunked_extended_messages_bit=pdo >> 24 & 1,
                        epr_mode_capable_bit=pdo >> 23 & 1)
            elif pdo >> 30 in (1, 2):
                row.update(min_mv=(pdo >> 10 & 1023) * 50, max_mv=(pdo >> 20 & 1023) * 50)
                if pdo >> 30 == 1:
                    row.update(kind='battery', max_power_mw=(pdo & 1023) * 250)
                else:
                    row.update(kind='variable', current_ma=(pdo & 1023) * 10)
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
                    port_type_name='guaranteed' if raw[3] & 128 else 'managed', reserved=raw[3] & 127)
    if name == 'source_status':
        return decode_source_status(raw)
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
    if name == 'pps_status':
        result = {}
        if origin == 'km003c_pps':
            if len(raw) not in (4, 8):
                return None
            rdo = int.from_bytes(raw[:4], 'little')
            result.update(requested_mv=(rdo >> 9 & 2047) * 20, requested_ma=(rdo & 127) * 50)
            raw = raw[4:]
        if len(raw) == 4:
            voltage, current, flags = struct.unpack('<HBB', raw)
            if not flags & 0xF1:
                result.update(reported_mv=None if voltage == 65535 else voltage * 20,
                              reported_ma=None if current == 255 else current * 50,
                              temperature_code=flags >> 1 & 3,
                              current_limit=bool(flags & 8), flags=flags)
        return result or None
    if name in ('cable_identity', 'source_identity'):
        vdos = words(raw)
        if (name == 'cable_identity' and origin == 'km003c_cable' and
                len(vdos) == 5 and not any(vdos[:4]) and vdos[4]):
            return dict(vdos=['00000000'] * 4,
                        svids=[f'{v:04X}' for v in (vdos[4] >> 16, vdos[4] & 65535) if v])
        result = dict(vdos=[f'{v:08X}' for v in vdos])
        if name == 'cable_identity':
            cable = decode_cable(vdos, origin)
            if cable is not None:
                result['cable'] = cable
        else:
            identity = decode_source_identity(vdos)
            if identity is not None:
                result['identity'] = identity
        return result
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
