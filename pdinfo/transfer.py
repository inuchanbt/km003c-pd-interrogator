"""Small fixed-memory TLV transfer, designed for a 240x240 display."""
import base64
import binascii
import re
import struct
import zlib
from urllib.parse import urlsplit, unquote

from .model import FIELDS, STATES, ORIGINS, CAPTURE_STATES, new_snapshot, set_field, validate

PAGE_BYTES = 152  # Version 8 / M, byte mode; includes the ASCII envelope.
CHUNK_BYTES = 81  # 108 base64 characters + 37-character envelope = 145 bytes.
MAX_PAGES = 64
DEFAULT_IMPORT_URL = 'https://inuchanbt.github.io/km003c-pd-interrogator/?v=4'
PAGE_RE = re.compile(r'^KPD1:([0-9a-f]{16}):([0-9]{2})/([0-9]{2}):([0-9A-F]{8}):([A-Za-z0-9_-]+)$')


def pack(snapshot):
    validate(snapshot)
    data = bytearray(b'KPD1' + bytes.fromhex(snapshot['measurement_id']))
    data.append(ORIGINS.index(snapshot['origin']))
    data.append(CAPTURE_STATES.index(snapshot['capture_status']))
    for tag, name in enumerate(FIELDS, 1):
        field = snapshot['fields'][name]
        raw = bytes.fromhex(field['raw']) if 'raw' in field else b''
        data.extend(struct.pack('<BBBH', tag, STATES.index(field['status']),
                                int('raw' in field), len(raw)))
        data.extend(raw)
    return bytes(data)


def unpack(data):
    if (len(data) < 14 or data[:4] != b'KPD1' or data[12] >= len(ORIGINS)
            or data[13] >= len(CAPTURE_STATES)):
        raise ValueError('Invalid binary snapshot header')
    snapshot = new_snapshot(ORIGINS[data[12]], data[4:12].hex())
    # Host capture time, logs, and user labels are deliberately outside QR v1.
    snapshot['captured_at'] = None
    snapshot['capture_status'] = CAPTURE_STATES[data[13]]
    seen, offset = set(), 14
    while offset < len(data):
        if offset + 5 > len(data):
            raise ValueError('Truncated field header')
        tag, state, present, length = struct.unpack_from('<BBBH', data, offset)
        offset += 5
        if (not 1 <= tag <= len(FIELDS) or tag in seen or state >= len(STATES)
                or present not in (0, 1) or length > 260 or (not present and length)
                or offset + length > len(data)):
            raise ValueError('Invalid or duplicate binary field')
        seen.add(tag)
        set_field(snapshot, FIELDS[tag - 1], STATES[state],
                  data[offset:offset + length] if present else None)
        offset += length
    if len(seen) != len(FIELDS):
        raise ValueError('Missing binary fields')
    validate(snapshot)
    return snapshot


def normalize_page(text):
    text = text.strip()
    if len(text) > 2048:
        raise ValueError('QR text is too long')
    if re.match(r'https?://', text, re.I):
        url = urlsplit(text)
        if not url.netloc or url.username or url.password or not url.fragment:
            raise ValueError('Invalid importer URL')
        text = unquote(url.fragment, encoding='ascii', errors='strict')
    return text


def importer_prefix(base_url):
    if base_url is None:
        return ''
    if not isinstance(base_url, str) or not base_url.isascii() or any(c.isspace() for c in base_url):
        raise ValueError('Importer URL must be an ASCII HTTPS URL')
    url = urlsplit(base_url)
    local = url.hostname in ('localhost', '127.0.0.1', '[::1]', '::1')
    if (url.scheme != 'https' and not (url.scheme == 'http' and local)
            or not url.netloc or url.username or url.password or url.fragment or '#' in base_url
            or ('?' in base_url and url.query not in ('v=2', 'v=3', 'v=4'))):
        raise ValueError('Use an HTTPS importer URL without credentials or fragment; use ?v=4 if needed')
    return base_url + '#'


def encode_pages(snapshot, base_url=None):
    data = pack(snapshot)
    checksum = f'{zlib.crc32(data):08X}'
    prefix = importer_prefix(base_url)
    # Keep the complete URL within Version 8/M's 152-byte budget.
    available = PAGE_BYTES - len(prefix) - 37
    chunk_bytes = min(CHUNK_BYTES, available * 3 // 4)
    if chunk_bytes < 1:
        raise ValueError('Importer URL is too long for a device-size QR')
    chunks = [data[i:i + chunk_bytes] for i in range(0, len(data), chunk_bytes)]
    if len(chunks) > MAX_PAGES:
        raise ValueError('Snapshot exceeds transfer page limit')
    pages = [prefix + f'KPD1:{snapshot["measurement_id"]}:{i:02}/{len(chunks):02}:{checksum}:'
             + base64.urlsafe_b64encode(chunk).decode('ascii').rstrip('=')
             for i, chunk in enumerate(chunks, 1)]
    assert all(len(page.encode('ascii')) <= PAGE_BYTES for page in pages)
    return pages


def decode_pages(pages):
    identity, chunks = None, {}
    for text in pages:
        text = normalize_page(text)
        if len(text) > PAGE_BYTES or not (match := PAGE_RE.fullmatch(text)):
            raise ValueError('Invalid QR page')
        mid, index, total, checksum, encoded = match.groups()
        index, total = int(index), int(total)
        key = (mid, total, checksum)
        if not 1 <= index <= total <= MAX_PAGES or (identity is not None and identity != key):
            raise ValueError('Mixed measurements or invalid page numbering')
        identity = key
        try:
            chunk = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
        except binascii.Error as exc:
            raise ValueError('Invalid page encoding') from exc
        if not chunk or len(chunk) > CHUNK_BYTES:
            raise ValueError('Invalid chunk length')
        if base64.urlsafe_b64encode(chunk).decode('ascii').rstrip('=') != encoded:
            raise ValueError('Noncanonical page encoding')
        if index in chunks and chunks[index] != chunk:
            raise ValueError('Conflicting duplicate page')
        chunks[index] = chunk
    if identity is None or len(chunks) != identity[1]:
        raise ValueError('Missing QR pages')
    data = b''.join(chunks[i] for i in range(1, identity[1] + 1))
    if f'{zlib.crc32(data):08X}' != identity[2]:
        raise ValueError('QR checksum mismatch')
    snapshot = unpack(data)
    if snapshot['measurement_id'] != identity[0]:
        raise ValueError('Measurement identity mismatch')
    return snapshot


def write_qr(pages, directory):
    try:
        import qrcode
        from qrcode.util import QRData, MODE_8BIT_BYTE
    except ImportError as exc:
        raise RuntimeError('Install requirements.txt to generate QR images') from exc
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'pages.txt').write_text('\n'.join(pages) + '\n', encoding='ascii')
    for index, text in enumerate(pages, 1):
        qr = qrcode.QRCode(version=8, error_correction=qrcode.constants.ERROR_CORRECT_M,
                          box_size=4, border=4)
        qr.add_data(QRData(text.encode('ascii'), mode=MODE_8BIT_BYTE))
        qr.make(fit=False)
        # Exact device pixels, with the required white quiet zone.
        matrix = qr.get_matrix()
        rects = ''.join(f'<rect x="{x*4+6}" y="{y*4+6}" width="4" height="4"/>'
                        for y, row in enumerate(matrix) for x, dark in enumerate(row) if dark)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240" '
               'viewBox="0 0 240 240"><rect width="240" height="240" fill="white"/>'
               f'<g fill="black">{rects}</g></svg>')
        (directory / f'qr-{index:02}.svg').write_text(svg, encoding='utf-8')
        # Firmware can consume this packed 49x49 symbol without a framebuffer.
        symbol = [row[4:-4] for row in matrix[4:-4]]
        bits = [int(bit) for row in symbol for bit in row]
        bits.extend([0] * (-len(bits) % 8))
        packed = bytes(sum(bits[i+j] << (7-j) for j in range(8)) for i in range(0, len(bits), 8))
        (directory / f'qr-{index:02}.bin').write_bytes(packed)
