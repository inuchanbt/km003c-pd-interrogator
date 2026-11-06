# KPD1 QR transfer format

English · [日本語](QR-FORMAT.ja.md)

KPD1 v1 transfers a raw-first measurement between the meter, host CLI, and browser. Update the Python, JavaScript, and C compatibility tests together if the format changes.

## Page envelope

```text
KPD1:<measurement-id>:<page>/<count>:<CRC32>:<base64url>
https://inuchanbt.github.io/km003c-pd-interrogator/?v=4#KPD1:<measurement-id>:<page>/<count>:<CRC32>:<base64url>
```

- The measurement ID is eight bytes written as 16 lowercase hexadecimal characters.
- Page and count start at one, use two decimal digits, and have a maximum of 64.
- CRC32 is the IEEE CRC of the complete binary, written as eight uppercase hexadecimal characters.
- The page payload is canonical unpadded Base64url for up to 81 bytes. Raw text is at most 145 ASCII bytes. The full URL is at most 152 ASCII bytes.
- The default destination leaves 44 bytes per page. Older destinations with `?v=2`, `?v=3`, and no query remain readable. Use `--qr-format raw` to export the text envelope.
- Pages may arrive out of order. Identical duplicates are allowed. A measurement ID, page count, and CRC must agree before grouping pages. The importer checks the final CRC before displaying results. CRC detects corruption.

The phone importer processes the fragment in the browser and keeps up to ten transfers in the same origin's local storage. Changing the language or URL path does not change that origin. Moving to another scheme, hostname, or port creates separate history.

## Binary payload

Integers use little-endian byte order.

| Offset | Size | Value |
| --- | --- | --- |
| 0 | 4 | ASCII `KPD1` |
| 4 | 8 | Measurement ID |
| 12 | 1 | Origin. 0 = KM003C source, 1 = CH32H417, 2 = demo, 3 = KM003C cable, 4 = KM003C PPS |
| 13 | 1 | Capture. 0 = unknown, 1 = completed, 2 = failed, 3 = interrupted, 4 = imported |
| 14 | Variable | One TLV per tag 1 through 10 |

A TLV is `tag:u8, status:u8, raw_present:u8, length:u16, raw:length bytes`. The presence flag is 0 or 1. Flag 0 requires length 0. Flag 1 and length 0 explicitly records empty raw data. Each field is at most 260 bytes. Unknown, duplicate, missing, and truncated records are rejected.

| Tag | Field | Raw payload |
| --- | --- | --- |
| 1 | `spr_pdos` | Received 32-bit PDOs in order |
| 2 | `epr_pdos` | Received PDOs, including SPR entries and reserved slots |
| 3 | `revision` | Four-byte RMDO |
| 4 | `source_cap_ext` | Source Capabilities Extended payload |
| 5 | `source_info` | Four-byte SIDO |
| 6 | `source_status` | Status payload |
| 7 | `pps_status` | Four-byte PPS Status. Standalone PPS instead starts with its four-byte Request RDO, followed by PPS Status when received. |
| 8 | `manufacturer` | Manufacturer Info payload |
| 9 | `cable_identity` | SOP′ Identity ACK VDOs, normally including the VDM header. Standalone cable capture stores stock IdHead, CertStat, Product, and Cable words. With four zero words and SVIDs present, a `partial` capture adds a fifth word containing the two SVIDs in display order, high half first. |
| 10 | `source_identity` | SOP Identity ACK VDOs with the VDM header |

Status codes in order are `not_requested`, `pass`, `observed`, `unsupported`, `no_response`, `tx_failed`, `rejected`, `busy`, `reset`, `invalid`, `partial`, `unknown`, and `emulation_enabled`.

`pass` means the expected reply was observed after a matching request. `observed` records reception. `emulation_enabled` records the setting active during a PD detection and preserves the original cable VDOs. Some older logs have a status without raw bytes. The decoder keeps that distinction. The transfer omits serial numbers, host notes, and timestamps. QR imports set `captured_at` to `null`.

## QR symbols

The meter uses Version 8, error correction M, and byte mode. The symbol has 49 by 49 modules with a quiet zone of at least four modules on each side. The meter centers the QR above the page label, with four pixels per module for power and cable results and three for standalone PPS.

`qr-NN.bin` holds the already generated symbol, packed row-first with the first module in the most significant bit. Zero padding brings the 2,401 symbol bits to 301 bytes. The file excludes the quiet zone. See the [DENSO WAVE capacity table](https://www.qrcode.com/en/about/versionPage/versionPage1_10.html).
