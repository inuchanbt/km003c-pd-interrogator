import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys

from .ch32 import import_log
from .events import Collector
from .model import new_snapshot, set_field, validate
from .transfer import encode_pages, decode_pages, write_qr


def positive_float(value):
    import math
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError('must be a finite positive number')
    return result


def parser():
    p = argparse.ArgumentParser(description='KM003C information + smartphone QR prototype (no firmware writes)')
    sub = p.add_subparsers(dest='command', required=True)
    live = sub.add_parser('capture', help='experimental stock firmware interrogation through USB + CDC')
    live.add_argument('--km003c-root', type=Path, default=Path('D:/KM003C'))
    live.add_argument('--port')
    live.add_argument('--serial')
    live.add_argument('--output', type=Path, required=True, help='new directory; existing paths are refused')
    live.add_argument('--probe-window', type=positive_float, default=2.0)
    live.add_argument('--native-settle', type=positive_float, default=2.0,
                      help='quiet host time for native SPR/EPR negotiation before GET_* probes')
    live.add_argument('--dry-run', action='store_true')
    live.add_argument('--qr', action='store_true')
    live.set_defaults(vid=0x5FC9, pid=0x63, baud=115200, timeout=2.0,
                      type=2, em=0, sink=1, pps_sweep='', initialize=True,
                      wait=1.0, entry_timeout=10.0, pdm_startup_wait=2.0,
                      keep_trigger=False, quiet=False, transport='cdc', hid_path=None)
    ch32 = sub.add_parser('import-ch32', help='convert one @PD1 session into a shared snapshot')
    ch32.add_argument('input', type=Path)
    ch32.add_argument('--session')
    replay = sub.add_parser('replay', help='extract observed fields from a KM003C events JSONL')
    replay.add_argument('input', type=Path)
    qr = sub.add_parser('export-qr', help='encode a snapshot as bounded device-size QR pages')
    qr.add_argument('input', type=Path)
    decode = sub.add_parser('import-qr', help='restore pages.txt to a snapshot')
    decode.add_argument('input', type=Path)
    demo = sub.add_parser('demo', help='write a clearly marked synthetic 140 W example')
    for command in (ch32, replay, qr, decode, demo):
        command.add_argument('--output', type=Path, required=True, help='new output directory')
        if command is not qr:
            command.add_argument('--qr', action='store_true')
    serve = sub.add_parser('serve', help='serve the local phone importer (no uploads or remote services)')
    serve.add_argument('--bind', default='127.0.0.1', help='use 0.0.0.0 to allow a phone on the same LAN')
    serve.add_argument('--port', type=int, default=8767)
    return p


def save(snapshot, directory, qr=False):
    validate(snapshot)
    # Rebuild decoded fields from raw so stale cached JSON does not become evidence.
    for name, field in list(snapshot['fields'].items()):
        set_field(snapshot, name, field['status'], bytes.fromhex(field['raw']) if 'raw' in field else None)
    (directory / 'snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if qr:
        write_qr(encode_pages(snapshot), directory / 'qr')
    print(f'Saved: {directory.resolve() / "snapshot.json"}')


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'serve':
            web = Path(__file__).resolve().parents[1] / 'phone'
            handler = partial(SimpleHTTPRequestHandler, directory=str(web))
            server = ThreadingHTTPServer((args.bind, args.port), handler)
            print(f'Phone importer: http://{args.bind}:{server.server_port}/', flush=True)
            print('For a phone, open this PC\'s LAN IP on the same port. Ctrl+C stops the server.', flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
            return 0
        if args.command == 'capture' and args.dry_run:
            from .live import PROBES
            print('USB + CDC: select the same KM003C serial; start raw/event recording')
            print('pdm open\npdm set type=2,em=0,sink=1\nentry pd\npd pdo')
            for name, data in PROBES:
                print(f'{name}: pd data={data}')
            print('Abort on disconnect/reset, rejected TX, or unanswered request.\nCleanup: reset -> pdm close')
            return 0
        if args.command == 'demo':
            snapshot = new_snapshot('demo', '0000000000000140')
            snapshot['capture_status'] = 'completed'
            snapshot['notes'] = ['SYNTHETIC DEMO. Not a real measurement.']
            import struct
            set_field(snapshot, 'spr_pdos', 'observed', struct.pack('<II', (100 << 10) | 300, (400 << 10) | 500))
            set_field(snapshot, 'epr_pdos', 'observed', struct.pack('<8I', (100 << 10) | 300,
                      (400 << 10) | 500, 0, 0, 0, 0, 0, (560 << 10) | 500))
            set_field(snapshot, 'revision', 'observed', bytes.fromhex('00001031'))
            set_field(snapshot, 'source_info', 'observed', bytes([140, 140, 140, 128]))
        elif args.command == 'import-ch32':
            snapshot = import_log(args.input.read_text(encoding='utf-8-sig', errors='replace'), args.session)
        elif args.command == 'replay':
            snapshot = new_snapshot('km003c')
            snapshot['capture_status'] = 'imported'
            snapshot['imported_at'] = snapshot['captured_at']
            snapshot['captured_at'] = None
            collector = Collector(snapshot)
            with args.input.open(encoding='utf-8-sig') as handle:
                for line in handle:
                    collector.feed(json.loads(line))
                    if collector.closed:
                        snapshot['notes'] = ['Replay stopped at ' + collector.stop_reason + '; subsequent events are excluded.']
                        break
        elif args.command == 'export-qr':
            snapshot = json.loads(args.input.read_text(encoding='utf-8-sig'))
            validate(snapshot)
        elif args.command == 'import-qr':
            snapshot = decode_pages(args.input.read_text(encoding='ascii').splitlines())
        else:
            snapshot = None
        # Fail before opening hardware if QR dependencies are unavailable.
        if args.command == 'export-qr' or args.qr:
            try:
                import qrcode  # noqa: F401
            except ImportError as exc:
                raise RuntimeError('Install requirements.txt before QR export') from exc
        args.output.mkdir(parents=True, exist_ok=False)
        if args.command == 'capture':
            from .live import capture
            snapshot, failure = capture(args, args.output)
            save(snapshot, args.output, args.qr)
            if failure:
                print('Capture did not complete: ' + (str(failure) or 'Interrupted'), file=sys.stderr)
                return 130 if isinstance(failure, KeyboardInterrupt) else 1
        else:
            save(snapshot, args.output, args.command == 'export-qr' or args.qr)
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print('Error: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
