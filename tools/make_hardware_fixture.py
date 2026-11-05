"""Extract only protocol bytes from the qualified local capture for regression tests."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / 'captures/stock-probe-06/events.jsonl'
keep = {'SOURCE_CAPABILITIES', 'EPR_SOURCE_CAPABILITIES', 'REVISION',
        'SOURCE_CAPABILITIES_EXTENDED', 'SOURCE_INFO', 'STATUS', 'MANUFACTURER_INFO',
        'GET_MANUFACTURER_INFO', 'VENDOR_DEFINED', 'SOFT_RESET'}
events = [json.loads(line) for line in source.read_text().splitlines()]
events = [{key: row[key] for key in ('kind', 'sop', 'header', 'wire_hex')}
          for row in events if row.get('message') in keep]
destination = root / 'tests/fixtures/stock-pd32-events.json'
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(events, indent=2) + '\n', encoding='utf-8')
print(f'Protocol-only regression fixture: {len(events)} events')
