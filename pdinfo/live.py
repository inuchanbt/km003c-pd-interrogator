"""Experimental pure-stock bridge via the existing KM003C CLI transports.

The native firmware owns MessageID/GoodCRC and contract maintenance. Host
requests cannot substitute for a timing-correct firmware policy engine.
"""
from contextlib import ExitStack
from dataclasses import asdict
import importlib
import json
from pathlib import Path
import queue
import sys
import threading
import time

from .events import Collector
from .model import new_snapshot, set_field

# Revision is intentionally first. A fresh Get_Source_Cap can upset some sources.
PROBES = (('revision', '009800'), ('source_cap_ext', '009100'),
          ('source_status', '009200'), ('source_info', '009700'),
          # Native PDM inserts the Extended Header. Supply only Target/Ref;
          # including an Extended Header here would put it inside the payload.
          ('manufacturer', '0086900000'))


def validate_native_tx(name, event):
    """None for unrelated traffic; bool for the observed host-request type."""
    h = event.get('header')
    if event.get('kind') != 'pd' or event.get('sop') != 0 or type(h) is not int or h & 0x100:
        return None
    requested_type = dict(revision=24, source_cap_ext=17, source_status=18, source_info=23, manufacturer=6)[name]
    if h & 31 != requested_type:
        return None
    try:
        raw = bytes.fromhex(event.get('wire_hex', ''))
    except ValueError:
        return False
    if len(raw) != 2 + 4 * (h >> 12 & 7) or int.from_bytes(raw[:2], 'little') != h:
        return False
    if name != 'manufacturer':
        return len(raw) == 2 and not h & 0x8000
    if not h & 0x8000 or len(raw) < 6:
        return False
    ext = int.from_bytes(raw[2:4], 'little')
    size = ext & 0x1FF
    # Stock PDM rounds the application payload up to the input header's NDO
    # before adding its own Extended Header. A zero-padded four-byte variant
    # is observed on this meter and is accepted only with Target/Ref both zero.
    return (ext & 0x7E00 == 0 and size in (2, 4) and len(raw) >= 4 + size
            and raw[4:4 + size] == bytes(size))


def load_km003c(root):
    root = Path(root).resolve()
    if not (root / 'km003c_modules' / 'transport.py').is_file():
        raise ValueError('--km003c-root must point to the existing KM003C CLI checkout')
    sys.path.insert(0, str(root))
    return tuple(importlib.import_module('km003c_modules.' + name)
                 for name in ('transport', 'protocol', 'sweep'))


def read_measurement(args, transport, protocol):
    with transport.Meter(args, transport=transport.HidTransport(args)) as meter:
        samples = [protocol.decode_adc(packet.payload) for packet in
                   protocol.logical_packets(meter.get_data(protocol.ADC))
                   if packet.attribute == protocol.ADC]
    if len(samples) != 1:
        raise RuntimeError('Could not read the measurement-side VBUS/current')
    return samples[0]


def read_after_cleanup(args, transport, protocol):
    sample = read_measurement(args, transport, protocol)
    return dict(vbus_v=sample.voltage, ibus_a=sample.current,
                voltage_verified=0 <= sample.voltage <= 5.5)


def read_preflight(args, transport, protocol, *, require_vbus=False):
    sample = read_measurement(args, transport, protocol)
    cc1_mv, cc2_mv = getattr(sample, 'cc1_mV', 0), getattr(sample, 'cc2_mV', 0)
    # With no downstream sink, an attached source can advertise Rp while VBUS
    # stays off until the native PDM asserts Rd. Do not demand VBUS before that.
    cc_hint = (200 <= max(cc1_mv, cc2_mv) <= 3600 and min(cc1_mv, cc2_mv) < 200)
    vbus_present = 4.0 <= sample.voltage <= 55.0
    if not vbus_present and (require_vbus or not (0 <= sample.voltage < 4 and cc_hint)):
        raise RuntimeError(f'Measurement input is {sample.voltage:.3f} V; CC1/CC2={cc1_mv:.1f}/{cc2_mv:.1f} mV. '
                           + ('Native PD attach did not establish VBUS.' if require_vbus else 'Connect the source to the measurement input first.'))
    if abs(sample.current) > .1:
        raise RuntimeError(f'Measurement current is {sample.current:.3f} A. Disconnect the downstream load before probing.')
    return dict(vbus_v=sample.voltage, ibus_a=sample.current, cc1_mv=cc1_mv, cc2_mv=cc2_mv,
                cc_source_hint=cc_hint, requires_pd_attach=not vbus_present,
                policy='vbus_or_cc_hint_then_confirm_vbus_after_native_attach_load_below_100mA')


def capture(args, output):
    transport, protocol, sweep = load_km003c(args.km003c_root)
    # Select CDC and USB by the same real device serial, never arbitrary first matches.
    ports = [p for p in transport.serial_ports(args.vid, args.pid)
             if (not args.port or p['port'].lower() == args.port.lower())
             and (not args.serial or p['serial'] == args.serial)]
    if len(ports) != 1 or not ports[0]['serial']:
        raise ValueError('Select one KM003C CDC port with a serial number using --port/--serial')
    args.port, args.serial = ports[0]['port'], ports[0]['serial']
    snapshot = new_snapshot('km003c')
    snapshot['device_serial'] = args.serial  # Kept in local JSON; excluded from QR.
    snapshot['experimental'] = True
    snapshot['setup'] = []
    snapshot['errors'] = []
    snapshot['probes'] = []
    snapshot['notes'] = ['Experimental stock-PDM host probe. Validation is limited to the recorded device/source pairing.',
                         'PPS contract probes and host-managed EPR entry are not implemented. '
                         'EPR PDOs and cable identity are retained if observed.',
                         'No independent CRC/EOP validity or complete bus trace is available.']
    snapshot['preflight'] = read_preflight(args, transport, protocol)
    collector = Collector(snapshot)
    received, stop, failed = queue.Queue(maxsize=4096), threading.Event(), []
    clock = protocol.ClockUnwrapper()
    failure = None
    with ExitStack() as stack:
        event_log = stack.enter_context((output / 'events.jsonl').open('x', encoding='utf-8'))
        raw_log = stack.enter_context((output / 'frames.jsonl').open('x', encoding='utf-8'))
        serial = transport.SerialTransport(args)
        stack.callback(serial.close)
        serial.open()
        try:
            meter = stack.enter_context(transport.Meter(args, transport=transport.UsbTransport(args)))

            def pump():
                try:
                    count = 0
                    while not stop.is_set():
                        frame = meter.get_data(protocol.PD_PACKET)
                        now = time.monotonic()
                        count += 1
                        if count > 10000:
                            raise RuntimeError('Capture exceeds frame limit')
                        raw_log.write(json.dumps(dict(host_monotonic=now, raw_hex=frame.hex())) + '\n')
                        raw_log.flush()
                        for packet in protocol.logical_packets(frame):
                            if packet.attribute == protocol.PD_PACKET:
                                _, events = protocol.decode_pd(packet.payload, clock)
                                for event in events:
                                    row = asdict(event)
                                    row['host_monotonic'] = now
                                    event_log.write(json.dumps(row) + '\n')
                                    received.put_nowait((now, row))
                                event_log.flush()
                        stop.wait(.04)
                except Exception as exc:
                    failed.append(str(exc))

            worker = threading.Thread(target=pump, name='km003c-info-capture', daemon=True)
            worker.start()
            try:
                sweep.initialize_trigger(serial, args, snapshot['setup'], require_pdo=True, wait_for_epr=False)
                snapshot['after_attach'] = read_preflight(args, transport, protocol, require_vbus=True)

                def drain(after=None, expected=None, request=None):
                    outcome = None
                    while True:
                        try:
                            when, row = received.get_nowait()
                        except queue.Empty:
                            break
                        if request is not None and when >= after:
                            valid_tx = validate_native_tx(expected, row)
                            if valid_tx is not None:
                                request['native_tx_hex'] = row['wire_hex']
                                request['native_tx_validated'] = valid_tx
                                if not valid_tx:
                                    raise RuntimeError('Native TX payload differs from the information request: ' + expected)
                                if expected == 'manufacturer':
                                    wire = bytes.fromhex(row['wire_hex'])
                                    size = int.from_bytes(wire[2:4], 'little') & 511
                                    request['native_extended_data_size'] = size
                                    if size == 4:
                                        request['variant'] = 'native_padded_4byte_target0_ref0'
                        value = collector.feed(row)
                        if collector.closed:
                            raise RuntimeError('PD session ended: ' + collector.stop_reason)
                        if after is not None and when >= after:
                            if value == expected:
                                outcome = 'pass'
                            elif value in ('unsupported', 'rejected', 'busy') and outcome != 'pass':
                                outcome = value
                    if failed:
                        raise RuntimeError('USB capture failed: ' + '; '.join(failed))
                    return outcome

                drain()
                settling = bytearray()
                settle_deadline = time.monotonic() + args.native_settle
                while time.monotonic() < settle_deadline:
                    settling.extend(serial.read(min(.05, max(0, settle_deadline - time.monotonic()))))
                    if len(settling) > 1024 * 1024:
                        raise RuntimeError('Native settling response exceeds size limit')
                    drain()
                snapshot['native_settle'] = dict(seconds=args.native_settle, response_hex=settling.hex().upper())
                snapshot['before_probes'] = read_preflight(args, transport, protocol, require_vbus=True)
                for name, data in PROBES:
                    drain()
                    entry = dict(field=name, command='pd data=' + data)
                    snapshot['probes'].append(entry)
                    response = bytearray()
                    since = time.monotonic()
                    try:
                        serial.write(entry['command'].encode('ascii'))
                        deadline = since + args.probe_window
                        outcome = None
                        while time.monotonic() < deadline:
                            response.extend(serial.read(min(.05, max(0, deadline - time.monotonic()))))
                            if len(response) > 1024 * 1024:
                                raise RuntimeError('ASCII response exceeds size limit')
                            value = drain(since, name, entry)
                            if value == 'pass' or (value and outcome != 'pass'):
                                outcome = value
                            # Finish the bounded window even on success, draining the
                            # native ASCII reply and late duplicates before another probe.
                        if sweep._rejects(response):
                            outcome = 'tx_failed'
                        if outcome is None:
                            state = snapshot['fields'][name]['status']
                            outcome = state if state in ('partial', 'invalid') else 'no_response'
                        field = snapshot['fields'][name]
                        set_field(snapshot, name, outcome,
                                  bytes.fromhex(field['raw']) if 'raw' in field else None)
                        entry['status'] = outcome
                    finally:
                        entry['response_hex'] = response.hex().upper()
                    # Do not keep sending information requests into an unresponsive policy engine.
                    if outcome in ('tx_failed', 'no_response', 'partial', 'invalid'):
                        snapshot['notes'].append('Remaining probes stopped after ' + outcome)
                        break
                snapshot['capture_status'] = 'completed'
            finally:
                snapshot['cleanup'] = sweep.release_trigger(serial, args)
                stop.set()
                worker.join(args.timeout + 1)
                if worker.is_alive():
                    raise RuntimeError('USB capture did not stop within its I/O timeout')
                if snapshot['cleanup']['status'] == 'failed':
                    raise RuntimeError('Trigger cleanup failed')
                snapshot['cleanup'].update(read_after_cleanup(args, transport, protocol))
                if not snapshot['cleanup']['voltage_verified']:
                    raise RuntimeError('VBUS did not return below 5.5 V after trigger cleanup')
                if failed:
                    raise RuntimeError('USB capture failed: ' + '; '.join(failed))
        except (Exception, KeyboardInterrupt) as exc:
            failure = exc
            if 'cleanup' not in snapshot:
                snapshot['cleanup'] = sweep.release_trigger(serial, args)
            snapshot['capture_status'] = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
            snapshot['errors'].append(str(exc) or 'Interrupted')
            current = snapshot['probes'][-1] if snapshot['probes'] else None
            if current and 'status' not in current:
                state = 'reset' if collector.stop_reason == 'soft_reset' else 'invalid'
                field = snapshot['fields'][current['field']]
                set_field(snapshot, current['field'], state,
                          bytes.fromhex(field['raw']) if 'raw' in field else None)
                current['status'] = state
    return snapshot, failure
