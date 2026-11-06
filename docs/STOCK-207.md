# Stock firmware 2.0.7 investigation

English · [日本語](STOCK-207.ja.md)

The reference is a qualified HW 2.3 backup with application SHA-256 `c63057eaf08b8625e7ac2fc7270c06d75515e78fdbdd46080bdf4b696984ec67`. Addresses apply only to this build. The investigation used local analysis and read-only acquisition, followed by a separately recorded overlay installation and restore.

```powershell
python tools/inspect_stock207.py C:\private\stock-app.bin
```

Use Unicorn 2.1.4 and Capstone 5.0.7. The script runs the stock scatter-load at `0x4c48` and stops at `0x4c6a`, before main. Reports and extracted RAM remain private under `artifacts/stock207-analysis/`.

| Function | Runtime address |
| --- | --- |
| GUI task registration and entry | `0x1100e`, Thumb entry `0x1ebb1` |
| GUI loop, handler and wait | `0x1ec04`, `0x39a18`, `0x4cffc` |
| Candidate LCD flush and pixel transfer | `0x1fff8000`, `0x1fff8110` |
| Bus lock object, acquire and release candidates | `0x1fffb4c4`, `0x4cbe6`, `0x4ccec` |
| Candidate flush completion | `0x2a696` |

The display and external NOR share QSPI, so the overlay uses the stock GUI path. The stock application occupies 456,896 bytes starting at `0x4000` and ends at `0x738c0`. Its tail margin is too small for the complete extension. See [overlay placement](OVERLAY-207.md) and [PDO cache observations](PD-CACHE-207.md).
