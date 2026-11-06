# Exact-build overlay layout and recovery

English · [日本語](OVERLAY-207.ja.md)

The overlay applies to the known HW 2.3 stock 2.0.7 application and bootloader.

| Region | Use |
| --- | --- |
| `0x4000..0x738bf` | Stock application. The integration replaces eight entry bytes and four GUI-loop bytes. |
| `0x738c0` | Gate in the unused stock tail. |
| `0x74000` | Extension header and code. The header records ABI, size, CRC32, callback pointers, and the stock hash prefix. |
| `0x78000` | Exclusive end of the dedicated update profile's allowed range. The linker also constrains the extension. |
| Static mutable RAM | Zero bytes in the linked overlay. |

The gate validates the extension, then holds the selected callback in R8. A missing or invalid extension selects the stock GUI handler. The gate replays the replaced entry instructions and returns to the stock initializer. The patched loop calls R8 and preserves the remaining stock scheduling. CRC32 detects corruption in the extension.

```powershell
python tools/build_overlay207.py --clang C:\path\to\clang.exe
python tools/check_overlay207.py artifacts/overlay207
python tools/check_native_arm.py artifacts/overlay207/overlay207-offline.elf --overlay
```

These tests cover 28 gate cases, stock call-through and task ABI, and native QR compatibility. The build outputs stock-free `gate.bin` and `extension.bin` for offline preparation. The exact qualified backup is required to generate a private patch candidate and two-stage recovery bundle.

Loader SHA-256 is `8e972bbd2abffa199d465082f473823372e32d4b4c86574664a976afd2797809`. The host verifies loader, app, untouched ranges and backup evidence before programming. The generic updater has different limits. Use the [dedicated Windows procedure](INSTALL.md) and [native host notes](../native-host/README.md).
