# Host CLI

English · [日本語](../README.ja.md#起動)

Use Python 3.10 or later and install `requirements.txt` for QR rendering. The PowerShell wrapper looks for a project virtual environment. The installation package is described separately in the [meter guide](INSTALL.md).

- `demo` creates synthetic measurements. Use `serve` for a local importer. The default HTTPS QR destination works with the iPhone Camera app.
- `export-qr` accepts `--qr-format raw` or a separate HTTPS importer through `--import-url`. A longer destination may require more pages. The complete QR URL must fit within 152 ASCII bytes.
- `import-ch32` accepts a CH32H417 log. If it contains several sessions, specify `--session` and separate sections that span a reboot. The importer uses complete packet bytes and preserves textual summaries in JSON where available.
- `replay` takes the first connection interval in a KM003C event log and stops on disconnect, reconnect, or Soft Reset.
- `import-qr` restores matching pages in any order. QR transfers omit the original host timestamp.

## Live capture on stock firmware

Install the separate [KM003C CLI](https://github.com/inuchanbt/KM003C-CLI) and its transport dependencies. With no downstream equipment or load attached, preview the procedure before running a live capture.

```powershell
.\pdinfo.ps1 capture --output captures/charger-01 --dry-run
.\pdinfo.ps1 capture --km003c-root C:\src\KM003C-CLI --port COM14 --output captures/charger-01 --qr
```

Adjust the checkout path and port to match your system. The adapter matches CDC and USB by serial, resets and opens stock PDM, configures a sink with e-marker emulation disabled, and queries Revision, Source Capabilities Extended, Status, Source Info, and Manufacturer Info. It uses the stock negotiation and defaults to a two-second settling period before queries. Disconnect, Soft Reset, send mismatch, and incomplete replies can end collection. The tool attempts `reset` and `pdm close` and records the final VBUS check. This capture path does not select a new PPS contract or initiate an EPR transition itself.

Capture outputs include `snapshot.json` with raw and decoded results, `frames.jsonl` with host-observed frames, and `events.jsonl` with PD events. Use a new output directory for every run. QR output contains `qr/pages.txt`, the QR SVGs, and packed QR symbols.
