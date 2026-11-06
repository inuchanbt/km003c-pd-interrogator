import base64
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import random
import struct
import subprocess
import tempfile
import unittest
import zlib

from pdinfo import ch32, model, transfer
from pdinfo.__main__ import main
from pdinfo.events import Collector, event_from_words
from pdinfo.live import PROBES

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT/'tests/fixtures'

# Captured on the KM003C. The eighth Status byte came from the on-device record.
MEASURED_STATUS = bytes.fromhex('0006000000000000')
MEASURED_SOURCE_ID = struct.pack('<5I', 0xFF00A841, 0x01C032AC, 0, 0x00220000, 0x40000000)
MEASURED_CABLE = struct.pack('<4I', 0x1C6020C2, 0, 0x0A140310, 0x460A2643)
MEASURED_SCEDB = bytes.fromhex('AC3222000000000000000000000000000000000000030064F0')
MEASURED_SIDO = bytes.fromhex('01F0F080')

# Synthetic values for the named layouts and reserved/length cases.
SYNTHETIC_STATUS = bytes.fromhex('3518919F872A1A55')
SYNTHETIC_DRD_ID = struct.pack('<7I', 0xFF00A841, 0xD5601234, 0x11223344, 0xABCD0310,
                               0x6900000B, 0, 0x45000004)
SYNTHETIC_PDOS = struct.pack('<5I', 0x3FF00000 | (100 << 10) | 300,
    0x02000000 | (180 << 10) | 300, 0x40000000 | (240 << 20) | (100 << 10) | 181,
    0x80000000 | (400 << 20) | (320 << 10) | 250, 0)

# Independent literal words and expected values from USB PD 3.2 v1.1 Tables 6.9,
# 6.11–15 and the EPR slot layout in §6.5.15.1. Some reserved bits are deliberately
# set to catch masks that bleed into a neighboring value. These are decoder tests,
# not valid capability advertisements.
REFERENCE_SOURCE_PDOS = (
    ('2AE190FA', 0, dict(kind='fixed', voltage_mv=5000, current_ma=2500, peak_current_code=2,
                        reserved='00400000', capabilities=dict(
                            dual_role_power=True, usb_suspend_supported=False, unconstrained_power=True,
                            usb_communications_capable=False, dual_role_data=True,
                            unchunked_extended_messages_bit=0, epr_mode_capable_bit=1))),
    ('3FE2D12C', 0, dict(kind='fixed', voltage_mv=9000, current_ma=3000, peak_current_code=2,
                        reserved='3FC00000')),
    ('59A1B8AB', 1, dict(kind='battery', min_mv=5500, max_mv=20500, max_power_mw=42750)),
    ('99051D25', 2, dict(kind='variable', min_mv=16350, max_mv=20000, current_ma=2930)),
    ('CFA521BF', 3, dict(kind='pps', min_mv=3300, max_mv=21000, current_ma=3150)),
    ('EFF47D73', 3, dict(kind='spr_avs', max_current_9v_15v_ma=2870,
                        max_current_15v_20v_ma=3710, peak_current_code=3)),
    ('00000000', 0, dict(kind='reserved')),
    ('0008C1F4', 0, dict(kind='fixed', voltage_mv=28000, current_ma=5000, peak_current_code=0,
                        reserved='00000000')),
    ('000B41F4', 0, dict(kind='fixed', voltage_mv=36000, current_ma=5000, peak_current_code=0,
                        reserved='00000000')),
    ('000F01F4', 0, dict(kind='fixed', voltage_mv=48000, current_ma=5000, peak_current_code=0,
                        reserved='00000000')),
    ('DBC196F0', 3, dict(kind='epr_avs', min_mv=15000, max_mv=48000, pdp_w=240,
                        peak_current_code=2)),
)


def reference_pdo_fields():
    fields = {}
    for name, records in (('spr_pdos', REFERENCE_SOURCE_PDOS[:6]), ('epr_pdos', REFERENCE_SOURCE_PDOS)):
        raw = b''.join(bytes.fromhex(word)[::-1] for word, _, _ in records)
        decoded = [dict(index=i, raw=word, type=type_code, **values)
                   for i, (word, type_code, values) in enumerate(records, 1)]
        fields[name] = dict(status='observed', raw=raw.hex().upper(), decoded=decoded)
    return fields


def setUpModule():
    (ROOT/'artifacts').mkdir(exist_ok=True)
    tempfile.tempdir = str(ROOT/'artifacts')


def snapshot():
    result = model.new_snapshot('demo', '1234567890abcdef')
    result['capture_status'] = 'completed'
    model.set_field(result, 'spr_pdos', 'observed', struct.pack('<II', (100 << 10) | 300, (400 << 10) | 500))
    model.set_field(result, 'revision', 'pass', bytes.fromhex('00001731'))
    model.set_field(result, 'source_info', 'pass', bytes([100, 120, 140, 128]))
    return result


def event(kind, payload=b'', sop=0, source=True, extended=False):
    payload += b'\0' * (-len(payload) % 4)
    header = kind | 0x80 | (0x100 if source else 0) | ((len(payload) // 4) << 12) | (0x8000 if extended else 0)
    return event_from_words(sop, header, list(struct.unpack('<'+'I'*(len(payload)//4),payload)))


def extended(kind, data, number=0, total=None, chunked=True):
    if total is None:
        total = len(data)
    ext = total | (number << 11) | (0x8000 if chunked else 0)
    return event(kind, struct.pack('<H',ext)+data, extended=True)


class ModelTests(unittest.TestCase):
    def test_measured_source_status(self):
        d = model.decode('source_status', MEASURED_STATUS)
        self.assertEqual(d['present_input'], dict(external=True, external_type='ac',
            internal_battery=False, internal_non_battery=False, reserved=0))
        self.assertEqual(d['internal_temperature_code'], 0)
        self.assertIsNone(d['internal_temperature_c'])
        self.assertIsNone(d['present_battery_input']['active'])
        self.assertFalse(any(d['events'].values()))
        self.assertEqual(d['temperature_status']['code'], 0)
        self.assertFalse(any(d['power_status'].values()))
        self.assertEqual(d['power_state_change'], dict(state_code=0, indicator_code=0, reserved=0))
        self.assertEqual(d['trailing_hex'], '00')

    def test_status_length_flags_and_reserved_bytes(self):
        self.assertIsNone(model.decode('source_status', b'\x00' * 5))
        legacy = model.decode('source_status', bytes.fromhex('0102A0000000'))
        self.assertEqual(legacy['internal_temperature_code'], 1)
        self.assertIsNone(legacy['internal_temperature_c'])
        self.assertEqual(legacy['present_input']['external_type'], 'dc')
        self.assertEqual(legacy['present_battery_input']['reserved'], 0xA0)
        self.assertNotIn('power_state_change', legacy)
        self.assertNotIn('trailing_hex', legacy)
        d = model.decode('source_status', SYNTHETIC_STATUS)
        self.assertEqual(d['internal_temperature_c'], 53)
        self.assertEqual(d['present_battery_input']['active'], [0, 4, 7])
        self.assertEqual(d['events'], dict(ocp=True, otp=True, ovp=True, pps_current_limit=True, reserved=0x81))
        self.assertEqual(d['temperature_status'], dict(code=3, reserved=0x81))
        self.assertEqual(d['power_status'], dict(cable=True, other_ports=False, external_power=True,
            events=False, temperature=True, reserved=0))
        self.assertEqual(d['power_state_change'], dict(state_code=2, indicator_code=3, reserved=0))
        self.assertEqual(d['trailing_hex'], '55')

    def test_status_reserved_bits_do_not_imply_input_or_events(self):
        d = model.decode('source_status', bytes.fromhex('01E5A0E1F9C1FF00'))
        self.assertEqual(d['present_input'], dict(external=False, external_type=None,
            internal_battery=False, internal_non_battery=False, reserved=0xE5))
        self.assertEqual(d['present_battery_input'], dict(raw=0xA0, active=None, reserved=0xA0))
        self.assertEqual(d['events'], dict(ocp=False, otp=False, ovp=False,
            pps_current_limit=False, reserved=0xE1))
        self.assertEqual(d['temperature_status'], dict(code=0, reserved=0xF9))
        self.assertEqual(d['power_status'], dict(cable=False, other_ports=False, external_power=False,
            events=False, temperature=False, reserved=0xC1))
        self.assertEqual(d['power_state_change'], dict(state_code=7, indicator_code=7, reserved=0xC0))
        self.assertEqual(d['trailing_hex'], '00')

    def test_measured_source_identity(self):
        d = model.decode('source_identity', MEASURED_SOURCE_ID)
        self.assertEqual(d['vdos'], ['FF00A841', '01C032AC', '00000000', '00220000', '40000000'])
        identity = d['identity']
        self.assertEqual((identity['vid'], identity['pid'], identity['xid'], identity['bcd_device']),
                         ('32AC', '0022', '00000000', '0000'))
        self.assertEqual(identity['vdm']['version'], '2.1')
        self.assertEqual((identity['ufp_type'], identity['dfp_type'], identity['connector_type_code']),
                         ('none', 'power_brick', 2))
        self.assertFalse(identity['usb_host_capable'] or identity['usb_device_capable'] or identity['modal_operation'])
        self.assertEqual(identity['product_type_vdos'], [dict(index=5, kind='dfp', raw='40000000',
            version_code=2, version='1.2', host_capability_code=0, port_number=0, reserved='00000000')])
        self.assertNotIn('cable', model.decode('cable_identity', MEASURED_SOURCE_ID))

    def test_source_identity_layout_validation(self):
        drd = model.decode('source_identity', SYNTHETIC_DRD_ID)['identity']
        self.assertTrue(drd['usb_host_capable'] and drd['usb_device_capable'] and drd['modal_operation'])
        ufp, pad, dfp = drd['product_type_vdos']
        self.assertEqual((ufp['version'], ufp['device_capability_code'], ufp['alternate_mode_code'], ufp['speed_code']),
                         ('1.3', 9, 1, 3))
        self.assertEqual(pad['kind'], 'pad')
        self.assertEqual((dfp['host_capability_code'], dfp['port_number']), (5, 4))
        invalid_pad = SYNTHETIC_DRD_ID[:20] + bytes.fromhex('01000000') + SYNTHETIC_DRD_ID[24:]
        for raw in (invalid_pad, SYNTHETIC_DRD_ID[:-4], struct.pack('<I', 0xFF00B841) + MEASURED_SOURCE_ID[4:]):
            self.assertTrue(all(v['kind'] == 'unknown' for v in model.decode('source_identity', raw)['identity']['product_type_vdos']))
        unknown_vdo = model.decode('source_identity', MEASURED_SOURCE_ID[:-4] + struct.pack('<I', 0xE0000000))
        self.assertNotIn('version', unknown_vdo['identity']['product_type_vdos'][0])
        for raw in (MEASURED_SOURCE_ID[:12], struct.pack('<I', 0xFF00A881) + MEASURED_SOURCE_ID[4:]):
            self.assertNotIn('identity', model.decode('source_identity', raw))

    def test_source_identity_validates_header_and_preserves_vdo_bits(self):
        # Wrong SVID, unstructured, nonzero object position, reserved bit, and wrong command.
        for word in (0x1234A841, 0xFF002841, 0xFF00A941, 0xFF00A861, 0xFF00A842):
            with self.subTest(vdm=hex(word)):
                raw = struct.pack('<I', word) + MEASURED_SOURCE_ID[4:]
                decoded = model.decode('source_identity', raw)
                self.assertEqual(decoded, dict(vdos=[f'{word:08X}', '01C032AC', '00000000',
                                                     '00220000', '40000000']))
        raw = struct.pack('<5I', 0xFF00A841, 0x01E132AC, 0x89ABCDEF, 0x00220127, 0x58C00024)
        identity = model.decode('source_identity', raw)['identity']
        self.assertEqual((identity['xid'], identity['bcd_device'], identity['dfp_type'],
                          identity['connector_type_code'], identity['id_header_reserved']),
                         ('89ABCDEF', '0127', 'power_brick', 3, '00010000'))
        self.assertEqual(identity['product_type_vdos'], [dict(index=5, kind='dfp', raw='58C00024',
            version_code=2, version='1.2', host_capability_code=0, port_number=4, reserved='18C00020')])

    def test_measured_source_cap_ext_and_source_info(self):
        d = model.decode('source_cap_ext', MEASURED_SCEDB)
        self.assertEqual(d, dict(vid='32AC', pid='0022', xid='00000000', fw_version=0,
                                 hw_version=0, spr_pdp_w=100, epr_pdp_w=240))
        self.assertIsNone(model.decode('source_cap_ext', MEASURED_SCEDB[:24])['epr_pdp_w'])
        self.assertIsNone(model.decode('source_cap_ext', MEASURED_SCEDB[:23]))
        d = model.decode('source_info', MEASURED_SIDO)
        self.assertEqual(d, dict(port_reported_pdp_w=1, port_present_pdp_w=240,
            port_maximum_pdp_w=240, port_type=1, port_type_name='guaranteed', reserved=0))
        d = model.decode('source_info', bytes.fromhex('6432647F'))
        self.assertEqual((d['port_type_name'], d['reserved']), ('managed', 127))

    def test_fixed_capabilities_and_battery_variable_pdo_units(self):
        fixed, other, battery, variable, reserved = model.decode('spr_pdos', SYNTHETIC_PDOS)
        self.assertEqual(fixed['capabilities'], dict(dual_role_power=True, usb_suspend_supported=True,
            unconstrained_power=True, usb_communications_capable=True, dual_role_data=True,
            unchunked_extended_messages_bit=1, epr_mode_capable_bit=1))
        self.assertEqual((fixed['peak_current_code'], fixed['reserved']), (3, '00400000'))
        self.assertNotIn('capabilities', other)
        self.assertEqual(other['reserved'], '02000000')
        self.assertEqual((battery['kind'], battery['min_mv'], battery['max_mv'], battery['max_power_mw']),
                         ('battery', 5000, 12000, 45250))
        self.assertEqual((variable['kind'], variable['min_mv'], variable['max_mv'], variable['current_ma']),
                         ('variable', 16000, 20000, 2500))
        self.assertEqual((reserved['index'], reserved['kind']), (5, 'reserved'))

    def test_independent_source_pdo_reference(self):
        for name, field in reference_pdo_fields().items():
            with self.subTest(field=name):
                self.assertEqual(model.decode(name, bytes.fromhex(field['raw'])), field['decoded'])

    def test_fixed_source_capability_bits_in_isolation(self):
        for word, flag in (
            (0x200190FA, 'dual_role_power'), (0x100190FA, 'usb_suspend_supported'),
            (0x080190FA, 'unconstrained_power'), (0x040190FA, 'usb_communications_capable'),
            (0x020190FA, 'dual_role_data'), (0x010190FA, 'unchunked_extended_messages_bit'),
            (0x008190FA, 'epr_mode_capable_bit'),
        ):
            with self.subTest(flag=flag):
                first = model.decode('spr_pdos', struct.pack('<I', word))[0]
                self.assertEqual([name for name, value in first['capabilities'].items() if value], [flag])
                self.assertEqual((first['voltage_mv'], first['current_ma']), (5000, 2500))

    def test_measured_headerless_and_synthetic_active_cable(self):
        measured = model.decode('cable_identity', MEASURED_CABLE, 'km003c_cable')['cable']
        self.assertEqual((measured['layout'], measured['vid'], measured['pid'], measured['max_vbus_mv'],
                          measured['current_ma'], measured['epr_capable'], measured['speed_code']),
                         ('pd3_assumed', '20C2', '0A14', 50000, 5000, True, 3))
        self.assertEqual((measured['hw_version'], measured['fw_version'], measured['vdo_version_code'],
                          measured['latency_code'], measured['connector_type_code'], measured['termination_code']),
                         (4, 6, 0, 1, 2, 0))
        self.assertFalse(measured['vconn_required'])
        active = model.decode('cable_identity', struct.pack('<6I', 0xFF00A841, 0x20001234, 0, 0,
            0x126A56DB, 0x55667788))['cable']
        self.assertEqual((active['kind'], active['vdo_version_code'], active['latency_code'], active['termination_code']),
                         ('active', 3, 2, 2))
        self.assertTrue(active['sbu_supported'] and active['sbu_active'] and active['sop_double_prime_present'])
        self.assertTrue(active['vconn_required'])
        legacy = model.decode('cable_identity', struct.pack('<5I', 0xFF008041, 0x20001234, 0, 0,
            0x000037BA))['cable']
        self.assertEqual((legacy['layout'], legacy['vdo_version_code'], legacy['termination_code']), ('pd2', None, 2))
        self.assertNotIn('sbu_supported', legacy)

    def test_revision_is_pd_spec_version_not_usb_bus_version(self):
        d = model.decode('revision',bytes.fromhex('00001731'))
        self.assertEqual(d,dict(pd_revision='3.1',pd_spec_version='1.7',reserved=0))
    def test_source_info_is_pdp_not_temperature(self):
        d = model.decode('source_info',bytes([100,120,140,128]))
        self.assertEqual(d['port_present_pdp_w'],120)
        self.assertEqual(d['port_maximum_pdp_w'],140)
        self.assertNotIn('temp_raw',d)
    def test_unknown_values_remain_raw(self):
        d=model.decode('spr_pdos',struct.pack('<II',0,0xf1234567))
        self.assertEqual([p['kind'] for p in d],['reserved','raw'])
        self.assertIsNone(model.decode('pps_status',bytes.fromhex('ffffffff')))
    def test_pdo_units(self):
        d=model.decode('spr_pdos',struct.pack('<II', (100<<10)|300, 0xc0000000|(110<<17)|(33<<8)|60))
        self.assertEqual((d[0]['voltage_mv'],d[0]['current_ma']),(5000,3000))
        self.assertEqual((d[1]['min_mv'],d[1]['max_mv'],d[1]['current_ma']),(3300,11000,3000))
    def test_avs_source_pdo_fields(self):
        spr,epr=model.decode('epr_pdos',struct.pack('<II',0xE404B1F4,0xD7C096F0))
        self.assertEqual(spr,dict(index=1,raw='E404B1F4',type=3,kind='spr_avs',
            max_current_9v_15v_ma=3000,max_current_15v_20v_ma=5000,peak_current_code=1))
        self.assertEqual(epr,dict(index=2,raw='D7C096F0',type=3,kind='epr_avs',
            min_mv=15000,max_mv=48000,pdp_w=240,peak_current_code=1))
        self.assertNotIn('current_ma',epr) # EPR AVS encodes PDP, not a flat current.
    def test_avs_masks_and_zero_upper_band(self):
        spr=model.decode('spr_pdos',struct.pack('<I',0xE0000000|(250<<10)))[0]
        self.assertEqual((spr['max_current_9v_15v_ma'],spr['max_current_15v_20v_ma']),(2500,0))
        epr=model.decode('epr_pdos',struct.pack('<I',0xD0000000|(500<<17)|(150<<8)|200))[0]
        self.assertEqual((epr['min_mv'],epr['max_mv'],epr['pdp_w']),(15000,50000,200))


class TransferTests(unittest.TestCase):
    def test_url_pages_fit_device_budget_and_restore_same_data(self):
        original=snapshot()
        pages=transfer.encode_pages(original,base_url=transfer.DEFAULT_IMPORT_URL)
        self.assertTrue(all(p.startswith(transfer.DEFAULT_IMPORT_URL+'#KPD1:') for p in pages))
        self.assertTrue(all(len(p.encode('ascii'))<=152 for p in pages))
        self.assertEqual(transfer.decode_pages(list(reversed(pages)))['fields'],original['fields'])
        self.assertEqual(transfer.decode_pages([transfer.normalize_page(p) for p in pages])['fields'],original['fields'])
        old = transfer.encode_pages(original, base_url=transfer.DEFAULT_IMPORT_URL.replace('v=4', 'v=2'))
        self.assertEqual(transfer.decode_pages(old)['fields'], original['fields'])
    def test_url_validation_and_encoded_fragments(self):
        from urllib.parse import quote
        raw=transfer.encode_pages(snapshot())
        wrapped=['https://example.test/import#'+quote(p,safe='') for p in raw]
        self.assertEqual(transfer.decode_pages(wrapped)['fields'],snapshot()['fields'])
        for base in ('javascript:alert(1)','https://user:pass@example.test/','https://example.test/#x',
                     'https://example.test/?x=1','http://example.test/','https://example.test/'+'x'*180):
            with self.subTest(base=base),self.assertRaises(ValueError):transfer.encode_pages(snapshot(),base_url=base)
        local=transfer.encode_pages(snapshot(),base_url='http://localhost:8767/')
        self.assertEqual(transfer.decode_pages(local)['fields'],snapshot()['fields'])
    def test_roundtrip_reverse_order_and_duplicates(self):
        original=snapshot();pages=transfer.encode_pages(original)
        restored=transfer.decode_pages(list(reversed(pages))+pages)
        self.assertEqual(restored['fields'],original['fields'])
        self.assertEqual(restored['capture_status'],'completed')
        self.assertIsNone(restored['captured_at'])
    def test_missing_and_mixed_measurements_refused(self):
        pages=transfer.encode_pages(snapshot())
        other=snapshot();other['measurement_id']='0000000000000001'
        with self.assertRaisesRegex(ValueError,'Missing'):transfer.decode_pages(pages[:-1])
        with self.assertRaisesRegex(ValueError,'Mixed'):transfer.decode_pages(pages+transfer.encode_pages(other))
    def test_damaged_and_conflicting_pages_refused(self):
        pages=transfer.encode_pages(snapshot());page=pages[0]
        prefix,encoded=page.rsplit(':',1)
        altered=prefix+':'+('A' if encoded[0]!='A' else 'B')+encoded[1:]
        with self.assertRaisesRegex(ValueError,'checksum'):transfer.decode_pages([altered]+pages[1:])
        with self.assertRaisesRegex(ValueError,'Conflicting'):transfer.decode_pages([page,altered]+pages[1:])
    def test_boundaries_and_no_extra_metadata_in_qr(self):
        original=snapshot()
        original['device_serial']='PRIVATE-SERIAL'
        original['notes']=['not included']
        for size in (0,1,4,24,25,26,27,52,128,260):
            with self.subTest(size=size):
                model.set_field(original,'manufacturer','observed',b'A'*size)
                pages=transfer.encode_pages(original)
                self.assertTrue(all(len(p.encode('ascii'))<=152 for p in pages))
                self.assertEqual(transfer.decode_pages(pages)['fields'],original['fields'])
                self.assertNotIn(b'PRIVATE-SERIAL',transfer.pack(original))
    def test_maximum_snapshot_is_bounded(self):
        original=snapshot()
        for name in model.FIELDS:model.set_field(original,name,'partial',b'\0'*260)
        pages=transfer.encode_pages(original)
        self.assertLessEqual(len(pages),64)
        self.assertEqual(transfer.decode_pages(pages)['fields'],original['fields'])
    def test_bad_envelopes_and_binary_refused(self):
        for page in ('','KPD2:x','x'*153,transfer.encode_pages(snapshot())[0].replace(':01/',':00/')):
            with self.subTest(page=page[:20]),self.assertRaises(ValueError):transfer.decode_pages([page])
        data=transfer.pack(snapshot())
        for malformed in (b'',data[:-1],data[:14],data+data[14:19],data[:12]+b'\xff'+data[13:]):
            with self.subTest(length=len(malformed)),self.assertRaises(ValueError):transfer.unpack(malformed)
    def test_inconsistent_field_presence_rejected(self):
        data=bytearray(transfer.pack(snapshot()));data[16]=0
        with self.assertRaises(ValueError):transfer.unpack(data)
    def test_all_capture_states_roundtrip(self):
        for state in model.CAPTURE_STATES:
            original=snapshot();original['capture_status']=state
            self.assertEqual(transfer.decode_pages(transfer.encode_pages(original))['capture_status'],state)
    def test_random_roundtrips(self):
        rng=random.Random(140)
        for _ in range(100):
            original=snapshot()
            for name in model.FIELDS:
                size=rng.randrange(66)*4 if name in ('spr_pdos','epr_pdos','cable_identity','source_identity') else rng.randrange(261)
                model.set_field(original,name,rng.choice(model.STATES),rng.randbytes(size))
            self.assertEqual(transfer.decode_pages(transfer.encode_pages(original))['fields'],original['fields'])


class CollectorTests(unittest.TestCase):
    def setUp(self):self.snapshot=model.new_snapshot('km003c');self.collector=Collector(self.snapshot)
    def test_source_only_and_exact_packet_length(self):
        self.assertIsNone(self.collector.feed(event(12,bytes.fromhex('00001031'),source=False)))
        self.assertIsNone(self.collector.feed(event(12,bytes.fromhex('00001031'),sop=1)))
        self.assertEqual(self.collector.feed(event(12,bytes.fromhex('00001031'))),'revision')
        bad=event(12,bytes.fromhex('00001031'));bad['wire_hex']+='00'
        self.assertIsNone(self.collector.feed(bad))
    def test_extended_complete_and_chunked_reassembly(self):
        data=bytes(range(40))
        self.assertEqual(self.collector.feed(extended(7,data[:26],total=40)),'partial')
        self.assertEqual(self.collector.feed(extended(7,data[26:],1,40)),'manufacturer')
        self.assertEqual(self.snapshot['fields']['manufacturer']['raw'],data.hex().upper())
        self.assertEqual(self.collector.feed(extended(1,bytes(25),chunked=False)),'source_cap_ext')
    def test_duplicate_chunk_zero_preserves_complete_message(self):
        data=bytes(range(40))
        self.collector.feed(extended(7,data[:26],total=40));self.collector.feed(extended(7,data[26:],1,40))
        self.assertEqual(self.collector.feed(extended(7,data[:26],total=40)),'manufacturer')
    def test_orphan_conflicting_truncated_chunks_not_complete(self):
        self.assertEqual(self.collector.feed(extended(7,b'a'*14,1,40)),'partial')
        self.assertNotIn('raw',self.snapshot['fields']['manufacturer'])
        self.collector.feed(extended(7,b'a'*26,total=66))
        self.collector.feed(extended(7,b'b'*26,1,66))
        self.assertEqual(self.collector.feed(extended(7,b'c'*26,1,66)),'invalid')
        self.assertEqual(self.collector.feed(extended(1,b'a'*4,total=25)),'invalid')
    def test_requested_chunk_and_reserved_bit_not_treated_as_response(self):
        self.assertIsNone(self.collector.feed(event(7,struct.pack('<H',0x8404)+bytes(2),extended=True)))
        self.assertEqual(self.collector.feed(event(7,struct.pack('<H',0x8204)+bytes(4),extended=True)),'invalid')
    def test_detach_and_soft_reset_freeze_snapshot(self):
        for terminal in (dict(kind='connection',message='DISCONNECT'),event(13),event(13,source=False)):
            snap=model.new_snapshot('km003c');collector=Collector(snap)
            collector.feed(event(12,bytes.fromhex('00001031')))
            collector.feed(terminal);collector.feed(event(12,bytes.fromhex('00001032')))
            self.assertTrue(collector.closed)
            self.assertEqual(snap['fields']['revision']['decoded']['pd_revision'],'3.1')
    def test_initial_attach_then_new_attach_closes(self):
        attach=dict(kind='connection',message='CONNECT')
        self.assertIsNone(self.collector.feed(attach))
        self.assertEqual(self.collector.feed(attach),'connection_changed')
    def test_cable_ack_is_distinct_from_source_identity(self):
        vdos=[0xff00a041,0x18001234,0,0x56780001,0x000a2042]
        packet=event(15,struct.pack('<5I',*vdos),sop=1,source=False)
        self.assertEqual(self.collector.feed(packet),'cable_identity')
        self.assertEqual(self.snapshot['fields']['source_identity']['status'],'not_requested')
    def test_unrelated_vdm_ignored(self):
        packet=event(15,struct.pack('<5I',0xff00a042,1,2,3,4),sop=1,source=False)
        self.assertIsNone(self.collector.feed(packet))
    def test_negative_control_results(self):
        for kind,state in ((4,'rejected'),(12,'busy'),(16,'unsupported')):
            self.assertEqual(self.collector.feed(event(kind)),state)
    def test_recorded_stock_pd32_source_and_cable_response(self):
        rows=json.loads((ROOT/'tests/fixtures/stock-pd32-events.json').read_text())
        for row in rows:self.collector.feed(row)
        self.assertFalse(self.collector.closed)  # SOP' Soft Reset is not a source-session reset.
        fields=self.snapshot['fields']
        self.assertEqual(fields['revision']['decoded']['pd_revision'],'3.2')
        self.assertEqual(fields['revision']['decoded']['pd_spec_version'],'1.1')
        self.assertEqual(fields['source_cap_ext']['decoded']['epr_pdp_w'],240)
        self.assertEqual(fields['source_cap_ext']['decoded']['spr_pdp_w'],100)
        self.assertEqual(len(fields['epr_pdos']['decoded']),11)
        self.assertEqual(fields['epr_pdos']['decoded'][9]['voltage_mv'],48000)
        self.assertEqual(fields['manufacturer']['decoded']['name'],'')
        self.assertEqual(len(fields['cable_identity']['decoded']['vdos']),5)
        pages=transfer.encode_pages(self.snapshot)
        self.assertEqual(len(pages),3)
        self.assertEqual(transfer.decode_pages(pages)['fields'],fields)


class CH32Tests(unittest.TestCase):
    def test_boot_records_do_not_create_fake_session_zero(self):
        snap=ch32.import_log('@PD1,type=boot,session=0\n@PD1,type=revision,session=1,raw=31100000')
        self.assertEqual(snap['imported_session'],'1')
        with self.assertRaisesRegex(ValueError,'multiple boots'):
            ch32.import_log('@PD1,type=boot,session=0\n@PD1,type=revision,session=1,raw=31100000\n@PD1,type=boot,session=0\n@PD1,type=revision,session=1,raw=32100000','1')
    def test_actual_probe_keys_and_legacy_indexed_extended_payload(self):
        text='@PD1,type=attach,session=1,cc=2\n@PD1,type=probe,session=1,name=source_cap_ext,status=pass\n  EXT RAW8: A1 89 19 00\n  EXT payload bytes: '+ ' '.join(f'[{i}]={value:02X}' for i,value in enumerate(bytes(range(25))))+'\n@PD1,type=probe,session=1,name=pps_status,status=unsupported'
        snap=ch32.import_log(text)
        self.assertEqual(snap['fields']['source_cap_ext']['raw'],bytes(range(25)).hex().upper())
        self.assertEqual(snap['fields']['source_cap_ext']['status'],'pass')
        self.assertEqual(snap['fields']['pps_status']['status'],'unsupported')
        bad=ch32.import_log(text.replace('[12]=0C ',''))
        self.assertNotIn('raw',bad['fields']['source_cap_ext'])
    def test_current_pdo_set_drops_old_tail_positions(self):
        snap=ch32.import_log('@PD1,type=spr,session=1,pdo_count=2\n@PD1,type=spr_pdo,session=1,index=1,raw=0001912C\n@PD1,type=spr_pdo,session=1,index=2,raw=0002D12C\n@PD1,type=spr,session=1,pdo_count=1\n@PD1,type=spr_pdo,session=1,index=1,raw=0001912C')
        self.assertEqual(len(snap['fields']['spr_pdos']['decoded']),1)
    def test_same_raw_reinterpreted_without_rewriting_legacy(self):
        text='@PD1,type=revision,session=1,pd_major=3,pd_minor=1,usb_major=1,usb_minor=7,raw=31170000\n@PD1,type=source_info,session=1,pdp_w=100,temp_raw=120,present_input=0,raw=808C7864'
        snap=ch32.import_log(text)
        self.assertEqual(snap['fields']['revision']['decoded']['pd_spec_version'],'1.7')
        self.assertEqual(snap['fields']['source_info']['decoded']['port_present_pdp_w'],120)
        self.assertEqual(snap['legacy_records'][0]['usb_major'],'1')
    def test_multiple_sessions_require_selection(self):
        text='@PD1,type=spr_pdo,session=1,index=1,raw=0001912C\n@PD1,type=spr_pdo,session=2,index=1,raw=0002D12C'
        with self.assertRaises(ValueError):ch32.import_log(text)
        snap=ch32.import_log(text,'2');self.assertEqual(snap['fields']['spr_pdos']['raw'],'2CD10200')
    def test_missing_pdo_positions_and_nonraw_fields_are_not_invented(self):
        snap=ch32.import_log('@PD1,type=spr_pdo,session=1,index=2,raw=0001912C\n@PD1,type=manufacturer,session=1,vid=1234,pid=5678,name=Demo')
        self.assertEqual(snap['fields']['spr_pdos']['status'],'partial')
        self.assertNotIn('raw',snap['fields']['manufacturer'])


class CLITests(unittest.TestCase):
    def execute(self,args):
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):return main(args)
    def test_demo_export_import_workflow(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);out=root/'demo'
            self.assertEqual(self.execute(['demo','--output',str(out),'--qr']),0)
            self.assertEqual(self.execute(['import-qr',str(out/'qr/pages.txt'),'--output',str(root/'restored')]),0)
            a=json.loads((out/'snapshot.json').read_text());b=json.loads((root/'restored/snapshot.json').read_text())
            self.assertEqual(a['fields'],b['fields']);self.assertEqual(b['origin'],'demo')
            self.assertIn('width="240"',(out/'qr/qr-01.svg').read_text())
            self.assertEqual(len((out/'qr/qr-01.bin').read_bytes()),301)
            self.assertTrue((out/'qr/pages.txt').read_text().startswith(transfer.DEFAULT_IMPORT_URL+'#'))
            self.assertEqual(self.execute(['demo','--output',str(out)]),1)
    def test_dry_run_has_no_hardware_or_output(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'unused'
            self.assertEqual(self.execute(['capture','--dry-run','--output',str(output)]),0)
            self.assertFalse(output.exists())
        self.assertEqual(PROBES[0][0],'revision')
        self.assertTrue(all(not command.startswith('req=') for _,command in PROBES))
    def test_replay_stops_at_session_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'events.jsonl'
            source.write_text('\n'.join(json.dumps(e) for e in [event(12,bytes.fromhex('00001031')),dict(kind='connection',message='DISCONNECT'),event(12,bytes.fromhex('00001032'))]))
            self.assertEqual(self.execute(['replay',str(source),'--output',str(root/'out')]),0)
            snap=json.loads((root/'out/snapshot.json').read_text())
            self.assertEqual(snap['fields']['revision']['decoded']['pd_revision'],'3.1')
    def test_legacy_raw_qr_remains_available(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'demo'
            self.assertEqual(self.execute(['demo','--output',str(root),'--qr','--qr-format','raw']),0)
            self.assertTrue((root/'qr/pages.txt').read_text().startswith('KPD1:'))


class PhoneTests(unittest.TestCase):
    def test_javascript_parity_and_real_qr_decoder(self):
        vectors=[]
        for state in model.CAPTURE_STATES:
            snap=snapshot();snap['capture_status']=state
            snap['measurement_id']=f'{len(vectors)+1:016x}'
            model.set_field(snap,'manufacturer','observed',bytes.fromhex('34127856')+'試験電源'.encode())
            model.set_field(snap,'cable_identity','observed',struct.pack('<5I',0xff00a041,1,2,3,4))
            model.set_field(snap,'source_cap_ext','observed',bytes(range(25)))
            vectors.append(dict(snapshot=snap,pages=transfer.encode_pages(snap),
                                url_pages=transfer.encode_pages(snap,base_url=transfer.DEFAULT_IMPORT_URL)))
        simulated=model.new_snapshot('km003c','0000000000000007')
        simulated['capture_status']='completed'
        model.set_field(simulated,'cable_identity','emulation_enabled',struct.pack('<5I',0xff00a841,0x1c600000,0,0,0x000a4644))
        vectors.append(dict(snapshot=simulated,pages=transfer.encode_pages(simulated),
                            url_pages=transfer.encode_pages(simulated,base_url=transfer.DEFAULT_IMPORT_URL)))
        cable=model.new_snapshot('km003c_cable','0000000000000008')
        cable['capture_status']='completed'
        model.set_field(cable,'cable_identity','observed',struct.pack('<4I',0x18000000,0,0,0x00082650))
        cable_vector=dict(snapshot=cable,pages=transfer.encode_pages(cable),
                          url_pages=transfer.encode_pages(cable,base_url=transfer.DEFAULT_IMPORT_URL))
        vectors.append(cable_vector)
        partial = model.new_snapshot('km003c_cable', '0000000000000014')
        partial['capture_status'] = 'completed'
        model.set_field(partial, 'cable_identity', 'partial', struct.pack('<5I', 0, 0, 0, 0, 0x80871e4e))
        self.assertEqual(partial['fields']['cable_identity']['decoded'],
                         dict(vdos=['00000000'] * 4, svids=['8087', '1E4E']))
        vectors.append(dict(snapshot=partial, pages=transfer.encode_pages(partial),
                            url_pages=transfer.encode_pages(partial, base_url=transfer.DEFAULT_IMPORT_URL)))
        rdo=struct.pack('<I',0x2301fe3c)
        for identifier,origin,status,raw in (
            (17,'km003c_pps','pass',rdo+bytes.fromhex('fe000002')),
            (18,'km003c_pps','no_response',rdo),
            (19,'km003c','pass',bytes.fromhex('ffffff08')),
        ):
            snap=model.new_snapshot(origin,f'{identifier:016x}')
            model.set_field(snap,'pps_status',status,raw)
            vectors.append(dict(snapshot=snap,pages=transfer.encode_pages(snap),
                                url_pages=transfer.encode_pages(snap,base_url=transfer.DEFAULT_IMPORT_URL)))
        # Passive EPR, legacy directional bits, active without VBUS, deprecated voltage,
        # contradictory EPR and reserved speed, and an unknown VDO version.
        for identifier, header, identity, vdo in (
            (9, 0xff00a841, 0x18001234, 0x000a2644),
            (10, 0xff008041, 0x18001234, 0x00082652),
            (11, 0xff00a841, 0x20001234, 0x00683643),
            (12, 0xff00a841, 0x18001234, 0x00082222),
            (13, 0xff00a841, 0x18001234, 0x000a2627),
            (14, 0xff00a841, 0x18001234, 0x00e82650),
            (15, 0xff00a801, 0x18001234, 0x000a2644),
            (16, 0xff00a841, 0x20001234, 0x006a3653),
        ):
            snap=model.new_snapshot('ch32h417',f'{identifier:016x}')
            model.set_field(snap,'cable_identity','observed',struct.pack('<5I',header,identity,0,0x12340000,vdo))
            vectors.append(dict(snapshot=snap,pages=transfer.encode_pages(snap),
                                url_pages=transfer.encode_pages(snap,base_url=transfer.DEFAULT_IMPORT_URL)))
        measured_cable = model.new_snapshot('km003c_cable', '0000000000000020')
        model.set_field(measured_cable, 'cable_identity', 'observed', MEASURED_CABLE)
        vectors.append(dict(snapshot=measured_cable, pages=transfer.encode_pages(measured_cable),
                            url_pages=transfer.encode_pages(measured_cable, base_url=transfer.DEFAULT_IMPORT_URL)))
        for identifier, status, identity in ((33, MEASURED_STATUS, MEASURED_SOURCE_ID),
                                            (34, SYNTHETIC_STATUS, SYNTHETIC_DRD_ID),
                                            (35, SYNTHETIC_STATUS[:6], MEASURED_SOURCE_ID[:12]),
                                            (36, SYNTHETIC_STATUS[:5], struct.pack('<I', 0xFF00B841) + MEASURED_SOURCE_ID[4:]),
                                            (37, bytes.fromhex('01E5A0E1F9C1FF00'),
                                             struct.pack('<5I', 0xFF00A841, 0x01E132AC, 0x89ABCDEF,
                                                         0x00220127, 0x58C00024))):
            snap = model.new_snapshot('km003c', f'{identifier:016x}')
            model.set_field(snap, 'source_status', 'pass', status)
            model.set_field(snap, 'source_identity', 'pass', identity)
            model.set_field(snap, 'spr_pdos', 'observed', SYNTHETIC_PDOS)
            vectors.append(dict(snapshot=snap, pages=transfer.encode_pages(snap),
                                url_pages=transfer.encode_pages(snap, base_url=transfer.DEFAULT_IMPORT_URL)))
        reference = model.new_snapshot('km003c', '0000000000000026')
        reference['fields'].update(reference_pdo_fields())
        vectors.append(dict(snapshot=reference, pages=transfer.encode_pages(reference),
                            url_pages=transfer.encode_pages(reference, base_url=transfer.DEFAULT_IMPORT_URL)))
        saved_pages = (FIXTURES/'stock-probe-06-pages.txt').read_text(encoding='ascii').splitlines()
        saved = transfer.decode_pages(reversed(saved_pages))
        self.assertEqual(saved['fields']['source_status']['raw'], '00060000000000')
        self.assertEqual(transfer.encode_pages(saved, base_url=saved_pages[0].split('#')[0]), saved_pages)
        vectors.append(dict(snapshot=saved, pages=[p.split('#')[1] for p in saved_pages], url_pages=saved_pages))
        actual=model.new_snapshot('km003c','0000000000000006')
        actual['capture_status']='imported'
        collector=Collector(actual)
        for row in json.loads((ROOT/'tests/fixtures/stock-pd32-events.json').read_text()):collector.feed(row)
        vectors.append(dict(snapshot=actual,pages=transfer.encode_pages(actual),
                            url_pages=transfer.encode_pages(actual,base_url=transfer.DEFAULT_IMPORT_URL)))
        images=[]
        with tempfile.TemporaryDirectory(dir=ROOT/'artifacts') as folder:
            for vector in (cable_vector, vectors[-1]):
                path=Path(folder)/vector['snapshot']['origin']
                transfer.write_qr(vector['url_pages'],path)
                for i,text in enumerate(vector['url_pages'],1):
                    images.append(dict(text=text,packed=base64.b64encode((path/f'qr-{i:02}.bin').read_bytes()).decode()))
        completed=subprocess.run(['node',str(ROOT/'tests/check_phone.cjs')],
                                 input=json.dumps(dict(vectors=vectors,images=images)),
                                 text=True,capture_output=True,cwd=ROOT,timeout=30)
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)

    def test_browser_export_and_history_handlers(self):
        # Exercise both measurement origins also used by the built-in demos.
        measurements = {}
        for origin, field, status, raw in (
            ('km003c_cable', 'cable_identity', 'observed', MEASURED_CABLE),
            ('km003c_pps', 'pps_status', 'pass', bytes.fromhex('64FE0123FE000002')),
        ):
            capture = model.new_snapshot(origin, '0000000000000029')
            model.set_field(capture, field, status, raw)
            measurements[origin] = transfer.encode_pages(capture)
        completed = subprocess.run(['node', str(ROOT/'tests/check_phone_ui.cjs')],
                                   input=json.dumps(measurements), text=True, encoding='utf-8',
                                   capture_output=True, cwd=ROOT, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__=='__main__':unittest.main()
