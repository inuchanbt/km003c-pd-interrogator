from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from pdinfo import live
from pdinfo.__main__ import parser
from test_pdinfo import event, extended, ROOT


def setUpModule():
    (ROOT/'artifacts').mkdir(exist_ok=True)


class LiveTests(unittest.TestCase):
    def execute(self, scenario='success'):
        packets=queue.Queue();writes=[];closed=[];setup=[]
        terminal=False
        class Serial:
            pending=b''
            def open(self):pass
            def close(self):closed.append('serial')
            def write(self,data):
                writes.append(data.decode())
                if not data.startswith(b'pd data='):return
                index=[command for _,command in live.PROBES].index(data.decode().split('=')[1])
                if scenario=='interrupt':raise KeyboardInterrupt()
                if scenario=='write_error':raise OSError('test short write')
                if scenario=='usb_error':packets.put(RuntimeError('test USB failure'));return
                if scenario=='no_response':return
                if scenario=='tx_rejected':self.pending=b'error: raw TX rejected\n';return
                if scenario=='soft_reset':packets.put(event(13));return
                if scenario=='disconnect':packets.put(dict(kind='connection',message='DISCONNECT'));return
                if scenario=='unsupported':packets.put(event(16));return
                if scenario=='invalid':packets.put(event(12,b'a'*8));return
                responses=[event(12,bytes.fromhex('00001031')),extended(1,bytes(25)),
                           extended(2,bytes(7)),event(11,bytes([140,140,140,128])),
                           extended(7,bytes.fromhex('34127856')+b'Test\0')]
                packets.put(responses[index])
            def read(self,timeout):
                time.sleep(min(.002,timeout));response=self.pending;self.pending=b'';return response
        class Meter:
            def __init__(self,*args,**kwargs):pass
            def __enter__(self):
                if scenario=='meter_error':raise RuntimeError('test USB open failure')
                return self
            def __exit__(self,*args):closed.append('meter')
            def get_data(self,attribute):
                try:packet=packets.get(timeout=.005)
                except queue.Empty:return b'[]'
                if isinstance(packet,Exception):raise packet
                return json.dumps([packet]).encode()
        def initialize(serial,args,records,**kwargs):
            for command in ('pdm open','pdm set type=2,em=0,sink=1','entry pd','pd pdo'):
                serial.write(command.encode());records.append(dict(command=command))
            if scenario=='setup_error':raise RuntimeError('test setup failure')
            packets.put(event(1,bytes.fromhex('2c910100')))
        def release(serial,args):
            serial.write(b'reset');serial.write(b'pdm close')
            return dict(status='failed' if scenario=='cleanup_error' else 'acknowledged',errors=[])
        transport=NS(serial_ports=lambda *args:[dict(port='COM14',serial='same-meter')],
                     SerialTransport=lambda args:Serial(),Meter=Meter,UsbTransport=lambda args:object())
        protocol=NS(PD_PACKET=16,ClockUnwrapper=lambda:None,
                    logical_packets=lambda frame:[NS(attribute=16,payload=frame)],
                    decode_pd=lambda payload,clock:(None,json.loads(payload)))
        sweep=NS(initialize_trigger=initialize,release_trigger=release,
                 _rejects=lambda response:bytes(response).startswith(b'error'))
        args=parser().parse_args(['capture','--output','unused','--probe-window','.15','--native-settle','.05'])
        args.timeout=.1
        with tempfile.TemporaryDirectory(dir=ROOT/'artifacts') as folder:
            with patch.object(live,'load_km003c',return_value=(transport,protocol,sweep)), \
                    patch.object(live,'read_preflight',return_value=dict(vbus_v=5.0,ibus_a=0.0)), \
                    patch.object(live,'read_after_cleanup',return_value=dict(vbus_v=.006,ibus_a=0,voltage_verified=True)), \
                    patch.object(live,'asdict',side_effect=lambda row:dict(row)),redirect_stdout(io.StringIO()):
                result,error=live.capture(args,Path(folder))
            logs={p.name:p.read_text() for p in Path(folder).glob('*.jsonl')}
        return result,error,writes,closed,logs

    def test_success_shared_serial_all_probes_raw_evidence_and_cleanup(self):
        snapshot,error,writes,closed,logs=self.execute()
        self.assertIsNone(error)
        self.assertEqual(snapshot['capture_status'],'completed')
        self.assertEqual([p['status'] for p in snapshot['probes']],['pass']*5)
        self.assertEqual(writes[-2:],['reset','pdm close'])
        self.assertIn('serial',closed);self.assertIn('meter',closed)
        self.assertIn('wire_hex',logs['events.jsonl']);self.assertIn('raw_hex',logs['frames.jsonl'])
        self.assertEqual(snapshot['fields']['source_info']['decoded']['port_maximum_pdp_w'],140)
        self.assertEqual(snapshot['fields']['pps_status']['status'],'not_requested')
    def test_reset_detach_interrupt_and_io_failures_always_cleanup(self):
        for scenario in ('soft_reset','disconnect','interrupt','write_error','usb_error','setup_error','meter_error','cleanup_error'):
            with self.subTest(scenario=scenario):
                snapshot,error,writes,closed,_=self.execute(scenario)
                self.assertIsNotNone(error)
                self.assertEqual(snapshot['capture_status'],'interrupted' if scenario=='interrupt' else 'failed')
                self.assertEqual(writes[-2:],['reset','pdm close'])
                self.assertIn('serial',closed)
                self.assertLessEqual(len(snapshot['probes']),5 if scenario=='cleanup_error' else 1)
                if scenario=='soft_reset':self.assertEqual(snapshot['fields']['revision']['status'],'reset')
    def test_unanswered_or_tx_rejected_probe_stops_sequence(self):
        for scenario,status in (('no_response','no_response'),('tx_rejected','tx_failed'),('invalid','invalid')):
            with self.subTest(scenario=scenario):
                snapshot,error,writes,_,_=self.execute(scenario)
                self.assertIsNone(error)
                self.assertEqual(snapshot['probes'][0]['status'],status)
                self.assertEqual(len(snapshot['probes']),1)
                self.assertEqual(snapshot['fields']['source_info']['status'],'not_requested')
                self.assertEqual(writes[-2:],['reset','pdm close'])
    def test_unsupported_reply_remains_distinct_from_no_response(self):
        snapshot,error,_,_,_=self.execute('unsupported')
        self.assertIsNone(error)
        self.assertEqual([p['status'] for p in snapshot['probes']],['unsupported']*5)


class PreflightTests(unittest.TestCase):
    def test_source_required_and_loaded_setup_refused_before_pd_commands(self):
        class Meter:
            def __init__(self,*args,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def get_data(self,attribute):return b'frame'
        transport=NS(Meter=Meter,HidTransport=lambda args:object())
        for voltage,current,allowed in ((.005,0,False),(5,1,False),(5,0,True),(20,-.05,True)):
            with self.subTest(voltage=voltage,current=current):
                protocol=NS(ADC=1,logical_packets=lambda frame:[NS(attribute=1,payload=b'')],
                            decode_adc=lambda raw:NS(voltage=voltage,current=current))
                if allowed:self.assertEqual(live.read_preflight(NS(),transport,protocol)['vbus_v'],voltage)
                else:
                    with self.assertRaises(RuntimeError):live.read_preflight(NS(),transport,protocol)
        protocol=NS(ADC=1,logical_packets=lambda frame:[NS(attribute=1,payload=b'')],
                    decode_adc=lambda raw:NS(voltage=.005,current=0,cc1_mV=3173,cc2_mV=.4))
        self.assertTrue(live.read_preflight(NS(),transport,protocol)['requires_pd_attach'])
        with self.assertRaisesRegex(RuntimeError,'did not establish VBUS'):
            live.read_preflight(NS(),transport,protocol,require_vbus=True)


class NativeTXTests(unittest.TestCase):
    def test_observed_extended_header_wrapping_regression(self):
        def tx(raw):
            data=bytes.fromhex(raw)
            return dict(kind='pd',sop=0,header=int.from_bytes(data[:2],'little'),wire_hex=raw)
        # First attempt doubled the Extended Header; second became PS_RDY.
        self.assertFalse(live.validate_native_tx('manufacturer',tx('86a60480028000000000')))
        self.assertFalse(live.validate_native_tx('manufacturer',tx('8606')))
        # Correct on-wire request: Extended Header DataSize=2, Target=0, Ref=0.
        self.assertTrue(live.validate_native_tx('manufacturer',tx('869002800000')))
        self.assertTrue(live.validate_native_tx('manufacturer',tx('86a60480000000000000')))
        self.assertTrue(live.validate_native_tx('revision',tx('9802')))
        self.assertIsNone(live.validate_native_tx('revision',event(12,b'\0'*4)))


if __name__=='__main__':unittest.main()
