"""Render exported device symbol bits as a labeled QR contact sheet."""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw

p = argparse.ArgumentParser()
p.add_argument('directory', type=Path)
p.add_argument('output', type=Path)
p.add_argument('--columns', type=int, default=2, help='QRs per row (default: 2)')
args = p.parse_args()
if args.columns < 1:
    p.error('--columns must be positive')
pages = args.directory.joinpath('pages.txt').read_text(encoding='ascii').splitlines()
if not pages:
    p.error('pages.txt contains no QR pages')
columns = min(args.columns, len(pages))
rows = (len(pages) + columns - 1) // columns
sheet = Image.new('RGB', (24 + columns * 264, rows * 304), 'white')
draw = ImageDraw.Draw(sheet)
for index, _ in enumerate(pages, 1):
    packed = (args.directory / f'qr-{index:02}.bin').read_bytes()
    left, top = 24 + (index - 1) % columns * 264, 40 + (index - 1) // columns * 304
    draw.text((left, top - 26), f'KPD1 - PAGE {index}/{len(pages)}', fill='black')
    for y in range(49):
        for x in range(49):
            bit = y * 49 + x
            if packed[bit // 8] & (1 << (7 - bit % 8)):
                xx, yy = left + (x + 4) * 4 + 6, top + (y + 4) * 4 + 6
                draw.rectangle((xx, yy, xx + 3, yy + 3), fill='black')
args.output.parent.mkdir(parents=True, exist_ok=True)
sheet.save(args.output)
print(args.output.resolve())
