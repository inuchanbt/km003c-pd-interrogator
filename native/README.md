# Native KPD1 and QR library

English · [日本語](README.ja.md)

The C11 library encodes captured PD fields as KPD1, an HTTPS URL, QR symbols, and RGB565 rows. The capture owner supplies the ten raw fields and statuses, origin, and eight-byte measurement ID. Call `kpd_freeze()` while the source data is consistent. Release the capture lock before encoding a page with `kpd_encode_page()`. Use `kpd_qr_row()` from the display path and reuse the frozen transfer for all pages.

The default URL allows 44 bytes per page and up to 61 pages. The normal QR renderer uses Version 8/M and 240-pixel RGB565 rows. Keep labels out of its quiet zone. The library uses caller-provided buffers and has no USB or flash access.

```powershell
python -m unittest discover -s tests -v
python tools/check_native_size.py --clang C:\path\to\clang.exe
python tools/check_native_arm.py artifacts/native-size/sizecheck-only.elf
```

The host tests require Node.js and a C11 compiler. Set `KPD_CC` if Clang or GCC is outside PATH. The ARM test uses Unicorn. The size-check ELF is a test image. See [the exact-build integration](../docs/OVERLAY-207.md) and the [format specification](../docs/QR-FORMAT.md).
